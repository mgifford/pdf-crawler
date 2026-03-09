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
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

# Ensure sibling scripts are importable
sys.path.insert(0, str(Path(__file__).parent))

from manifest import _md5, load_manifest, save_manifest, upsert_entry


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
) -> None:
    """Walk the crawled output directory and update the manifest."""
    parsed = urlparse(url)
    site = parsed.netloc
    site_dir = Path(output_dir) / site

    if not site_dir.exists():
        print(f"No files found in {site_dir}")
        return

    entries = load_manifest(manifest_path)
    new_count = 0
    updated_count = 0

    for file_path in sorted(site_dir.iterdir()):
        if not file_path.is_file():
            continue
        # We record the URL as best-guess; the spider already saved the file
        file_url = f"https://{site}/{file_path.name}"
        entries, needs_scan = upsert_entry(entries, file_url, file_path, site)
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
    args = parser.parse_args()

    # Ensure output and reports directories exist
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)

    print(f"Crawling {args.url} (timeout: {args.timeout}s)…")
    run_scrapy(args.url, args.output_dir, args.timeout, args.spider)

    print("Updating manifest…")
    update_manifest(args.url, args.output_dir, args.manifest)


if __name__ == "__main__":
    main()
