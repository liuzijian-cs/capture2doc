from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def load_smoke_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "smoke_qwen35.py"
    spec = importlib.util.spec_from_file_location("smoke_qwen35", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke_module = load_smoke_module()
parse_args = smoke_module.parse_args
settings_from_args = smoke_module.settings_from_args


def test_smoke_uses_qwen35_defaults(tmp_path: Path) -> None:
    args = parse_args(["--image", "document.jpg", "--cache-dir", str(tmp_path)])

    settings = settings_from_args(args)

    assert settings.port == 8119
    assert settings.max_pixels == 1_310_720
    assert settings.max_output_tokens == 8_192
    assert settings.max_model_len == 16_384
    assert settings.kv_cache_memory_bytes == 1024**3
    assert settings.quantization == "fp8_per_channel"


def test_smoke_applies_resource_overrides(tmp_path: Path) -> None:
    args = parse_args(
        [
            "--image",
            "document.jpg",
            "--cache-dir",
            str(tmp_path),
            "--host",
            "10.255.255.254",
            "--port",
            "8120",
            "--max-tokens",
            "4096",
            "--gpu-memory-utilization",
            "0.75",
            "--enable-thinking",
        ]
    )

    settings = settings_from_args(args)

    assert settings.host == "10.255.255.254"
    assert settings.port == 8120
    assert settings.max_output_tokens == 4_096
    assert settings.kv_cache_memory_bytes is None
    assert settings.gpu_memory_utilization == 0.75
    assert args.enable_thinking is True


def test_smoke_memory_flags_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--image",
                "document.jpg",
                "--kv-cache-memory-bytes",
                "1073741824",
                "--gpu-memory-utilization",
                "0.75",
            ]
        )
