#!/usr/bin/env python3
"""Download the fixed PaddleOCR-VL snapshot with ModelScope."""

from __future__ import annotations

import argparse

from capture2doc.config import MODEL_REVISION, PaddleOcrVlSettings
from capture2doc.inference.model_store import prepare_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", help="ModelScope cache directory")
    parser.add_argument("--revision", default=MODEL_REVISION, help="Model revision")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = PaddleOcrVlSettings.from_sources(args.cache_dir, revision=args.revision)
    print(f"ModelScope cache: {settings.cache_dir}")
    print(f"Preparing model: {settings.model_id}")
    print(f"Revision: {settings.revision}")
    snapshot_path = prepare_model(settings)
    print(f"Snapshot path: {snapshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
