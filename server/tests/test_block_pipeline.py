"""Exercise V2 with real XML validation/storage and deterministic model boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from capture2doc.formats.c2d_xml import validate_document, validate_update
from capture2doc.pipeline.blocks import (
    candidate,
    envelope,
    examples,
    fallback,
    paragraph,
    segments,
)
from capture2doc.pipeline.document import (
    BlockStore,
    history_tool,
    import_ocr,
    run_document_v2,
)
from capture2doc.pipeline.draft import (
    RejectedPatch,
    apply_patch,
    commit_blocks,
    initialize,
    new_draft,
    resolve_fallback,
    start_attempt,
)
from capture2doc.pipeline.store import write_json
from test_document_pipeline import FakeModels, document, response


def block(text="正文", xml=None, refs=None):
    return {
        "xml": xml if xml is not None else paragraph(text),
        "text": text,
        "ocr_refs": refs or [],
    }


def submit(*values, tail=None):
    return {"action": "submit", "tail": tail, "blocks": list(values)}


class Models(FakeModels):
    def inspect(self, path, prompt, system):
        tokens = (len(prompt) + len(system)) // 3 + 1280
        return SimpleNamespace(
            prompt_tokens=tokens, to_dict=lambda: {"prompt_tokens": tokens}
        )

    def generate(
        self, path, prompt, system, inspection, output, *, response_schema=None
    ):
        assert response_schema is not None
        payload = json.loads(prompt)
        self.requests.append(payload)
        self.events.append(f"qwen:{path.stem}")
        if self.actions:
            action = self.actions.pop(0)
            if isinstance(action, BaseException):
                raise action
            if callable(action):
                action = action(payload)
            if action is not None:
                return (
                    action
                    if hasattr(action, "raw_response")
                    else response(json.dumps(action, ensure_ascii=False))
                )
        result = submit(
            block(
                "".join(s["text"] for s in payload["ocr_sources"]),
                refs=[s["source_id"] for s in payload["ocr_sources"]],
            )
        )
        if payload["mode"] == "repair":
            result.pop("tail")
            result.update(
                attempt_id=payload["attempt_id"],
                target_versions=payload["target_versions"],
            )
            result["blocks"] = [
                block(b["text"] or "恢复文字", refs=b["ocr_refs"])
                for b in payload["targets"]
            ]
        return response(json.dumps(result, ensure_ascii=False))


def setup(tmp_path, texts):
    _, manifest = document(tmp_path, texts)
    store = BlockStore(tmp_path / "v2")
    store.create(manifest)
    return store


def run(store, models, **kwargs):
    return run_document_v2(store, models, progress=lambda _: None, **kwargs)


def result(store):
    return json.loads((store.root / "document.json").read_text())["doc"]


def repeat_bad(payload):
    return {
        "action": "submit",
        "attempt_id": payload["attempt_id"],
        "target_versions": payload["target_versions"],
        "blocks": [
            block(
                b["text"],
                "<blockquote><pre><code>坏结构</code></pre></blockquote>",
                b["ocr_refs"],
            )
            for b in payload["targets"]
        ],
    }


def test_json_lossless_rich_content_roundtrip(tmp_path):
    text = '中文 "引号" \\ 路径\n第二行'
    table = (
        "<table><tbody>"
        + "".join(f"<tr><td>行{i}</td><td>&quot;\\</td></tr>" for i in range(600))
        + "</tbody></table>"
    )
    code = '<pre lang="python"><code>print("中文")\npath = "C:\\new"\nif a &lt; b:\n    go()\n</code></pre>'
    values = [block(text), block("表格", table), block("代码", code)]
    store = setup(tmp_path, {"a": text})
    run(store, Models({"a": text}, actions=[submit(*values)]))
    data = result(store)
    for original, output in zip(values, data["blocks"]):
        assert output["status"] == "ok"
        assert validate_update(envelope(output["xml"])).valid
        assert json.loads(json.dumps(output, ensure_ascii=False)) == output
    assert data["blocks"][0]["text"] == text
    assert 'path = "C:\\new"' in data["blocks"][2]["text"]
    assert data["blocks"][2]["text"].endswith("go()\n")
    assert "行599" in (store.root / "blocks.md").read_text()
    assert validate_document((store.root / "document.c2d.xml").read_bytes()).valid


def test_one_bad_block_repairs_five_times_then_later_image_continues(tmp_path):
    texts = {"a": "保留\n失败原文", "b": "后续图片"}
    store = setup(tmp_path, texts)
    first = submit(
        block("保留", refs=["a:ocr:0"]), block("失败原文", "<bad/>", ["a:ocr:1"])
    )
    models = Models(texts, actions=[first] + [repeat_bad] * 5)
    run(store, models)
    output = result(store)
    assert [b["status"] for b in output["blocks"]] == ["ok", "fallback", "ok"]
    failed = output["blocks"][1]
    assert failed["repair_attempts"] == 5
    assert (
        failed["vlm_validation"] == "failed" and failed["final_validation"] == "passed"
    )
    assert failed["fallback_source"] == "ocr" and failed["text"] == "失败原文"
    assert failed["errors"] and output["processing_status"] == "completed"
    assert len(models.requests) == 7
    for request in models.requests[1:6]:
        assert len(request["targets"]) == 1
        assert request["targets"][0]["text"] == "失败原文"
        assert "".join(s["text"] for s in request["ocr_sources"]) == texts["a"]
        assert "ocr_text" not in request
        assert request["targets"][0]["current_errors"][0]["correct_example"]
    assert [b["block_id"] for b in output["blocks"]] == [0, 1, 2]


def test_repairs_split_one_invalid_container_without_repeating_neighbors(tmp_path):
    store = setup(tmp_path, {"a": "引文代码后文"})
    bad = block(
        "引文代码", "<blockquote><p>引文</p><pre><code>代码</code></pre></blockquote>"
    )

    def split(p):
        return {
            "action": "submit",
            "attempt_id": p["attempt_id"],
            "target_versions": p["target_versions"],
            "blocks": [
                block("引文", "<blockquote><p>引文</p></blockquote>"),
                block("代码", "<pre><code>代码</code></pre>"),
            ],
        }

    models = Models({"a": "引文代码后文"}, actions=[submit(bad, block("后文")), split])
    run(store, models)
    assert [b["text"] for b in result(store)["blocks"]] == ["引文", "代码", "后文"]
    assert [b["repair_attempts"] for b in result(store)["blocks"]] == [1, 1, 0]
    assert result(store)["blocks"][0]["errors"]  # Historical failure remains visible.
    assert all(b["vlm_validation"] == "passed" for b in result(store)["blocks"])


def test_shared_invalid_or_unknown_ocr_refs_use_independent_text():
    sources = segments("a", "OCR甲\nOCR乙")
    values = [
        candidate(block("VLM甲", "<bad/>", ["a:ocr:0"]), "a"),
        candidate(block("VLM乙", "<bad/>", ["a:ocr:0"]), "a"),
        candidate(block("VLM丙", "<bad/>", ["unknown"]), "a"),
    ]
    fallback(values, sources)
    assert [b["fallback_source"] for b in values] == ["vlm_text"] * 3
    assert [b["text"] for b in values] == ["VLM甲", "VLM乙", "VLM丙"]
    valid = candidate(block("OCR甲", refs=["a:ocr:0"]), "a")
    failed = candidate(block("仅此块", "<bad/>", ["a:ocr:0"]), "a")
    fallback([valid, failed], sources)
    assert failed["fallback_source"] == "vlm_text"


@pytest.mark.parametrize("refs", [None, "invalid", [42], ["a:ocr:0", "a:ocr:0"]])
def test_malformed_source_references_do_not_crash_fallback(refs):
    b = candidate({"xml": "<bad/>", "text": "可用文本", "ocr_refs": refs}, "a")
    fallback([b], segments("a", "OCR"))
    assert b["fallback_source"] == "vlm_text"


def test_whole_image_fallback_once_when_json_or_length_is_incomplete(tmp_path):
    texts = {"a": "整图OCR", "b": "下一张"}
    store = setup(tmp_path, texts)
    models = Models(
        texts, actions=[response(json.dumps(submit(block("截断候选"))), "length")]
    )
    run(store, models)
    assert [b["text"] for b in result(store)["blocks"]] == list(texts.values())
    assert result(store)["blocks"][0]["fallback_source"] == "ocr"
    assert len(models.requests) == 2


def test_unresolved_position_produces_partial_xml_and_continues(tmp_path):
    texts = {"a": "", "b": "后续"}
    store = setup(tmp_path, texts)
    run(store, Models(texts, actions=[response("not json")]))
    output = result(store)
    assert output["blocks"][0]["status"] == "unresolved"
    assert output["blocks"][0]["xml"] is output["blocks"][0]["text"] is None
    assert output["blocks"][1]["text"] == "后续"
    assert output["xml_status"] == "partial" and output["needs_review"]
    assert not (store.root / "document.c2d.xml").exists()
    assert validate_document(
        (store.root / "document.partial.c2d.xml").read_bytes()
    ).valid


def test_tail_rollback_only_falls_back_current_image_text(tmp_path):
    texts = {"a": "旧尾块", "b": "新增\n后段"}
    store = setup(tmp_path, texts)
    action = submit(
        block("后段", refs=["b:ocr:1"]), tail=block("旧尾块新增", "<bad/>", ["b:ocr:0"])
    )
    run(store, Models(texts, actions=[None, action] + [repeat_bad] * 5))
    output = result(store)["blocks"]
    assert [b["text"] for b in output] == ["旧尾块", "新增", "后段"]
    assert output[1]["fallback_source"] == "ocr"


def test_unseparable_tail_becomes_hole_instead_of_copying_old_text(tmp_path):
    texts = {"a": "旧文字", "b": "新文字"}
    store = setup(tmp_path, texts)
    action = submit(tail=block("无法区分旧新", "<bad/>"))
    run(store, Models(texts, actions=[None, action] + [repeat_bad] * 5))
    output = result(store)["blocks"]
    assert output[0]["text"] == "旧文字"
    assert output[1]["status"] == "unresolved"


def test_tail_continuation_preserves_stable_internal_identity(tmp_path):
    texts = {"a": "旧", "b": "新增"}
    store = setup(tmp_path, texts)
    action = submit(tail=block("旧新增", refs=["b:ocr:0"]))
    run(store, Models(texts, actions=[None, action]))
    assert len(store.state["blocks"]) == 1
    assert result(store)["blocks"][0]["text"] == "旧新增"
    assert store.state["blocks"][0]["version"] == 1
    assert store.state["blocks"][0]["ocr_refs"] == ["a:ocr:0", "b:ocr:0"]


def test_stale_duplicate_and_out_of_scope_responses_are_rejected():
    draft = new_draft("a", None)
    initialize(draft, submit(block("甲", "<bad/>"), block("乙")))
    target = draft["blocks"][0]
    attempt = start_attempt(draft, [target["id"]])
    proposal = {
        "attempt_id": attempt["attempt_id"],
        "target_versions": attempt["target_versions"],
        "blocks": [block("甲")],
    }
    with pytest.raises(RejectedPatch, match="scope"):
        apply_patch(draft, attempt, {**proposal, "target_versions": {"forbidden": 0}})
    target["version"] += 1
    with pytest.raises(RejectedPatch, match="Stale"):
        apply_patch(draft, attempt, proposal)
    target["version"] -= 1
    apply_patch(draft, attempt, proposal)
    snapshot = deepcopy(draft)
    with pytest.raises(RejectedPatch, match="Duplicate"):
        apply_patch(draft, attempt, proposal)
    assert draft == snapshot


def test_split_and_merge_inherit_shared_budget():
    draft = new_draft("a", None)
    initialize(draft, submit(block("失败", "<bad/>")))
    target = draft["blocks"][0]
    for count in range(1, 6):
        attempt = start_attempt(draft, [b["id"] for b in draft["blocks"]])
        proposal = {
            "attempt_id": attempt["attempt_id"],
            "target_versions": attempt["target_versions"],
            "blocks": (
                [block("失", "<bad/>"), block("败", "<bad/>")]
                if count % 2
                else [block("失败", "<bad/>")]
            ),
        }
        apply_patch(draft, attempt, proposal)
        assert max(draft["budgets"].values()) == count
        assert all(b["lineage"] == [target["id"]] for b in draft["blocks"])
    with pytest.raises(RejectedPatch, match="budget"):
        start_attempt(draft, [b["id"] for b in draft["blocks"]])


def test_interrupted_repair_budget_survives_resume_and_completed_resume_needs_no_gpu(
    tmp_path,
):
    texts = {"a": "可用", "b": "后来"}
    store = setup(tmp_path, texts)
    models = Models(
        texts,
        actions=[submit(block("可用", "<bad/>")), repeat_bad, KeyboardInterrupt()],
    )
    with pytest.raises(KeyboardInterrupt):
        run(store, models)
    assert max(store.state["draft"]["budgets"].values()) == 2
    store.load()
    resumed = Models(texts, actions=[repeat_bad] * 3 + [None])
    run(store, resumed)
    assert result(store)["blocks"][0]["repair_attempts"] == 5
    assert result(store)["blocks"][1]["text"] == "后来"
    assert len(resumed.requests) == 4  # Interrupted attempt was not requested again.
    store.load()
    no_models = Models({})
    (store.root / "document.json").unlink()
    run(store, no_models)
    assert no_models.events == []
    assert len(store.state["batches"]) == 2


def test_received_response_is_replayed_after_interruption_without_second_call(
    tmp_path, monkeypatch
):
    import capture2doc.pipeline.document as module

    store = setup(tmp_path, {"a": "正文"})
    original = module.initialize

    def interrupted(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(module, "initialize", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(store, Models({"a": "正文"}))
    store.load()
    monkeypatch.setattr(module, "initialize", original)
    models = Models({"a": "正文"})
    run(store, models)
    assert not models.requests
    assert len(store.state["blocks"]) == 1


def test_combined_title_error_is_repaired_locally(tmp_path):
    store = setup(tmp_path, {"a": "先正文后标题"})
    models = Models(
        {"a": "先正文后标题"},
        actions=[submit(block("先正文"), block("后标题", "<title>后标题</title>"))],
    )
    run(store, models)
    assert len(models.requests[1]["targets"]) == 1
    assert models.requests[1]["targets"][0]["text"] == "后标题"
    assert validate_document((store.root / "document.c2d.xml").read_bytes()).valid


def test_ocr_difference_is_diagnostic_not_a_repair_gate(tmp_path):
    store = setup(tmp_path, {"a": "OCR包含桌面环境文字，遗漏了正文"})
    models = Models(
        {"a": "OCR包含桌面环境文字，遗漏了正文"},
        actions=[
            submit(
                block(
                    "图片有效正文",
                    '<p><b><span text-color="red">图片有效正文</span></b></p>',
                )
            )
        ],
    )
    run(store, models)
    assert len(models.requests) == 1
    assert result(store)["blocks"][0]["status"] == "ok"
    assert store.state["batches"][0]["diagnostic"]["hard_gate"] is False


def test_readonly_tools_return_complete_history_and_are_bounded(tmp_path):
    store = setup(tmp_path, {"a": "历史", "b": "后文"})
    models = Models(
        {"a": "历史", "b": "后文"},
        actions=[None, {"action": "read_blocks", "block_ids": [0]}, None],
    )
    run(store, models)
    assert (
        models.requests[2]["tool_results"][0]["result"]["blocks"][0]["text"] == "历史"
    )
    assert len(result(store)["blocks"]) == 2
    assert history_tool(
        {"action": "read_blocks", "block_ids": [-1]}, store.state["blocks"]
    )["error"]
    assert (
        history_tool(
            {"action": "search_blocks", "query": "历史"}, store.state["blocks"]
        )["blocks"][0]["block_id"]
        == 0
    )


def test_context_overflow_keeps_full_tail_and_falls_back_without_request(tmp_path):
    texts = {"a": "长尾" * 2000, "b": "后来"}
    store = setup(tmp_path, texts)
    models = Models(texts)
    original = models.inspect

    def inspect(path, prompt, system):
        if path.stem == "b":
            assert "长尾" * 2000 in json.loads(prompt)["mutable_tail"]["text"]
            return SimpleNamespace(prompt_tokens=16380, to_dict=lambda: {})
        return original(path, prompt, system)

    models.inspect = inspect
    run(store, models)
    assert len(models.requests) == 1
    assert result(store)["blocks"][1]["fallback_source"] == "ocr"


def test_corruption_and_modified_exports_are_fatal(tmp_path):
    store = setup(tmp_path, {"a": "正文"})
    run(store, Models({"a": "正文"}))
    data = json.loads(store.state_path.read_text())
    data["blocks"][0]["text"] = "tampered"
    write_json(store.state_path, data)
    with pytest.raises(ValueError, match="integrity"):
        BlockStore(store.root).load()
    store.save()
    (store.root / "document.json").write_text("user edit")
    with pytest.raises(ValueError, match="OUTPUT_CONFLICT"):
        run(store, Models({}))


def test_gpu_cleanup_failure_stops_before_next_model(tmp_path):
    store = setup(tmp_path, {"a": "正文"})
    models = Models({"a": "正文"})
    models.release_failure = True
    with pytest.raises(RuntimeError, match="recover"):
        run(store, models)
    assert "start:qwen" not in models.events


def test_legacy_ocr_import_checks_input_model_and_raw_content(tmp_path):
    from capture2doc.pipeline.runner import run_document

    old, manifest = document(tmp_path, {"a": "OCR正文"})
    models = FakeModels({"a": "OCR正文"})
    models.config = {"paddle": {"model": "same"}, "qwen": {"model": "old"}}
    run_document(old, models, progress=lambda _: None)
    new = BlockStore(tmp_path / "new")
    new.create(manifest)
    for image_id, image in new.state["images"].items():
        image["model_image_sha256"] = old.state["images"][image_id][
            "model_image_sha256"
        ]
    new.save()
    config = {"paddle": {"model": "same"}, "qwen": {"model": "new"}}
    assert import_ocr(new, old.root, config) == 1
    assert new.state["images"]["a"]["sources"][0]["text"] == "OCR正文"
    new.state["images"]["a"]["ocr"] = None
    with pytest.raises(ValueError, match="configuration mismatch"):
        import_ocr(new, old.root, {"paddle": {"model": "changed"}})
    raw = old.root / old.state["images"]["a"]["ocr"]["response_ref"]
    data = json.loads(raw.read_text())
    data["choices"][0]["message"]["content"] = "different"
    write_json(raw, data)
    with pytest.raises(ValueError, match="content mismatch"):
        import_ocr(new, old.root, config)


def test_cli_v2_completed_resume_exit_code_and_no_cuda(tmp_path):
    store = setup(tmp_path, {"a": "正文"})
    run(store, Models({"a": "正文"}))
    script = Path(__file__).resolve().parents[1] / "scripts/run_document.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--resume",
            "--output-dir",
            str(store.root),
            "--gpu-lock",
            str(tmp_path / "gpu.lock"),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["blocks"] == 1


def test_complete_examples_are_all_valid():
    assert len(examples()) == 5
    for xml in examples().values():
        assert validate_update(xml).valid


def test_export_interruption_recovers_atomic_checkpoint(tmp_path, monkeypatch):
    import capture2doc.pipeline.document as module

    store = setup(tmp_path, {"a": "第一图", "b": "第二图"})
    original = module.atomic_write
    armed = True

    def fail_report(path, data):
        nonlocal armed
        if path.name == "blocks.md" and armed:
            armed = False
            raise OSError("injected export write failure")
        original(path, data)

    monkeypatch.setattr(module, "atomic_write", fail_report)
    with pytest.raises(OSError, match="injected"):
        run(store, Models({"a": "第一图", "b": "第二图"}))
    assert len(store.state["batches"]) == 1
    store.load()
    resumed = Models({"a": "第一图", "b": "第二图"})
    run(store, resumed)
    assert [b["text"] for b in result(store)["blocks"]] == ["第一图", "第二图"]
    assert len(resumed.requests) == 1


def test_multiple_failed_blocks_have_independent_five_attempt_budgets(tmp_path):
    texts = {"a": "甲\n乙", "b": "后续"}
    store = setup(tmp_path, texts)
    actions = [
        submit(block("甲", "<bad/>", ["a:ocr:0"]), block("乙", "<bad/>", ["a:ocr:1"]))
    ] + [repeat_bad] * 10
    models = Models(texts, actions=actions)
    run(store, models)
    assert [b["repair_attempts"] for b in result(store)["blocks"]] == [5, 5, 0]
    assert len(models.requests) == 12
    assert [b["text"] for b in result(store)["blocks"]] == ["甲", "乙", "后续"]


def test_completed_draft_artifact_corruption_stops_resume(tmp_path):
    store = setup(tmp_path, {"a": "正文"})
    run(store, Models({"a": "正文"}))
    (store.root / store.state["batches"][0]["draft_ref"]).write_text("corrupt")
    with pytest.raises(ValueError, match="artifact hash"):
        BlockStore(store.root).load()


def test_history_tool_budget_does_not_loop_forever(tmp_path):
    store = setup(tmp_path, {"a": "第一图", "b": "第二图"})
    actions = [None] + [{"action": "read_blocks", "block_ids": [0]}] * 4
    models = Models({"a": "第一图", "b": "第二图"}, actions=actions)
    run(store, models)
    assert len(models.requests) == 5
    assert result(store)["blocks"][1]["fallback_source"] == "ocr"
    assert len(result(store)["blocks"]) == 2


def test_bad_repair_field_types_remain_local_failures():
    draft = new_draft("a", None)
    initialize(draft, submit(block("可用", "<bad/>")))
    attempt = start_attempt(draft, [draft["blocks"][0]["id"]])
    proposal = {
        "attempt_id": attempt["attempt_id"],
        "target_versions": attempt["target_versions"],
        "blocks": [{"xml": ["not XML"], "text": ["not a string"], "ocr_refs": None}],
    }
    apply_patch(draft, attempt, proposal)
    assert draft["blocks"][0]["status"] == "pending"
    fallback(draft["blocks"], [])
    assert draft["blocks"][0]["status"] == "fallback"
    assert draft["blocks"][0]["text"] == "可用"


def test_plain_text_table_keeps_header_and_body_on_separate_lines():
    value = candidate(
        block(
            "项目 值 A 1",
            "<table><thead><tr><th>项目</th><th>值</th></tr></thead><tbody><tr><td>A</td><td>1</td></tr></tbody></table>",
        ),
        "a",
    )
    assert value["text"] == "项目\t值\nA\t1"


def test_truncated_ocr_whole_image_fallback_marks_unknown_remainder(tmp_path):
    store = setup(tmp_path, {"a": "部分OCR"})
    models = Models({"a": "部分OCR"}, actions=[response("bad JSON")])
    models.ocr = lambda path: response("部分OCR", "length")
    run(store, models)
    doc = result(store)
    assert [b["status"] for b in doc["blocks"]] == ["fallback", "unresolved"]
    assert doc["blocks"][0]["text"] == "部分OCR"
    assert doc["xml_status"] == "partial"


@pytest.mark.parametrize(
    "replacement",
    [
        [block("修复完成")],
        [block("", "<bad/>"), block("", "<bad/>")],
        [block("fft(audio)"), block("fft(audio)")],
        [block("", "<hr/>")],
    ],
)
def test_repair_rejects_content_loss_duplication_and_empty_split_atomically(
    replacement,
):
    draft = new_draft("a", None)
    initialize(
        draft,
        submit(
            block(
                "fft(audio)",
                "<blockquote><pre><code>fft(audio)</code></pre></blockquote>",
            )
        ),
    )
    target = draft["blocks"][0]
    attempt = start_attempt(draft, [target["id"]])
    before = deepcopy(draft)
    with pytest.raises(RejectedPatch, match="REPAIR_CONTENT_CHANGED"):
        apply_patch(
            draft,
            attempt,
            {
                "attempt_id": attempt["attempt_id"],
                "target_versions": attempt["target_versions"],
                "blocks": replacement,
            },
        )
    assert (
        draft == before
    )  # IDs, versions, source refs and useful original text survive.
    assert draft["budgets"][target["id"]] == 1
    assert target["repair_attempts"] == 1
    fallback(draft["blocks"], [])
    assert [(b["status"], b["text"]) for b in draft["blocks"]] == [
        ("fallback", "fft(audio)")
    ]


def test_content_rejections_exhaust_budget_then_keep_text_and_process_next_image(
    tmp_path,
):
    texts = {"a": "可用原文", "b": "后续图片"}
    store = setup(tmp_path, texts)

    def destructive_repair(payload):
        return {
            "action": "submit",
            "attempt_id": payload["attempt_id"],
            "target_versions": payload["target_versions"],
            "blocks": [block("修复完成")],
        }

    models = Models(
        texts,
        actions=[submit(block("可用原文", "<bad/>"))] + [destructive_repair] * 5,
    )
    run(store, models)
    output = result(store)["blocks"]
    assert [b["text"] for b in output] == ["可用原文", "后续图片"]
    assert output[0]["repair_attempts"] == 5
    assert output[0]["fallback_source"] == "vlm_text"
    assert any(e["code"] == "REPAIR_CONTENT_CHANGED" for e in output[0]["errors"])
    assert len(models.requests) == 7
    assert all(r["targets"][0]["text"] == "可用原文" for r in models.requests[1:6])


def test_repair_cannot_copy_readonly_paragraph_and_table_into_its_target_group():
    # Reproduce the real probe: one bad pre was expanded to pre + the already
    # successful paragraph and table, duplicating both outside its own scope.
    code = "fft(audio)\n"
    paragraph_neighbor = block("下表展示结果")
    table_neighbor = block(
        "A\t1", "<table><tbody><tr><td>A</td><td>1</td></tr></tbody></table>"
    )
    draft = new_draft("a", None)
    initialize(
        draft,
        submit(
            block(code, f"<blockquote><pre><code>{code}</code></pre></blockquote>"),
            paragraph_neighbor,
            table_neighbor,
        ),
    )
    attempt = start_attempt(draft, [draft["blocks"][0]["id"]])
    before = deepcopy(draft)
    with pytest.raises(RejectedPatch, match="REPAIR_CONTENT_CHANGED"):
        apply_patch(
            draft,
            attempt,
            {
                "attempt_id": attempt["attempt_id"],
                "target_versions": attempt["target_versions"],
                "blocks": [
                    block(code, f"<pre><code>{code}</code></pre>"),
                    paragraph_neighbor,
                    table_neighbor,
                ],
            },
        )
    assert draft == before
    assert len(draft["blocks"]) == 3


def test_repair_can_recover_missing_text_without_losing_known_group_content():
    draft = new_draft("a", None)
    initialize(draft, submit(block("", "<bad/>"), block("保留邻居")))
    attempt = start_attempt(draft, [b["id"] for b in draft["blocks"]])
    apply_patch(
        draft,
        attempt,
        {
            "attempt_id": attempt["attempt_id"],
            "target_versions": attempt["target_versions"],
            "blocks": [block("从图片恢复的文字"), block("保留邻居")],
        },
    )
    assert [b["text"] for b in draft["blocks"]] == ["从图片恢复的文字", "保留邻居"]


@pytest.mark.parametrize(
    "changed",
    [
        'if ready:\n  print("a b")\n',
        'if ready:\n\tprint("a b")\n',
        'if ready:    print("a b")\n',
        'if ready:\n    print("a b")',
        '\nif ready:\n    print("a b")\n',
        'if ready:\n    print("a b")\n\n',
    ],
)
def test_repair_preserves_code_indentation_linebreaks_and_trailing_newline(changed):
    code = 'if ready:\n    print("a b")\n'
    draft = new_draft("a", None)
    initialize(
        draft,
        submit(block(code, f"<blockquote><pre><code>{code}</code></pre></blockquote>")),
    )
    attempt = start_attempt(draft, [draft["blocks"][0]["id"]])
    before = deepcopy(draft)
    with pytest.raises(RejectedPatch, match="REPAIR_(CODE_WHITESPACE|CONTENT)_CHANGED"):
        apply_patch(
            draft,
            attempt,
            {
                "attempt_id": attempt["attempt_id"],
                "target_versions": attempt["target_versions"],
                "blocks": [block(changed, f"<pre><code>{changed}</code></pre>")],
            },
        )
    assert draft == before


def test_repair_can_split_structure_while_retaining_exact_code():
    code = 'if ready:\n    print("a b")\n'
    draft = new_draft("a", None)
    initialize(
        draft,
        submit(
            block(
                "引文\n" + code,
                f"<blockquote><p>引文</p><pre><code>{code}</code></pre></blockquote>",
            )
        ),
    )
    attempt = start_attempt(draft, [draft["blocks"][0]["id"]])
    apply_patch(
        draft,
        attempt,
        {
            "attempt_id": attempt["attempt_id"],
            "target_versions": attempt["target_versions"],
            "blocks": [
                block("引文", "<blockquote><p>引文</p></blockquote>"),
                block(code, f"<pre><code>{code}</code></pre>"),
            ],
        },
    )
    assert [b["text"] for b in draft["blocks"]] == ["引文", code]
    assert all(b["status"] == "ok" for b in draft["blocks"])


@pytest.mark.parametrize(
    "values",
    [
        [block("新增"), block("旧尾")],
        [block("旧"), block("尾新增")],
    ],
)
def test_tail_repair_rejects_moved_or_split_history_as_one_atomic_group(values):
    old_tail = candidate(block("旧尾"), "a")
    draft = new_draft("b", old_tail)
    initialize(draft, submit(tail=block("旧尾新增", "<bad/>")))
    attempt = start_attempt(draft, [draft["blocks"][0]["id"]])
    before = deepcopy(draft)
    with pytest.raises(RejectedPatch, match="REPAIR_CONTENT_CHANGED|TAIL_CONTENT_LOSS"):
        apply_patch(
            draft,
            attempt,
            {
                "attempt_id": attempt["attempt_id"],
                "target_versions": attempt["target_versions"],
                "blocks": values,
            },
        )
    assert draft == before
    resolve_fallback(draft, [], [old_tail])
    output = commit_blocks([old_tail], draft)
    assert [b["text"] for b in output] == ["旧尾", "新增"]


def test_tail_repair_cannot_move_duplicate_history_into_a_successful_child():
    old_tail = candidate(block("旧尾"), "a")
    draft = new_draft("b", old_tail)
    initialize(draft, submit(tail=block("旧尾新增旧尾", "<bad/>")))
    attempt = start_attempt(draft, [draft["blocks"][0]["id"]])
    before = deepcopy(draft)
    with pytest.raises(RejectedPatch, match="TAIL_CONTENT_LOSS"):
        apply_patch(
            draft,
            attempt,
            {
                "attempt_id": attempt["attempt_id"],
                "target_versions": attempt["target_versions"],
                "blocks": [block("旧尾新增"), block("旧尾")],
            },
        )
    assert draft == before


def test_unseparable_tail_disables_ocr_fallback_even_with_current_image_refs():
    old_tail = candidate(block("旧尾", refs=["a:ocr:0"]), "a")
    draft = new_draft("b", old_tail)
    initialize(draft, submit(tail=block("无法区分旧新", "<bad/>", ["b:ocr:0"])))
    resolve_fallback(draft, segments("b", "旧尾续写"), [old_tail])
    output = commit_blocks([old_tail], draft)
    assert [b["text"] for b in output] == ["旧尾", None]
    assert output[1]["status"] == "unresolved"
    assert not output[1]["ocr_refs"]


def test_known_tail_suffix_uses_text_when_ocr_also_contains_history():
    old_tail = candidate(block("旧尾", refs=["a:ocr:0"]), "a")
    draft = new_draft("b", old_tail)
    initialize(draft, submit(tail=block("旧尾续写", "<bad/>", ["b:ocr:0"])))
    resolve_fallback(draft, segments("b", "旧尾续写"), [old_tail])
    output = commit_blocks([old_tail], draft)
    assert [b["text"] for b in output] == ["旧尾", "续写"]
    assert output[1]["fallback_source"] == "vlm_text"


def test_xml_omission_repairs_real_heading_and_intro_without_dropping_either(tmp_path):
    heading = "功能亮点："
    intro = "MiniCPM-o4.5 带来了多项突破性的新特性："
    text = heading + intro
    store = setup(tmp_path, {"a": text})

    def split_title_and_intro(payload):
        target = payload["targets"][0]
        assert target["text"] == text
        issue = next(
            e for e in target["current_errors"] if e["code"] == "XML_TEXT_OMISSION"
        )
        assert issue["text_position"]["missing_suffix"] == intro
        assert issue["text_position"]["xml_start"] == 0
        assert issue["xpath"] and issue["line"]
        assert validate_update(issue["correct_example"]).valid
        return {
            "action": "submit",
            "attempt_id": payload["attempt_id"],
            "target_versions": payload["target_versions"],
            "blocks": [block(heading, f"<h2>{heading}</h2>"), block(intro)],
        }

    models = Models(
        {"a": text},
        actions=[submit(block(text, f"<h2>{heading}</h2>")), split_title_and_intro],
    )
    run(store, models)
    output = result(store)["blocks"]
    assert [b["text"] for b in output] == [heading, intro]
    assert [b["status"] for b in output] == ["ok", "ok"]
    assert [b["repair_attempts"] for b in output] == [1, 1]
    assert len(models.requests) == 2
    assert validate_document((store.root / "document.c2d.xml").read_bytes()).valid


def test_xml_omission_preserves_full_independent_text_and_both_missing_positions():
    before, after = "这里是不可省略的前置说明", "这里是不可省略的后置说明"
    value = candidate(block(before + "标题" + after, "<h2>标题</h2>"), "a")
    assert value["status"] == "pending"
    assert value["text"] == before + "标题" + after
    assert value["model_text"] == value["text"]
    issue = value["current_errors"][0]
    assert issue["code"] == "XML_TEXT_OMISSION"
    assert issue["text_position"]["xml_start"] == len(before)
    assert issue["text_position"]["missing_prefix"] == before
    assert issue["text_position"]["missing_suffix"] == after
    assert value["representation_diagnostic"]["hard_gate"]


@pytest.mark.parametrize(
    "extra, expected_status",
    [("甲乙丙丁戊己庚", "ok"), ("甲乙丙丁戊己庚辛", "pending")],
)
def test_xml_omission_requires_eight_additional_nonspace_characters(
    extra, expected_status
):
    value = candidate(block("标题\n" + extra, "<h2>标题</h2>"), "a")
    assert value["status"] == expected_status
    assert value["representation_diagnostic"]["missing_nonspace_characters"] == len(
        extra
    )


def test_short_caption_never_replaces_long_valid_table(tmp_path):
    table = (
        "<table><tbody>"
        + "".join(f"<tr><td>行{i}</td><td>完整内容{i}</td></tr>" for i in range(30))
        + "</tbody></table>"
    )
    store = setup(tmp_path, {"a": "表格"})
    models = Models({"a": "表格"}, actions=[submit(block("表格", table))])
    run(store, models)
    value = store.state["blocks"][0]
    assert value["status"] == "ok"
    assert value["model_text"] == "表格"
    assert "行29\t完整内容29" in value["text"]
    assert not value["representation_diagnostic"]["hard_gate"]
    assert not value["current_errors"]
    assert len(models.requests) == 1


@pytest.mark.parametrize("model_text", ["A\n\tB\r\nC\tD", "A B\nC D"])
def test_equivalent_table_layout_whitespace_does_not_trigger_omission(model_text):
    value = candidate(
        block(
            model_text,
            "<table><tbody><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></tbody></table>",
        ),
        "a",
    )
    assert value["status"] == "ok"
    assert value["text"] == "A\tB\nC\tD"
    assert not value["representation_diagnostic"]["hard_gate"]
    assert not value["current_errors"]


@pytest.mark.parametrize(
    "model_text, xml, relation",
    [
        (
            "<|im_start|>正文<|im_end|>",
            "<p>正文</p>",
            "code_or_control_label_difference",
        ),
        (
            "```python\nprint(1)\n```",
            "<pre><code>print(1)</code></pre>",
            "code_or_control_label_difference",
        ),
        (
            "以下代码块的控制标签\nprint(1)",
            "<pre><code>print(1)</code></pre>",
            "code_or_control_label_difference",
        ),
        ("标题: 原文内容", "<p>标题：原文内容</p>", "uncertain_difference"),
        (
            "if ready:\n  go()\n",
            "<pre><code>if ready:\n    go()\n</code></pre>",
            "code_whitespace_difference",
        ),
        ("----------------正文", "<p>正文</p>", "xml_is_text_substring"),
    ],
)
def test_uncertain_representation_differences_are_diagnostic_only(
    model_text, xml, relation
):
    value = candidate(block(model_text, xml), "a")
    assert value["status"] == "ok"
    assert value["model_text"] == model_text
    assert value["representation_diagnostic"]["relation"] == relation
    assert not value["representation_diagnostic"]["hard_gate"]
    assert not value["current_errors"]


def test_empty_xml_text_is_allowed_for_hr_but_not_a_substantial_omission():
    text = "这段明确正文不能全部消失"
    separator = candidate(block(text, "<hr/>"), "a")
    missing = candidate(block(text, "<h2/>"), "a")
    assert separator["status"] == "ok" and separator["text"] == ""
    assert separator["representation_diagnostic"]["relation"] == "non_text_separator"
    assert missing["status"] == "pending" and missing["text"] == text
    assert missing["current_errors"][0]["code"] == "XML_TEXT_OMISSION"
