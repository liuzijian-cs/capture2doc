"""V2 block validation, source ownership, safe fallback and public projections."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from difflib import SequenceMatcher
import re
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
    xml_text = None
    if not records and root is not None:
        xml_text = plain_text(root[0])
        block["model_text"] = block["text"]
        diagnostic, omission = representation_check(block, root[0], xml_text)
        block["representation_diagnostic"] = diagnostic
        if omission is not None:
            records.append(omission)
        link_issues = empty_link_errors(block["id"], root[0])
        records.extend(link_issues)
        if link_issues and omission is None:
            # This XML already passed the public schema. Its complete text must
            # survive a link-only repair even if model_text was a short caption.
            # The independently supplied text remains recorded in model_text.
            block["text"] = xml_text
    if not records and root is not None:
        block["xml"] = etree.tostring(root[0], encoding="unicode", with_tail=False)
        block["status"] = "ok"
        block["vlm_validation"] = block["final_validation"] = "passed"
        # Valid XML is the authoritative final text; keep the independent model text for evidence.
        block["text"] = xml_text
    else:
        if not isinstance(block["text"], str):
            block["text"] = None
        if not isinstance(block["xml"], str):
            block["xml"] = None
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
        if tag in {"p", "li", "tr", "thead", "tbody"}:
            result += "\n"
        elif tag in {"td", "th"}:
            result += "\t"
        result += child.tail or ""
    return (
        result.rstrip("\n\t")
        if etree.QName(node).localname not in {"code", "pre"}
        else result
    )


def structural_text(text: str) -> str:
    """Ignore layout breaks, retaining ordinary spaces inside a line of text."""
    return re.sub(r"[ \t]*[\r\n]+[ \t]*", "", text).replace("\t", "")


def empty_link_errors(block_id: str, node: Any) -> list[dict]:
    """An empty target is a candidate error, without changing public URI rules."""
    links = [
        child
        for child in node.iter(f"{{{NS}}}a")
        if child.get("href") is not None and not child.get("href").strip()
    ]
    if not links:
        return []
    example = envelope(
        '<p><span text-color="blue">操作手册</span></p>'
        '<p><b><span background-color="yellow">备份配置</span></b></p>'
    )
    if not validate_update(example).valid:
        raise RuntimeError("Invalid bundled empty-link repair example")
    records = []
    for link in links:
        record = error(
            "LINK_TARGET_EMPTY",
            "a 的 href 为空或纯空白。只有已知的非空链接目标才能使用 a；"
            "蓝字或底色不代表已知 URL，不能编造目标。",
            [block_id],
        )
        record.update(
            line=link.sourceline,
            xpath=link.getroottree().getpath(link),
            actual_structure=f"a href={link.get('href')!r}",
            allowed_structure="已知非空目标使用 a href；目标未知时保留文字及有依据的 b/span，不输出 a。",
            repair_instruction=(
                "只移除目标未知的 a 外层，完整保留文字、粗体和有图像依据的 span；"
                "保持文字色与背景色的区别及原强调范围，不因移除链接而删除样式。"
                "只有可靠输入已给出目标时才填写非空 href，不猜测或编造 URL。"
            ),
            correct_example=example,
        )
        records.append(record)
    return records


def representation_check(
    block: dict, node: Any, xml_text: str
) -> tuple[dict, dict | None]:
    """Only a substantial, literal XML omission is a local repair condition.

    Independent text may itself be an abbreviated caption or contain code/UI
    labels. Uncertain disagreement remains evidence, never authority to shorten
    valid XML or an OCR-based content gate.
    """
    model_text = block["text"]
    expected, actual = structural_text(model_text), structural_text(xml_text)
    tag = etree.QName(node).localname
    contains_code = any(
        etree.QName(child).localname in {"pre", "code"} for child in node.iter()
    )
    diagnostic = {
        "method": "independent-text-vs-valid-xml",
        "normalization": "structural-linebreaks-and-tabs-only",
        "relation": "uncertain_difference",
        "hard_gate": False,
        "model_text_nonspace_characters": sum(not c.isspace() for c in model_text),
        "xml_text_nonspace_characters": sum(not c.isspace() for c in xml_text),
    }
    if model_text == xml_text:
        diagnostic["relation"] = "equal"
    elif tag == "hr":
        diagnostic["relation"] = "non_text_separator"
    elif expected == actual:
        diagnostic["relation"] = (
            "code_whitespace_difference"
            if contains_code
            else "structural_whitespace_only"
        )
    elif expected and expected in actual:
        diagnostic["relation"] = "xml_contains_more_text"
    elif actual in expected:
        start = expected.index(actual)
        prefix, suffix = expected[:start], expected[start + len(actual) :]
        extra = prefix + suffix
        missing_count = sum(not c.isspace() for c in extra)
        diagnostic.update(
            relation="xml_is_text_substring",
            xml_text_start=start,
            missing_nonspace_characters=missing_count,
        )
        control_markup = re.search(
            r"<\|[^>\r\n]+\|>|</?(?:think|tool_call|tool_response)>|```|~~~",
            extra,
        )
        if (contains_code and actual) or control_markup:
            diagnostic["relation"] = "code_or_control_label_difference"
        elif missing_count >= 8 and any(c.isalnum() for c in extra):
            diagnostic["hard_gate"] = True
            record = error(
                "XML_TEXT_OMISSION",
                "合法 XML 的文字是独立 text 的真子串，"
                f"缺少至少 {missing_count} 个非空白字符。"
                "标题后的引导句或前后正文不能只保留在 text 中。",
                [block["id"]],
            )
            example = envelope(
                "<h2>使用说明：</h2><p>设备在采样完成后将结果写入缓存。</p>"
            )
            if not validate_update(example).valid:
                raise RuntimeError("Invalid bundled XML/text omission example")
            record.update(
                line=node.sourceline,
                xpath=node.getroottree().getpath(node),
                text_position={
                    "normalization": diagnostic["normalization"],
                    "xml_start": start,
                    "xml_end": start + len(actual),
                    "missing_prefix": prefix,
                    "missing_suffix": suffix,
                },
                actual_structure=f"{tag} 仅表达了独立 text 的一部分。",
                allowed_structure="每个块的 XML 和 text 表达同一份完整文字；标题和引导句可拆成 h2 + p。",
                repair_instruction=(
                    "保留 targets.text 全文，不增加邻居内容。把遗漏的前后正文写入 XML；"
                    "若包含标题和引导句，将其拆成两个完整候选，分别给出对应的 xml/text。"
                    "已有粗体、颜色和其他样式保持不变，不用短 XML 覆盖完整 text。"
                ),
                correct_example=example,
            )
            return diagnostic, record
    return diagnostic, None


def protected_code(block: dict) -> list[str]:
    """Find exact code evidence for repair guards, never for fallback extraction.

    A schema-invalid container may still have safely readable code children.
    Syntax-invalid XML cannot identify those boundaries, so conservatively keep
    its independently saved text unchanged when it contains a code tag.
    """
    xml, text = block.get("xml"), block.get("text")
    if not isinstance(xml, str) or not isinstance(text, str) or not text:
        return []
    if not re.search(r"<(?:[\w.-]+:)?(?:pre|code)(?:\s|>)", xml):
        return []
    try:
        root = etree.fromstring(
            envelope(xml).encode(),
            etree.XMLParser(resolve_entities=False, no_network=True, recover=False),
        )
        if root.getroottree().docinfo.doctype or any(
            not isinstance(node.tag, str) for node in root.iter()
        ):
            return [text]
    except (etree.LxmlError, UnicodeError):
        return [text]
    values = []
    for node in root.iter(f"{{{NS}}}code"):
        value = "".join(node.itertext())
        # Independent text is the content authority for a failed candidate.
        # Conflicting XML is diagnostic evidence, not replacement fallback text.
        if value and value in text:
            values.append(value)
    return values


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
