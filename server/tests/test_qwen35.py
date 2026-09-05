from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from capture2doc.config import Qwen35Settings
from capture2doc.inference.qwen35 import analyze_image, validate_prompt_budget
from capture2doc.inference.messages import image_messages


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
    assert request["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
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


def test_system_message_is_shared_with_preflight_representation(tmp_path: Path) -> None:
    path = tmp_path / "image.jpg"
    path.write_bytes(b"image")
    client, completions = make_client()
    prompt = '文档里有 "quotes"、{变量} 和 <标签>。'
    system = "完整 C2D 规则"
    analyze_image(
        path,
        prompt,
        Qwen35Settings(cache_dir=tmp_path),
        prompt_tokens=500,
        client=client,
        system_prompt=system,
    )
    sent = completions.requests[0]["messages"]
    inspected = image_messages(prompt, system_prompt=system)
    assert sent[0] == inspected[0] == {"role": "system", "content": system}
    assert sent[1]["content"][1] == inspected[1]["content"][1]
    assert sent[1]["content"][0]["type"] == "image_url"
    assert inspected[1]["content"][0]["type"] == "image"


def test_json_schema_transport_keeps_existing_image_messages_and_budget(
    tmp_path: Path,
) -> None:
    path = tmp_path / "document.png"
    path.write_bytes(b"image")
    client, completions = make_client()
    schema = {
        "type": "object",
        "properties": {"xml": {"type": "string"}},
        "required": ["xml"],
    }
    analyze_image(
        path,
        "Convert",
        Qwen35Settings(cache_dir=tmp_path),
        prompt_tokens=1300,
        client=client,
        system_prompt="Full contract",
        response_schema=schema,
    )
    request = completions.requests[0]
    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "c2d_action", "schema": schema},
    }
    assert request["messages"][0]["content"] == "Full contract"
    assert request["messages"][1]["content"][1]["text"] == "Convert"
    assert request["max_tokens"] == 8192
    assert request["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
