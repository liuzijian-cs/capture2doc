"""OpenAI-compatible client for a single-image PaddleOCR-VL request."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from capture2doc.config import PaddleOcrVlSettings
from capture2doc.inference.image_input import image_data_url


@dataclass(frozen=True, slots=True)
class OcrResult:
    content: str
    raw_response: dict[str, Any]


def _create_openai_client(settings: PaddleOcrVlSettings) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The OpenAI client is not installed. On NVIDIA/WSL run "
            "`uv sync --extra cuda`."
        ) from exc
    return OpenAI(api_key="EMPTY", base_url=settings.api_base_url, timeout=300.0)


def recognize_image(
    image_path: str | Path,
    settings: PaddleOcrVlSettings,
    *,
    client: Any | None = None,
    max_tokens: int | None = None,
) -> OcrResult:
    """Send one image in user-provided order to the local VLM worker."""

    output_limit = settings.max_output_tokens if max_tokens is None else max_tokens
    if output_limit <= 0:
        raise ValueError("max_tokens must be positive")
    openai_client = client or _create_openai_client(settings)
    response = openai_client.chat.completions.create(
        model=settings.served_model_name,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url(image_path)},
                    },
                    {"type": "text", "text": "OCR:"},
                ],
            }
        ],
        temperature=0,
        max_tokens=output_limit,
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("PaddleOCR-VL returned an empty OCR response")

    if hasattr(response, "model_dump"):
        raw_response = response.model_dump(mode="json")
    else:
        raw_response = dict(response)
    return OcrResult(content=content, raw_response=raw_response)
