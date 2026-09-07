from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from capture2doc.config import inference_backend, qwen_settings
from capture2doc.inference.mlx_runtime import MlxRuntime, _generate, configure_image_processor
from capture2doc.pipeline import models as module
from capture2doc.service.settings import Settings


def _fake_model_process(connection, *_):
    connection.send({"metrics": {"weight_bytes": 100}})
    while True:
        request = connection.recv()
        if request is None:
            return
        connection.send({"content": request["image"], "raw_response": {}})


def test_real_subprocess_request_and_exit(tmp_path, monkeypatch):
    from capture2doc.inference import mlx_runtime

    monkeypatch.setattr(mlx_runtime, "_serve", _fake_model_process)
    runtime = MlxRuntime(qwen_settings(tmp_path, model="4b"), tmp_path, tmp_path / "mlx.log")
    try:
        runtime.start()
        runtime.wait_ready()
        assert runtime.metrics["weight_bytes"] == 100
        assert runtime.generate({"image": "sample.png"})["content"] == "sample.png"
    finally:
        runtime.stop()
    assert not runtime.process.is_alive()
    assert runtime.process.exitcode == 0


@pytest.mark.parametrize("backend,size,dtype,quantization", [
    ("cuda-vllm", "9b", "bfloat16", "fp8_per_channel"),
    ("cuda-vllm", "4b", "bfloat16", None),
    ("apple-mlx", "4b", "bfloat16", None),
    ("apple-mlx", "9b", "bfloat16", None),
])
def test_model_profiles(tmp_path, backend, size, dtype, quantization):
    settings = qwen_settings(tmp_path, model=size, backend=backend)
    assert settings.model_id == f"Qwen/Qwen3.5-{size.upper()}"
    assert settings.served_model_name == f"Qwen3.5-{size.upper()}"
    assert (settings.dtype, settings.quantization) == (dtype, quantization)


@pytest.mark.parametrize("has_size", [True, False])
def test_image_budgets_match_hf_and_mlx_processor_layouts(tmp_path, has_size):
    (tmp_path / "preprocessor_config.json").write_text(json.dumps({"size": {"shortest_edge": 65536}}))
    image_processor = SimpleNamespace(min_pixels=3136, max_pixels=1003520)
    if has_size:
        image_processor.size = {"shortest_edge": 3136, "longest_edge": 1003520}
    configure_image_processor(SimpleNamespace(image_processor=image_processor), qwen_settings(model="4b"), tmp_path)
    assert image_processor.min_pixels == 65536
    assert image_processor.max_pixels == 1310720
    if has_size:
        assert image_processor.size == {"shortest_edge": 65536, "longest_edge": 1310720}


@pytest.mark.parametrize("system,machine,backend", [
    ("Darwin", "arm64", "apple-mlx"), ("Linux", "x86_64", "cuda-vllm"),
])
def test_backend_detection(monkeypatch, system, machine, backend):
    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("platform.machine", lambda: machine)
    assert inference_backend() == backend


def test_service_config_and_environment_choose_same_model(tmp_path, monkeypatch):
    config = tmp_path / "service.toml"
    config.write_text('[service]\nqwen_model = "4b"\n')
    assert Settings.load(config).qwen_model == "4b"
    monkeypatch.setenv("C2D_QWEN_MODEL", "9b")
    assert Settings.load(config).qwen_model == "9b"
    with pytest.raises(ValueError, match="qwen_model"):
        Settings(qwen_model="4bit")
    with pytest.raises(ValueError, match="qwen_model"):
        qwen_settings(tmp_path, model="4bit")


def test_worker_cli_model_overrides_environment(tmp_path, monkeypatch):
    from capture2doc.service import cli, worker

    captured = []
    monkeypatch.setenv("C2D_QWEN_MODEL", "9b")
    monkeypatch.setattr(worker, "Worker", lambda settings: SimpleNamespace(
        serve=lambda: captured.append(settings.qwen_model)))
    assert cli.main(["worker", "--qwen-model", "4b"]) == 0
    assert captured == ["4b"]


def test_service_worker_passes_model_choice_to_local_models(tmp_path, monkeypatch):
    from capture2doc.service import worker

    captured = []
    monkeypatch.setattr(worker, "LocalModels", lambda **kwargs: captured.append(kwargs) or object())
    worker.Worker(Settings(data_root=tmp_path, qwen_model="4b"))
    assert captured[0]["qwen_model"] == "4b"


@pytest.mark.parametrize("failure", [None, "ready", "request", "stop"])
def test_mlx_phases_never_overlap_and_cleanup_gates_next_model(tmp_path, monkeypatch, failure):
    events = []

    class Runtime:
        process = SimpleNamespace(pid=12345)
        metrics = {"peak_memory_bytes": 123}

        def __init__(self, settings, *args):
            self.settings = settings

        def start(self):
            events.append("start")

        def wait_ready(self):
            events.append("ready")
            if failure == "ready":
                raise RuntimeError("ready failed")

        def stop(self):
            events.append("stop")
            if failure == "stop":
                raise RuntimeError("stop failed")

    monkeypatch.setattr(module, "inference_backend", lambda: "apple-mlx")
    monkeypatch.setattr(module, "MlxRuntime", Runtime)
    monkeypatch.setattr(module, "GpuMemorySampler", lambda: pytest.fail("NVIDIA query on Mac"))
    models = module.LocalModels(cache_dir=str(tmp_path), qwen_model="4b")
    models.paths = {"paddle": tmp_path, "qwen": tmp_path}

    def run():
        with models.phase("paddle", tmp_path):
            with pytest.raises(RuntimeError, match="already active"):
                with models.phase("qwen", tmp_path):
                    pytest.fail("two models loaded")
            if failure == "request":
                raise RuntimeError("request failed")

    if failure:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            run()
    else:
        run()
        with models.phase("qwen", tmp_path):
            pass
    assert events[:3] == ["start", "ready", "stop"]
    metrics = json.loads((tmp_path / "paddle.metrics.json").read_text())
    assert metrics["cleanup_verified"] is (failure != "stop")
    assert metrics["mlx_memory"]["peak_memory_bytes"] == 123
    if failure == "stop":
        with pytest.raises(RuntimeError, match="already active"):
            with models.phase("qwen", tmp_path):
                pytest.fail("cleanup failure allowed next phase")
    else:
        assert models.active_mlx is None


def test_mlx_recovery_requires_process_exit_without_nvidia(tmp_path, monkeypatch):
    path = tmp_path / "run" / "qwen.metrics.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"backend": "apple-mlx", "worker_pid": 12345,
                                "cleanup_verified": False}))
    monkeypatch.setattr(module.os, "kill", lambda *_: None)
    with pytest.raises(RuntimeError, match="still exists"):
        module.verify_previous_cleanup(tmp_path)

    def exited(*_):
        raise ProcessLookupError

    monkeypatch.setattr(module.os, "kill", exited)
    monkeypatch.setattr(module, "GpuMemorySampler", lambda: pytest.fail("NVIDIA query on Mac"))
    module.verify_previous_cleanup(tmp_path)
    assert "recovery_verified_at" in json.loads(path.read_text())


def test_mlx_receive_reports_timeout_and_child_failure(tmp_path):
    runtime = MlxRuntime(qwen_settings(tmp_path, model="4b"), tmp_path, tmp_path / "mlx.log")
    runtime.connection = SimpleNamespace(poll=lambda _: False)
    with pytest.raises(TimeoutError, match="exceeded"):
        runtime._receive(.01)
    runtime.connection = SimpleNamespace(poll=lambda _: True, recv=lambda: {"error": "load failed"})
    with pytest.raises(RuntimeError, match="load failed"):
        runtime._receive(.01)


def test_timed_out_request_cannot_consume_a_late_response(tmp_path):
    runtime = MlxRuntime(qwen_settings(tmp_path, model="4b"), tmp_path, tmp_path / "mlx.log")
    runtime.process = SimpleNamespace(is_alive=lambda: True)
    runtime.busy = True
    with pytest.raises(RuntimeError, match="restart the phase"):
        runtime.generate({"image": "another.png"})


def test_missing_4b_snapshot_suggests_correct_download(tmp_path):
    from capture2doc.inference.model_store import ModelNotPreparedError, resolve_prepared_model

    with pytest.raises(ModelNotPreparedError, match="prepare_qwen35.py --qwen-model 4b"):
        resolve_prepared_model(qwen_settings(tmp_path / "missing", model="4b"))


@pytest.mark.parametrize("tokens,output,expected_error", [
    (12, 32, None), (13, 32, "differs from preflight"), (12, 16384, "budget"),
])
def test_mlx_generation_preserves_preflight_and_schema(tmp_path, monkeypatch, tokens, output, expected_error):
    calls = []
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    mx = SimpleNamespace(get_active_memory=lambda: 10, get_peak_memory=lambda: 20, clear_cache=lambda: None)
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=mx))
    monkeypatch.setitem(sys.modules, "mlx.core", mx)

    def generate(model, processor, prompt, **kwargs):
        calls.append((prompt, kwargs))
        return SimpleNamespace(text='{"answer":"ok"}', generation_tokens=6, finish_reason="stop")

    monkeypatch.setitem(sys.modules, "mlx_vlm", SimpleNamespace(generate=generate))
    monkeypatch.setitem(sys.modules, "mlx_vlm.prompt_utils", SimpleNamespace(apply_chat_template=lambda *a, **k: "OCR"))
    monkeypatch.setitem(sys.modules, "mlx_vlm.structured", SimpleNamespace(
        build_json_schema_logits_processor=lambda tokenizer, value: (tokenizer, value)))
    monkeypatch.setitem(sys.modules, "mlx_vlm.utils", SimpleNamespace(prepare_inputs=lambda *a, **k: {
        "input_ids": SimpleNamespace(shape=(1, tokens)), "pixel_values": "pixels", "attention_mask": "mask",
    }))
    settings = qwen_settings(tmp_path, model="4b")
    request = {"image": "image.png", "rendered_prompt": "exact template", "prompt_tokens": 12,
               "max_tokens": output, "response_schema": schema}
    invoke = lambda: _generate(SimpleNamespace(config=SimpleNamespace()), SimpleNamespace(tokenizer="tokenizer"), settings, request)
    if expected_error:
        with pytest.raises(ValueError, match=expected_error):
            invoke()
        assert not calls
    else:
        result = invoke()
        assert result["raw_response"]["usage"]["prompt_tokens"] == 12
        prompt, kwargs = calls[0]
        assert prompt == "exact template"
        assert kwargs["logits_processors"] == [("tokenizer", schema)]
        assert kwargs["enable_thinking"] is False
        assert kwargs["mask"] == "mask"
        assert kwargs["max_tokens"] == 32
