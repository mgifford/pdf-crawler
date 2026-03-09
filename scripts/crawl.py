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


def run_scrapy(url: str, output_dir: str, timeout: int, spider_path: str) -> None:
    """Invoke Scrapy as a subprocess with an optional wall-clock timeout."""
    cmd = [
        sys.executable, "-m", "scrapy", "runspider",
        spider_path,
        "-a", f"url={url}",
        "-a", f"output_dir={output_dir}",
        "--logfile", "scrapy.log",
    ]
    print(f"Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, timeout=timeout, check=True)
    except subprocess.TimeoutExpired:
        print(f"Scrapy timed out after {timeout}s – proceeding with partial results.")
    except subprocess.CalledProcessError as exc:
        print(f"Scrapy exited with code {exc.returncode} – proceeding with partial results.")


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
    args = parser.parse_args()

    # Ensure output and reports directories exist
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)

    # Normalise the URL – prepend a protocol and probe variants when needed.
    url = normalize_url(args.url)

    print(f"Crawling {url} (timeout: {args.timeout}s)…")
    run_scrapy(url, args.output_dir, args.timeout, args.spider)

    print("Updating manifest…")
    update_manifest(url, args.output_dir, args.manifest, notes=args.notes)


if __name__ == "__main__":
    main()
