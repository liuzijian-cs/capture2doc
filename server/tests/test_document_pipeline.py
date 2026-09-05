from __future__ import annotations

import json
import re
import subprocess
import sys
from contextlib import contextmanager
from html import escape
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import pytest

from capture2doc.formats.c2d_xml import (
    C2DAssembler,
    C2DAssemblyError,
    validate_document,
    validate_update,
)
from capture2doc.pipeline.models import snapshot_fingerprint, wait_released
from capture2doc.pipeline.runner import (
    PipelineError,
    content_check,
    plan_request,
    replay,
    run_document,
)
from capture2doc.pipeline.store import (
    DocumentStore,
    atomic_write,
    digest,
    exclusive_lock,
    write_json,
)
from capture2doc.prompts import c2d_system_prompt


def update(body: str) -> str:
    return f'<c2d-update xmlns="urn:capture2doc:c2d:1" schema-version="0.1">{body}</c2d-update>'


def response(content: str, reason: str = "stop") -> Any:
    return SimpleNamespace(
        content=content,
        raw_response={
            "choices": [{"message": {"content": content}, "finish_reason": reason}],
            "usage": {"prompt_tokens": 100, "completion_tokens": len(content)},
        },
    )


class FakeModels:
    """Deterministic model boundary; XML, persistence and orchestration are real."""

    def __init__(self, texts: dict[str, str], *, actions: list[Any] | None = None):
        self.texts = texts
        self.actions = list(actions or [])
        self.events: list[str] = []
        self.requests: list[dict[str, Any]] = []
        self.qwen = SimpleNamespace(max_model_len=16384, max_output_tokens=8192)
        self.config = {"test_backend": 1}
        self.release_failure = False

    def prepare(self) -> dict[str, Any]:
        return self.config

    def image_info(self, path: Path, destination: Path) -> dict[str, Any]:
        data = path.read_bytes()
        atomic_write(destination, data)
        return {"width": 10, "height": 10, "model_image_sha256": digest(data)}

    @contextmanager
    def phase(self, name: str, directory: Path):
        self.events.append(f"start:{name}")
        try:
            yield
        finally:
            self.events.append(f"stop:{name}")
            if self.release_failure:
                raise RuntimeError("GPU memory did not recover")

    def ocr(self, path: Path) -> Any:
        self.events.append(f"ocr:{path.stem}")
        return response(self.texts[path.stem])

    def count_tokens(self, text: str) -> int:
        return len(text)

    def inspect(self, path: Path, prompt: str, system: str) -> Any:
        tokens = len(prompt) + len(system) // 2 + 1280
        return SimpleNamespace(
            prompt_tokens=tokens,
            rendered_prompt=system + "\n" + prompt,
            to_dict=lambda: {"prompt_tokens": tokens},
        )

    def generate(
        self, path: Path, prompt: str, system: str, inspection: Any, output: int
    ) -> Any:
        payload = json.loads(prompt)
        self.requests.append(payload)
        self.events.append(f"qwen:{path.stem}")
        if self.actions:
            action = self.actions.pop(0)
            if isinstance(action, BaseException):
                raise action
            if action is not None:
                return action
        return response(
            update(
                (payload["mutable_tail"] or "")
                + f"<p>{escape(payload['ocr_text'])}</p>"
            )
        )


def document(
    tmp_path: Path, texts: dict[str, str], order: list[str] | None = None
) -> tuple[DocumentStore, Path]:
    manifest = tmp_path / "input.json"
    for image_id in texts:
        (tmp_path / f"{image_id}.jpg").write_bytes(f"fake image {image_id}".encode())
    write_json(
        manifest,
        {
            "document_id": "doc-1",
            "lang": "zh-CN",
            "images": [{"image_id": i, "path": f"{i}.jpg"} for i in texts],
            "ordered_image_ids": order or list(texts),
        },
    )
    store = DocumentStore(tmp_path / "result")
    store.create(manifest)
    return store, manifest


def run(store: DocumentStore, models: FakeModels, **kwargs: Any) -> Path:
    return run_document(store, models, progress=lambda _: None, **kwargs)


def test_final_order_model_switching_and_completed_replay(tmp_path: Path) -> None:
    texts = {"a": "甲的正文。", "b": "乙的正文。", "deleted": "删除图片不参与文档。"}
    store, _ = document(tmp_path, texts, ["b", "a"])
    models = FakeModels(texts)
    output = run(store, models)
    assert models.events == [
        "start:paddle",
        "ocr:b",
        "ocr:a",
        "stop:paddle",
        "start:qwen",
        "qwen:b",
        "qwen:a",
        "stop:qwen",
    ]
    assert validate_document(output.read_bytes()).valid
    assert (
        "".join(ElementTree.fromstring(output.read_bytes()).itertext())
        == "乙的正文。甲的正文。"
    )
    assert store.state["images"]["deleted"]["ocr"] is None
    assert store.state["status"] == "complete"
    assert "image_id" not in output.read_text()
    resumed = DocumentStore(store.root)
    resumed.load()
    no_models = FakeModels({})
    output.unlink()  # Final file can be reconstructed solely from committed rounds.
    assert run(resumed, no_models).read_bytes() == output.read_bytes()
    assert no_models.events == []
    assert len(resumed.state["rounds"]) == 2


def test_input_identity_and_order_are_frozen(tmp_path: Path) -> None:
    store, manifest = document(tmp_path, {"a": "A", "b": "B"})
    store.create(manifest)  # Same manifest is idempotent.
    data = json.loads(manifest.read_text())
    data["ordered_image_ids"] = ["b", "a"]
    write_json(manifest, data)
    with pytest.raises(ValueError, match="Frozen"):
        store.create(manifest)
    data["ordered_image_ids"] = ["a", "b"]
    write_json(manifest, data)
    (tmp_path / "a.jpg").write_bytes(b"retaken image with reused identity")
    with pytest.raises(ValueError, match="Frozen"):
        store.create(manifest)


@pytest.mark.parametrize("order", [["a", "a"], ["unknown"], []])
def test_invalid_final_list_is_rejected_before_checkpoint(
    tmp_path: Path, order: list[str]
) -> None:
    store, manifest = document(tmp_path, {"a": "A"})
    data = json.loads(manifest.read_text())
    data["ordered_image_ids"] = order
    write_json(manifest, data)
    with pytest.raises(ValueError, match="ordered_image_ids"):
        DocumentStore(tmp_path / "other").create(manifest)


def test_ocr_length_keeps_raw_response_without_qwen_or_automatic_repeat(
    tmp_path: Path,
) -> None:
    store, _ = document(tmp_path, {"a": "text"})
    models = FakeModels({"a": "text"})
    models.ocr = lambda path: response("incomplete text", "length")
    with pytest.raises(PipelineError, match="4096"):
        run(store, models)
    attempt = store.state["images"]["a"]["ocr_attempts"][0]
    assert (store.root / attempt["response_ref"]).is_file()
    assert store.state["images"]["a"]["ocr"] is None
    assert "start:qwen" not in models.events
    store.load()
    with pytest.raises(PipelineError, match="4K"):
        run(store, FakeModels({"a": "text"}))
    assert len(store.state["images"]["a"]["ocr_attempts"]) == 1
    run(store, FakeModels({"a": "text"}), retry_failed=True)
    assert store.state["status"] == "complete"


def test_invalid_xml_repair_uses_unchanged_tail(tmp_path: Path) -> None:
    texts = {"a": "第一段。", "b": "第二段。"}
    store, _ = document(tmp_path, texts)
    models = FakeModels(texts, actions=[None, response("<broken>"), None])
    output = run(store, models)
    assert len(store.state["rounds"]) == 2
    assert len(store.state["attempts"]) == 3
    assert models.requests[1]["mutable_tail"] == models.requests[2]["mutable_tail"]
    assert models.requests[2]["retry_errors"]
    assert (
        "".join(ElementTree.fromstring(output.read_bytes()).itertext())
        == "第一段。第二段。"
    )


def test_interruption_reuses_ocr_and_does_not_reapply_committed_round(
    tmp_path: Path,
) -> None:
    texts = {"a": "已提交。", "b": "后来输入。"}
    store, _ = document(tmp_path, texts)
    models = FakeModels(texts, actions=[None, KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        run(store, models)
    assert models.events[-1] == "stop:qwen"
    assert store.state["status"] == "interrupted"
    assert len(store.state["rounds"]) == 1
    resumed = DocumentStore(store.root)
    resumed.load()
    models = FakeModels(texts)
    output = run(resumed, models)
    assert models.events == ["start:qwen", "qwen:b", "stop:qwen"]
    assert (
        "".join(ElementTree.fromstring(output.read_bytes()).itertext())
        == "已提交。后来输入。"
    )
    assert len(resumed.state["rounds"]) == 2


def test_truncated_valid_xml_is_discarded_and_source_windows_cover_once(
    tmp_path: Path,
) -> None:
    texts = {"a": "abcdefgh"}
    store, _ = document(tmp_path, texts)
    models = FakeModels(texts, actions=[response(update("<p>abcd</p>"), "length")])
    output = run(store, models)
    assert [(r["source_start"], r["source_end"]) for r in store.state["rounds"]] == [
        (0, 4),
        (4, 8),
    ]
    assert "".join(ElementTree.fromstring(output.read_bytes()).itertext()) == "abcdefgh"
    assert store.state["attempts"][0]["status"] == "failed"


def test_repair_attempt_budget_persists_across_resumes(tmp_path: Path) -> None:
    store, _ = document(tmp_path, {"a": "原文"})
    models = FakeModels({"a": "原文"}, actions=[response("bad")] * 3)
    with pytest.raises(PipelineError, match="Round 1"):
        run(store, models)
    store.load()
    models = FakeModels({"a": "原文"})
    with pytest.raises(PipelineError, match="Round 1"):
        run(store, models)
    assert not models.requests
    run(store, models, retry_failed=True)
    assert len(store.state["rounds"]) == 1


def test_release_failure_prevents_qwen_and_resume_reuses_ocr(tmp_path: Path) -> None:
    store, _ = document(tmp_path, {"a": "文字"})
    models = FakeModels({"a": "文字"})
    models.release_failure = True
    with pytest.raises(RuntimeError, match="recover"):
        run(store, models)
    assert models.events == ["start:paddle", "ocr:a", "stop:paddle"]
    assert store.state["images"]["a"]["ocr"] is not None


def test_committed_round_is_never_generated_again_after_progress_failure(
    tmp_path: Path,
) -> None:
    store, _ = document(tmp_path, {"a": "正文"})
    models = FakeModels({"a": "正文"})

    def fail_after_commit(message: str) -> None:
        if "committed" in message:
            raise OSError("simulated reporting failure after checkpoint")

    with pytest.raises(OSError):
        run_document(store, models, progress=fail_after_commit)
    assert len(models.requests) == 1
    store.load()
    resumed_models = FakeModels({"a": "正文"})
    run(store, resumed_models)
    assert not resumed_models.events
    assert len(store.state["rounds"]) == 1


def test_prompt_or_model_drift_rejects_mixed_run(tmp_path: Path) -> None:
    store, _ = document(tmp_path, {"a": "正文"})
    models = FakeModels({"a": "正文"}, actions=[KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        run(store, models)
    store.load()
    models.config = {"test_backend": 2}
    with pytest.raises(ValueError, match="configuration changed"):
        run(store, models)


def test_replay_checks_cursor_and_response_hashes(tmp_path: Path) -> None:
    store, _ = document(tmp_path, {"a": "正文"})
    run(store, FakeModels({"a": "正文"}))
    store.state["rounds"][0]["source_start"] = 1
    with pytest.raises(PipelineError, match="mismatch"):
        replay(store)


def test_existing_changed_output_is_not_overwritten(tmp_path: Path) -> None:
    store, _ = document(tmp_path, {"a": "正文"})
    output = run(store, FakeModels({"a": "正文"}))
    output.write_text("user edit")
    with pytest.raises(PipelineError, match="differs"):
        run(store, FakeModels({"a": "正文"}))
    assert output.read_text() == "user edit"


def test_broken_output_symlink_is_not_replaced(tmp_path: Path) -> None:
    store, _ = document(tmp_path, {"a": "正文"})
    output = run(store, FakeModels({"a": "正文"}))
    output.unlink()
    output.symlink_to(tmp_path / "missing.xml")
    with pytest.raises(PipelineError):
        run(store, FakeModels({"a": "正文"}))
    assert output.is_symlink()


def test_code_tail_checks_significant_indentation() -> None:
    tail = '<pre xmlns="urn:capture2doc:c2d:1"><code>if ok:\n    run()</code></pre>'
    _, errors = content_check(
        update("<pre><code>if ok:\nrun()</code></pre><p>后文</p>"), tail, "后文"
    )
    assert any("CODE_CONTENT_LOSS" in error for error in errors)


def test_local_model_phase_cleans_up_after_request_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import capture2doc.pipeline.models as module

    events = []

    class Runtime:
        process = SimpleNamespace(pid=12345)

        def __init__(self, *args: Any):
            pass

        def start(self):
            events.append("start")

        def wait_ready(self):
            events.append("ready")

        def fetch_models(self):
            return {}

        def stop(self):
            events.append("stop")

    sampler = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        snapshot=lambda _: SimpleNamespace(used_mib=1000),
        summary=lambda: {},
    )
    monkeypatch.setattr(module, "VllmRuntime", Runtime)
    monkeypatch.setattr(module, "GpuMemorySampler", lambda: sampler)
    monkeypatch.setattr(
        module, "ensure_group_exited", lambda pid: events.append(f"group:{pid}")
    )
    monkeypatch.setattr(
        module, "wait_released", lambda *args: events.append("released")
    )
    models = module.LocalModels(cache_dir=str(tmp_path))
    models.paths["paddle"] = tmp_path
    with pytest.raises(RuntimeError, match="request failed"):
        with models.phase("paddle", tmp_path / "run"):
            raise RuntimeError("request failed")
    assert events == ["start", "ready", "stop", "group:12345", "released"]
    metrics = json.loads((tmp_path / "run/paddle.metrics.json").read_text())
    assert metrics["error"] == "request failed"


def test_content_loss_and_duplicate_text_have_separate_diagnostics() -> None:
    tail = '<p xmlns="urn:capture2doc:c2d:1">必须保留的旧正文</p>'
    report, errors = content_check(update("<p>新正文</p>"), tail, "新正文")
    assert any("TAIL_CONTENT_LOSS" in e for e in errors)
    report, errors = content_check(update("<p>原文原文原文</p>"), None, "原文")
    assert errors and report["needs_review"]
    assert report["semantic_fidelity_verified"] is False


def test_long_tail_cannot_hide_an_omitted_new_window() -> None:
    body = "历史正文" * 500
    tail = f'<p xmlns="urn:capture2doc:c2d:1">{body}</p>'
    report, errors = content_check(update(f"<p>{body}</p>"), tail, "新增的重要数字123")
    assert errors
    assert report["source_coverage"] == 0


def test_moderate_content_difference_produces_review_status(tmp_path: Path) -> None:
    text = "abcdefghijklmnopqrst"
    store, _ = document(tmp_path, {"a": text})
    output = run(
        store,
        FakeModels({"a": text}, actions=[response(update(f"<p>{text[:-2]}</p>"))]),
    )
    assert validate_document(output.read_bytes()).valid
    assert store.state["status"] == "needs_review"


def test_large_tail_is_returned_whole_only_with_explicit_actual_budget() -> None:
    assembler = C2DAssembler()
    assert assembler.apply_update(update(f"<p>{'字' * 1700}</p>")).valid
    with pytest.raises(C2DAssemblyError):
        assembler.context_blocks(count_tokens=len)
    blocks = assembler.context_blocks(
        count_tokens=len, token_budget=2500, allow_large_tail=True
    )
    assert len(blocks) == 1
    assert "字" * 1700 in blocks[0].decode()
    with pytest.raises(C2DAssemblyError):
        assembler.context_blocks(
            count_tokens=len, token_budget=1600, allow_large_tail=True
        )


def test_dynamic_budget_never_truncates_tail(tmp_path: Path) -> None:
    assembler = C2DAssembler()
    assembler.apply_update(update(f"<p>{'字' * 1700}</p>"))
    models = FakeModels({})
    end, prompt, inspection, output, tail = plan_request(
        models,
        assembler,
        tmp_path / "a.png",
        "a",
        "新正文" * 2000,
        0,
        6000,
        c2d_system_prompt(),
        [],
        None,
        True,
    )
    assert end <= 6000
    assert "字" * 1700 in tail
    assert json.loads(prompt)["mutable_tail"] == tail
    assert inspection.prompt_tokens + output + 512 <= models.qwen.max_model_len


def test_prompt_examples_validate_against_real_schema() -> None:
    examples = c2d_system_prompt().split("八、合法示例", 1)[1]
    responses = re.findall(r"<c2d-update\b.*?</c2d-update>", examples, re.DOTALL)
    assert len(responses) == 3
    for example in responses:
        assert validate_update(example).valid


def test_gpu_memory_recovery_is_a_gate_not_a_silent_timeout() -> None:
    sampler = SimpleNamespace(snapshot=lambda _: SimpleNamespace(used_mib=10000))
    with pytest.raises(RuntimeError, match="Next model was not started"):
        wait_released(sampler, 1000, timeout=0)


def test_unreleased_previous_worker_blocks_resume_without_signaling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import capture2doc.pipeline.models as module

    write_json(
        tmp_path / "old/qwen.metrics.json",
        {
            "cleanup_verified": False,
            "pgid": 12345,
            "baseline_memory_mib": 1000,
        },
    )
    signals = []
    monkeypatch.setattr(module.os, "killpg", lambda pid, sig: signals.append(sig))
    with pytest.raises(RuntimeError, match="Previous worker group"):
        module.verify_previous_cleanup(tmp_path)
    assert signals == [0]


def test_document_lock_excludes_second_owner(tmp_path: Path) -> None:
    with exclusive_lock(tmp_path / "lock"):
        with pytest.raises(RuntimeError, match="Already in use"):
            with exclusive_lock(tmp_path / "lock"):
                pytest.fail("second owner entered")


def test_snapshot_fingerprint_detects_config_and_weight_metadata_changes(
    tmp_path: Path,
) -> None:
    (tmp_path / "config.json").write_text('{"a":1}')
    first = snapshot_fingerprint(tmp_path)
    (tmp_path / "config.json").write_text('{"a":2}')
    assert snapshot_fingerprint(tmp_path) != first


@pytest.mark.parametrize("review", [False, True])
def test_real_cli_resumes_finished_document_without_cuda(
    tmp_path: Path, review: bool
) -> None:
    text = "abcdefghijklmnopqrst"
    store, _ = document(tmp_path, {"a": text})
    actions = [response(update(f"<p>{text[:-2]}</p>"))] if review else []
    run(store, FakeModels({"a": text}, actions=actions))
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
        timeout=10,
    )
    assert completed.returncode == (3 if review else 0), completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["status"] == ("needs_review" if review else "complete")
    assert summary["semantic_fidelity_verified"] is False
