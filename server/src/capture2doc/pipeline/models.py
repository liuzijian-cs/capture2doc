"""Single-GPU sequential model phases with explicit cleanup gates."""

from __future__ import annotations

import json
import io
import os
import signal
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Iterator

from capture2doc.config import PaddleOcrVlSettings, Qwen35Settings
from capture2doc.inference.gpu_memory import GpuMemorySampler
from capture2doc.inference.model_store import resolve_prepared_model
from capture2doc.inference.paddleocr_vl import recognize_image
from capture2doc.inference.qwen35 import analyze_image
from capture2doc.inference.qwen35_tokens import (
    inspect_qwen35_tokens,
    load_qwen35_processor,
)
from capture2doc.inference.runtime import VllmRuntime

from .store import atomic_write, digest, now, write_json


def snapshot_fingerprint(path: Path) -> str:
    """Cheap drift detection, not a cryptographic digest of model weight contents."""
    entries = []
    for file in sorted(path.rglob("*")):
        if (
            file.is_file()
            and file.suffix
            in {
                ".safetensors",
                ".bin",
                ".pt",
                ".json",
                ".jinja",
                ".py",
                ".model",
                ".txt",
            }
            and "__pycache__" not in file.parts
        ):
            stat = file.stat()
            entry: list[Any] = [
                str(file.relative_to(path)),
                stat.st_size,
                stat.st_mtime_ns,
            ]
            if file.suffix in {".json", ".jinja", ".py"}:
                entry.append(digest(file.read_bytes()))
            entries.append(entry)
    return digest(json.dumps(entries, ensure_ascii=False).encode())


def wait_released(
    sampler: GpuMemorySampler,
    baseline: int,
    *,
    timeout: float = 30,
    tolerance: int = 128,
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        reading = sampler.snapshot("after_stop")
        if reading.used_mib <= baseline + tolerance:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"GPU memory did not recover: {reading.used_mib} MiB; "
                f"baseline {baseline} + tolerance {tolerance}. Next model was not started."
            )
        time.sleep(0.5)


def ensure_group_exited(pgid: int | None, *, timeout: float = 5) -> None:
    if pgid is None or os.name != "posix":
        return
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return
    # Only the process group created by this runtime is targeted.
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Worker process group {pgid} still exists after shutdown"
            )
        time.sleep(0.1)


def verify_previous_cleanup(directory: Path) -> None:
    """Check abandoned phases without signaling a potentially recycled PID."""
    for path in sorted(directory.glob("*/*.metrics.json")):
        metrics = json.loads(path.read_text(encoding="utf-8"))
        if metrics.get("cleanup_verified") or metrics.get("recovery_verified_at"):
            continue
        pgid = metrics.get("pgid")
        if pgid is not None:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError(
                    f"Previous worker group {pgid} still exists; inspect {path} and "
                    "stop the verified old worker before resuming. It was not signaled automatically."
                )
        sampler = GpuMemorySampler()
        wait_released(sampler, metrics["baseline_memory_mib"])
        metrics["recovery_verified_at"] = now()
        write_json(path, metrics)


class LocalModels:
    def __init__(self, *, cache_dir: str | None = None, host: str = "127.0.0.1"):
        self.paddle = replace(PaddleOcrVlSettings.from_sources(cache_dir), host=host)
        self.qwen = replace(
            Qwen35Settings.from_sources(cache_dir),
            host=host,
            kv_cache_memory_bytes=640 * 1024**2,
        )
        self.paths: dict[str, Path] = {}
        self.processor: Any = None

    def prepare(self) -> dict[str, Any]:
        info = {}
        for name, settings in (("paddle", self.paddle), ("qwen", self.qwen)):
            path = resolve_prepared_model(settings)
            self.paths[name] = path
            info[name] = {
                "settings": json.loads(json.dumps(asdict(settings), default=str)),
                "snapshot_path": str(path),
                "snapshot_metadata_sha256": snapshot_fingerprint(path),
            }
        self.processor = load_qwen35_processor(self.paths["qwen"])
        return info

    def image_info(self, path: Path, destination: Path) -> dict[str, Any]:
        from PIL import Image, ImageOps

        with Image.open(path) as image:
            image.load()
            oriented = ImageOps.exif_transpose(image).convert("RGB")
            buffer = io.BytesIO()
            oriented.save(buffer, format="PNG")
            data = buffer.getvalue()
            atomic_write(destination, data)
            return {
                "width": oriented.width,
                "height": oriented.height,
                "model_image_sha256": digest(data),
            }

    @contextmanager
    def phase(self, name: str, directory: Path) -> Iterator[None]:
        settings = self.paddle if name == "paddle" else self.qwen
        runtime = VllmRuntime(
            settings, self.paths[name], directory / f"{name}.vllm.log"
        )
        sampler = GpuMemorySampler()
        baseline = sampler.snapshot("baseline")
        metrics: dict[str, Any] = {
            "started_at": now(),
            "model": name,
            "baseline_memory_mib": baseline.used_mib,
            "cleanup_verified": False,
        }
        pgid = None
        sampler.start()
        try:
            started = time.monotonic()
            try:
                runtime.start()
                pgid = runtime.process.pid if runtime.process is not None else None
                metrics["pgid"] = pgid
                write_json(directory / f"{name}.metrics.json", metrics)
                runtime.wait_ready()
                metrics["load_seconds"] = time.monotonic() - started
                sampler.snapshot("idle_loaded")
                metrics["models_response"] = runtime.fetch_models()
                yield
                sampler.snapshot("post_request")
            finally:
                stopped = time.monotonic()
                try:
                    runtime.stop()
                finally:
                    ensure_group_exited(pgid)
                wait_released(sampler, baseline.used_mib)
                metrics["cleanup_verified"] = True
                metrics["unload_seconds"] = time.monotonic() - stopped
        except BaseException as exc:
            metrics["error"] = str(exc) or type(exc).__name__
            raise
        finally:
            try:
                sampler.stop()
            finally:
                metrics["ended_at"] = now()
                metrics["gpu_memory"] = sampler.summary()
                write_json(directory / f"{name}.metrics.json", metrics)

    def _client(self, name: str) -> Any:
        from openai import OpenAI

        settings = self.paddle if name == "paddle" else self.qwen
        # Pipeline owns the attempt budget; disable hidden SDK retries.
        return OpenAI(
            api_key="EMPTY",
            base_url=settings.api_base_url,
            timeout=1200.0,
            max_retries=0,
        )

    def ocr(self, path: Path) -> Any:
        with self._client("paddle") as client:
            return recognize_image(path, self.paddle, client=client, allow_empty=True)

    def count_tokens(self, text: str) -> int:
        return len(self.processor.tokenizer.encode(text, add_special_tokens=False))

    def inspect(self, path: Path, prompt: str, system: str) -> Any:
        return inspect_qwen35_tokens(
            path,
            prompt,
            self.qwen,
            self.paths["qwen"],
            system_prompt=system,
            processor=self.processor,
        )

    def generate(
        self, path: Path, prompt: str, system: str, inspection: Any, output: int
    ) -> Any:
        with self._client("qwen") as client:
            return analyze_image(
                path,
                prompt,
                self.qwen,
                system_prompt=system,
                prompt_tokens=inspection.prompt_tokens,
                max_tokens=output,
                request_timeout_seconds=1200,
                client=client,
                allow_empty=True,
            )
