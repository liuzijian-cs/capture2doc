from __future__ import annotations

from pathlib import Path

import pytest

from capture2doc.config import Qwen35Settings
from capture2doc.inference.qwen35_tokens import (
    calculate_image_tokens,
    inspect_qwen35_tokens,
)


class FakeTensor:
    def __init__(self, value: list[int]) -> None:
        self.value = value

    def tolist(self) -> list[int]:
        return self.value


class FakeImage:
    size = (1280, 960)


class FakeImageProcessor:
    size = {"shortest_edge": 65_536, "longest_edge": 16_777_216}
    patch_size = 16
    merge_size = 2


class FakeTokenizer:
    def convert_tokens_to_ids(self, token: str) -> int:
        assert token == "<|image_pad|>"
        return 99


class FakeProcessor:
    def __init__(self) -> None:
        self.image_processor = FakeImageProcessor()
        self.tokenizer = FakeTokenizer()

    def apply_chat_template(self, *_args: object, **_kwargs: object) -> str:
        return "<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>read"

    def __call__(self, **_kwargs: object) -> dict[str, list[FakeTensor]]:
        return {
            "input_ids": [FakeTensor(([1] * 25) + ([99] * 1_200))],
            "image_grid_thw": [FakeTensor([1, 60, 80])],
        }


def test_1280_by_960_image_produces_1200_tokens() -> None:
    assert calculate_image_tokens((1, 60, 80), spatial_merge_size=2) == 1_200


def test_inspector_cross_checks_grid_and_placeholders(tmp_path: Path) -> None:
    image_path = tmp_path / "document.jpg"
    image_path.write_bytes(b"fake")
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "chat_template.jinja").write_text("template", encoding="utf-8")
    (model_path / "preprocessor_config.json").write_text("{}", encoding="utf-8")
    settings = Qwen35Settings(cache_dir=tmp_path / "cache")

    result = inspect_qwen35_tokens(
        image_path,
        "read",
        settings,
        model_path,
        processor=FakeProcessor(),
        image=FakeImage(),
    )

    assert result.image_grid_thw == (1, 60, 80)
    assert result.resized_width == 1_280
    assert result.resized_height == 960
    assert result.image_tokens == result.placeholder_tokens == 1_200
    assert result.prompt_tokens == 1_225
    assert result.maximum_prompt_tokens == 8_192
    assert result.fits_reserved_output
    assert result.chat_template_sha256 is not None


def test_image_grid_must_align_with_merge_area() -> None:
    with pytest.raises(ValueError, match="not divisible"):
        calculate_image_tokens((1, 61, 81), spatial_merge_size=2)
