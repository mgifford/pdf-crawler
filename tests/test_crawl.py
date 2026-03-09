"""Tests for scripts/crawl.py – focused on normalize_url(), _site_folder() and update_manifest()."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from crawl import normalize_url, _URL_PREFIXES, _site_folder, update_manifest


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
# update_manifest – URL map integration
# ---------------------------------------------------------------------------

def make_test_pdf(path: Path) -> None:
    """Write a minimal fake PDF to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 fake content")


def test_update_manifest_uses_url_map(tmp_path):
    """update_manifest should use the actual URL from _url_map.json."""
    site = "example.com"
    site_dir = tmp_path / site
    site_dir.mkdir(parents=True)

    # Create a fake PDF file
    pdf_name = "annual-report.pdf"
    make_test_pdf(site_dir / pdf_name)

    # Write a _url_map.json with the real download URL
    actual_url = "https://www.example.com/en/publications/annual-report.pdf"
    url_map = {pdf_name: actual_url}
    (site_dir / "_url_map.json").write_text(
        json.dumps(url_map), encoding="utf-8"
    )

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(
        "https://www.example.com",
        str(tmp_path),
        str(manifest_path),
    )

    from manifest import load_manifest
    entries = load_manifest(manifest_path)
    assert len(entries) == 1
    assert entries[0]["url"] == actual_url


def test_update_manifest_fallback_without_url_map(tmp_path):
    """When _url_map.json is absent, update_manifest falls back to best-guess URLs."""
    site = "example.com"
    site_dir = tmp_path / site
    site_dir.mkdir(parents=True)

    pdf_name = "report.pdf"
    make_test_pdf(site_dir / pdf_name)
    # No _url_map.json written

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(
        "https://example.com",
        str(tmp_path),
        str(manifest_path),
    )

    from manifest import load_manifest
    entries = load_manifest(manifest_path)
    assert len(entries) == 1
    assert entries[0]["url"] == f"https://{site}/{pdf_name}"


def test_update_manifest_partial_url_map(tmp_path):
    """Only the file present in _url_map.json gets the real URL; the rest fall back."""
    site = "example.com"
    site_dir = tmp_path / site
    site_dir.mkdir(parents=True)

    mapped_name = "mapped.pdf"
    unmapped_name = "unmapped.pdf"
    make_test_pdf(site_dir / mapped_name)
    make_test_pdf(site_dir / unmapped_name)

    actual_url = "https://www.example.com/path/to/mapped.pdf"
    (site_dir / "_url_map.json").write_text(
        json.dumps({mapped_name: actual_url}), encoding="utf-8"
    )

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(
        "https://example.com",
        str(tmp_path),
        str(manifest_path),
    )

    from manifest import load_manifest
    entries = load_manifest(manifest_path)
    by_filename = {e["filename"]: e for e in entries}

    assert by_filename[mapped_name]["url"] == actual_url
    assert by_filename[unmapped_name]["url"] == f"https://{site}/{unmapped_name}"


def test_update_manifest_skips_url_map_file(tmp_path):
    """The _url_map.json file itself must NOT be added as a manifest entry."""
    site = "example.com"
    site_dir = tmp_path / site
    site_dir.mkdir(parents=True)

    make_test_pdf(site_dir / "doc.pdf")
    (site_dir / "_url_map.json").write_text(
        json.dumps({"doc.pdf": "https://example.com/doc.pdf"}), encoding="utf-8"
    )

    manifest_path = tmp_path / "manifest.yaml"
    update_manifest(
        "https://example.com",
        str(tmp_path),
        str(manifest_path),
    )

    from manifest import load_manifest
    entries = load_manifest(manifest_path)
    filenames = [e["filename"] for e in entries]
    assert "_url_map.json" not in filenames
    assert len(entries) == 1
