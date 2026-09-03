"""Local accelerator discovery for inference backends."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class InferenceBackend(StrEnum):
    CUDA_VLLM = "cuda-vllm"
    APPLE_MLX = "apple-mlx"  # Reserved for the future macOS implementation.


class CudaUnavailableError(RuntimeError):
    """Raised when the CUDA-only worker cannot be started."""


@dataclass(frozen=True, slots=True)
class CudaDeviceInfo:
    index: int
    name: str
    compute_capability: tuple[int, int]
    total_memory_bytes: int
    free_memory_bytes: int | None
    torch_version: str
    cuda_version: str | None

    @property
    def total_memory_gib(self) -> float:
        return self.total_memory_bytes / 1024**3

    @property
    def free_memory_gib(self) -> float | None:
        if self.free_memory_bytes is None:
            return None
        return self.free_memory_bytes / 1024**3

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["compute_capability"] = list(self.compute_capability)
        value["total_memory_gib"] = round(self.total_memory_gib, 3)
        value["free_memory_gib"] = (
            None if self.free_memory_gib is None else round(self.free_memory_gib, 3)
        )
        return value


def _import_torch() -> Any:
    try:
        import torch
    except ImportError as exc:
        raise CudaUnavailableError(
            "PyTorch is not installed. On NVIDIA/WSL run `uv sync --extra cuda`."
        ) from exc
    return torch


def detect_cuda(torch_module: Any | None = None, *, device_index: int = 0) -> CudaDeviceInfo:
    """Return CUDA device details or fail with an actionable error."""

    torch = torch_module or _import_torch()
    cuda = torch.cuda
    if not cuda.is_available() or cuda.device_count() <= device_index:
        raise CudaUnavailableError(
            "CUDA is unavailable. PaddleOCR-VL currently requires an NVIDIA CUDA GPU; "
            "the MLX backend is not implemented yet."
        )

    properties = cuda.get_device_properties(device_index)
    capability = (
        int(getattr(properties, "major")),
        int(getattr(properties, "minor")),
    )

    free_memory: int | None = None
    total_memory = int(properties.total_memory)
    try:
        free_memory_raw, total_memory_raw = cuda.mem_get_info(device_index)
        free_memory = int(free_memory_raw)
        total_memory = int(total_memory_raw)
    except (AttributeError, RuntimeError):
        pass

    return CudaDeviceInfo(
        index=device_index,
        name=str(properties.name),
        compute_capability=capability,
        total_memory_bytes=total_memory,
        free_memory_bytes=free_memory,
        torch_version=str(torch.__version__),
        cuda_version=getattr(torch.version, "cuda", None),
    )


def require_cuda() -> CudaDeviceInfo:
    return detect_cuda()


def is_wsl2(osrelease: str | None = None) -> bool:
    """Return whether the current Linux kernel identifies itself as WSL2."""

    if osrelease is None:
        try:
            osrelease = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
        except OSError:
            return False
    normalized = osrelease.lower()
    return "microsoft" in normalized and "wsl2" in normalized
