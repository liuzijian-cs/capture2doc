"""V2 block validation, source ownership, safe fallback and public projections."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from lxml import etree

from capture2doc.formats.c2d_xml import (
    ValidationResult,
    validate_document,
    validate_update,
)
from capture2doc.formats.c2d_xml.validator import NS, _parse_and_validate


# Each example is a complete, schema-validated update, also used in repair context.
EXAMPLES = {
    "structure": '<p>引文前言</p><pre lang="python"><code>if a &lt; b:\n    print("你好")</code></pre><blockquote><p>引用正文</p></blockquote>',
    "style": '<p><a href="https://example.org"><b><em><del><u><code><span text-color="red" background-color="yellow">重点</span></code></u></del></em></b></a><br/><latex>E=mc^2</latex></p>',
    "table": '<table><thead><tr><th>项</th><th>值</th></tr></thead><tbody><tr><td rowspan="2">A</td><td>1</td></tr><tr><td>2</td></tr></tbody></table>',
    "list": "<ul><li>第一项<ul><li>子项</li></ul></li><li>第二项</li></ul>",
    "paragraph": "<h2>章节标题</h2><p>A &amp; B，1 &lt; 2。</p>",
}


def envelope(body: str, tag: str = "c2d-update") -> str:
    return f'<{tag} xmlns="{NS}" schema-version="0.1">{body}</{tag}>'


def examples() -> dict[str, str]:
    result = {key: envelope(body) for key, body in EXAMPLES.items()}
    for value in result.values():
        if not validate_update(value).valid:
            raise RuntimeError("Invalid bundled repair example")
    return result


def error(code: str, message: str, targets: list[str] | None = None) -> dict:
    return {
        "code": code,
        "message": message,
        "target_blocks": targets or [],
        "line": None,
        "column": None,
        "xpath": None,
        "actual_structure": message,
        "allowed_structure": "完整的合法 C2D 顶层块；不得删除有效正文。",
        "repair_instruction": "只修复允许范围，返回完整块及独立 text，保留原文。",
        "correct_example": examples()["paragraph"],
    }


def validation_errors(result: Any, ids: list[str]) -> list[dict]:
    records = []
    for issue in result.issues:
        targets = (
            [ids[issue.block_index]]
            if issue.block_index is not None and issue.block_index < len(ids)
            else ids
        )
        record = error(issue.code, issue.message, targets)
        record.update({k: v for k, v in asdict(issue).items() if k != "block_index"})
        msg = issue.message.lower()
        key = "paragraph"
        if any(word in msg for word in ("pre", "blockquote", "code")):
            key = "structure"
            record["allowed_structure"] = (
                "blockquote 仅含 p、ul、ol、blockquote；pre 必须是顶层块，仅含一个纯文本 code。"
            )
            record["repair_instruction"] = (
                "将代码提升为独立 pre 块；允许把当前失败块拆成多个完整块，保留全部代码及空白。"
            )
        elif (
            any(word in msg for word in ("table", "row", "cell", "span", "column"))
            and "color" not in msg
        ):
            key = "table"
            record["allowed_structure"] = (
                "table: 可选 thead + 必需 tbody；thead/tr/th，tbody/tr/td；各行占位列数相同，跨度不得越界。"
            )
        if any(word in msg for word in ("color", "inline", "nesting", "style")):
            key = "style"
            record["allowed_structure"] = (
                "a→b→em→del→u→code→span；颜色仅 red/orange/yellow/green/blue/purple/gray；br、latex 不在样式链中。"
            )
        record["correct_example"] = examples()[key]
        records.append(record)
    return records


def add_errors(block: dict, records: list[dict]) -> None:
    block["current_errors"] = records
    for record in records:
        if record not in block["errors"]:
            block["errors"].append(deepcopy(record))


def candidate(value: Any, image_id: str, *, lineage: list[str] | None = None) -> dict:
    uid = uuid4().hex
    value = value if isinstance(value, dict) else {}
    block = {
        "id": uid,
        "version": 0,
        "image_id": image_id,
        "lineage": lineage or [uid],
        "xml": value.get("xml"),
        "text": value.get("text"),
        "ocr_refs": value.get("ocr_refs", []),
        "status": "pending",
        "vlm_validation": "failed",
        "final_validation": "failed",
        "fallback_source": None,
        "errors": [],
        "current_errors": [],
        "guards": [],
        "original_candidate": deepcopy(value),
        "repair_attempts": 0,
    }
    validate_block(block)
    return block


def validate_block(block: dict) -> None:
    records = []
    if not isinstance(block["text"], str):
        records.append(
            error(
                "TEXT_REQUIRED",
                "每块必须独立返回字符串 text，不从损坏 XML 剥除标签。",
                [block["id"]],
            )
        )
    refs = block["ocr_refs"]
    if (
        not isinstance(refs, list)
        or any(not isinstance(r, str) for r in refs)
        or len(set(refs)) != len(refs)
    ):
        records.append(
            error(
                "OCR_REFS_INVALID",
                "ocr_refs 必须是无重复来源标识的数组；无法关联时用 []。",
                [block["id"]],
            )
        )
    xml = block["xml"]
    root = None
    if not isinstance(xml, str):
        records.append(error("XML_REQUIRED", "xml 必须为字符串。", [block["id"]]))
    else:
        root, result = _parse_and_validate(envelope(xml), "c2d-update")
        records.extend(validation_errors(result, [block["id"]]))
        if root is not None and len(root) != 1:
            records.append(
                error(
                    "ONE_BLOCK_REQUIRED",
                    "一个候选项只能含一个顶层块；多个块请拆成多个数组项。",
                    [block["id"]],
                )
            )
    if not records and root is not None:
        block["xml"] = etree.tostring(root[0], encoding="unicode", with_tail=False)
        block["status"] = "ok"
        block["vlm_validation"] = block["final_validation"] = "passed"
        # Valid XML is the authoritative final text; keep the independent model text for evidence.
        block["model_text"] = block["text"]
        block["text"] = plain_text(root[0])
    else:
        block["ocr_refs"] = (
            refs
            if isinstance(refs, list) and all(isinstance(r, str) for r in refs)
            else []
        )
        block["status"] = "pending"
        block["vlm_validation"] = block["final_validation"] = "failed"
    add_errors(block, records)


def plain_text(node: Any) -> str:
    """Readable text from VALID XML only, retaining code and structural breaks."""
    result = node.text or ""
    for child in node:
        tag = etree.QName(child).localname
        result += "\n" if tag == "br" else plain_text(child)
        if tag in {"p", "li", "tr"}:
            result += "\n"
        elif tag in {"td", "th"}:
            result += "\t"
        result += child.tail or ""
    return (
        result.rstrip("\n\t")
        if etree.QName(node).localname not in {"code", "pre"}
        else result
    )


def segments(image_id: str, text: str) -> list[dict]:
    """Stable offsets over the raw OCR, not semantic block segmentation."""
    result, offset = [], 0
    for index, line in enumerate(text.splitlines(keepends=True)):
        if line.strip():
            result.append(
                {
                    "source_id": f"{image_id}:ocr:{index}",
                    "start": offset,
                    "end": offset + len(line),
                    "text": line,
                }
            )
        offset += len(line)
    return result


def fallback(
    blocks: list[dict], sources: list[dict], *, reserved: set[str] | None = None
) -> None:
    """Resolve failures without letting order arbitrarily win shared OCR sources."""
    source_map = {s["source_id"]: s["text"] for s in sources}
    claims = Counter(r for b in blocks for r in b["ocr_refs"] if isinstance(r, str))
    reserved = reserved or set()
    for block in blocks:
        if block["status"] != "pending":
            continue
        refs = block["ocr_refs"]
        text, origin = None, None
        if refs and all(
            r in source_map and claims[r] == 1 and r not in reserved for r in refs
        ):
            # OCR source order is fixed by the service, never by model-provided ref order.
            text = "".join(s["text"] for s in sources if s["source_id"] in refs).rstrip(
                "\r\n"
            )
            origin = "ocr"
        elif isinstance(block["text"], str) and block["text"].strip():
            text, origin = block["text"], "vlm_text"
        if text is not None and text.strip():
            try:
                xml = paragraph(text)
            except (ValueError, UnicodeError):
                xml = None
            if xml is not None:
                block.update(
                    status="fallback",
                    text=text,
                    xml=xml,
                    vlm_validation="failed",
                    final_validation="passed",
                    fallback_source=origin,
                )
                continue
        block.update(
            status="unresolved",
            text=None,
            xml=None,
            vlm_validation="failed",
            final_validation="failed",
            fallback_source=None,
        )
        add_errors(
            block,
            block["current_errors"]
            + [
                error(
                    "CONTENT_UNAVAILABLE",
                    "没有可可靠使用的 OCR 片段或合法纯文本。",
                    [block["id"]],
                )
            ],
        )


def paragraph(text: str) -> str:
    node = etree.Element(f"{{{NS}}}p", nsmap={None: NS})
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    node.text = lines[0]
    for line in lines[1:]:
        etree.SubElement(node, f"{{{NS}}}br").tail = line
    xml = etree.tostring(node, encoding="unicode")
    if not validate_update(envelope(xml)).valid:
        raise ValueError("Fallback paragraph does not validate")
    return xml


def combined_errors(blocks: list[dict]) -> list[dict]:
    selected = [b for b in blocks if b["status"] in {"ok", "fallback"}]
    if not selected:
        return []
    xml = envelope("".join(b["xml"] for b in selected), "document")
    result = validate_document(xml)
    # Inputs were individually safely validated. Map document-only issues back
    # to direct children; the public validator deliberately omits update indices.
    tree = etree.fromstring(
        xml.encode(), etree.XMLParser(resolve_entities=False, no_network=True)
    )
    issues = []
    for issue in result.issues:
        nodes = [
            node
            for node in tree.iter()
            if tree.getroottree().getpath(node) == issue.xpath
        ]
        node = nodes[0] if nodes else tree
        while node.getparent() is not None and node.getparent() is not tree:
            node = node.getparent()
        index = list(tree).index(node) if node.getparent() is tree else None
        issues.append(replace(issue, block_index=index))
    return validation_errors(
        ValidationResult(result.valid, tuple(issues)), [b["id"] for b in selected]
    )


def document_xml(blocks: list[dict], lang: str | None) -> bytes | None:
    selected = [
        b for b in blocks if b["xml"] is not None and b["status"] in {"ok", "fallback"}
    ]
    if not selected:
        return None
    root = etree.Element(f"{{{NS}}}document", nsmap={None: NS})
    root.set("schema-version", "0.1")
    if lang:
        root.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
    for b in selected:
        tree, validation = _parse_and_validate(envelope(b["xml"]), "c2d-update")
        if not validation.valid:
            raise ValueError("Corrupt committed block XML")
        root.append(tree[0])
    output = etree.tostring(root, encoding="utf-8", xml_declaration=True)
    if not validate_document(output).valid:
        raise ValueError("Combined document validation failed")
    return output


def public_block(block: dict, index: int) -> dict:
    keys = (
        "status",
        "vlm_validation",
        "final_validation",
        "text",
        "xml",
        "fallback_source",
        "errors",
        "repair_attempts",
    )
    return {"block_id": index, **{key: deepcopy(block[key]) for key in keys}}


def diagnostic(ocr: str, blocks: list[dict]) -> dict:
    expected = "".join(ocr.split())
    actual = "".join("".join((b["text"] or "").split()) for b in blocks)
    matcher = SequenceMatcher(None, expected, actual, autojunk=False)
    matched = sum(m.size for m in matcher.get_matching_blocks())
    return {
        "method": "ocr-character-alignment-diagnostic-only",
        "hard_gate": False,
        "ocr_coverage": matched / len(expected) if expected else None,
        "output_support": matched / len(actual) if actual else None,
        "semantic_fidelity_verified": False,
    }
