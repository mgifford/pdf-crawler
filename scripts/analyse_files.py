"""Analyse PDF files that are already on disk, without a crawl or a manifest.

For tools that find and download PDFs themselves and want the same checks
this repository's reports use, such as vital-core, which lists the PDFs each
site links to. Reads a JSON list of ``{"path", "url", "site", "link_text"}``
objects and writes one result per file, in the same order:

    {"engine": "original", "results": [
        {"url", "path", "status": "analysed" | "error", "report", "errors", "seconds"}
    ]}

``report`` is the dict ``check_file()`` returns; ``errors`` holds why a file
was not analysed, or the analyser's notes for one that was.

Usage:
    python scripts/analyse_files.py --input files.json --output results.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

from pdf_analyser import _analyse_with_process_timeout, classify_document_category


def _is_pdf(path: Path) -> bool:
    # A download can be an HTML error page saved under a .pdf name, so check
    # the header (which may follow a few junk bytes) rather than the suffix.
    with path.open("rb") as fh:
        return b"%PDF-" in fh.read(1024)


def analyse_files(
    items: Iterable[Dict[str, Any]],
    per_file_timeout: int = 120,
    max_file_size_mb: float = 200.0,
    crawler_version: str = "original",
    run_verapdf_check: bool = False,
) -> List[Dict[str, Any]]:
    """Analyse each file in *items* and return one result dict per item."""
    results = []
    for item in items:
        path = Path(item["path"])
        url = item.get("url") or str(path)
        result: Dict[str, Any] = {
            "url": url, "path": str(path), "status": "error", "report": None, "errors": [], "seconds": 0.0,
        }
        results.append(result)
        if not path.is_file():
            result["errors"] = [f"File not found: {path}"]
            continue
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > max_file_size_mb:
            result["errors"] = [f"File too large to analyse: {size_mb:.1f} MB (limit: {max_file_size_mb:.0f} MB)"]
            continue
        if not _is_pdf(path):
            result["errors"] = ["Not a PDF file: no %PDF- header"]
            continue

        start = time.monotonic()
        try:
            report = _analyse_with_process_timeout(
                str(path), item.get("site"), per_file_timeout, run_verapdf_check, crawler_version
            )
        except (TimeoutError, RuntimeError) as exc:
            result["errors"] = [str(exc)]
        else:
            category = classify_document_category(url, path.name, item.get("link_text", ""))
            if category is not None:
                report["DocCategory"] = category
            log = report.pop("_log", "")
            result.update(status="analysed", report=report, errors=[log] if log else [])
        result["seconds"] = round(time.monotonic() - start, 2)
    return results


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyse PDF files already on disk for accessibility issues")
    parser.add_argument("--input", required=True, help='JSON list of {"path", "url", "site", "link_text"}')
    parser.add_argument("--output", default="-", help="Where to write the results JSON (default: stdout)")
    parser.add_argument("--per-file-timeout", type=int, default=120, help="Seconds allowed per file (default: 120)")
    parser.add_argument("--max-file-size", type=float, default=200.0, help="Skip files larger than this in MB (default: 200)")
    parser.add_argument("--crawler-version", choices=["original", "bloom"], default="original",
                        help="Checks to run: this repository's (original) or simplA11yPDFCrawler's (bloom)")
    parser.add_argument("--verapdf", action="store_true", help="Also run veraPDF (PDF/UA-1) when it is on PATH")
    args = parser.parse_args(argv)

    items = json.loads(Path(args.input).read_text(encoding="utf-8"))
    results = analyse_files(items, args.per_file_timeout, args.max_file_size, args.crawler_version, args.verapdf)
    payload = json.dumps({"engine": args.crawler_version, "results": results}, indent=2, ensure_ascii=False, default=str)
    if args.output == "-":
        print(payload)
    else:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    analysed = sum(1 for r in results if r["status"] == "analysed")
    print(f"Analysed {analysed} of {len(results)} file(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
