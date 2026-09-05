"""Strict, non-mutating validation; no model calls or document assembly."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import as_file, files
from threading import Lock

from lxml import etree

NS = "urn:capture2doc:c2d:1"
_SCHEMA_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    stage: str
    code: str
    message: str
    line: int | None = None
    column: int | None = None
    xpath: str | None = None
    block_index: int | None = None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    issues: tuple[ValidationIssue, ...] = ()


class _DenyExternal(etree.Resolver):
    def resolve(self, url, public_id, context):
        raise OSError("External XML resources are forbidden")


def _parser(*, external: bool = False, encoding: str | None = None):
    parser = etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        recover=False,
        huge_tree=False,
        remove_blank_text=False,
        attribute_defaults=False,
        dtd_validation=False,
        encoding=encoding,
    )
    if not external:
        parser.resolvers.add(_DenyExternal())
    return parser


@lru_cache(maxsize=1)
def _schema():
    # Only bundled, trusted schemas may resolve their local import.
    try:
        with as_file(files(__package__).joinpath("schemas")) as directory:
            return etree.XMLSchema(
                etree.parse(str(directory / "v0_1.xsd"), _parser(external=True))
            )
    except (OSError, etree.LxmlError) as exc:
        raise RuntimeError("Cannot load bundled C2D-XML schema") from exc


def validate_update(xml: str | bytes) -> ValidationResult:
    """Validate complete blocks; replacement semantics require caller state."""
    return _validate(xml, "c2d-update")


def validate_document(xml: str | bytes) -> ValidationResult:
    """Validate the final document, including title order and uniqueness."""
    return _validate(xml, "document")


def _validate(xml: str | bytes, expected: str) -> ValidationResult:
    return _parse_and_validate(xml, expected)[1]


def _parse_and_validate(
    xml: str | bytes, expected: str
) -> tuple[etree._Element | None, ValidationResult]:
    """Return a private tree only after the same checks as the public API."""
    if not isinstance(xml, (str, bytes)):
        raise TypeError("xml must be str or bytes")
    try:
        data = xml.encode("utf-8") if isinstance(xml, str) else xml
        root = etree.fromstring(
            data, _parser(encoding="utf-8" if isinstance(xml, str) else None)
        )
    except (etree.LxmlError, UnicodeError, OSError) as exc:
        line, column = getattr(exc, "position", (None, None))
        return None, ValidationResult(
            False, (ValidationIssue("xml", "XML_SYNTAX", str(exc), line, column),)
        )
    result = _validate_tree(root, expected)
    return (root if result.valid else None), result


def _validate_tree(root: etree._Element, expected: str) -> ValidationResult:
    """Validate a safely parsed or internally constructed tree without editing it."""
    tree = root.getroottree()
    issues: list[ValidationIssue] = []

    def add(node, stage, code, message, column=None):
        index = None
        if expected == "c2d-update" and node is not root:
            block = node
            while block.getparent() is not None and block.getparent() is not root:
                block = block.getparent()
            if block.getparent() is root:
                index = list(root).index(block)
        issues.append(
            ValidationIssue(
                stage,
                code,
                message,
                node.sourceline,
                column,
                tree.getpath(node),
                index,
            )
        )

    if tree.docinfo.doctype:
        add(root, "security", "DOCTYPE_FORBIDDEN", "DOCTYPE is forbidden")
    for node in tree.xpath("//comment() | //processing-instruction()"):
        add(
            node,
            "security",
            "NON_CONTENT_NODE",
            "Comments and processing instructions are forbidden",
        )
    if issues:
        return ValidationResult(False, tuple(issues))
    if root.tag != f"{{{NS}}}{expected}":
        add(root, "schema", "ROOT_INVALID", f"Expected C2D {expected} root")
        return ValidationResult(False, tuple(issues))

    # XMLSchema.error_log is mutable; copy and consume it under the same lock.
    with _SCHEMA_LOCK:
        schema = _schema()
        if not schema.validate(tree):
            for error in schema.error_log:
                nodes = []
                if error.path:
                    try:
                        nodes = tree.xpath(
                            error.path,
                            namespaces={k: v for k, v in root.nsmap.items() if k},
                        )
                    except etree.XPathError:
                        pass
                node = nodes[0] if nodes else root
                add(
                    node, "schema", error.type_name, error.message, error.column or None
                )
            return ValidationResult(False, tuple(issues))

    for node in root.iter():
        tag = etree.QName(node).localname
        if any(
            "\u200d" in value
            for value in (node.text or "", node.tail or "", *node.attrib.values())
        ):
            add(
                node,
                "semantic",
                "PLACEHOLDER_CHARACTER",
                "U+200D must be removed before validation",
            )
        if tag == "p" and not "".join(node.itertext()).strip():
            add(
                node,
                "semantic",
                "EMPTY_PARAGRAPH",
                "Whitespace-only paragraphs are forbidden",
            )
        if tag == "span" and not node.attrib:
            add(
                node,
                "semantic",
                "SPAN_COLOR_REQUIRED",
                "span requires a text or background color",
            )
        if tag == "table":
            _check_table(node, add)
    return ValidationResult(not issues, tuple(issues))


def _check_table(table, add):
    """Check a rectangular grid without allocating rowspan*colspan cells."""
    width = None
    for section in table:
        active = []  # (start column, end column, exclusive ending row)
        rows = list(section)
        for row_index, row in enumerate(rows):
            occupied = [(a, b) for a, b, end in active if end > row_index]
            active = [(a, b, end) for a, b, end in active if end > row_index]
            cursor = 0
            for cell in row:
                for a, b in sorted(occupied):
                    if a <= cursor < b:
                        cursor = b
                end_column = cursor + int(cell.get("colspan", "1"))
                end_row = row_index + int(cell.get("rowspan", "1"))
                if end_row > len(rows) or any(
                    cursor < b and end_column > a for a, b in occupied
                ):
                    add(
                        cell,
                        "semantic",
                        "TABLE_SPAN_INVALID",
                        "Cell span overlaps or exceeds its row group",
                    )
                    return
                occupied.append((cursor, end_column))
                active.append((cursor, end_column, end_row))
                cursor = end_column
            edge = 0
            for a, b in sorted(occupied):
                if a != edge:
                    add(
                        row,
                        "semantic",
                        "TABLE_GRID_INVALID",
                        "Table row contains a gap or overlap",
                    )
                    return
                edge = b
            if edge == 0 or (width is not None and width != edge):
                add(
                    row,
                    "semantic",
                    "TABLE_GRID_INVALID",
                    "Table rows must have equal nonzero width",
                )
                return
            width = edge
