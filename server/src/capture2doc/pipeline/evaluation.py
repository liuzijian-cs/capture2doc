"""Read-only artifact verification and explicitly sampled visual-fidelity checks.

This module never invokes models or edits checkpoints. Text differences and
duplicates are diagnostics, not acceptance gates or automatic content edits.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from lxml import etree

from capture2doc.formats.c2d_xml import validate_document, validate_update

from .blocks import document_xml, envelope, plain_text, public_block
from .document import BlockStore, MARGIN, read_artifact


def _tree(xml: str | None) -> Any:
    if not isinstance(xml, str) or not xml or not validate_update(envelope(xml)).valid:
        return None
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    wrapped = etree.fromstring(envelope(xml).encode(), parser)
    return wrapped[0] if len(wrapped) == 1 else None


def proposal_text_diagnostic(value: dict) -> dict:
    """Compare independently returned text to valid XML, preserving both in full."""
    original = value.get("text")
    node = _tree(value.get("xml"))
    xml_text = plain_text(node) if node is not None else None
    comparable = isinstance(original, str) and xml_text is not None
    return {
        "proposal_text": original,
        "valid_xml_text": xml_text,
        "comparable": comparable,
        "exact_match": original == xml_text if comparable else None,
        "whitespace_normalized_match": (
            "".join(original.split()) == "".join(xml_text.split())
            if comparable
            else None
        ),
        "diagnostic_only": True,
    }


def duplicate_blocks(blocks: list[dict], *, minimum_characters: int = 80) -> list[dict]:
    """Find long exact repeats after whitespace normalization, without deleting."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for index, block in enumerate(blocks):
        text = block.get("text")
        if isinstance(text, str):
            key = "".join(text.split())
            if len(key) >= minimum_characters:
                grouped[key].append({"block_id": index, "text": text})
    return [
        {
            "block_ids": [row["block_id"] for row in rows],
            "text": rows[0]["text"],
            "normalized_characters": len(key),
            "byte_identical_text": len({row["text"] for row in rows}) == 1,
            "comparison": "exact after whitespace removal; no fuzzy matching",
            "diagnostic_only": True,
        }
        for key, rows in grouped.items()
        if len(rows) > 1
    ]


def visual_checks(blocks: list[dict], checks: list[dict]) -> dict:
    """A match counts a final block once, even when nested style nodes match."""
    results = []
    seen = set()
    for check in checks:
        check_id = check.get("id")
        if not isinstance(check_id, str) or not check_id or check_id in seen:
            raise ValueError("Visual checks require unique nonempty string ids")
        seen.add(check_id)
        kind = check.get("kind", "content")
        if kind not in {"content", "bold", "highlight", "link"}:
            raise ValueError(f"Unknown visual check kind: {kind}")
        pattern = re.compile(check["pattern"])
        expected = check.get("expected", {})
        lower, upper = expected.get("min", 1), expected.get("max")
        if (
            not isinstance(lower, int)
            or isinstance(lower, bool)
            or lower < 0
            or (
                upper is not None
                and (
                    not isinstance(upper, int)
                    or isinstance(upper, bool)
                    or upper < lower
                )
            )
        ):
            raise ValueError(f"Invalid min/max for visual check {check_id}")
        matches = []
        for index, block in enumerate(blocks):
            if check.get("image_id") and block.get("image_id") != check["image_id"]:
                continue
            root = _tree(block.get("xml"))
            if root is None:
                continue
            selected = []
            for node in root.iter():
                tag = etree.QName(node).localname
                if (
                    (kind == "content" and node is root)
                    or (
                        kind == "bold"
                        and tag in {"b", "title", "h1", "h2", "h3", "h4", "h5", "h6"}
                    )
                    or (
                        kind == "highlight"
                        and tag == "span"
                        and node.get("background-color")
                    )
                    or (kind == "link" and tag == "a" and node.get("href"))
                ):
                    selected.append(plain_text(node))
            if any(pattern.search(text) for text in selected):
                matches.append(index)
        passed = len(matches) >= lower and (upper is None or len(matches) <= upper)
        results.append(
            {**check, "actual": len(matches), "block_ids": matches, "passed": passed}
        )
    return {
        "scope": "Sampled manual visual anchors; not complete document recognition accuracy",
        "count_unit": "matching final blocks, not regex occurrences",
        "checks": results,
        "passed": sum(row["passed"] for row in results),
        "total": len(results),
        "all_passed": all(row["passed"] for row in results) if results else None,
    }


def evaluate(root: str | Path, *, checks: list[dict] | None = None) -> dict:
    """Verify a completed V2 export against its journal and actual model traces.

    Corrupt state/input identities raise. Other failed verification checks are
    returned explicitly, so a report remains available for a failed evaluation.
    """
    root = Path(root).expanduser().resolve()
    store = BlockStore(root)
    store.load()
    state = store.state
    doc = json.loads(read_artifact(store, "document.json"))["doc"]
    verification = []

    def verify(name: str, passed: bool, **details: Any) -> None:
        verification.append({"id": name, "passed": bool(passed), **details})

    verify("completed", state["status"] == doc["processing_status"] == "completed")
    verify(
        "image_order",
        [b["image_id"] for b in state["batches"]] == state["ordered_image_ids"],
    )
    verify("document_identity", doc["document_id"] == state["document_id"])
    verify(
        "block_projection",
        doc["blocks"] == [public_block(b, i) for i, b in enumerate(state["blocks"])],
    )
    verify(
        "block_numbering",
        [b["block_id"] for b in doc["blocks"]] == list(range(len(doc["blocks"]))),
    )
    for filename, expected_hash in state.get("exports", {}).items():
        actual_hash = hashlib.sha256(read_artifact(store, filename)).hexdigest()
        verify(f"export_hash:{filename}", actual_hash == expected_hash)
    expected_xml = document_xml(state["blocks"], state["lang"])
    holes = any(b["status"] == "unresolved" for b in state["blocks"])
    expected_status = (
        ("partial" if holes else "complete") if expected_xml else "unavailable"
    )
    verify("xml_status", doc["xml_status"] == expected_status)
    xml = None
    if expected_xml is not None:
        filename = "document.partial.c2d.xml" if holes else "document.c2d.xml"
        xml = read_artifact(store, filename)
        verify("xml_valid", validate_document(xml).valid)
        verify("xml_projection", xml == expected_xml)

    rows = []
    observed_prompt_matches = []
    max_context = (
        state.get("contract", {})
        .get("model_configuration", {})
        .get("qwen", {})
        .get("settings", {})
        .get("max_model_len")
    )
    for batch in state["batches"]:
        draft = json.loads(
            read_artifact(store, batch["draft_ref"], batch["draft_sha256"])
        )
        calls, proposals = [], []
        for attempt in draft["attempts"]:
            proposal = attempt.get("proposal")
            if isinstance(proposal, dict):
                values = list(proposal.get("blocks", []))
                if isinstance(proposal.get("tail"), dict):
                    values.insert(0, proposal["tail"])
                proposals.append(
                    {
                        "attempt_id": attempt["attempt_id"],
                        "kind": attempt["kind"],
                        "blocks": [
                            proposal_text_diagnostic(value)
                            for value in values
                            if isinstance(value, dict)
                        ],
                    }
                )
            for call in attempt["calls"]:
                request = json.loads(read_artifact(store, call["request_ref"]))
                inspection = request.get("inspection", {})
                context = inspection.get("max_model_len", max_context)
                inspected = inspection.get("prompt_tokens")
                identifier = call["call_id"]
                verify(
                    f"preflight_record:{identifier}", inspected == call["prompt_tokens"]
                )
                budget_ok = (
                    isinstance(context, int)
                    and call["prompt_tokens"] + call["max_output_tokens"] + MARGIN
                    <= context
                )
                verify(f"context_budget:{identifier}", budget_ok, max_model_len=context)
                row = {
                    "call_id": identifier,
                    "kind": attempt["kind"],
                    "seconds": call.get("request_seconds"),
                    "prompt_tokens": call["prompt_tokens"],
                    "max_output_tokens": call["max_output_tokens"],
                    "response_available": "response_ref" in call,
                    "tool": call.get("tool_request", {}).get("action"),
                }
                if "response_ref" in call:
                    raw = json.loads(
                        read_artifact(
                            store, call["response_ref"], call.get("response_sha256")
                        )
                    )
                    usage = raw.get("usage", {})
                    matches = usage.get("prompt_tokens") == inspected
                    observed_prompt_matches.append(matches)
                    verify(f"actual_prompt_tokens:{identifier}", matches)
                    row.update(
                        actual_prompt_tokens=usage.get("prompt_tokens"),
                        output_tokens=usage.get("completion_tokens"),
                        finish_reason=(raw.get("choices") or [{}])[0].get(
                            "finish_reason"
                        ),
                        prompt_count_matches=matches,
                    )
                calls.append(row)
        rows.append(
            {
                "image_id": batch["image_id"],
                "candidate_count": len(draft["blocks"]),
                "candidate_texts": [
                    {
                        "internal_id": b["id"],
                        "text": b.get("text"),
                        "status": b["status"],
                        "repair_attempts": b["repair_attempts"],
                    }
                    for b in draft["blocks"]
                ],
                "statuses": dict(Counter(b["status"] for b in draft["blocks"])),
                "repair_attempts": sum(
                    a["kind"] == "repair" for a in draft["attempts"]
                ),
                "repair_request_count": sum(c["kind"] == "repair" for c in calls),
                "errors": [
                    a["error"]["code"] for a in draft["attempts"] if a.get("error")
                ],
                "calls": calls,
                "proposal_text_diagnostics": proposals,
            }
        )
    metrics = [
        json.loads(p.read_text())
        for p in sorted((root / "runs").glob("*/*.metrics.json"))
    ]
    verify("phase_metrics_available", bool(metrics))
    for index, phase in enumerate(metrics):
        verify(
            f"phase_cleanup:{index}",
            phase.get("cleanup_verified") or phase.get("recovery_verified_at"),
        )
    visual = visual_checks(state["blocks"], checks or [])
    return {
        "root": str(root),
        "verification_passed": all(row["passed"] for row in verification),
        "verification": verification,
        "image_order": [b["image_id"] for b in state["batches"]],
        "blocks": len(doc["blocks"]),
        "status_counts": dict(Counter(b["status"] for b in doc["blocks"])),
        "needs_review": doc["needs_review"],
        "semantic_fidelity_verified": False,
        "xml_status": doc["xml_status"],
        "xml_sha256": hashlib.sha256(xml).hexdigest() if xml else None,
        "styles": dict(
            Counter(etree.QName(n).localname for n in etree.fromstring(xml).iter())
        )
        if xml
        else {},
        "images": rows,
        "phases": [
            {
                k: m.get(k)
                for k in (
                    "model",
                    "load_seconds",
                    "unload_seconds",
                    "cleanup_verified",
                    "recovery_verified_at",
                    "gpu_memory",
                )
            }
            for m in metrics
        ],
        "all_prompt_counts_match": all(observed_prompt_matches)
        if observed_prompt_matches
        else None,
        "observed_response_count": len(observed_prompt_matches),
        "long_exact_duplicates": duplicate_blocks(state["blocks"]),
        "visual_sample": visual,
    }
