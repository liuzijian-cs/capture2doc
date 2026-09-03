"""Configuration shared by the local inference worker and scripts."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MODEL_ID = "PaddlePaddle/PaddleOCR-VL-1.6"
MODEL_REVISION = "master"
SERVED_MODEL_NAME = "PaddleOCR-VL-1.6"
MODELSCOPE_CACHE_ENV = "MODELSCOPE_CACHE"
DEFAULT_MAX_PIXELS = 1_003_520
DEFAULT_MAX_OUTPUT_TOKENS = 4_096
DEFAULT_MAX_MODEL_LEN = 8_192
DEFAULT_MAX_NUM_BATCHED_TOKENS = 4_096
DEFAULT_KV_CACHE_MEMORY_BYTES = 256 * 1024**2


def resolve_cache_dir(
    cache_dir: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve cache path using CLI, environment, then project default precedence."""

    environment = os.environ if environ is None else environ
    configured = cache_dir or environment.get(MODELSCOPE_CACHE_ENV)
    path = Path(configured) if configured else Path.home() / "models" / "modelscope"
    return path.expanduser().resolve()


@dataclass(frozen=True, slots=True)
class PaddleOcrVlSettings:
    """First-pass settings for a single local PaddleOCR-VL worker."""

    cache_dir: Path
    model_id: str = MODEL_ID
    revision: str = MODEL_REVISION
    served_model_name: str = SERVED_MODEL_NAME
    host: str = "127.0.0.1"
    port: int = 8118
    dtype: str = "bfloat16"
    max_pixels: int = DEFAULT_MAX_PIXELS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    max_model_len: int = DEFAULT_MAX_MODEL_LEN
    max_num_batched_tokens: int = DEFAULT_MAX_NUM_BATCHED_TOKENS
    max_num_seqs: int = 1
    kv_cache_memory_bytes: int | None = DEFAULT_KV_CACHE_MEMORY_BYTES
    gpu_memory_utilization: float | None = None
    startup_timeout_seconds: float = 600.0
    shutdown_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        positive_values = {
            "port": self.port,
            "max_pixels": self.max_pixels,
            "max_output_tokens": self.max_output_tokens,
            "max_model_len": self.max_model_len,
            "max_num_batched_tokens": self.max_num_batched_tokens,
            "max_num_seqs": self.max_num_seqs,
        }
        for name, value in positive_values.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")

        if self.max_output_tokens >= self.max_model_len:
            raise ValueError("max_output_tokens must be smaller than max_model_len")
        if self.kv_cache_memory_bytes is not None and self.kv_cache_memory_bytes <= 0:
            raise ValueError("kv_cache_memory_bytes must be positive")
        if self.gpu_memory_utilization is not None and not (
            0 < self.gpu_memory_utilization <= 1
        ):
            raise ValueError("gpu_memory_utilization must be in the interval (0, 1]")
        if (self.kv_cache_memory_bytes is None) == (self.gpu_memory_utilization is None):
            raise ValueError(
                "configure exactly one of kv_cache_memory_bytes and gpu_memory_utilization"
            )

    @classmethod
    def from_sources(
        cls,
        cache_dir: str | Path | None = None,
        *,
        revision: str = MODEL_REVISION,
    ) -> "PaddleOcrVlSettings":
        return cls(cache_dir=resolve_cache_dir(cache_dir), revision=revision)

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def api_base_url(self) -> str:
        return f"{self.origin}/v1"
