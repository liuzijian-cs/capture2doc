"""Low-overhead global NVIDIA memory sampling for smoke diagnostics."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GpuMemoryReading:
    used_mib: int
    free_mib: int
    total_mib: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


MemoryReader = Callable[[], GpuMemoryReading]


def read_nvidia_memory(*, device_index: int = 0) -> GpuMemoryReading:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.free,memory.total",
            "--format=csv,noheader,nounits",
            "-i",
            str(device_index),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10.0,
    )
    first_line = completed.stdout.strip().splitlines()[0]
    values = [int(value.strip()) for value in first_line.split(",")]
    if len(values) != 3:
        raise RuntimeError(f"Unexpected nvidia-smi memory output: {first_line!r}")
    return GpuMemoryReading(*values)


class GpuMemorySampler:
    """Continuously sample global GPU memory while retaining named snapshots."""

    def __init__(
        self,
        *,
        reader: MemoryReader = read_nvidia_memory,
        interval_seconds: float = 0.2,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.reader = reader
        self.interval_seconds = interval_seconds
        self._samples: list[GpuMemoryReading] = []
        self._snapshots: dict[str, GpuMemoryReading] = {}
        self._error: Exception | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("GPU memory sampler is already running")
        self._stop_event.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._sample_loop,
            name="gpu-memory-sampler",
            daemon=True,
        )
        self._thread.start()

    def snapshot(self, name: str) -> GpuMemoryReading:
        reading = self.reader()
        with self._lock:
            self._samples.append(reading)
            self._snapshots[name] = reading
        return reading

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout=max(2.0, self.interval_seconds * 4))
        self._thread = None
        if self._error is not None:
            raise RuntimeError(f"GPU memory sampling failed: {self._error}") from self._error

    def summary(self) -> dict[str, Any]:
        with self._lock:
            samples = list(self._samples)
            snapshots = dict(self._snapshots)
        if not samples:
            raise RuntimeError("GPU memory sampler has no readings")
        peak = max(samples, key=lambda value: value.used_mib)
        baseline = snapshots.get("baseline", samples[0])
        return {
            "baseline_memory_mib": baseline.used_mib,
            "idle_loaded_memory_mib": _used(snapshots.get("idle_loaded")),
            "peak_memory_mib": peak.used_mib,
            "post_request_memory_mib": _used(snapshots.get("post_request")),
            "after_stop_memory_mib": _used(snapshots.get("after_stop")),
            "available_at_peak_mib": peak.total_mib - peak.used_mib,
            "worker_incremental_peak_mib": peak.used_mib - baseline.used_mib,
            "sample_count": len(samples),
            "snapshots": {name: reading.to_dict() for name, reading in snapshots.items()},
        }

    def _sample_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                reading = self.reader()
                with self._lock:
                    self._samples.append(reading)
            except Exception as exc:
                self._error = exc
                self._stop_event.set()
                return
            self._stop_event.wait(self.interval_seconds)

    def __enter__(self) -> GpuMemorySampler:
        self.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.stop()


def _used(reading: GpuMemoryReading | None) -> int | None:
    return None if reading is None else reading.used_mib
