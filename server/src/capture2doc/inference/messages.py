"""One message builder shared by multimodal preflight and inference."""

from typing import Any


def image_messages(
    prompt: str, *, system_prompt: str | None = None, image_url: str | None = None
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if system_prompt is not None:
        if not system_prompt.strip():
            raise ValueError("system_prompt must not be empty")
        messages.append({"role": "system", "content": system_prompt})
    image = (
        {"type": "image"}
        if image_url is None
        else {"type": "image_url", "image_url": {"url": image_url}}
    )
    messages.append(
        {"role": "user", "content": [image, {"type": "text", "text": prompt}]}
    )
    return messages
