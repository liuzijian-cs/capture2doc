#!/usr/bin/env python3
"""Assemble ordered local C2D updates into a new XML file without model calls."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile

from capture2doc.formats.c2d_xml import C2DAssembler, C2DAssemblyError


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--updates",
        nargs="+",
        required=True,
        type=Path,
        help="Update XML files in application order",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New output path; parent directory must already exist",
    )
    parser.add_argument("--lang", help="Optional document language, e.g. zh-CN")
    return parser.parse_args(argv)


def _publish(data: bytes, destination: Path) -> None:
    """Publish a complete same-filesystem file exclusively, never replacing one."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Unlike os.replace, link fails if any file or symlink appeared at the
        # destination after preflight. Readers see only the completed contents.
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    round_number: int | None = None
    source: Path | None = None
    try:
        if os.path.lexists(args.output):
            raise FileExistsError(f"Output already exists: {args.output}")
        if not args.output.parent.is_dir():
            raise FileNotFoundError(
                f"Output directory does not exist: {args.output.parent}"
            )
        assembler = C2DAssembler(lang=args.lang)
        for round_number, source in enumerate(args.updates, start=1):
            result = assembler.apply_update(source.read_bytes())
            if not result.valid:
                print(
                    json.dumps(
                        {
                            "file": str(source),
                            "round": round_number,
                            "issues": [asdict(issue) for issue in result.issues],
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                )
                return 1
        round_number, source = None, None
        _publish(assembler.finalize(), args.output)
    except (OSError, ValueError, RuntimeError) as exc:
        error = {
            "file": str(source) if source else str(args.output),
            "round": round_number,
            "error": str(exc),
        }
        if isinstance(exc, C2DAssemblyError):
            error["issues"] = [asdict(issue) for issue in exc.issues]
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 1
    print(f"Assembled {len(args.updates)} updates: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
