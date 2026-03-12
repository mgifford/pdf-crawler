"""
PDF Spider – crawls a website and downloads all PDF files found.

Usage:
    scrapy runspider pdf_spider.py -a url=https://example.com -a output_dir=crawled_files

The spider respects a DOWNLOAD_DELAY of 1 second between requests.  HTML
pages are only followed within the same domain (including subdomains) as the
seed URL.  PDF files linked from those pages may be hosted on any domain —
for example, government sites often serve PDFs from a dedicated asset CDN
(e.g. assets.publishing.service.gov.uk) while the main pages live on a
different hostname (www.gov.uk).  Scrapy's built-in OffsiteMiddleware is
disabled so that these cross-domain download requests are not dropped; instead
the spider enforces same-domain page crawling explicitly.
"""

import hashlib
import json
import random
import scrapy
import urllib.parse
import re
import os
import itertools
from scrapy.http import Request

# Pool of modern browser User-Agent strings to rotate across requests.
# Rotating across different browsers (Chrome, Firefox, Edge, Safari) and
# platforms (Windows, macOS) reduces the chance of being fingerprinted or
# rate-limited by WAF/CDN systems that track repeated identical User-Agent
# strings.  All entries look like a real browser (contain "Mozilla/") and
# contain no Scrapy identifiers.
USER_AGENTS = [
    # Chrome on Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    # Firefox on Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) "
        "Gecko/20100101 Firefox/132.0"
    ),
    # Edge on Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
    ),
    # Chrome on macOS
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    # Safari on macOS
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.1 Safari/605.1.15"
    ),
    # Firefox on macOS
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) "
        "Gecko/20100101 Firefox/132.0"
    ),
]


class PdfA11ySpider(scrapy.Spider):
    name = "pdf_a11y_crawler"
    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        # Randomize the actual delay to between 0.5× and 1.5× the base
        # DOWNLOAD_DELAY so consecutive requests do not arrive at a perfectly
        # uniform cadence.  This reduces the likelihood of triggering
        # rate-limiting on government servers while still being polite.
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "COOKIES_ENABLED": True,
        # Provide a sensible default UA; individual requests override this
        # with a randomly selected entry from the module-level USER_AGENTS
        # pool via _random_ua().
        "USER_AGENT": USER_AGENTS[0],
        # Set Accept/Accept-Language headers to match what a real browser
        # sends; some servers reject requests that omit these headers.
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.5",
        },
        # Disable the built-in OffsiteMiddleware so that PDF download requests
        # pointing to a different domain (e.g. a CDN or asset host) are not
        # silently dropped.  Same-domain enforcement for HTML page crawling is
        # handled explicitly in parse() via _is_allowed_domain().
        "SPIDER_MIDDLEWARES": {
            "scrapy.spidermiddlewares.offsite.OffsiteMiddleware": None,
        },
    }

    # Only PDF files are downloaded; all other document types are skipped since
    # the analyser can only process PDF documents.
    DOWNLOAD_EXTENSIONS = {
        ".pdf",
    }

    def __init__(self, url=None, output_dir="crawled_files", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.url = url
        self.output_dir = output_dir
        self.parsed_url = urllib.parse.urlparse(url)
        # Normalize to lowercase: HTTP hostnames are case-insensitive.
        # This list is used by _is_allowed_domain() to decide which HTML pages
        # should be followed; PDF download requests are not restricted by domain.
        self.allowed_domains = [self.parsed_url.netloc.lower()]
        self.start_urls = [url]
        # Accumulate filename→URL mappings in memory; written to disk on close.
        # Keyed by save_dir so each site subdirectory gets its own map file.
        self._url_maps: dict = {}
        # Track every HTML page URL visited during the crawl.
        self._crawled_pages: list = []
        # Accumulate filename→referer mappings (the page that linked to each
        # downloadable file).  Keyed by save_dir like _url_maps.
        self._referer_maps: dict = {}

    def _has_download_extension(self, path):
        _, ext = os.path.splitext(path.lower())
        return ext in self.DOWNLOAD_EXTENSIONS

    def _is_allowed_domain(self, url):
        """Return True if *url*'s hostname is the seed domain or a subdomain of it.

        In parse(), *url* is always the result of response.urljoin(), so it is
        an absolute URL with a non-empty netloc for any link encountered on the
        page.  The empty-netloc guard is retained for safety (e.g. direct calls
        with a relative path) but is not normally exercised during crawling.
        This logic is applied only to HTML page links so that cross-domain PDF
        download requests are not restricted.
        """
        hostname = urllib.parse.urlparse(url).netloc.lower()
        if not hostname:
            return True
        return any(
            hostname == d or hostname.endswith("." + d)
            for d in self.allowed_domains
        )

    def _random_ua(self):
        """Return a randomly selected User-Agent string from USER_AGENTS."""
        return random.choice(USER_AGENTS)

    def start_requests(self):
        """Yield the initial request(s) with an errback for connection errors."""
        for url in self.start_urls:
            yield scrapy.Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                headers={"User-Agent": self._random_ua()},
            )

    def handle_error(self, failure):
        """Log request failures (connection errors, DNS failures, etc.)."""
        url = failure.request.url
        self.logger.error("Request failed for %s: %s", url, failure.value)
        print(f"  ERROR: {url}: {failure.value}", flush=True)

    def parse(self, response):
        if not isinstance(response, scrapy.http.response.html.HtmlResponse):
            # A link followed as a potential HTML page returned non-HTML content.
            # This happens on CMS sites (e.g. CivicPlus/CivicEngage) where PDF
            # documents are served through paths that lack a .pdf extension, such
            # as /DocumentCenter/View/1234/ or /ArchiveCenter/ViewFile/Item/5678.
            # Check the Content-Type header; if the server declares the response
            # as a PDF, save it just like any other discovered PDF.
            content_type = (
                response.headers.get(b"Content-Type", b"")
                .decode("utf-8", errors="replace")
                .lower()
            )
            if "application/pdf" in content_type:
                referer = response.meta.get("referer", "")
                self.logger.info(
                    "Detected PDF by Content-Type (no .pdf extension): %s",
                    response.url,
                )
                print(
                    f"  Found PDF by Content-Type: {response.url}",
                    flush=True,
                )
                self.save_pdf(response, referer=referer)
            return

        print(f"Crawling: {response.url}", flush=True)
        self._crawled_pages.append(response.url)

        for href in response.xpath("//a[@href]/@href"):
            link = href.extract().strip()
            parsed_link = urllib.parse.urlparse(link)
            path = re.sub(r"/+$", "", parsed_link.path)
            scheme = parsed_link.scheme
            full_link = response.urljoin(link)

            if scheme not in ("", "http", "https"):
                continue

            if self._has_download_extension(path):
                self.logger.info("Downloading: %s", full_link)
                print(f"  Found for download: {full_link}", flush=True)
                yield Request(
                    full_link,
                    callback=self.save_pdf,
                    errback=self.handle_error,
                    cb_kwargs={"referer": response.url},
                    headers={"User-Agent": self._random_ua()},
                )
            else:
                path_lower = path.lower()
                if "recherche" in path_lower or "search" in path_lower:
                    self.logger.info("Skipping search page: %s", full_link)
                elif not self._is_allowed_domain(full_link):
                    self.logger.info("Skipping off-site page: %s", full_link)
                else:
                    yield response.follow(
                        link, self.parse, errback=self.handle_error,
                        headers={"User-Agent": self._random_ua()},
                        meta={"referer": response.url},
                    )

    def save_pdf(self, response, referer=""):
        # Use only the URL path component so that query-string parameters
        # (e.g. ?VersionId=abc123) do not end up embedded in the filename.
        # Characters such as '?' are rejected by GitHub Actions artifact upload
        # and are invalid on several file systems (Windows, NTFS).
        url_path = urllib.parse.urlparse(response.url).path
        # Strip trailing slashes before extracting the last path segment so
        # that URLs like /DocumentCenter/View/1234/ use "1234" as the base
        # rather than an empty string.
        segments = [s for s in url_path.split("/") if s]
        if segments:
            raw_path = segments[-1]
        else:
            # Root path ("/") or empty path: derive a stable name from the URL
            # to avoid all such PDFs colliding under the same filename.
            raw_path = "doc-" + hashlib.md5(response.url.encode()).hexdigest()[:8]
        basename, ext = os.path.splitext(os.path.basename(raw_path))
        # When the URL has no file extension (common on CMS-generated PDF
        # links such as /DocumentCenter/View/1234/), default to .pdf so the
        # saved file is recognised by the manifest update step.
        if not ext:
            ext = ".pdf"
        # Use lowercase netloc with www. stripped for a clean, consistent folder
        # name (e.g. "ontario.ca" instead of "www.Ontario.ca").
        netloc = self.parsed_url.netloc.lower()
        subfolder = netloc.removeprefix("www.")
        save_dir = os.path.join(self.output_dir, subfolder)
        os.makedirs(save_dir, exist_ok=True)
        filename = self._unique_filename(save_dir, basename, ext)
        full_path = os.path.join(save_dir, filename)
        self.logger.info("Saving file: %s", full_path)
        print(f"  Saving: {full_path}", flush=True)
        with open(full_path, "wb") as fh:
            fh.write(response.body)
        # Record the original download URL in memory; the map is persisted to
        # _url_map.json when the spider closes (see `closed()`).
        self._url_maps.setdefault(save_dir, {})[filename] = response.url
        self._referer_maps.setdefault(save_dir, {})[filename] = referer

    @staticmethod
    def _unique_filename(directory, basename, ext):
        candidate = f"{basename}{ext}"
        counter = itertools.count()
        while os.path.exists(os.path.join(directory, candidate)):
            candidate = f"{basename}-{next(counter)}{ext}"
        return candidate

    def closed(self, reason):
        """Write accumulated filename→URL maps to disk when the spider finishes.

        Persisting the maps in a single write per directory avoids any
        partial-write issues that could arise from writing after every
        individual PDF download.
        """
        for save_dir, url_map in self._url_maps.items():
            url_map_path = os.path.join(save_dir, "_url_map.json")
            with open(url_map_path, "w", encoding="utf-8") as fh:
                json.dump(url_map, fh, indent=2, ensure_ascii=False)
            referer_map = self._referer_maps.get(save_dir, {})
            referer_map_path = os.path.join(save_dir, "_referer_map.json")
            with open(referer_map_path, "w", encoding="utf-8") as fh:
                json.dump(referer_map, fh, indent=2, ensure_ascii=False)

        # Write the list of all HTML pages crawled to the site directory.
        # Use the first save_dir with downloaded files, or derive the directory
        # from the seed URL when no files were downloaded.
        site_save_dirs = list(self._url_maps.keys())
        if site_save_dirs:
            pages_save_dir = site_save_dirs[0]
        else:
            # No PDFs found: determine site dir from the seed URL and write there.
            netloc = self.parsed_url.netloc.lower()
            subfolder = netloc.removeprefix("www.")
            pages_save_dir = os.path.join(self.output_dir, subfolder)
            os.makedirs(pages_save_dir, exist_ok=True)
        pages_path = os.path.join(pages_save_dir, "_crawled_pages.json")
        with open(pages_path, "w", encoding="utf-8") as fh:
            json.dump(self._crawled_pages, fh, indent=2, ensure_ascii=False)
