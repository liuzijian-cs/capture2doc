"""Inspect how Qwen3.5 turns one image and prompt into model tokens."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from capture2doc.config import Qwen35Settings

IMAGE_PAD_TOKEN = "<|image_pad|>"


@dataclass(frozen=True, slots=True)
class Qwen35TokenInspection:
    original_width: int
    original_height: int
    resized_width: int
    resized_height: int
    image_grid_thw: tuple[int, int, int]
    patch_size: int
    spatial_merge_size: int
    image_tokens: int
    placeholder_tokens: int
    non_image_prompt_tokens: int
    prompt_tokens: int
    maximum_prompt_tokens: int
    max_output_tokens: int
    max_model_len: int
    remaining_context_tokens: int
    rendered_prompt: str
    chat_template_sha256: str | None
    preprocessor_config_sha256: str | None

    @property
    def fits_reserved_output(self) -> bool:
        return self.prompt_tokens <= self.maximum_prompt_tokens

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["image_grid_thw"] = list(self.image_grid_thw)
        value["fits_reserved_output"] = self.fits_reserved_output
        value.pop("rendered_prompt")
        return value


def calculate_image_tokens(
    image_grid_thw: tuple[int, int, int],
    spatial_merge_size: int,
) -> int:
    if spatial_merge_size <= 0:
        raise ValueError("spatial_merge_size must be positive")
    grid_t, grid_h, grid_w = image_grid_thw
    if min(grid_t, grid_h, grid_w) <= 0:
        raise ValueError("image_grid_thw values must be positive")
    divisor = spatial_merge_size**2
    grid_size = grid_t * grid_h * grid_w
    if grid_size % divisor:
        raise ValueError("image grid is not divisible by the spatial merge area")
    return grid_size // divisor


def sha256_if_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_qwen35_processor(model_path: str | Path) -> Any:
    """Load the prepared processor without loading model weights."""

    resolved_model_path = Path(model_path).expanduser().resolve()
    try:
        from transformers import AutoProcessor
    except ImportError as exc:
        raise RuntimeError(
            "Transformers is not installed. On NVIDIA/WSL run "
            "`uv sync --extra cuda`."
        ) from exc
    return AutoProcessor.from_pretrained(
        str(resolved_model_path),
        local_files_only=True,
    )


def _load_rgb_image(image_path: Path) -> Any:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is not installed. On NVIDIA/WSL run `uv sync --extra cuda`."
        ) from exc
    with Image.open(image_path) as opened:
        return opened.convert("RGB")


def inspect_qwen35_tokens(
    image_path: str | Path,
    prompt: str,
    settings: Qwen35Settings,
    model_path: str | Path,
    *,
    enable_thinking: bool = False,
    processor: Any | None = None,
    image: Any | None = None,
) -> Qwen35TokenInspection:
    """Render the official template and count expanded visual placeholders."""

    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    resolved_image_path = Path(image_path).expanduser().resolve()
    if not resolved_image_path.is_file():
        raise FileNotFoundError(f"Image does not exist: {resolved_image_path}")
    resolved_model_path = Path(model_path).expanduser().resolve()
    if not resolved_model_path.is_dir():
        raise FileNotFoundError(f"Model snapshot does not exist: {resolved_model_path}")

    local_processor = processor or load_qwen35_processor(resolved_model_path)
    local_image = image if image is not None else _load_rgb_image(resolved_image_path)
    original_width, original_height = (int(value) for value in local_image.size)

    image_processor = local_processor.image_processor
    processor_size = dict(image_processor.size)
    processor_size["longest_edge"] = settings.max_pixels
    image_processor.size = processor_size

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    rendered_prompt = local_processor.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=enable_thinking,
    )
    encoded = local_processor(
        text=[rendered_prompt],
        images=[local_image],
        return_tensors="pt",
    )

    input_ids = encoded["input_ids"][0].tolist()
    raw_grid = encoded["image_grid_thw"][0].tolist()
    image_grid_thw = tuple(int(value) for value in raw_grid)
    if len(image_grid_thw) != 3:
        raise RuntimeError(f"Unexpected image_grid_thw: {raw_grid!r}")

    patch_size = int(image_processor.patch_size)
    spatial_merge_size = int(image_processor.merge_size)
    image_tokens = calculate_image_tokens(image_grid_thw, spatial_merge_size)
    image_token_id = int(local_processor.tokenizer.convert_tokens_to_ids(IMAGE_PAD_TOKEN))
    placeholder_tokens = input_ids.count(image_token_id)
    if placeholder_tokens != image_tokens:
        raise RuntimeError(
            f"Processor expanded {placeholder_tokens} image placeholders, but the "
            f"grid implies {image_tokens} image tokens."
        )

    _, grid_h, grid_w = image_grid_thw
    prompt_tokens = len(input_ids)
    maximum_prompt_tokens = settings.max_model_len - settings.max_output_tokens
    return Qwen35TokenInspection(
        original_width=original_width,
        original_height=original_height,
        resized_width=grid_w * patch_size,
        resized_height=grid_h * patch_size,
        image_grid_thw=image_grid_thw,
        patch_size=patch_size,
        spatial_merge_size=spatial_merge_size,
        image_tokens=image_tokens,
        placeholder_tokens=placeholder_tokens,
        non_image_prompt_tokens=prompt_tokens - image_tokens,
        prompt_tokens=prompt_tokens,
        maximum_prompt_tokens=maximum_prompt_tokens,
        max_output_tokens=settings.max_output_tokens,
        max_model_len=settings.max_model_len,
        remaining_context_tokens=settings.max_model_len - prompt_tokens,
        rendered_prompt=rendered_prompt,
        chat_template_sha256=sha256_if_file(resolved_model_path / "chat_template.jinja"),
        preprocessor_config_sha256=sha256_if_file(
            resolved_model_path / "preprocessor_config.json"
        ),
    )
