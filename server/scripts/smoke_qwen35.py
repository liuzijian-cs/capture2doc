#!/usr/bin/env python3
"""Start Qwen3.5-9B FP8 and run one preflighted image-and-text request."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from capture2doc.config import QWEN35_MODEL_REVISION, Qwen35Settings
from capture2doc.inference.device import detect_cuda
from capture2doc.inference.gpu_memory import GpuMemorySampler
from capture2doc.inference.model_store import resolve_prepared_model
from capture2doc.inference.qwen35 import (
    DEFAULT_DOCUMENT_PROMPT,
    analyze_image,
    validate_prompt_budget,
)
from capture2doc.inference.qwen35_tokens import inspect_qwen35_tokens
from capture2doc.inference.runtime import VllmRuntime


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Absolute path to a PNG/JPEG image")
    parser.add_argument("--prompt", default=DEFAULT_DOCUMENT_PROMPT, help="Text after the image")
    parser.add_argument("--cache-dir", help="ModelScope cache directory")
    parser.add_argument("--revision", default=QWEN35_MODEL_REVISION, help="Model revision")
    parser.add_argument("--output-dir", help="Directory for response, tokens, timings, and logs")
    parser.add_argument("--host", help="Worker bind and client connect host")
    parser.add_argument("--port", type=int, help="Worker port")
    parser.add_argument("--max-pixels", type=int, help="Maximum image pixels")
    parser.add_argument("--max-model-len", type=int, help="Prompt and output context limit")
    parser.add_argument(
        "--max-num-batched-tokens",
        type=int,
        help="Maximum tokens handled in one scheduler iteration",
    )
    parser.add_argument("--max-tokens", type=int, help="Maximum generated tokens")
    parser.add_argument("--enable-thinking", action="store_true")
    memory_group = parser.add_mutually_exclusive_group()
    memory_group.add_argument(
        "--kv-cache-memory-bytes",
        type=int,
        help="Fixed KV cache allocation in bytes",
    )
    memory_group.add_argument(
        "--gpu-memory-utilization",
        type=float,
        help="Use proportional GPU allocation instead of fixed KV cache bytes",
    )
    return parser.parse_args(argv)


def settings_from_args(args: argparse.Namespace) -> Qwen35Settings:
    settings = Qwen35Settings.from_sources(args.cache_dir, revision=args.revision)
    overrides = {
        "host": args.host,
        "port": args.port,
        "max_pixels": args.max_pixels,
        "max_model_len": args.max_model_len,
        "max_num_batched_tokens": args.max_num_batched_tokens,
        "max_output_tokens": args.max_tokens,
        "kv_cache_memory_bytes": args.kv_cache_memory_bytes,
    }
    selected = {name: value for name, value in overrides.items() if value is not None}
    if args.gpu_memory_utilization is not None:
        selected["kv_cache_memory_bytes"] = None
        selected["gpu_memory_utilization"] = args.gpu_memory_utilization
    return replace(settings, **selected)


def default_output_dir() -> Path:
    server_root = Path(__file__).resolve().parents[1]
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return server_root / ".cache" / "qwen35-smoke" / run_id


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def wait_for_memory_recovery(
    sampler: GpuMemorySampler,
    baseline_used_mib: int,
    *,
    timeout_seconds: float = 10.0,
    tolerance_mib: int = 128,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        reading = sampler.snapshot("after_stop")
        if reading.used_mib <= baseline_used_mib + tolerance_mib:
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(0.5)


def main() -> int:
    args = parse_args()
    image_path = Path(args.image).expanduser().resolve()
    output_dir = (
        Path(args.output_dir).expanduser().resolve() if args.output_dir else default_output_dir()
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = settings_from_args(args)
    print(f"ModelScope cache: {settings.cache_dir}")
    print(f"Model: {settings.model_id}@{settings.revision}")
    print(f"Quantization: {settings.quantization}")

    device_before = detect_cuda()
    print(
        f"CUDA device: {device_before.name}, compute capability "
        f"{device_before.compute_capability[0]}.{device_before.compute_capability[1]}, "
        f"{device_before.total_memory_gib:.2f} GiB"
    )
    model_path = resolve_prepared_model(settings)
    print(f"Local snapshot: {model_path}")

    inspection = inspect_qwen35_tokens(
        image_path,
        args.prompt,
        settings,
        model_path,
        enable_thinking=args.enable_thinking,
    )
    validate_prompt_budget(inspection.prompt_tokens, settings)
    write_json(output_dir / "token-summary.json", inspection.to_dict())
    (output_dir / "rendered-prompt.txt").write_text(
        inspection.rendered_prompt,
        encoding="utf-8",
    )

    runtime = VllmRuntime(settings, model_path, output_dir / "vllm.log")
    sampler = GpuMemorySampler()
    baseline = sampler.snapshot("baseline")
    sampler.start()
    try:
        started_at = time.perf_counter()
        runtime.start()
        try:
            runtime.wait_ready()
            load_seconds = time.perf_counter() - started_at
            sampler.snapshot("idle_loaded")
            models_response = runtime.fetch_models()

            inference_started_at = time.perf_counter()
            result = analyze_image(
                image_path,
                args.prompt,
                settings,
                prompt_tokens=inspection.prompt_tokens,
                enable_thinking=args.enable_thinking,
            )
            inference_seconds = time.perf_counter() - inference_started_at
            sampler.snapshot("post_request")
            write_json(output_dir / "response.json", result.raw_response)
        finally:
            runtime.stop()
        wait_for_memory_recovery(sampler, baseline.used_mib)
    finally:
        sampler.stop()

    device_after = detect_cuda()
    summary = {
        "model_id": settings.model_id,
        "revision": settings.revision,
        "model_path": str(model_path),
        "image_path": str(image_path),
        "prompt": args.prompt,
        "enable_thinking": args.enable_thinking,
        "endpoint": settings.api_base_url,
        "inference_settings": {
            "dtype": settings.dtype,
            "quantization": settings.quantization,
            "max_pixels": settings.max_pixels,
            "max_output_tokens": settings.max_output_tokens,
            "max_model_len": settings.max_model_len,
            "max_num_batched_tokens": settings.max_num_batched_tokens,
            "max_num_seqs": settings.max_num_seqs,
            "kv_cache_memory_bytes": settings.kv_cache_memory_bytes,
            "gpu_memory_utilization": settings.gpu_memory_utilization,
        },
        "token_inspection": inspection.to_dict(),
        "gpu_memory": sampler.summary(),
        "load_seconds": round(load_seconds, 3),
        "inference_seconds": round(inference_seconds, 3),
        "response_characters": len(result.content),
        "reasoning_characters": 0 if result.reasoning is None else len(result.reasoning),
        "usage": result.raw_response.get("usage"),
        "finish_reason": result.raw_response.get("choices", [{}])[0].get("finish_reason"),
        "device_before": device_before.to_dict(),
        "device_after": device_after.to_dict(),
        "models_response": models_response,
    }
    write_json(output_dir / "summary.json", summary)
    print(f"Image tokens: {inspection.image_tokens}")
    print(f"Full prompt tokens: {inspection.prompt_tokens}")
    print(f"Response: {len(result.content)} characters")
    print(f"Load time: {load_seconds:.3f}s")
    print(f"Inference time: {inference_seconds:.3f}s")
    print(f"Peak GPU memory: {summary['gpu_memory']['peak_memory_mib']} MiB")
    print(f"Artifacts: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
