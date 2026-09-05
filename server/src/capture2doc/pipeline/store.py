"""Exclusive document ownership and atomic, replayable checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from capture2doc.inference.image_input import SUPPORTED_IMAGE_TYPES


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    atomic_write(
        path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Cooperating CLI processes must use the same lock path; Linux/WSL/macOS."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Already in use: {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def valid_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value
    ):
        raise ValueError(
            "IDs must be 1-128 ASCII letters, digits, underscores, dots or hyphens"
        )
    return value


class DocumentStore:
    """Caller holds .document.lock throughout reading, model work and writing.

    Successful OCR and the ordered accepted update journal live in one atomic
    state.json. Raw request/response artifacts are written before that checkpoint.
    Uncommitted artifacts are evidence only and are never replayed as updates.
    """

    schema_version = 1

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.state_path = self.root / "state.json"
        self.state: dict[str, Any] = {}

    def load(self) -> None:
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if self.state.get("schema_version") != self.schema_version:
            raise ValueError("Unsupported document checkpoint version")
        self.verify_images()

    def create(self, manifest_path: Path) -> None:
        manifest_path = manifest_path.expanduser().resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        document_id = valid_id(manifest["document_id"])
        images: dict[str, Any] = {}
        assets: dict[str, bytes] = {}
        for item in manifest["images"]:
            image_id = valid_id(item["image_id"])
            if image_id in images:
                raise ValueError(f"Duplicate image_id: {image_id}")
            source = Path(item["path"]).expanduser()
            if not source.is_absolute():
                source = manifest_path.parent / source
            suffix = source.suffix.lower()
            if suffix not in SUPPORTED_IMAGE_TYPES:
                raise ValueError(f"Use PNG or JPEG: {source}")
            data = source.read_bytes()
            if not data:
                raise ValueError(f"Empty image: {source}")
            relative_path = f"images/{image_id}{suffix}"
            images[image_id] = {
                "image_id": image_id,
                "path": relative_path,
                "sha256": digest(data),
                "bytes": len(data),
                "received_at": now(),
                "ocr": None,
                "ocr_attempts": [],
            }
            assets[relative_path] = data
        order = manifest["ordered_image_ids"]
        if (
            not isinstance(order, list)
            or not order
            or any(not isinstance(i, str) for i in order)
            or len(set(order)) != len(order)
            or any(i not in images for i in order)
        ):
            raise ValueError(
                "ordered_image_ids must be nonempty, unique and reference supplied images"
            )
        lang = manifest.get("lang", "zh-CN")
        # Reuse schema language checks before publishing any input.
        from capture2doc.formats.c2d_xml import C2DAssembler

        C2DAssembler(lang=lang)
        if self.state_path.exists():
            self.load()

            def identity(values: dict[str, Any]) -> dict[str, tuple[str, str]]:
                return {k: (v["path"], v["sha256"]) for k, v in values.items()}

            if (
                self.state["document_id"] != document_id
                or self.state["ordered_image_ids"] != order
                or self.state["lang"] != lang
                or identity(self.state["images"]) != identity(images)
            ):
                raise ValueError(
                    "Frozen document input changed; use a new output directory"
                )
            return
        for relative_path, data in assets.items():
            target = self.root / relative_path
            if target.exists() and target.read_bytes() != data:
                raise ValueError(f"Conflicting immutable image: {target}")
            atomic_write(target, data)
        self.state = {
            "schema_version": self.schema_version,
            "document_id": document_id,
            "lang": lang,
            "ordered_image_ids": order,
            "images": images,
            "frozen_at": now(),
            "status": "frozen",
            "rounds": [],
            "attempts": [],
            "runs": [],
            "contract": None,
            "error": None,
        }
        self.save()

    def verify_images(self) -> None:
        for image_id in self.state["ordered_image_ids"]:
            image = self.state["images"][image_id]
            path = (self.root / image["path"]).resolve()
            if not path.is_relative_to(self.root / "images"):
                raise ValueError("Image path escapes document assets")
            if digest(path.read_bytes()) != image["sha256"]:
                raise ValueError(f"Immutable image changed: {image_id}")

    def bind_contract(self, contract: dict[str, Any]) -> None:
        previous = self.state["contract"]
        if previous is not None and previous != contract:
            raise ValueError(
                "Prompt/model/configuration changed; use a new output directory"
            )
        self.state["contract"] = contract
        self.save()

    def save(self) -> None:
        self.state["updated_at"] = now()
        write_json(self.state_path, self.state)

    def artifact(self, relative: str, value: Any) -> str:
        write_json(self.root / relative, value)
        return relative
