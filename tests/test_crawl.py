"""Tests for scripts/crawl.py – focused on normalize_url(), _site_folder(), and run_scrapy()."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from crawl import normalize_url, _URL_PREFIXES, _site_folder, run_scrapy, _print_scrapy_log_tail, is_pdf_url


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
