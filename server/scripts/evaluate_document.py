#!/usr/bin/env python3
"""Verify saved V2 pipeline artifacts and optional sampled visual anchor checks."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence

from capture2doc.pipeline.evaluation import evaluate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Completed V2 result directory")
    parser.add_argument(
        "--checks", type=Path, help="JSON array or object with checks array"
    )
    parser.add_argument("--output", type=Path, help="Write report JSON; default stdout")
    args = parser.parse_args(argv)
    try:
        checks = json.loads(args.checks.read_text()) if args.checks else []
        if isinstance(checks, dict):
            checks = checks["checks"]
        if not isinstance(checks, list):
            raise ValueError("--checks must contain an array")
        result = evaluate(args.root, checks=checks)
    except (OSError, ValueError, KeyError, TypeError, re.error) as exc:
        print(
            json.dumps(
                {"verification_passed": False, "error": str(exc)}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 1
    content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    else:
        print(content, end="")
    if not result["verification_passed"]:
        return 1
    return 3 if result["visual_sample"]["all_passed"] is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
