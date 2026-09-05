"""A model chooses a verified repair; authority and existing budgets still apply."""

from copy import deepcopy
import json

import pytest

from capture2doc.formats.c2d_xml import validate_document
from capture2doc.pipeline import document as pipeline
from capture2doc.pipeline.document import (
    BlockStore,
    apply_repair_proposal,
    current_repair_options,
)
from capture2doc.pipeline.draft import (
    RejectedPatch,
    initialize,
    new_draft,
    start_attempt,
)
from capture2doc.pipeline.protocol import response_schema
from test_block_pipeline import Models, block, result, run, setup, submit


CODE = 'if a < b:\n    print("a  b")\n'
XML = '<blockquote>\n<pre lang="python"><code>if a &lt; b:\n    print("a  b")\n</code></pre>\n</blockquote>'


def original_block():
    return block(CODE, XML, ["a:ocr:0", "a:ocr:1"])


def choose(payload):
    assert len(payload["repair_options"]) == 1
    return {
        "action": "apply_repair_option",
        "option_id": payload["repair_options"][0]["option_id"],
        "attempt_id": payload["attempt_id"],
        "target_versions": payload["target_versions"],
    }


def decline(payload):
    return {
        "action": "decline_repair_option",
        "attempt_id": payload["attempt_id"],
        "target_versions": payload["target_versions"],
    }


def saved_draft(store, index=0):
    return json.loads(
        (store.root / store.state["batches"][index]["draft_ref"]).read_text()
    )


class InspectChoices(Models):
    def inspect(self, path, prompt, system):
        payload = json.loads(prompt)
        if payload.get("repair_options"):
            assert payload["targets"][0]["text"] == CODE
            assert payload["repair_options"][0]["blocks"][0]["text"] == CODE
            assert XML == payload["targets"][0]["xml"]
        return super().inspect(path, prompt, system)

    def generate(
        self, path, prompt, system, inspection, output, *, response_schema=None
    ):
        payload = json.loads(prompt)
        if payload.get("repair_options"):
            branches = response_schema["anyOf"]
            assert [b["properties"]["action"]["const"] for b in branches] == [
                "apply_repair_option",
                "decline_repair_option",
            ]
            assert all("blocks" not in b["properties"] for b in branches)
            assert branches[0]["properties"]["option_id"]["enum"] == [
                payload["repair_options"][0]["option_id"]
            ]
        return super().generate(
            path, prompt, system, inspection, output, response_schema=response_schema
        )


def test_model_selection_repairs_once_preserving_code_sources_and_original_error(
    tmp_path,
):
    texts = {"a": CODE, "b": "后续图片正常内容"}
    store = setup(tmp_path, texts)
    models = InspectChoices(
        texts, actions=[submit(original_block()), choose, submit(block(texts["b"]))]
    )
    run(store, models)
    final = result(store)["blocks"][0]
    assert final["status"] == "ok" and final["repair_attempts"] == 1
    assert final["text"] == CODE and "blockquote" not in final["xml"]
    assert final["errors"] and any("SCHEMAV" in e["code"] for e in final["errors"])
    draft = saved_draft(store)
    assert draft["blocks"][0]["ocr_refs"] == ["a:ocr:0", "a:ocr:1"]
    assert draft["blocks"][0]["original_candidate"]["xml"] == XML
    attempt = draft["attempts"][1]
    assert len(attempt["calls"]) == 1
    assert attempt["repair_option_decision"]["action"] == "apply_repair_option"
    assert (
        attempt["proposal"]["option_id"]
        == attempt["sent_repair_options"][0]["option_id"]
    )
    assert max(draft["budgets"].values()) == 1
    assert len(models.requests) == 3
    assert store.state["batches"][0]["repair_option_selections"] == 1
    assert store.state["batches"][0]["repair_option_declines"] == 0
    assert validate_document((store.root / "document.c2d.xml").read_bytes()).valid


def test_five_declines_keep_original_candidate_then_fallback_and_process_next_image(
    tmp_path,
):
    texts = {"a": CODE, "b": "后续任务不能阻塞"}
    store = setup(tmp_path, texts)
    models = Models(
        texts,
        actions=[submit(original_block())]
        + [decline] * 5
        + [submit(block(texts["b"]))],
    )
    run(store, models)
    blocks = result(store)["blocks"]
    assert [b["status"] for b in blocks] == ["fallback", "ok"]
    assert blocks[0]["repair_attempts"] == 5
    assert any(e["code"] == "REPAIR_OPTION_DECLINED" for e in blocks[0]["errors"])
    draft = saved_draft(store)
    assert max(draft["budgets"].values()) == 5
    assert draft["blocks"][0]["original_candidate"]["xml"] == XML
    assert [a["repair_option_decision"]["action"] for a in draft["attempts"][1:]] == [
        "decline_repair_option"
    ] * 5
    assert len(models.requests) == 7
    assert store.state["batches"][0]["repair_option_selections"] == 0
    assert store.state["batches"][0]["repair_option_declines"] == 5


def test_without_safe_option_original_submit_repair_remains_available(tmp_path):
    texts = {"a": "正文"}
    store = setup(tmp_path, texts)

    def ordinary(payload):
        assert payload["repair_options"] == []
        return {
            "action": "submit",
            "attempt_id": payload["attempt_id"],
            "target_versions": payload["target_versions"],
            "blocks": [block("正文")],
        }

    run(
        store,
        Models(texts, actions=[submit(block("正文", "<bad>正文</bad>")), ordinary]),
    )
    assert result(store)["blocks"][0]["status"] == "ok"
    assert result(store)["blocks"][0]["repair_attempts"] == 1


def decision_case():
    draft = new_draft("a", None)
    initialize(draft, submit(original_block()))
    attempt = start_attempt(draft, [draft["blocks"][0]["id"]])
    offered = current_repair_options(draft, attempt)
    # Represents the immutable copy recorded when this request was dispatched.
    attempt["sent_repair_options"] = deepcopy(offered)
    proposal = {
        "action": "apply_repair_option",
        "option_id": offered[0]["option_id"],
        "attempt_id": attempt["attempt_id"],
        "target_versions": attempt["target_versions"],
    }
    return draft, attempt, proposal


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "xml",
        "sources",
        "unknown_option",
        "wrong_attempt",
        "wrong_target",
        "bool_version",
        "extra_blocks",
        "not_sent",
    ],
)
def test_stale_unoffered_or_out_of_scope_choices_cannot_mutate_target(change):
    draft, attempt, proposal = decision_case()
    if change == "version":
        draft["blocks"][0]["version"] += 1
    elif change == "xml":
        draft["blocks"][0]["xml"] = XML.replace("<blockquote>\n", "<blockquote>\n\n")
    elif change == "sources":
        draft["blocks"][0]["ocr_refs"].append("a:ocr:99")
    elif change == "unknown_option":
        proposal["option_id"] = "repair-option:unknown"
    elif change == "wrong_attempt":
        proposal["attempt_id"] = "not-this-attempt"
    elif change == "wrong_target":
        proposal["target_versions"] = {"another-block": 0}
    elif change == "bool_version":
        proposal["target_versions"] = {draft["blocks"][0]["id"]: False}
    elif change == "extra_blocks":
        proposal["blocks"] = [block("注入正文")]
    else:
        attempt.pop("sent_repair_options")
    before = deepcopy(draft)
    with pytest.raises(RejectedPatch):
        apply_repair_proposal(draft, attempt, proposal)
    assert draft == before


def test_choice_still_calls_existing_patch_guard_and_cannot_apply_twice(monkeypatch):
    draft, attempt, proposal = decision_case()
    original = pipeline.apply_patch
    calls = []

    def traced(*args):
        calls.append(deepcopy(args[2]))
        return original(*args)

    monkeypatch.setattr(pipeline, "apply_patch", traced)
    apply_repair_proposal(draft, attempt, proposal)
    assert len(calls) == 1
    assert calls[0]["blocks"][0]["text"] == CODE
    assert draft["blocks"][0]["repair_attempts"] == 1
    before = deepcopy(draft)
    with pytest.raises(RejectedPatch, match="Duplicate"):
        apply_repair_proposal(draft, attempt, proposal)
    assert draft == before and len(calls) == 1


def test_received_choice_resumes_without_new_request_or_budget_reset(
    tmp_path, monkeypatch
):
    store = setup(tmp_path, {"a": CODE})
    original = pipeline.apply_repair_proposal

    def interrupt(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(pipeline, "apply_repair_proposal", interrupt)
    models = Models({"a": CODE}, actions=[submit(original_block()), choose])
    with pytest.raises(KeyboardInterrupt):
        run(store, models)
    assert (
        store.state["draft"]["attempts"][-1]["proposal"]["action"]
        == "apply_repair_option"
    )
    assert max(store.state["draft"]["budgets"].values()) == 1
    monkeypatch.setattr(pipeline, "apply_repair_proposal", original)
    restored = BlockStore(store.root)
    restored.load()
    resumed = Models({"a": CODE})
    run(restored, resumed)
    assert resumed.requests == []
    assert result(restored)["blocks"][0]["status"] == "ok"
    assert result(restored)["blocks"][0]["repair_attempts"] == 1
    assert len(saved_draft(restored)["attempts"]) == 2


def test_interrupted_choice_request_still_consumes_the_fourth_attempt(tmp_path):
    store = setup(tmp_path, {"a": CODE})
    models = Models(
        {"a": CODE},
        actions=[submit(original_block())] + [decline] * 3 + [KeyboardInterrupt()],
    )
    with pytest.raises(KeyboardInterrupt):
        run(store, models)
    assert max(store.state["draft"]["budgets"].values()) == 4
    restored = BlockStore(store.root)
    restored.load()
    resumed = Models({"a": CODE}, actions=[choose])
    run(restored, resumed)
    final = result(restored)["blocks"][0]
    assert final["status"] == "ok" and final["repair_attempts"] == 5
    assert len(resumed.requests) == 1
    assert len(saved_draft(restored)["attempts"]) == 6


def test_freeform_rewrite_is_not_accepted_when_choice_schema_was_sent(tmp_path):
    store = setup(tmp_path, {"a": CODE})

    def rewrite(payload):
        assert payload["repair_options"]
        return {
            "action": "submit",
            "attempt_id": payload["attempt_id"],
            "target_versions": payload["target_versions"],
            "blocks": [block("擅自扩写的内容")],
        }

    run(store, Models({"a": CODE}, actions=[submit(original_block())] + [rewrite] * 5))
    final = result(store)["blocks"][0]
    assert final["status"] == "fallback" and final["repair_attempts"] == 5
    assert "擅自扩写" not in final["text"]
    assert any(e["code"] == "REPAIR_OPTION_CHOICE_REQUIRED" for e in final["errors"])


def test_multi_target_task_has_no_automatic_choice_and_keeps_submit_schema():
    draft = new_draft("a", None)
    initialize(draft, submit(original_block(), original_block()))
    attempt = start_attempt(draft, [b["id"] for b in draft["blocks"]])
    assert current_repair_options(draft, attempt) == []
    schema = response_schema(
        "repair", attempt["attempt_id"], attempt["target_versions"], options=[]
    )
    assert schema["anyOf"][0]["properties"]["action"]["const"] == "submit"
