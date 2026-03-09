"""Tests for scripts/crawl.py – focused on normalize_url()."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from crawl import normalize_url, _URL_PREFIXES


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
