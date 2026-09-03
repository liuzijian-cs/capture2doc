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
    gpu_memory_utilization: float = 0.5
    max_model_len: int = 16_384
    max_num_batched_tokens: int = 16_384
    max_num_seqs: int = 1
    startup_timeout_seconds: float = 600.0
    shutdown_timeout_seconds: float = 30.0

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
