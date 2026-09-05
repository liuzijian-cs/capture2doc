#!/usr/bin/env python3
"""Download the official Qwen3.5-9B snapshot with ModelScope."""

from __future__ import annotations

import argparse
from pathlib import Path

from capture2doc.config import QWEN35_MODEL_REVISION, Qwen35Settings
from capture2doc.inference.model_store import prepare_model
from capture2doc.inference.qwen35_tokens import sha256_if_file

REQUIRED_CONFIG_FILES = (
    "config.json",
    "preprocessor_config.json",
    "chat_template.jinja",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", help="ModelScope cache directory")
    parser.add_argument("--revision", default=QWEN35_MODEL_REVISION, help="Model revision")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = Qwen35Settings.from_sources(args.cache_dir, revision=args.revision)
    print(f"ModelScope cache: {settings.cache_dir}")
    print(f"Preparing model: {settings.model_id}")
    print(f"Revision: {settings.revision}")
    snapshot_path = prepare_model(settings)
    print(f"Snapshot path: {snapshot_path}")

    missing = [name for name in REQUIRED_CONFIG_FILES if not (snapshot_path / name).is_file()]
    if missing:
        raise RuntimeError(f"Prepared snapshot is missing: {', '.join(missing)}")
    for name in REQUIRED_CONFIG_FILES:
        path = Path(snapshot_path) / name
        print(f"{name} SHA-256: {sha256_if_file(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
