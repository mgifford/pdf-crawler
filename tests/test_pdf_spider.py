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
