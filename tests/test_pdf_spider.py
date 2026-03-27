"""Tests for scripts/pdf_spider.py."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from scrapy.http import Request as ScrapyRequest
from scrapy.http import Response as ScrapyResponse

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
# USER_AGENTS pool – diverse set of modern browser strings
# ---------------------------------------------------------------------------


def test_user_agents_pool_defined():
    """Module-level USER_AGENTS constant must be importable and non-empty."""
    from pdf_spider import USER_AGENTS

    assert isinstance(USER_AGENTS, list)
    assert len(USER_AGENTS) > 1, "USER_AGENTS pool must contain more than one entry"


def test_user_agents_pool_all_browser_like():
    """Every entry in USER_AGENTS must look like a real browser UA."""
    from pdf_spider import USER_AGENTS

    for ua in USER_AGENTS:
        assert "Mozilla/" in ua, f"UA does not look browser-like: {ua!r}"
        assert "Scrapy" not in ua, f"UA still contains 'Scrapy': {ua!r}"


def test_user_agents_pool_includes_firefox():
    """USER_AGENTS must include at least one Firefox UA for browser diversity."""
    from pdf_spider import USER_AGENTS

    assert any("Firefox" in ua for ua in USER_AGENTS), (
        "USER_AGENTS pool contains no Firefox entry"
    )


def test_user_agents_pool_includes_chrome():
    """USER_AGENTS must include at least one Chrome UA."""
    from pdf_spider import USER_AGENTS

    assert any("Chrome" in ua for ua in USER_AGENTS), (
        "USER_AGENTS pool contains no Chrome entry"
    )


def test_random_ua_returns_from_pool():
    """_random_ua() must return a value drawn from USER_AGENTS."""
    from pdf_spider import USER_AGENTS

    spider = _make_spider("/tmp")
    for _ in range(20):
        ua = spider._random_ua()
        assert ua in USER_AGENTS, f"_random_ua() returned a value not in pool: {ua!r}"


# ---------------------------------------------------------------------------
# RANDOMIZE_DOWNLOAD_DELAY – polite, varied crawl cadence
# ---------------------------------------------------------------------------


def test_randomize_download_delay_enabled():
    """custom_settings must enable RANDOMIZE_DOWNLOAD_DELAY."""
    from pdf_spider import PdfA11ySpider

    assert PdfA11ySpider.custom_settings.get("RANDOMIZE_DOWNLOAD_DELAY") is True


# ---------------------------------------------------------------------------
# start_requests – per-request User-Agent header
# ---------------------------------------------------------------------------


def test_start_requests_sets_user_agent_header():
    """start_requests() must set a User-Agent header on the initial request."""
    from pdf_spider import USER_AGENTS

    spider = _make_spider("/tmp")
    requests = list(spider.start_requests())
    assert len(requests) == 1
    req = requests[0]
    # Scrapy stores headers case-insensitively; retrieve as bytes and decode.
    ua_bytes = req.headers.get(b"User-Agent")
    assert ua_bytes is not None, "start_requests() request has no User-Agent header"
    ua = ua_bytes.decode()
    assert ua in USER_AGENTS, f"Request User-Agent not from pool: {ua!r}"


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


# ---------------------------------------------------------------------------
# _is_allowed_domain – same-domain page crawling enforcement
# ---------------------------------------------------------------------------


def test_is_allowed_domain_same_domain():
    """Exact seed domain must be allowed."""
    spider = _make_spider("/tmp")
    assert spider._is_allowed_domain("https://example.com/page")


def test_is_allowed_domain_subdomain():
    """Subdomains of the seed domain must be allowed for page following."""
    spider = _make_spider("/tmp")
    # Spider seeded at example.com; sub.example.com is a subdomain.
    assert spider._is_allowed_domain("https://sub.example.com/page")


def test_is_allowed_domain_different_domain():
    """A completely different domain must NOT be allowed for page following."""
    spider = _make_spider("/tmp")
    assert not spider._is_allowed_domain("https://other.com/page")


def test_is_allowed_domain_cdn_different_domain():
    """A CDN / asset host on a different domain must NOT be followed as a page."""
    spider = _make_spider("/tmp")
    # assets.publishing.service.gov.uk is NOT a subdomain of example.com
    assert not spider._is_allowed_domain(
        "https://assets.publishing.service.gov.uk/media/doc.html"
    )


def test_is_allowed_domain_empty_netloc():
    """A relative URL (empty netloc after urljoin) must be considered allowed."""
    spider = _make_spider("/tmp")
    # Relative URLs resolve to the current page domain; empty netloc = allowed.
    assert spider._is_allowed_domain("/relative/path")


def test_is_allowed_domain_case_insensitive():
    """Hostname comparison must be case-insensitive."""
    spider = _make_spider("/tmp")
    assert spider._is_allowed_domain("https://EXAMPLE.COM/page")
    assert spider._is_allowed_domain("https://Example.Com/page")


def test_offsite_middleware_disabled():
    """OffsiteMiddleware must be disabled so cross-domain PDF downloads are not dropped."""
    from pdf_spider import PdfA11ySpider

    middlewares = PdfA11ySpider.custom_settings.get("SPIDER_MIDDLEWARES", {})
    assert middlewares.get("scrapy.spidermiddlewares.offsite.OffsiteMiddleware") is None, (
        "OffsiteMiddleware must be set to None (disabled) in custom_settings"
    )


# ---------------------------------------------------------------------------
# parse() – cross-domain PDF download and same-domain HTML following
# ---------------------------------------------------------------------------


def _make_html_response(page_url, html_body, meta=None):
    """Return a real Scrapy HtmlResponse for use in parse() tests.

    Attaches a synthetic Request so that ``response.meta`` is accessible,
    matching what Scrapy does when a response is received for an actual
    in-flight request.

    Args:
        page_url: The URL the response came from.
        html_body: HTML string for the response body.
        meta: Optional dict to set as the request meta (e.g. {"no_follow": True}).
    """
    from scrapy.http import HtmlResponse, Request as ScrapyRequest

    req = ScrapyRequest(page_url, meta=meta or {})
    return HtmlResponse(
        url=page_url,
        body=html_body.encode("utf-8"),
        encoding="utf-8",
        request=req,
    )


def test_parse_cross_domain_pdf_is_downloaded():
    """parse() must yield a download Request for a PDF on a different domain."""
    spider = _make_spider("/tmp")
    page_url = "https://example.com/publications"
    pdf_url = "https://assets.cdn.example.org/report.pdf"
    html = f'<html><body><a href="{pdf_url}">Download PDF</a></body></html>'

    response = _make_html_response(page_url, html)
    requests = list(spider.parse(response))

    assert len(requests) == 1, f"Expected 1 download request, got {len(requests)}"
    req = requests[0]
    assert isinstance(req, ScrapyRequest)
    assert req.url == pdf_url
    assert req.callback == spider.save_pdf


def test_parse_cross_domain_html_not_followed():
    """parse() must NOT yield a follow Request for an HTML page (with .html extension) on a different domain."""
    spider = _make_spider("/tmp")
    page_url = "https://example.com/page"
    # URL with a .html extension on an external domain → must be skipped
    offsite_url = "https://other.com/another-page.html"
    html = f'<html><body><a href="{offsite_url}">Other site</a></body></html>'

    response = _make_html_response(page_url, html)
    requests = list(spider.parse(response))

    assert requests == [], (
        f"Expected no requests for off-site HTML link, got {requests}"
    )


def test_parse_cross_domain_extensionless_link_yields_potential_pdf_request():
    """parse() must yield a parse Request for an extensionless link on an external domain.

    Government CMS platforms (e.g. CivicPlus, Drupal, SharePoint) often serve
    PDF documents through paths without a .pdf extension such as
    /DocumentCenter/View/1234 or /download/annual-report.  When such a link is
    found on the seed domain but points to an external host, the spider must
    fetch it to check the Content-Type header (the response may be a PDF).
    """
    spider = _make_spider("/tmp")
    page_url = "https://example.com/publications"
    # Extensionless URL on an external domain – no .pdf, no .html suffix
    offsite_cms_url = "https://cdn.othergov.org/DocumentCenter/View/1234"
    html = f'<html><body><a href="{offsite_cms_url}">Download Report</a></body></html>'

    response = _make_html_response(page_url, html)
    requests = list(spider.parse(response))

    assert len(requests) == 1, f"Expected 1 potential-PDF request, got {len(requests)}"
    req = requests[0]
    assert isinstance(req, ScrapyRequest)
    assert req.url == offsite_cms_url
    assert req.callback == spider.parse
    assert req.meta.get("no_follow") is True
    assert req.meta.get("referer") == page_url


def test_parse_cross_domain_extensionless_link_pdf_is_saved(tmp_path):
    """PDF returned for an extensionless external-domain fetch must be saved.

    The no_follow request yields a non-HTML response with Content-Type
    application/pdf; parse() must detect it and call save_pdf().
    """
    spider = _make_spider(tmp_path)
    cms_pdf_url = "https://cdn.othergov.org/DocumentCenter/View/1234"
    referer_page = "https://example.com/publications"
    response = _make_binary_response(
        cms_pdf_url,
        meta={"referer": referer_page, "no_follow": True},
    )
    list(spider.parse(response))

    site_dir = tmp_path / "example.com"
    pdf_files = [f for f in site_dir.iterdir() if f.suffix == ".pdf"]
    assert len(pdf_files) == 1, (
        f"Expected 1 saved PDF for external-domain CMS URL, got {[f.name for f in site_dir.iterdir()]}"
    )


def test_parse_no_follow_html_response_is_ignored():
    """parse() must not follow links when the response has no_follow=True in meta.

    When an external URL fetched as a potential PDF returns an HTML page, the
    spider must not crawl that external site's links.
    """
    spider = _make_spider("/tmp")
    ext_url = "https://other.com/some-page"
    html = '<html><body><a href="https://other.com/linked-page">link</a></body></html>'

    # Simulate the response that would come back for a no_follow=True request
    response = _make_html_response(ext_url, html, meta={"no_follow": True})
    requests = list(spider.parse(response))

    assert requests == [], (
        f"Expected no requests when no_follow=True (got {requests})"
    )


def test_parse_same_domain_html_is_followed():
    """parse() must yield a follow Request for an HTML page on the seed domain."""
    spider = _make_spider("/tmp")
    page_url = "https://example.com/page"
    same_domain_url = "https://example.com/other-page"
    html = f'<html><body><a href="{same_domain_url}">Other page</a></body></html>'

    response = _make_html_response(page_url, html)
    requests = list(spider.parse(response))

    assert len(requests) == 1, f"Expected 1 follow request, got {len(requests)}"
    assert requests[0].callback == spider.parse


def test_parse_same_domain_follow_carries_referer_in_meta():
    """parse() must pass the current page URL as 'referer' in the request meta."""
    spider = _make_spider("/tmp")
    page_url = "https://example.com/page"
    child_url = "https://example.com/child-page"
    html = f'<html><body><a href="{child_url}">Child</a></body></html>'

    response = _make_html_response(page_url, html)
    requests = list(spider.parse(response))

    assert len(requests) == 1
    req = requests[0]
    assert req.meta.get("referer") == page_url, (
        f"Expected referer={page_url!r} in meta, got {req.meta!r}"
    )


# ---------------------------------------------------------------------------
# parse() – Content-Type PDF detection (no .pdf extension in URL)
# ---------------------------------------------------------------------------


def _make_binary_response(url, body=b"%PDF-1.4 fake", content_type=b"application/pdf",
                          meta=None):
    """Return a real Scrapy Response (non-HTML) with the given Content-Type.

    Attaches a synthetic Request so that response.meta is accessible, matching
    what Scrapy does when a response is received for an actual in-flight request.

    Args:
        url: The response URL.
        body: Raw response body bytes.
        content_type: Content-Type header value as bytes (default: b"application/pdf").
        meta: Optional dict to set as the request meta (e.g. {"referer": "..."}).
    """
    request = ScrapyRequest(url, meta=meta or {})
    return ScrapyResponse(url=url, body=body, headers={b"Content-Type": content_type},
                          request=request)


def test_parse_saves_pdf_detected_by_content_type(tmp_path):
    """parse() must save a PDF response even when the URL lacks a .pdf extension.

    CMS sites like CivicPlus/CivicEngage serve PDFs through paths such as
    /DocumentCenter/View/1234/ without a .pdf suffix.  The spider must detect
    these via the Content-Type response header and save them.
    """
    spider = _make_spider(tmp_path)
    cms_pdf_url = "https://example.com/DocumentCenter/View/1234/"
    response = _make_binary_response(cms_pdf_url)
    # parse() is a generator; consuming it executes the function body.
    list(spider.parse(response))

    site_dir = tmp_path / "example.com"
    pdf_files = [f for f in site_dir.iterdir() if f.suffix == ".pdf"]
    assert len(pdf_files) == 1, (
        f"Expected 1 saved PDF, got {[f.name for f in site_dir.iterdir()]}"
    )


def test_parse_pdf_content_type_uses_path_segment_as_filename(tmp_path):
    """PDF saved via Content-Type detection must use the last URL path segment."""
    spider = _make_spider(tmp_path)
    # URL: last segment is "Annual-Budget-2024", no extension → should become "Annual-Budget-2024.pdf"
    cms_url = "https://example.com/DocumentCenter/View/Annual-Budget-2024"
    response = _make_binary_response(cms_url)
    list(spider.parse(response))

    site_dir = tmp_path / "example.com"
    saved = [f.name for f in site_dir.iterdir() if f.suffix == ".pdf"]
    assert "Annual-Budget-2024.pdf" in saved, f"Expected Annual-Budget-2024.pdf, got {saved}"


def test_parse_pdf_content_type_records_referer(tmp_path):
    """PDF saved via Content-Type detection must record the referer from meta."""
    spider = _make_spider(tmp_path)
    cms_url = "https://example.com/DocumentCenter/View/1234"
    referer_page = "https://example.com/documents"
    response = _make_binary_response(cms_url, meta={"referer": referer_page})
    list(spider.parse(response))

    save_dir = str(tmp_path / "example.com")
    referer_map = spider._referer_maps.get(save_dir, {})
    assert any(v == referer_page for v in referer_map.values()), (
        f"Expected referer {referer_page!r} in referer_map, got {referer_map}"
    )


def test_parse_non_pdf_non_html_response_is_ignored(tmp_path):
    """parse() must silently ignore non-HTML, non-PDF responses (e.g. images)."""
    spider = _make_spider(tmp_path)
    img_url = "https://example.com/logo.png"
    response = _make_binary_response(
        img_url, body=b"\x89PNG\r\n", content_type=b"image/png"
    )
    list(spider.parse(response))

    site_dir = tmp_path / "example.com"
    # No files should have been saved (directory may not even exist).
    if site_dir.exists():
        pdf_files = [f for f in site_dir.iterdir() if f.suffix == ".pdf"]
        assert pdf_files == [], f"Unexpected PDF files saved: {pdf_files}"


# ---------------------------------------------------------------------------
# save_pdf – filename when URL has no extension (CMS URLs)
# ---------------------------------------------------------------------------


def test_save_pdf_adds_pdf_extension_when_url_has_none(tmp_path):
    """save_pdf must append .pdf when the URL path has no file extension."""
    spider = _make_spider(tmp_path)
    url = "https://example.com/DocumentCenter/View/1234"
    spider.save_pdf(_make_response(url))

    site_dir = tmp_path / "example.com"
    pdf_files = [f for f in site_dir.iterdir() if f.suffix == ".pdf"]
    assert len(pdf_files) == 1, f"Expected 1 .pdf file, got {[f.name for f in site_dir.iterdir()]}"
    assert pdf_files[0].name == "1234.pdf"


def test_save_pdf_trailing_slash_url_uses_last_nonempty_segment(tmp_path):
    """save_pdf must use the last non-empty URL segment for trailing-slash URLs."""
    spider = _make_spider(tmp_path)
    # Trailing slash → last segment is "1234", not ""
    url = "https://example.com/DocumentCenter/View/1234/"
    spider.save_pdf(_make_response(url))

    site_dir = tmp_path / "example.com"
    pdf_files = [f for f in site_dir.iterdir() if f.suffix == ".pdf"]
    assert len(pdf_files) == 1
    assert pdf_files[0].name == "1234.pdf"


def test_save_pdf_preserves_existing_pdf_extension(tmp_path):
    """save_pdf must NOT double-add .pdf when the URL already ends with .pdf."""
    spider = _make_spider(tmp_path)
    url = "https://example.com/reports/annual.pdf"
    spider.save_pdf(_make_response(url))

    site_dir = tmp_path / "example.com"
    pdf_files = [f for f in site_dir.iterdir() if f.suffix == ".pdf"]
    assert len(pdf_files) == 1
    assert pdf_files[0].name == "annual.pdf", (
        f"Extension was doubled: {pdf_files[0].name}"
    )
