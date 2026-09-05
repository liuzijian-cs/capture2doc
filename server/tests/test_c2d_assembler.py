from lxml import etree
import pytest

from capture2doc.formats.c2d_xml import (
    C2DAssembler,
    C2DAssemblyError,
    validate_document,
)

NS = "urn:capture2doc:c2d:1"


def update(body):
    return f'<c2d-update xmlns="{NS}" schema-version="0.1">{body}</c2d-update>'


def make_assembler(body="<p>A</p><p>B</p><p>C</p><p>D</p>"):
    assembler = C2DAssembler(lang="zh-CN")
    assert assembler.apply_update(update(body)).valid
    return assembler


def contents(blocks):
    return ["".join(etree.fromstring(block).itertext()) for block in blocks]


def test_sequential_tail_replacement_and_prefix_preservation():
    assembler = make_assembler("<p>A <b>B</b> C</p>\n<p>unfinished</p>")
    assert assembler.apply_update(
        update("<h2>corrected classification</h2><p>new tail</p>")
    ).valid
    assert assembler.apply_update(update("<p>continued tail</p>")).valid
    root = etree.fromstring(assembler.finalize())
    assert [etree.QName(n).localname for n in root] == ["p", "h2", "p"]
    assert root[0].text == "A " and root[0][0].tail == " C"
    assert root[0].tail == "\n"
    assert root[-1].text == "continued tail"
    assert root.get("{http://www.w3.org/XML/1998/namespace}lang") == "zh-CN"


@pytest.mark.parametrize(
    "xml",
    [
        update("<p>missing close"),
        update("<title>late title</title>"),
        update('<table><tbody><tr><td rowspan="2">bad</td></tr></tbody></table>'),
        '<!DOCTYPE c2d-update [<!ENTITY x SYSTEM "file:///not-read">]>'
        + update("<p>&x;</p>"),
        update("<p>x</p>") + "<?forbidden x?>",
        update(""),
    ],
)
def test_failed_round_preserves_document_and_context(xml):
    assembler = make_assembler()
    before = assembler.finalize()
    context = assembler.context_blocks(count_tokens=lambda text: 1)
    result = assembler.apply_update(xml)
    assert not result.valid
    assert assembler.finalize() == before
    assert assembler.context_blocks(count_tokens=lambda text: 1) == context


def test_bad_first_round_and_global_error_locations():
    assembler = C2DAssembler()
    result = assembler.apply_update(update("<title>A</title><title>B</title>"))
    assert not result.valid
    assert result.issues[0].xpath
    assert result.issues[0].block_index is None
    with pytest.raises(C2DAssemblyError) as exc:
        assembler.finalize()
    assert exc.value.issues[0].code == "EMPTY_DOCUMENT"
    result = assembler.apply_update(update('<p>ok</p><p invalid="x">bad</p>'))
    assert result.issues[0].block_index == 1
    assert assembler.apply_update(update("<title>A</title><p>text</p>")).valid


@pytest.mark.parametrize("lang", ["not a language", "", 3, "zh\x00CN"])
def test_bad_language(lang):
    with pytest.raises(ValueError):
        C2DAssembler(lang=lang)


def test_repeat_finalize_and_no_pretty_print_or_content_cleanup():
    assembler = make_assembler(
        '<pre lang="python"><code>  x = 1\n\n    print(x)\n</code></pre>'
        '<p><a href="https://example.com">link</a> &amp; <latex>x &lt; y</latex></p>'
    )
    first = assembler.finalize()
    assert first == assembler.finalize()
    assert first.startswith(b"<?xml")
    assert validate_document(first).valid
    assert etree.fromstring(first)[0][0].text == "  x = 1\n\n    print(x)\n"
    assert assembler.apply_update(update("<p>next</p>")).valid


@pytest.mark.parametrize("number", [0, 1, 2, 3, 4])
def test_context_count_order_and_standalone_xml(number):
    assembler = C2DAssembler()
    if number:
        assert assembler.apply_update(
            update("".join(f"<p>{n}</p>" for n in range(number)))
        ).valid
    blocks = assembler.context_blocks(count_tokens=lambda text: 1)
    assert contents(blocks) == [str(n) for n in range(max(0, number - 3), number)]
    assert all(etree.fromstring(block).tag == f"{{{NS}}}p" for block in blocks)


@pytest.mark.parametrize(
    "tail,three,two,expected",
    [
        (768, 1536, 1000, ["B", "C", "D"]),
        (769, 1000, 900, ["D"]),
        (768, 1537, 1536, ["C", "D"]),
        (768, 1537, 1537, ["D"]),
        (1536, 9000, 9000, ["D"]),
    ],
)
def test_context_token_boundaries(tail, three, two, expected):
    assembler = make_assembler()
    seen = []

    def count(text):
        seen.append(text)
        return {1: tail, 2: two, 3: three}[text.count("</p>")]

    blocks = assembler.context_blocks(count_tokens=count)
    assert contents(blocks) == expected
    assert b"\n".join(blocks).decode() in seen
    if tail > 768:
        assert len(seen) == 1


def test_counts_joined_xml_including_markup_and_namespace():
    assembler = make_assembler("<p>A</p>\n  <p>B</p>\n  <p>C</p>\n")
    seen = []

    def count(text):
        seen.append(text)
        return 10 if text.count("</p>") == 1 else 2000

    blocks = assembler.context_blocks(count_tokens=count)
    assert contents(blocks) == ["C"]
    assert 'xmlns="' + NS + '"' in seen[0]
    assert "</p>\n<p" in seen[1]
    assert not blocks[0].endswith(b"\n")
    assert len(seen) == 3


@pytest.mark.parametrize("budget,tail", [(1536, 1537), (9999, 1537), (10, 11), (0, 1)])
def test_context_overflow_is_explicit_and_nonmutating(budget, tail):
    assembler = make_assembler()
    before = assembler.finalize()
    with pytest.raises(C2DAssemblyError) as exc:
        assembler.context_blocks(count_tokens=lambda text: tail, token_budget=budget)
    assert exc.value.issues[0].code == "CONTEXT_BUDGET_EXCEEDED"
    assert assembler.finalize() == before


def test_smaller_dynamic_budget():
    assembler = make_assembler()
    blocks = assembler.context_blocks(
        count_tokens=lambda text: text.count("</p>") * 100, token_budget=250
    )
    assert contents(blocks) == ["C", "D"]


@pytest.mark.parametrize("budget", [-1, 1.5, True])
def test_invalid_budget(budget):
    with pytest.raises(ValueError):
        make_assembler().context_blocks(
            count_tokens=lambda text: 1, token_budget=budget
        )


@pytest.mark.parametrize("count", [-1, 1.5, True])
def test_invalid_token_counter_result(count):
    with pytest.raises(ValueError):
        make_assembler().context_blocks(count_tokens=lambda text: count)


def test_empty_history_does_not_invoke_counter():
    def unavailable(text):
        raise AssertionError("must not tokenize empty history")

    assert C2DAssembler().context_blocks(count_tokens=unavailable, token_budget=0) == ()


@pytest.mark.parametrize(
    "body,next_body",
    [
        ("<ul><li>A</li></ul>", "<ul><li>A</li><li>B<ol><li>C</li></ol></li></ul>"),
        (
            "<table><tbody><tr><td>A</td></tr></tbody></table>",
            '<table><tbody><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></tbody></table>',
        ),
    ],
)
def test_complete_list_and_table_replacement(body, next_body):
    assembler = make_assembler("<p>prefix</p>" + body)
    assert assembler.apply_update(update(next_body)).valid
    assert len(etree.fromstring(assembler.finalize())) == 2
    assert validate_document(assembler.finalize()).valid
