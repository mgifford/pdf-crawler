"""Tests for verbose output added to crawl.py and pdf_analyser.py."""

import sys
import types
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from manifest import build_entry, save_manifest


# ---------------------------------------------------------------------------
# crawl.py – update_manifest verbose output
# ---------------------------------------------------------------------------

def test_update_manifest_prints_each_file(tmp_path, capsys):
    """update_manifest should print a line for every file it processes."""
    from crawl import update_manifest

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    (site_dir / "doc1.pdf").write_bytes(b"%PDF fake1")
    (site_dir / "doc2.pdf").write_bytes(b"%PDF fake2")

    manifest_path = tmp_path / "manifest.yaml"

    update_manifest(f"https://{site}", str(output_dir), str(manifest_path))

    captured = capsys.readouterr()
    assert f"https://{site}/doc1.pdf" in captured.out
    assert f"https://{site}/doc2.pdf" in captured.out
    assert "Processing:" in captured.out


def test_update_manifest_no_dir_prints_message(tmp_path, capsys):
    """update_manifest should print a message when no files are found."""
    from crawl import update_manifest

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest("https://example.com", str(tmp_path / "empty_output"), str(manifest_path))

    captured = capsys.readouterr()
    assert "No files found" in captured.out


# ---------------------------------------------------------------------------
# pdf_analyser.py – main() verbose output
# (Heavy dependencies are mocked at the sys.modules level so the module can
# be imported without requiring pikepdf, dateparser, etc.)
# ---------------------------------------------------------------------------

def _stub_analyser_deps():
    """Insert stub modules so pdf_analyser can be imported in the test env."""
    stubs = {
        "pikepdf": MagicMock(),
        "pikepdf.qpdf": MagicMock(),
        "pikepdf.models": MagicMock(),
        "pikepdf.models.metadata": MagicMock(),
        "dateparser": MagicMock(),
        "bitstring": MagicMock(),
        "langcodes": MagicMock(),
        "pytz": MagicMock(),
    }
    for name, stub in stubs.items():
        sys.modules.setdefault(name, stub)
    return stubs


def _fake_entry(url, filename, site="example.com"):
    return {
        "url": url,
        "filename": filename,
        "site": site,
        "status": "pending",
        "md5": "abc123",
        "crawled_at": "2024-01-01T00:00:00+00:00",
        "report": None,
        "errors": [],
    }


def _fake_report():
    return {
        "Accessible": True,
        "TotallyInaccessible": False,
        "BrokenFile": False,
        "TaggedTest": "Pass",
        "EmptyTextTest": "Pass",
        "ProtectedTest": "Pass",
        "TitleTest": "Pass",
        "LanguageTest": "Pass",
        "BookmarksTest": "Pass",
        "Exempt": False,
        "Date": None,
        "hasTitle": True,
        "hasDisplayDocTitle": True,
        "hasLang": True,
        "InvalidLang": None,
        "Form": None,
        "xfa": None,
        "hasBookmarks": True,
        "hasXmp": True,
        "PDFVersion": "1.4",
        "Creator": None,
        "Producer": None,
        "Pages": 1,
        "_log": "",
    }


@pytest.fixture()
def analyser_module():
    """Import pdf_analyser with heavy dependencies stubbed out."""
    _stub_analyser_deps()
    # Remove cached module so fresh import picks up stubs
    sys.modules.pop("pdf_analyser", None)
    import pdf_analyser
    yield pdf_analyser
    sys.modules.pop("pdf_analyser", None)


@pytest.fixture()
def analyser_with_pending_pdf(tmp_path, analyser_module):
    """Set up a manifest with one pending entry and a corresponding fake PDF file."""
    site = "example.com"
    crawled_dir = tmp_path / "crawled_files"
    site_dir = crawled_dir / site
    site_dir.mkdir(parents=True)

    url = f"https://{site}/report.pdf"
    filename = "report.pdf"
    (site_dir / filename).write_bytes(b"%PDF fake content")

    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([_fake_entry(url, filename, site)], manifest_path)

    return {
        "url": url,
        "filename": filename,
        "site": site,
        "crawled_dir": str(crawled_dir),
        "manifest_path": str(manifest_path),
    }


def test_analyser_prints_url_being_checked(capsys, analyser_module, analyser_with_pending_pdf):
    """pdf_analyser main() should print the URL of each file being checked."""
    ctx = analyser_with_pending_pdf
    with patch.object(analyser_module, "check_file", return_value=_fake_report()):
        analyser_module.main(
            manifest_path=ctx["manifest_path"],
            crawled_dir=ctx["crawled_dir"],
            keep_files=True,
        )

    captured = capsys.readouterr()
    assert ctx["url"] in captured.out
    assert "Checking:" in captured.out


def test_analyser_prints_local_file_path(capsys, analyser_module, analyser_with_pending_pdf):
    """pdf_analyser main() should also print the local file path."""
    ctx = analyser_with_pending_pdf
    with patch.object(analyser_module, "check_file", return_value=_fake_report()):
        analyser_module.main(
            manifest_path=ctx["manifest_path"],
            crawled_dir=ctx["crawled_dir"],
            keep_files=True,
        )

    captured = capsys.readouterr()
    assert ctx["filename"] in captured.out
    assert "File:" in captured.out


def test_analyser_prints_skip_for_missing_file(tmp_path, capsys, analyser_module):
    """pdf_analyser main() should print SKIP when the local file does not exist."""
    site = "example.com"
    crawled_dir = tmp_path / "crawled_files"
    (crawled_dir / site).mkdir(parents=True)

    url = f"https://{site}/missing.pdf"
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([_fake_entry(url, "missing.pdf", site)], manifest_path)

    analyser_module.main(
        manifest_path=str(manifest_path),
        crawled_dir=str(crawled_dir),
        keep_files=True,
    )

    captured = capsys.readouterr()
    assert "SKIP" in captured.out


# ---------------------------------------------------------------------------
# pdf_spider.py – parse() and save_pdf() verbose output
# ---------------------------------------------------------------------------

def test_spider_parse_prints_crawled_url(capsys):
    """PdfA11ySpider.parse() should print the URL currently being crawled."""
    from pdf_spider import PdfA11ySpider
    import urllib.parse

    spider = PdfA11ySpider.__new__(PdfA11ySpider)
    spider.url = "https://example.com"
    spider.output_dir = "/tmp/out"
    spider.parsed_url = urllib.parse.urlparse("https://example.com")
    spider.allowed_domains = ["example.com"]
    spider.start_urls = ["https://example.com"]

    from scrapy.http import HtmlResponse
    response = HtmlResponse(
        url="https://example.com/page",
        body=b"<html><body><a href='/about'>about</a></body></html>",
        encoding="utf-8",
    )

    list(spider.parse(response))  # consume the generator

    captured = capsys.readouterr()
    assert "Crawling: https://example.com/page" in captured.out


def test_spider_parse_prints_found_pdf(capsys):
    """PdfA11ySpider.parse() should print each PDF link it finds."""
    from pdf_spider import PdfA11ySpider
    import urllib.parse

    spider = PdfA11ySpider.__new__(PdfA11ySpider)
    spider.url = "https://example.com"
    spider.output_dir = "/tmp/out"
    spider.parsed_url = urllib.parse.urlparse("https://example.com")
    spider.allowed_domains = ["example.com"]
    spider.start_urls = ["https://example.com"]

    from scrapy.http import HtmlResponse
    response = HtmlResponse(
        url="https://example.com/page",
        body=b"<html><body><a href='/files/doc.pdf'>PDF</a></body></html>",
        encoding="utf-8",
    )

    list(spider.parse(response))

    captured = capsys.readouterr()
    assert "Found for download" in captured.out
    assert "doc.pdf" in captured.out

