"""Empty VLM links fail locally while the public C2D URI contract stays intact."""

import pytest

from capture2doc.formats.c2d_xml import validate_update
from capture2doc.pipeline.blocks import candidate, envelope
from test_block_pipeline import Models, block, result, run, setup, submit


@pytest.mark.parametrize("href", ["", " ", "\t\n", "\u00a0"])
def test_empty_link_is_local_candidate_error_with_complete_evidence(href):
    xml = f'<p><a href="{href}"><b><span background-color="yellow">备份配置</span></b></a></p>'
    assert validate_update(envelope(xml)).valid  # No XSD change or URL whitelist.
    value = candidate(block("备份配置", xml), "a")
    assert value["status"] == "pending"
    assert value["vlm_validation"] == value["final_validation"] == "failed"
    assert value["text"] == value["model_text"] == "备份配置"
    assert value["xml"] == xml  # Preserve the original candidate and all styles.
    assert value["original_candidate"]["xml"] == xml
    issue = value["current_errors"][0]
    assert issue["code"] == "LINK_TARGET_EMPTY"
    assert issue["target_blocks"] == [value["id"]]
    assert issue["line"] and issue["xpath"]
    assert validate_update(issue["correct_example"]).valid
    assert "不猜测或编造 URL" in issue["repair_instruction"]


@pytest.mark.parametrize(
    "href",
    [
        "https://example.org/guide",
        "/guide",
        "../guide",
        "#section",
        "mailto:user@example.org",
        "capture2doc:document",
        " guide ",
    ],
)
def test_nonempty_relative_absolute_and_other_valid_targets_are_unchanged(href):
    xml = f'<p><a href="{href}">操作手册</a></p>'
    assert validate_update(envelope(xml)).valid
    value = candidate(block("操作手册", xml), "a")
    assert value["status"] == "ok"
    assert not value["errors"]
    assert f'href="{href}"' in value["xml"]


def test_missing_href_remains_a_schema_error():
    xml = "<p><a>操作手册</a></p>"
    assert not validate_update(envelope(xml)).valid
    value = candidate(block("操作手册", xml), "a")
    assert value["status"] == "pending"
    assert value["errors"]
    assert all(issue["code"] != "LINK_TARGET_EMPTY" for issue in value["errors"])


def test_link_repair_does_not_replace_long_valid_xml_with_short_caption():
    text = "必须保留的完整正文以及全部说明"
    xml = f'<p><a href=""><b>{text}</b></a></p>'
    value = candidate(block("短标题", xml), "a")
    assert value["status"] == "pending"
    assert value["text"] == text
    assert value["model_text"] == "短标题"
    assert value["xml"] == xml
    assert value["current_errors"][0]["code"] == "LINK_TARGET_EMPTY"


def test_simultaneous_omission_and_empty_link_keep_complete_independent_text():
    text = "功能亮点：此处引导句不可从正文中省略。"
    value = candidate(block(text, '<h2><a href="">功能亮点：</a></h2>'), "a")
    assert value["status"] == "pending"
    assert value["text"] == value["model_text"] == text
    assert {issue["code"] for issue in value["current_errors"]} == {
        "XML_TEXT_OMISSION",
        "LINK_TARGET_EMPTY",
    }


def test_local_repair_removes_only_unknown_link_and_preserves_bold_and_background(
    tmp_path,
):
    text = "请先备份配置，再更新软件。"
    bad = '<p>请先<a href=""><b><span background-color="yellow">备份配置</span></b></a>，再更新软件。</p>'
    good = '<p>请先<b><span background-color="yellow">备份配置</span></b>，再更新软件。</p>'
    store = setup(tmp_path, {"a": text})

    def repair(payload):
        assert len(payload["targets"]) == 1
        assert payload["targets"][0]["xml"] == bad
        return {
            "action": "submit",
            "attempt_id": payload["attempt_id"],
            "target_versions": payload["target_versions"],
            "blocks": [block(text, good)],
        }

    models = Models({"a": text}, actions=[submit(block(text, bad)), repair])
    run(store, models)
    value = result(store)["blocks"][0]
    assert value["status"] == "ok"
    assert value["repair_attempts"] == 1
    assert value["text"] == text
    assert '<b><span background-color="yellow">备份配置</span></b>' in value["xml"]
    assert "<a " not in value["xml"]
    assert any(issue["code"] == "LINK_TARGET_EMPTY" for issue in value["errors"])


def test_empty_link_exhaustion_falls_back_locally_and_continues_later_images(tmp_path):
    texts = {"a": "保留块\n操作手册", "b": "后续图片"}
    bad = '<p><a href=""><span text-color="blue">操作手册</span></a></p>'
    store = setup(tmp_path, texts)

    def repeat(payload):
        return {
            "action": "submit",
            "attempt_id": payload["attempt_id"],
            "target_versions": payload["target_versions"],
            "blocks": [block("操作手册", bad, ["a:ocr:1"])],
        }

    actions = [submit(block("保留块"), block("操作手册", bad, ["a:ocr:1"]))] + [
        repeat
    ] * 5
    models = Models(texts, actions=actions)
    run(store, models)
    values = result(store)["blocks"]
    assert [value["text"] for value in values] == ["保留块", "操作手册", "后续图片"]
    assert [value["status"] for value in values] == ["ok", "fallback", "ok"]
    assert values[1]["repair_attempts"] == 5
    assert (
        values[1]["vlm_validation"] == "failed"
        and values[1]["final_validation"] == "passed"
    )
    assert values[1]["fallback_source"] == "ocr"
    assert any(issue["code"] == "LINK_TARGET_EMPTY" for issue in values[1]["errors"])
    assert len(models.requests) == 7
