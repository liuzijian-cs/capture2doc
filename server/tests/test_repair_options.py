"""Conservative model-selectable repair choices, with real content validation."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

import capture2doc.pipeline.repair_options as module
from capture2doc.formats.c2d_xml import validate_update
from capture2doc.pipeline.blocks import candidate, envelope
from capture2doc.pipeline.draft import (
    apply_patch,
    initialize,
    new_draft,
    preserve_content,
    start_attempt,
)
from capture2doc.pipeline.repair_options import repair_options


CODE = 'if a < b:\n    print("a  b")\n'
XML = '<blockquote>\n  <pre lang="python"><code>if a &lt; b:\n    print("a  b")\n</code></pre>\n</blockquote>'


def target(xml=XML, text=CODE):
    return candidate(
        {"xml": xml, "text": text, "ocr_refs": ["image-1:ocr:0"]}, "image-1"
    )


def test_only_safe_wrapper_is_removed_with_code_text_and_sources_unchanged():
    original = target()
    before = deepcopy(original)
    options = repair_options(original)
    assert original == before
    assert len(options) == 1
    option = options[0]
    assert option["target_versions"] == {original["id"]: 0}
    assert len(option["blocks"]) == 1
    repaired = option["blocks"][0]
    assert repaired["text"] == CODE
    assert repaired["ocr_refs"] == ["image-1:ocr:0"]
    assert "blockquote" not in repaired["xml"]
    assert 'lang="python"' in repaired["xml"]
    assert validate_update(envelope(repaired["xml"])).valid
    verified = candidate(repaired, "image-1")
    assert verified["status"] == "ok" and verified["text"] == CODE
    preserve_content([original], [verified])


@pytest.mark.parametrize(
    "xml",
    [
        '<!DOCTYPE blockquote [<!ENTITY x SYSTEM "file:///not-to-be-read">]><blockquote><pre><code>&x;</code></pre></blockquote>',
        "<blockquote><pre><code>&undefined;</code></pre></blockquote>",
        "<blockquote><!-- hidden meaning --><pre><code>x</code></pre></blockquote>",
        "<blockquote><?hidden meaning?><pre><code>x</code></pre></blockquote>",
        "<blockquote><pre><code>x</pre></code></blockquote>",
        "<blockquote><pre><code>x</code></pre></blockquote><p>neighbor</p>",
        "正文<blockquote><pre><code>x</code></pre></blockquote>",
        "<blockquote><pre><code>x</code></pre></blockquote>正文",
        "<blockquote>正文<pre><code>x</code></pre></blockquote>",
        "<blockquote><pre><code>x</code></pre>正文</blockquote>",
        "<blockquote><p>引导句</p><pre><code>x</code></pre></blockquote>",
        "<blockquote><pre><code>x</code></pre><pre><code>y</code></pre></blockquote>",
        '<blockquote xml:lang="en"><pre><code>x</code></pre></blockquote>',
        '<blockquote class="important"><pre><code>x</code></pre></blockquote>',
        "<div><pre><code>x</code></pre></div>",
        "<blockquote><h2>heading</h2></blockquote>",
        "<blockquote><pre><b>not a code child</b></pre></blockquote>",
        '<blockquote><pre><code><span text-color="red">x</span></code></pre></blockquote>',
    ],
)
def test_unsafe_ambiguous_or_unknown_structure_has_no_option(xml):
    original = target(xml, "x")
    before = deepcopy(original)
    assert repair_options(original) == []
    assert original == before


@pytest.mark.parametrize(
    "text",
    [
        "代码概要",
        'if a < b:\n  print("a  b")\n',
        'if a < b:\n    print("a b")\n',
        'if a < b:\n    print("a  b")',
        None,
    ],
)
def test_incorrect_independent_text_does_not_replace_or_shorten_code(text):
    original = target(text=text)
    assert repair_options(original) == []


def test_content_guard_rejection_never_exposes_a_choice(monkeypatch):
    original = target()
    before = deepcopy(original)

    def reject(*args):
        raise module.RejectedPatch("injected content guard rejection")

    monkeypatch.setattr(module, "preserve_content", reject)
    assert repair_options(original) == []
    assert original == before


def test_valid_xml_with_missing_prose_is_not_a_wrapper_repair():
    original = target("<h2>标题</h2>", "标题以及不能省略的完整引导句")
    assert original["status"] == "pending"
    assert original["current_errors"][0]["code"] == "XML_TEXT_OMISSION"
    assert repair_options(original) == []


def test_identity_covers_version_original_candidate_and_proposed_content():
    original = target()
    first = repair_options(original)[0]
    assert repair_options(deepcopy(original))[0] == first
    candidate_hash = sha256(
        json.dumps(
            first["blocks"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    assert first["candidate_sha256"] == candidate_hash
    changed_version = deepcopy(original)
    changed_version["version"] += 1
    second = repair_options(changed_version)[0]
    assert first["option_id"] != second["option_id"]
    assert first["candidate_sha256"] == second["candidate_sha256"]
    changed_wrapper = deepcopy(original)
    changed_wrapper["xml"] = changed_wrapper["xml"].replace(
        "<blockquote>\n", "<blockquote>\n\n"
    )
    third = repair_options(changed_wrapper)[0]
    assert first["option_id"] != third["option_id"]
    assert first["source_sha256"] != third["source_sha256"]
    assert first["candidate_sha256"] == third["candidate_sha256"]
    changed_sources = deepcopy(original)
    changed_sources["ocr_refs"].append("image-1:ocr:1")
    fourth = repair_options(changed_sources)[0]
    assert first["candidate_sha256"] != fourth["candidate_sha256"]
    assert first["option_id"] != fourth["option_id"]
    # Caller changes to a returned option never mutate the target or cache a
    # poisoned result for the next choice construction.
    first["blocks"][0]["ocr_refs"].append("poison")
    assert repair_options(original)[0]["blocks"][0]["ocr_refs"] == ["image-1:ocr:0"]


def test_choice_construction_does_not_apply_or_reset_the_attempt_budget():
    draft = new_draft("image-1", None)
    initialize(
        draft, {"blocks": [{"xml": XML, "text": CODE, "ocr_refs": ["image-1:ocr:0"]}]}
    )
    attempt = start_attempt(draft, [draft["blocks"][0]["id"]])
    before = deepcopy(draft)
    options = repair_options(draft["blocks"][0])
    assert draft == before
    apply_patch(
        draft,
        attempt,
        {
            "attempt_id": attempt["attempt_id"],
            "target_versions": attempt["target_versions"],
            "blocks": options[0]["blocks"],
        },
    )
    assert draft["blocks"][0]["status"] == "ok"
    assert draft["blocks"][0]["repair_attempts"] == 1
    assert max(draft["budgets"].values()) == 1
    assert draft["blocks"][0]["text"] == CODE
    assert repair_options(draft["blocks"][0]) == []
