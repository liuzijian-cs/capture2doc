"""Validate the packaged visual examples against the real document contract."""

from copy import deepcopy
from importlib.resources import files
import json

import pytest

import capture2doc.prompts as prompts
from capture2doc.formats.c2d_xml import validate_update
from capture2doc.pipeline.blocks import candidate, envelope


def test_packaged_style_examples_validate_and_match_their_independent_text():
    examples = prompts.style_examples()
    assert len(examples) == 7
    for example in examples:
        assert validate_update(envelope(example["xml"])).valid
        block = candidate({**example, "ocr_refs": []}, "example")
        assert block["status"] == "ok"
        assert block["text"] == example["text"]
    # These opposite visual conditions require different markup even though
    # both labels are blue; local emphasis must not spread over a paragraph.
    assert '<a href="https://example.org/guide">' in examples[3]["xml"]
    assert "<a " not in examples[4]["xml"]
    assert '<span text-color="blue">操作手册</span>' in examples[4]["xml"]
    assert (
        '<b><span background-color="yellow">备份配置</span></b>' in examples[5]["xml"]
    )
    assert examples[6]["xml"] == "<p>连接后等待指示灯稳定。</p>"


def test_prompt_contains_serialized_style_resource_and_complete_contract():
    prompt = prompts.blocks_system_prompt(include_style_examples=True)
    package = files(prompts.__package__)
    assert package.joinpath("c2d_contract_v2.txt").read_text(encoding="utf-8") in prompt
    serialized = json.dumps(
        prompts.style_examples(), ensure_ascii=False, separators=(",", ":")
    )
    assert serialized in prompt
    assert json.loads(prompt[prompt.index(serialized) :]) == prompts.style_examples()


@pytest.fixture
def isolated_prompt_package(tmp_path, monkeypatch):
    package = files(prompts.__package__)
    for name in (
        "c2d_blocks_v2.txt",
        "c2d_contract_v2.txt",
        "c2d_style_examples_v2.json",
    ):
        (tmp_path / name).write_text(
            package.joinpath(name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    monkeypatch.setattr(prompts, "files", lambda _: tmp_path)
    return tmp_path


def test_style_resource_changes_are_covered_by_prompt_fingerprint(
    isolated_prompt_package,
):
    path = isolated_prompt_package / "c2d_style_examples_v2.json"
    before = prompts.prompt_fingerprint(
        prompts.blocks_system_prompt(include_style_examples=True)
    )
    examples = json.loads(path.read_text())
    examples[0]["visual_fact"] += "可见标题与后面的正文分段。"
    path.write_text(json.dumps(examples, ensure_ascii=False), encoding="utf-8")
    assert (
        prompts.prompt_fingerprint(
            prompts.blocks_system_prompt(include_style_examples=True)
        )
        != before
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda values: values[0].update(
            xml="<blockquote><pre><code>bad</code></pre></blockquote>"
        ),
        lambda values: values[0].update(
            xml='<p><span text-color="#ffff00">bad</span></p>'
        ),
        lambda values: values[0].update(xml="<h2>one</h2><p>two</p>"),
        lambda values: values[0].pop("visual_fact"),
    ],
)
def test_invalid_style_resource_fails_before_prompt_is_used(
    isolated_prompt_package, change
):
    path = isolated_prompt_package / "c2d_style_examples_v2.json"
    examples = deepcopy(prompts.style_examples())
    change(examples)
    path.write_text(json.dumps(examples, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Invalid bundled style example 0"):
        prompts.blocks_system_prompt(include_style_examples=True)


def test_corrupt_style_json_fails_with_resource_identity(isolated_prompt_package):
    (isolated_prompt_package / "c2d_style_examples_v2.json").write_text(
        "[{", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="c2d_style_examples_v2.json"):
        prompts.blocks_system_prompt(include_style_examples=True)


def test_default_prompt_keeps_full_contract_without_experimental_style_expansion():
    default = prompts.blocks_system_prompt()
    extended = prompts.blocks_system_prompt(include_style_examples=True)
    assert (
        files(prompts.__package__)
        .joinpath("c2d_contract_v2.txt")
        .read_text(encoding="utf-8")
        in default
    )
    assert "通用样式 few-shot" not in default
    assert extended.startswith(default)
    assert prompts.prompt_fingerprint(default) != prompts.prompt_fingerprint(extended)
