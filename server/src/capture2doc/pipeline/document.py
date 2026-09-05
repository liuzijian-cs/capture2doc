"""V2: ordered image batches, independent block repair and recoverable local fallback.

state.json is the atomic transaction boundary. Export files are projections of a
committed state; no model response is ever directly published as a document.
"""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from capture2doc.prompts import blocks_system_prompt, prompt_fingerprint

from .blocks import (
    add_errors,
    candidate,
    diagnostic,
    document_xml,
    error,
    examples,
    public_block,
    segments,
)
from .draft import (
    MAX_REPAIRS,
    RejectedPatch,
    apply_patch,
    check_combined,
    commit_blocks,
    failed_attempt,
    initialize,
    new_draft,
    next_queue,
    resolve_fallback,
    start_attempt,
)
from .models import verify_previous_cleanup
from .protocol import response_schema
from .runner import finish_reason
from .store import DocumentStore, atomic_write, digest, now, write_json

MARGIN = 512
MAX_READS = 3


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


class BlockStore(DocumentStore):
    schema_version = 2

    def save(self) -> None:
        self.state.setdefault("blocks", [])
        self.state.setdefault("batches", [])
        self.state.setdefault("draft", None)
        self.state["updated_at"] = now()
        self.state.pop("integrity_sha256", None)
        self.state["integrity_sha256"] = digest(json_bytes(self.state))
        write_json(self.state_path, self.state)

    def load(self) -> None:
        super().load()
        stored = self.state.pop("integrity_sha256", None)
        calculated = digest(json_bytes(self.state))
        self.state["integrity_sha256"] = stored
        if stored != calculated:
            raise ValueError("CHECKPOINT_CORRUPT: state integrity mismatch")
        self.verify_committed()

    def verify_committed(self) -> None:
        if [b["image_id"] for b in self.state["batches"]] != self.state[
            "ordered_image_ids"
        ][: len(self.state["batches"])]:
            raise ValueError("CHECKPOINT_CORRUPT: committed image order mismatch")
        if self.state["batches"]:
            if (
                digest(json_bytes(self.state["blocks"]))
                != self.state["batches"][-1]["blocks_sha256"]
            ):
                raise ValueError("CHECKPOINT_CORRUPT: committed blocks mismatch")
        for batch in self.state["batches"]:
            read_artifact(self, batch["draft_ref"], batch["draft_sha256"])
        document_xml(self.state["blocks"], self.state["lang"])
        for image in self.state["images"].values():
            ocr = image.get("ocr")
            if ocr and ocr.get("response_ref"):
                read_artifact(self, ocr["response_ref"], ocr["response_sha256"])
        draft = self.state["draft"]
        if (
            draft
            and not draft["committed"]
            and draft["image_id"]
            != self.state["ordered_image_ids"][len(self.state["batches"])]
        ):
            raise ValueError("CHECKPOINT_CORRUPT: draft input identity mismatch")


def read_artifact(store: DocumentStore, ref: str, sha256: str | None = None) -> bytes:
    path = (store.root / ref).resolve()
    if not path.is_relative_to(store.root):
        raise ValueError("Artifact escapes document directory")
    data = path.read_bytes()
    if sha256 is not None and digest(data) != sha256:
        raise ValueError("CHECKPOINT_CORRUPT: artifact hash mismatch")
    return data


def import_ocr(store: BlockStore, source_dir: Path, model_configuration: dict) -> int:
    source_dir = source_dir.expanduser().resolve()
    source_state = json.loads((source_dir / "state.json").read_text())
    source = (
        BlockStore(source_dir)
        if source_state.get("schema_version") == 2
        else DocumentStore(source_dir)
    )
    source.load()
    original = source.state.get("contract") or {}
    old_paddle = original.get("model_configuration", {}).get("paddle")
    if old_paddle is None or old_paddle != model_configuration.get("paddle"):
        raise ValueError("OCR reuse rejected: Paddle model/configuration mismatch")
    imported = 0
    for image_id in store.state["ordered_image_ids"]:
        target = store.state["images"][image_id]
        if target["ocr"] is not None:
            continue
        old = source.state["images"].get(image_id)
        if (
            not old
            or old["sha256"] != target["sha256"]
            or old.get("model_image_sha256") != target.get("model_image_sha256")
        ):
            raise ValueError(
                f"OCR reuse rejected: image identity/preprocessing mismatch: {image_id}"
            )
        ocr = old.get("ocr")
        if not ocr or ocr.get("finish_reason") != "stop":
            continue
        raw_data = read_artifact(
            source, ocr["response_ref"], ocr.get("response_sha256")
        )
        raw = json.loads(raw_data)
        choices = raw.get("choices", [])
        if (
            not choices
            or choices[0].get("finish_reason") != "stop"
            or choices[0].get("message", {}).get("content") != ocr["content"]
        ):
            raise ValueError("OCR reuse rejected: raw response/content mismatch")
        ref = f"ocr/{image_id}/imported.json"
        atomic_write(store.root / ref, raw_data)
        target["ocr"] = {
            **deepcopy(ocr),
            "response_ref": ref,
            "response_sha256": digest(raw_data),
            "complete": True,
            "imported_from": str(source_dir),
            "source_state_sha256": digest((source_dir / "state.json").read_bytes()),
            "source_attempts": deepcopy(old.get("ocr_attempts", [])),
        }
        target["sources"] = segments(image_id, ocr["content"])
        imported += 1
    store.save()
    return imported


def transient(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError)) or type(exc).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
    }


def run_ocr(store: BlockStore, models: Any, image_id: str) -> None:
    image = store.state["images"][image_id]
    while len(image["ocr_attempts"]) < 3:
        attempt = {"attempt_id": uuid4().hex, "started_at": now(), "status": "started"}
        image["ocr_attempts"].append(attempt)
        store.save()  # Count a request before dispatch, including interrupted requests.
        started = time.monotonic()
        try:
            result = models.ocr(store.root / image["model_path"])
        except Exception as exc:
            if not transient(exc):
                raise
            attempt.update(
                status="failed",
                error=str(exc),
                request_seconds=time.monotonic() - started,
            )
            store.save()
            continue
        raw = json_bytes(result.raw_response)
        ref = f"ocr/{image_id}/{attempt['attempt_id']}.json"
        atomic_write(store.root / ref, raw)
        reason = finish_reason(result)
        attempt.update(
            status="received",
            request_seconds=time.monotonic() - started,
            finish_reason=reason,
            response_ref=ref,
            ended_at=now(),
        )
        image["ocr"] = {
            "content": result.content,
            "finish_reason": reason,
            "complete": reason == "stop" and bool(result.content.strip()),
            "response_ref": ref,
            "response_sha256": digest(raw),
            "usage": result.raw_response.get("usage"),
            "completed_at": now(),
        }
        image["sources"] = segments(image_id, result.content)
        store.save()
        return
    image["ocr"] = {
        "content": "",
        "finish_reason": None,
        "complete": False,
        "error": "OCR_RETRIES_EXHAUSTED",
    }
    image["sources"] = []
    store.save()


def history_view(block: dict, index: int) -> dict:
    return {
        "block_id": index,
        "internal_id": block["id"],
        "version": block["version"],
        "text": block["text"],
        "xml": block["xml"],
        "status": block["status"],
    }


def history_tool(proposal: dict, blocks: list[dict]) -> dict:
    action = proposal.get("action")
    if action == "read_blocks":
        ids = proposal.get("block_ids")
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 3
            or any(type(i) is not int or not 0 <= i < len(blocks) for i in ids)
        ):
            return {"error": "block_ids 必须含 1–3 个存在的从 0 开始的历史编号。"}
    elif action == "search_blocks":
        query = proposal.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            return {"error": "query 必须是非空且不超过 200 字的关键词。"}
        ids = [i for i, b in enumerate(blocks) if query in (b["text"] or "")][:3]
    else:
        return {"error": "未知只读工具，仅可 read_blocks/search_blocks。"}
    return {"blocks": [history_view(blocks[i], i) for i in ids]}


def request_context(state: dict, draft: dict, attempt: dict) -> dict:
    image = state["images"][draft["image_id"]]
    committed = state["blocks"]

    def view(b: dict, *, repair: bool = False) -> dict:
        result = {
            k: deepcopy(b.get(k))
            for k in (
                "id",
                "version",
                "xml",
                "text",
                "ocr_refs",
                "status",
                "replaces_tail",
            )
        }
        if repair:
            for key in ("current_errors", "guards"):
                unique = {}
                for record in b[key]:
                    unique[(record["code"], record["message"])] = record
                result[key] = list(unique.values())
        return result

    targets = [
        view(b, repair=True) for b in draft["blocks"] if b["id"] in attempt["targets"]
    ]
    neighbors = []
    for index, b in enumerate(draft["blocks"]):
        if b["id"] in attempt["targets"]:
            for n in draft["blocks"][max(0, index - 1) : index + 2]:
                if n["id"] not in attempt["targets"] and n not in neighbors:
                    neighbors.append(n)
    return {
        "mode": "generate" if attempt["kind"] == "generate" else "repair",
        "task_scope": (
            "整图视觉转录：检查全部文档区域，OCR只是参考。"
            if attempt["kind"] == "generate"
            else "局部结构修复：只返回 targets 的全部内容。neighbors、历史和图上其他内容不得加入 blocks。"
        ),
        "image_id": draft["image_id"],
        "ocr_complete": image["ocr"].get("complete", False),
        "ocr_sources": image["sources"],
        "mutable_tail": view(draft["old_tail"]) if draft["old_tail"] else None,
        "document_has_title": any("<title" in (b["xml"] or "") for b in committed),
        "history_block_count": len(committed),
        "readonly_history": [
            history_view(committed[i], i)
            for i in range(max(0, len(committed) - 3), len(committed))
        ],
        "targets": targets,
        "neighbors": [view(b) for b in neighbors],
        "attempt_id": attempt["attempt_id"],
        "target_versions": attempt["target_versions"],
        "allowed_modify_ids": attempt["targets"],
        "repair_wave": draft["wave"],
        "complete_examples": examples(),
        "tool_results": [],
    }


def get_proposal(
    store: BlockStore, models: Any, draft: dict, attempt: dict, system: str
) -> dict:
    """Application-level JSON tools share the exact image/text token preflight.

    No native tool template flags are needed. Every dispatched request and tool
    reply is durable. Replaying a received response never makes a second request.
    """
    if attempt["proposal"] is not None:
        return attempt["proposal"]
    payload = request_context(store.state, draft, attempt)
    schema = response_schema(
        payload["mode"], attempt["attempt_id"], attempt["target_versions"]
    )
    path = store.root / store.state["images"][draft["image_id"]]["model_path"]
    call_index = 0
    while call_index <= MAX_READS:
        if call_index < len(attempt["calls"]):
            call = attempt["calls"][call_index]
            if "response_ref" not in call:
                raise RejectedPatch(
                    "INTERRUPTED_REQUEST: 上次请求未留下完整响应；此次尝试已计入预算。"
                )
            raw = json.loads(
                read_artifact(store, call["response_ref"], call["response_sha256"])
            )
        else:
            while True:
                prompt = json.dumps(payload, ensure_ascii=False)
                inspection = models.inspect(path, prompt, system)
                output = min(
                    models.qwen.max_output_tokens,
                    models.qwen.max_model_len - inspection.prompt_tokens - MARGIN,
                )
                if output >= 512:
                    break
                if len(payload["readonly_history"]) > 1:
                    payload["readonly_history"].pop(0)
                else:
                    raise RejectedPatch(
                        "CONTEXT_BUDGET_EXCEEDED: 完整图片、规则、OCR、目标块及输出空间不能同时容纳；没有截断任何块。"
                    )
            call = {
                "call_id": uuid4().hex,
                "started_at": now(),
                "prompt_tokens": inspection.prompt_tokens,
                "max_output_tokens": output,
            }
            attempt["calls"].append(call)
            ref = f"requests/{call['call_id']}.json"
            call["request_ref"] = store.artifact(
                ref,
                {
                    "system": system,
                    "payload": payload,
                    "inspection": inspection.to_dict(),
                    "max_output_tokens": output,
                    "response_schema": schema,
                },
            )
            store.save()
            started = time.monotonic()
            try:
                result = models.generate(
                    path, prompt, system, inspection, output, response_schema=schema
                )
            except Exception as exc:
                if not transient(exc):
                    raise
                raise RejectedPatch(f"MODEL_REQUEST_FAILED: {exc}") from exc
            raw = result.raw_response
            data = json_bytes(raw)
            ref = f"responses/{call['call_id']}.json"
            atomic_write(store.root / ref, data)
            call.update(
                response_ref=ref,
                response_sha256=digest(data),
                request_seconds=time.monotonic() - started,
                ended_at=now(),
            )
            store.save()
        choices = raw.get("choices", [])
        if not choices or choices[0].get("finish_reason") != "stop":
            raise RejectedPatch(
                "OUTPUT_INCOMPLETE: 响应未正常结束，包括 length；不得补标签伪装成功。"
            )
        content = choices[0].get("message", {}).get("content", "")
        try:
            proposal = json.loads(content)
        except (ValueError, TypeError) as exc:
            raise RejectedPatch(f"JSON_INVALID: {exc}") from exc
        if not isinstance(proposal, dict):
            raise RejectedPatch("JSON_OBJECT_REQUIRED")
        if proposal.get("action") == "submit":
            attempt["proposal"] = proposal
            store.save()
            return proposal
        if proposal.get("action") not in {"read_blocks", "search_blocks"}:
            raise RejectedPatch(
                "ACTION_INVALID: 仅允许 submit/read_blocks/search_blocks"
            )
        if call_index == MAX_READS:
            raise RejectedPatch("HISTORY_TOOL_BUDGET_EXHAUSTED")
        tool_result = history_tool(proposal, store.state["blocks"])
        call["tool_request"] = proposal
        call["tool_result"] = tool_result
        store.save()
        payload["tool_results"].append({"request": proposal, "result": tool_result})
        call_index += 1
    raise AssertionError("unreachable")


def initial_fallback(
    draft: dict, sources: list[dict], record: dict, *, ocr_complete: bool = True
) -> None:
    b = candidate(
        {"xml": None, "text": None, "ocr_refs": [s["source_id"] for s in sources]},
        draft["image_id"],
    )
    b["whole_image_fallback"] = True
    add_errors(b, [record])
    draft.update(blocks=[b], initialized=True, budgets={b["id"]: 0})
    if sources and not ocr_complete:
        missing = candidate(
            {"xml": None, "text": None, "ocr_refs": []}, draft["image_id"]
        )
        add_errors(
            missing,
            [
                error(
                    "OCR_INCOMPLETE",
                    "整图候选缺失且 OCR 不完整；已保留可用文字，未知剩余内容明确留空。",
                    [missing["id"]],
                )
            ],
        )
        draft["blocks"].append(missing)
        draft["budgets"][missing["id"]] = 0


def run_batch(
    store: BlockStore, models: Any, system: str, progress: Callable[[str], None]
) -> None:
    state = store.state
    image_id = state["ordered_image_ids"][len(state["batches"])]
    if state["draft"] is None:
        tail = (
            state["blocks"][-1]
            if state["blocks"] and state["blocks"][-1]["xml"]
            else None
        )
        state["draft"] = new_draft(image_id, tail)
        store.save()
    draft = state["draft"]
    if not draft["initialized"]:
        attempt = (
            draft["attempts"][-1]
            if draft["active_attempt"]
            else start_attempt(draft, [], "generate")
        )
        store.save()
        try:
            initialize(draft, get_proposal(store, models, draft, attempt, system))
            attempt["status"] = "applied"
            draft["active_attempt"] = None
        except RejectedPatch as exc:
            record = error(str(exc).split(":")[0], str(exc))
            failed_attempt(draft, attempt, record)
            initial_fallback(
                draft,
                state["images"][image_id]["sources"],
                record,
                ocr_complete=state["images"][image_id]["ocr"].get("complete", False),
            )
        check_combined(state["blocks"], draft)
        store.save()
    whole_image_failed = any(b.get("whole_image_fallback") for b in draft["blocks"])
    while not whole_image_failed:
        if draft["active_attempt"]:
            attempt = draft["attempts"][-1]
        else:
            if not draft["queue"]:
                if draft["wave"] >= MAX_REPAIRS:
                    break
                queue = next_queue(draft)
                if not queue:
                    break
                draft["wave"] += 1
                draft["queue"] = queue
                store.save()
            targets = draft["queue"].pop(0)
            by_id = {b["id"]: b for b in draft["blocks"]}
            if any(i not in by_id for i in targets) or all(
                by_id[i]["status"] != "pending" for i in targets
            ):
                store.save()
                continue
            roots = {r for i in targets for r in by_id[i]["lineage"]}
            if max(draft["budgets"][r] for r in roots) >= MAX_REPAIRS:
                store.save()
                continue
            attempt = start_attempt(draft, targets)
            store.save()
        progress(
            f"Repair {image_id}: wave {draft['wave']}/5, {len(attempt['targets'])} target(s)"
        )
        try:
            proposal = get_proposal(store, models, draft, attempt, system)
            apply_patch(draft, attempt, proposal)
        except RejectedPatch as exc:
            failed_attempt(
                draft,
                attempt,
                error(str(exc).split(":")[0], str(exc), attempt["targets"]),
            )
        check_combined(state["blocks"], draft)
        store.save()
    resolve_fallback(draft, state["images"][image_id]["sources"], state["blocks"])
    blocks = commit_blocks(state["blocks"], draft)
    document_xml(blocks, state["lang"])
    draft["committed"] = True
    ref = f"drafts/{len(state['batches']):04d}-{image_id}.json"
    draft_data = json_bytes(draft)
    atomic_write(store.root / ref, draft_data)
    state["blocks"] = blocks
    diag = diagnostic(state["images"][image_id]["ocr"]["content"], draft["blocks"])
    state["batches"].append(
        {
            "image_id": image_id,
            "committed_at": now(),
            "draft_ref": ref,
            "draft_sha256": digest(draft_data),
            "blocks_sha256": digest(json_bytes(blocks)),
            "diagnostic": diag,
            "candidate_count": len(draft["blocks"]),
            "repair_calls": sum(
                len(a["calls"]) for a in draft["attempts"] if a["kind"] == "repair"
            ),
        }
    )
    state["draft"] = None
    store.save()  # Atomic commit: result, cursor, journal and cleared draft together.
    export(store, completed=False)
    progress(
        f"Committed {image_id}: {len(blocks)} document blocks; {len(state['batches'])}/{len(state['ordered_image_ids'])} images"
    )


def projection(state: dict, completed: bool) -> dict:
    blocks = [public_block(b, i) for i, b in enumerate(state["blocks"])]
    holes = any(b["status"] == "unresolved" for b in blocks)
    review = any(b["status"] != "ok" for b in blocks) or any(
        not state["images"][i]["ocr"].get("complete", False)
        for i in state["ordered_image_ids"]
        if state["images"][i]["ocr"]
    )
    # Semantic fidelity is unverified; large OCR differences flag review, never repair.
    review |= any(
        (batch["diagnostic"][key] is not None and batch["diagnostic"][key] < 0.8)
        for batch in state["batches"]
        for key in ("ocr_coverage", "output_support")
    )
    xml_available = any(b["xml"] for b in blocks)
    return {
        "doc": {
            "document_id": state["document_id"],
            "processing_status": "completed" if completed else "processing",
            "needs_review": review,
            "semantic_fidelity_verified": False,
            "xml_status": ("partial" if holes or not completed else "complete")
            if xml_available
            else "unavailable",
            "blocks": blocks,
        }
    }


def export(store: BlockStore, *, completed: bool) -> Path:
    state = store.state
    result = projection(state, completed)
    xml = document_xml(state["blocks"], state["lang"])
    xml_name = (
        "document.partial.c2d.xml"
        if result["doc"]["xml_status"] == "partial"
        else "document.c2d.xml"
    )
    report = [
        f"# {state['document_id']} — block report",
        "",
        f"Processing: {result['doc']['processing_status']}; XML: {result['doc']['xml_status']}; needs_review: {result['doc']['needs_review']}",
        "",
        "语法通过不等于视觉内容完整。以下文字和 XML 均为完整内容。",
        "",
    ]
    for b in result["doc"]["blocks"]:
        report += [
            f"## Block {b['block_id']}",
            "",
            f"status={b['status']}; vlm_validation={b['vlm_validation']}; final_validation={b['final_validation']}; repairs={b['repair_attempts']}; fallback_source={b['fallback_source']}",
            "",
        ]
        for label, content in (
            ("Text", b["text"]),
            ("XML", b["xml"]),
            ("Errors", json.dumps(b["errors"], ensure_ascii=False, indent=2)),
        ):
            content = content if content is not None else "null"
            fence = "`" * max(
                3,
                max((len(part) for part in re.findall(r"`+", content)), default=0) + 1,
            )
            report += [
                label,
                "",
                fence + ("xml" if label == "XML" else "text"),
                content,
                fence,
                "",
            ]
    summary = {
        "document_id": state["document_id"],
        "ordered_image_ids": state["ordered_image_ids"],
        "processed_images": len(state["batches"]),
        "blocks": len(state["blocks"]),
        "status_counts": {
            s: sum(b["status"] == s for b in state["blocks"])
            for s in ("ok", "fallback", "unresolved")
        },
        "repair_calls": sum(b["repair_calls"] for b in state["batches"]),
        "needs_review": result["doc"]["needs_review"],
        "xml_status": result["doc"]["xml_status"],
        "xml_file": xml_name if xml else None,
        "semantic_fidelity_verified": False,
        "batches": state["batches"],
        "runs": state["runs"],
    }
    files = {
        "document.json": json_bytes(result),
        "blocks.md": "\n".join(report).encode(),
        "summary.json": json_bytes(summary),
    }
    if xml is not None:
        files[xml_name] = xml
    previous = state.get("exports", {})
    recovery = state.get("export_recovery", {})
    for name in set(previous) | set(files) | set(recovery):
        path = store.root / name
        allowed = {previous.get(name), recovery.get(name)} - {None}
        if path.is_symlink() or (
            path.exists() and digest(path.read_bytes()) not in allowed
        ):
            raise ValueError("OUTPUT_CONFLICT: existing export was modified")
    # Publish expected hashes first. Keep old hashes for recovery across partial exports.
    state["export_recovery"] = {**recovery, **previous}
    state["exports"] = {name: digest(data) for name, data in files.items()}
    store.save()
    for name, data in files.items():
        atomic_write(store.root / name, data)
    for name in (previous.keys() | recovery.keys()) - files.keys():
        (store.root / name).unlink(missing_ok=True)
    state.pop("export_recovery", None)
    store.save()
    return store.root / "document.json"


def run_document_v2(
    store: BlockStore,
    models: Any,
    *,
    reuse_ocr_from: Path | None = None,
    progress: Callable[[str], None] = print,
) -> Path:
    state = store.state
    store.verify_committed()
    verify_previous_cleanup(store.root / "runs")
    if state["status"] == "completed":
        if len(state["batches"]) != len(state["ordered_image_ids"]):
            raise ValueError("CHECKPOINT_CORRUPT: completed input cursor mismatch")
        return export(store, completed=True)
    run = {"run_id": uuid4().hex, "started_at": now(), "status": "running"}
    state["runs"].append(run)
    directory = store.root / "runs" / run["run_id"]
    try:
        system = blocks_system_prompt()
        config = models.prepare()
        store.bind_contract(
            {
                "pipeline_version": 2,
                "prompt_sha256": prompt_fingerprint(system),
                "model_configuration": config,
                "max_repairs": MAX_REPAIRS,
                "max_history_reads": MAX_READS,
                "context_margin": MARGIN,
                "examples_sha256": digest(json_bytes(examples())),
                "response_protocol": "json-schema-v1",
                "response_schema_sha256": digest(
                    json_bytes(
                        [
                            response_schema("generate", "attempt", {}),
                            response_schema("repair", "attempt", {"target": 0}),
                        ]
                    )
                ),
            }
        )
        atomic_write(directory / "system-prompt.txt", system.encode())
        for image_id in state["ordered_image_ids"]:
            image = state["images"][image_id]
            prepared = f"prepared/{image_id}.png"
            info = models.image_info(store.root / image["path"], store.root / prepared)
            if image.get("model_image_sha256") not in (
                None,
                info["model_image_sha256"],
            ):
                raise ValueError("IMAGE_PREPROCESSING_CHANGED")
            image.update(info, model_path=prepared)
        store.save()
        if reuse_ocr_from is not None:
            count = import_ocr(store, reuse_ocr_from, config)
            progress(f"Reused verified OCR for {count} images")
        pending = [
            i for i in state["ordered_image_ids"] if state["images"][i]["ocr"] is None
        ]
        if pending:
            state["status"] = "ocr"
            store.save()
            progress("Loading Paddle; OCR queue is serial")
            with models.phase("paddle", directory):
                for image_id in pending:
                    run_ocr(store, models, image_id)
                    progress(f"OCR ready: {image_id}")
        if len(state["batches"]) < len(state["ordered_image_ids"]):
            state["status"] = "assembling"
            store.save()
            progress("Loading Qwen; ordered image batches and serial block repairs")
            with models.phase("qwen", directory):
                while len(state["batches"]) < len(state["ordered_image_ids"]):
                    run_batch(store, models, system, progress)
        state["status"] = "completed"
        state["error"] = None
        run.update(status="completed", ended_at=now())
        store.save()
        return export(store, completed=True)
    except BaseException as exc:
        state["status"] = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        )
        state["error"] = {
            "code": getattr(exc, "code", type(exc).__name__),
            "message": str(exc),
        }
        run.update(status=state["status"], ended_at=now())
        store.save()
        raise
