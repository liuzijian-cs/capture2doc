"""Helpers for sending local images to OpenAI-compatible multimodal APIs."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

SUPPORTED_IMAGE_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
}


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
