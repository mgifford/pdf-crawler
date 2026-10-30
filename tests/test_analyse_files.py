"""Tests for scripts/analyse_files.py: analysing given files without a manifest."""
# pylint: disable=import-outside-toplevel,protected-access,unbalanced-tuple-unpacking

import json
import sys
from pathlib import Path

import pikepdf

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import analyse_files  # noqa: E402
from analyse_files import analyse_files as run, main  # noqa: E402


def _pdf(path: Path, title=None) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    if title:
        with pdf.open_metadata() as meta:
            meta["dc:title"] = title
        pdf.Root.ViewerPreferences = pikepdf.Dictionary(DisplayDocTitle=True)
    pdf.save(path)
    return path


def test_analyses_a_real_pdf_with_the_same_checks_as_the_crawl(tmp_path):
    path = _pdf(tmp_path / "annual-report.pdf", title="Annual report")
    [result] = run([{"path": str(path), "url": "https://example.gov.uk/annual-report.pdf", "site": "example.gov.uk"}])
    assert result["status"] == "analysed"
    assert result["url"] == "https://example.gov.uk/annual-report.pdf"
    report = result["report"]
    assert report["File"] == "annual-report.pdf"
    assert report["TitleTest"] == "Pass"
    assert report["TaggedTest"] == "Fail", "a blank untagged page is not tagged"
    assert report["Accessible"] is False
    assert "_log" not in report, "the analyser's notes move to errors"
    assert result["seconds"] >= 0


def test_files_that_cannot_be_analysed_say_why_and_keep_their_order(tmp_path):
    html = tmp_path / "error-page.pdf"
    html.write_text("<!DOCTYPE html><title>Not found</title>", encoding="utf-8")
    ok = _pdf(tmp_path / "ok.pdf")
    results = run([{"path": str(tmp_path / "gone.pdf")}, {"path": str(html)}, {"path": str(ok)}])
    assert [r["status"] for r in results] == ["error", "error", "analysed"]
    assert results[0]["errors"] == [f"File not found: {tmp_path / 'gone.pdf'}"]
    assert results[0]["url"] == str(tmp_path / "gone.pdf"), "the path stands in for a missing url"
    assert results[1]["errors"] == ["Not a PDF file: no %PDF- header"]

    [big] = run([{"path": str(ok)}], max_file_size_mb=1e-6)
    assert big["status"] == "error"
    assert big["errors"][0].startswith("File too large to analyse:")


def test_a_timeout_is_an_error_not_a_crash(tmp_path, monkeypatch):
    def too_slow(*_args, **_kwargs):
        raise TimeoutError("Analysis exceeded 1s per-file limit")

    monkeypatch.setattr(analyse_files, "_analyse_with_process_timeout", too_slow)
    [result] = run([{"path": str(_pdf(tmp_path / "slow.pdf"))}], per_file_timeout=1)
    assert result == {**result, "status": "error", "report": None, "errors": ["Analysis exceeded 1s per-file limit"]}


def test_cli_reads_a_list_and_writes_results(tmp_path, capsys):
    path = _pdf(tmp_path / "plan.pdf", title="Local plan")
    listing = tmp_path / "files.json"
    listing.write_text(json.dumps([{"path": str(path), "url": "https://example.gov.uk/plan.pdf"}]), encoding="utf-8")
    out = tmp_path / "results.json"
    assert main(["--input", str(listing), "--output", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["engine"] == "original"
    assert [r["url"] for r in data["results"]] == ["https://example.gov.uk/plan.pdf"]
    assert data["results"][0]["report"]["TitleTest"] == "Pass"
    assert "Analysed 1 of 1 file(s)" in capsys.readouterr().err
