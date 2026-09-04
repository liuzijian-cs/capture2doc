from __future__ import annotations

import time

from capture2doc.inference.gpu_memory import (
    GpuMemoryReading,
    GpuMemorySampler,
)


def test_sampler_reports_global_peak_and_named_phases() -> None:
    readings = iter(
        [
            GpuMemoryReading(2_000, 14_000, 16_000),
            GpuMemoryReading(11_000, 5_000, 16_000),
            GpuMemoryReading(12_500, 3_500, 16_000),
            GpuMemoryReading(2_050, 13_950, 16_000),
        ]
    )
    sampler = GpuMemorySampler(reader=lambda: next(readings))

    sampler.snapshot("baseline")
    sampler.snapshot("idle_loaded")
    sampler.snapshot("post_request")
    sampler.snapshot("after_stop")
    summary = sampler.summary()

    assert summary["baseline_memory_mib"] == 2_000
    assert summary["idle_loaded_memory_mib"] == 11_000
    assert summary["peak_memory_mib"] == 12_500
    assert summary["available_at_peak_mib"] == 3_500
    assert summary["worker_incremental_peak_mib"] == 10_500
    assert summary["after_stop_memory_mib"] == 2_050


def test_sampler_background_thread_stops_cleanly() -> None:
    reading = GpuMemoryReading(2_000, 14_000, 16_000)
    sampler = GpuMemorySampler(reader=lambda: reading, interval_seconds=0.001)

    sampler.start()
    time.sleep(0.005)
    sampler.stop()

    assert sampler.summary()["sample_count"] > 0
