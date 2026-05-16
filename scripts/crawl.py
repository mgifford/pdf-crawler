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
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import urllib.robotparser
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse, urlunparse, urljoin
from urllib.request import urlopen, Request
from urllib.error import URLError

# Ensure sibling scripts are importable
sys.path.insert(0, str(Path(__file__).parent))

from manifest import load_manifest, save_manifest, upsert_entry

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


def is_pdf_url(url: str) -> bool:
    """Return True if *url* appears to be a direct link to a PDF file.

    Checks the path component of the URL (ignoring query strings and fragments)
    for a ``.pdf`` extension so that ``https://example.com/doc.pdf?id=1`` is
    still recognised as a PDF link.

    Args:
        url: A fully-qualified URL string.

    Returns:
        ``True`` when the URL path ends with ``.pdf`` (case-insensitive).
    """
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


def _broaden_seed_urls(url: str) -> list[str]:
    """Return broader crawl scopes for a path-specific seed URL.

    For a seed like ``https://example.com/a/b/c`` this yields:
    ``https://example.com/a/b``, ``https://example.com/a``, and
    ``https://example.com/``.

    Query parameters and fragments are removed from broadened URLs.
    """
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return []

    candidates: list[str] = []
    seen = set()
    for depth in range(len(segments) - 1, -1, -1):
        path = f"/{'/'.join(segments[:depth])}" if depth else "/"
        candidate = urlunparse(
            parsed._replace(path=path, params="", query="", fragment="")
        )
        if candidate != url and candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)
    return candidates


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


# User-Agent string used for spot-check HTTP requests.
# A specific (pinned) Chrome version is intentional: the goal is to look like
# a real browser to pass basic bot-detection rules.  The version number should
# be reviewed and updated periodically (e.g. when a major Chrome release ships)
# to remain plausible, but any recent Chrome version is acceptable here.
_SPOT_CHECK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_SPOT_CHECK_HEADERS = {
    "User-Agent": _SPOT_CHECK_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
_COMMON_SITEMAP_PATHS = [
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/wp-sitemap.xml",
]


def _extract_sitemap_urls_from_robots(content: str) -> list[str]:
    """Extract sitemap URLs from robots.txt content."""
    urls: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("sitemap:"):
            sitemap_url = line.split(":", 1)[1].strip()
            if sitemap_url:
                urls.append(sitemap_url)
    return urls


def _candidate_sitemap_urls(url: str, extra_urls: list[str] | None = None) -> list[str]:
    """Return candidate sitemap URLs for a site, ordered by likely usefulness."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [urljoin(base, path) for path in _COMMON_SITEMAP_PATHS]
    if extra_urls:
        candidates.extend(extra_urls)

    deduped: list[str] = []
    seen = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)
    return deduped


def spot_check_zero_results(url: str, timeout: int = 15) -> dict:
    """Probe a site when the crawler found zero pages to diagnose why.

    Makes three lightweight HTTP requests:

    1. **Seed URL** – reports the HTTP status code so callers can tell whether
       the site is outright unreachable (connection error), blocking automated
       requests (HTTP 4xx), or responding normally (2xx/3xx).
    2. **robots.txt** – parses the file and reports whether common crawler
       user-agents are disallowed from accessing the root path.
    3. **sitemap.xml** – fetches and parses the XML to count and sample any
       PDF URLs listed there; a non-empty sitemap confirms PDFs exist even
       though the link-following crawl could not reach them.

    All requests use a browser-like User-Agent and Accept headers to reduce
    the chance of being blocked by WAF rules that target bot user-agents.

    Args:
        url: The seed URL that was crawled (normalised, with protocol).
        timeout: Per-request connection timeout in seconds.

    Returns:
        A dict with the following keys:

        * ``seed_status`` (int | None) – HTTP status of the seed URL, or
          ``None`` if the request failed entirely.
        * ``robots_blocked`` (bool) – ``True`` when the site's ``robots.txt``
          disallows the root path for any of the common crawler agents checked.
        * ``robots_disallows`` (list[str]) – Disallow rules found in robots.txt
          that apply to the root path; empty when not blocked.
        * ``sitemap_pdf_count`` (int) – Number of PDF URLs found in sitemap.xml.
        * ``sitemap_pdf_samples`` (list[str]) – Up to five example PDF URLs from
          the sitemap.
        * ``sitemap_source`` (str) – Which sitemap URL produced the sample PDFs.
        * ``error`` (str) – Human-readable summary of any errors encountered.
    """
    result: dict = {
        "seed_status": None,
        "robots_blocked": False,
        "robots_disallows": [],
        "sitemap_pdf_count": 0,
        "sitemap_pdf_samples": [],
        "sitemap_source": "",
        "error": "",
    }
    errors = []
    robots_content = ""

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # 1. Probe the seed URL.
    try:
        req = Request(url, headers=_SPOT_CHECK_HEADERS)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            result["seed_status"] = resp.status
    except URLError as exc:
        errors.append(f"Seed URL probe failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Seed URL probe error: {exc}")

    # 2. Check robots.txt.
    robots_url = urljoin(base, "/robots.txt")
    try:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        # Fetch robots.txt manually so we can apply the browser UA and timeout.
        req = Request(robots_url, headers=_SPOT_CHECK_HEADERS)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            robots_content = resp.read().decode("utf-8", errors="replace")
        rp.parse(robots_content.splitlines())
        # Check whether any of the common crawler user-agent strings are
        # blocked from accessing the root path.  We check both the wildcard
        # agent (*) and the Scrapy default agent name.
        agents_to_check = ["*", "Scrapy", "python-urllib"]
        blocked_by = []
        for agent in agents_to_check:
            if not rp.can_fetch(agent, url):
                blocked_by.append(agent)
        if blocked_by:
            result["robots_blocked"] = True
            result["robots_disallows"] = blocked_by
    except URLError as exc:
        errors.append(f"robots.txt probe failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"robots.txt parse error: {exc}")

    # 3. Check sitemap locations for PDF URLs (including child sitemaps when a
    #    candidate document is a sitemap index).
    sitemap_errors = []
    robots_sitemap_urls = _extract_sitemap_urls_from_robots(robots_content)
    for sitemap_url in _candidate_sitemap_urls(url, extra_urls=robots_sitemap_urls):
        try:
            pdf_urls = _collect_sitemap_pdf_urls(sitemap_url, timeout=timeout)
            if pdf_urls:
                result["sitemap_pdf_count"] = len(pdf_urls)
                result["sitemap_pdf_samples"] = pdf_urls[:5]
                result["sitemap_source"] = sitemap_url
                break
        except URLError as exc:
            sitemap_errors.append(f"{sitemap_url}: {exc}")
        except Exception as exc:  # noqa: BLE001
            sitemap_errors.append(f"{sitemap_url}: {exc}")
    if sitemap_errors and result["sitemap_pdf_count"] == 0:
        errors.append("sitemap probes failed: " + "; ".join(sitemap_errors))

    if errors:
        result["error"] = "; ".join(errors)

    # Print a brief diagnostic summary to stdout so it appears in the
    # GitHub Actions log even when no issue comment is generated.
    print("\n--- Spot-check diagnostics (zero pages crawled) ---")
    status = result["seed_status"]
    if status is None:
        print(f"  Seed URL ({url}): unreachable")
    else:
        print(f"  Seed URL ({url}): HTTP {status}")
    if result["robots_blocked"]:
        print(f"  robots.txt: crawlers blocked ({', '.join(result['robots_disallows'])})")
    else:
        print("  robots.txt: no block detected")
    if result["sitemap_pdf_count"]:
        source = result["sitemap_source"] or "sitemap.xml"
        print(f"  {source}: {result['sitemap_pdf_count']} PDF(s) found")
        for sample in result["sitemap_pdf_samples"]:
            print(f"    {sample}")
    else:
        print("  sitemap.xml (and common alternatives): no PDFs found")
    if result["error"]:
        print(f"  Errors: {result['error']}")
    print("--- end spot-check ---\n")

    return result


def _extract_pdf_urls_from_sitemap(content: bytes) -> list:
    """Return all PDF URLs found in a sitemap XML document.

    Handles both standard sitemaps (``<urlset>``) and sitemap index files
    (``<sitemapindex>``).  Nested sitemap index files are not recursively
    fetched (only the top level is parsed) to avoid unbounded HTTP requests.

    Args:
        content: Raw bytes of the sitemap XML document.

    Returns:
        A list of PDF URL strings found in the sitemap.
    """
    pdf_urls: list[str] = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return pdf_urls

    # Strip the namespace prefix for simpler tag comparisons.
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    ns = {"sm": _SITEMAP_NS}

    if tag == "urlset":
        for loc in root.findall("sm:url/sm:loc", ns):
            if loc.text and loc.text.lower().endswith(".pdf"):
                pdf_urls.append(loc.text.strip())
    elif tag == "sitemapindex":
        # Sitemap index: list child sitemap URLs but do not fetch them —
        # just report their paths so we avoid additional HTTP requests.
        for loc in root.findall("sm:sitemap/sm:loc", ns):
            if loc.text and loc.text.lower().endswith(".pdf"):
                pdf_urls.append(loc.text.strip())

    return pdf_urls


def _collect_sitemap_pdf_urls(
    sitemap_url: str,
    timeout: int = 15,
    max_child_sitemaps: int = 20,
) -> list:
    """Fetch a sitemap and collect all PDF URLs, including from child sitemaps.

    If *sitemap_url* points to a sitemap index, up to *max_child_sitemaps*
    child sitemaps are fetched and parsed.  Only one level of nesting is
    followed to avoid unbounded HTTP requests.

    Unlike ``_extract_pdf_urls_from_sitemap`` (which only parses a pre-fetched
    bytes blob), this function performs the HTTP request(s) itself and will
    raise ``URLError`` when the top-level sitemap is unreachable.

    Args:
        sitemap_url: URL of the sitemap (or sitemap index) to fetch.
        timeout: Per-request connection timeout in seconds.
        max_child_sitemaps: Maximum number of child sitemaps to fetch when the
            root document is a sitemap index.  Defaults to 20.

    Returns:
        A list of PDF URL strings found in the sitemap(s).

    Raises:
        URLError: When the top-level sitemap cannot be fetched.
    """
    req = Request(sitemap_url, headers=_SPOT_CHECK_HEADERS)
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310
        content = resp.read()

    # Collect PDFs from the top-level document (handles both urlset and
    # the rare case of a sitemap index whose child entries are .pdf files).
    pdf_urls = _extract_pdf_urls_from_sitemap(content)

    # If the document is a sitemap index, also fetch each child sitemap.
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return pdf_urls

    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if tag != "sitemapindex":
        return pdf_urls

    ns = {"sm": _SITEMAP_NS}
    child_urls = [
        loc.text.strip()
        for loc in root.findall("sm:sitemap/sm:loc", ns)
        if loc.text and not loc.text.strip().lower().endswith(".pdf")
    ]
    for child_url in child_urls[:max_child_sitemaps]:
        try:
            req = Request(child_url, headers=_SPOT_CHECK_HEADERS)
            with urlopen(req, timeout=timeout) as resp:  # noqa: S310
                child_content = resp.read()
            pdf_urls.extend(_extract_pdf_urls_from_sitemap(child_content))
        except (URLError, OSError) as exc:
            print(f"  Child sitemap fetch failed ({child_url}): {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"  Child sitemap parse error ({child_url}): {exc}")

    return pdf_urls


def fetch_sitemap_pdfs(
    url: str,
    output_dir: str,
    max_pdfs: int,
    timeout: int = 3600,
) -> int:
    """Download PDFs listed in the site's sitemap.xml.

    Used as a fallback for sites whose PDFs are not linked from any HTML page
    but are listed in sitemap.xml (or a sitemap index).  Handles both standard
    sitemaps and sitemap index files (one level of nesting).

    Already-downloaded files (present in the site output directory) are
    skipped so that re-running the crawler does not re-fetch unchanged PDFs.

    Args:
        url: Seed URL of the site (used to derive the sitemap URL and
            output directory).
        output_dir: Directory where downloaded files are saved.
        max_pdfs: Maximum number of PDFs to download.
        timeout: Upper bound (in seconds) used to derive the per-request HTTP
            timeout: each individual HTTP request is capped at
            ``min(timeout, 60)`` seconds.  There is no enforced wall-clock
            limit on the total download time.

    Returns:
        The number of PDFs successfully downloaded.
    """
    parsed = urlparse(url)
    per_request_timeout = min(timeout, 60)
    pdf_urls: list[str] = []
    sitemap_errors: list[str] = []
    sitemap_candidates = _candidate_sitemap_urls(url)

    for sitemap_url in sitemap_candidates:
        print(f"Fetching PDF list from sitemap: {sitemap_url}")
        try:
            pdf_urls = _collect_sitemap_pdf_urls(
                sitemap_url, timeout=per_request_timeout
            )
            if pdf_urls:
                break
        except (URLError, OSError) as exc:
            sitemap_errors.append(f"{sitemap_url}: {exc}")
        except Exception as exc:  # noqa: BLE001
            sitemap_errors.append(f"{sitemap_url}: {exc}")

    if not pdf_urls:
        if sitemap_errors:
            print("  Sitemap fetch failures:")
            for error in sitemap_errors:
                print(f"    - {error}")
        print("  No PDFs found in sitemap.")
        return 0

    total = len(pdf_urls)
    limited = pdf_urls[:max_pdfs]
    if total > max_pdfs:
        print(f"  Found {total} PDF(s) in sitemap; downloading up to {max_pdfs}.")
    else:
        print(f"  Found {total} PDF(s) in sitemap; downloading all.")

    netloc = parsed.netloc.lower()
    subfolder = netloc.removeprefix("www.")
    save_dir = Path(output_dir) / subfolder
    save_dir.mkdir(parents=True, exist_ok=True)

    # Load any URL map written by an earlier spider run so we can skip files
    # that were already downloaded during link-following.
    url_map_path = save_dir / "_url_map.json"
    url_map: dict = {}
    if url_map_path.exists():
        try:
            with open(url_map_path, encoding="utf-8") as fh:
                url_map = json.load(fh)
        except (json.JSONDecodeError, OSError):
            url_map = {}

    already_downloaded = set(url_map.values())
    downloaded = 0

    for pdf_url in limited:
        if pdf_url in already_downloaded:
            continue

        pdf_path = urlparse(pdf_url).path
        segments = [s for s in pdf_path.split("/") if s]
        raw_name = segments[-1] if segments else (
            "doc-" + hashlib.md5(pdf_url.encode()).hexdigest()[:8]
        )
        basename, ext = os.path.splitext(raw_name)
        if not ext:
            ext = ".pdf"

        # Ensure the filename is unique within the save directory.
        candidate = f"{basename}{ext}"
        counter = itertools.count()
        while (save_dir / candidate).exists():
            candidate = f"{basename}-{next(counter)}{ext}"
        filename = candidate
        full_path = save_dir / filename

        try:
            req = Request(pdf_url, headers=_SPOT_CHECK_HEADERS)
            with urlopen(req, timeout=per_request_timeout) as resp:  # noqa: S310
                data = resp.read()
            with open(full_path, "wb") as fh:
                fh.write(data)
            url_map[filename] = pdf_url
            already_downloaded.add(pdf_url)
            downloaded += 1
            print(f"  [{downloaded}/{len(limited)}] Downloaded: {pdf_url}")
        except (URLError, OSError) as exc:
            print(f"  Failed to download {pdf_url}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"  Error downloading {pdf_url}: {exc}")

    # Persist the updated URL map so update_manifest can read it.
    with open(url_map_path, "w", encoding="utf-8") as fh:
        json.dump(url_map, fh, indent=2, ensure_ascii=False)

    # Create empty referer/anchor maps if absent so update_manifest succeeds.
    for map_name in ("_referer_map.json", "_anchor_map.json"):
        map_path = save_dir / map_name
        if not map_path.exists():
            with open(map_path, "w", encoding="utf-8") as fh:
                json.dump({}, fh)

    print(
        f"Sitemap download complete: {downloaded} of {len(limited)} PDF(s)"
        f" saved to {save_dir}"
    )
    return downloaded


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

    # Load the anchor-text map written by the spider (filename → link text).
    # This captures the visible text of the <a> tag that linked to each PDF,
    # which is useful for document category classification (inspired by the
    # approach taken by Code for America's asap_pdf project).
    anchor_map: dict = {}
    anchor_map_path = site_dir / "_anchor_map.json"
    if anchor_map_path.exists():
        try:
            with open(anchor_map_path, "r", encoding="utf-8") as fh:
                anchor_map = json.load(fh)
        except (json.JSONDecodeError, OSError):
            anchor_map = {}

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
        link_text = anchor_map.get(file_path.name, "")
        print(f"  Processing: {file_url}")
        entries, needs_scan = upsert_entry(
            entries, file_url, file_path, site, notes=notes, link_text=link_text
        )
        if needs_scan:
            new_count += 1
        else:
            updated_count += 1

    save_manifest(entries, manifest_path)
    print(
        f"Manifest updated: {new_count} new/changed file(s), "
        f"{updated_count} unchanged file(s)."
    )


def _count_downloaded_pdfs(site_dir: Path) -> int:
    """Count downloaded PDFs in a site directory."""
    if not site_dir.exists():
        return 0
    return sum(1 for f in site_dir.iterdir() if f.is_file() and f.suffix.lower() == ".pdf")


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
        "--max-pdfs",
        type=int,
        default=200,
        help=(
            "Maximum number of PDFs to download from sitemap.xml when the "
            "spider finds no PDFs via link-following (default: 200)"
        ),
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

    # Reject direct PDF links early – the spider crawls *websites* to discover
    # PDFs, so starting from a PDF file itself makes no sense and would result
    # in zero pages crawled and a confusing empty report.
    if is_pdf_url(url):
        print(
            f"ERROR: '{url}' is a direct link to a PDF file.\n"
            "This tool is designed to crawl websites and discover PDF files "
            "linked from HTML pages.\n"
            "Please provide the URL of a website (e.g. https://example.com) "
            "rather than a direct PDF link."
        )
        sys.exit(1)

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

    if not args.skip_crawl:
        # Count PDFs already downloaded by the spider so we can decide whether
        # to fall back to the sitemap.
        parsed_seed = urlparse(url)
        site_folder = _site_folder(parsed_seed.netloc)
        site_dir = Path(args.output_dir) / site_folder
        pdf_count = _count_downloaded_pdfs(site_dir)

        if pdf_count == 0:
            # The spider found no PDFs – either it could not crawl any pages
            # (blocked by WAF/robots.txt) or the site serves PDFs only via
            # sitemap rather than through navigable HTML links.
            if pages_crawled == 0:
                print(
                    "WARNING: No pages were crawled. The site may be blocking automated "
                    "requests. Check the Scrapy log below for details."
                )
                _print_scrapy_log_tail(log_path)

            # Attempt to download PDFs directly from sitemap.xml.
            print(
                "No PDFs found via link-following. "
                "Checking sitemap.xml for PDF links…"
            )
            sitemap_fetched = fetch_sitemap_pdfs(
                url, args.output_dir, args.max_pdfs, args.timeout
            )
            if sitemap_fetched > 0:
                print(
                    f"Downloaded {sitemap_fetched} PDF(s) from sitemap. "
                    "Updating manifest…"
                )
                update_manifest(url, args.output_dir, args.manifest, notes=args.notes)
                pdf_count = _count_downloaded_pdfs(site_dir)

        if pdf_count == 0:
            # If the user submitted a deep page URL and that scope produced no
            # PDFs, retry from progressively broader parent paths so we can
            # still return nearby/domain-level PDFs instead of an empty result.
            for broader_url in _broaden_seed_urls(url):
                print(
                    "No PDFs found from the submitted URL scope. "
                    f"Retrying crawl with broader scope: {broader_url}"
                )
                run_scrapy(
                    broader_url,
                    args.output_dir,
                    args.timeout,
                    args.spider,
                    args.max_pages,
                    log_path,
                )
                update_manifest(
                    broader_url,
                    args.output_dir,
                    args.manifest,
                    notes=args.notes,
                )
                pages_crawled = generate_crawled_urls_csv(
                    broader_url, args.output_dir, args.report_dir
                )
                print(f"Pages crawled from broader scope: {pages_crawled}")
                pdf_count = _count_downloaded_pdfs(site_dir)
                if pdf_count > 0:
                    print(
                        f"Broader scope succeeded ({pdf_count} PDF(s), "
                        f"{pages_crawled} page(s) crawled)."
                    )
                    break

        if pages_crawled == 0:
            # Run a lightweight spot-check to diagnose why the crawl found
            # nothing and persist the results so the analysis workflow can
            # surface them in the issue comment.
            print("Running spot-check diagnostics…")
            spot = spot_check_zero_results(url)
            spot_check_path = Path("scan-meta") / "spot_check.json"
            spot_check_path.parent.mkdir(parents=True, exist_ok=True)
            spot_check_path.write_text(
                json.dumps(spot, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"Spot-check saved: {spot_check_path}")


if __name__ == "__main__":  # pragma: no cover
    main()
