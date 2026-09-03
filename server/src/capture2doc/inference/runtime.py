"""Lifecycle management for the standalone vLLM worker."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from capture2doc.config import PaddleOcrVlSettings
from capture2doc.inference.device import is_wsl2


class RuntimeStartError(RuntimeError):
    """Raised when vLLM exits early or does not become healthy."""


class VllmRuntime:
    def __init__(
        self,
        settings: PaddleOcrVlSettings,
        model_path: str | Path,
        log_path: str | Path,
        *,
        executable: str = "vllm",
    ) -> None:
        self.settings = settings
        self.model_path = Path(model_path).expanduser().resolve()
        self.log_path = Path(log_path).expanduser().resolve()
        self.executable = executable
        self._process: subprocess.Popen[bytes] | None = None
        self._log_file: BinaryIO | None = None

    def build_command(self) -> list[str]:
        command = [
            self.executable,
            "serve",
            str(self.model_path),
            "--served-model-name",
            self.settings.served_model_name,
            "--host",
            self.settings.host,
            "--port",
            str(self.settings.port),
            "--dtype",
            self.settings.dtype,
            "--max-model-len",
            str(self.settings.max_model_len),
            "--max-num-batched-tokens",
            str(self.settings.max_num_batched_tokens),
            "--max-num-seqs",
            str(self.settings.max_num_seqs),
            "--no-enable-prefix-caching",
            "--mm-processor-cache-gb",
            "0",
            "--mm-processor-kwargs",
            json.dumps(
                {"max_pixels": self.settings.max_pixels},
                separators=(",", ":"),
            ),
            "--limit-mm-per-prompt",
            json.dumps({"image": 1}, separators=(",", ":")),
            "--trust-remote-code",
        ]
        if self.settings.kv_cache_memory_bytes is not None:
            command.extend(
                ["--kv-cache-memory-bytes", str(self.settings.kv_cache_memory_bytes)]
            )
        else:
            assert self.settings.gpu_memory_utilization is not None
            command.extend(
                ["--gpu-memory-utilization", str(self.settings.gpu_memory_utilization)]
            )
        return command

    def build_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            }
        )
        environment.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
        if is_wsl2():
            environment.setdefault("VLLM_WSL2_ENABLE_PIN_MEMORY", "1")
        return environment

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("vLLM worker has already been started")
        if not self.model_path.is_dir():
            raise RuntimeStartError(f"Model snapshot is not a directory: {self.model_path}")
        if shutil.which(self.executable) is None:
            raise RuntimeStartError(
                f"Cannot find `{self.executable}`. Run `uv sync --extra cuda` first."
            )

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = self.log_path.open("ab", buffering=0)
        try:
            self._process = subprocess.Popen(
                self.build_command(),
                stdin=subprocess.DEVNULL,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                env=self.build_environment(),
                shell=False,
                start_new_session=(os.name == "posix"),
            )
        except Exception:
            self._close_log()
            raise

    def wait_ready(
        self,
        *,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float = 1.0,
        urlopen_fn: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if self._process is None:
            raise RuntimeError("vLLM worker has not been started")

        timeout = timeout_seconds or self.settings.startup_timeout_seconds
        deadline = time.monotonic() + timeout
        health_url = f"{self.settings.origin}/health"
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            return_code = self._process.poll()
            if return_code is not None:
                raise RuntimeStartError(
                    f"vLLM exited with code {return_code} before becoming healthy.\n"
                    f"Log tail:\n{self._tail_log()}"
                )
            try:
                with urlopen_fn(health_url, timeout=2.0) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError) as exc:
                last_error = exc
            time.sleep(poll_interval_seconds)

        detail = f" Last health error: {last_error}" if last_error else ""
        raise RuntimeStartError(
            f"vLLM did not become healthy within {timeout:.1f}s.{detail}\n"
            f"Log tail:\n{self._tail_log()}"
        )

    def fetch_models(self) -> dict[str, Any]:
        url = f"{self.settings.api_base_url}/models"
        with urllib.request.urlopen(url, timeout=10.0) as response:
            if response.status != 200:
                raise RuntimeStartError(f"GET {url} returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))

    def stop(self, *, timeout_seconds: float | None = None) -> None:
        process = self._process
        if process is None:
            self._close_log()
            return

        timeout = timeout_seconds or self.settings.shutdown_timeout_seconds
        try:
            if process.poll() is None:
                self._send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._send_signal(signal.SIGKILL)
                    process.wait(timeout=5.0)
        finally:
            self._process = None
            self._close_log()

    def _send_signal(self, value: signal.Signals) -> None:
        assert self._process is not None
        if os.name == "posix":
            try:
                os.killpg(self._process.pid, value)
                return
            except ProcessLookupError:
                return
        if value == signal.SIGTERM:
            self._process.terminate()
        else:
            self._process.kill()

    def _tail_log(self, line_count: int = 80) -> str:
        if not self.log_path.exists():
            return "<no log output>"
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return f"<unable to read log: {exc}>"
        return "\n".join(lines[-line_count:]) or "<empty log>"

    def _close_log(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def __enter__(self) -> "VllmRuntime":
        self.start()
        try:
            self.wait_ready()
        except Exception:
            self.stop()
            raise
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.stop()
