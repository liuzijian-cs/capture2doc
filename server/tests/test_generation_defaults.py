"""Production generation stays separate from the historical-overlap experiment."""

import json

import pytest

from capture2doc.pipeline import document as pipeline
from capture2doc.pipeline.protocol import response_schema
from capture2doc.prompts import blocks_system_prompt, prompt_fingerprint
from test_block_pipeline import Models, block, result, run, setup, submit


def test_default_prompt_and_schema_exclude_overlap_but_keep_repair_options():
    prompt = blocks_system_prompt()
    schema = response_schema("generate", "unused", {})
    assert "blocks 仅含当前图片的新增块" in prompt
    for keyword in ("overlap_claim", "retrieved_history", "history_ref"):
        assert keyword not in prompt
        assert keyword not in json.dumps(schema)
    assert "repair_options" in prompt
    assert "apply_repair_option" in prompt
    assert "read_blocks" in prompt and "search_blocks" in prompt
    experiment = blocks_system_prompt(include_overlap_experiment=True)
    assert "overlap_claim" in experiment
    assert prompt_fingerprint(prompt) != prompt_fingerprint(experiment)


def test_default_context_never_retrieves_and_keeps_complete_recent_history_and_tools(
    tmp_path, monkeypatch
):
    history = [block(f"历史完整段落{i}，保留全部句子。") for i in range(5)]
    texts = {"a": "历史", "b": "当前新内容"}
    store = setup(tmp_path, texts)

    def unexpected_retrieval(*args, **kwargs):
        raise AssertionError("default generation must not proactively retrieve")

    monkeypatch.setattr(pipeline, "retrieve_history", unexpected_retrieval)

    def read(payload):
        assert "retrieved_history" not in payload
        assert [item["block_id"] for item in payload["readonly_history"]] == [2, 3]
        assert payload["mutable_tail"]["block_id"] == 4
        for item in payload["readonly_history"] + [payload["mutable_tail"]]:
            assert item["text"] == history[item["block_id"]]["text"]
            assert "history_ref" not in item
        return {"action": "read_blocks", "block_ids": [0]}

    def search(payload):
        item = payload["tool_results"][0]["result"]["blocks"][0]
        assert item["text"] == history[0]["text"]
        assert "history_ref" not in item
        return {"action": "search_blocks", "query": "完整段落1"}

    def finish(payload):
        item = payload["tool_results"][1]["result"]["blocks"][0]
        assert item["text"] == history[1]["text"]
        assert "history_ref" not in item
        return submit(block(texts["b"]))

    run(store, Models(texts, actions=[submit(*history), read, search, finish]))
    assert len(result(store)["blocks"]) == 6
    assert store.state["contract"]["overlap_generation"] is False
    draft = json.loads(
        (store.root / store.state["batches"][1]["draft_ref"]).read_text()
    )
    assert draft["attempts"][0]["sent_overlap_history"] == []
    assert len(draft["attempts"][0]["calls"]) == 3


def test_unsolicited_overlap_claim_cannot_remove_default_generation_candidates(
    tmp_path,
):
    texts = {"a": "重复正文", "b": "重复正文"}
    store = setup(tmp_path, texts)
    proposal = {
        **submit(block("重复正文")),
        "overlap_claim": {"relation": "same_source_prefix_overlap"},
    }
    run(store, Models(texts, actions=[submit(block("重复正文")), proposal]))
    assert [item["text"] for item in result(store)["blocks"]] == [
        "重复正文",
        "重复正文",
    ]
    batch = store.state["batches"][1]
    assert batch["omitted_candidate_count"] == 0
    assert batch["applied_overlap"] is None
    assert batch["overlap_warnings"][0]["code"] == "OVERLAP_GENERATION_DISABLED"
    assert result(store)["needs_review"] is True


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("completed", [False, True])
def test_overlap_mode_cannot_change_on_resume_or_completed_export(
    tmp_path, enabled, completed
):
    texts = {"a": "正文"}
    store = setup(tmp_path, texts)
    models = Models(texts, actions=[] if completed else [KeyboardInterrupt()])
    if completed:
        run(store, models, enable_overlap_generation=enabled)
    else:
        with pytest.raises(KeyboardInterrupt):
            run(store, models, enable_overlap_generation=enabled)
    contract = store.state["contract"].copy()
    resumed = Models(texts)
    with pytest.raises(ValueError, match="configuration changed.*overlap"):
        run(store, resumed, enable_overlap_generation=not enabled)
    assert store.state["contract"] == contract
    assert resumed.events == []


def test_experimental_schema_change_is_part_of_the_persisted_contract(tmp_path):
    contracts = []
    for index, enabled in enumerate((False, True)):
        root = tmp_path / str(index)
        root.mkdir()
        store = setup(root, {"a": "正文"})
        run(store, Models({"a": "正文"}), enable_overlap_generation=enabled)
        contracts.append(store.state["contract"])
    assert (
        contracts[0]["response_schema_sha256"] != contracts[1]["response_schema_sha256"]
    )
    assert contracts[0]["prompt_sha256"] != contracts[1]["prompt_sha256"]
