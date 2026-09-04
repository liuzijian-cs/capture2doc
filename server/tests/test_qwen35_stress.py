from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from capture2doc.inference.qwen35_stress import (
    BOUNDARY_MAX_PROMPT_TOKENS,
    build_stress_prompt,
    context_fits,
    generate_exact_token_payload,
    parse_kv_cache_capacity,
    validate_output_stages,
)


class RepeatingTokenizer:
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        assert add_special_tokens is False
        if text and set(text.split()) == {"x"}:
            return [1] * len(text.split())
        return [1] * (len(text) + 1)


class NeverExactTokenizer:
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        assert add_special_tokens is False
        return [1] * (len(text) + 1)


def load_stress_script() -> ModuleType:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "stress_qwen35.py"
    spec = importlib.util.spec_from_file_location("stress_qwen35", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generates_exact_4k_and_8k_payloads() -> None:
    tokenizer = RepeatingTokenizer()

    for target in (4_096, 8_192):
        payload = generate_exact_token_payload(tokenizer, target, candidates=(" x",))
        assert len(tokenizer.encode(payload, add_special_tokens=False)) == target
        assert "<stress-payload>" in build_stress_prompt(payload)


def test_exact_payload_fails_instead_of_silently_approximating() -> None:
    with pytest.raises(RuntimeError, match="exact synthetic payload"):
        generate_exact_token_payload(
            NeverExactTokenizer(),
            8,
            candidates=("x",),
        )


def test_context_matrix_distinguishes_4k_and_8k_inputs() -> None:
    assert context_fits(1_230 + 43 + 4_096, 8_192, 16_384)
    assert not context_fits(1_230 + 43 + 8_192, 8_192, 16_384)
    assert context_fits(1_230 + 43 + 8_192, 8_192, 17_728)
    assert BOUNDARY_MAX_PROMPT_TOKENS == 9_536


def test_output_stages_must_be_strictly_increasing() -> None:
    assert validate_output_stages([2_048, 4_096, 8_192]) == (2_048, 4_096, 8_192)
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_output_stages([4_096, 2_048])
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_output_stages([2_048, 2_048])
    with pytest.raises(ValueError, match="must not exceed 8192"):
        validate_output_stages([2_048, 8_193])


def test_parses_latest_vllm_kv_capacity() -> None:
    log = "GPU KV cache size: 16,384 tokens\nGPU KV cache size: 17,788 tokens\n"
    assert parse_kv_cache_capacity(log) == 17_788
    assert parse_kv_cache_capacity("no capacity here") is None


def test_stress_cli_defaults_to_complete_matrix() -> None:
    module = load_stress_script()
    args = module.parse_args(["--image", "document.jpg"])

    assert args.case == "all"
    assert args.output_stages == [2_048, 4_096, 8_192]
    assert args.request_timeout_seconds == 1_200


def test_worker_case_preserves_completed_stage_after_later_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_stress_script()
    log_path = tmp_path / "vllm.log"

    class FakeRuntime:
        def __init__(self, _settings: object, _model_path: object, path: Path) -> None:
            self.log_path = path

        def start(self) -> None:
            self.log_path.write_text(
                "GPU KV cache size: 17,788 tokens\n",
                encoding="utf-8",
            )

        def wait_ready(self, **_kwargs: object) -> None:
            return None

        def fetch_models(self) -> dict[str, list[object]]:
            return {"data": []}

        def stop(self) -> None:
            return None

    class FakeSampler:
        def snapshot(self, _name: str) -> SimpleNamespace:
            return SimpleNamespace(used_mib=1_000)

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def begin_window(self) -> int:
            return 0

        def window_summary(self, _start: int) -> dict[str, int]:
            return {"peak_memory_mib": 15_900}

        def summary(self) -> dict[str, int]:
            return {"peak_memory_mib": 15_900}

    calls = 0

    def fake_analyze(*_args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic OOM")
        requested = kwargs["max_tokens"]
        return SimpleNamespace(
            content="0",
            raw_response={
                "choices": [{"finish_reason": "length"}],
                "usage": {
                    "prompt_tokens": 5_300,
                    "completion_tokens": requested,
                },
            },
        )

    monkeypatch.setattr(module, "VllmRuntime", FakeRuntime)
    monkeypatch.setattr(module, "GpuMemorySampler", FakeSampler)
    monkeypatch.setattr(module, "analyze_image", fake_analyze)
    monkeypatch.setattr(module, "wait_for_memory_recovery", lambda *_args: None)

    settings = SimpleNamespace(
        max_model_len=16_384,
        max_output_tokens=8_192,
        kv_cache_memory_bytes=640 * 1024**2,
        max_num_batched_tokens=4_096,
        max_num_seqs=1,
    )
    inspection = SimpleNamespace(prompt_tokens=5_300, to_dict=lambda: {})
    image_path = tmp_path / "document.jpg"
    image_path.write_bytes(b"fake")
    model_path = tmp_path / "model"
    model_path.mkdir()

    result = module.run_worker_case(
        name="4k-default",
        image_path=image_path,
        prompt="stress",
        payload_tokens=4_096,
        inspection=inspection,
        settings=settings,
        model_path=model_path,
        output_dir=tmp_path,
        output_stages=(2_048, 4_096),
        request_timeout_seconds=1_200,
        minimum_kv_capacity=16_384,
    )

    assert result["status"] == "failed"
    assert result["stages"][0]["status"] == "passed"
    assert result["stages"][1]["error"]["message"] == "synthetic OOM"
    saved = json.loads(
        (tmp_path / "4k-default" / "case-summary.json").read_text(encoding="utf-8")
    )
    assert [stage["status"] for stage in saved["stages"]] == ["passed", "failed"]
