from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re

import pytest

from capture2doc.formats.c2d_xml import validate_document, validate_update

NS = "urn:capture2doc:c2d:1"


def wrap(body, root="c2d-update"):
    return f'<{root} xmlns="{NS}" schema-version="0.1">{body}</{root}>'


@pytest.mark.parametrize(
    "body",
    [
        "<p>跨图片续写后的完整段落。</p><p>新增段落。</p>",
        "<ul><li>旧项</li><li>新项<ol><li>子项</li></ol></li></ul>",
        "<table><tbody><tr><td>旧行</td></tr><tr><td>新行</td></tr></tbody></table>",
        '<table><tbody><tr><td rowspan="2" colspan="2">A</td></tr><tr/></tbody></table>',
        '<p><a href="https://example.com"><b><em><del><u><code><span text-color="blue" background-color="gray">文字</span></code></u></del></em></b></a><br/><latex>x &lt; y</latex></p>',
        "<blockquote><p>引用</p><blockquote><p>嵌套</p></blockquote></blockquote>",
        '<pre lang="python"><code>  if a &lt; b:\n    print("x")\n</code></pre><hr/>',
    ],
)
def test_valid_updates(body):
    assert validate_update(wrap(body)).valid


def test_document_example():
    spec = Path(__file__).resolve().parents[2] / "docs/c2d_xml.md"
    example = spec.read_text().split("## 完整示例", 1)[1]
    xml = re.search(r"```xml\n(.*?)\n```", example, re.S).group(1)
    assert validate_document(xml).valid
    assert validate_document(xml.encode()).valid


@pytest.mark.parametrize(
    "body",
    [
        "<img/>",
        '<p unknown="x">text</p>',
        '<p><span text-color="pink">x</span></p>',
        "<p><em><b>wrong</b></em></p>",
        "<p><b><b>x</b></b></p>",
        "<p><b><latex>x</latex></b></p>",
        "<li>x</li>",
        "<tr/>",
        "<td/>",
        "<p><ul><li>x</li></ul></p>",
        "<pre><code><b>x</b></code></pre>",
        "<ul/>",
        "<blockquote/>",
        "<table/>",
        '<table><tbody><tr><td rowspan="0">x</td></tr></tbody></table>',
        "<table><thead><tr><td>x</td></tr></thead><tbody><tr><td>x</td></tr></tbody></table>",
    ],
)
def test_invalid_structures(body):
    result = validate_update(wrap(body))
    assert not result.valid
    assert result.issues[0].stage == "schema"


@pytest.mark.parametrize(
    "body,code",
    [
        ("<p> \n </p>", "EMPTY_PARAGRAPH"),
        ("<p><b> </b><br/></p>", "EMPTY_PARAGRAPH"),
        ("<p>a&#x200d;b</p>", "PLACEHOLDER_CHARACTER"),
        ("<p><span>x</span></p>", "SPAN_COLOR_REQUIRED"),
        (
            '<table><tbody><tr><td rowspan="2">x</td></tr></tbody></table>',
            "TABLE_SPAN_INVALID",
        ),
        (
            "<table><tbody><tr><td>x</td></tr><tr><td>x</td><td>y</td></tr></tbody></table>",
            "TABLE_GRID_INVALID",
        ),
    ],
)
def test_semantic_errors(body, code):
    result = validate_update(wrap("<p>valid</p>" + body))
    assert not result.valid
    assert result.issues[0].code == code
    assert result.issues[0].block_index == 1


def test_error_mapping_same_line_and_prefixed_namespace():
    for xml in [
        wrap('<p>ok</p><p bad="x">bad</p>'),
        f'<c:c2d-update xmlns:c="{NS}" schema-version="0.1"><c:p>ok</c:p><c:p bad="x">bad</c:p></c:c2d-update>',
    ]:
        issue = validate_update(xml).issues[0]
        assert issue.block_index == 1
        assert issue.line == 1
        assert issue.xpath


@pytest.mark.parametrize(
    "xml",
    [
        "",
        "<p>",
        "<p/><p/>",
        "```xml\n" + wrap("<p>x</p>") + "\n```",
        "Here is XML: " + wrap("<p>x</p>"),
        wrap("<p>x</p>") + " done",
        wrap("<p>&unknown;</p>"),
        wrap("<p>x</p>").replace(NS, "wrong"),
        wrap("<p>x</p>").replace('schema-version="0.1"', 'schema-version="0.2"'),
        wrap("<p>x</p>").replace('schema-version="0.1"', ""),
    ],
)
def test_bad_inputs(xml):
    assert not validate_update(xml).valid


def test_document_global_rules():
    assert validate_update(wrap("<title>A</title><title>B</title>")).valid
    for body in ["<title>A</title><title>B</title>", "<p>x</p><title>A</title>"]:
        result = validate_document(wrap(body, "document"))
        assert not result.valid
        assert all(issue.block_index is None for issue in result.issues)
    assert not validate_document(wrap("<p>x</p>")).valid
    assert not validate_update(wrap("<p>x</p>", "document")).valid
    assert not validate_update(wrap("")).valid
    assert validate_document(wrap("", "document")).valid


@pytest.mark.parametrize(
    "prefix",
    [
        "<!DOCTYPE c2d-update>",
        '<!DOCTYPE c2d-update [<!ENTITY x "expanded">]>',
        '<!DOCTYPE c2d-update SYSTEM "https://127.0.0.1:1/not-accessed">',
        '<!DOCTYPE c2d-update [<!ENTITY % x SYSTEM "file:///not-accessed">%x;]>',
    ],
)
def test_doctype_forbidden(prefix):
    result = validate_update(prefix + wrap("<p>x</p>"))
    assert not result.valid


def test_external_entity_never_expanded(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET_MUST_NOT_APPEAR")
    xml = f'<!DOCTYPE c2d-update [<!ENTITY x SYSTEM "{secret.as_uri()}">]>' + wrap(
        "<p>&x;</p>"
    )
    for data in [xml, xml.encode("utf-16")]:
        result = validate_update(data)
        assert not result.valid
        assert "SECRET_MUST_NOT_APPEAR" not in repr(result)


@pytest.mark.parametrize(
    "xml",
    [
        "<!--outside-->" + wrap("<p>x</p>"),
        wrap("<p>x</p><!--inside-->"),
        wrap("<p>x</p>") + "<?instruction forbidden?>",
    ],
)
def test_non_content_nodes(xml):
    assert not validate_update(xml).valid


def test_independent_concurrent_error_logs():
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(validate_update, [wrap("<p>x</p>"), wrap("<bad/>")] * 20)
        )
    assert [r.valid for r in results] == [True, False] * 20
