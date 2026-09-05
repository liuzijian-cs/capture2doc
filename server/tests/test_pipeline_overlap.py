"""Prefix omission is explicit, exact, bounded, and independently recoverable."""

from __future__ import annotations

from copy import deepcopy
import json

from lxml import etree
import pytest

from capture2doc.pipeline.blocks import candidate, envelope, paragraph, plain_text
from capture2doc.pipeline.draft import (
    apply_patch,
    commit_blocks,
    initialize,
    new_draft,
    start_attempt,
)
from capture2doc.pipeline.overlap import (
    RELATION,
    bind_overlap,
    history_item,
    resolve_overlap,
    retrieve_history,
)


BODY = (
    "设备在所有采集通道完成数据校验之后才会写入归档文件，"
    "保存操作还应保留每个通道的原始时间戳及校准参数。"
)
SECOND = (
    "操作人员应当在更换传感器之后重新执行完整的标定程序，"
    "确认全部测量结果满足记录要求以后再继续后续任务。"
)


def value(text: str = "", xml: str | None = None) -> dict:
    xml = paragraph(text) if xml is None else xml
    root = etree.fromstring(envelope(xml).encode())
    return {"xml": xml, "text": plain_text(root[0]), "ocr_refs": []}


def block(text: str = "", *, xml: str | None = None, image: str = "old") -> dict:
    result = candidate(value(text, xml), image)
    assert result["status"] == "ok"
    return result


def setup(*, all_repeated: bool = False):
    committed = [block(xml="<h2>采集说明</h2>"), block(BODY)]
    draft = new_draft("new", committed[-1])
    values = [value(b["text"], b["xml"]) for b in committed]
    if not all_repeated:
        values.append(value("本次新增的独立段落。"))
    initialize(draft, {"blocks": values, "tail": None})
    history = [history_item(b, i) for i, b in enumerate(committed)]
    claim = {
        "relation": RELATION,
        "candidate_prefix_count": 2,
        "history_refs": [item["history_ref"] for item in history],
        "first_history_match": "full_block",
        "whole_image_has_no_new_content": all_repeated,
    }
    return committed, draft, history, claim


def bind_case(committed: list[dict], current: list[dict], *, first="full_block"):
    draft = new_draft("new", committed[-1])
    initialize(draft, {"blocks": current, "tail": None})
    history = [history_item(b, i) for i, b in enumerate(committed)]
    claim = {
        "relation": RELATION,
        "candidate_prefix_count": len(current),
        "history_refs": [item["history_ref"] for item in history],
        "first_history_match": first,
        "whole_image_has_no_new_content": True,
    }
    assert bind_overlap(draft, claim, history, committed)
    return draft


def test_retrieval_finds_nonadjacent_complete_source_window_without_mutation():
    committed = [
        block(xml="<h2>采集说明</h2>", image="photo-a"),
        block(BODY, image="photo-a"),
        block(SECOND, image="photo-a"),
        block("与采集章节无关的一段饮食记录。", image="photo-b"),
    ]
    original = deepcopy(committed)
    history = retrieve_history(committed, BODY.replace("时间戳", "时问戳"), limit=10)
    assert [item["block_id"] for item in history] == [0, 1, 2]
    assert history[1]["xml"] == committed[1]["xml"]
    assert all("internal_id" not in item for item in history)
    assert all(committed[1]["id"] not in item["history_ref"] for item in history)
    assert committed == original
    assert history == retrieve_history(committed, BODY.replace("时间戳", "时问戳"))


def test_retrieval_only_recommends_context_and_is_count_bounded():
    committed = [block(BODY + str(i)) for i in range(12)]
    original = deepcopy(committed)
    history = retrieve_history(committed, BODY, limit=3)
    indices = [item["block_id"] for item in history]
    assert len(history) == 3
    assert indices == list(range(indices[0], indices[0] + 3))
    assert committed == original
    assert retrieve_history(committed, "") == []
    assert retrieve_history(committed, "完全无关") == []
    with pytest.raises(ValueError):
        retrieve_history(committed, BODY, limit=True)


def test_invalid_history_splits_retrieval_windows():
    committed = [block(BODY), block("未完成"), block(SECOND)]
    committed[1]["status"] = "fallback"
    history = retrieve_history(committed, BODY + SECOND)
    assert len(history) == 1
    assert history[0]["block_id"] in {0, 2}


def test_exact_prefix_omits_only_current_candidates_and_preserves_history_budgets():
    committed, draft, history, claim = setup()
    original_history = deepcopy(committed)
    original = deepcopy(draft["blocks"])
    budgets = deepcopy(draft["budgets"])
    assert bind_overlap(draft, claim, history, committed)
    assert resolve_overlap(draft, committed)
    assert draft["blocks"] == original[2:]
    assert draft["omitted_blocks"] == original[:2]
    assert committed == original_history
    assert draft["budgets"] == budgets
    assert draft["applied_overlap"]["textual_coverage_verified"] is True
    assert draft["applied_overlap"]["independent_visual_identity_verified"] is False
    assert commit_blocks(committed, draft) == committed + original[2:]


def test_full_image_repeat_is_zero_additions_and_resume_is_idempotent():
    committed, draft, history, claim = setup(all_repeated=True)
    assert bind_overlap(draft, claim, history, committed)
    bound = json.loads(json.dumps(draft))
    assert bind_overlap(bound, claim, history, committed)
    assert resolve_overlap(bound, committed)
    assert bound["blocks"] == []
    assert bound["applied_overlap"]["whole_image_has_no_new_content"] is True
    assert len(bound["omitted_blocks"]) == 2
    assert commit_blocks(committed, bound) == committed
    resumed = json.loads(json.dumps(bound))
    before = deepcopy(resumed)
    assert resolve_overlap(resumed, committed)
    assert bind_overlap(resumed, claim, history, committed)
    assert resumed == before


def test_empty_observations_cannot_claim_full_image_repeat():
    committed, _, history, claim = setup(all_repeated=True)
    draft = new_draft("new", committed[-1])
    original = deepcopy(draft["blocks"])
    assert not bind_overlap(draft, claim, history, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_OBSERVATIONS_REQUIRED"
    assert draft["blocks"] == original


@pytest.mark.parametrize(
    "change",
    [
        "unknown_ref",
        "reverse",
        "duplicate",
        "negative",
        "bool",
        "nonprefix",
        "unsupported",
    ],
)
def test_invalid_claims_warn_without_losing_any_candidate(change):
    committed, draft, history, claim = setup()
    original = deepcopy(draft["blocks"])
    if change == "unknown_ref":
        claim["history_refs"][0] = "not-sent"
    elif change == "reverse":
        claim["history_refs"].reverse()
    elif change == "duplicate":
        claim["history_refs"][1] = claim["history_refs"][0]
    elif change == "negative":
        claim["candidate_prefix_count"] = -1
    elif change == "bool":
        claim["candidate_prefix_count"] = True
    elif change == "nonprefix":
        claim["candidate_indices"] = [1, 2]
    else:
        claim["relation"] = "similar_text"
    assert not bind_overlap(draft, claim, history, committed)
    assert draft["blocks"] == original
    assert not resolve_overlap(draft, committed)
    assert draft["blocks"] == original


def test_noncontiguous_history_is_not_authorized_by_similar_content():
    committed, draft, history, claim = setup()
    committed.insert(1, block("中间另一个段落"))
    history = [history_item(committed[i], i) for i in [0, 2]]
    claim["history_refs"] = [item["history_ref"] for item in history]
    assert not bind_overlap(draft, claim, history, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_HISTORY_NOT_CONTIGUOUS"


@pytest.mark.parametrize("change", ["version", "identity", "content"])
def test_changed_history_cannot_apply_an_old_claim(change):
    committed, draft, history, claim = setup()
    assert bind_overlap(draft, claim, history, committed)
    original = deepcopy(draft["blocks"])
    if change == "version":
        committed[1]["version"] += 1
    elif change == "identity":
        committed[1]["id"] = "replacement-with-identical-text"
    else:
        committed[1]["text"] += "修改"
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_HISTORY_CHANGED"
    assert draft["blocks"] == original


def test_a_modified_context_hash_cannot_be_bound():
    committed, draft, history, claim = setup()
    history[1]["text"] += "被篡改"
    assert not bind_overlap(draft, claim, history, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_HISTORY_INVALID"


@pytest.mark.parametrize(
    "old,new",
    [
        ("结果是12，不是21。", "结果是21，不是12。"),
        ("应当继续", "不应继续"),
        ("18 ÷ 3 = 6", "18 × 3 = 6"),
    ],
)
def test_numeral_negation_and_operator_conflicts_preserve_both_versions(old, new):
    committed = [block(xml="<h2>采集说明</h2>"), block(BODY + old)]
    draft = bind_case(committed, [value(xml="<h2>采集说明</h2>"), value(BODY + new)])
    original = deepcopy(draft["blocks"])
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_CONTENT_CONFLICT"
    assert draft["blocks"] == original


def test_code_indentation_is_not_normalized_for_omission():
    old = "<pre><code>if ready:\n    save()\n</code></pre>"
    new = "<pre><code>if ready:\n save()\n</code></pre>"
    committed = [block(xml=old), block(BODY)]
    draft = bind_case(committed, [value(xml=new), value(BODY)])
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_CONTENT_CONFLICT"


def test_table_cell_structure_is_preserved_even_when_flattened_text_matches():
    old = "<table><tbody><tr><td>A</td><td>B</td></tr></tbody></table>"
    new = '<table><tbody><tr><td colspan="2">AB</td></tr></tbody></table>'
    committed = [block(xml=old), block(BODY)]
    draft = bind_case(committed, [value(xml=new), value(BODY)])
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_CONTENT_CONFLICT"


@pytest.mark.parametrize("tag", ["ul", "ol"])
def test_list_item_boundaries_are_preserved_when_concatenated_text_matches(tag):
    old = f"<{tag}><li>AB</li><li>C</li></{tag}>"
    new = f"<{tag}><li>A</li><li>BC</li></{tag}>"
    committed = [block(xml=old), block(BODY)]
    draft = bind_case(committed, [value(xml=new), value(BODY)])
    original = deepcopy(draft["blocks"])
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_CONTENT_CONFLICT"
    assert draft["blocks"] == original


def test_same_list_item_text_with_inline_style_difference_can_match():
    old = "<ol><li>AB</li><li>C</li></ol>"
    new = "<ol><li><b>AB</b></li><li>C</li></ol>"
    committed = [block(xml=old), block(BODY)]
    draft = bind_case(committed, [value(xml=new), value(BODY)])
    assert resolve_overlap(draft, committed)
    assert draft["blocks"] == []
    assert draft["omitted_blocks"][0]["xml"].count("<b>") == 1


def test_known_link_target_conflict_is_not_treated_as_a_style_difference():
    old = '<p><a href="https://example.org/one">说明文件</a></p>'
    new = '<p><a href="https://example.org/two">说明文件</a></p>'
    committed = [block(xml=old), block(BODY)]
    draft = bind_case(committed, [value(xml=new), value(BODY)])
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_CONTENT_CONFLICT"


@pytest.mark.parametrize(
    "history_xml", [["<h2>使用说明</h2>"], ["<h2>使用说明</h2>", "<h3>操作步骤</h3>"]]
)
def test_repeated_short_titles_are_retained(history_xml):
    committed = [block(xml=xml) for xml in history_xml]
    draft = bind_case(committed, [value(xml=xml) for xml in history_xml])
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_EVIDENCE_TOO_SHORT"
    assert len(draft["blocks"]) == len(history_xml)


def test_repeated_identical_long_paragraphs_are_not_distinct_boundary_evidence():
    committed = [block(BODY), block(BODY)]
    draft = bind_case(committed, [value(BODY), value(BODY)])
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_EVIDENCE_TOO_SHORT"


def test_clipped_suffix_cannot_borrow_unseen_history_as_long_body_evidence():
    committed = [block(BODY), block(xml="<h2>注意</h2>")]
    draft = bind_case(
        committed,
        [value("。"), value(xml="<h2>注意</h2>")],
        first="suffix_of_first_history",
    )
    original = deepcopy(draft["blocks"])
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_EVIDENCE_TOO_SHORT"
    assert draft["blocks"] == original


def test_tail_continuation_conflicts_with_prefix_omission():
    committed, draft, history, claim = setup()
    draft["blocks"][0]["replaces_tail"] = True
    assert not bind_overlap(draft, claim, history, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_TAIL_CONFLICT"


def test_pending_repairs_defer_resolution_without_charging_an_extra_attempt():
    committed, draft, history, claim = setup()
    assert bind_overlap(draft, claim, history, committed)
    draft["blocks"][1]["status"] = "pending"
    before = deepcopy(draft)
    assert not resolve_overlap(draft, committed)
    assert draft == before
    draft["blocks"][1]["status"] = "ok"
    assert resolve_overlap(draft, committed)


def test_fallback_candidates_cannot_authorize_omission():
    committed, draft, history, claim = setup()
    assert bind_overlap(draft, claim, history, committed)
    draft["blocks"][1]["status"] = "fallback"
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_BLOCK_UNVERIFIED"
    assert len(draft["blocks"]) == 3


def test_real_repair_split_keeps_lineage_budget_and_omits_entire_original_prefix():
    committed, draft, history, claim = setup()
    assert bind_overlap(draft, claim, history, committed)
    target = draft["blocks"][1]
    attempt = start_attempt(draft, [target["id"]])
    middle = BODY.index("保存操作")
    apply_patch(
        draft,
        attempt,
        {
            "attempt_id": attempt["attempt_id"],
            "target_versions": attempt["target_versions"],
            "blocks": [value(BODY[:middle]), value(BODY[middle:])],
        },
    )
    budgets = deepcopy(draft["budgets"])
    assert resolve_overlap(draft, committed)
    assert len(draft["omitted_blocks"]) == 3
    assert [b["repair_attempts"] for b in draft["omitted_blocks"]] == [0, 1, 1]
    assert draft["budgets"] == budgets
    assert len(draft["blocks"]) == 1


def test_repair_merging_prefix_with_new_content_cancels_omission():
    committed, draft, history, claim = setup()
    assert bind_overlap(draft, claim, history, committed)
    old, new = draft["blocks"][1:]
    merged = candidate(
        value(old["text"] + new["text"]), "new", lineage=old["lineage"] + new["lineage"]
    )
    draft["blocks"][1:] = [merged]
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_LINEAGE_AMBIGUOUS"
    assert len(draft["blocks"]) == 2


def test_reordered_or_missing_lineage_does_not_retarget_omission():
    committed, draft, history, claim = setup()
    assert bind_overlap(draft, claim, history, committed)
    draft["blocks"][0], draft["blocks"][1] = draft["blocks"][1], draft["blocks"][0]
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_LINEAGE_AMBIGUOUS"


def test_first_visible_paragraph_can_be_an_exact_suffix_with_full_neighbors():
    committed = [block(BODY), block(SECOND)]
    suffix = BODY[BODY.index("保存操作") :]
    draft = bind_case(
        committed, [value(suffix), value(SECOND)], first="suffix_of_first_history"
    )
    assert resolve_overlap(draft, committed)
    assert draft["blocks"] == []


def test_clipped_code_suffix_requires_a_line_boundary_and_exact_whitespace():
    old = "<pre><code>prepare()\n    save()\n</code></pre>"
    committed = [block(xml=old), block(BODY)]
    good = bind_case(
        committed,
        [value(xml="<pre><code>    save()\n</code></pre>"), value(BODY)],
        first="suffix_of_first_history",
    )
    assert resolve_overlap(good, committed)
    bad = bind_case(
        committed,
        [value(xml="<pre><code>save()\n</code></pre>"), value(BODY)],
        first="suffix_of_first_history",
    )
    assert not resolve_overlap(bad, committed)
    assert bad["overlap_warning"]["code"] == "OVERLAP_CONTENT_CONFLICT"


def test_changed_binding_and_failed_claim_replay_do_not_remove_content():
    committed, draft, history, claim = setup()
    assert bind_overlap(draft, claim, history, committed)
    draft["overlap_binding"]["roots"].reverse()
    assert not resolve_overlap(draft, committed)
    assert draft["overlap_warning"]["code"] == "OVERLAP_BINDING_CHANGED"
    before = deepcopy(draft)
    assert not resolve_overlap(draft, committed)
    assert draft == before


def test_no_claim_never_causes_implicit_similarity_omission():
    committed, draft, history, _ = setup()
    original = deepcopy(draft)
    assert not bind_overlap(draft, None, history, committed)
    assert not resolve_overlap(draft, committed)
    assert draft == original
