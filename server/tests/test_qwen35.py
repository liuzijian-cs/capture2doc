from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from capture2doc.config import Qwen35Settings
from capture2doc.inference.qwen35 import analyze_image, validate_prompt_budget


class FakeCompletions:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        raw = {
            "choices": [{"message": {"content": "document"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 20},
        }
        message = SimpleNamespace(content="document", reasoning=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            model_dump=lambda **_kwargs: raw,
        )


def make_client() -> tuple[Any, FakeCompletions]:
    completions = FakeCompletions()
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_analyze_image_uses_fp8_worker_contract(tmp_path: Path) -> None:
    image_path = tmp_path / "document.jpg"
    image_path.write_bytes(b"not-decoded-by-the-client")
    settings = Qwen35Settings(cache_dir=tmp_path / "cache")
    client, completions = make_client()

    result = analyze_image(
        image_path,
        "Read the document.",
        settings,
        prompt_tokens=1_300,
        client=client,
    )

    request = completions.requests[0]
    assert result.content == "document"
    assert request["model"] == "Qwen3.5-9B"
    assert request["temperature"] == 0
    assert request["max_tokens"] == 8_192
    assert request["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    assert request["messages"][0]["content"][1]["text"] == "Read the document."


def test_analyze_image_can_enable_thinking_and_override_output(tmp_path: Path) -> None:
    image_path = tmp_path / "document.png"
    image_path.write_bytes(b"not-decoded-by-the-client")
    settings = Qwen35Settings(cache_dir=tmp_path / "cache")
    client, completions = make_client()

    analyze_image(
        image_path,
        "Describe it.",
        settings,
        prompt_tokens=500,
        enable_thinking=True,
        max_tokens=1_024,
        client=client,
    )

    request = completions.requests[0]
    assert request["max_tokens"] == 1_024
    assert request["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True


def test_analyze_image_can_force_stress_output_length(tmp_path: Path) -> None:
    image_path = tmp_path / "document.png"
    image_path.write_bytes(b"not-decoded-by-the-client")
    settings = Qwen35Settings(cache_dir=tmp_path / "cache")
    client, completions = make_client()

    analyze_image(
        image_path,
        "Stress it.",
        settings,
        prompt_tokens=500,
        max_tokens=2_048,
        ignore_eos=True,
        request_timeout_seconds=1_200,
        client=client,
    )

    request = completions.requests[0]
    assert request["max_tokens"] == 2_048
    assert request["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False},
        "ignore_eos": True,
    }


def test_prompt_budget_reserves_full_default_output(tmp_path: Path) -> None:
    settings = Qwen35Settings(cache_dir=tmp_path)

    assert validate_prompt_budget(8_192, settings) == 8_192
    with pytest.raises(ValueError, match="at most 8192"):
        validate_prompt_budget(8_193, settings)


def test_request_timeout_must_be_positive(tmp_path: Path) -> None:
    image_path = tmp_path / "document.png"
    image_path.write_bytes(b"not-decoded-by-the-client")
    settings = Qwen35Settings(cache_dir=tmp_path / "cache")
    client, _completions = make_client()

    with pytest.raises(ValueError, match="request_timeout_seconds"):
        analyze_image(
            image_path,
            "Stress it.",
            settings,
            prompt_tokens=500,
            request_timeout_seconds=0,
            client=client,
        )
