#!/usr/bin/env python3
"""Inspect Qwen3.5 image tokens and the rendered prompt without loading weights."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from capture2doc.config import QWEN35_MODEL_REVISION, Qwen35Settings
from capture2doc.inference.model_store import resolve_prepared_model
from capture2doc.inference.qwen35 import DEFAULT_DOCUMENT_PROMPT, validate_prompt_budget
from capture2doc.inference.qwen35_tokens import inspect_qwen35_tokens


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Absolute path to a PNG/JPEG image")
    parser.add_argument("--prompt", default=DEFAULT_DOCUMENT_PROMPT, help="Text after the image")
    parser.add_argument("--cache-dir", help="ModelScope cache directory")
    parser.add_argument("--revision", default=QWEN35_MODEL_REVISION, help="Model revision")
    parser.add_argument("--output-dir", help="Directory for token summary and template")
    parser.add_argument("--max-pixels", type=int, help="Maximum image pixels")
    parser.add_argument("--max-model-len", type=int, help="Prompt and output context limit")
    parser.add_argument("--max-tokens", type=int, help="Reserved output token count")
    parser.add_argument("--enable-thinking", action="store_true")
    return parser.parse_args(argv)


def default_output_dir() -> Path:
    server_root = Path(__file__).resolve().parents[1]
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return server_root / ".cache" / "qwen35-token-inspection" / run_id


def main() -> int:
    from dataclasses import replace

    args = parse_args()
    settings = Qwen35Settings.from_sources(args.cache_dir, revision=args.revision)
    overrides = {
        "max_pixels": args.max_pixels,
        "max_model_len": args.max_model_len,
        "max_output_tokens": args.max_tokens,
    }
    settings = replace(
        settings,
        **{name: value for name, value in overrides.items() if value is not None},
    )
    model_path = resolve_prepared_model(settings)
    inspection = inspect_qwen35_tokens(
        args.image,
        args.prompt,
        settings,
        model_path,
        enable_thinking=args.enable_thinking,
    )
    validate_prompt_budget(inspection.prompt_tokens, settings)

    output_dir = (
        Path(args.output_dir).expanduser().resolve() if args.output_dir else default_output_dir()
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "token-summary.json").write_text(
        json.dumps(inspection.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "rendered-prompt.txt").write_text(
        inspection.rendered_prompt,
        encoding="utf-8",
    )
    print(
        f"Image: {inspection.original_width}x{inspection.original_height} -> "
        f"{inspection.resized_width}x{inspection.resized_height}"
    )
    print(f"image_grid_thw: {list(inspection.image_grid_thw)}")
    print(f"Image tokens: {inspection.image_tokens}")
    print(f"Full prompt tokens: {inspection.prompt_tokens}")
    print(f"Reserved output tokens: {inspection.max_output_tokens}")
    print(f"Remaining context before output: {inspection.remaining_context_tokens}")
    print(f"Template image span: <|image_pad|> x {inspection.placeholder_tokens}")
    print(f"Artifacts: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
