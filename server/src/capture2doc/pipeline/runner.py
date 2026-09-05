"""Serial image windows, bounded repairs, content diagnostics and journal replay."""

from __future__ import annotations

import json
import time
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from xml.etree import ElementTree

from capture2doc.formats.c2d_xml import C2DAssembler
from capture2doc.prompts import c2d_system_prompt, prompt_fingerprint

from .models import verify_previous_cleanup
from .store import DocumentStore, atomic_write, digest, now

MAX_ATTEMPTS = 3
CONTEXT_MARGIN = 512


class PipelineError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def finish_reason(result: Any) -> str | None:
    choices = result.raw_response.get("choices", [])
    return choices[0].get("finish_reason") if choices else None


def xml_text(xml: str) -> str:
    # Called only on XML already accepted by the C2D safe validator/assembler.
    return "".join(ElementTree.fromstring(xml).itertext())


def normalized(text: str) -> str:
    return "".join(text.split()).replace("\u200d", "")


def content_check(
    update: str, tail: str | None, ocr: str
) -> tuple[dict[str, Any], list[str]]:
    root = ElementTree.fromstring(update)
    old = normalized(xml_text(tail)) if tail else ""
    first = normalized("".join(root[0].itertext()))
    expected = normalized(ocr)
    actual = normalized("".join(root.itertext()))
    # Score the new window separately: a long, correctly copied tail must not
    # hide a completely omitted short window or dilute duplicated new content.
    if old and first.startswith(old):
        actual = actual[len(old) :]
    matched = sum(
        m.size
        for m in SequenceMatcher(
            None, expected, actual, autojunk=False
        ).get_matching_blocks()
    )
    coverage = matched / len(expected) if expected else 1.0
    grounded = matched / len(actual) if actual else 0.0
    errors = []
    if old and not first.startswith(old):
        errors.append(
            "TAIL_CONTENT_LOSS: first block must retain all previous tail text in order"
        )
    if tail and ElementTree.fromstring(tail).tag.endswith("}pre"):
        if not "".join(root[0].itertext()).startswith(xml_text(tail)):
            errors.append(
                "CODE_CONTENT_LOSS: retain the exact previous code whitespace and newlines"
            )
    # These are deliberately labeled heuristics, not claims of semantic fidelity.
    if coverage < 0.80 or grounded < 0.80:
        errors.append(
            f"CONTENT_MISMATCH: OCR/tail coverage={coverage:.3f}, output support={grounded:.3f}; "
            "check omitted, changed, invented or duplicated content"
        )
    return {
        "method": "new-window-character-alignment-v1",
        "source_coverage": round(coverage, 6),
        "output_support": round(grounded, 6),
        "needs_review": coverage < 0.95 or grounded < 0.95,
        "semantic_fidelity_verified": False,
    }, errors


def document_hash(assembler: C2DAssembler, has_rounds: bool) -> str:
    return digest(assembler.finalize() if has_rounds else b"")


def replay(store: DocumentStore) -> tuple[C2DAssembler, int, int]:
    assembler = C2DAssembler(lang=store.state["lang"])
    order = store.state["ordered_image_ids"]
    image_index = offset = 0
    for index, record in enumerate(store.state["rounds"]):
        if image_index >= len(order):
            raise PipelineError(
                "CHECKPOINT_INVALID", "Extra committed rounds after final image"
            )
        image_id = order[image_index]
        ocr = store.state["images"][image_id]["ocr"]["content"]
        end = record["source_end"]
        if (
            record["round_index"] != index
            or record["image_id"] != image_id
            or record["source_start"] != offset
            or not offset < end <= len(ocr)
            or record["source_sha256"] != digest(ocr[offset:end].encode())
            or record["base_document_sha256"] != document_hash(assembler, index > 0)
            or record["update_sha256"] != digest(record["update_xml"].encode())
        ):
            raise PipelineError(
                "CHECKPOINT_INVALID", "Committed round identity/order/hash mismatch"
            )
        validation = assembler.apply_update(record["update_xml"])
        if not validation.valid:
            raise PipelineError(
                "CHECKPOINT_INVALID", "Committed update no longer validates"
            )
        offset = end
        if offset == len(ocr):
            image_index += 1
            offset = 0
    return assembler, image_index, offset


def shorter_end(text: str, start: int, end: int) -> int:
    middle = start + (end - start) // 2
    newline = text.rfind("\n", start, middle + 1)
    return newline + 1 if newline >= start and newline + 1 > start else middle


def plan_request(
    models: Any,
    assembler: C2DAssembler,
    path: Path,
    image_id: str,
    ocr: str,
    start: int,
    end: int,
    system: str,
    errors: list[str],
    previous: str | None,
    has_rounds: bool,
) -> tuple[int, str, Any, int, str | None]:
    history = assembler.context_blocks(
        count_tokens=models.count_tokens,
        token_budget=models.qwen.max_model_len,
        allow_large_tail=True,
    )
    tail = history[-1].decode() if history else None
    readonly = [b.decode() for b in history[:-1]]
    tail_tokens = models.count_tokens(tail) if tail else 0
    required_output = max(512, tail_tokens + 128)
    has_title = False
    if has_rounds:
        root = ElementTree.fromstring(assembler.finalize())
        has_title = any(child.tag.endswith("}title") for child in root)
    while end > start:
        payload = {
            "image_id": image_id,
            "source_start": start,
            "source_end": end,
            "is_last_window_of_image": end == len(ocr),
            "document_has_title": has_title,
            "readonly_blocks": readonly,
            "mutable_tail": tail,
            "ocr_text": ocr[start:end],
            "retry_errors": errors,
            "previous_response": previous,
        }
        prompt = json.dumps(payload, ensure_ascii=False)
        inspection = models.inspect(path, prompt, system)
        output = min(
            models.qwen.max_output_tokens,
            models.qwen.max_model_len - inspection.prompt_tokens - CONTEXT_MARGIN,
        )
        if output >= required_output:
            return end, prompt, inspection, output, tail
        # Drop expendable context first; never shorten the mutable tail.
        if previous is not None:
            previous = None
        elif readonly:
            readonly = []
        else:
            end = shorter_end(ocr, start, end)
    raise PipelineError(
        "CONTEXT_BUDGET_EXCEEDED",
        "Complete tail, system prompt, image and output cannot fit; checkpoint retained. "
        "This block requires a larger supported budget or a separately designed structural patch.",
    )


def run_document(
    store: DocumentStore,
    models: Any,
    *,
    retry_failed: bool = False,
    progress: Callable[[str], None] = print,
) -> Path:
    """Caller holds document and GPU locks; models can be replaced in CPU tests."""
    state = store.state
    assembler, image_index, offset = replay(store)
    output_path = store.root / "document.c2d.xml"
    if state["status"] in {"complete", "needs_review"}:
        if image_index != len(state["ordered_image_ids"]):
            raise PipelineError(
                "CHECKPOINT_INVALID", "Completed state has unconsumed input"
            )
        final = assembler.finalize()
        if digest(final) != state.get("output_sha256"):
            raise PipelineError(
                "CHECKPOINT_INVALID", "Completed document hash mismatch"
            )
        if output_path.is_symlink() or (
            output_path.exists() and output_path.read_bytes() != final
        ):
            raise PipelineError(
                "OUTPUT_CONFLICT", "Existing final XML differs from checkpoint"
            )
        atomic_write(output_path, final)
        return output_path

    run_id = uuid4().hex
    run_dir = store.root / "runs" / run_id
    run_info = {"run_id": run_id, "started_at": now(), "status": "running"}
    state["runs"].append(run_info)
    state["error"] = None
    store.save()
    try:
        verify_previous_cleanup(store.root / "runs")
        system = c2d_system_prompt()
        store.bind_contract(
            {
                "pipeline_version": 1,
                "prompt_id": "c2d_system",
                "prompt_sha256": prompt_fingerprint(system),
                "model_configuration": models.prepare(),
                "max_attempts": MAX_ATTEMPTS,
                "context_margin": CONTEXT_MARGIN,
            }
        )
        atomic_write(run_dir / "system-prompt.txt", system.encode())
        for image_id in state["ordered_image_ids"]:
            image = state["images"][image_id]
            prepared = f"prepared/{image_id}.png"
            info = models.image_info(store.root / image["path"], store.root / prepared)
            if (
                image.get("model_image_sha256") is not None
                and image["model_image_sha256"] != info["model_image_sha256"]
            ):
                raise PipelineError(
                    "IMAGE_PREPROCESSING_CHANGED", "Normalized input changed on resume"
                )
            image.update(info)
            image["model_path"] = prepared
        store.save()

        pending = [
            i for i in state["ordered_image_ids"] if state["images"][i]["ocr"] is None
        ]
        if pending:
            state["status"] = "ocr"
            store.save()
            progress("Loading Paddle; processing OCR sequentially")
            with models.phase("paddle", run_dir):
                for image_id in pending:
                    _run_ocr(store, models, image_id, retry_failed, progress)

        if image_index < len(state["ordered_image_ids"]):
            state["status"] = "assembling"
            store.save()
            progress("Paddle released; loading Qwen for ordered C2D updates")
            with models.phase("qwen", run_dir):
                while image_index < len(state["ordered_image_ids"]):
                    image_id = state["ordered_image_ids"][image_index]
                    assembler, offset = _run_round(
                        store,
                        models,
                        assembler,
                        image_id,
                        offset,
                        system,
                        retry_failed,
                        progress,
                    )
                    if offset == len(state["images"][image_id]["ocr"]["content"]):
                        image_index += 1
                        offset = 0

        final = assembler.finalize()
        if output_path.is_symlink() or (
            output_path.exists() and output_path.read_bytes() != final
        ):
            raise PipelineError(
                "OUTPUT_CONFLICT", "Refusing to replace a different final XML"
            )
        atomic_write(output_path, final)
        review = any(r["content_check"]["needs_review"] for r in state["rounds"])
        state["status"] = "needs_review" if review else "complete"
        state["output_sha256"] = digest(final)
        run_info["status"] = state["status"]
        return output_path
    except BaseException as exc:
        state["status"] = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        )
        state["error"] = {
            "code": getattr(exc, "code", type(exc).__name__),
            "message": str(exc) or type(exc).__name__,
        }
        run_info["status"] = state["status"]
        raise
    finally:
        run_info["ended_at"] = now()
        store.save()


def _run_ocr(
    store: DocumentStore,
    models: Any,
    image_id: str,
    retry_failed: bool,
    progress: Callable[[str], None],
) -> None:
    image = store.state["images"][image_id]
    attempts = image["ocr_attempts"]
    limit = len(attempts) + MAX_ATTEMPTS if retry_failed else MAX_ATTEMPTS
    # A deterministic capacity failure requires explicit user retry, not another
    # automatic request with the same insufficient 4K ceiling.
    if (
        not retry_failed
        and attempts
        and attempts[-1].get("error_code") == "OCR_TRUNCATED"
    ):
        raise PipelineError(
            "OCR_TRUNCATED", f"{image_id}: 4K OCR output boundary exceeded"
        )
    while len(attempts) < limit:
        attempt_id = uuid4().hex
        attempt = {"attempt_id": attempt_id, "started_at": now(), "status": "started"}
        attempts.append(attempt)
        store.save()
        started = time.monotonic()
        try:
            result = models.ocr(store.root / image["model_path"])
            response_ref = store.artifact(
                f"ocr/{image_id}/{attempt_id}.json", result.raw_response
            )
            attempt["response_ref"] = response_ref
            reason = finish_reason(result)
            attempt["finish_reason"] = reason
            if reason == "length":
                raise PipelineError(
                    "OCR_TRUNCATED",
                    f"{image_id}: OCR reached 4096 tokens; response retained",
                )
            if reason != "stop" or not result.content.strip():
                raise PipelineError(
                    "OCR_INCOMPLETE",
                    f"{image_id}: empty or abnormal OCR finish ({reason})",
                )
            image["ocr"] = {
                "content": result.content,
                "response_ref": response_ref,
                "usage": result.raw_response.get("usage"),
                "finish_reason": reason,
                "completed_at": now(),
            }
            attempt["status"] = "accepted"
            progress(f"OCR ready: {image_id}")
            return
        except Exception as exc:
            attempt["status"] = "failed"
            attempt["error"] = str(exc)
            attempt["error_code"] = getattr(exc, "code", type(exc).__name__)
            if attempt["error_code"] == "OCR_TRUNCATED":
                raise
        finally:
            attempt["request_seconds"] = time.monotonic() - started
            attempt["ended_at"] = now()
            store.save()
    raise PipelineError(
        "OCR_RETRIES_EXHAUSTED",
        f"{image_id}: use --retry-failed after addressing the error",
    )


def _run_round(
    store: DocumentStore,
    models: Any,
    assembler: C2DAssembler,
    image_id: str,
    start: int,
    system: str,
    retry_failed: bool,
    progress: Callable[[str], None],
) -> tuple[C2DAssembler, int]:
    state = store.state
    image = state["images"][image_id]
    ocr = image["ocr"]["content"]
    index = len(state["rounds"])
    past = [a for a in state["attempts"] if a["round_index"] == index]
    limit = len(past) + MAX_ATTEMPTS if retry_failed else MAX_ATTEMPTS
    errors = past[-1].get("errors", []) if past else []
    end = past[-1].get("next_source_end", past[-1]["source_end"]) if past else len(ocr)
    previous = None
    if end <= start:
        raise PipelineError(
            "QWEN_TRUNCATED", "Cannot split source window further while retaining tail"
        )
    while len(past) < limit:
        end, prompt, inspection, output, tail = plan_request(
            models,
            assembler,
            store.root / image["model_path"],
            image_id,
            ocr,
            start,
            end,
            system,
            errors,
            previous,
            index > 0,
        )
        attempt_id = uuid4().hex
        prefix = f"rounds/{index:04d}/{attempt_id}"
        request_ref = store.artifact(
            f"{prefix}/request.json",
            {
                "system_prompt": system,
                "user_prompt": prompt,
                "image_id": image_id,
                "image_sha256": image["sha256"],
                "model_image_sha256": image["model_image_sha256"],
                "max_tokens": output,
                "temperature": 0,
                "enable_thinking": False,
                "token_inspection": inspection.to_dict(),
            },
        )
        atomic_write(
            store.root / prefix / "rendered-prompt.txt",
            inspection.rendered_prompt.encode(),
        )
        attempt = {
            "round_index": index,
            "attempt_id": attempt_id,
            "image_id": image_id,
            "source_start": start,
            "source_end": end,
            "request_ref": request_ref,
            "started_at": now(),
            "status": "started",
        }
        state["attempts"].append(attempt)
        past.append(attempt)
        store.save()
        started = time.monotonic()
        committing = False
        try:
            result = models.generate(
                store.root / image["model_path"], prompt, system, inspection, output
            )
            attempt["response_ref"] = store.artifact(
                f"{prefix}/response.json", result.raw_response
            )
            reason = finish_reason(result)
            attempt["finish_reason"] = reason
            previous = result.content
            if reason == "length":
                end = shorter_end(ocr, start, end)
                attempt["next_source_end"] = end
                previous = None  # The next request uses a smaller source window.
                raise PipelineError(
                    "QWEN_TRUNCATED",
                    "Output truncated; regenerate a smaller source window",
                )
            if reason != "stop" or not result.content.strip():
                raise PipelineError(
                    "QWEN_INCOMPLETE", f"Empty or abnormal model finish ({reason})"
                )
            candidate = deepcopy(assembler)
            validation = candidate.apply_update(result.content)
            errors = [f"{i.code}: {i.message}" for i in validation.issues][:12]
            report: dict[str, Any] = {}
            if validation.valid:
                report, errors = content_check(result.content, tail, ocr[start:end])
            attempt["validation_ref"] = store.artifact(
                f"{prefix}/validation.json",
                {
                    "xml_valid": validation.valid,
                    "errors": errors,
                    "content_check": report,
                },
            )
            if errors:
                raise PipelineError("UPDATE_REJECTED", "\n".join(errors))
            record = {
                "round_index": index,
                "image_id": image_id,
                "source_start": start,
                "source_end": end,
                "source_sha256": digest(ocr[start:end].encode()),
                "base_document_sha256": document_hash(assembler, index > 0),
                "update_sha256": digest(result.content.encode()),
                "update_xml": result.content,
                "attempt_id": attempt_id,
                "content_check": report,
                "committed_at": now(),
            }
            committing = True
            state["rounds"].append(record)
            attempt["status"] = "accepted"
            # The accepted update and its identity/consumed range are committed
            # atomically. Restart always reconstructs a fresh assembler journal.
            store.save()
            progress(
                f"C2D round {index + 1} committed: {image_id}, OCR chars {start}:{end}"
            )
            return candidate, end
        except Exception as exc:
            if committing:
                # Persistence/progress failures must never retry generation on
                # an already appended journal entry. Recovery replays the disk state.
                raise
            errors = [f"{getattr(exc, 'code', type(exc).__name__)}: {str(exc)}"[:3000]]
            attempt["status"] = "failed"
            attempt["errors"] = errors
        finally:
            attempt["request_seconds"] = time.monotonic() - started
            attempt["ended_at"] = now()
            store.save()
        if end <= start:
            break
    raise PipelineError(
        "QWEN_RETRIES_EXHAUSTED",
        f"Round {index + 1} retained its old checkpoint. Last errors: {errors}. "
        "Use --retry-failed only after reviewing the artifacts.",
    )
