#!/usr/bin/env python3
"""Run staged Qwen3.5 image, long-prompt, and forced-decode stress tests."""

from __future__ import annotations

import argparse
import json
import time
import traceback
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from capture2doc.config import QWEN35_MODEL_REVISION, Qwen35Settings
from capture2doc.inference.device import detect_cuda
from capture2doc.inference.gpu_memory import GpuMemorySampler
from capture2doc.inference.model_store import resolve_prepared_model
from capture2doc.inference.qwen35 import analyze_image, validate_prompt_budget
from capture2doc.inference.qwen35_stress import (
    BOUNDARY_CONTEXT_TOKENS,
    BOUNDARY_MAX_PROMPT_TOKENS,
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_OUTPUT_STAGES,
    STRESS_KV_CACHE_MEMORY_BYTES,
    build_stress_prompt,
    context_fits,
    count_text_tokens,
    generate_exact_token_payload,
    parse_kv_cache_capacity,
    validate_output_stages,
)
from capture2doc.inference.qwen35_tokens import (
    Qwen35TokenInspection,
    inspect_qwen35_tokens,
    load_qwen35_processor,
)
from capture2doc.inference.runtime import VllmRuntime

CASE_4K = "4k-default"
CASE_8K_REJECTION = "8k-rejection"
CASE_8K_BOUNDARY = "8k-boundary"
ALL_CASES = (CASE_4K, CASE_8K_REJECTION, CASE_8K_BOUNDARY)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Absolute path to a PNG/JPEG image")
    parser.add_argument("--cache-dir", help="ModelScope cache directory")
    parser.add_argument("--revision", default=QWEN35_MODEL_REVISION)
    parser.add_argument("--output-dir", help="Directory for incremental stress artifacts")
    parser.add_argument("--host", help="Worker bind and client connect host")
    parser.add_argument("--port", type=int, help="Worker port")
    parser.add_argument(
        "--case",
        choices=("all", *ALL_CASES),
        default="all",
        help="Run the complete matrix or one case",
    )
    parser.add_argument(
        "--output-stages",
        type=int,
        nargs="+",
        default=list(DEFAULT_OUTPUT_STAGES),
        metavar="TOKENS",
    )
    parser.add_argument("--request-timeout-seconds", type=float, default=1200.0)
    return parser.parse_args(argv)


def default_output_dir() -> Path:
    server_root = Path(__file__).resolve().parents[1]
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return server_root / ".cache" / "qwen35-stress" / run_id


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def error_record(exc: BaseException) -> dict[str, str]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(exc)),
    }


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


def read_log_capacity(log_path: Path) -> int | None:
    if not log_path.is_file():
        return None
    return parse_kv_cache_capacity(
        log_path.read_text(encoding="utf-8", errors="replace")
    )


def inspect_case(
    image_path: Path,
    prompt: str,
    settings: Qwen35Settings,
    model_path: Path,
    processor: Any,
) -> Qwen35TokenInspection:
    return inspect_qwen35_tokens(
        image_path,
        prompt,
        settings,
        model_path,
        enable_thinking=False,
        processor=processor,
    )


def write_inspection_artifacts(
    output_dir: Path,
    name: str,
    inspection: Qwen35TokenInspection,
) -> None:
    case_dir = output_dir / name
    write_json(case_dir / "token-summary.json", inspection.to_dict())
    (case_dir / "rendered-prompt.txt").write_text(
        inspection.rendered_prompt,
        encoding="utf-8",
    )


def run_worker_case(
    *,
    name: str,
    image_path: Path,
    prompt: str,
    payload_tokens: int,
    inspection: Qwen35TokenInspection,
    settings: Qwen35Settings,
    model_path: Path,
    output_dir: Path,
    output_stages: tuple[int, ...],
    request_timeout_seconds: float,
    minimum_kv_capacity: int,
) -> dict[str, Any]:
    case_dir = output_dir / name
    case_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "vllm.log"
    case: dict[str, Any] = {
        "name": name,
        "status": "starting",
        "payload_text_tokens": payload_tokens,
        "token_inspection": inspection.to_dict(),
        "settings": {
            "max_model_len": settings.max_model_len,
            "kv_cache_memory_bytes": settings.kv_cache_memory_bytes,
            "max_num_batched_tokens": settings.max_num_batched_tokens,
            "max_num_seqs": settings.max_num_seqs,
        },
        "minimum_kv_capacity_tokens": minimum_kv_capacity,
        "stages": [],
    }
    write_json(case_dir / "case-summary.json", case)

    runtime = VllmRuntime(settings, model_path, log_path)
    sampler = GpuMemorySampler()
    baseline = sampler.snapshot("baseline")
    sampler.start()
    try:
        started_at = time.perf_counter()
        runtime.start()
        runtime.wait_ready()
        case["load_seconds"] = round(time.perf_counter() - started_at, 3)
        sampler.snapshot("idle_loaded")
        case["models_response"] = runtime.fetch_models()
        capacity = read_log_capacity(log_path)
        case["reported_kv_capacity_tokens"] = capacity
        if capacity is None or capacity < minimum_kv_capacity:
            case["status"] = "skipped_capacity"
            case["error"] = {
                "type": "InsufficientKvCapacity",
                "message": (
                    f"vLLM reported {capacity!r} KV tokens; "
                    f"at least {minimum_kv_capacity} are required"
                ),
            }
            write_json(case_dir / "case-summary.json", case)
            return case

        case["status"] = "running"
        write_json(case_dir / "case-summary.json", case)
        for output_tokens in output_stages:
            stage_dir = case_dir / f"output-{output_tokens}"
            stage_dir.mkdir(parents=True, exist_ok=True)
            stage: dict[str, Any] = {
                "requested_output_tokens": output_tokens,
                "status": "running",
            }
            case["stages"].append(stage)
            write_json(case_dir / "case-summary.json", case)
            try:
                validate_prompt_budget(
                    inspection.prompt_tokens,
                    settings,
                    max_tokens=output_tokens,
                )
                before = sampler.snapshot(f"{name}_{output_tokens}_before")
                window_start = sampler.begin_window()
                inference_started_at = time.perf_counter()
                result = analyze_image(
                    image_path,
                    prompt,
                    settings,
                    prompt_tokens=inspection.prompt_tokens,
                    enable_thinking=False,
                    max_tokens=output_tokens,
                    ignore_eos=True,
                    request_timeout_seconds=request_timeout_seconds,
                )
                inference_seconds = time.perf_counter() - inference_started_at
                after = sampler.snapshot(f"{name}_{output_tokens}_after")
                usage = result.raw_response.get("usage") or {}
                actual_completion_tokens = usage.get("completion_tokens")
                finish_reason = result.raw_response.get("choices", [{}])[0].get(
                    "finish_reason"
                )
                stage.update(
                    {
                        "status": "passed"
                        if actual_completion_tokens == output_tokens
                        else "failed_short_output",
                        "actual_prompt_tokens": usage.get("prompt_tokens"),
                        "actual_completion_tokens": actual_completion_tokens,
                        "finish_reason": finish_reason,
                        "inference_seconds": round(inference_seconds, 3),
                        "completion_tokens_per_second": (
                            round(actual_completion_tokens / inference_seconds, 3)
                            if isinstance(actual_completion_tokens, int)
                            else None
                        ),
                        "memory_before_mib": before.used_mib,
                        "memory_after_mib": after.used_mib,
                        "gpu_memory": sampler.window_summary(window_start),
                        "response_characters": len(result.content),
                    }
                )
                write_json(stage_dir / "response.json", result.raw_response)
                write_json(stage_dir / "stage-summary.json", stage)
                runtime.wait_ready(timeout_seconds=10.0)
                if stage["status"] != "passed":
                    case["status"] = "failed"
                    break
            except Exception as exc:
                stage["status"] = "failed"
                stage["error"] = error_record(exc)
                write_json(stage_dir / "stage-summary.json", stage)
                case["status"] = "failed"
                break
            finally:
                write_json(case_dir / "case-summary.json", case)
        else:
            case["status"] = "passed"
    except Exception as exc:
        case["status"] = "failed"
        case["error"] = error_record(exc)
    finally:
        runtime.stop()
        wait_for_memory_recovery(sampler, baseline.used_mib)
        sampler.stop()
        case["gpu_memory"] = sampler.summary()
        write_json(case_dir / "case-summary.json", case)
    return case


def main() -> int:
    args = parse_args()
    output_stages = validate_output_stages(args.output_stages)
    if args.request_timeout_seconds <= 0:
        raise ValueError("request timeout must be positive")
    selected_cases = ALL_CASES if args.case == "all" else (args.case,)
    image_path = Path(args.image).expanduser().resolve()
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else default_output_dir()
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    base_settings = Qwen35Settings.from_sources(args.cache_dir, revision=args.revision)
    base_settings = replace(
        base_settings,
        host=args.host or base_settings.host,
        port=args.port or base_settings.port,
        kv_cache_memory_bytes=STRESS_KV_CACHE_MEMORY_BYTES,
        gpu_memory_utilization=None,
    )
    device = detect_cuda()
    model_path = resolve_prepared_model(base_settings)
    processor = load_qwen35_processor(model_path)
    generated_dir = output_dir / "generated-inputs"
    generated_dir.mkdir(parents=True, exist_ok=True)

    payloads: dict[int, str] = {}
    prompts: dict[int, str] = {}
    for target in (4_096, 8_192):
        payload = generate_exact_token_payload(processor.tokenizer, target)
        actual = count_text_tokens(processor.tokenizer, payload)
        if actual != target:
            raise RuntimeError(f"Generated {actual} tokens instead of {target}")
        prompt = build_stress_prompt(payload)
        payloads[target] = payload
        prompts[target] = prompt
        (generated_dir / f"payload-{target}.txt").write_text(payload, encoding="utf-8")
        (generated_dir / f"prompt-{target}.txt").write_text(prompt, encoding="utf-8")

    settings_16k = replace(
        base_settings,
        max_model_len=DEFAULT_CONTEXT_TOKENS,
        max_output_tokens=DEFAULT_OUTPUT_STAGES[-1],
    )
    settings_boundary = replace(
        base_settings,
        max_model_len=BOUNDARY_CONTEXT_TOKENS,
        max_output_tokens=DEFAULT_OUTPUT_STAGES[-1],
    )
    inspection_4k = inspect_case(
        image_path, prompts[4_096], settings_16k, model_path, processor
    )
    inspection_8k_16k = inspect_case(
        image_path, prompts[8_192], settings_16k, model_path, processor
    )
    inspection_8k_boundary = inspect_case(
        image_path, prompts[8_192], settings_boundary, model_path, processor
    )

    suite: dict[str, Any] = {
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "model_id": base_settings.model_id,
        "revision": base_settings.revision,
        "model_path": str(model_path),
        "image_path": str(image_path),
        "device": device.to_dict(),
        "selected_cases": list(selected_cases),
        "output_stages": list(output_stages),
        "force_output_length": True,
        "cases": {},
    }
    suite_path = output_dir / "suite-summary.json"
    write_json(suite_path, suite)

    inspections = {
        CASE_4K: inspection_4k,
        CASE_8K_REJECTION: inspection_8k_16k,
        CASE_8K_BOUNDARY: inspection_8k_boundary,
    }
    for name in selected_cases:
        write_inspection_artifacts(output_dir, name, inspections[name])

    if CASE_8K_REJECTION in selected_cases:
        rejection: dict[str, Any] = {
            "name": CASE_8K_REJECTION,
            "payload_text_tokens": 8_192,
            "token_inspection": inspection_8k_16k.to_dict(),
        }
        try:
            validate_prompt_budget(inspection_8k_16k.prompt_tokens, settings_16k)
        except ValueError as exc:
            rejection["status"] = "passed_expected_rejection"
            rejection["error"] = {"type": type(exc).__name__, "message": str(exc)}
        else:
            rejection["status"] = "failed_not_rejected"
        suite["cases"][CASE_8K_REJECTION] = rejection
        write_json(output_dir / CASE_8K_REJECTION / "case-summary.json", rejection)
        write_json(suite_path, suite)

    if CASE_4K in selected_cases:
        suite["cases"][CASE_4K] = run_worker_case(
            name=CASE_4K,
            image_path=image_path,
            prompt=prompts[4_096],
            payload_tokens=4_096,
            inspection=inspection_4k,
            settings=settings_16k,
            model_path=model_path,
            output_dir=output_dir,
            output_stages=output_stages,
            request_timeout_seconds=args.request_timeout_seconds,
            minimum_kv_capacity=DEFAULT_CONTEXT_TOKENS,
        )
        write_json(suite_path, suite)

    if CASE_8K_BOUNDARY in selected_cases:
        default_case_failed = (
            args.case == "all" and suite["cases"][CASE_4K]["status"] != "passed"
        )
        if default_case_failed:
            boundary: dict[str, Any] = {
                "name": CASE_8K_BOUNDARY,
                "status": "skipped_prerequisite",
                "payload_text_tokens": 8_192,
                "reason": "4k-default did not complete all output stages",
                "token_inspection": inspection_8k_boundary.to_dict(),
            }
            write_json(output_dir / CASE_8K_BOUNDARY / "case-summary.json", boundary)
        elif inspection_8k_boundary.prompt_tokens > BOUNDARY_MAX_PROMPT_TOKENS:
            boundary: dict[str, Any] = {
                "name": CASE_8K_BOUNDARY,
                "status": "skipped_prompt_budget",
                "payload_text_tokens": 8_192,
                "maximum_prompt_tokens": BOUNDARY_MAX_PROMPT_TOKENS,
                "token_inspection": inspection_8k_boundary.to_dict(),
            }
            write_json(output_dir / CASE_8K_BOUNDARY / "case-summary.json", boundary)
        else:
            boundary = run_worker_case(
                name=CASE_8K_BOUNDARY,
                image_path=image_path,
                prompt=prompts[8_192],
                payload_tokens=8_192,
                inspection=inspection_8k_boundary,
                settings=settings_boundary,
                model_path=model_path,
                output_dir=output_dir,
                output_stages=output_stages,
                request_timeout_seconds=args.request_timeout_seconds,
                minimum_kv_capacity=BOUNDARY_CONTEXT_TOKENS,
            )
        suite["cases"][CASE_8K_BOUNDARY] = boundary
        write_json(suite_path, suite)

    required_results = []
    if CASE_4K in selected_cases:
        required_results.append(suite["cases"][CASE_4K]["status"] == "passed")
    if CASE_8K_REJECTION in selected_cases:
        required_results.append(
            suite["cases"][CASE_8K_REJECTION]["status"]
            == "passed_expected_rejection"
        )
    if CASE_8K_BOUNDARY in selected_cases:
        required_results.append(
            suite["cases"][CASE_8K_BOUNDARY]["status"]
            in {
                "passed",
                "failed",
                "skipped_capacity",
                "skipped_prompt_budget",
                "skipped_prerequisite",
            }
        )
    suite["status"] = "completed" if all(required_results) else "failed"
    suite["completed_at"] = datetime.now(UTC).isoformat()
    write_json(suite_path, suite)

    print(f"4K full prompt tokens: {inspection_4k.prompt_tokens}")
    print(f"8K full prompt tokens: {inspection_8k_boundary.prompt_tokens}")
    print(f"8K + 8K fits 16K: {context_fits(inspection_8k_16k.prompt_tokens, 8192, 16384)}")
    print(f"Artifacts: {output_dir}")
    return 0 if suite["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
