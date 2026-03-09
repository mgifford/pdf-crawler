"""Tests for scripts/crawl.py – focused on normalize_url(), _site_folder(), and run_scrapy()."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from crawl import normalize_url, _URL_PREFIXES, _site_folder, run_scrapy


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
