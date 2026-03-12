"""
PDF crawl wrapper.

Runs the Scrapy pdf_spider for a given URL and updates the YAML manifest with
newly discovered files.  Already-crawled, unchanged files are skipped.

Usage:
    python crawl.py --url https://example.com
    python crawl.py --url https://example.com --manifest reports/manifest.yaml
    python crawl.py --url https://example.com --output-dir crawled_files --timeout 3600
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen
from urllib.error import URLError

# Ensure sibling scripts are importable
sys.path.insert(0, str(Path(__file__).parent))

from manifest import _md5, load_manifest, save_manifest, upsert_entry

# Ordered list of protocol prefixes to probe when a bare domain is supplied.
_URL_PREFIXES = [
    "https://",
    "https://www.",
    "http://",
    "http://www.",
]


def _site_folder(netloc: str) -> str:
    """Return a clean, normalized folder name derived from a URL hostname.

    Lowercases *netloc* and strips a leading ``www.`` prefix so that crawled
    files for ``www.Ontario.ca`` end up in ``crawled_files/ontario.ca/`` rather
    than ``crawled_files/www.Ontario.ca/``.

    Args:
        netloc: The network location component of a URL (e.g. ``www.ontario.ca``).

    Returns:
        A lowercase domain string without a leading ``www.`` prefix.
    """
    netloc = netloc.lower()
    return netloc.removeprefix("www.")


def normalize_url(url: str, timeout: int = 15) -> str:
    """Return a fully-qualified URL for *url*, probing protocol variants if needed.

    If *url* already starts with ``http://`` or ``https://`` it is returned
    with the hostname lowercased (HTTP hostnames are case-insensitive).
    Otherwise the function tries each entry in ``_URL_PREFIXES``
    (in order) and returns the first one that responds with an HTTP 2xx or 3xx
    status.  If none of the variants respond successfully, ``https://<url>`` is
    returned as a safe fallback so that the caller can still attempt the crawl.

    Args:
        url: A URL string (with or without a protocol prefix).
        timeout: Per-probe connection timeout in seconds.

    Returns:
        A URL string that begins with ``https://`` or ``http://``.
    """
    if url.startswith("http://") or url.startswith("https://"):
        # Normalize the hostname to lowercase so that mixed-case URLs such as
        # "https://www.Ontario.ca" are treated consistently everywhere.
        parsed = urlparse(url)
        if parsed.netloc and parsed.netloc != parsed.netloc.lower():
            url = urlunparse(parsed._replace(netloc=parsed.netloc.lower()))
        return url

    # Strip any leading slashes that might have been included accidentally.
    bare = url.lstrip("/")

    # Basic validation: must look like a domain (letters/digits/dots/hyphens,
    # contains at least one dot, no whitespace or path-traversal sequences).
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9.\-_/]*\.[a-zA-Z]{2,}', bare) or \
            '..' in bare or bare.startswith('.'):
        fallback = f"https://{bare}"
        print(f"Input '{url}' does not look like a domain; using '{fallback}'")
        return fallback

    for prefix in _URL_PREFIXES:
        candidate = f"{prefix}{bare}"
        try:
            with urlopen(candidate, timeout=timeout) as resp:  # noqa: S310
                if 200 <= resp.status < 400:
                    print(f"Resolved '{url}' → '{candidate}'")
                    return candidate
        except (URLError, OSError, ValueError):
            pass

    fallback = f"https://{bare}"
    print(f"No reachable variant found for '{url}'; using fallback '{fallback}'")
    return fallback


def _print_scrapy_log_tail(log_path: str, tail_lines: int = 50) -> None:
    """Print the last *tail_lines* lines of the Scrapy log file.

    Filters to ERROR/CRITICAL lines first; falls back to the raw tail when no
    errors are present.  This helps diagnose crawl failures (e.g. HTTP 403,
    DNS errors) that would otherwise be invisible in the GitHub Actions log.

    Args:
        log_path: Path to the Scrapy log file.
        tail_lines: Number of lines to print when no errors are found.
    """
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return

    if not lines:
        return

    error_lines = [
        line for line in lines if " ERROR " in line or " CRITICAL " in line
    ]
    if error_lines:
        print(f"\n--- Scrapy errors from {log_path} ---")
        for line in error_lines[-tail_lines:]:
            print(line, end="")
        print("--- end of Scrapy errors ---\n")
    else:
        print(f"\n--- Last {tail_lines} lines of {log_path} ---")
        for line in lines[-tail_lines:]:
            print(line, end="")
        print(f"--- end of {log_path} ---\n")


def run_scrapy(
    url: str,
    output_dir: str,
    timeout: int,
    spider_path: str,
    max_pages: int = 2500,
    log_path: str = "scrapy.log",
) -> None:
    """Invoke Scrapy as a subprocess with an optional wall-clock timeout.

    Args:
        url: Seed URL to crawl.
        output_dir: Directory where downloaded files are saved.
        timeout: Maximum wall-clock seconds before the subprocess is killed.
        spider_path: Path to the Scrapy spider file.
        max_pages: Maximum number of pages (URLs) to crawl before stopping.
            Passed to Scrapy via the ``CLOSESPIDER_PAGECOUNT`` setting.
            Defaults to 2500.
        log_path: Path to write the Scrapy log file.  Defaults to
            ``scrapy.log`` in the current working directory.
    """
    cmd = [
        sys.executable, "-m", "scrapy", "runspider",
        spider_path,
        "-a", f"url={url}",
        "-a", f"output_dir={output_dir}",
        "-s", f"CLOSESPIDER_PAGECOUNT={max_pages}",
        "--logfile", log_path,
    ]
    print(f"Running: {' '.join(cmd)}")
    failed = False
    try:
        subprocess.run(cmd, timeout=timeout, check=True)
    except subprocess.TimeoutExpired:
        print(f"Scrapy timed out after {timeout}s – proceeding with partial results.")
        failed = True
    except subprocess.CalledProcessError as exc:
        print(f"Scrapy exited with code {exc.returncode} – proceeding with partial results.")
        failed = True

    if failed:
        _print_scrapy_log_tail(log_path)


def update_manifest(
    url: str,
    output_dir: str,
    manifest_path: str,
    notes: str = "",
) -> None:
    """Walk the crawled output directory and update the manifest."""
    parsed = urlparse(url)
    site = _site_folder(parsed.netloc)
    site_dir = Path(output_dir) / site

    entries = load_manifest(manifest_path)

    if not site_dir.exists():
        print(f"No files found in {site_dir}")
        save_manifest(entries, manifest_path)
        return

    new_count = 0
    updated_count = 0

    # Load the URL map written by the spider so we can use the real download
    # URL (including the full path) instead of a best-guess reconstruction.
    url_map: dict = {}
    url_map_path = site_dir / "_url_map.json"
    if url_map_path.exists():
        try:
            with open(url_map_path, "r", encoding="utf-8") as fh:
                url_map = json.load(fh)
        except (json.JSONDecodeError, OSError):
            url_map = {}

    for file_path in sorted(site_dir.iterdir()):
        if not file_path.is_file():
            continue
        if file_path.name == "_url_map.json":
            continue
        # Skip non-PDF files – the analyser only processes PDF documents.
        if file_path.suffix.lower() != ".pdf":
            print(f"  Skipping non-PDF file: {file_path.name}")
            continue
        # Prefer the actual URL recorded by the spider; fall back to the
        # best-guess "https://{site}/{filename}" only when the map is absent.
        file_url = url_map.get(file_path.name) or f"https://{site}/{file_path.name}"
        print(f"  Processing: {file_url}")
        entries, needs_scan = upsert_entry(entries, file_url, file_path, site, notes=notes)
        if needs_scan:
            new_count += 1
        else:
            updated_count += 1

    save_manifest(entries, manifest_path)
    print(
        f"Manifest updated: {new_count} new/changed file(s), "
        f"{updated_count} unchanged file(s)."
    )


def generate_crawled_urls_csv(
    url: str,
    output_dir: str,
    report_dir: str,
) -> int:
    """Generate a CSV listing every URL encountered during the crawl.

    Reads the ``_crawled_pages.json``, ``_url_map.json``, and
    ``_referer_map.json`` files written by the spider and produces a CSV at
    ``<report_dir>/crawled_urls.csv`` with three columns:

    * ``url``     – the full URL
    * ``type``    – ``page`` for HTML pages, ``pdf`` (or other document type)
                    for downloaded files
    * ``referer`` – the page that linked to this file (empty for HTML pages)

    Returns the number of HTML pages crawled.
    """
    parsed = urlparse(url)
    site = _site_folder(parsed.netloc)
    site_dir = Path(output_dir) / site

    crawled_pages: list = []
    pages_path = site_dir / "_crawled_pages.json"
    if pages_path.exists():
        try:
            with open(pages_path, encoding="utf-8") as fh:
                crawled_pages = json.load(fh)
        except (json.JSONDecodeError, OSError):
            crawled_pages = []

    url_map: dict = {}
    url_map_path = site_dir / "_url_map.json"
    if url_map_path.exists():
        try:
            with open(url_map_path, encoding="utf-8") as fh:
                url_map = json.load(fh)
        except (json.JSONDecodeError, OSError):
            url_map = {}

    referer_map: dict = {}
    referer_map_path = site_dir / "_referer_map.json"
    if referer_map_path.exists():
        try:
            with open(referer_map_path, encoding="utf-8") as fh:
                referer_map = json.load(fh)
        except (json.JSONDecodeError, OSError):
            referer_map = {}

    rows = []
    for page_url in crawled_pages:
        rows.append({"url": page_url, "type": "page", "referer": ""})
    for filename, file_url in sorted(url_map.items()):
        _, ext = os.path.splitext(filename.lower())
        file_type = ext.lstrip(".") if ext else "file"
        rows.append({
            "url": file_url,
            "type": file_type,
            "referer": referer_map.get(filename, ""),
        })

    def _write_csv(dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=["url", "type", "referer"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"Written: {dest}")

    # Write into the site directory so the file is included in the
    # crawled-files artifact and available to the analysis workflow.
    if site_dir.exists():
        _write_csv(site_dir / "crawled_urls.csv")

    # Also write into the report directory for immediate local access.
    _write_csv(Path(report_dir) / "crawled_urls.csv")

    return len(crawled_pages)


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl a website for PDFs")
    parser.add_argument("--url", required=True, help="Seed URL to crawl")
    parser.add_argument(
        "--manifest",
        default="reports/manifest.yaml",
        help="Path to the YAML manifest (default: reports/manifest.yaml)",
    )
    parser.add_argument(
        "--output-dir",
        default="crawled_files",
        help="Directory to store downloaded files (default: crawled_files)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Maximum seconds to spend crawling (default: 3600)",
    )
    parser.add_argument(
        "--spider",
        default=str(Path(__file__).parent / "pdf_spider.py"),
        help="Path to the Scrapy spider file",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Optional notes about this scan (e.g. organisation name, reason for scan)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=2500,
        help="Maximum number of pages (URLs) to crawl (default: 2500)",
    )
    parser.add_argument(
        "--report-dir",
        default="reports",
        help="Directory to write the crawled_urls.csv report into (default: reports)",
    )
    parser.add_argument(
        "--skip-crawl",
        action="store_true",
        help=(
            "Skip the Scrapy crawl and only update the manifest from already-downloaded "
            "files in --output-dir.  Useful when retrying a push after a merge conflict: "
            "the crawled files are still on disk, so only the manifest re-merge is needed."
        ),
    )
    args = parser.parse_args()

    # Ensure output and reports directories exist
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)

    # Normalise the URL – prepend a protocol and probe variants when needed.
    url = normalize_url(args.url)

    log_path = "scrapy.log"
    if args.skip_crawl:
        print(f"Skipping crawl for {url} – updating manifest from existing files only.")
    else:
        print(f"Crawling {url} (timeout: {args.timeout}s, max pages: {args.max_pages})…")
        run_scrapy(url, args.output_dir, args.timeout, args.spider, args.max_pages, log_path)

    print("Updating manifest…")
    update_manifest(url, args.output_dir, args.manifest, notes=args.notes)

    print("Generating crawled URLs CSV…")
    pages_crawled = generate_crawled_urls_csv(url, args.output_dir, args.report_dir)
    print(f"Pages crawled: {pages_crawled}")

    if not args.skip_crawl and pages_crawled == 0:
        print(
            "WARNING: No pages were crawled. The site may be blocking automated "
            "requests. Check the Scrapy log below for details."
        )
        _print_scrapy_log_tail(log_path)


if __name__ == "__main__":
    main()
