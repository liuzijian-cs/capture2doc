"""Explicit ModelScope preparation and offline snapshot resolution."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from capture2doc.config import MODELSCOPE_CACHE_ENV, PaddleOcrVlSettings

SnapshotDownload = Callable[..., str]


class ModelNotPreparedError(RuntimeError):
    """Raised when an offline worker cannot find a prepared model snapshot."""


def _snapshot_download() -> SnapshotDownload:
    try:
        from modelscope import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "ModelScope is not installed. On NVIDIA/WSL run `uv sync --extra cuda`."
        ) from exc
    return snapshot_download


def prepare_model(
    settings: PaddleOcrVlSettings,
    *,
    snapshot_download_fn: SnapshotDownload | None = None,
) -> Path:
    """Download/update the configured revision and return its concrete snapshot path."""

    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ[MODELSCOPE_CACHE_ENV] = str(settings.cache_dir)
    downloader = snapshot_download_fn or _snapshot_download()
    snapshot = downloader(
        settings.model_id,
        revision=settings.revision,
        cache_dir=str(settings.cache_dir),
    )
    return Path(snapshot).expanduser().resolve()


def resolve_prepared_model(
    settings: PaddleOcrVlSettings,
    *,
    snapshot_download_fn: SnapshotDownload | None = None,
) -> Path:
    """Resolve a cached snapshot without permitting network access."""

    if not settings.cache_dir.is_dir():
        raise ModelNotPreparedError(
            f"ModelScope cache does not exist: {settings.cache_dir}. "
            "Run scripts/prepare_paddleocr_vl.py first."
        )

    os.environ[MODELSCOPE_CACHE_ENV] = str(settings.cache_dir)
    downloader = snapshot_download_fn or _snapshot_download()
    try:
        snapshot = downloader(
            settings.model_id,
            revision=settings.revision,
            cache_dir=str(settings.cache_dir),
            local_files_only=True,
        )
    except Exception as exc:
        raise ModelNotPreparedError(
            f"No local snapshot for {settings.model_id}@{settings.revision} in "
            f"{settings.cache_dir}. Run scripts/prepare_paddleocr_vl.py first."
        ) from exc

    path = Path(snapshot).expanduser().resolve()
    if not path.is_dir():
        raise ModelNotPreparedError(f"Resolved model snapshot is not a directory: {path}")
    return path
