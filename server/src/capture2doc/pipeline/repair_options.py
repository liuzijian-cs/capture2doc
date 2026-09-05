"""Validated choices for a model-directed, narrowly scoped structural repair.

This module does not apply patches, dispatch models or consume/reset budgets.
The coordinator must regenerate choices from the current target, resolve only
the returned option_id, then submit those stored blocks through apply_patch.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

from lxml import etree

from capture2doc.formats.c2d_xml import validate_update
from capture2doc.formats.c2d_xml.validator import NS, _parser

from .blocks import candidate, envelope
from .draft import RejectedPatch, preserve_content


def _content_hash(value: object) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def repair_options(target: dict) -> list[dict]:
    """Offer only the unique legal pre inside an otherwise empty blockquote.

    Removing arbitrary wrappers could discard quote meaning, language, links or
    other content. The supported case is deliberately restricted to the actual
    observed invalid blockquote/pre pairing, without attributes or extra text.
    """
    if (
        target.get("status") != "pending"
        or not isinstance(target.get("id"), str)
        or not target["id"]
        or type(target.get("version")) is not int
        or target["version"] < 0
        or not isinstance(target.get("xml"), str)
        or not isinstance(target.get("text"), str)
    ):
        return []
    source_xml = envelope(target["xml"])
    if validate_update(source_xml).valid:
        return []
    try:
        root = etree.fromstring(source_xml.encode("utf-8"), _parser(encoding="utf-8"))
    except (etree.LxmlError, UnicodeError, OSError):
        return []
    if (
        root.getroottree().docinfo.doctype
        or any(not isinstance(node.tag, str) for node in root.iter())
        or len(root) != 1
        or (root.text or "").strip()
    ):
        return []
    wrapper = root[0]
    if (
        wrapper.tag != f"{{{NS}}}blockquote"
        or wrapper.attrib
        or len(wrapper) != 1
        or (wrapper.text or "").strip()
        or (wrapper.tail or "").strip()
    ):
        return []
    child = wrapper[0]
    if child.tag != f"{{{NS}}}pre" or (child.tail or "").strip():
        return []
    # Serialize only the child; indentation outside it is structural. Its code
    # text, leading spaces and trailing newline are never stripped or rewritten.
    proposed = {
        "xml": etree.tostring(child, encoding="unicode", with_tail=False),
        "text": target["text"],
        "ocr_refs": deepcopy(target.get("ocr_refs")),
    }
    checked = candidate(proposed, target.get("image_id", "repair-option"))
    if checked["status"] != "ok":
        return []
    # With no other text in the wrapper, the independent text must be the code
    # itself. A short caption is not enough evidence to offer this exact repair.
    if target["text"].replace("\r\n", "\n") != checked["text"]:
        return []
    try:
        preserve_content([target], [checked])
    except RejectedPatch:
        return []
    blocks = [{key: deepcopy(checked[key]) for key in ("xml", "text", "ocr_refs")}]
    source = {key: target[key] for key in ("id", "version", "xml", "text", "ocr_refs")}
    source_hash = _content_hash(source)
    candidate_hash = _content_hash(blocks)
    identity = {
        "kind": "unwrap-blockquote-pre-v1",
        "target_id": target["id"],
        "target_version": target["version"],
        "source_sha256": source_hash,
        "candidate_sha256": candidate_hash,
    }
    return [
        {
            "option_id": "repair-option:" + _content_hash(identity),
            "target_versions": {target["id"]: target["version"]},
            "source_sha256": source_hash,
            "candidate_sha256": candidate_hash,
            "description": "仅移除非法 blockquote 外壳，将其中唯一的 pre 提升为完整顶层块；全部代码、空白和 OCR 引用保持不变。",
            "blocks": blocks,
        }
    ]
