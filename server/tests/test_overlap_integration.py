"""Full V2 orchestration with explicit, recoverable image-prefix overlap claims."""

from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from capture2doc.formats.c2d_xml import validate_document
from capture2doc.pipeline import document as pipeline
from capture2doc.pipeline.document import BlockStore
from capture2doc.pipeline.overlap import RELATION, history_item
from capture2doc.pipeline.protocol import response_schema
from test_block_pipeline import (
    Models,
    block,
    result,
    run as run_baseline,
    setup,
    submit,
)


def run(store, models, **kwargs):
    return run_baseline(store, models, enable_overlap_generation=True, **kwargs)


BODY = (
    "设备在所有采集通道完成数据校验之后才会写入归档文件，"
    "保存操作还应保留每个通道的原始时间戳及校准参数。"
)
UNITS = [
    block("采集说明", "<h2>采集说明</h2>"),
    block("输入准备", "<h3>输入准备</h3>"),
    block(BODY),
    block("归档步骤"),
]
GAP = [block(f"本节介绍独立的维护日程和设备保养计划，第{i}部分。") for i in range(4)]


def observed_text(values):
    return "\n".join(value["text"] for value in values)


def sent_items(payload):
    values = payload["readonly_history"] + payload["retrieved_history"]
    if payload["mutable_tail"]:
        values += [payload["mutable_tail"]]
    for reply in payload["tool_results"]:
        values += reply.get("result", {}).get("blocks", [])
    return {item["block_id"]: item for item in values if "history_ref" in item}


def overlap_submit(payload, repeated, *new, refs=None):
    available = sent_items(payload)
    references = (
        [available[i]["history_ref"] for i in range(len(repeated))]
        if refs is None
        else refs
    )
    return {
        **submit(*(deepcopy(repeated) + list(new))),
        "overlap_claim": {
            "relation": RELATION,
            "candidate_prefix_count": len(repeated),
            "history_refs": references,
            "first_history_match": "full_block",
            "whole_image_has_no_new_content": not new,
        },
    }


def draft_for(store, index):
    return json.loads(
        (store.root / store.state["batches"][index]["draft_ref"]).read_text()
    )


def assert_public_xml(store):
    data = (store.root / "document.c2d.xml").read_bytes()
    assert validate_document(data).valid
    assert b"history_ref" not in data and b"overlap_claim" not in data
    assert b"image_id=" not in data and b"internal_id=" not in data


def test_nonadjacent_overlap_keeps_intervening_content_and_appends_only_new_blocks(
    tmp_path,
):
    fresh = block("这是当前图片首次出现的正文。")
    texts = {
        "a": observed_text(UNITS),
        "b": observed_text(GAP),
        "c": observed_text(UNITS + [fresh]),
    }
    store = setup(tmp_path, texts)

    def third(payload):
        assert [item["block_id"] for item in payload["retrieved_history"]] == [
            0,
            1,
            2,
            3,
        ]
        assert "attempt_id" not in payload and "target_versions" not in payload
        assert "allowed_modify_ids" not in payload and "targets" not in payload
        assert "id" not in payload["mutable_tail"]
        assert all("internal_id" not in item for item in sent_items(payload).values())
        return overlap_submit(payload, UNITS, fresh)

    models = Models(texts, actions=[submit(*UNITS), submit(*GAP), third])
    run(store, models)
    assert [b["text"] for b in result(store)["blocks"]] == [
        b["text"] for b in UNITS + GAP + [fresh]
    ]
    batch = store.state["batches"][2]
    assert batch["candidate_count"] == 5
    assert batch["committed_candidate_count"] == 1
    assert batch["omitted_candidate_count"] == 4
    assert batch["applied_overlap"]["history_indices"] == [0, 1, 2, 3]
    assert batch["repair_calls"] == 0
    assert len(draft_for(store, 2)["omitted_blocks"]) == 4
    assert_public_xml(store)


def test_four_block_heading_description_overlap_retains_new_table(tmp_path):
    table = block(
        "项目\t状态\n采集\t完成",
        "<table><thead><tr><th>项目</th><th>状态</th></tr></thead><tbody><tr><td>采集</td><td>完成</td></tr></tbody></table>",
    )
    texts = {"a": observed_text(UNITS), "b": observed_text(UNITS + [table])}
    store = setup(tmp_path, texts)

    def second(payload):
        # Last three alone miss the first heading; proactive retrieval adds it.
        assert [item["block_id"] for item in payload["retrieved_history"]] == [0]
        assert sorted(sent_items(payload)) == [0, 1, 2, 3]
        return overlap_submit(payload, UNITS, table)

    run(store, Models(texts, actions=[submit(*UNITS), second]))
    blocks = result(store)["blocks"]
    assert len(blocks) == 5 and "<table" in blocks[-1]["xml"]
    assert [b["text"] for b in blocks[:4]] == [b["text"] for b in UNITS]
    assert_public_xml(store)


def test_full_duplicate_commits_zero_new_blocks_then_later_image_continues(tmp_path):
    texts = {"a": observed_text(UNITS), "b": observed_text(UNITS), "c": "后续图片正文"}
    store = setup(tmp_path, texts)
    run(
        store,
        Models(
            texts,
            actions=[
                submit(*UNITS),
                lambda p: overlap_submit(p, UNITS),
                submit(block(texts["c"])),
            ],
        ),
    )
    batch = store.state["batches"][1]
    assert batch["committed_candidate_count"] == 0
    assert batch["applied_overlap"]["whole_image_has_no_new_content"] is True
    assert batch["diagnostic"]["ocr_coverage"] == pytest.approx(1.0)
    assert batch["diagnostic"]["output_support"] == pytest.approx(1.0)
    assert draft_for(store, 1)["blocks"] == []
    assert len(draft_for(store, 1)["omitted_blocks"]) == 4
    output = result(store)
    assert output["processing_status"] == "completed" and len(output["blocks"]) == 5
    assert output["blocks"][-1]["text"] == texts["c"]
    assert output["needs_review"] is True
    assert output["stitching"][0]["applied_overlap"]["needs_review"] is True
    assert_public_xml(store)
    restored = BlockStore(store.root)
    restored.load()
    idle = Models({})
    run(restored, idle)
    assert idle.requests == [] and len(restored.state["batches"]) == 3


def test_invalid_claim_is_a_review_warning_not_whole_image_fallback(tmp_path):
    texts = {"a": observed_text(UNITS), "b": "需要保留的新内容"}
    store = setup(tmp_path, texts)
    invalid = {
        **submit(block(texts["b"])),
        "overlap_claim": {"relation": "similar_ocr"},
    }
    run(store, Models(texts, actions=[submit(*UNITS), invalid]))
    output = result(store)
    assert len(output["blocks"]) == 5
    assert output["blocks"][-1]["status"] == "ok"
    assert output["blocks"][-1]["fallback_source"] is None
    assert output["needs_review"] is True
    warning = output["stitching"][0]["warnings"][0]
    assert warning["code"] == "OVERLAP_CLAIM_INVALID"
    assert not draft_for(store, 1).get("omitted_blocks")


def test_budget_removes_complete_extra_history_before_recent_blocks_and_binds_only_sent_scope(
    tmp_path,
):
    fresh = block("补充说明。")
    texts = {
        "a": observed_text(UNITS),
        "b": observed_text(GAP),
        "c": observed_text(UNITS + [fresh]),
    }
    store = setup(tmp_path, texts)
    inspections = []

    def third(payload):
        assert payload["retrieved_history"] == []
        assert payload["history_context_trimmed"] is True
        assert payload["mutable_tail"]["text"] == GAP[-1]["text"]
        # A guessed reference was never dispatched: it cannot authorize omission.
        refs = [
            history_item(store.state["blocks"][i], i)["history_ref"] for i in range(4)
        ]
        return overlap_submit(payload, UNITS, fresh, refs=refs)

    models = Models(texts, actions=[submit(*UNITS), submit(*GAP), third])
    original_inspect = models.inspect

    def inspect(path, prompt, system):
        payload = json.loads(prompt)
        if path.stem == "c":
            inspections.append(deepcopy(payload))
            for item in payload["retrieved_history"]:
                assert item["text"] == store.state["blocks"][item["block_id"]]["text"]
                assert item["xml"] == store.state["blocks"][item["block_id"]]["xml"]
            if payload["retrieved_history"]:
                return SimpleNamespace(prompt_tokens=16380, to_dict=lambda: {})
        return original_inspect(path, prompt, system)

    models.inspect = inspect
    run(store, models)
    assert [len(p["retrieved_history"]) for p in inspections] == [4, 3, 2, 1, 0]
    draft = draft_for(store, 2)
    assert all(
        item["block_id"] >= 5 for item in draft["attempts"][0]["sent_overlap_history"]
    )
    assert draft["overlap_warning"]["code"] == "OVERLAP_HISTORY_INVALID"
    assert len(result(store)["blocks"]) == 13
    assert all(b["status"] == "ok" for b in result(store)["blocks"])


@pytest.mark.parametrize("include_unseen_fourth", [False, True])
def test_only_actually_dispatched_tool_history_may_authorize_omission(
    tmp_path, monkeypatch, include_unseen_fourth
):
    monkeypatch.setattr(pipeline, "retrieve_history", lambda *args: [])
    repeated = UNITS if include_unseen_fourth else UNITS[:3]
    fresh = block("这一段是新出现的正文。")
    texts = {
        "a": observed_text(UNITS),
        "b": observed_text(GAP),
        "c": observed_text(repeated + [fresh]),
    }
    store = setup(tmp_path, texts)

    def final(payload):
        assert len(payload["tool_results"]) == 1
        refs = [
            history_item(store.state["blocks"][i], i)["history_ref"]
            for i in range(len(repeated))
        ]
        return overlap_submit(payload, repeated, fresh, refs=refs)

    models = Models(
        texts,
        actions=[
            submit(*UNITS),
            submit(*GAP),
            {"action": "read_blocks", "block_ids": [0, 1, 2]},
            final,
        ],
    )
    run(store, models)
    draft = draft_for(store, 2)
    seen = {item["block_id"] for item in draft["attempts"][0]["sent_overlap_history"]}
    assert {0, 1, 2} <= seen and 3 not in seen
    assert len(draft["attempts"][0]["calls"]) == 2
    if include_unseen_fourth:
        assert draft["overlap_warning"]["code"] == "OVERLAP_HISTORY_INVALID"
        assert len(result(store)["blocks"]) == 13
    else:
        assert draft["applied_overlap"]["omitted_count"] == 3
        assert len(result(store)["blocks"]) == 9


def test_oversized_tool_reply_is_never_recorded_as_sent_history(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve_history", lambda *args: [])
    texts = {
        "a": observed_text(UNITS),
        "b": observed_text(GAP),
        "c": "当前图的保留文字",
    }
    store = setup(tmp_path, texts)
    models = Models(
        texts,
        actions=[
            submit(*UNITS),
            submit(*GAP),
            {"action": "read_blocks", "block_ids": [0, 1, 2]},
        ],
    )
    original = models.inspect

    def inspect(path, prompt, system):
        payload = json.loads(prompt)
        if path.stem == "c" and payload["tool_results"]:
            assert payload["tool_results"][0]["result"]["blocks"][2]["text"] == BODY
            return SimpleNamespace(prompt_tokens=16380, to_dict=lambda: {})
        return original(path, prompt, system)

    models.inspect = inspect
    run(store, models)
    draft = draft_for(store, 2)
    seen = {item["block_id"] for item in draft["attempts"][0]["sent_overlap_history"]}
    assert not {0, 1, 2} & seen
    assert len(draft["attempts"][0]["calls"]) == 1
    assert len(models.requests) == 3
    assert result(store)["blocks"][-1]["fallback_source"] == "ocr"


def test_received_overlap_proposal_resume_uses_saved_sent_history_without_regeneration(
    tmp_path, monkeypatch
):
    texts = {
        "a": observed_text(UNITS),
        "b": observed_text(GAP),
        "c": observed_text(UNITS),
    }
    store = setup(tmp_path, texts)
    original = pipeline.initialize

    def stop_before_initialize(draft, proposal):
        if draft["image_id"] == "c":
            raise KeyboardInterrupt()
        return original(draft, proposal)

    monkeypatch.setattr(pipeline, "initialize", stop_before_initialize)
    models = Models(
        texts,
        actions=[submit(*UNITS), submit(*GAP), lambda p: overlap_submit(p, UNITS)],
    )
    with pytest.raises(KeyboardInterrupt):
        run(store, models)
    assert store.state["draft"]["attempts"][0]["proposal"] is not None
    assert len(store.state["batches"]) == 2
    monkeypatch.setattr(pipeline, "initialize", original)
    restored = BlockStore(store.root)
    restored.load()
    resumed_models = Models(texts)
    run(restored, resumed_models)
    assert resumed_models.requests == []
    assert len(restored.state["batches"]) == 3
    assert restored.state["batches"][2]["omitted_candidate_count"] == 4
    assert len(result(restored)["blocks"]) == 8
    assert_public_xml(restored)


def test_generate_schema_has_bounded_nullable_claim_and_no_repair_authority():
    generate = response_schema(
        "generate", "irrelevant-random-id", {}, enable_overlap_generation=True
    )["anyOf"][0]
    assert "attempt_id" not in generate["properties"]
    assert "target_versions" not in generate["properties"]
    claim = generate["properties"]["overlap_claim"]["anyOf"]
    assert claim[0] == {"type": "null"}
    assert claim[1]["properties"]["history_refs"]["maxItems"] == 64
    repair = response_schema("repair", "attempt", {"target": 2})["anyOf"][0]
    assert repair["properties"]["attempt_id"]["const"] == "attempt"
    assert "overlap_claim" not in repair["properties"]
