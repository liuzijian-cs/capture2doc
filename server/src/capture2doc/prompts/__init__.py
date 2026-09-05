"""Versioned, packaged UTF-8 prompts; no interpolation of document content."""

from hashlib import sha256
from importlib.resources import files
import json

from capture2doc.formats.c2d_xml.validator import NS, _parse_and_validate


def c2d_system_prompt() -> str:
    return files(__package__).joinpath("c2d_system.txt").read_text(encoding="utf-8")


def prompt_fingerprint(prompt: str) -> str:
    return sha256(prompt.encode("utf-8")).hexdigest()


def style_examples() -> list[dict[str, str]]:
    """Load packaged examples and reject broken structures before model startup."""
    name = "c2d_style_examples_v2.json"
    try:
        examples = json.loads(
            files(__package__).joinpath(name).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot load bundled style examples: {name}") from exc
    if not isinstance(examples, list) or not examples:
        raise RuntimeError(
            f"Invalid bundled style examples: {name} must be a nonempty array"
        )
    for index, example in enumerate(examples):
        if (
            not isinstance(example, dict)
            or set(example) != {"visual_fact", "xml", "text"}
            or any(
                not isinstance(value, str) or not value.strip()
                for value in example.values()
            )
        ):
            raise RuntimeError(
                f"Invalid bundled style example {index}: expected visual_fact/xml/text strings"
            )
        xml = f'<c2d-update xmlns="{NS}" schema-version="0.1">{example["xml"]}</c2d-update>'
        root, validation = _parse_and_validate(xml, "c2d-update")
        if not validation.valid or root is None or len(root) != 1:
            issues = "; ".join(
                f"{issue.code}: {issue.message}" for issue in validation.issues
            )
            raise RuntimeError(
                f"Invalid bundled style example {index}: {issues or 'expected exactly one complete block'}"
            )
    return examples


def blocks_system_prompt(
    *,
    include_style_examples: bool = False,
    include_overlap_experiment: bool = False,
) -> str:
    """Full contract; extended style examples remain an explicit experiment.

    The eight-image R3 evaluation regressed numerical transcription with the
    extended examples. Keep the resource reusable without enabling it by default.
    """
    instructions = [
        files(__package__).joinpath(name).read_text(encoding="utf-8")
        for name in ("c2d_blocks_v2.txt", "c2d_contract_v2.txt")
    ]
    if include_overlap_experiment:
        instructions.append(
            files(__package__)
            .joinpath("c2d_overlap_experiment_v2.txt")
            .read_text(encoding="utf-8")
        )
    if include_style_examples:
        instructions.append(
            "通用样式 few-shot：visual_fact 仅说明可见条件，不是输出字段。"
            "学习局部样式范围与结构，不复制示例内容；每张实际图片仍使用完整块协议输出。\n"
            + json.dumps(style_examples(), ensure_ascii=False, separators=(",", ":"))
        )
    return "\n\n".join(instructions)
