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
