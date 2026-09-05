"""Evaluation checks remain read-only and distinguish structure from fidelity."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from capture2doc.pipeline.evaluation import (
    duplicate_blocks,
    evaluate,
    proposal_text_diagnostic,
    visual_checks,
)
from capture2doc.pipeline.store import write_json
from test_block_pipeline import Models, block, run, setup, submit


def test_visual_checks_use_real_style_scope_and_count_blocks_once():
    blocks = [
        {"image_id": "a", "xml": "<h2>标题</h2>"},
        {
            "image_id": "a",
            "xml": '<p><b><span background-color="yellow">重点</span></b> 普通字</p>',
        },
        {
            "image_id": "b",
            "xml": '<p><span text-color="red">重点</span><a href="https://example.org">链接</a></p>',
        },
        {"image_id": "b", "xml": "<p>重点 重点</p>"},
    ]
    checks = [
        {"id": "heading", "kind": "bold", "pattern": "标题"},
        {
            "id": "bold",
            "kind": "bold",
            "pattern": "重点",
            "expected": {"min": 1, "max": 1},
        },
        {
            "id": "highlight",
            "kind": "highlight",
            "pattern": "重点",
            "expected": {"min": 1, "max": 1},
        },
        {
            "id": "not-bold",
            "kind": "bold",
            "pattern": "普通字",
            "expected": {"min": 0, "max": 0},
        },
        {"id": "link", "kind": "link", "pattern": "链接"},
        {
            "id": "content",
            "kind": "content",
            "image_id": "b",
            "pattern": "重点",
            "expected": {"min": 2, "max": 2},
        },
    ]
    result = visual_checks(blocks, checks)
    assert result["all_passed"]
    assert result["checks"][2]["block_ids"] == [1]
    assert result["checks"][-1]["block_ids"] == [2, 3]
    assert "not complete" in result["scope"]


def test_visual_checks_validate_ids_ranges_and_regex():
    with pytest.raises(ValueError, match="unique"):
        visual_checks([], [{"id": "x", "pattern": "x"}] * 2)
    with pytest.raises(ValueError, match="min/max"):
        visual_checks(
            [], [{"id": "x", "pattern": "x", "expected": {"min": 2, "max": 1}}]
        )


def test_text_color_checks_do_not_require_or_infer_hyperlinks():
    blocks = [
        {"image_id": "a", "xml": '<p><span text-color="blue">Ollama</span></p>'},
        {"image_id": "a", "xml": '<p><a href="https://example.org">Ollama</a></p>'},
        {"image_id": "a", "xml": '<p><span text-color="red">Ollama</span></p>'},
    ]
    checks = [
        {
            "id": "blue",
            "kind": "text_color",
            "color": "blue",
            "pattern": "Ollama",
            "expected": {"min": 1, "max": 1},
        },
        {
            "id": "any-color",
            "kind": "text_color",
            "pattern": "Ollama",
            "expected": {"min": 2, "max": 2},
        },
    ]
    result = visual_checks(blocks, checks)
    assert result["all_passed"]
    assert result["checks"][0]["block_ids"] == [0]
    assert result["checks"][1]["block_ids"] == [0, 2]
    assert not visual_checks([blocks[1]], [checks[0]])["all_passed"]
    with pytest.raises(ValueError, match="Invalid text color"):
        visual_checks(
            [], [{"id": "bad", "kind": "text_color", "color": "cyan", "pattern": "x"}]
        )


def test_diagnostics_do_not_conflate_invalid_xml_or_whitespace_differences():
    result = proposal_text_diagnostic(
        {"text": "中文\n代码", "xml": "<p>中文<br/>代码</p>"}
    )
    assert result["exact_match"] is True
    result = proposal_text_diagnostic(
        {"text": "中文 代码", "xml": "<p>中文<br/>代码</p>"}
    )
    assert result["exact_match"] is False
    assert result["whitespace_normalized_match"] is True
    broken = proposal_text_diagnostic({"text": "独立原文", "xml": "<p><bad>残缺"})
    assert broken["proposal_text"] == "独立原文"
    assert broken["valid_xml_text"] is None
    assert broken["comparable"] is False
    multiple = proposal_text_diagnostic({"text": "两段", "xml": "<p>一</p><p>二</p>"})
    assert multiple["comparable"] is False
    assert multiple["valid_xml_text"] is None
    repeated = "这是一段足够长的正文内容" * 8
    output = duplicate_blocks(
        [{"text": repeated}, {"text": "\n" + repeated}, {"text": "短标题"}] * 2
    )
    assert len(output) == 1
    assert output[0]["block_ids"] == [0, 1, 3, 4]
    assert output[0]["byte_identical_text"] is False


class TracedModels(Models):
    wrong_tokens = False

    def prepare(self):
        return {"qwen": {"settings": {"max_model_len": self.qwen.max_model_len}}}

    @contextmanager
    def phase(self, name, directory):
        try:
            yield
        finally:
            write_json(
                directory / f"{name}.metrics.json",
                {"model": name, "cleanup_verified": True},
            )

    def generate(
        self, path, prompt, system, inspection, output, *, response_schema=None
    ):
        result = super().generate(
            path, prompt, system, inspection, output, response_schema=response_schema
        )
        result.raw_response["usage"]["prompt_tokens"] = inspection.prompt_tokens + int(
            self.wrong_tokens
        )
        return result


def test_evaluate_saved_trace_and_cli_read_only(tmp_path):
    store = setup(tmp_path, {"a": "原始参考"})
    run(
        store,
        TracedModels(
            {"a": "原始参考"},
            actions=[submit(block("独立候选", "<p><b>实际XML</b></p>"))],
        ),
    )
    before = {p: p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    report = evaluate(
        store.root, checks=[{"id": "bold", "kind": "bold", "pattern": "实际XML"}]
    )
    assert report["verification_passed"]
    assert report["all_prompt_counts_match"]
    assert report["visual_sample"]["all_passed"]
    assert report["images"][0]["candidate_texts"][0]["text"] == "实际XML"
    diagnostic = report["images"][0]["proposal_text_diagnostics"][0]["blocks"][0]
    assert diagnostic["exact_match"] is False
    assert diagnostic["proposal_text"] == "独立候选"
    assert {p: p.read_bytes() for p in before} == before
    script = Path(__file__).parents[1] / "scripts/evaluate_document.py"
    checks = tmp_path / "checks.json"
    write_json(checks, [{"id": "missing", "kind": "highlight", "pattern": "实际XML"}])
    process = subprocess.run(
        [sys.executable, str(script), str(store.root), "--checks", str(checks)],
        text=True,
        capture_output=True,
    )
    assert process.returncode == 3
    assert json.loads(process.stdout)["verification_passed"]


def test_evaluator_rejects_actual_token_mismatch(tmp_path):
    store = setup(tmp_path, {"a": "正文"})
    models = TracedModels({"a": "正文"})
    models.wrong_tokens = True
    run(store, models)
    report = evaluate(store.root)
    assert report["verification_passed"] is False
    assert report["all_prompt_counts_match"] is False
    assert any(
        not check["passed"] and check["id"].startswith("actual_prompt_tokens:")
        for check in report["verification"]
    )


def test_evaluator_detects_modified_exports_and_missing_cleanup_evidence(tmp_path):
    store = setup(tmp_path, {"a": "正文"})
    run(store, TracedModels({"a": "正文"}))
    document_path = store.root / "document.json"
    document = json.loads(document_path.read_text())
    document["doc"]["blocks"][0]["text"] = "被修改"
    write_json(document_path, document)
    for path in (store.root / "runs").glob("*/*.metrics.json"):
        path.unlink()
    report = evaluate(store.root)
    assert report["verification_passed"] is False
    failed = {row["id"] for row in report["verification"] if not row["passed"]}
    assert {
        "block_projection",
        "export_hash:document.json",
        "phase_metrics_available",
    } <= failed
