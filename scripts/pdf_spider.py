"""
PDF Spider – crawls a website and downloads all PDF files found.

Usage:
    scrapy runspider pdf_spider.py -a url=https://example.com -a output_dir=crawled_files

The spider respects a DOWNLOAD_DELAY of 1 second between requests and only
follows links within the same domain as the seed URL.
"""

import json
import scrapy
import urllib.parse
import re
import os
import itertools
from scrapy.http import Request


class PdfA11ySpider(scrapy.Spider):
    name = "pdf_a11y_crawler"
    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        "COOKIES_ENABLED": True,
    }

    # File extensions that should be downloaded rather than followed
    DOWNLOAD_EXTENSIONS = {
        ".pdf", ".docx", ".pptx", ".xlsx",
        ".doc", ".ppt", ".xls",
        ".epub", ".odt", ".ods", ".odp",
    }

    def __init__(self, url=None, output_dir="crawled_files", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.url = url
        self.output_dir = output_dir
        self.parsed_url = urllib.parse.urlparse(url)
        # Normalize to lowercase: HTTP hostnames are case-insensitive, but
        # Scrapy's OffsiteMiddleware compares the lowercase hostname of each
        # request against this list.  A mixed-case entry (e.g. "www.Ontario.ca")
        # would cause all follow-up links to be rejected.
        self.allowed_domains = [self.parsed_url.netloc.lower()]
        self.start_urls = [url]
        # Accumulate filename→URL mappings in memory; written to disk on close.
        # Keyed by save_dir so each site subdirectory gets its own map file.
        self._url_maps: dict = {}

    def _has_download_extension(self, path):
        _, ext = os.path.splitext(path.lower())
        return ext in self.DOWNLOAD_EXTENSIONS

    def parse(self, response):
        if not isinstance(response, scrapy.http.response.html.HtmlResponse):
            return

        print(f"Crawling: {response.url}", flush=True)

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
                yield Request(full_link, callback=self.save_pdf)
            else:
                path_lower = path.lower()
                if "recherche" in path_lower or "search" in path_lower:
                    self.logger.info("Skipping search page: %s", full_link)
                else:
                    yield response.follow(link, self.parse)

    def save_pdf(self, response):
        raw_path = response.url.split("/")[-1]
        basename, ext = os.path.splitext(os.path.basename(raw_path))
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
