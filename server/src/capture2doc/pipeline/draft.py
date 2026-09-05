"""Versioned draft transactions and a bounded, serial repair coordinator."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from .blocks import (
    add_errors,
    candidate,
    combined_errors,
    error,
    fallback,
    protected_code,
    structural_text,
)

MAX_REPAIRS = 5


class RejectedPatch(ValueError):
    """A duplicate/stale/out-of-scope result never mutates the draft."""


def new_draft(image_id: str, old_tail: dict | None) -> dict:
    return {
        "image_id": image_id,
        "old_tail": deepcopy(old_tail),
        "blocks": [],
        "initialized": False,
        "budgets": {},
        "attempts": [],
        "active_attempt": None,
        "wave": 0,
        "queue": [],
        "committed": False,
    }


def initialize(draft: dict, proposal: dict) -> None:
    if draft["initialized"]:
        raise RejectedPatch("Initial response already applied")
    values = proposal.get("blocks")
    if not isinstance(values, list) or (not values and proposal.get("tail") is None):
        raise RejectedPatch("Expected blocks or an explicit tail continuation")
    tail = proposal.get("tail")
    if tail is not None and (not draft["old_tail"] or not isinstance(tail, dict)):
        raise RejectedPatch("Tail replacement outside the allowed scope")
    items = ([tail] if tail is not None else []) + values
    result = [candidate(value, draft["image_id"]) for value in items]
    if tail is not None:
        result[0]["replaces_tail"] = True
    draft["blocks"] = result
    draft["budgets"] = {b["id"]: 0 for b in result}
    draft["initialized"] = True
    check_tail(draft)


def check_tail(draft: dict) -> None:
    for b in draft["blocks"]:
        if not b.get("replaces_tail") or b["status"] != "ok":
            continue
        old = draft["old_tail"]
        # Full old tail retained in the first replacement block. Code whitespace matters.
        if not (b["text"] or "").startswith(old["text"] or ""):
            b.update(
                status="pending", vlm_validation="failed", final_validation="failed"
            )
            add_errors(
                b,
                [
                    error(
                        "TAIL_CONTENT_LOSS",
                        "旧尾块的全部文字必须原样保留在第一个替换块开头；不能拆散、删减或重复历史。",
                        [b["id"]],
                    )
                ],
            )


def composed(committed: list[dict], draft: dict) -> list[dict]:
    prefix = (
        committed[:-1]
        if any(b.get("replaces_tail") for b in draft["blocks"])
        else committed
    )
    return prefix + draft["blocks"]


def check_combined(committed: list[dict], draft: dict) -> None:
    errors = combined_errors(composed(committed, draft))
    ids = {b["id"] for b in draft["blocks"]}
    for record in errors:
        targets = set(record["target_blocks"]) & ids
        if not targets:
            # A cross-document root constraint can name the immutable prefix too.
            targets = {b["id"] for b in draft["blocks"] if b["status"] == "ok"}
        if not targets:
            raise ValueError("Committed document is invalid outside editable scope")
        for b in draft["blocks"]:
            if b["id"] in targets:
                b.update(
                    status="pending", vlm_validation="failed", final_validation="failed"
                )
                add_errors(b, b["current_errors"] + [record])


def next_queue(draft: dict) -> list[list[str]]:
    """Union related targets, preserving draft order; unrelated failures stay separate."""
    failed = [b for b in draft["blocks"] if b["status"] == "pending"]
    groups = [{b["id"]} for b in failed]
    for b in failed:
        for record in b["current_errors"]:
            target = set(record["target_blocks"]) & {x["id"] for x in failed}
            touching = [g for g in groups if g & target]
            if touching:
                merged = set().union(*touching)
                groups = [g for g in groups if g not in touching] + [merged]
    order = [b["id"] for b in draft["blocks"]]
    # Include intervening candidates in one contiguous splice, with shared budget.
    ranges = sorted(
        (min(order.index(i) for i in g), max(order.index(i) for i in g)) for g in groups
    )
    joined = []
    for lo, hi in ranges:
        if joined and lo <= joined[-1][1]:
            joined[-1] = (joined[-1][0], max(hi, joined[-1][1]))
        else:
            joined.append((lo, hi))
    return [order[lo : hi + 1] for lo, hi in joined]


def start_attempt(draft: dict, targets: list[str], kind: str = "repair") -> dict:
    by_id = {b["id"]: b for b in draft["blocks"]}
    if draft["active_attempt"] is not None:
        raise RejectedPatch("An attempt is already active")
    if any(i not in by_id for i in targets):
        raise RejectedPatch("Unknown repair target")
    roots = set(r for i in targets for r in by_id[i]["lineage"])
    if kind == "repair":
        if not roots or max(draft["budgets"][r] for r in roots) >= MAX_REPAIRS:
            raise RejectedPatch("Repair budget exhausted")
        for root in roots:
            draft["budgets"][root] += 1
        for i in targets:
            by_id[i]["repair_attempts"] = max(
                draft["budgets"][r] for r in by_id[i]["lineage"]
            )
    attempt = {
        "attempt_id": uuid4().hex,
        "kind": kind,
        "targets": targets,
        "target_versions": {i: by_id[i]["version"] for i in targets},
        "lineage": sorted(roots),
        "status": "started",
        "calls": [],
        "wave": draft["wave"],
        "proposal": None,
        "error": None,
    }
    draft["attempts"].append(attempt)
    draft["active_attempt"] = attempt["attempt_id"]
    return attempt


def preserve_content(old: list[dict], replacements: list[dict]) -> None:
    """Reject the entire patch before a failed rewrite can replace saved text."""
    old_text = "".join(b["text"] or "" for b in old)
    new_text = "".join(b["text"] or "" for b in replacements)
    expected, actual = structural_text(old_text), structural_text(new_text)
    if all(
        isinstance(b["text"], str) and (b["text"].strip() or b["status"] == "ok")
        for b in old
    ):
        preserved = expected == actual
    else:
        # Missing text may be recovered from the image. Retain every known
        # neighbor in order, without reusing one occurrence for multiple blocks.
        offset, preserved = 0, True
        for b in old:
            known = structural_text(b["text"] or "")
            position = actual.find(known, offset)
            if position < 0:
                preserved = False
                break
            offset = position + len(known)
    if not preserved:
        index = next(
            (i for i, (a, b) in enumerate(zip(expected, actual)) if a != b),
            min(len(expected), len(actual)),
        )
        raise RejectedPatch(
            "REPAIR_CONTENT_CHANGED: 结构修复必须保留整个目标组的文字、顺序和重复次数；"
            "失败块的独立 text 也必须保留。仅允许结构换行变化，不得改写、总结或删减。"
            f"首个差异位于归一化字符 {index}；"
            f"期望={expected[max(0, index - 24) : index + 72]!r}；"
            f"实际={actual[max(0, index - 24) : index + 72]!r}。"
            "原候选保持不变，请按 targets 的完整文字重新修复。"
        )
    # Code can move out of an invalid blockquote, but its indentation, line
    # breaks, spaces inside strings and trailing newline must remain exact.
    new_codes = [value for b in replacements for value in protected_code(b)]
    offset = 0
    for code in (value for b in old for value in protected_code(b)):
        matched_end = None
        for start in range(offset, len(new_codes)):
            combined = ""
            for end in range(start, len(new_codes)):
                combined += new_codes[end]
                if combined == code:
                    matched_end = end + 1
                    break
                if not code.startswith(combined):
                    break
            if matched_end is not None:
                break
        if matched_end is None:
            raise RejectedPatch(
                "REPAIR_CODE_WHITESPACE_CHANGED: 代码内容及缩进、换行、字符串内空格"
                "必须逐字保留；仅移动 pre/code 结构。"
                f"缺失的原始代码片段={code[:160]!r}。原候选保持不变。"
            )
        offset = matched_end


def apply_patch(draft: dict, attempt: dict, proposal: dict) -> None:
    if (
        attempt["status"] == "applied"
        or draft["active_attempt"] != attempt["attempt_id"]
    ):
        raise RejectedPatch("Duplicate or inactive attempt")
    by_id = {b["id"]: b for b in draft["blocks"]}
    versions = attempt["target_versions"]
    if any(i not in by_id or by_id[i]["version"] != v for i, v in versions.items()):
        raise RejectedPatch("Stale target version")
    if (
        proposal.get("attempt_id") != attempt["attempt_id"]
        or proposal.get("target_versions") != versions
    ):
        raise RejectedPatch("Response attempt/target scope mismatch")
    values = proposal.get("blocks")
    if not isinstance(values, list) or not values or proposal.get("tail") is not None:
        raise RejectedPatch("Repair must replace its nonempty target group only")
    targets = attempt["targets"]
    order = [b["id"] for b in draft["blocks"]]
    indices = [order.index(i) for i in targets]
    if indices != list(range(min(indices), max(indices) + 1)):
        raise RejectedPatch("Noncontiguous splice")
    old = [by_id[i] for i in targets]
    replacements = [
        candidate(v, draft["image_id"], lineage=attempt["lineage"]) for v in values
    ]
    # A malformed repair must not destroy the independently saved text. Only
    # reuse it for an unambiguous one-to-one replacement, never copy a whole
    # ancestor into every child of a split.
    if (
        len(old) == len(replacements) == 1
        and replacements[0]["status"] == "pending"
        and not replacements[0]["text"]
    ):
        replacements[0]["text"] = old[0]["text"]
    preserve_content(old, replacements)
    if old[0].get("replaces_tail"):
        tail_text = draft["old_tail"]["text"] or ""
        if not (replacements[0]["text"] or "").startswith(tail_text) or (
            tail_text and any(tail_text in (b["text"] or "") for b in replacements[1:])
        ):
            raise RejectedPatch(
                "TAIL_CONTENT_LOSS: 旧尾块全文必须原样保留在第一个替换块开头；"
                "不得移入其他子块，也不得在其他子块重复旧尾。整个原候选组保持不变。"
            )
    errors = [e for b in old for e in b["errors"]]
    guards = [e for b in old for e in b["guards"]]
    for index, b in enumerate(replacements):
        if index < len(old):
            temporary = b["id"]
            b["id"] = old[index]["id"]
            b["version"] = old[index]["version"] + 1
            for e in b["current_errors"]:
                e["target_blocks"] = [
                    b["id"] if i == temporary else i for i in e["target_blocks"]
                ]
        b["repair_attempts"] = max(draft["budgets"][r] for r in b["lineage"])
        b["errors"] = deepcopy(errors) + b["errors"]
        b["guards"] = deepcopy(guards) + [
            deepcopy(e)
            for e in errors
            if e["code"] not in {c["code"] for c in b["current_errors"]}
        ]
        b["original_candidate"] = deepcopy(
            old[min(index, len(old) - 1)]["original_candidate"]
        )
    if old[0].get("replaces_tail"):
        replacements[0]["replaces_tail"] = True
    draft["blocks"][min(indices) : max(indices) + 1] = replacements
    check_tail(draft)
    attempt["status"] = "applied"
    draft["active_attempt"] = None


def failed_attempt(draft: dict, attempt: dict, record: dict) -> None:
    for b in draft["blocks"]:
        if b["id"] in attempt["targets"]:
            add_errors(b, b["current_errors"] + [record])
    attempt.update(status="failed", error=record)
    draft["active_attempt"] = None


def resolve_fallback(draft: dict, sources: list[dict], committed: list[dict]) -> None:
    reserved = {r for b in committed for r in b["ocr_refs"]}
    for b in draft["blocks"]:
        if b.get("replaces_tail") and b["status"] == "pending":
            old_text = draft["old_tail"]["text"] or ""
            text = b["text"]
            b["text"] = (
                text[len(old_text) :]
                if isinstance(text, str) and text.startswith(old_text)
                else None
            )
            b.pop("replaces_tail")
            b["tail_restored"] = True
            # Only current-image refs may contribute; old content must never be duplicated.
            b["ocr_refs"] = [
                r for r in b["ocr_refs"] if r.startswith(draft["image_id"] + ":ocr:")
            ]
            source_text = "".join(
                source["text"]
                for source in sources
                if source["source_id"] in b["ocr_refs"]
            ).rstrip("\r\n")
            # Current-image provenance alone does not prove that an OCR line
            # excludes an overlapping old tail. Only a known new suffix can
            # authorize its matching OCR text; otherwise keep the safe suffix
            # or an unresolved hole, never the old text a second time.
            if b["text"] is None or structural_text(source_text) != structural_text(
                b["text"]
            ):
                b["ocr_refs"] = []
    fallback(draft["blocks"], sources, reserved=reserved)


def commit_blocks(committed: list[dict], draft: dict) -> list[dict]:
    if draft["committed"] or any(b["status"] == "pending" for b in draft["blocks"]):
        raise RejectedPatch("Batch already committed or not terminal")
    result = deepcopy(composed(committed, draft))
    if any(b.get("replaces_tail") for b in draft["blocks"]):
        old = draft["old_tail"]
        if (
            not committed
            or committed[-1]["id"] != old["id"]
            or committed[-1]["version"] != old["version"]
        ):
            raise RejectedPatch("Committed tail changed")
        replacement = result[len(committed) - 1]
        replacement["id"] = old["id"]
        replacement["version"] = old["version"] + 1
        replacement["ocr_refs"] = list(
            dict.fromkeys(old["ocr_refs"] + replacement["ocr_refs"])
        )
    if combined_errors(result):
        raise ValueError("Terminal batch still violates document constraints")
    return result
