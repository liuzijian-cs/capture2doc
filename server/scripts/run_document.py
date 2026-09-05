#!/usr/bin/env python3
"""Run/resume a frozen document through Paddle OCR, Qwen and C2D validation."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from capture2doc.pipeline.models import LocalModels
from capture2doc.pipeline.runner import run_document
from capture2doc.pipeline.store import DocumentStore, exclusive_lock


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--manifest", type=Path, help="JSON document/images/ordered_image_ids"
    )
    source.add_argument(
        "--resume", action="store_true", help="Resume from output-dir/state.json"
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Dedicated document directory"
    )
    parser.add_argument(
        "--cache-dir", help="Prepared ModelScope cache; never downloads models"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Local worker bind/connect host"
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Explicitly grant up to three more attempts to each unfinished OCR/round",
    )
    parser.add_argument(
        "--gpu-lock",
        type=Path,
        default=Path(tempfile.gettempdir()) / "capture2doc-gpu.lock",
        help="All cooperating processes on this GPU must use the same lock file",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    store = DocumentStore(args.output_dir)

    def interrupted(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt("termination requested")

    old_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        with exclusive_lock(store.root / ".document.lock"):
            if args.resume:
                store.load()
            else:
                store.create(args.manifest)
            with exclusive_lock(args.gpu_lock):
                output = run_document(
                    store,
                    LocalModels(cache_dir=args.cache_dir, host=args.host),
                    retry_failed=args.retry_failed,
                    progress=lambda message: print(
                        message, file=sys.stderr, flush=True
                    ),
                )
            print(
                json.dumps(
                    {
                        "document_id": store.state["document_id"],
                        "status": store.state["status"],
                        "output": str(output),
                        "rounds": len(store.state["rounds"]),
                        "semantic_fidelity_verified": False,
                    },
                    ensure_ascii=False,
                )
            )
            return 3 if store.state["status"] == "needs_review" else 0
    except KeyboardInterrupt:
        print("Interrupted; durable checkpoint retained.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(
            json.dumps(
                {
                    "code": getattr(exc, "code", type(exc).__name__),
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        signal.signal(signal.SIGTERM, old_handler)


if __name__ == "__main__":
    raise SystemExit(main())
