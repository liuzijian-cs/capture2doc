from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def load_smoke_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "smoke_paddleocr_vl.py"
    spec = importlib.util.spec_from_file_location("smoke_paddleocr_vl", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke_module = load_smoke_module()
parse_args = smoke_module.parse_args
settings_from_args = smoke_module.settings_from_args


def test_smoke_settings_use_single_image_defaults(tmp_path: Path) -> None:
    args = parse_args(["--image", "document.jpg", "--cache-dir", str(tmp_path)])

    settings = settings_from_args(args)

    assert settings.host == "127.0.0.1"
    assert settings.max_pixels == 1_003_520
    assert settings.max_output_tokens == 4_096
    assert settings.max_model_len == 8_192
    assert settings.max_num_batched_tokens == 4_096
    assert settings.kv_cache_memory_bytes == 256 * 1024**2
    assert settings.gpu_memory_utilization is None


def test_smoke_settings_apply_cli_overrides(tmp_path: Path) -> None:
    args = parse_args(
        [
            "--image",
            "document.jpg",
            "--cache-dir",
            str(tmp_path),
            "--host",
            "10.255.255.254",
            "--max-pixels",
            "802816",
            "--max-model-len",
            "6144",
            "--max-num-batched-tokens",
            "2048",
            "--max-tokens",
            "3072",
            "--gpu-memory-utilization",
            "0.2",
        ]
    )

    settings = settings_from_args(args)

    assert settings.host == "10.255.255.254"
    assert settings.max_pixels == 802_816
    assert settings.max_model_len == 6_144
    assert settings.max_num_batched_tokens == 2_048
    assert settings.max_output_tokens == 3_072
    assert settings.kv_cache_memory_bytes is None
    assert settings.gpu_memory_utilization == 0.2


def test_smoke_memory_flags_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--image",
                "document.jpg",
                "--kv-cache-memory-bytes",
                "268435456",
                "--gpu-memory-utilization",
                "0.2",
            ]
        )
