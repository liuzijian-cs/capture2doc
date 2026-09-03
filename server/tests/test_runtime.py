from __future__ import annotations

import io
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from capture2doc.config import PaddleOcrVlSettings
from capture2doc.inference import runtime as runtime_module
from capture2doc.inference.runtime import RuntimeStartError, VllmRuntime


def make_runtime(tmp_path: Path) -> VllmRuntime:
    model_path = tmp_path / "model"
    model_path.mkdir()
    settings = PaddleOcrVlSettings(cache_dir=tmp_path / "cache")
    return VllmRuntime(settings, model_path, tmp_path / "worker.log")


def test_build_command_contains_fixed_first_pass_settings(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    command = runtime.build_command()

    assert command[:2] == ["vllm", "serve"]
    assert ["--dtype", "bfloat16"] == command[command.index("--dtype") : command.index("--dtype") + 2]
    assert "--trust-remote-code" in command
    assert "--no-enable-prefix-caching" in command
    assert command[command.index("--mm-processor-cache-gb") + 1] == "0"
    assert command[command.index("--max-num-seqs") + 1] == "1"
    assert command[command.index("--max-model-len") + 1] == "8192"
    assert command[command.index("--max-num-batched-tokens") + 1] == "4096"
    assert command[command.index("--kv-cache-memory-bytes") + 1] == "268435456"
    assert command[command.index("--mm-processor-kwargs") + 1] == '{"max_pixels":1003520}'
    assert command[command.index("--limit-mm-per-prompt") + 1] == '{"image":1}'
    assert "--gpu-memory-utilization" not in command


def test_build_command_can_use_gpu_utilization_instead(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    settings = PaddleOcrVlSettings(
        cache_dir=tmp_path / "cache",
        kv_cache_memory_bytes=None,
        gpu_memory_utilization=0.2,
    )
    runtime = VllmRuntime(settings, model_path, tmp_path / "worker.log")

    command = runtime.build_command()

    assert command[command.index("--gpu-memory-utilization") + 1] == "0.2"
    assert "--kv-cache-memory-bytes" not in command


def test_build_environment_enables_wsl_pin_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    monkeypatch.delenv("VLLM_WSL2_ENABLE_PIN_MEMORY", raising=False)
    monkeypatch.setattr(runtime_module, "is_wsl2", lambda: True)

    environment = runtime.build_environment()

    assert environment["VLLM_WSL2_ENABLE_PIN_MEMORY"] == "1"
    assert environment["VLLM_USE_FLASHINFER_SAMPLER"] == "0"
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"


def test_build_environment_preserves_explicit_wsl_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    monkeypatch.setenv("VLLM_WSL2_ENABLE_PIN_MEMORY", "0")
    monkeypatch.setattr(runtime_module, "is_wsl2", lambda: True)

    assert runtime.build_environment()["VLLM_WSL2_ENABLE_PIN_MEMORY"] == "0"


def test_build_environment_preserves_explicit_sampler_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    monkeypatch.setenv("VLLM_USE_FLASHINFER_SAMPLER", "1")

    assert runtime.build_environment()["VLLM_USE_FLASHINFER_SAMPLER"] == "1"


def test_build_environment_does_not_enable_pin_memory_outside_wsl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    monkeypatch.delenv("VLLM_WSL2_ENABLE_PIN_MEMORY", raising=False)
    monkeypatch.setattr(runtime_module, "is_wsl2", lambda: False)

    assert "VLLM_WSL2_ENABLE_PIN_MEMORY" not in runtime.build_environment()


class HealthyResponse(io.BytesIO):
    status = 200

    def __enter__(self) -> "HealthyResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def test_wait_ready_accepts_healthy_response(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime._process = SimpleNamespace(poll=lambda: None)  # type: ignore[assignment]

    runtime.wait_ready(timeout_seconds=0.1, urlopen_fn=lambda *_args, **_kwargs: HealthyResponse())


def test_wait_ready_reports_early_exit_and_log_tail(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.log_path.write_text("important failure\n", encoding="utf-8")
    runtime._process = SimpleNamespace(poll=lambda: 3)  # type: ignore[assignment]

    with pytest.raises(RuntimeStartError, match="important failure"):
        runtime.wait_ready(timeout_seconds=0.1)


def test_wait_ready_reports_health_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    runtime._process = SimpleNamespace(poll=lambda: None)  # type: ignore[assignment]
    times = iter((0.0, 1.0))
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: next(times))

    with pytest.raises(RuntimeStartError, match="within 0.5s"):
        runtime.wait_ready(timeout_seconds=0.5)


def test_stop_escalates_after_terminate_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    wait_calls = 0

    class FakeProcess:
        pid = 12345

        def poll(self) -> None:
            return None

        def wait(self, *, timeout: float) -> int:
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls == 1:
                raise subprocess.TimeoutExpired("vllm", timeout)
            return 0

    runtime._process = FakeProcess()  # type: ignore[assignment]
    signals: list[signal.Signals] = []
    monkeypatch.setattr(runtime, "_send_signal", signals.append)

    runtime.stop(timeout_seconds=0.01)

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert runtime.process is None
