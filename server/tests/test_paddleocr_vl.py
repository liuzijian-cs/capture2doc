from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from capture2doc.config import PaddleOcrVlSettings
from capture2doc.inference.paddleocr_vl import recognize_image


class FakeCompletions:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        raw = {
            "choices": [{"message": {"content": "recognized"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        }
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="recognized"))],
            model_dump=lambda **_kwargs: raw,
        )


def make_client() -> tuple[Any, FakeCompletions]:
    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def test_recognize_image_uses_configured_output_limit(tmp_path: Path) -> None:
    image_path = tmp_path / "document.jpg"
    image_path.write_bytes(b"not-decoded-by-the-client")
    settings = PaddleOcrVlSettings(cache_dir=tmp_path / "cache", max_output_tokens=4_096)
    client, completions = make_client()

    result = recognize_image(image_path, settings, client=client)

    assert result.content == "recognized"
    assert completions.requests[0]["max_tokens"] == 4_096
    assert completions.requests[0]["temperature"] == 0


def test_recognize_image_allows_explicit_output_override(tmp_path: Path) -> None:
    image_path = tmp_path / "document.png"
    image_path.write_bytes(b"not-decoded-by-the-client")
    settings = PaddleOcrVlSettings(cache_dir=tmp_path / "cache")
    client, completions = make_client()

    recognize_image(image_path, settings, client=client, max_tokens=512)

    assert completions.requests[0]["max_tokens"] == 512
