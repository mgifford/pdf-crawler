"""Tests for scripts/pdf_spider.py."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def _make_spider(output_dir):
    """Return a PdfA11ySpider instance pointed at *output_dir*."""
    from pdf_spider import PdfA11ySpider

    # Construct the spider with __init__ so Scrapy's internal state
    # (including the read-only `logger` property) is properly set up.
    spider = PdfA11ySpider(
        url="https://example.com",
        output_dir=str(output_dir),
    )
    return spider


def _make_response(url, body=b"%PDF-1.4 fake"):
    """Return a minimal mock Scrapy response with *url* and *body*."""
    resp = MagicMock()
    resp.url = url
    resp.body = body
    return resp


# ---------------------------------------------------------------------------
# save_pdf – filename must never contain characters from the query string
# ---------------------------------------------------------------------------


def test_save_pdf_strips_query_string(tmp_path):
    """A URL with ?VersionId=... must produce a filename without '?'."""
    spider = _make_spider(tmp_path)
    url = "https://example.com/15_fy2023_0.pdf?VersionId=LV1fEl0ZGY1fu6I4LU2fFmTv1WMLTVex"
    spider.save_pdf(_make_response(url))

    site_dir = tmp_path / "example.com"
    saved_files = [f for f in site_dir.iterdir() if f.name != "_url_map.json"]
    assert len(saved_files) == 1
    assert "?" not in saved_files[0].name
    assert saved_files[0].name == "15_fy2023_0.pdf"


def test_save_pdf_strips_multiple_query_params(tmp_path):
    """Multiple query parameters should all be stripped from the filename."""
    spider = _make_spider(tmp_path)
    url = "https://example.com/report.pdf?token=abc&v=2&lang=en"
    spider.save_pdf(_make_response(url))

    site_dir = tmp_path / "example.com"
    saved_files = [f for f in site_dir.iterdir() if f.name != "_url_map.json"]
    assert len(saved_files) == 1
    assert "?" not in saved_files[0].name
    assert "&" not in saved_files[0].name
    assert saved_files[0].name == "report.pdf"


def test_save_pdf_plain_url_unchanged(tmp_path):
    """A URL without query params should produce the expected plain filename."""
    spider = _make_spider(tmp_path)
    url = "https://example.com/docs/annual-report.pdf"
    spider.save_pdf(_make_response(url))

    site_dir = tmp_path / "example.com"
    saved_files = [f for f in site_dir.iterdir() if f.name != "_url_map.json"]
    assert len(saved_files) == 1
    assert saved_files[0].name == "annual-report.pdf"


def test_save_pdf_url_map_records_original_url(tmp_path):
    """The _url_map.json entry must store the *original* URL (with query string)."""
    spider = _make_spider(tmp_path)
    url = "https://example.com/data.pdf?VersionId=xyz"
    spider.save_pdf(_make_response(url))

    save_dir = str(tmp_path / "example.com")
    # The map key is the sanitized filename, the value is the original full URL
    url_map = spider._url_maps.get(save_dir, {})
    assert "data.pdf" in url_map
    assert url_map["data.pdf"] == url


def test_save_pdf_file_content_preserved(tmp_path):
    """File body must be written correctly even when the URL has a query string."""
    spider = _make_spider(tmp_path)
    body = b"%PDF-1.4 real content here"
    url = "https://example.com/doc.pdf?v=1"
    spider.save_pdf(_make_response(url, body=body))

    site_dir = tmp_path / "example.com"
    saved_files = [f for f in site_dir.iterdir() if f.name != "_url_map.json"]
    assert len(saved_files) == 1
    assert saved_files[0].read_bytes() == body


# ---------------------------------------------------------------------------
# Crawl tracking – _crawled_pages, _referer_maps, and closed() output files
# ---------------------------------------------------------------------------


def test_save_pdf_records_referer(tmp_path):
    """The _referer_maps entry must store the page that linked to the PDF."""
    spider = _make_spider(tmp_path)
    url = "https://example.com/report.pdf"
    referer = "https://example.com/reports/"
    spider.save_pdf(_make_response(url), referer=referer)

    save_dir = str(tmp_path / "example.com")
    referer_map = spider._referer_maps.get(save_dir, {})
    assert "report.pdf" in referer_map
    assert referer_map["report.pdf"] == referer


def test_save_pdf_empty_referer_by_default(tmp_path):
    """save_pdf called without a referer must store an empty string."""
    spider = _make_spider(tmp_path)
    spider.save_pdf(_make_response("https://example.com/doc.pdf"))

    save_dir = str(tmp_path / "example.com")
    referer_map = spider._referer_maps.get(save_dir, {})
    assert referer_map.get("doc.pdf", "") == ""


def test_closed_writes_referer_map(tmp_path):
    """closed() must write _referer_map.json alongside _url_map.json."""
    spider = _make_spider(tmp_path)
    referer = "https://example.com/index"
    spider.save_pdf(_make_response("https://example.com/a.pdf"), referer=referer)
    spider.closed("finished")

    import json
    referer_map_path = tmp_path / "example.com" / "_referer_map.json"
    assert referer_map_path.exists()
    data = json.loads(referer_map_path.read_text(encoding="utf-8"))
    assert data.get("a.pdf") == referer


def test_closed_writes_crawled_pages(tmp_path):
    """closed() must write _crawled_pages.json with all visited page URLs."""
    spider = _make_spider(tmp_path)
    spider.save_pdf(_make_response("https://example.com/b.pdf"))
    spider._crawled_pages = [
        "https://example.com/",
        "https://example.com/about",
    ]
    spider.closed("finished")

    import json
    pages_path = tmp_path / "example.com" / "_crawled_pages.json"
    assert pages_path.exists()
    data = json.loads(pages_path.read_text(encoding="utf-8"))
    assert data == ["https://example.com/", "https://example.com/about"]


def test_closed_writes_crawled_pages_no_pdfs(tmp_path):
    """closed() must write _crawled_pages.json even when no PDFs were found."""
    spider = _make_spider(tmp_path)
    spider._crawled_pages = ["https://example.com/"]
    spider.closed("finished")

    import json
    pages_path = tmp_path / "example.com" / "_crawled_pages.json"
    assert pages_path.exists()
    data = json.loads(pages_path.read_text(encoding="utf-8"))
    assert data == ["https://example.com/"]


# ---------------------------------------------------------------------------
# DOWNLOAD_EXTENSIONS – only PDF files should be downloaded
# ---------------------------------------------------------------------------


def test_download_extensions_only_pdf():
    """DOWNLOAD_EXTENSIONS must contain only .pdf so that non-PDF files are never downloaded."""
    from pdf_spider import PdfA11ySpider

    assert PdfA11ySpider.DOWNLOAD_EXTENSIONS == {".pdf"}


def test_has_download_extension_accepts_pdf():
    """_has_download_extension must return True for .pdf URLs."""
    spider = _make_spider("/tmp")
    assert spider._has_download_extension("file.pdf")
    assert spider._has_download_extension("FILE.PDF")
    assert spider._has_download_extension("report.Pdf")


def test_has_download_extension_rejects_non_pdf():
    """_has_download_extension must return False for non-PDF extensions."""
    spider = _make_spider("/tmp")
    for filename in [
        "table.xlsx",
        "report.docx",
        "slides.pptx",
        "data.xls",
        "doc.doc",
        "book.epub",
        "sheet.ods",
    ]:
        assert not spider._has_download_extension(filename), (
            f"Expected {filename} to be rejected but it was accepted"
        )


# ---------------------------------------------------------------------------
# User-Agent – spider must use a browser-like UA, not Scrapy's default
# ---------------------------------------------------------------------------


def test_user_agent_is_set():
    """custom_settings must include a USER_AGENT entry."""
    from pdf_spider import PdfA11ySpider

    assert "USER_AGENT" in PdfA11ySpider.custom_settings


def test_user_agent_is_browser_like():
    """USER_AGENT must look like a browser, not the default Scrapy bot string."""
    from pdf_spider import PdfA11ySpider

    ua = PdfA11ySpider.custom_settings["USER_AGENT"]
    # Must contain Mozilla/ to resemble a real browser User-Agent header.
    assert "Mozilla/" in ua, f"USER_AGENT does not look browser-like: {ua!r}"
    # Must not expose the Scrapy identity which is commonly blocked by WAFs.
    assert "Scrapy" not in ua, f"USER_AGENT still contains 'Scrapy': {ua!r}"


def test_default_request_headers_accept():
    """DEFAULT_REQUEST_HEADERS must include an Accept header."""
    from pdf_spider import PdfA11ySpider

    headers = PdfA11ySpider.custom_settings.get("DEFAULT_REQUEST_HEADERS", {})
    assert "Accept" in headers, "DEFAULT_REQUEST_HEADERS is missing the Accept key"


# ---------------------------------------------------------------------------
# start_requests / handle_error – errback wiring
# ---------------------------------------------------------------------------


def test_start_requests_yields_request_with_errback():
    """start_requests() must produce a Scrapy Request that has an errback set."""
    spider = _make_spider("/tmp")
    requests = list(spider.start_requests())
    assert len(requests) == 1
    req = requests[0]
    assert req.errback is not None, "start_requests() request must have an errback"
    assert req.errback == spider.handle_error


def test_handle_error_logs_url(capsys):
    """handle_error() must print the failing URL to stdout."""
    spider = _make_spider("/tmp")

    failure = MagicMock()
    failure.request.url = "https://example.com/blocked.pdf"
    failure.value = ConnectionError("Connection refused")

    spider.handle_error(failure)

    captured = capsys.readouterr()
    assert "https://example.com/blocked.pdf" in captured.out
