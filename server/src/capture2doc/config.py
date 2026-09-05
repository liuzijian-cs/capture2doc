"""Configuration shared by the local inference workers and scripts."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODEL_ID = "PaddlePaddle/PaddleOCR-VL-1.6"
MODEL_REVISION = "master"
SERVED_MODEL_NAME = "PaddleOCR-VL-1.6"
QWEN35_MODEL_ID = "Qwen/Qwen3.5-9B"
QWEN35_MODEL_REVISION = "master"
QWEN35_SERVED_MODEL_NAME = "Qwen3.5-9B"
MODELSCOPE_CACHE_ENV = "MODELSCOPE_CACHE"

DEFAULT_MAX_PIXELS = 1_003_520
DEFAULT_MAX_OUTPUT_TOKENS = 4_096
DEFAULT_MAX_MODEL_LEN = 8_192
DEFAULT_MAX_NUM_BATCHED_TOKENS = 4_096
DEFAULT_KV_CACHE_MEMORY_BYTES = 256 * 1024**2

QWEN35_MAX_PIXELS = 1_280 * 32**2
QWEN35_MAX_IMAGE_TOKENS = 1_280
QWEN35_EXPECTED_TEXT_TOKENS = 4_096
QWEN35_TEMPLATE_TOKEN_MARGIN = 512
QWEN35_MAX_OUTPUT_TOKENS = 8_192
QWEN35_MAX_MODEL_LEN = 16_384
QWEN35_MAX_NUM_BATCHED_TOKENS = 4_096
QWEN35_KV_CACHE_MEMORY_BYTES = 1024**3


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
class VllmWorkerSettings:
    """Model-neutral settings consumed by the standalone vLLM runtime."""

    cache_dir: Path
    model_id: str
    revision: str
    served_model_name: str
    host: str
    port: int
    dtype: str
    max_pixels: int
    max_output_tokens: int
    max_model_len: int
    max_num_batched_tokens: int
    max_num_seqs: int
    kv_cache_memory_bytes: int | None
    gpu_memory_utilization: float | None
    startup_timeout_seconds: float
    shutdown_timeout_seconds: float
    quantization: str | None = None
    reasoning_parser: str | None = None
    enable_thinking_by_default: bool | None = None
    enable_chunked_prefill: bool = False
    trust_remote_code: bool = False
    enable_prefix_caching: bool = False
    mm_processor_cache_gb: float = 0
    max_images_per_prompt: int = 1
    prepare_script_name: str = "prepare_model.py"

    def __post_init__(self) -> None:
        positive_values = {
            "port": self.port,
            "max_pixels": self.max_pixels,
            "max_output_tokens": self.max_output_tokens,
            "max_model_len": self.max_model_len,
            "max_num_batched_tokens": self.max_num_batched_tokens,
            "max_num_seqs": self.max_num_seqs,
            "max_images_per_prompt": self.max_images_per_prompt,
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
        if self.mm_processor_cache_gb < 0:
            raise ValueError("mm_processor_cache_gb must be non-negative")

    @classmethod
    def from_sources(
        cls,
        cache_dir: str | Path | None = None,
        *,
        revision: str | None = None,
    ) -> VllmWorkerSettings:
        values: dict[str, Any] = {"cache_dir": resolve_cache_dir(cache_dir)}
        if revision is not None:
            values["revision"] = revision
        return cls(**values)

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def api_base_url(self) -> str:
        return f"{self.origin}/v1"

    @property
    def mm_processor_kwargs(self) -> dict[str, int]:
        return {"max_pixels": self.max_pixels}

    @property
    def limit_mm_per_prompt(self) -> dict[str, int]:
        return {"image": self.max_images_per_prompt}

    @property
    def default_chat_template_kwargs(self) -> dict[str, bool] | None:
        if self.enable_thinking_by_default is None:
            return None
        return {"enable_thinking": self.enable_thinking_by_default}


@dataclass(frozen=True, slots=True)
class PaddleOcrVlSettings(VllmWorkerSettings):
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
    trust_remote_code: bool = True
    prepare_script_name: str = "prepare_paddleocr_vl.py"


@dataclass(frozen=True, slots=True)
class Qwen35Settings(VllmWorkerSettings):
    """Settings for the standalone Qwen3.5-9B FP8 validation worker."""

    cache_dir: Path
    model_id: str = QWEN35_MODEL_ID
    revision: str = QWEN35_MODEL_REVISION
    served_model_name: str = QWEN35_SERVED_MODEL_NAME
    host: str = "127.0.0.1"
    port: int = 8119
    dtype: str = "bfloat16"
    max_pixels: int = QWEN35_MAX_PIXELS
    max_output_tokens: int = QWEN35_MAX_OUTPUT_TOKENS
    max_model_len: int = QWEN35_MAX_MODEL_LEN
    max_num_batched_tokens: int = QWEN35_MAX_NUM_BATCHED_TOKENS
    max_num_seqs: int = 1
    kv_cache_memory_bytes: int | None = QWEN35_KV_CACHE_MEMORY_BYTES
    gpu_memory_utilization: float | None = None
    startup_timeout_seconds: float = 900.0
    shutdown_timeout_seconds: float = 30.0
    quantization: str | None = "fp8_per_channel"
    reasoning_parser: str | None = "qwen3"
    enable_thinking_by_default: bool | None = False
    enable_chunked_prefill: bool = True
    prepare_script_name: str = "prepare_qwen35.py"
