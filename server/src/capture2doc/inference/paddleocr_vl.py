"""OpenAI-compatible client for a single-image PaddleOCR-VL request."""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from capture2doc.config import PaddleOcrVlSettings

SUPPORTED_IMAGE_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
}


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


def image_data_url(image_path: str | Path) -> str:
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Image does not exist: {path}")
    mime_type = SUPPORTED_IMAGE_TYPES.get(path.suffix.lower())
    if mime_type is None:
        guessed_type, _ = mimetypes.guess_type(path.name)
        raise ValueError(
            f"Unsupported image type {guessed_type or path.suffix!r}; use PNG or JPEG."
        )
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def recognize_image(
    image_path: str | Path,
    settings: PaddleOcrVlSettings,
    *,
    client: Any | None = None,
    max_tokens: int = 4096,
) -> OcrResult:
    """Send one image in user-provided order to the local VLM worker."""

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
        max_tokens=max_tokens,
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("PaddleOCR-VL returned an empty OCR response")

    if hasattr(response, "model_dump"):
        raw_response = response.model_dump(mode="json")
    else:
        raw_response = dict(response)
    return OcrResult(content=content, raw_response=raw_response)
