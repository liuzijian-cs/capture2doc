import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from lxml import etree
import pytest

from capture2doc.formats.c2d_xml import validate_document

SERVER = Path(__file__).resolve().parents[1]
SCRIPT = SERVER / "scripts/assemble_c2d_xml.py"
FIXTURES = SERVER / "tests/fixtures/c2d_xml/assembly"
UPDATES = [
    FIXTURES / name
    for name in (
        "01_paragraph.xml",
        "02_list.xml",
        "03_table.xml",
        "04_finish.xml",
    )
]

spec = importlib.util.spec_from_file_location("assemble_c2d_xml", SCRIPT)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def args(output, updates=UPDATES):
    return ["--updates", *map(str, updates), "--output", str(output), "--lang", "zh-CN"]


def normalized(xml):
    return etree.tostring(
        etree.fromstring(xml, etree.XMLParser(remove_blank_text=True))
    )


def test_offline_subprocess_export_matches_expected(tmp_path):
    output = tmp_path / "document.c2d.xml"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args(output)],
        cwd=SERVER,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert validate_document(output.read_bytes()).valid
    assert normalized(output.read_bytes()) == normalized(
        (FIXTURES / "expected.c2d.xml").read_bytes()
    )
    assert list(tmp_path.iterdir()) == [output]


def test_argument_order_not_filename_order(tmp_path):
    first = tmp_path / "z.xml"
    second = tmp_path / "a.xml"
    first.write_text(
        '<c2d-update xmlns="urn:capture2doc:c2d:1" schema-version="0.1"><p>old</p></c2d-update>'
    )
    second.write_text(
        '<c2d-update xmlns="urn:capture2doc:c2d:1" schema-version="0.1"><p>new</p></c2d-update>'
    )
    output = tmp_path / "result.xml"
    assert cli.main(args(output, [first, second])) == 0
    assert etree.fromstring(output.read_bytes())[0].text == "new"


def test_bad_round_stops_without_output(tmp_path, capsys):
    bad = tmp_path / "bad.xml"
    bad.write_text(
        '<c2d-update xmlns="urn:capture2doc:c2d:1" schema-version="0.1"><img/></c2d-update>'
    )
    output = tmp_path / "result.xml"
    assert cli.main(args(output, [UPDATES[0], bad, UPDATES[1]])) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["file"] == str(bad)
    assert error["round"] == 2
    assert error["issues"][0]["block_index"] == 0
    assert list(tmp_path.iterdir()) == [bad]


@pytest.mark.parametrize("kind", ["file", "dangling_symlink"])
def test_existing_destination_untouched(tmp_path, kind):
    output = tmp_path / "result.xml"
    if kind == "file":
        output.write_bytes(b"original")
    else:
        output.symlink_to(tmp_path / "missing-target")
    assert cli.main(args(output)) == 1
    if kind == "file":
        assert output.read_bytes() == b"original"
    else:
        assert output.is_symlink()
    assert list(tmp_path.iterdir()) == [output]


def test_publish_race_does_not_overwrite(tmp_path, monkeypatch):
    output = tmp_path / "result.xml"
    original_link = cli.os.link

    def competing_writer(source, destination):
        destination.write_bytes(b"concurrent writer")
        original_link(source, destination)

    monkeypatch.setattr(cli.os, "link", competing_writer)
    assert cli.main(args(output)) == 1
    assert output.read_bytes() == b"concurrent writer"
    assert list(tmp_path.iterdir()) == [output]


def test_write_failure_cleans_temporary_file(tmp_path, monkeypatch):
    def disk_failure(fd):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(cli.os, "fsync", disk_failure)
    assert cli.main(args(tmp_path / "result.xml")) == 1
    assert list(tmp_path.iterdir()) == []


def test_missing_input_reports_round(tmp_path, capsys):
    missing = tmp_path / "missing.xml"
    assert cli.main(args(tmp_path / "result.xml", [UPDATES[0], missing])) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["round"] == 2 and error["file"] == str(missing)
    assert list(tmp_path.iterdir()) == []


def test_missing_parent_is_not_created(tmp_path):
    output = tmp_path / "absent/result.xml"
    assert cli.main(args(output)) == 1
    assert list(tmp_path.iterdir()) == []


def test_language_failure_does_not_write(tmp_path, capsys):
    argv = args(tmp_path / "result.xml")
    argv[-1] = "not a language"
    assert cli.main(argv) == 1
    assert json.loads(capsys.readouterr().err)["issues"]
    assert list(tmp_path.iterdir()) == []


def test_usage_error_has_exit_code_two():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
