from __future__ import annotations

from types import SimpleNamespace

import pytest

from capture2doc.inference.device import CudaUnavailableError, detect_cuda


class FakeCuda:
    def __init__(self, *, available: bool) -> None:
        self.available = available

    def is_available(self) -> bool:
        return self.available

    def device_count(self) -> int:
        return 1 if self.available else 0

    def get_device_properties(self, _index: int) -> SimpleNamespace:
        return SimpleNamespace(
            name="Fake RTX",
            major=8,
            minor=9,
            total_memory=16 * 1024**3,
        )

    def mem_get_info(self, _index: int) -> tuple[int, int]:
        return 12 * 1024**3, 16 * 1024**3


def fake_torch(*, available: bool) -> SimpleNamespace:
    return SimpleNamespace(
        cuda=FakeCuda(available=available),
        version=SimpleNamespace(cuda="13.0"),
        __version__="2.13.0+cu130",
    )


def test_detect_cuda_returns_device_details() -> None:
    info = detect_cuda(fake_torch(available=True))

    assert info.name == "Fake RTX"
    assert info.compute_capability == (8, 9)
    assert info.total_memory_gib == 16
    assert info.free_memory_gib == 12


def test_detect_cuda_fails_clearly_when_unavailable() -> None:
    with pytest.raises(CudaUnavailableError, match="CUDA is unavailable"):
        detect_cuda(fake_torch(available=False))
