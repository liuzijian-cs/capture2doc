"""Versioned draft transactions and a bounded, serial repair coordinator."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from .blocks import add_errors, candidate, combined_errors, error, fallback

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
    if len(old) == len(replacements) == 1 and not replacements[0]["text"]:
        replacements[0]["text"] = old[0]["text"]
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
    # Preserve successful neighbors if a grouped repair accidentally drops their text.
    new_text = "".join(b["text"] or "" for b in replacements)
    for old_block in old:
        if (
            old_block["status"] == "ok"
            and old_block["text"]
            and old_block["text"] not in new_text
        ):
            for b in replacements:
                b.update(
                    status="pending", vlm_validation="failed", final_validation="failed"
                )
                add_errors(
                    b,
                    b["current_errors"]
                    + [
                        error(
                            "PRESERVED_CONTENT_LOSS",
                            "关联修复丢失了此前通过的块文字；保留邻居原文。",
                            [x["id"] for x in replacements],
                        )
                    ],
                )
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
