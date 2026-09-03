from __future__ import annotations

from pathlib import Path

import pytest

from capture2doc.config import PaddleOcrVlSettings, resolve_cache_dir
from capture2doc.inference.model_store import (
    ModelNotPreparedError,
    prepare_model,
    resolve_prepared_model,
)


def test_cache_dir_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    assert resolve_cache_dir(tmp_path / "cli", {"MODELSCOPE_CACHE": "/env"}) == (
        tmp_path / "cli"
    ).resolve()
    assert resolve_cache_dir(None, {"MODELSCOPE_CACHE": str(tmp_path / "env")}) == (
        tmp_path / "env"
    ).resolve()
    assert resolve_cache_dir(None, {}) == (tmp_path / "home/models/modelscope").resolve()


def test_prepare_model_uses_configured_revision_and_cache(tmp_path: Path) -> None:
    settings = PaddleOcrVlSettings(cache_dir=tmp_path / "cache")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    calls: list[tuple[str, dict[str, object]]] = []

    def downloader(model_id: str, **kwargs: object) -> str:
        calls.append((model_id, kwargs))
        return str(snapshot)

    assert prepare_model(settings, snapshot_download_fn=downloader) == snapshot.resolve()
    assert calls == [
        (
            settings.model_id,
            {"revision": "master", "cache_dir": str(settings.cache_dir)},
        )
    ]


def test_resolve_prepared_model_is_offline(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    snapshot = tmp_path / "snapshot"
    cache.mkdir()
    snapshot.mkdir()
    settings = PaddleOcrVlSettings(cache_dir=cache)
    kwargs_seen: dict[str, object] = {}

    def downloader(_model_id: str, **kwargs: object) -> str:
        kwargs_seen.update(kwargs)
        return str(snapshot)

    assert resolve_prepared_model(settings, snapshot_download_fn=downloader) == snapshot.resolve()
    assert kwargs_seen["local_files_only"] is True


def test_resolve_prepared_model_explains_missing_cache(tmp_path: Path) -> None:
    settings = PaddleOcrVlSettings(cache_dir=tmp_path / "missing")

    with pytest.raises(ModelNotPreparedError, match="prepare_paddleocr_vl.py"):
        resolve_prepared_model(settings, snapshot_download_fn=lambda *_args, **_kwargs: "")
