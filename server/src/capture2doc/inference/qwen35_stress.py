"""Pure helpers for repeatable Qwen3.5 context and decode stress tests."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

DEFAULT_OUTPUT_STAGES = (2_048, 4_096, 8_192)
STRESS_KV_CACHE_MEMORY_BYTES = 640 * 1024**2
DEFAULT_CONTEXT_TOKENS = 16_384
BOUNDARY_CONTEXT_TOKENS = 17_728
BOUNDARY_MAX_PROMPT_TOKENS = BOUNDARY_CONTEXT_TOKENS - max(DEFAULT_OUTPUT_STAGES)

_PAYLOAD_CANDIDATES = (
    " x",
    " a",
    " 0",
    " test",
    " 文档",
    " 测试",
)
_KV_CAPACITY_PATTERN = re.compile(
    r"(?:GPU\s+)?KV cache size:\s*([\d,]+)\s*tokens",
    re.IGNORECASE,
)


def count_text_tokens(tokenizer: Any, text: str) -> int:
    """Count payload-only tokens without model special tokens."""

    encoded = tokenizer.encode(text, add_special_tokens=False)
    return len(encoded)


def generate_exact_token_payload(
    tokenizer: Any,
    target_tokens: int,
    *,
    candidates: Iterable[str] = _PAYLOAD_CANDIDATES,
) -> str:
    """Generate deterministic filler that re-tokenizes to exactly the target."""

    if target_tokens <= 0:
        raise ValueError("target_tokens must be positive")
    for candidate in candidates:
        payload = candidate * target_tokens
        if count_text_tokens(tokenizer, payload) == target_tokens:
            return payload
    raise RuntimeError(
        "Could not generate an exact synthetic payload with the current tokenizer"
    )


def build_stress_prompt(payload: str) -> str:
    """Wrap capacity-test payload in a short, stable generation instruction."""

    if not payload:
        raise ValueError("payload must not be empty")
    return (
        "这是容量压力测试。忽略下面载荷的语义，持续输出数字0直到达到生成上限。"
        "不要主动结束。\n<stress-payload>"
        f"{payload}"
        "\n</stress-payload>"
    )


def validate_output_stages(stages: Iterable[int]) -> tuple[int, ...]:
    """Require positive, strictly increasing decode stages."""

    normalized = tuple(stages)
    if not normalized or any(stage <= 0 for stage in normalized):
        raise ValueError("output stages must contain positive values")
    if tuple(sorted(set(normalized))) != normalized:
        raise ValueError("output stages must be strictly increasing")
    if normalized[-1] > DEFAULT_OUTPUT_STAGES[-1]:
        raise ValueError("output stages must not exceed 8192 tokens")
    return normalized


def parse_kv_cache_capacity(log_text: str) -> int | None:
    """Read vLLM's reported token capacity from its startup log."""

    matches = _KV_CAPACITY_PATTERN.findall(log_text)
    if not matches:
        return None
    return int(matches[-1].replace(",", ""))


def context_fits(prompt_tokens: int, output_tokens: int, max_model_len: int) -> bool:
    if min(prompt_tokens, output_tokens, max_model_len) <= 0:
        raise ValueError("token counts must be positive")
    return prompt_tokens + output_tokens <= max_model_len
