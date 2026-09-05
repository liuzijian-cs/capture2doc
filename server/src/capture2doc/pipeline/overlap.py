"""Conservative, auditable removal of a model-declared image-prefix overlap.

Similarity only retrieves read-only context. It never authorizes omission.
The model must still transcribe the entire current image and explicitly claim
that a contiguous prefix shows the same source region. Exact content and
structural checks establish textual coverage, not independent visual identity;
``applied_overlap`` deliberately records that limitation.

Integration order: initialize -> bind_overlap -> existing repairs/fallback ->
resolve_overlap -> existing atomic batch commit. Keep omitted_blocks in the
durable draft. A verified all-overlap batch can have zero additional blocks.
"""

from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
from hashlib import sha256
import json
import re
from typing import Any

from lxml import etree

from capture2doc.formats.c2d_xml.validator import _parse_and_validate

from .blocks import envelope, plain_text, structural_text

MIN_BODY_CHARACTERS = 40
MAX_HISTORY_REFERENCES = 64
RELATION = "same_source_prefix_overlap"


def _digest(value: Any) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _content_digest(block: dict) -> str:
    return _digest({"text": block.get("text"), "xml": block.get("xml")})


def _eligible(block: dict) -> bool:
    return (
        block.get("status") == "ok"
        and isinstance(block.get("text"), str)
        and bool(block["text"].strip())
        and isinstance(block.get("xml"), str)
        and type(block.get("version")) is int
        and block["version"] >= 0
    )


def history_item(block: dict, index: int) -> dict:
    """A stable model-facing reference; no random internal UUID is exposed."""
    if type(index) is not int or index < 0 or not _eligible(block):
        raise ValueError("History requires a valid, nonempty committed block")
    return {
        "block_id": index,
        "version": block["version"],
        "history_ref": f"h{index}v{block['version']}:{_content_digest(block)}",
        "text": block["text"],
        "xml": block["xml"],
        "status": block["status"],
    }


def _retrieval_text(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def retrieve_history(blocks: list[dict], ocr_text: str, limit: int = 10) -> list[dict]:
    """Retrieve one complete, contiguous window from a prior source batch.

    Callers must include this additional context in real tokenizer preflight;
    this count limit is not a token budget. Retrieval does not mutate blocks.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_HISTORY_REFERENCES:
        raise ValueError("History limit must be an integer from 1 to 64")
    if not isinstance(ocr_text, str) or not ocr_text.strip():
        return []
    reference = _retrieval_text(ocr_text)
    groups: list[list[tuple[int, float]]] = []
    previous_origin = object()
    for index, block in enumerate(blocks):
        if not _eligible(block):
            previous_origin = object()
            continue
        origin = block.get("image_id")
        if not groups or origin != previous_origin:
            groups.append([])
        previous_origin = origin
        content = _retrieval_text(block["text"])
        matches = SequenceMatcher(None, content, reference, autojunk=False)
        longest = max((m.size for m in matches.get_matching_blocks()), default=0)
        score = (
            sum(m.size for m in matches.get_matching_blocks()) / max(len(content), 1)
            if longest >= 12
            else 0.0
        )
        groups[-1].append((index, score))
    options = []
    for group in groups:
        if not group or not any(score for _, score in group):
            continue
        width = min(limit, len(group))
        for start in range(len(group) - width + 1):
            window = group[start : start + width]
            options.append((sum(score for _, score in window), window[0][0], window))
    if not options:
        return []
    _, _, selected = max(options, key=lambda item: (item[0], item[1]))
    return [history_item(blocks[index], index) for index, _ in selected]


def _warn(draft: dict, code: str, message: str) -> bool:
    record = {"code": code, "message": message, "needs_review": True}
    warnings = draft.setdefault("overlap_warnings", [])
    if record not in warnings:
        warnings.append(record)
    draft["overlap_warning"] = record
    if draft.get("applied_overlap") is None:
        draft["overlap_status"] = "rejected"
    return False


def _roots(block: dict) -> list[str] | None:
    value = block.get("lineage")
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        return None
    return value


def bind_overlap(
    draft: dict,
    claim: Any,
    sent_history: list[dict],
    committed: list[dict] | None = None,
) -> bool:
    """Bind a generation claim to original lineage and sent history snapshots.

    Pass ``committed`` in production to also bind immutable internal identities.
    A failed claim only produces a warning; legal candidates remain untouched.
    """
    if claim is None:
        return False
    if not isinstance(claim, dict):
        return _warn(draft, "OVERLAP_CLAIM_INVALID", "重叠声明必须是对象或 null。")
    claim_hash = _digest(claim)
    binding = draft.get("overlap_binding")
    if binding is not None:
        if binding.get("claim_sha256") == claim_hash:
            return True
        return _warn(draft, "OVERLAP_REBIND_REJECTED", "不能更换已绑定的重叠声明。")
    if draft.get("applied_overlap") is not None or draft.get("committed"):
        return _warn(
            draft, "OVERLAP_ALREADY_TERMINAL", "已提交批次不能绑定新重叠声明。"
        )
    values = draft.get("blocks", [])
    if not draft.get("initialized") or not values:
        return _warn(
            draft,
            "OVERLAP_OBSERVATIONS_REQUIRED",
            "必须先保留本图完整候选，不能用空响应声明全部重复。",
        )
    if any(block.get("replaces_tail") for block in values):
        return _warn(
            draft, "OVERLAP_TAIL_CONFLICT", "本轮尾块续接与前缀去重不能同时声明。"
        )
    allowed = {
        "relation",
        "candidate_prefix_count",
        "history_refs",
        "first_history_match",
        "whole_image_has_no_new_content",
    }
    count = claim.get("candidate_prefix_count")
    refs = claim.get("history_refs")
    first_match = claim.get("first_history_match", "full_block")
    no_new = claim.get("whole_image_has_no_new_content", False)
    if (
        set(claim) - allowed
        or claim.get("relation") != RELATION
        or type(count) is not int
        or not 1 <= count <= len(values)
        or not isinstance(refs, list)
        or not 1 <= len(refs) <= MAX_HISTORY_REFERENCES
        or any(not isinstance(ref, str) for ref in refs)
        or len(set(refs)) != len(refs)
        or first_match not in {"full_block", "suffix_of_first_history"}
        or type(no_new) is not bool
        or (no_new and count != len(values))
    ):
        return _warn(
            draft,
            "OVERLAP_CLAIM_INVALID",
            "仅允许本图连续前缀、唯一历史引用及明确的首块匹配方式。",
        )
    lookup = {item.get("history_ref"): item for item in sent_history}
    snapshots = []
    for ref in refs:
        item = lookup.get(ref)
        try:
            if item is None:
                raise ValueError("invalid context")
            snapshot = history_item(item, item["block_id"])
            if snapshot["history_ref"] != ref:
                raise ValueError("context hash changed")
            snapshot["content_sha256"] = _content_digest(item)
            if committed is not None:
                source = committed[item["block_id"]]
                if history_item(source, item["block_id"]) != history_item(
                    item, item["block_id"]
                ):
                    raise ValueError("history changed")
                snapshot["internal_id"] = source["id"]
            snapshots.append(snapshot)
        except (KeyError, IndexError, TypeError, ValueError):
            return _warn(
                draft,
                "OVERLAP_HISTORY_INVALID",
                "历史引用不存在、未发送、已过期或摘要不符。",
            )
    indices = [item["block_id"] for item in snapshots]
    if indices != list(range(indices[0], indices[0] + len(indices))):
        return _warn(
            draft,
            "OVERLAP_HISTORY_NOT_CONTIGUOUS",
            "历史引用必须按原顺序组成连续区间。",
        )
    selected_roots = [_roots(block) for block in values[:count]]
    remaining_roots = [_roots(block) for block in values[count:]]
    if any(roots is None for roots in selected_roots + remaining_roots):
        return _warn(
            draft, "OVERLAP_LINEAGE_AMBIGUOUS", "候选 lineage 不完整，保留全部内容。"
        )
    roots = list(dict.fromkeys(root for group in selected_roots for root in group))
    if set(roots) & {root for group in remaining_roots for root in group}:
        return _warn(
            draft,
            "OVERLAP_LINEAGE_AMBIGUOUS",
            "重叠前缀与新增内容共享 lineage，无法安全划分。",
        )
    binding = {
        "claim_sha256": claim_hash,
        "image_id": draft["image_id"],
        "roots": roots,
        "initial_candidate_ids": [block["id"] for block in values[:count]],
        "initial_prefix_count": count,
        "first_history_match": first_match,
        "history": snapshots,
    }
    binding["binding_sha256"] = _digest(binding)
    draft["overlap_claim"] = deepcopy(claim)
    draft["overlap_binding"] = binding
    draft["overlap_status"] = "bound"
    return True


def _tag(node: Any) -> str:
    return etree.QName(node).localname


def _parse_blocks(blocks: list[dict]) -> list[Any]:
    roots = []
    for block in blocks:
        if not _eligible(block):
            raise ValueError("只能验证模型成功且非空的块；兜底或缺失不能授权省略。")
        root, validation = _parse_and_validate(envelope(block["xml"]), "c2d-update")
        if not validation.valid or root is None or len(root) != 1:
            raise ValueError("候选或历史 XML 无法完整验证。")
        if structural_text(plain_text(root[0])) != structural_text(block["text"]):
            raise ValueError("候选或历史的 XML/text 不一致。")
        roots.append(root[0])
    return roots


def _profile(roots: list[Any]) -> list[Any]:
    """Preserve structures whose changes can alter meaning despite equal text."""
    result = []
    for root in roots:
        for node in root.iter():
            tag = _tag(node)
            if tag == "pre":
                result.append(("pre", plain_text(node)))
            elif tag == "code" and not any(
                _tag(parent) == "pre" for parent in node.iterancestors()
            ):
                result.append(("inline_code", plain_text(node)))
            elif tag == "table":
                result.append(
                    (
                        "table",
                        [
                            (
                                _tag(part),
                                [
                                    [
                                        (
                                            _tag(cell),
                                            cell.get("rowspan", "1"),
                                            cell.get("colspan", "1"),
                                            structural_text(plain_text(cell)),
                                        )
                                        for cell in row
                                    ]
                                    for row in part
                                ],
                            )
                            for part in node
                        ],
                    )
                )
            elif tag in {"ul", "ol"}:
                result.append(
                    (
                        tag,
                        # v0.1 rejects list attributes; retain any validated
                        # attributes here if the contract later permits numbering.
                        tuple(sorted(node.attrib.items())),
                        [
                            (
                                structural_text(plain_text(li)),
                                [
                                    (_tag(child), structural_text(plain_text(child)))
                                    for child in li
                                    if _tag(child) in {"ul", "ol"}
                                ],
                            )
                            for li in node
                        ],
                    )
                )
            elif tag == "a":
                result.append(
                    ("a", node.get("href"), structural_text(plain_text(node)))
                )
            elif tag == "latex":
                result.append(("latex", plain_text(node)))
    return result


def _strict_match(current: list[Any], history: list[Any], first_match: str) -> bool:
    actual = [structural_text(plain_text(root)) for root in current]
    expected = [structural_text(plain_text(root)) for root in history]
    current_profile, history_profile = _profile(current), _profile(history)
    if first_match == "full_block":
        return (
            "".join(actual) == "".join(expected) and current_profile == history_profile
        )
    # A clipped first block may be a suffix, followed by whole historical blocks.
    if _tag(history[0]) not in {"p", "pre"} or len(history) < 2:
        return False
    if _tag(history[0]) == "pre":
        if _tag(current[0]) != "pre" or not current_profile or not history_profile:
            return False
        if "".join(actual[1:]) != "".join(expected[1:]):
            return False
        old_code, new_code = plain_text(history[0]), plain_text(current[0])
        offset = len(old_code) - len(new_code)
        if (
            not new_code
            or not old_code.endswith(new_code)
            or offset < 0
            or (offset and old_code[offset - 1] != "\n")
        ):
            return False
        history_profile[0] = ("pre", new_code)
    else:
        full_suffix = "".join(expected[1:])
        entire = "".join(actual)
        if not full_suffix or not entire.endswith(full_suffix):
            return False
        first = entire[: -len(full_suffix)]
        if not first or not expected[0].endswith(first):
            return False
        if _profile([history[0]]) or _profile([current[0]]):
            # Do not infer which hidden links/formulas belonged to the clipped prefix.
            return False
    return current_profile == history_profile


def resolve_overlap(draft: dict, committed: list[dict]) -> bool:
    """Omit only a proven textual prefix; no history or budgets are modified."""
    if draft.get("applied_overlap") is not None:
        return True
    binding = draft.get("overlap_binding")
    if binding is None or draft.get("overlap_status") == "rejected":
        return False
    if any(block.get("status") == "pending" for block in draft["blocks"]):
        return False  # Deferred until the existing repair/fallback queue is terminal.
    payload = {key: value for key, value in binding.items() if key != "binding_sha256"}
    if (
        _digest(payload) != binding.get("binding_sha256")
        or _digest(draft.get("overlap_claim")) != binding["claim_sha256"]
        or draft["image_id"] != binding["image_id"]
    ):
        return _warn(draft, "OVERLAP_BINDING_CHANGED", "重叠绑定或图片身份发生变化。")
    if any(block.get("replaces_tail") for block in draft["blocks"]):
        return _warn(draft, "OVERLAP_TAIL_CONFLICT", "尾块续接与前缀去重不能同时应用。")
    history = []
    for item in binding["history"]:
        index = item["block_id"]
        if not 0 <= index < len(committed):
            return _warn(draft, "OVERLAP_HISTORY_CHANGED", "历史引用范围已变化。")
        block = committed[index]
        if (
            block.get("version") != item["version"]
            or _content_digest(block) != item["content_sha256"]
            or ("internal_id" in item and block.get("id") != item["internal_id"])
        ):
            return _warn(
                draft, "OVERLAP_HISTORY_CHANGED", "历史身份、版本或内容已变化。"
            )
        history.append(block)
    bound_roots = set(binding["roots"])
    prefix = []
    seen = set()
    ended = False
    previous_rank = -1
    for block in draft["blocks"]:
        lineage = _roots(block)
        if lineage is None:
            return _warn(
                draft, "OVERLAP_LINEAGE_AMBIGUOUS", "修复后的 lineage 无法定位。"
            )
        overlap = set(lineage) & bound_roots
        if not overlap:
            ended = True
            continue
        if ended or overlap != set(lineage):
            return _warn(
                draft, "OVERLAP_LINEAGE_AMBIGUOUS", "修复跨越前缀边界或重排了重叠对象。"
            )
        rank = min(binding["roots"].index(root) for root in lineage)
        if rank < previous_rank:
            return _warn(
                draft, "OVERLAP_LINEAGE_AMBIGUOUS", "重叠对象的原始顺序已变化。"
            )
        previous_rank = rank
        prefix.append(block)
        seen.update(lineage)
    if not prefix or seen != bound_roots:
        return _warn(draft, "OVERLAP_LINEAGE_AMBIGUOUS", "无法完整覆盖原始重叠范围。")
    try:
        current_roots, history_roots = _parse_blocks(prefix), _parse_blocks(history)
    except ValueError as exc:
        return _warn(draft, "OVERLAP_BLOCK_UNVERIFIED", str(exc))
    body = [
        structural_text(plain_text(root))
        for root in history_roots
        if _tag(root) not in {"title", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}
    ]
    distinct = {structural_text(plain_text(root)) for root in history_roots}
    # Only currently observed body text can establish a clipped overlap.
    # Sum across repaired splits; splitting a verified body cannot erase evidence.
    observed_body_characters = sum(
        len(re.sub(r"\s+", "", plain_text(root)))
        for root in current_roots
        if _tag(root) not in {"title", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}
    )
    if (
        len(history_roots) < 2
        or len(distinct) < 2
        or max((len(re.sub(r"\s+", "", value)) for value in body), default=0)
        < MIN_BODY_CHARACTERS
        or observed_body_characters < MIN_BODY_CHARACTERS
    ):
        return _warn(
            draft,
            "OVERLAP_EVIDENCE_TOO_SHORT",
            "仅相同短标题、页眉或短句不足以省略正文。",
        )
    if not _strict_match(current_roots, history_roots, binding["first_history_match"]):
        return _warn(
            draft,
            "OVERLAP_CONTENT_CONFLICT",
            "当前前缀与历史的文字、符号、代码空白或有意义结构不一致；全部保留。",
        )
    omitted = deepcopy(prefix)
    remaining = draft["blocks"][len(prefix) :]
    applied = {
        "relation": RELATION,
        "image_id": draft["image_id"],
        "binding_sha256": binding["binding_sha256"],
        "history_refs": [item["history_ref"] for item in binding["history"]],
        "history_indices": [item["block_id"] for item in binding["history"]],
        "omitted_ids": [block["id"] for block in omitted],
        "omitted_sha256": _digest(omitted),
        "omitted_count": len(omitted),
        "remaining_count": len(remaining),
        "whole_image_has_no_new_content": not remaining,
        "textual_coverage_verified": True,
        "independent_visual_identity_verified": False,
        "needs_review": True,
    }
    draft["omitted_blocks"] = omitted
    draft["applied_overlap"] = applied
    draft["blocks"] = remaining
    draft["overlap_status"] = "applied"
    return True
