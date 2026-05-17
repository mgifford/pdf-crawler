"""Tests for scripts/crawl.py – focused on normalize_url(), _site_folder(), and run_scrapy()."""
# pylint: disable=import-outside-toplevel,unused-argument,unused-import
# pylint: disable=trailing-newlines,use-implicit-booleaness-not-comparison

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from crawl import (
    normalize_url,
    _URL_PREFIXES,
    _site_folder,
    run_scrapy,
    _print_scrapy_log_tail,
    is_pdf_url,
    _broaden_seed_urls,
    _count_downloaded_pdfs,
    spot_check_zero_results,
    _extract_sitemap_urls_from_robots,
    _candidate_sitemap_urls,
    _extract_pdf_urls_from_sitemap,
    _collect_sitemap_pdf_urls,
    fetch_sitemap_pdfs,
    _extract_pdf_urls_from_duckduckgo,
    fetch_duckduckgo_pdfs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status: int):
    """Return a mock urllib response-like object with the given status."""
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# normalize_url – URLs that already have a protocol
# ---------------------------------------------------------------------------

def test_already_https_returned_unchanged():
    url = "https://example.com/path"
    assert normalize_url(url) == url


def test_already_http_returned_unchanged():
    url = "http://example.com/path"
    assert normalize_url(url) == url


def test_https_www_returned_unchanged():
    url = "https://www.example.com"
    assert normalize_url(url) == url


# ---------------------------------------------------------------------------
# normalize_url – bare domains (no protocol)
# ---------------------------------------------------------------------------

def test_bare_domain_resolves_to_https_when_reachable():
    """When https://domain responds 200 it should be chosen first."""
    with patch("crawl.urlopen") as mock_open:
        mock_open.return_value = _make_response(200)
        result = normalize_url("example.com")
    assert result == "https://example.com"
    mock_open.assert_called_once_with("https://example.com", timeout=15)


def test_bare_domain_falls_through_to_https_www():
    """https://domain fails → https://www.domain succeeds."""
    from urllib.error import URLError

    responses = [URLError("connection refused"), _make_response(200)]
    call_count = 0

    def side_effect(url, timeout):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        if isinstance(resp, URLError):
            raise resp
        return resp

    with patch("crawl.urlopen", side_effect=side_effect):
        result = normalize_url("example.com")

    assert result == "https://www.example.com"


def test_bare_domain_falls_through_to_http():
    """https variants fail → http://domain succeeds."""
    from urllib.error import URLError

    responses = [
        URLError("fail"),      # https://
        URLError("fail"),      # https://www.
        _make_response(200),   # http://
    ]
    idx = 0

    def side_effect(url, timeout):
        nonlocal idx
        resp = responses[idx]
        idx += 1
        if isinstance(resp, URLError):
            raise resp
        return resp

    with patch("crawl.urlopen", side_effect=side_effect):
        result = normalize_url("example.com")

    assert result == "http://example.com"


def test_bare_domain_falls_through_to_http_www():
    """All variants except http://www. fail."""
    from urllib.error import URLError

    responses = [
        URLError("fail"),      # https://
        URLError("fail"),      # https://www.
        URLError("fail"),      # http://
        _make_response(200),   # http://www.
    ]
    idx = 0

    def side_effect(url, timeout):
        nonlocal idx
        resp = responses[idx]
        idx += 1
        if isinstance(resp, URLError):
            raise resp
        return resp

    with patch("crawl.urlopen", side_effect=side_effect):
        result = normalize_url("example.com")

    assert result == "http://www.example.com"


def test_bare_domain_fallback_when_all_fail():
    """When no variant responds, the https:// fallback is returned."""
    from urllib.error import URLError

    with patch("crawl.urlopen", side_effect=URLError("all fail")):
        result = normalize_url("example.com")

    assert result == "https://example.com"


def test_bare_domain_3xx_redirect_counts_as_success():
    """A 301/302 redirect response should be treated as success."""
    with patch("crawl.urlopen") as mock_open:
        mock_open.return_value = _make_response(301)
        result = normalize_url("example.com")
    assert result == "https://example.com"


def test_bare_domain_4xx_not_counted_as_success():
    """A 404 response should NOT be counted as a working URL; fall through."""
    from urllib.error import URLError

    responses = [
        _make_response(404),   # https:// responds but 404 ≠ success
        _make_response(200),   # https://www. succeeds
    ]
    idx = 0

    def side_effect(url, timeout):
        nonlocal idx
        resp = responses[idx]
        idx += 1
        return resp

    with patch("crawl.urlopen", side_effect=side_effect):
        result = normalize_url("example.com")

    assert result == "https://www.example.com"


def test_url_prefixes_order():
    """Verify that the probing order is https → https://www → http → http://www."""
    assert _URL_PREFIXES == [
        "https://",
        "https://www.",
        "http://",
        "http://www.",
    ]


def test_leading_slashes_stripped():
    """Bare domain with accidental leading slashes is handled gracefully."""
    with patch("crawl.urlopen") as mock_open:
        mock_open.return_value = _make_response(200)
        result = normalize_url("//example.com")
    assert result == "https://example.com"


# ---------------------------------------------------------------------------
# normalize_url – hostname case normalisation
# ---------------------------------------------------------------------------

def test_mixed_case_hostname_lowercased():
    """https:// URL with a mixed-case hostname is returned with the hostname lowercased."""
    result = normalize_url("https://www.Ontario.ca/page")
    assert result == "https://www.ontario.ca/page"


def test_uppercase_hostname_lowercased():
    """All-uppercase hostname is lowercased."""
    result = normalize_url("https://WWW.EXAMPLE.COM/path")
    assert result == "https://www.example.com/path"


def test_already_lowercase_https_unchanged():
    """A fully-lowercase https:// URL is returned as-is (no unnecessary rebuild)."""
    url = "https://www.example.com/path"
    assert normalize_url(url) == url


def test_http_mixed_case_hostname_lowercased():
    """http:// URL with mixed-case hostname is also normalised."""
    result = normalize_url("http://Example.COM/index.html")
    assert result == "http://example.com/index.html"


# ---------------------------------------------------------------------------
# _site_folder
# ---------------------------------------------------------------------------

def test_site_folder_strips_www_prefix():
    """www. prefix should be removed to produce a clean folder name."""
    assert _site_folder("www.ontario.ca") == "ontario.ca"


def test_site_folder_strips_www_from_mixed_case():
    """Mixed-case netloc is lowercased and www. is stripped."""
    assert _site_folder("www.Ontario.ca") == "ontario.ca"


def test_site_folder_no_www_prefix():
    """A netloc without www. is just lowercased."""
    assert _site_folder("docs.example.com") == "docs.example.com"


def test_site_folder_already_lowercase_no_www():
    """Already-clean netloc is returned unchanged."""
    assert _site_folder("example.com") == "example.com"


def test_site_folder_uppercase_no_www():
    """Uppercase netloc without www. is still lowercased."""
    assert _site_folder("EXAMPLE.COM") == "example.com"


# ---------------------------------------------------------------------------
# is_pdf_url
# ---------------------------------------------------------------------------

def test_is_pdf_url_direct_pdf():
    """A URL whose path ends with .pdf should be identified as a PDF URL."""
    assert is_pdf_url("https://example.com/document.pdf") is True


def test_is_pdf_url_pdf_with_query_string():
    """Query string after .pdf should not hide the PDF extension."""
    assert is_pdf_url("https://example.com/report.pdf?version=2") is True


def test_is_pdf_url_pdf_with_fragment():
    """Fragment after .pdf should not hide the PDF extension."""
    assert is_pdf_url("https://example.com/file.pdf#page=3") is True


def test_is_pdf_url_pdf_uppercase_extension():
    """PDF extension check should be case-insensitive."""
    assert is_pdf_url("https://example.com/REPORT.PDF") is True


def test_is_pdf_url_mixed_case_extension():
    """Mixed-case .Pdf extension should still be detected."""
    assert is_pdf_url("https://example.com/doc.Pdf") is True


def test_is_pdf_url_html_page():
    """A normal HTML page URL should not be identified as a PDF URL."""
    assert is_pdf_url("https://example.com/index.html") is False


def test_is_pdf_url_homepage():
    """A bare homepage URL should not be identified as a PDF URL."""
    assert is_pdf_url("https://example.com") is False


def test_is_pdf_url_homepage_with_path():
    """A deep page URL (non-PDF) should not be identified as a PDF URL."""
    assert is_pdf_url("https://example.com/reports/2024/index.aspx") is False


def test_is_pdf_url_pdf_in_path_segment():
    """A URL with 'pdf' in a path segment (not extension) is not a PDF URL."""
    assert is_pdf_url("https://example.com/pdf-reports/index.html") is False


# ---------------------------------------------------------------------------
# _broaden_seed_urls
# ---------------------------------------------------------------------------

def test_broaden_seed_urls_returns_parent_paths_then_root():
    """Path-scoped seeds should broaden to parent paths then domain root."""
    assert _broaden_seed_urls("https://example.com/programs/abc123") == [
        "https://example.com/programs",
        "https://example.com/",
    ]


def test_broaden_seed_urls_strips_query_and_fragment():
    """Broadened URLs should not keep query strings or fragments."""
    assert _broaden_seed_urls("https://example.com/a/b?x=1#sec") == [
        "https://example.com/a",
        "https://example.com/",
    ]


def test_broaden_seed_urls_root_returns_empty():
    """A root URL has no broader scope to try."""
    assert _broaden_seed_urls("https://example.com/") == []


# ---------------------------------------------------------------------------
# _count_downloaded_pdfs
# ---------------------------------------------------------------------------

def test_count_downloaded_pdfs_counts_only_pdf_files(tmp_path):
    """Only .pdf files (case-insensitive) should be counted."""
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "b.PDF").write_bytes(b"%PDF-1.4")
    (tmp_path / "c.txt").write_text("not pdf", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "inside.pdf").write_bytes(b"%PDF-1.4")
    assert _count_downloaded_pdfs(tmp_path) == 2


# ---------------------------------------------------------------------------
# sitemap helpers
# ---------------------------------------------------------------------------

def test_extract_sitemap_urls_from_robots():
    """robots.txt Sitemap directives should be parsed case-insensitively."""
    content = """
User-agent: *
Disallow:
Sitemap: https://example.com/sitemap.xml
sitemap: https://example.com/news-sitemap.xml
"""
    assert _extract_sitemap_urls_from_robots(content) == [
        "https://example.com/sitemap.xml",
        "https://example.com/news-sitemap.xml",
    ]


def test_candidate_sitemap_urls_includes_robots_entries():
    """Candidate sitemap URL list should include defaults plus robots sitemap URLs."""
    candidates = _candidate_sitemap_urls(
        "https://example.com/deep/path",
        extra_urls=["https://example.com/custom.xml"],
    )
    assert "https://example.com/sitemap.xml" in candidates
    assert "https://example.com/custom.xml" in candidates


# ---------------------------------------------------------------------------
# run_scrapy – max_pages / CLOSESPIDER_PAGECOUNT
# ---------------------------------------------------------------------------

def test_run_scrapy_passes_closespider_pagecount_default():
    """run_scrapy should pass CLOSESPIDER_PAGECOUNT=2500 by default."""
    with patch("crawl.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_scrapy("https://example.com", "out", 3600, "spider.py")
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "-s" in cmd
    idx = cmd.index("-s")
    assert cmd[idx + 1] == "CLOSESPIDER_PAGECOUNT=2500"


def test_run_scrapy_passes_custom_max_pages():
    """run_scrapy should pass the caller-supplied max_pages as CLOSESPIDER_PAGECOUNT."""
    with patch("crawl.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_scrapy("https://example.com", "out", 3600, "spider.py", max_pages=4000)
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "-s" in cmd
    idx = cmd.index("-s")
    assert cmd[idx + 1] == "CLOSESPIDER_PAGECOUNT=4000"


def test_run_scrapy_passes_max_pages_one():
    """run_scrapy should pass max_pages=1 correctly (boundary check)."""
    with patch("crawl.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_scrapy("https://example.com", "out", 3600, "spider.py", max_pages=1)
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "-s" in cmd
    idx = cmd.index("-s")
    assert cmd[idx + 1] == "CLOSESPIDER_PAGECOUNT=1"


# ---------------------------------------------------------------------------
# update_manifest – URL map (_url_map.json) tests
# ---------------------------------------------------------------------------

def test_update_manifest_uses_url_map_when_present(tmp_path):
    """update_manifest should use the real URL from _url_map.json."""
    from crawl import update_manifest

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    (site_dir / "doc.pdf").write_bytes(b"%PDF fake")
    real_url = "https://www.example.com/en/docs/deep/path/doc.pdf"
    url_map = {"doc.pdf": real_url}
    (site_dir / "_url_map.json").write_text(json.dumps(url_map), encoding="utf-8")

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(f"https://{site}", str(output_dir), str(manifest_path))

    from manifest import load_manifest
    entries = load_manifest(str(manifest_path))
    urls = [e["url"] for e in entries]
    assert real_url in urls


def test_update_manifest_falls_back_without_url_map(tmp_path):
    """update_manifest should fall back to best-guess URL when no _url_map.json exists."""
    from crawl import update_manifest

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    (site_dir / "report.pdf").write_bytes(b"%PDF fake")

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(f"https://{site}", str(output_dir), str(manifest_path))

    from manifest import load_manifest
    entries = load_manifest(str(manifest_path))
    urls = [e["url"] for e in entries]
    assert f"https://{site}/report.pdf" in urls


def test_update_manifest_partial_url_map_uses_fallback(tmp_path):
    """When a file is missing from _url_map.json, the fallback URL is used."""
    from crawl import update_manifest

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    (site_dir / "mapped.pdf").write_bytes(b"%PDF fake1")
    (site_dir / "unmapped.pdf").write_bytes(b"%PDF fake2")
    real_url = "https://www.example.com/deep/mapped.pdf"
    url_map = {"mapped.pdf": real_url}
    (site_dir / "_url_map.json").write_text(json.dumps(url_map), encoding="utf-8")

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(f"https://{site}", str(output_dir), str(manifest_path))

    from manifest import load_manifest
    entries = load_manifest(str(manifest_path))
    urls = [e["url"] for e in entries]
    assert real_url in urls
    assert f"https://{site}/unmapped.pdf" in urls


def test_update_manifest_skips_url_map_json(tmp_path):
    """_url_map.json itself should NOT appear as a manifest entry."""
    from crawl import update_manifest

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    (site_dir / "doc.pdf").write_bytes(b"%PDF fake")
    url_map = {"doc.pdf": "https://www.example.com/doc.pdf"}
    (site_dir / "_url_map.json").write_text(json.dumps(url_map), encoding="utf-8")

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(f"https://{site}", str(output_dir), str(manifest_path))

    from manifest import load_manifest
    entries = load_manifest(str(manifest_path))
    urls = [e["url"] for e in entries]
    assert not any("_url_map.json" in u for u in urls)


def test_update_manifest_skips_non_pdf_files(tmp_path):
    """update_manifest must skip non-PDF files and only add .pdf files to the manifest."""
    from crawl import update_manifest

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    (site_dir / "report.pdf").write_bytes(b"%PDF fake")
    (site_dir / "table.xlsx").write_bytes(b"fake xlsx content")
    (site_dir / "document.docx").write_bytes(b"fake docx content")
    (site_dir / "slides.pptx").write_bytes(b"fake pptx content")

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(f"https://{site}", str(output_dir), str(manifest_path))

    from manifest import load_manifest
    entries = load_manifest(str(manifest_path))
    urls = [e["url"] for e in entries]

    # Only the PDF must be recorded in the manifest
    assert len(entries) == 1
    assert f"https://{site}/report.pdf" in urls
    assert not any(".xlsx" in u for u in urls)
    assert not any(".docx" in u for u in urls)
    assert not any(".pptx" in u for u in urls)


def test_update_manifest_skips_non_pdf_prints_message(tmp_path, capsys):
    """update_manifest must print a message when skipping non-PDF files."""
    from crawl import update_manifest

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    (site_dir / "report.pdf").write_bytes(b"%PDF fake")
    (site_dir / "data.xlsx").write_bytes(b"fake xlsx")

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(f"https://{site}", str(output_dir), str(manifest_path))

    captured = capsys.readouterr()
    assert "data.xlsx" in captured.out
    assert "Skipping non-PDF file" in captured.out


# ---------------------------------------------------------------------------
# generate_crawled_urls_csv
# ---------------------------------------------------------------------------


def test_generate_crawled_urls_csv_creates_file(tmp_path):
    """generate_crawled_urls_csv must create crawled_urls.csv in report_dir."""
    from crawl import generate_crawled_urls_csv

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    pages = ["https://example.com/", "https://example.com/about"]
    (site_dir / "_crawled_pages.json").write_text(json.dumps(pages), encoding="utf-8")

    url_map = {"doc.pdf": "https://example.com/doc.pdf"}
    (site_dir / "_url_map.json").write_text(json.dumps(url_map), encoding="utf-8")

    referer_map = {"doc.pdf": "https://example.com/about"}
    (site_dir / "_referer_map.json").write_text(json.dumps(referer_map), encoding="utf-8")

    report_dir = tmp_path / "reports"
    count = generate_crawled_urls_csv("https://example.com", str(output_dir), str(report_dir))

    assert count == 2
    csv_path = report_dir / "crawled_urls.csv"
    assert csv_path.exists()


def test_generate_crawled_urls_csv_content(tmp_path):
    """The CSV must contain page rows and pdf rows with correct types and referers."""
    from crawl import generate_crawled_urls_csv
    import csv

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    pages = ["https://example.com/", "https://example.com/reports"]
    (site_dir / "_crawled_pages.json").write_text(json.dumps(pages), encoding="utf-8")

    url_map = {"report.pdf": "https://example.com/files/report.pdf"}
    (site_dir / "_url_map.json").write_text(json.dumps(url_map), encoding="utf-8")

    referer_map = {"report.pdf": "https://example.com/reports"}
    (site_dir / "_referer_map.json").write_text(json.dumps(referer_map), encoding="utf-8")

    report_dir = tmp_path / "reports"
    generate_crawled_urls_csv("https://example.com", str(output_dir), str(report_dir))

    rows = list(csv.DictReader((report_dir / "crawled_urls.csv").open(encoding="utf-8")))
    page_rows = [r for r in rows if r["type"] == "page"]
    pdf_rows  = [r for r in rows if r["type"] == "pdf"]

    assert len(page_rows) == 2
    assert len(pdf_rows) == 1
    assert pdf_rows[0]["url"] == "https://example.com/files/report.pdf"
    assert pdf_rows[0]["referer"] == "https://example.com/reports"
    assert pdf_rows[0]["type"] == "pdf"
    assert page_rows[0]["referer"] == ""


def test_generate_crawled_urls_csv_returns_page_count(tmp_path):
    """Return value must equal the number of HTML pages crawled."""
    from crawl import generate_crawled_urls_csv

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    pages = [f"https://example.com/page{i}" for i in range(7)]
    (site_dir / "_crawled_pages.json").write_text(json.dumps(pages), encoding="utf-8")

    report_dir = tmp_path / "reports"
    count = generate_crawled_urls_csv("https://example.com", str(output_dir), str(report_dir))
    assert count == 7


def test_generate_crawled_urls_csv_missing_files(tmp_path):
    """When spider output files are absent, an empty CSV must still be written."""
    from crawl import generate_crawled_urls_csv

    output_dir = tmp_path / "crawled_files"
    output_dir.mkdir()

    report_dir = tmp_path / "reports"
    count = generate_crawled_urls_csv("https://example.com", str(output_dir), str(report_dir))

    assert count == 0
    assert (report_dir / "crawled_urls.csv").exists()


# ---------------------------------------------------------------------------
# _print_scrapy_log_tail – diagnostic log helper
# ---------------------------------------------------------------------------


def test_print_scrapy_log_tail_shows_error_lines(tmp_path, capsys):
    """Error lines from the Scrapy log must be printed to stdout."""
    log = tmp_path / "scrapy.log"
    log.write_text(
        "2024-01-01 INFO Spider opened\n"
        "2024-01-01 ERROR Some problem occurred\n"
        "2024-01-01 INFO Spider closed\n",
        encoding="utf-8",
    )
    _print_scrapy_log_tail(str(log))
    captured = capsys.readouterr()
    assert "ERROR Some problem occurred" in captured.out


def test_print_scrapy_log_tail_falls_back_to_tail_when_no_errors(tmp_path, capsys):
    """When no ERROR lines exist, the last N lines must be printed instead."""
    log = tmp_path / "scrapy.log"
    lines = [f"INFO line {i}\n" for i in range(100)]
    log.write_text("".join(lines), encoding="utf-8")
    _print_scrapy_log_tail(str(log), tail_lines=10)
    captured = capsys.readouterr()
    assert "INFO line 99" in captured.out
    # Lines well before the tail should not appear.
    assert "INFO line 0" not in captured.out


def test_print_scrapy_log_tail_missing_file_is_silent(tmp_path, capsys):
    """A missing log file must not raise an exception or produce output."""
    _print_scrapy_log_tail(str(tmp_path / "nonexistent.log"))
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_scrapy_log_tail_empty_file_is_silent(tmp_path, capsys):
    """An empty log file must not produce output."""
    log = tmp_path / "scrapy.log"
    log.write_text("", encoding="utf-8")
    _print_scrapy_log_tail(str(log))
    captured = capsys.readouterr()
    assert captured.out == ""


def test_run_scrapy_prints_log_on_error(tmp_path, capsys):
    """run_scrapy must print the Scrapy log tail when Scrapy exits with a non-zero code."""
    import subprocess

    log_path = str(tmp_path / "test_scrapy.log")
    Path(log_path).write_text(
        "INFO started\nERROR Connection refused\n", encoding="utf-8"
    )

    with patch("crawl.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "scrapy")
        run_scrapy(
            "https://example.com", "out", 3600, "spider.py", log_path=log_path
        )

    captured = capsys.readouterr()
    assert "Connection refused" in captured.out


# ---------------------------------------------------------------------------
# --skip-crawl flag in main()
# ---------------------------------------------------------------------------


def test_skip_crawl_does_not_invoke_scrapy(tmp_path):
    """When --skip-crawl is set, run_scrapy() must not be called."""
    from crawl import main

    output_dir = tmp_path / "crawled_files" / "example.com"
    output_dir.mkdir(parents=True)
    manifest_path = tmp_path / "manifest.yaml"
    report_dir = tmp_path / "reports"

    with patch("crawl.run_scrapy") as mock_scrapy, \
         patch("crawl.normalize_url", return_value="https://example.com"), \
         patch("crawl.update_manifest"), \
         patch("crawl.generate_crawled_urls_csv", return_value=0):
        with patch("sys.argv", [
            "crawl.py",
            "--url", "https://example.com",
            "--manifest", str(manifest_path),
            "--output-dir", str(tmp_path / "crawled_files"),
            "--report-dir", str(report_dir),
            "--skip-crawl",
        ]):
            main()

    mock_scrapy.assert_not_called()


def test_no_skip_crawl_does_invoke_scrapy(tmp_path):
    """Without --skip-crawl, run_scrapy() must be called exactly once."""
    from crawl import main

    output_dir = tmp_path / "crawled_files" / "example.com"
    output_dir.mkdir(parents=True)
    manifest_path = tmp_path / "manifest.yaml"
    report_dir = tmp_path / "reports"

    with patch("crawl.run_scrapy") as mock_scrapy, \
         patch("crawl.normalize_url", return_value="https://example.com"), \
         patch("crawl.update_manifest"), \
         patch("crawl.generate_crawled_urls_csv", return_value=5):
        with patch("sys.argv", [
            "crawl.py",
            "--url", "https://example.com",
            "--manifest", str(manifest_path),
            "--output-dir", str(tmp_path / "crawled_files"),
            "--report-dir", str(report_dir),
        ]):
            main()

    mock_scrapy.assert_called_once()


def test_skip_crawl_still_runs_update_manifest(tmp_path):
    """When --skip-crawl is set, update_manifest() must still be called."""
    from crawl import main

    manifest_path = tmp_path / "manifest.yaml"
    report_dir = tmp_path / "reports"

    with patch("crawl.run_scrapy"), \
         patch("crawl.normalize_url", return_value="https://example.com"), \
         patch("crawl.update_manifest") as mock_update, \
         patch("crawl.generate_crawled_urls_csv", return_value=0):
        with patch("sys.argv", [
            "crawl.py",
            "--url", "https://example.com",
            "--manifest", str(manifest_path),
            "--output-dir", str(tmp_path / "crawled_files"),
            "--report-dir", str(report_dir),
            "--skip-crawl",
        ]):
            main()

    mock_update.assert_called_once()


def test_skip_crawl_no_zero_pages_warning(tmp_path, capsys):
    """When --skip-crawl is set and no pages were crawled, no warning is emitted."""
    from crawl import main

    manifest_path = tmp_path / "manifest.yaml"
    report_dir = tmp_path / "reports"

    with patch("crawl.run_scrapy"), \
         patch("crawl.normalize_url", return_value="https://example.com"), \
         patch("crawl.update_manifest"), \
         patch("crawl.generate_crawled_urls_csv", return_value=0):
        with patch("sys.argv", [
            "crawl.py",
            "--url", "https://example.com",
            "--manifest", str(manifest_path),
            "--output-dir", str(tmp_path / "crawled_files"),
            "--report-dir", str(report_dir),
            "--skip-crawl",
        ]):
            main()

    captured = capsys.readouterr()
    assert "WARNING" not in captured.out


# ---------------------------------------------------------------------------
# normalize_url – invalid domain fallback (lines 107-109)
# ---------------------------------------------------------------------------

def test_normalize_url_invalid_domain_uses_fallback(capsys):
    """Input that does not look like a domain should use a https:// fallback."""
    result = normalize_url("..bad_input")
    # Should return the input prefixed with https://
    assert result.startswith("https://")
    out = capsys.readouterr().out
    assert "does not look like a domain" in out


def test_normalize_url_dotdot_uses_fallback(capsys):
    """Input with '..' path traversal sequences must use the fallback."""
    result = normalize_url("example..com")
    assert result.startswith("https://")
    out = capsys.readouterr().out
    assert "does not look like a domain" in out


# ---------------------------------------------------------------------------
# run_scrapy – TimeoutExpired handling (lines 195-196)
# ---------------------------------------------------------------------------

def test_run_scrapy_timeout_prints_message(tmp_path, capsys):
    """run_scrapy must print a timeout message and continue when Scrapy times out."""
    import subprocess

    log_path = str(tmp_path / "scrapy.log")
    Path(log_path).write_text("", encoding="utf-8")

    with patch("crawl.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("scrapy", 3600)
        run_scrapy("https://example.com", "out", 3600, "spider.py", log_path=log_path)

    captured = capsys.readouterr()
    assert "timed out" in captured.out.lower()


# ---------------------------------------------------------------------------
# update_manifest – invalid JSON in _url_map.json (lines 234-235)
# ---------------------------------------------------------------------------

def test_update_manifest_corrupt_url_map_json_falls_back(tmp_path):
    """A corrupt _url_map.json must be ignored; the fallback URL must be used."""
    from crawl import update_manifest
    from manifest import load_manifest

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    (site_dir / "doc.pdf").write_bytes(b"%PDF fake")
    # Write invalid JSON to the url_map file
    (site_dir / "_url_map.json").write_text("NOT VALID JSON", encoding="utf-8")

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(f"https://{site}", str(output_dir), str(manifest_path))

    entries = load_manifest(str(manifest_path))
    urls = [e["url"] for e in entries]
    # Should fall back to the best-guess URL
    assert f"https://{site}/doc.pdf" in urls


# ---------------------------------------------------------------------------
# generate_crawled_urls_csv – invalid JSON in spider output files (lines 291-292, 300-301, 309-310)
# ---------------------------------------------------------------------------

def test_generate_crawled_urls_csv_corrupt_crawled_pages_json(tmp_path):
    """A corrupt _crawled_pages.json must be handled gracefully (returns 0 pages)."""
    from crawl import generate_crawled_urls_csv

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    (site_dir / "_crawled_pages.json").write_text("INVALID JSON", encoding="utf-8")

    report_dir = tmp_path / "reports"
    count = generate_crawled_urls_csv("https://example.com", str(output_dir), str(report_dir))
    assert count == 0


def test_generate_crawled_urls_csv_corrupt_url_map_json(tmp_path):
    """A corrupt _url_map.json must be handled gracefully."""
    from crawl import generate_crawled_urls_csv

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    pages = ["https://example.com/"]
    (site_dir / "_crawled_pages.json").write_text(json.dumps(pages), encoding="utf-8")
    (site_dir / "_url_map.json").write_text("INVALID JSON", encoding="utf-8")

    report_dir = tmp_path / "reports"
    count = generate_crawled_urls_csv("https://example.com", str(output_dir), str(report_dir))
    # Pages still counted; url_map gracefully defaulted to empty
    assert count == 1


def test_generate_crawled_urls_csv_corrupt_referer_map_json(tmp_path):
    """A corrupt _referer_map.json must be handled gracefully."""
    from crawl import generate_crawled_urls_csv
    import csv

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    pages = ["https://example.com/"]
    (site_dir / "_crawled_pages.json").write_text(json.dumps(pages), encoding="utf-8")
    url_map = {"doc.pdf": "https://example.com/doc.pdf"}
    (site_dir / "_url_map.json").write_text(json.dumps(url_map), encoding="utf-8")
    (site_dir / "_referer_map.json").write_text("INVALID JSON", encoding="utf-8")

    report_dir = tmp_path / "reports"
    count = generate_crawled_urls_csv("https://example.com", str(output_dir), str(report_dir))
    assert count == 1
    # CSV must still be written
    assert (report_dir / "crawled_urls.csv").exists()


# ---------------------------------------------------------------------------
# main() – PDF URL rejection (lines 405-412)
# ---------------------------------------------------------------------------

def test_main_rejects_pdf_url(tmp_path, capsys):
    """main() must exit with code 1 when the URL points directly to a PDF."""
    from crawl import main

    manifest_path = tmp_path / "manifest.yaml"
    report_dir = tmp_path / "reports"

    with patch("crawl.normalize_url", return_value="https://example.com/doc.pdf"), \
         pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", [
            "crawl.py",
            "--url", "https://example.com/doc.pdf",
            "--manifest", str(manifest_path),
            "--output-dir", str(tmp_path / "crawled_files"),
            "--report-dir", str(report_dir),
        ]):
            main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "ERROR" in captured.out
    assert "direct link to a PDF" in captured.out


# ---------------------------------------------------------------------------
# main() – no pages crawled warning (lines 428-433)
# ---------------------------------------------------------------------------

def test_main_warns_when_no_pages_crawled(tmp_path, capsys):
    """main() must print a WARNING when pages_crawled is 0 and --skip-crawl is not set."""
    from crawl import main

    manifest_path = tmp_path / "manifest.yaml"
    report_dir = tmp_path / "reports"

    with patch("crawl.run_scrapy"), \
         patch("crawl.normalize_url", return_value="https://example.com"), \
         patch("crawl.update_manifest"), \
         patch("crawl.generate_crawled_urls_csv", return_value=0), \
         patch("crawl._print_scrapy_log_tail"):
        with patch("sys.argv", [
            "crawl.py",
            "--url", "https://example.com",
            "--manifest", str(manifest_path),
            "--output-dir", str(tmp_path / "crawled_files"),
            "--report-dir", str(report_dir),
        ]):
            main()

    captured = capsys.readouterr()
    assert "WARNING" in captured.out


def test_main_retries_with_broader_scope_when_path_seed_finds_no_results(tmp_path):
    """If a deep path finds no pages/PDFs, main() should retry from a broader path."""
    from crawl import main

    output_root = tmp_path / "crawled_files"
    site_dir = output_root / "example.com"
    site_dir.mkdir(parents=True)
    manifest_path = tmp_path / "manifest.yaml"
    report_dir = tmp_path / "reports"

    def scrapy_side_effect(url, *_args, **_kwargs):
        # Simulate broader-scope crawl finding at least one PDF.
        if url == "https://example.com/programs":
            (site_dir / "found.pdf").write_bytes(b"%PDF fake")

    with patch("crawl.run_scrapy", side_effect=scrapy_side_effect) as mock_scrapy, \
         patch(
             "crawl.normalize_url",
             return_value="https://example.com/programs/5b5d7aab-0149-4898",
         ), \
         patch("crawl.update_manifest"), \
         patch("crawl.generate_crawled_urls_csv", side_effect=[0, 12]) as mock_crawled_urls, \
         patch("crawl.fetch_sitemap_pdfs", return_value=0), \
         patch("crawl._print_scrapy_log_tail"), \
         patch("crawl.spot_check_zero_results") as mock_spot:
        with patch("sys.argv", [
            "crawl.py",
            "--url", "https://example.com/programs/5b5d7aab-0149-4898",
            "--manifest", str(manifest_path),
            "--output-dir", str(output_root),
            "--report-dir", str(report_dir),
        ]):
            main()

    original_url = "https://example.com/programs/5b5d7aab-0149-4898"
    broader_url = "https://example.com/programs"
    called_urls = [call.args[0] for call in mock_scrapy.call_args_list]
    assert called_urls[0] == original_url
    assert called_urls[1] == broader_url
    assert mock_crawled_urls.call_count == 2
    assert mock_crawled_urls.call_args_list[0].args[0] == original_url
    assert mock_crawled_urls.call_args_list[1].args[0] == broader_url
    mock_spot.assert_not_called()


# ---------------------------------------------------------------------------
# update_manifest – subdirectory skipping (line 239)
# ---------------------------------------------------------------------------

def test_update_manifest_skips_subdirectories(tmp_path):
    """update_manifest must skip subdirectories inside the site directory."""
    from crawl import update_manifest
    from manifest import load_manifest

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    # Create a real PDF and a subdirectory inside the site dir.
    (site_dir / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    subdir = site_dir / "subdir"
    subdir.mkdir()
    # A PDF inside the subdirectory should not be picked up.
    (subdir / "nested.pdf").write_bytes(b"%PDF-1.4 nested")

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(f"https://{site}", str(output_dir), str(manifest_path))

    entries = load_manifest(str(manifest_path))
    urls = [e["url"] for e in entries]
    # Only the top-level PDF should be in the manifest.
    assert len(entries) == 1
    assert f"https://{site}/report.pdf" in urls
    assert not any("nested" in u for u in urls)


# ---------------------------------------------------------------------------
# update_manifest – unchanged file counter (line 254)
# ---------------------------------------------------------------------------

def test_update_manifest_counts_unchanged_files(tmp_path, capsys):
    """update_manifest must count unchanged files (already in manifest) in updated_count."""
    from crawl import update_manifest
    from manifest import load_manifest, save_manifest, build_entry

    site = "example.com"
    output_dir = tmp_path / "crawled_files"
    site_dir = output_dir / site
    site_dir.mkdir(parents=True)

    pdf_path = site_dir / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stable")

    # Pre-populate the manifest with the file already analysed (unchanged MD5).
    existing = build_entry(f"https://{site}/doc.pdf", pdf_path, site)
    existing["status"] = "analysed"
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest([existing], manifest_path)

    # Run update_manifest again – the file hasn't changed, so upsert_entry
    # returns needs_scan=False, incrementing updated_count.
    update_manifest(f"https://{site}", str(output_dir), str(manifest_path))

    captured = capsys.readouterr()
    assert "0 new/changed" in captured.out
    assert "1 unchanged" in captured.out

    entries = load_manifest(str(manifest_path))
    # Status should not have been reset (file MD5 unchanged, already analysed).
    assert entries[0]["status"] == "analysed"


# ---------------------------------------------------------------------------
# spot_check_zero_results
# ---------------------------------------------------------------------------

def _make_http_response(status: int, body: bytes = b""):
    """Return a mock urllib response with the given status and body."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_spot_check_seed_status_2xx():
    """Seed URL returning HTTP 200 should be recorded as seed_status=200."""
    from urllib.error import URLError

    # Seed OK, robots.txt error, sitemap.xml error (both fail gracefully).
    call_count = [0]
    responses = [
        _make_http_response(200),                # seed URL
        URLError("no robots.txt"),               # robots.txt fetch fails
        URLError("no sitemap.xml"),              # sitemap.xml fetch fails
    ]

    def side_effect(req, timeout=15):
        r = responses[call_count[0]]
        call_count[0] += 1
        if isinstance(r, URLError):
            raise r  # pylint: disable=raising-non-exception
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        result = spot_check_zero_results("https://example.com")

    assert result["seed_status"] == 200
    assert result["robots_blocked"] is False


def test_spot_check_seed_unreachable():
    """When the seed URL is unreachable, seed_status should be None."""
    from urllib.error import URLError

    with patch("crawl.urlopen", side_effect=URLError("connection refused")):
        result = spot_check_zero_results("https://example.com")

    assert result["seed_status"] is None
    assert "Seed URL probe failed" in result["error"]


def test_spot_check_robots_blocked():
    """robots.txt that disallows '/' for all agents should set robots_blocked=True."""
    robots_content = b"User-agent: *\nDisallow: /\n"

    call_count = [0]
    responses = [
        _make_http_response(403),               # seed – blocked
        _make_http_response(200, robots_content),  # robots.txt
        Exception("no sitemap"),  # sitemap fails
    ]

    def side_effect(req, timeout=15):
        r = responses[call_count[0]]
        call_count[0] += 1
        if isinstance(r, Exception):
            raise r  # pylint: disable=raising-non-exception
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        result = spot_check_zero_results("https://example.com")

    assert result["robots_blocked"] is True
    assert result["robots_disallows"]  # non-empty list


def test_spot_check_robots_not_blocked():
    """robots.txt that allows crawling should not set robots_blocked."""
    robots_content = b"User-agent: *\nDisallow: /private/\n"

    call_count = [0]
    from urllib.error import URLError
    responses = [
        _make_http_response(200),               # seed
        _make_http_response(200, robots_content),  # robots.txt
        URLError("no sitemap"),                 # sitemap fails
    ]

    def side_effect(req, timeout=15):
        r = responses[call_count[0]]
        call_count[0] += 1
        if isinstance(r, URLError):
            raise r  # pylint: disable=raising-non-exception
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        result = spot_check_zero_results("https://example.com")

    assert result["robots_blocked"] is False


def test_spot_check_sitemap_pdfs_found():
    """A sitemap.xml listing PDF URLs should populate sitemap_pdf_count and samples."""
    sitemap_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a.pdf</loc></url>
  <url><loc>https://example.com/b.pdf</loc></url>
  <url><loc>https://example.com/page.html</loc></url>
</urlset>"""

    call_count = [0]
    from urllib.error import URLError
    responses = [
        URLError("seed unreachable"),           # seed
        URLError("no robots.txt"),              # robots.txt
        _make_http_response(200, sitemap_xml),  # sitemap.xml
    ]

    def side_effect(req, timeout=15):
        r = responses[call_count[0]]
        call_count[0] += 1
        if isinstance(r, URLError):
            raise r
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        result = spot_check_zero_results("https://example.com")

    assert result["sitemap_pdf_count"] == 2
    assert "https://example.com/a.pdf" in result["sitemap_pdf_samples"]
    assert "https://example.com/b.pdf" in result["sitemap_pdf_samples"]


def test_spot_check_sitemap_no_pdfs():
    """A sitemap.xml with no PDF URLs should give sitemap_pdf_count=0."""
    sitemap_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page.html</loc></url>
</urlset>"""

    call_count = [0]
    from urllib.error import URLError
    responses = [
        URLError("seed unreachable"),
        URLError("no robots.txt"),
        _make_http_response(200, sitemap_xml),
    ]

    def side_effect(req, timeout=15):
        r = responses[call_count[0]]
        call_count[0] += 1
        if isinstance(r, URLError):
            raise r
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        result = spot_check_zero_results("https://example.com")

    assert result["sitemap_pdf_count"] == 0
    assert result["sitemap_pdf_samples"] == []


def test_spot_check_sitemap_samples_capped_at_five():
    """sitemap_pdf_samples should include at most 5 URLs even if more exist."""
    locs = "\n".join(
        f"  <url><loc>https://example.com/{i}.pdf</loc></url>" for i in range(10)
    )
    sitemap_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + locs.encode()
        + b"</urlset>"
    )

    call_count = [0]
    from urllib.error import URLError
    responses = [
        URLError("seed unreachable"),
        URLError("no robots.txt"),
        _make_http_response(200, sitemap_xml),
    ]

    def side_effect(req, timeout=15):
        r = responses[call_count[0]]
        call_count[0] += 1
        if isinstance(r, URLError):
            raise r
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        result = spot_check_zero_results("https://example.com")

    assert result["sitemap_pdf_count"] == 10
    assert len(result["sitemap_pdf_samples"]) == 5


def test_spot_check_all_probes_fail_gracefully():
    """All probes failing should not raise an exception; errors reported in 'error'."""
    from urllib.error import URLError

    with patch("crawl.urlopen", side_effect=URLError("all fail")):
        result = spot_check_zero_results("https://example.com")

    assert result["seed_status"] is None
    assert result["sitemap_pdf_count"] == 0
    assert result["error"]  # non-empty error summary


# ---------------------------------------------------------------------------
# _extract_pdf_urls_from_sitemap
# ---------------------------------------------------------------------------

def test_extract_pdf_urls_standard_sitemap():
    """Standard <urlset> sitemap with PDF and non-PDF URLs."""
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/doc.pdf</loc></url>
  <url><loc>https://example.com/page.html</loc></url>
  <url><loc>https://example.com/report.PDF</loc></url>
</urlset>"""
    urls = _extract_pdf_urls_from_sitemap(xml)
    assert "https://example.com/doc.pdf" in urls
    assert "https://example.com/report.PDF" in urls
    assert "https://example.com/page.html" not in urls


def test_extract_pdf_urls_sitemap_index():
    """Sitemap index files: PDF locs in <sitemap> elements are returned."""
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap2.pdf</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap3.xml</loc></sitemap>
</sitemapindex>"""
    urls = _extract_pdf_urls_from_sitemap(xml)
    assert "https://example.com/sitemap2.pdf" in urls
    assert "https://example.com/sitemap3.xml" not in urls


def test_extract_pdf_urls_invalid_xml():
    """Malformed XML should return an empty list without raising."""
    urls = _extract_pdf_urls_from_sitemap(b"not xml at all <<<<")
    assert urls == []


def test_extract_pdf_urls_empty_sitemap():
    """Sitemap with no URL elements returns an empty list."""
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
</urlset>"""
    urls = _extract_pdf_urls_from_sitemap(xml)
    assert urls == []


# ---------------------------------------------------------------------------
# _collect_sitemap_pdf_urls
# ---------------------------------------------------------------------------

def _make_http_response_plain(status: int, body: bytes = b""):
    """Return a plain mock urllib response (no MagicMock context-manager magic)."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_collect_sitemap_pdf_urls_simple_urlset():
    """A standard urlset sitemap returns its PDF URLs."""
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a.pdf</loc></url>
  <url><loc>https://example.com/b.pdf</loc></url>
  <url><loc>https://example.com/page.html</loc></url>
</urlset>"""

    with patch("crawl.urlopen", return_value=_make_http_response_plain(200, xml)):
        urls = _collect_sitemap_pdf_urls("https://example.com/sitemap.xml")

    assert "https://example.com/a.pdf" in urls
    assert "https://example.com/b.pdf" in urls
    assert "https://example.com/page.html" not in urls


def test_collect_sitemap_pdf_urls_index_fetches_children():
    """A sitemap index causes child sitemaps to be fetched."""
    index_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-docs.xml</loc></sitemap>
</sitemapindex>"""
    child_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/pub/report.pdf</loc></url>
</urlset>"""

    responses = [
        _make_http_response_plain(200, index_xml),   # root sitemap
        _make_http_response_plain(200, child_xml),   # child sitemap
    ]
    call_count = [0]

    def side_effect(req, timeout=15):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        urls = _collect_sitemap_pdf_urls("https://example.com/sitemap.xml")

    assert "https://example.com/pub/report.pdf" in urls


def test_collect_sitemap_pdf_urls_index_child_limit():
    """max_child_sitemaps caps how many child sitemaps are fetched."""
    child_locs = "\n".join(
        f"  <sitemap><loc>https://example.com/s{i}.xml</loc></sitemap>"
        for i in range(5)
    )
    index_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + child_locs.encode()
        + b"</sitemapindex>"
    )
    child_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/doc.pdf</loc></url>
</urlset>"""

    responses = [_make_http_response_plain(200, index_xml)] + [
        _make_http_response_plain(200, child_xml) for _ in range(5)
    ]
    call_count = [0]

    def side_effect(req, timeout=15):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    # Only fetch 2 child sitemaps at most.
    with patch("crawl.urlopen", side_effect=side_effect):
        urls = _collect_sitemap_pdf_urls(
            "https://example.com/sitemap.xml", max_child_sitemaps=2
        )

    # 1 root fetch + 2 child fetches = 3 urlopen calls.
    assert call_count[0] == 3
    assert len(urls) == 2


def test_collect_sitemap_pdf_urls_raises_on_top_level_failure():
    """URLError fetching the root sitemap propagates to the caller."""
    from urllib.error import URLError

    with patch("crawl.urlopen", side_effect=URLError("timeout")):
        with pytest.raises(URLError):
            _collect_sitemap_pdf_urls("https://example.com/sitemap.xml")


def test_collect_sitemap_pdf_urls_child_failure_skipped():
    """A failed child sitemap fetch is skipped; successful ones still contribute."""
    from urllib.error import URLError

    index_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/s1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/s2.xml</loc></sitemap>
</sitemapindex>"""
    child_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/good.pdf</loc></url>
</urlset>"""

    responses = [
        _make_http_response_plain(200, index_xml),
        URLError("child unreachable"),
        _make_http_response_plain(200, child_xml),
    ]
    call_count = [0]

    def side_effect(req, timeout=15):
        r = responses[call_count[0]]
        call_count[0] += 1
        if isinstance(r, URLError):
            raise r  # pylint: disable=raising-non-exception
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        urls = _collect_sitemap_pdf_urls("https://example.com/sitemap.xml")

    assert "https://example.com/good.pdf" in urls


# ---------------------------------------------------------------------------
# fetch_sitemap_pdfs
# ---------------------------------------------------------------------------

def test_fetch_sitemap_pdfs_downloads_pdfs(tmp_path):
    """PDFs listed in a sitemap are downloaded to the output directory."""
    sitemap_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/docs/report.pdf</loc></url>
  <url><loc>https://example.com/docs/guide.pdf</loc></url>
</urlset>"""
    pdf_bytes = b"%PDF-1.4 fake"

    responses = [
        _make_http_response_plain(200, sitemap_xml),  # sitemap fetch
        _make_http_response_plain(200, pdf_bytes),    # report.pdf
        _make_http_response_plain(200, pdf_bytes),    # guide.pdf
    ]
    call_count = [0]

    def side_effect(req, timeout=60):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        count = fetch_sitemap_pdfs(
            "https://example.com", str(tmp_path), max_pdfs=10, timeout=60
        )

    assert count == 2
    save_dir = tmp_path / "example.com"
    pdfs = list(save_dir.glob("*.pdf"))
    assert len(pdfs) == 2


def test_fetch_sitemap_pdfs_respects_max_pdfs(tmp_path):
    """max_pdfs limits how many PDFs are downloaded."""
    locs = "\n".join(
        f"  <url><loc>https://example.com/{i}.pdf</loc></url>" for i in range(5)
    )
    sitemap_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + locs.encode()
        + b"</urlset>"
    )
    pdf_bytes = b"%PDF-1.4 fake"

    responses = [_make_http_response_plain(200, sitemap_xml)] + [
        _make_http_response_plain(200, pdf_bytes) for _ in range(5)
    ]
    call_count = [0]

    def side_effect(req, timeout=60):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        count = fetch_sitemap_pdfs(
            "https://example.com", str(tmp_path), max_pdfs=3, timeout=60
        )

    assert count == 3
    save_dir = tmp_path / "example.com"
    assert len(list(save_dir.glob("*.pdf"))) == 3


def test_fetch_sitemap_pdfs_empty_sitemap(tmp_path):
    """An empty sitemap returns 0 without creating any files."""
    sitemap_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
</urlset>"""

    with patch(
        "crawl.urlopen",
        return_value=_make_http_response_plain(200, sitemap_xml),
    ):
        count = fetch_sitemap_pdfs(
            "https://example.com", str(tmp_path), max_pdfs=10, timeout=60
        )

    assert count == 0


def test_fetch_sitemap_pdfs_tries_alternative_sitemap_paths(tmp_path):
    """When /sitemap.xml has no PDFs, alternative sitemap paths should be tried."""
    empty_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>"""
    alt_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/docs/from-alt.pdf</loc></url>
</urlset>"""
    pdf_bytes = b"%PDF-1.4 fake"

    responses = [
        _make_http_response_plain(200, empty_xml),  # /sitemap.xml
        _make_http_response_plain(200, alt_xml),    # /sitemap_index.xml
        _make_http_response_plain(200, pdf_bytes),  # PDF download
    ]
    call_count = [0]

    def side_effect(req, timeout=60):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        count = fetch_sitemap_pdfs(
            "https://example.com", str(tmp_path), max_pdfs=10, timeout=60
        )

    assert count == 1
    assert (tmp_path / "example.com" / "from-alt.pdf").exists()


def test_fetch_sitemap_pdfs_unreachable_sitemap(tmp_path):
    """A sitemap fetch failure returns 0 without raising."""
    from urllib.error import URLError

    with patch("crawl.urlopen", side_effect=URLError("timeout")):
        count = fetch_sitemap_pdfs(
            "https://example.com", str(tmp_path), max_pdfs=10, timeout=60
        )

    assert count == 0


def test_fetch_sitemap_pdfs_skips_already_downloaded(tmp_path):
    """PDFs whose URLs are already in the existing _url_map.json are skipped."""
    sitemap_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/old.pdf</loc></url>
  <url><loc>https://example.com/new.pdf</loc></url>
</urlset>"""
    pdf_bytes = b"%PDF-1.4 fake"

    # Pre-populate the output directory with an existing URL map.
    save_dir = tmp_path / "example.com"
    save_dir.mkdir()
    url_map = {"old.pdf": "https://example.com/old.pdf"}
    (save_dir / "_url_map.json").write_text(
        __import__("json").dumps(url_map), encoding="utf-8"
    )
    (save_dir / "old.pdf").write_bytes(pdf_bytes)

    responses = [
        _make_http_response_plain(200, sitemap_xml),  # sitemap
        _make_http_response_plain(200, pdf_bytes),    # new.pdf (old.pdf skipped)
    ]
    call_count = [0]

    def side_effect(req, timeout=60):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        count = fetch_sitemap_pdfs(
            "https://example.com", str(tmp_path), max_pdfs=10, timeout=60
        )

    assert count == 1  # only new.pdf was downloaded


def test_fetch_sitemap_pdfs_creates_url_map(tmp_path):
    """fetch_sitemap_pdfs writes _url_map.json mapping filename to URL."""
    sitemap_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/docs/report.pdf</loc></url>
</urlset>"""
    pdf_bytes = b"%PDF-1.4 fake"

    responses = [
        _make_http_response_plain(200, sitemap_xml),
        _make_http_response_plain(200, pdf_bytes),
    ]
    call_count = [0]

    def side_effect(req, timeout=60):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        fetch_sitemap_pdfs(
            "https://example.com", str(tmp_path), max_pdfs=10, timeout=60
        )

    url_map = json.loads((tmp_path / "example.com" / "_url_map.json").read_text())
    assert "https://example.com/docs/report.pdf" in url_map.values()


def test_fetch_sitemap_pdfs_strips_www(tmp_path):
    """www. prefix is stripped when deriving the output subdirectory."""
    sitemap_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.example.com/a.pdf</loc></url>
</urlset>"""
    pdf_bytes = b"%PDF-1.4 fake"

    responses = [
        _make_http_response_plain(200, sitemap_xml),
        _make_http_response_plain(200, pdf_bytes),
    ]
    call_count = [0]

    def side_effect(req, timeout=60):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        count = fetch_sitemap_pdfs(
            "https://www.example.com", str(tmp_path), max_pdfs=10, timeout=60
        )

    assert count == 1
    # Output directory should be example.com, not www.example.com.
    assert (tmp_path / "example.com").exists()
    assert not (tmp_path / "www.example.com").exists()


# ---------------------------------------------------------------------------
# DuckDuckGo fallback search
# ---------------------------------------------------------------------------

def test_extract_pdf_urls_from_duckduckgo_redirect_links():
    """DuckDuckGo redirect links should yield same-site .pdf URLs."""
    html = """
<html><body>
  <a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fnfb.org%2Ffiles%2Fa.pdf">
    result 1
  </a>
  <a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fcdn.nfb.org%2Fdocs%2Fb.PDF">
    result 2
  </a>
  <a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fskip.pdf">
    wrong domain
  </a>
  <a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fnfb.org%2Fpage.html">
    not a pdf
  </a>
</body></html>
"""
    urls = _extract_pdf_urls_from_duckduckgo(html, "https://nfb.org")
    assert urls == [
        "https://nfb.org/files/a.pdf",
        "https://cdn.nfb.org/docs/b.PDF",
    ]


def test_fetch_duckduckgo_pdfs_downloads_pdfs(tmp_path):
    """DuckDuckGo-discovered PDFs should be downloaded and counted."""
    html = """
<html><body>
  <a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs%2Fguide.pdf">
    guide
  </a>
</body></html>
"""
    pdf_bytes = b"%PDF-1.4 fake"
    responses = [
        _make_http_response_plain(200, html.encode("utf-8")),  # DDG search
        _make_http_response_plain(200, pdf_bytes),             # PDF download
    ]
    call_count = [0]

    def side_effect(req, timeout=60):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    with patch("crawl.urlopen", side_effect=side_effect):
        count = fetch_duckduckgo_pdfs(
            "https://example.com", str(tmp_path), max_pdfs=10, timeout=60
        )

    assert count == 1
    assert (tmp_path / "example.com" / "guide.pdf").exists()


def test_fetch_duckduckgo_pdfs_no_results(tmp_path):
    """When search results contain no same-site PDFs, nothing is downloaded."""
    html = "<html><body><a href='https://example.com/nope.html'>nope</a></body></html>"
    with patch(
        "crawl.urlopen",
        return_value=_make_http_response_plain(200, html.encode("utf-8")),
    ):
        count = fetch_duckduckgo_pdfs(
            "https://example.com", str(tmp_path), max_pdfs=10, timeout=60
        )
    assert count == 0
