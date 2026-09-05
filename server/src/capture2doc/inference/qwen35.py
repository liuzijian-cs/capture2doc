"""OpenAI-compatible client for one Qwen3.5 image-and-text request."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from capture2doc.config import Qwen35Settings
from capture2doc.inference.image_input import image_data_url
from capture2doc.inference.messages import image_messages

DEFAULT_DOCUMENT_PROMPT = (
    "请阅读这张文档图片，按原有阅读顺序转写清晰可见的内容，"
    "并简要说明标题、正文、列表、表格等结构。"
)


@dataclass(frozen=True, slots=True)
class Qwen35Result:
    content: str
    reasoning: str | None
    raw_response: dict[str, Any]


def _create_openai_client(
    settings: Qwen35Settings,
    *,
    timeout_seconds: float = 600.0,
) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The OpenAI client is not installed. On NVIDIA/WSL run "
            "`uv sync --extra cuda`."
        ) from exc
    return OpenAI(
        api_key="EMPTY",
        base_url=settings.api_base_url,
        timeout=timeout_seconds,
    )


def validate_prompt_budget(
    prompt_tokens: int,
    settings: Qwen35Settings,
    *,
    max_tokens: int | None = None,
) -> int:
    """Reserve the requested output budget instead of silently truncating input."""

    output_limit = settings.max_output_tokens if max_tokens is None else max_tokens
    if prompt_tokens <= 0:
        raise ValueError("prompt_tokens must be positive")
    if output_limit <= 0:
        raise ValueError("max_tokens must be positive")
    maximum_prompt_tokens = settings.max_model_len - output_limit
    if prompt_tokens > maximum_prompt_tokens:
        raise ValueError(
            f"Prompt uses {prompt_tokens} tokens, but at most {maximum_prompt_tokens} "
            f"fit while reserving {output_limit} output tokens in the "
            f"{settings.max_model_len}-token context."
        )
    return output_limit


def analyze_image(
    image_path: str | Path,
    prompt: str,
    settings: Qwen35Settings,
    *,
    prompt_tokens: int,
    enable_thinking: bool = False,
    client: Any | None = None,
    max_tokens: int | None = None,
    ignore_eos: bool = False,
    request_timeout_seconds: float = 600.0,
    system_prompt: str | None = None,
    allow_empty: bool = False,
) -> Qwen35Result:
    """Send one preflighted image-and-text request to the local Qwen worker."""

    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    output_limit = validate_prompt_budget(
        prompt_tokens,
        settings,
        max_tokens=max_tokens,
    )
    if request_timeout_seconds <= 0:
        raise ValueError("request_timeout_seconds must be positive")
    openai_client = client or _create_openai_client(
        settings,
        timeout_seconds=request_timeout_seconds,
    )
    extra_body: dict[str, Any] = {
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    if ignore_eos:
        extra_body["ignore_eos"] = True
    response = openai_client.chat.completions.create(
        model=settings.served_model_name,
        messages=image_messages(
            prompt, system_prompt=system_prompt, image_url=image_data_url(image_path)
        ),
        temperature=0,
        max_tokens=output_limit,
        extra_body=extra_body,
    )
    message = response.choices[0].message
    content = message.content
    if not isinstance(content, str) or not content.strip():
        if not allow_empty:
            raise RuntimeError("Qwen3.5 returned an empty response")
        content = content if isinstance(content, str) else ""

    reasoning = getattr(message, "reasoning", None)
    if reasoning is None:
        reasoning = getattr(message, "reasoning_content", None)
    if not isinstance(reasoning, str):
        reasoning = None

    if hasattr(response, "model_dump"):
        raw_response = response.model_dump(mode="json")
    else:
        raw_response = dict(response)
    return Qwen35Result(
        content=content,
        reasoning=reasoning,
        raw_response=raw_response,
    )
