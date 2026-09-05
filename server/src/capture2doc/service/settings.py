from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_root: Path = Path.home() / ".local/share/capture2doc"
    host: str = "127.0.0.1"
    port: int = 11209
    trusted_proxy: str = "127.0.0.1"
    model_host: str = "127.0.0.1"
    model_cache: Path = Path.home() / "models/modelscope"
    gpu_lock: Path = Path("/tmp/capture2doc-gpu.lock")
    max_upload_bytes: int = 10 * 1024**2
    max_image_edge: int = 1280
    max_images: int = 100
    max_active_documents: int = 10
    max_data_bytes: int = 50 * 1024**3
    min_free_bytes: int = 20 * 1024**3
    idle_seconds: float = 30
    poll_seconds: float = 0.25
    heartbeat_seconds: float = 15
    send_timeout_seconds: float = 30
    upload_timeout_seconds: float = 120
    max_streams_per_device_document: int = 2

    def __post_init__(self):
        for name in ("data_root", "model_cache", "gpu_lock"):
            object.__setattr__(self, name, Path(getattr(self, name)).expanduser().resolve())
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, (int, float)) and (value <= 0 or isinstance(value, bool)):
                raise ValueError(f"{item.name} must be positive")
        if not 1 <= self.port <= 65535 or self.poll_seconds > 0.25:
            raise ValueError("Invalid port or event poll interval")

    @classmethod
    def load(cls, path: Path | None = None):
        values = {}
        if path:
            values = tomllib.loads(path.read_text()).get("service", {})
        allowed = {f.name for f in fields(cls)}
        if set(values) - allowed:
            raise ValueError(f"Unknown service settings: {sorted(set(values) - allowed)}")
        defaults = cls()
        for name in allowed:
            raw = os.environ.get("C2D_" + name.upper(), values.get(name))
            if raw is not None:
                values[name] = type(getattr(defaults, name))(raw)
        return cls(**values)
