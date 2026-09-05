from __future__ import annotations

from pathlib import Path

import pytest

from capture2doc.config import (
    DEFAULT_KV_CACHE_MEMORY_BYTES,
    DEFAULT_MAX_MODEL_LEN,
    DEFAULT_MAX_NUM_BATCHED_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_PIXELS,
    PaddleOcrVlSettings,
    QWEN35_EXPECTED_TEXT_TOKENS,
    QWEN35_KV_CACHE_MEMORY_BYTES,
    QWEN35_MAX_IMAGE_TOKENS,
    QWEN35_MAX_MODEL_LEN,
    QWEN35_MAX_OUTPUT_TOKENS,
    QWEN35_MAX_PIXELS,
    QWEN35_TEMPLATE_TOKEN_MARGIN,
    Qwen35Settings,
)


def test_single_image_defaults_fit_the_context_budget(tmp_path: Path) -> None:
    settings = PaddleOcrVlSettings(cache_dir=tmp_path)

    assert settings.max_pixels == DEFAULT_MAX_PIXELS == 1_003_520
    assert settings.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS == 4_096
    assert settings.max_model_len == DEFAULT_MAX_MODEL_LEN == 8_192
    assert settings.max_num_batched_tokens == DEFAULT_MAX_NUM_BATCHED_TOKENS == 4_096
    assert settings.max_num_seqs == 1
    assert settings.kv_cache_memory_bytes == DEFAULT_KV_CACHE_MEMORY_BYTES == 256 * 1024**2
    assert settings.gpu_memory_utilization is None

    maximum_image_tokens = settings.max_pixels // 28**2
    assert maximum_image_tokens + 13 + settings.max_output_tokens == 5_389
    assert 5_389 < settings.max_model_len


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_pixels": 0}, "max_pixels must be positive"),
        ({"kv_cache_memory_bytes": 0}, "kv_cache_memory_bytes must be positive"),
        ({"gpu_memory_utilization": 0.0, "kv_cache_memory_bytes": None}, "interval"),
        ({"gpu_memory_utilization": 0.2}, "configure exactly one"),
        ({"kv_cache_memory_bytes": None}, "configure exactly one"),
    ],
)
def test_settings_reject_invalid_resource_configuration(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        PaddleOcrVlSettings(cache_dir=tmp_path, **overrides)  # type: ignore[arg-type]


def test_settings_allow_gpu_utilization_instead_of_fixed_cache(tmp_path: Path) -> None:
    settings = PaddleOcrVlSettings(
        cache_dir=tmp_path,
        kv_cache_memory_bytes=None,
        gpu_memory_utilization=0.2,
    )

    assert settings.kv_cache_memory_bytes is None
    assert settings.gpu_memory_utilization == 0.2


def test_qwen35_defaults_reserve_eight_thousand_output_tokens(tmp_path: Path) -> None:
    settings = Qwen35Settings(cache_dir=tmp_path)

    assert settings.max_pixels == QWEN35_MAX_PIXELS == 1_310_720
    assert QWEN35_MAX_PIXELS // 32**2 == QWEN35_MAX_IMAGE_TOKENS == 1_280
    assert settings.max_output_tokens == QWEN35_MAX_OUTPUT_TOKENS == 8_192
    assert settings.max_model_len == QWEN35_MAX_MODEL_LEN == 16_384
    assert settings.kv_cache_memory_bytes == QWEN35_KV_CACHE_MEMORY_BYTES == 1024**3
    assert settings.quantization == "fp8_per_channel"
    assert settings.enable_thinking_by_default is False

    planned_tokens = (
        QWEN35_MAX_IMAGE_TOKENS
        + QWEN35_EXPECTED_TEXT_TOKENS
        + QWEN35_TEMPLATE_TOKEN_MARGIN
        + QWEN35_MAX_OUTPUT_TOKENS
    )
    assert planned_tokens == 14_080
    assert planned_tokens < settings.max_model_len
