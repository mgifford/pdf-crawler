"""
Report generator.

Reads the YAML manifest and produces:
  - reports/report.md   – human-readable Markdown summary
  - reports/report.json – machine-readable JSON summary
  - reports/report_structured.json – structured rule-based JSON summary
  - reports/report.csv  – CSV for spreadsheet consumption

Usage:
    python generate_report.py [--manifest reports/manifest.yaml]
    python generate_report.py --site energy.gov --issue-comment-file /tmp/comment.md
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).parent))
from manifest import load_manifest
from structured_report import build_json_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = "✅ Pass"
_FAIL = "❌ Fail"
_NA = "—"

_EXPANDED_CHECKS: List[tuple[str, str]] = [
  ("TaggedContentTest", "TaggedContent"),
  ("FormsTest", "Forms"),
  ("TaggedFormFieldsTest", "TaggedForms"),
  ("TaggedAnnotationsTest", "TaggedAnnots"),
  ("FiguresAltTextTest", "FiguresAlt"),
  ("HeadingsTest", "Headings"),
  ("ListsTest", "Lists"),
  ("TablesTest", "Tables"),
]


def _entry_for_engine(entry: Dict[str, Any], engine: str) -> Dict[str, Any]:
    """Return a report-ready entry view for a specific engine result."""
    analyses = entry.get("analyses") if isinstance(entry.get("analyses"), dict) else {}
    engine_data = analyses.get(engine) if isinstance(analyses, dict) else None
    if not isinstance(engine_data, dict):
        status = entry.get("status")
        report = entry.get("report")
        errors = entry.get("errors")
    else:
        status = engine_data.get("status")
        report = engine_data.get("report")
        errors = engine_data.get("errors")

    return {
        **entry,
        "status": status,
        "report": report,
        "errors": errors or [],
        "engine": engine,
        "engine_status": status,
    }


def _expanded_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return entries flattened per engine for reporting and comparison."""
    expanded: List[Dict[str, Any]] = []
    for entry in entries:
        analyses = entry.get("analyses")
        if isinstance(analyses, dict) and analyses:
            for engine in sorted(analyses.keys()):
                expanded.append(_entry_for_engine(entry, engine))
        else:
            expanded.append(_entry_for_engine(entry, "original"))
    return expanded


def _entries_for_reporting(
    raw_entries: List[Dict[str, Any]],
    engine_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return report entries scoped to a single engine when requested."""
    if engine_filter in ("original", "bloom"):
        return [_entry_for_engine(entry, engine_filter) for entry in raw_entries]
    return _expanded_entries(raw_entries)


def _engine_comparison(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a comparison summary for files analysed by both engines."""
    by_url: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for entry in entries:
        analyses = entry.get("analyses")
        if not isinstance(analyses, dict):
            continue
        by_url[entry.get("url", "")] = analyses

    comparable = 0
    accessible_disagreements = 0
    tagged_disagreements = 0
    title_disagreements = 0
    language_disagreements = 0
    bookmarks_disagreements = 0

    for analyses in by_url.values():
        original = analyses.get("original")
        bloom = analyses.get("bloom")
        if not isinstance(original, dict) or not isinstance(bloom, dict):
            continue
        if original.get("status") != "analysed" or bloom.get("status") != "analysed":
            continue
        r1 = original.get("report") or {}
        r2 = bloom.get("report") or {}
        comparable += 1
        if r1.get("Accessible") != r2.get("Accessible"):
            accessible_disagreements += 1
        if r1.get("TaggedTest") != r2.get("TaggedTest"):
            tagged_disagreements += 1
        if r1.get("TitleTest") != r2.get("TitleTest"):
            title_disagreements += 1
        if r1.get("LanguageTest") != r2.get("LanguageTest"):
            language_disagreements += 1
        if r1.get("BookmarksTest") != r2.get("BookmarksTest"):
            bookmarks_disagreements += 1

    return {
        "comparable_files": comparable,
        "accessible_disagreements": accessible_disagreements,
        "tagged_disagreements": tagged_disagreements,
        "title_disagreements": title_disagreements,
        "language_disagreements": language_disagreements,
        "bookmarks_disagreements": bookmarks_disagreements,
    }


def generate_structured_json(entries: List[Dict[str, Any]], stats: Dict[str, Any]) -> Dict[str, Any]:
    """Return structured-report JSON for analysed manifest entries.

    Args:
        entries: Manifest entries loaded from ``reports/manifest.yaml``.
            Only entries with ``status == "analysed"`` are included in the
            structured output.
        stats: Aggregate summary stats produced by ``_summary_stats``.

    Returns:
        A dictionary with:
            - ``generated_at`` timestamp
            - ``summary`` counts for total/analysed/structured files
            - ``files`` list where each item includes:
                - file identity fields (url/filename/site/status)
                - ``structured_report`` (normal mode)
                - ``structured_report_compatible`` (compatible mode)
    """
    files: List[Dict[str, Any]] = []
    for e in entries:
        if e.get("status") != "analysed":
            continue
        report = dict(e.get("report") or {})
        report.setdefault("File", e.get("filename"))
        report.setdefault("Site", e.get("site"))
        files.append(
            {
                "url": e.get("url", ""),
                "filename": e.get("filename", ""),
                "site": e.get("site", ""),
                "status": e.get("status", ""),
                "structured_report": build_json_report(
                    report,
                    compatible=False,
                    debug=False,
                ),
                "structured_report_compatible": build_json_report(
                    report,
                    compatible=True,
                    debug=False,
                ),
            }
        )

    return {
        "generated_at": stats.get("generated_at"),
        "summary": {
            "total_files": stats.get("total_files", 0),
            "analysed": stats.get("analysed", 0),
            "files_with_structured_report": len(files),
        },
        "files": files,
    }


def _fmt(value) -> str:
    if value is None:
        return _NA
    if isinstance(value, bool):
        return _PASS if value else _FAIL
    if value == "Pass":
        return _PASS
    if value == "Fail":
        return _FAIL
    return str(value)


def _human_size(size_bytes: Optional[Any]) -> str:
    """Return a human-readable file size string from bytes."""
    try:
        size = int(size_bytes)
    except (TypeError, ValueError):
        return _NA
    if size < 0:
        return _NA
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def _with_lang(url: str, default_lang: Optional[str]) -> str:
    """Return *url* with ?lang=<en|fr> set when a supported language is given."""
    lang = (default_lang or "").strip().lower()
    if lang not in {"en", "fr"}:
        return url
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params["lang"] = lang
    return urlunparse(parsed._replace(query=urlencode(params)))


def _external_domain(entry: Dict[str, Any]) -> str:
    """Return the PDF's host domain when it differs from the seed site, else ''.

    A subdomain of the seed site (e.g. ``files.example.com`` when the seed is
    ``example.com``) is not considered external.  Only a completely different
    hostname triggers a non-empty return value.

    Args:
        entry: A manifest entry dict with ``url`` and ``site`` keys.

    Returns:
        The PDF's hostname (without ``www.``) when it is an external domain,
        or an empty string when the PDF is hosted on the seed site.
    """
    url = entry.get("url", "")
    site = entry.get("site", "")
    if not url or not site:
        return ""
    pdf_host = urlparse(url).netloc.lower().removeprefix("www.")
    seed = site.lower().removeprefix("www.")
    if not pdf_host or pdf_host == seed or pdf_host.endswith("." + seed):
        return ""
    return pdf_host


def _summary_stats(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(entries)
    analysed = [e for e in entries if e.get("status") == "analysed"]
    pending = [e for e in entries if e.get("status") == "pending"]
    errored = [e for e in entries if e.get("status") == "error"]

    accessible = sum(
        1
        for e in analysed
        if e.get("report") and e["report"].get("Accessible") is True
    )
    issues_found = max(0, len(analysed) - accessible)
    totally_inaccessible = sum(
        1
        for e in analysed
        if e.get("report") and e["report"].get("TotallyInaccessible") is True
    )
    broken = sum(
        1
        for e in analysed
        if e.get("report") and e["report"].get("BrokenFile") is True
    )
    exempt = sum(
        1
        for e in analysed
        if e.get("report") and e["report"].get("Exempt") is True
    )

    sites: Dict[str, int] = {}
    for e in entries:
        site = e.get("site", "unknown")
        sites[site] = sites.get(site, 0) + 1

    engine_counts: Dict[str, int] = {}
    for e in entries:
        engine = e.get("engine", "original")
        engine_counts[engine] = engine_counts.get(engine, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_files": total,
        "analysed": len(analysed),
        "pending": len(pending),
        "errored": len(errored),
        "accessible": accessible,
        "issues_found": issues_found,
        "totally_inaccessible": totally_inaccessible,
        "broken": broken,
        "exempt": exempt,
        "sites": sites,
        "engines": engine_counts,
        "pages_crawled": 0,
    }


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def _md_summary(stats: Dict[str, Any]) -> str:
    lines = [
        "# PDF Accessibility Scan Report",
        "",
        f"Generated: {stats['generated_at']}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Total files tracked | {stats['total_files']} |",
        f"| Analysed | {stats['analysed']} |",
        f"| Pending analysis | {stats['pending']} |",
        f"| Errors during analysis | {stats['errored']} |",
        f"| Accessible | {stats['accessible']} |",
        f"| Issues found | {stats['issues_found']} |",
        f"| Totally inaccessible subset | {stats['totally_inaccessible']} |",
        f"| Broken / unreadable | {stats['broken']} |",
        f"| Exempt (pre-2018) | {stats['exempt']} |",
        "",
    ]
    if stats.get("pages_crawled"):
        # Insert after the table header rows (header + separator = index 6 and 7)
        lines.insert(8, f"| URLs crawled | {stats['pages_crawled']} |")

    if stats["sites"]:
        lines += [
            "## Files per Site",
            "",
            "| Site | Files |",
            "|------|-------|",
        ]
        for site, count in sorted(stats["sites"].items()):
            lines.append(f"| {site} | {count} |")
        lines.append("")

    return "\n".join(lines)


def _md_file_table(entries: List[Dict[str, Any]]) -> str:
    analysed = [e for e in entries if e.get("status") == "analysed"]
    if not analysed:
        return "_No analysed files yet._\n"

    present_expanded = [
        (field, label)
        for field, label in _EXPANDED_CHECKS
        if any((e.get("report") or {}).get(field) is not None for e in analysed)
    ]

    header = (
        "## File Details\n\n"
        "| File | Site | Published Date | Doc Title | Author | Subject | Keywords"
        " | Accessible | Tagged | EmptyText | Protected"
        " | Title | Language | Bookmarks"
        + (" | " + " | ".join(label for _, label in present_expanded) if present_expanded else "")
        + " | Exempt | Pages | Size | Words | Images |\n"
        "|------|------|----------------|-----------|--------|---------|---------|"
        "------------|--------|-----------|---------|"
        "-------|----------|-----------"
        + ("|" + "|".join(["-------------"] * len(present_expanded)) if present_expanded else "")
        + "|--------|-------|------|-------|--------|\n"
    )

    rows = []
    for e in analysed:
        r = e.get("report") or {}
        url = e.get("url", "")
        filename = e.get("filename", url)
        site = e.get("site", "")
        ext_domain = _external_domain(e)
        site_str = f"{site} *(ext: {ext_domain})*" if ext_domain else site
        date_val = r.get("Date")
        date_str = str(date_val)[:10] if date_val else _NA
        row_parts = [
          f"| [{filename}]({url}) ",
          f"| {site_str} ",
          f"| {date_str} ",
          f"| {r.get('Title') or _NA} ",
          f"| {r.get('Author') or _NA} ",
          f"| {r.get('Subject') or _NA} ",
          f"| {r.get('Keywords') or _NA} ",
          f"| {_fmt(r.get('Accessible'))} ",
          f"| {_fmt(r.get('TaggedTest'))} ",
          f"| {_fmt(r.get('EmptyTextTest'))} ",
          f"| {_fmt(r.get('ProtectedTest'))} ",
          f"| {_fmt(r.get('TitleTest'))} ",
          f"| {_fmt(r.get('LanguageTest'))} ",
          f"| {_fmt(r.get('BookmarksTest'))} ",
        ]
        row_parts.extend(
          f"| {_fmt(r.get(field))} "
          for field, _ in present_expanded
        )
        row_parts.extend(
          [
            f"| {_fmt(r.get('Exempt'))} ",
            f"| {r.get('Pages', _NA)} ",
            f"| {_human_size(e.get('file_size_bytes'))} ",
            f"| {r.get('Words') if r.get('Words') is not None else _NA} ",
            f"| {r.get('Images') if r.get('Images') is not None else _NA} |",
          ]
        )
        rows.append("".join(row_parts))
    return header + "\n".join(rows) + "\n"


def _md_errors(entries: List[Dict[str, Any]]) -> str:
    errored = [e for e in entries if e.get("errors")]
    if not errored:
        return ""

    lines = ["## Files with Errors or Notes\n"]
    for e in errored:
        lines.append(f"### {e.get('filename', e.get('url', ''))}\n")
        lines.append(f"- **URL**: {e.get('url', '')}")
        lines.append(f"- **Status**: {e.get('status', '')}")
        for err in e.get("errors") or []:
            if err:
                lines.append(f"- {err}")
        lines.append("")
    return "\n".join(lines)


def generate_markdown(entries: List[Dict[str, Any]], stats: Dict[str, Any]) -> str:
    return (
        _md_summary(stats)
        + _md_file_table(entries)
        + "\n"
        + _md_errors(entries)
    )


# ---------------------------------------------------------------------------
# CSV report
# ---------------------------------------------------------------------------

_CSV_COLUMNS = [
    "url",
    "filename",
    "site",
    "external_domain",
  "file_size_bytes",
  "file_size_human",
    "status",
    "crawled_at",
    "published_date",
    "doc_title",
    "author",
    "subject",
    "keywords",
    "description",
    "accessible",
    "totally_inaccessible",
    "broken",
    "tagged",
    "empty_text",
    "protected",
    "title",
    "language",
    "bookmarks",
    "tagged_content",
    "forms",
    "tagged_form_fields",
    "tagged_annotations",
    "figures_alt_text",
    "headings",
    "lists",
    "tables",
    "exempt",
    "pages",
    "words",
    "images",
    "errors",
]


def generate_csv(entries: List[Dict[str, Any]]) -> str:
    """Return a CSV string with one row per manifest entry.

    Columns mirror the fields shown in the Markdown file table, using plain
    true/false/Pass/Fail values so the CSV is easy to import into a
    spreadsheet or process with standard tools.
    """
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=_CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()

    for e in entries:
        r = e.get("report") or {}
        errors = e.get("errors") or []
        writer.writerow(
            {
                "url": e.get("url", ""),
                "filename": e.get("filename", ""),
                "site": e.get("site", ""),
                "external_domain": _external_domain(e),
                "file_size_bytes": e.get("file_size_bytes", ""),
                "file_size_human": _human_size(e.get("file_size_bytes")),
                "status": e.get("status", ""),
                "crawled_at": e.get("crawled_at", ""),
                "published_date": r.get("Date", ""),
                "doc_title": r.get("Title", ""),
                "author": r.get("Author", ""),
                "subject": r.get("Subject", ""),
                "keywords": r.get("Keywords", ""),
                "description": r.get("Description", ""),
                "accessible": r.get("Accessible", ""),
                "totally_inaccessible": r.get("TotallyInaccessible", ""),
                "broken": r.get("BrokenFile", ""),
                "tagged": r.get("TaggedTest", ""),
                "empty_text": r.get("EmptyTextTest", ""),
                "protected": r.get("ProtectedTest", ""),
                "title": r.get("TitleTest", ""),
                "language": r.get("LanguageTest", ""),
                "bookmarks": r.get("BookmarksTest", ""),
                "tagged_content": r.get("TaggedContentTest", ""),
                "forms": r.get("FormsTest", ""),
                "tagged_form_fields": r.get("TaggedFormFieldsTest", ""),
                "tagged_annotations": r.get("TaggedAnnotationsTest", ""),
                "figures_alt_text": r.get("FiguresAltTextTest", ""),
                "headings": r.get("HeadingsTest", ""),
                "lists": r.get("ListsTest", ""),
                "tables": r.get("TablesTest", ""),
                "exempt": r.get("Exempt", ""),
                "pages": r.get("Pages", ""),
                "words": r.get("Words", ""),
                "images": r.get("Images", ""),
                "errors": "; ".join(str(err) for err in errors if err),
            }
        )

    return output.getvalue()


# ---------------------------------------------------------------------------
# Issue comment generator
# ---------------------------------------------------------------------------

_MAX_FILES_IN_COMMENT = 30


def _icon(value) -> str:
    if value is True:
        return "✅"
    if value is False:
        return "❌"
    if value == "Pass":
        return "✅"
    if value == "Fail":
        return "❌"
    return "—"


def generate_issue_comment(
    entries: List[Dict[str, Any]],
    crawl_url: str,
    pages_base: str,
    run_url: str,
    site_filter: Optional[str] = None,
    max_files: int = _MAX_FILES_IN_COMMENT,
    pages_crawled: int = 0,
    archive_name: Optional[str] = None,
    spot_check: Optional[Dict[str, Any]] = None,
    default_lang: Optional[str] = None,
) -> str:
    """Return a Markdown string suitable for posting as a GitHub issue comment.

    If *site_filter* is provided, only entries for that site are included in
    the per-file table (the summary counts use those same filtered entries).

    If *archive_name* is provided, the HTML report link points to the
    per-scan archived report (``{pages_base}/reports/{archive_name}``)
    rather than the cumulative ``report.html``.

    If *spot_check* is provided (a dict returned by ``spot_check_zero_results``
    in crawl.py), its findings are included in the diagnostic block that is
    shown when zero PDFs and zero pages were found.

    If *default_lang* is ``"fr"`` or ``"en"``, report page links in the
    comment include ``?lang=<value>`` so the UI opens in that language.
    """
    scoped = (
        [e for e in entries if e.get("site") == site_filter]
        if site_filter
        else entries
    )

    analysed = [e for e in scoped if e.get("status") == "analysed"]
    pending = [e for e in scoped if e.get("status") == "pending"]
    errored = [e for e in scoped if e.get("status") == "error"]
    accessible = sum(
        1 for e in analysed if e.get("report", {}).get("Accessible") is True
    )
    issues_found = len(analysed) - accessible

    lines: List[str] = [
        f"📊 **Accessibility analysis complete** for `{crawl_url}`.",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
    ]
    if pages_crawled:
        lines.append(f"| 🌐 URLs crawled | {pages_crawled} |")
    lines += [
        f"| Total PDFs found | {len(scoped)} |",
        f"| Analysed | {len(analysed)} |",
        f"| ✅ Accessible | {accessible} |",
        f"| ❌ Issues found | {issues_found} |",
    ]
    if pending:
        lines.append(f"| ⏳ Pending analysis | {len(pending)} |")
    if errored:
        lines.append(f"| ⚠️ Errors | {len(errored)} |")
    lines.append("")

    # Diagnostic note when no PDFs were found – helps submitters understand why.
    if not scoped:
        if pages_crawled == 0:
            lines += [
                "> ⚠️ **No PDFs were found and no pages could be visited.**",
                "> The site may be blocking automated requests, or the starting URL may be unreachable.",
                "> Check the [workflow run](" + run_url + ") logs for crawl errors.",
            ]
            # Include spot-check diagnostics when available.
            if spot_check:
                seed_status = spot_check.get("seed_status")
                robots_blocked = spot_check.get("robots_blocked", False)
                robots_disallows = spot_check.get("robots_disallows", [])
                sitemap_pdf_count = spot_check.get("sitemap_pdf_count", 0)
                sitemap_pdf_samples = spot_check.get("sitemap_pdf_samples", [])

                lines += [">", "> **Automated diagnostics:**"]

                if seed_status is None:
                    lines.append("> - 🔴 **Seed URL**: could not connect — the site may be down or blocking requests entirely.")
                elif seed_status < 400:
                    lines.append(f"> - 🟢 **Seed URL**: responded HTTP {seed_status} — the site is reachable with browser-like headers.")
                else:
                    lines.append(f"> - 🔴 **Seed URL**: responded HTTP {seed_status} — the site is actively blocking automated requests.")

                if robots_blocked:
                    blocked_agents = ", ".join(f"`{a}`" for a in robots_disallows)
                    lines.append(f"> - 🔴 **robots.txt**: crawl disallowed for {blocked_agents} — the site's robots.txt explicitly blocks automated crawlers.")
                else:
                    lines.append("> - 🟢 **robots.txt**: no crawler block detected.")

                if sitemap_pdf_count:
                    lines.append(f"> - 📄 **sitemap.xml**: {sitemap_pdf_count} PDF(s) found — PDFs exist but cannot be reached by link-following alone.")
                    for sample in sitemap_pdf_samples:
                        lines.append(f">   - `{sample}`")
                    lines.append(">   Consider submitting one of these PDF URLs' parent directory as the crawl starting point.")
                else:
                    lines.append("> - **sitemap.xml**: no PDF URLs listed.")

            lines += [
                ">",
                "> You may also try submitting a more specific starting URL (e.g. a `/documents` sub-page).",
                "",
            ]
        else:
            page_word = "page" if pages_crawled == 1 else "pages"
            lines += [
                f"> ⚠️ **No PDFs were found** after visiting {pages_crawled} {page_word}.",
                "> Common reasons include:",
                ">",
                "> - **JavaScript navigation** – PDFs linked only via JavaScript menus or dynamic content cannot be followed by the crawler.",
                "> - **Robots.txt restrictions** – the site may restrict crawler access to sections that contain PDFs.",
                "> - **External hosting** – PDFs served without a `.pdf` extension on a different domain may not be discovered. PDFs with a `.pdf` file extension on any domain are always followed.",
                "> - **Deeper pages** – try submitting a more specific starting URL (e.g. a `/documents` or `/resources` sub-page).",
                ">",
                f"> Review the [Crawled URLs]({pages_base}/reports/crawled_urls.csv) to see which pages were visited,",
                f"> and the [workflow run]({run_url}) logs for any crawl warnings.",
                "",
            ]

    if analysed:
        present_expanded = [
            (field, label)
            for field, label in _EXPANDED_CHECKS
            if any((e.get("report") or {}).get(field) is not None for e in analysed)
        ]
        lines += [
            "## PDFs Scanned",
            "",
            "| PDF | Accessible | Tagged | Title | Language | Bookmarks"
            + (" | " + " | ".join(label for _, label in present_expanded) if present_expanded else "")
          + " | Pages | Size | Words | Images |",
            "|-----|-----------|--------|-------|----------|-----------"
            + ("|" + "|".join(["-------------"] * len(present_expanded)) if present_expanded else "")
          + "|-------|------|-------|--------|",
        ]
        for e in analysed[:max_files]:
            r = e.get("report") or {}
            url = e.get("url", "")
            filename = e.get("filename", url.split("/")[-1])
            ext_domain = _external_domain(e)
            file_cell = f"[{filename}]({url})"
            if ext_domain:
                file_cell += f" *(ext: {ext_domain})*"
            words = r.get("Words")
            images = r.get("Images")
            row_parts = [
                f"| {file_cell}",
                f" | {_icon(r.get('Accessible'))}",
                f" | {_icon(r.get('TaggedTest'))}",
                f" | {_icon(r.get('TitleTest'))}",
                f" | {_icon(r.get('LanguageTest'))}",
                f" | {_icon(r.get('BookmarksTest'))}",
            ]
            row_parts.extend(
                f" | {_icon(r.get(field))}"
                for field, _ in present_expanded
            )
            row_parts.extend(
                [
                    f" | {r.get('Pages', '—')}",
                  f" | {_human_size(e.get('file_size_bytes'))}",
                    f" | {words if words is not None else '—'}",
                    f" | {images if images is not None else '—'} |",
                ]
            )
            lines.append("".join(row_parts))
        if len(analysed) > max_files:
            lines += [
                "",
                f"_… and {len(analysed) - max_files} more PDFs."
                " See the full report for details._",
            ]
        lines.append("")

    lines += [
        "## Full Reports",
        "",
    ]
    if archive_name and pages_base:
        archive_stem = archive_name[:-5] if archive_name.endswith(".html") else archive_name
        lines.append(
            f"- [Site-specific HTML report]({_with_lang(f'{pages_base}/reports/{archive_name}', default_lang)})"
        )
        lines.append(f"- [Site-specific JSON report]({pages_base}/reports/{archive_stem}/report.json)")
        lines.append(f"- [Site-specific structured JSON report]({pages_base}/reports/{archive_stem}/report_structured.json)")
        lines.append(f"- [Site-specific CSV report]({pages_base}/reports/{archive_stem}/report.csv)")
        lines.append(f"- [Site-specific YAML manifest]({pages_base}/reports/{archive_stem}/manifest.yaml)")
        lines.append(f"- [Site-specific crawled URLs CSV]({pages_base}/reports/{archive_stem}/crawled_urls.csv)")
        lines.append(f"- [Reports history]({_with_lang(f'{pages_base}/reports.html', default_lang)})")
    else:
        lines.append(f"- [HTML report]({_with_lang(f'{pages_base}/report.html', default_lang)})")
        lines += [
            f"- [Reports history]({_with_lang(f'{pages_base}/reports.html', default_lang)})",
            f"- [Markdown report]({pages_base}/reports/report.md)",
            f"- [JSON report]({pages_base}/reports/report.json)",
            f"- [Structured JSON report]({pages_base}/reports/report_structured.json)",
            f"- [CSV report]({pages_base}/reports/report.csv)",
            f"- [Crawled URLs CSV]({pages_base}/reports/crawled_urls.csv)",
            f"- [YAML manifest]({pages_base}/reports/manifest.yaml)",
        ]
    if run_url:
        lines.append(f"- [View workflow run]({run_url})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PDF Accessibility Scan Results</title>
  <meta name="description" content="PDF accessibility scan results showing accessible and inaccessible PDFs found on a website." />
  <!-- Open Graph (LinkedIn, Mastodon, Bluesky) -->
  <meta property="og:title" content="PDF Accessibility Scan Results" />
  <meta property="og:description" content="PDF accessibility scan results showing accessible and inaccessible PDFs found on a website." />
  <meta property="og:type" content="article" />
  <meta property="og:site_name" content="PDF Accessibility Crawler" />
  <!-- Twitter Card (also used by many other platforms) -->
  <meta name="twitter:card" content="summary" />
  <meta name="twitter:title" content="PDF Accessibility Scan Results" />
  <meta name="twitter:description" content="PDF accessibility scan results showing accessible and inaccessible PDFs found on a website." />
  <!-- Prevent flash of unstyled content when a saved theme preference is present -->
  <script>
    (function () {{
      var t = localStorage.getItem('theme');
      if (t === 'dark' || t === 'light') document.documentElement.setAttribute('data-theme', t);
    }})();
  </script>
  <style>
    /* ---- colour tokens ---- */
    :root {{
      color-scheme: light dark;
      --color-bg:           #f8f9fa;
      --color-fg:           #1a1a2e;
      --color-link:         #0d6efd;
      --color-card-bg:      #fff;
      --color-border:       #dee2e6;
      --color-th-bg:        #e9ecef;
      --color-row-stripe:   #f8f9fa;
      --color-muted:        #6c757d;
      --color-pass:         #198754;
      --color-fail:         #dc3545;
    }}

    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]) {{
        --color-bg:           #0d1117;
        --color-fg:           #e6edf3;
        --color-link:         #4493f8;
        --color-card-bg:      #161b22;
        --color-border:       #30363d;
        --color-th-bg:        #21262d;
        --color-row-stripe:   #161b22;
        --color-muted:        #8b949e;
        --color-pass:         #3fb950;
        --color-fail:         #f85149;
      }}
    }}

    [data-theme="dark"] {{
      --color-bg:           #0d1117;
      --color-fg:           #e6edf3;
      --color-link:         #4493f8;
      --color-card-bg:      #161b22;
      --color-border:       #30363d;
      --color-th-bg:        #21262d;
      --color-row-stripe:   #161b22;
      --color-muted:        #8b949e;
      --color-pass:         #3fb950;
      --color-fail:         #f85149;
    }}

    *, *::before, *::after {{ box-sizing: border-box; }}

    body {{
      font-family: system-ui, -apple-system, sans-serif;
      max-width: 1000px;
      margin: 0 auto;
      padding: 2rem 1rem;
      color: var(--color-fg);
      background: var(--color-bg);
    }}

    nav {{
      display: flex;
      align-items: center;
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    nav a {{ color: var(--color-link); text-decoration: none; }}
    nav a:hover {{ text-decoration: underline; }}

    .lang-switch {{
      font-size: 0.9rem;
      color: var(--color-muted);
      display: inline-flex;
      gap: 0.35rem;
      align-items: center;
    }}

    .lang-switch a[aria-current="page"] {{
      font-weight: 700;
      text-decoration: underline;
    }}

    .theme-toggle {{
      background: none;
      border: 1px solid var(--color-border);
      border-radius: 0.375rem;
      cursor: pointer;
      font-size: 1.1rem;
      padding: 0.25rem 0.5rem;
      line-height: 1;
      color: var(--color-fg);
      margin-left: auto;
    }}
    .theme-toggle:hover {{ background: var(--color-th-bg); }}

    h1 {{ color: var(--color-link); }}
    h2 {{ margin-top: 2rem; }}

    #generated-at {{ font-size: 0.85rem; color: var(--color-muted); margin-top: -0.5rem; }}

    .downloads {{
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex-wrap: wrap;
      margin: 1rem 0 1.25rem;
    }}
    .downloads a,
    .downloads button {{
      border: 1px solid var(--color-border);
      border-radius: 0.375rem;
      padding: 0.35rem 0.65rem;
      text-decoration: none;
      color: var(--color-link);
      background: var(--color-card-bg);
      font: inherit;
      cursor: pointer;
    }}
    .downloads a:hover,
    .downloads button:hover {{
      background: var(--color-th-bg);
    }}

    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 1rem;
      margin: 1.5rem 0;
    }}
    .stat-card {{
      background: var(--color-card-bg);
      border: 1px solid var(--color-border);
      border-radius: 0.375rem;
      padding: 1rem;
      text-align: center;
    }}
    .stat-card .value {{
      font-size: 2rem;
      font-weight: 700;
      color: var(--color-link);
      line-height: 1.1;
    }}
    .stat-card .label {{ font-size: 0.8rem; color: var(--color-muted); margin-top: 0.25rem; }}

    table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.9rem; }}
    th {{
      background: var(--color-th-bg);
      padding: 0.5rem 0.75rem;
      text-align: left;
      border-bottom: 2px solid var(--color-border);
    }}
    th.sortable {{
      cursor: pointer;
      user-select: none;
    }}
    th.sortable:hover {{ background: var(--color-border); }}
    td {{ padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--color-border); vertical-align: top; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:nth-child(even) td {{ background: var(--color-row-stripe); }}

    .pass {{ color: var(--color-pass); }}
    .fail {{ color: var(--color-fail); }}
    .na   {{ color: var(--color-muted); }}

    a {{ color: var(--color-link); }}

    .empty-state {{
      background: var(--color-card-bg);
      border: 1px solid var(--color-border);
      border-radius: 0.375rem;
      padding: 2rem;
      text-align: center;
      color: var(--color-muted);
    }}

    footer {{
      margin-top: 3rem;
      font-size: 0.8rem;
      color: var(--color-muted);
      border-top: 1px solid var(--color-border);
      padding-top: 1rem;
    }}
  </style>
</head>
<body>

  <nav>
    <a id="back-link" href="{back_url}">&#8592; {back_label}</a>
    <span class="lang-switch" aria-label="Language switch">
      <span id="lang-switch-label">Language: English</span>
      <a id="lang-en-report" data-lang="en" href="?lang=en" hreflang="en" lang="en">English</a>
      <span aria-hidden="true">|</span>
      <a id="lang-fr-report" data-lang="fr" href="?lang=fr" hreflang="fr" lang="fr">Français</a>
    </span>
    <button class="theme-toggle" id="theme-toggle" aria-label="Switch to dark mode" title="Switch to dark mode">&#127769;</button>
  </nav>

  <h1 id="page-title">&#128202; PDF Accessibility Scan Results</h1>
  <p id="generated-at"></p>

  <div class="downloads">
    <a id="download-csv-link" href="{report_assets_base}/report.csv">Download CSV</a>
    <a id="download-json-link" href="{report_assets_base}/report.json">Download JSON</a>
    <a id="download-manifest-link" href="{report_assets_base}/manifest.yaml">Download Manifest</a>
    <button id="download-current-csv" type="button">Download Current View CSV</button>
    <button id="download-current-json" type="button">Download Current View JSON</button>
  </div>

  <div id="root"></div>

  <script type="application/json" id="report-data">
{json_data}
  </script>

  <script>
    (function () {{
      // i18n scaffold: add new locales by extending I18N with the same keys.
      var I18N = {{
        en: {{
          langSwitchLabel: 'Language:',
          langEnglish: 'English',
          langFrench: 'Français',
          backLabel: 'Back to submission form',
          pageTitle: '📊 PDF Accessibility Scan Results',
          generatedAt: 'Last updated: ',
          summary: 'Summary',
          totalPdfs: 'Total PDFs',
          analysed: 'Analysed',
          accessible: '✅ Accessible',
          issuesFound: '❌ Issues Found',
          pending: '⏳ Pending',
          errors: '⚠️ Errors',
          sitesScanned: 'Sites Scanned',
          site: 'Site',
          pdfs: 'PDFs',
          pdfDetails: 'PDF Details',
          legend: '✅ = Pass/Accessible   ❌ = Fail/Inaccessible   — = Not applicable',
          noData: 'No scan data available yet.',
          submitPrompt: 'Submit a crawl request',
          submitSuffix: ' to get started.',
          downloadCsv: 'Download CSV',
          downloadJson: 'Download JSON',
          downloadManifest: 'Download Manifest',
          downloadCurrentCsv: 'Download Current View CSV',
          downloadCurrentJson: 'Download Current View JSON',
          colFile: 'File',
          colSite: 'Site',
          colPublishedDate: 'Published Date',
          colDocTitle: 'Doc Title',
          colAuthor: 'Author',
          colSubject: 'Subject',
          colKeywords: 'Keywords',
          colDescription: 'Description',
          colAccessible: 'Accessible',
          colTagged: 'Tagged',
          colTitle: 'Title',
          colLanguage: 'Language',
          colBookmarks: 'Bookmarks',
          colTaggedContent: 'TaggedContent',
          colForms: 'Forms',
          colTaggedForms: 'TaggedForms',
          colTaggedAnnots: 'TaggedAnnots',
          colFiguresAlt: 'FiguresAlt',
          colHeadings: 'Headings',
          colLists: 'Lists',
          colTables: 'Tables',
          colPages: 'Pages',
          colSize: 'Size',
          colWords: 'Words',
          colImages: 'Images',
        }},
        fr: {{
          langSwitchLabel: 'Langue :',
          langEnglish: 'English',
          langFrench: 'Français',
          backLabel: 'Retour au formulaire de soumission',
          pageTitle: '📊 Résultats du scan d\\'accessibilité PDF',
          generatedAt: 'Dernière mise à jour : ',
          summary: 'Résumé',
          totalPdfs: 'Total des PDF',
          analysed: 'Analysés',
          accessible: '✅ Accessibles',
          issuesFound: '❌ Problèmes détectés',
          pending: '⏳ En attente',
          errors: '⚠️ Erreurs',
          sitesScanned: 'Sites analysés',
          site: 'Site',
          pdfs: 'PDF',
          pdfDetails: 'Détails des PDF',
          legend: '✅ = Réussite/Accessible   ❌ = Échec/Inaccessible   — = Non applicable',
          noData: 'Aucune donnée de scan disponible pour le moment.',
          submitPrompt: 'Soumettre une demande de scan',
          submitSuffix: ' pour commencer.',
          downloadCsv: 'Télécharger le CSV',
          downloadJson: 'Télécharger le JSON',
          downloadManifest: 'Télécharger le manifeste',
          downloadCurrentCsv: 'Télécharger le CSV de la vue actuelle',
          downloadCurrentJson: 'Télécharger le JSON de la vue actuelle',
          colFile: 'Fichier',
          colSite: 'Site',
          colPublishedDate: 'Date de publication',
          colDocTitle: 'Titre du doc',
          colAuthor: 'Auteur',
          colSubject: 'Sujet',
          colKeywords: 'Mots-clés',
          colDescription: 'Description',
          colAccessible: 'Accessible',
          colTagged: 'Balisé',
          colTitle: 'Titre',
          colLanguage: 'Langue',
          colBookmarks: 'Signets',
          colTaggedContent: 'ContenuBalisé',
          colForms: 'Formulaires',
          colTaggedForms: 'FormulairesBalisés',
          colTaggedAnnots: 'AnnotationsBalisées',
          colFiguresAlt: 'TexteAltFigures',
          colHeadings: 'Titres',
          colLists: 'Listes',
          colTables: 'Tableaux',
          colPages: 'Pages',
          colSize: 'Taille',
          colWords: 'Mots',
          colImages: 'Images',
        }},
      }};

      function detectLang() {{
        var params = new URLSearchParams(window.location.search);
        var fromQuery = params.get('lang');
        if (fromQuery && I18N[fromQuery]) return fromQuery;
        var stored = localStorage.getItem('lang');
        if (stored && I18N[stored]) return stored;
        var navLang = (navigator.language || 'en').toLowerCase();
        return navLang.indexOf('fr') === 0 ? 'fr' : 'en';
      }}

      var currentLang = detectLang();
      var M = I18N[currentLang] || I18N.en;
      document.documentElement.setAttribute('lang', currentLang);
      localStorage.setItem('lang', currentLang);

      function t(key) {{
        return M[key] || I18N.en[key] || key;
      }}

      function withLang(url, langCode) {{
        try {{
          var parsed = new URL(url, window.location.href);
          parsed.searchParams.set('lang', langCode || currentLang);
          return parsed.toString();
        }} catch (e) {{
          return url;
        }}
      }}

      function applyLanguageStatic() {{
        var backLink = document.getElementById('back-link');
        if (backLink) backLink.textContent = '← ' + t('backLabel');
        var pageTitle = document.getElementById('page-title');
        if (pageTitle) pageTitle.textContent = t('pageTitle');
        var csvLink = document.getElementById('download-csv-link');
        if (csvLink) csvLink.textContent = t('downloadCsv');
        var jsonLink = document.getElementById('download-json-link');
        if (jsonLink) jsonLink.textContent = t('downloadJson');
        var manifestLink = document.getElementById('download-manifest-link');
        if (manifestLink) manifestLink.textContent = t('downloadManifest');
        var curCsv = document.getElementById('download-current-csv');
        if (curCsv) curCsv.textContent = t('downloadCurrentCsv');
        var curJson = document.getElementById('download-current-json');
        if (curJson) curJson.textContent = t('downloadCurrentJson');

        var langLabelNode = document.getElementById('lang-switch-label');
        var currentLangName = currentLang === 'fr' ? t('langFrench') : t('langEnglish');
        if (langLabelNode) langLabelNode.textContent = t('langSwitchLabel') + ' ' + currentLangName;

        var langLinks = document.querySelectorAll('.lang-switch a[data-lang]');
        langLinks.forEach(function (link) {{
          var code = (link.getAttribute('data-lang') || '').toLowerCase();
          if (!code) return;
          link.href = withLang(window.location.href, code);
          if (code === 'en') link.textContent = t('langEnglish');
          else if (code === 'fr') link.textContent = t('langFrench');
          else link.textContent = code.toUpperCase();
          if (code === currentLang) link.setAttribute('aria-current', 'page');
          else link.removeAttribute('aria-current');
        }});
      }}

      applyLanguageStatic();

      var raw  = document.getElementById('report-data').textContent;
      var data = JSON.parse(raw);
      var summary = data.summary || {{}};
      var files   = data.files   || [];
      var root    = document.getElementById('root');

      if (!summary.total_files) {{
        root.innerHTML =
          '<div class="empty-state">' +
          '<p>' + esc(t('noData')) + '</p>' +
          '<p><a href="./">' + esc(t('submitPrompt')) + '</a>' + esc(t('submitSuffix')) + '</p>' +
          '</div>';
        return;
      }}

      // Generated-at timestamp
      if (summary.generated_at) {{
        document.getElementById('generated-at').textContent =
          t('generatedAt') + new Date(summary.generated_at).toLocaleString(currentLang);
      }}

      var html = '';

      // --- Summary cards ---
      var cards = [
        {{ value: summary.total_files,         label: t('totalPdfs') }},
        {{ value: summary.analysed,            label: t('analysed') }},
        {{ value: summary.accessible,          label: t('accessible') }},
        {{ value: summary.issues_found,        label: t('issuesFound') }},
        {{ value: summary.pending,             label: t('pending') }},
        {{ value: summary.errored,             label: t('errors') }},
      ];
      html += '<h2>' + esc(t('summary')) + '</h2><div class="stats-grid">';
      cards.forEach(function (c) {{
        html += '<div class="stat-card"><div class="value">' + (c.value || 0) +
                '</div><div class="label">' + c.label + '</div></div>';
      }});
      html += '</div>';

      // --- Sites table ---
      var sites = summary.sites || {{}};
      var siteNames = Object.keys(sites).sort();
      if (siteNames.length) {{
        html += '<h2>' + esc(t('sitesScanned')) + '</h2>';
        html += '<table><thead><tr><th>' + esc(t('site')) + '</th><th>' + esc(t('pdfs')) + '</th></tr></thead><tbody>';
        siteNames.forEach(function (s) {{
          html += '<tr><td>' + esc(s) + '</td><td>' + sites[s] + '</td></tr>';
        }});
        html += '</tbody></table>';
      }}

      // --- File details table ---
      var analysed = files.filter(function (f) {{ return f.status === 'analysed'; }});

      // Determine which optional columns have data
      var hasWords       = analysed.some(function (f) {{ return f.report && f.report.Words       != null; }});
      var hasImages      = analysed.some(function (f) {{ return f.report && f.report.Images      != null; }});
      var hasFileSize    = analysed.some(function (f) {{ return f.file_size_bytes != null; }});
      var hasTaggedContent = analysed.some(function (f) {{ return f.report && f.report.TaggedContentTest != null; }});
      var hasForms         = analysed.some(function (f) {{ return f.report && f.report.FormsTest != null; }});
      var hasTaggedForms   = analysed.some(function (f) {{ return f.report && f.report.TaggedFormFieldsTest != null; }});
      var hasTaggedAnnots  = analysed.some(function (f) {{ return f.report && f.report.TaggedAnnotationsTest != null; }});
      var hasFiguresAlt    = analysed.some(function (f) {{ return f.report && f.report.FiguresAltTextTest != null; }});
      var hasHeadings      = analysed.some(function (f) {{ return f.report && f.report.HeadingsTest != null; }});
      var hasLists         = analysed.some(function (f) {{ return f.report && f.report.ListsTest != null; }});
      var hasTables        = analysed.some(function (f) {{ return f.report && f.report.TablesTest != null; }});
      var hasVeraPDF       = analysed.some(function (f) {{ return f.report && f.report.veraPDF && typeof f.report.veraPDF === 'object'; }});
      var hasVPFailed      = analysed.some(function (f) {{ return f.report && f.report.veraPDF && f.report.veraPDF.failed_checks != null; }});
      var hasVPPassed      = analysed.some(function (f) {{ return f.report && f.report.veraPDF && f.report.veraPDF.passed_checks != null; }});
      var hasVPRules       = analysed.some(function (f) {{ return f.report && f.report.veraPDF && Array.isArray(f.report.veraPDF.failed_rules); }});
      var hasVPError       = analysed.some(function (f) {{ return f.report && f.report.veraPDF && f.report.veraPDF.error; }});
      var hasDocTitle    = analysed.some(function (f) {{ return f.report && f.report.Title       != null; }});
      var hasAuthor      = analysed.some(function (f) {{ return f.report && f.report.Author      != null; }});
      var hasSubject     = analysed.some(function (f) {{ return f.report && f.report.Subject     != null; }});
      var hasKeywords    = analysed.some(function (f) {{ return f.report && f.report.Keywords    != null; }});
      var hasDescription = analysed.some(function (f) {{ return f.report && f.report.Description != null; }});

      // Sort state
      var sortCol = null;
      var sortAsc = true;

      function formatSize(sizeBytes) {{
        if (sizeBytes == null) return '&#x2014;';
        var num = Number(sizeBytes);
        if (!isFinite(num) || num < 0) return '&#x2014;';
        var units = ['B', 'KB', 'MB', 'GB', 'TB'];
        var value = num;
        var unit = 0;
        while (value >= 1024 && unit < units.length - 1) {{
          value /= 1024;
          unit += 1;
        }}
        if (unit === 0) return String(Math.round(value)) + ' ' + units[unit];
        return value.toFixed(1) + ' ' + units[unit];
      }}

      function readSortFromQuery() {{
        var params = new URLSearchParams(window.location.search);
        var col = params.get('sort');
        var dir = params.get('dir');
        if (col) sortCol = col;
        if (dir === 'asc' || dir === 'desc') sortAsc = dir === 'asc';
      }}

      function writeSortToQuery() {{
        var params = new URLSearchParams(window.location.search);
        if (sortCol) {{
          params.set('sort', sortCol);
          params.set('dir', sortAsc ? 'asc' : 'desc');
        }} else {{
          params.delete('sort');
          params.delete('dir');
        }}
        var qs = params.toString();
        var nextUrl = window.location.pathname + (qs ? '?' + qs : '') + window.location.hash;
        window.history.replaceState(null, '', nextUrl);
      }}

      function colVal(f, col) {{
        var r = f.report || {{}};
        switch (col) {{
          case 'file':        return (f.filename || f.url || '').toLowerCase();
          case 'site':        return (f.site || '').toLowerCase();
          case 'date':        return r.Date ? String(r.Date) : '';
          case 'doc_title':   return (r.Title       || '').toLowerCase();
          case 'author':      return (r.Author      || '').toLowerCase();
          case 'subject':     return (r.Subject     || '').toLowerCase();
          case 'keywords':    return (r.Keywords    || '').toLowerCase();
          case 'description': return (r.Description || '').toLowerCase();
          case 'accessible':  return r.Accessible === true ? 1 : r.Accessible === false ? 0 : -1;
          case 'tagged':      return r.TaggedTest    === 'Pass' ? 1 : r.TaggedTest    === 'Fail' ? 0 : -1;
          case 'title':       return r.TitleTest     === 'Pass' ? 1 : r.TitleTest     === 'Fail' ? 0 : -1;
          case 'language':    return r.LanguageTest  === 'Pass' ? 1 : r.LanguageTest  === 'Fail' ? 0 : -1;
          case 'bookmarks':   return r.BookmarksTest === 'Pass' ? 1 : r.BookmarksTest === 'Fail' ? 0 : -1;
          case 'tagged_content': return r.TaggedContentTest === 'Pass' ? 1 : r.TaggedContentTest === 'Fail' ? 0 : -1;
          case 'forms':          return r.FormsTest === 'Pass' ? 1 : r.FormsTest === 'Fail' ? 0 : -1;
          case 'tagged_forms':   return r.TaggedFormFieldsTest === 'Pass' ? 1 : r.TaggedFormFieldsTest === 'Fail' ? 0 : -1;
          case 'tagged_annots':  return r.TaggedAnnotationsTest === 'Pass' ? 1 : r.TaggedAnnotationsTest === 'Fail' ? 0 : -1;
          case 'figures_alt':    return r.FiguresAltTextTest === 'Pass' ? 1 : r.FiguresAltTextTest === 'Fail' ? 0 : -1;
          case 'headings':       return r.HeadingsTest === 'Pass' ? 1 : r.HeadingsTest === 'Fail' ? 0 : -1;
          case 'lists':          return r.ListsTest === 'Pass' ? 1 : r.ListsTest === 'Fail' ? 0 : -1;
          case 'tables':         return r.TablesTest === 'Pass' ? 1 : r.TablesTest === 'Fail' ? 0 : -1;
          case 'verapdf_status':
            if (!r.veraPDF || typeof r.veraPDF !== 'object') return -1;
            if (r.veraPDF.error) return 0;
            if (r.veraPDF.compliant === false) return 1;
            if (r.veraPDF.compliant === true) return 2;
            return -1;
          case 'verapdf_failed': return (r.veraPDF && r.veraPDF.failed_checks != null) ? r.veraPDF.failed_checks : -1;
          case 'verapdf_passed': return (r.veraPDF && r.veraPDF.passed_checks != null) ? r.veraPDF.passed_checks : -1;
          case 'verapdf_rules': return (r.veraPDF && Array.isArray(r.veraPDF.failed_rules)) ? r.veraPDF.failed_rules.length : -1;
          case 'verapdf_error': return (r.veraPDF && r.veraPDF.error) ? String(r.veraPDF.error).toLowerCase() : '';
          case 'pages':       return r.Pages  != null ? r.Pages  : -1;
          case 'size':        return f.file_size_bytes != null ? Number(f.file_size_bytes) : -1;
          case 'words':       return r.Words  != null ? r.Words  : -1;
          case 'images':      return r.Images != null ? r.Images : -1;
          default: return '';
        }}
      }}

      function buildRow(f) {{
        var r = f.report || {{}};
        var dateStr = '';
        if (r.Date) {{
          var dm = String(r.Date).match(/^(\\d{{4}}-\\d{{2}}-\\d{{2}})/);
          dateStr = dm ? dm[1] : String(r.Date);
        }}
        var extDomain = '';
        try {{
          var pdfHost = new URL(f.url).hostname.replace(/^www\\./, '');
          var seed = (f.site || '').replace(/^www\\./, '');
          if (pdfHost && seed && pdfHost !== seed && !pdfHost.endsWith('.' + seed)) {{
            extDomain = pdfHost;
          }}
        }} catch (e) {{}}
        var siteCell = esc(f.site || '');
        if (extDomain) {{
          siteCell += ' <em>(ext: ' + esc(extDomain) + ')</em>';
        }}
        var vp = r.veraPDF;
        var vpStatus = '&#x2014;';
        if (vp && typeof vp === 'object') {{
          if (vp.error) vpStatus = '<span class="fail">Error</span>';
          else if (vp.compliant === true) vpStatus = '<span class="pass">Pass</span>';
          else if (vp.compliant === false) vpStatus = '<span class="fail">Fail</span>';
        }}
        var vpRules = (vp && Array.isArray(vp.failed_rules)) ? vp.failed_rules.length : null;
        var vpError = (vp && vp.error) ? String(vp.error) : null;
        var docTitleCell = '&#x2014;';
        if (r.Title) docTitleCell = esc(r.Title);
        else if (r.TitleTest === 'Fail') docTitleCell = icon('Fail');
        return '<tr>' +
          '<td><a href="' + esc(f.url) + '" target="_blank" rel="noopener">' + esc(f.filename || f.url) + '</a></td>' +
          '<td>' + siteCell + '</td>' +
          '<td>' + (dateStr ? esc(dateStr) : '&#x2014;') + '</td>' +
          (hasDocTitle    ? '<td>' + docTitleCell + '</td>' : '') +
          (hasAuthor      ? '<td>' + (r.Author      ? esc(r.Author)      : '&#x2014;') + '</td>' : '') +
          (hasSubject     ? '<td>' + (r.Subject     ? esc(r.Subject)     : '&#x2014;') + '</td>' : '') +
          (hasKeywords    ? '<td>' + (r.Keywords    ? esc(r.Keywords)    : '&#x2014;') + '</td>' : '') +
          (hasDescription ? '<td>' + (r.Description ? esc(r.Description) : '&#x2014;') + '</td>' : '') +
          '<td>' + icon(r.Accessible)    + '</td>' +
          '<td>' + icon(r.TaggedTest)    + '</td>' +
          '<td>' + icon(r.TitleTest)     + '</td>' +
          '<td>' + icon(r.LanguageTest)  + '</td>' +
          '<td>' + icon(r.BookmarksTest) + '</td>' +
          (hasTaggedContent ? '<td>' + icon(r.TaggedContentTest) + '</td>' : '') +
          (hasForms         ? '<td>' + icon(r.FormsTest) + '</td>' : '') +
          (hasTaggedForms   ? '<td>' + icon(r.TaggedFormFieldsTest) + '</td>' : '') +
          (hasTaggedAnnots  ? '<td>' + icon(r.TaggedAnnotationsTest) + '</td>' : '') +
          (hasFiguresAlt    ? '<td>' + icon(r.FiguresAltTextTest) + '</td>' : '') +
          (hasHeadings      ? '<td>' + icon(r.HeadingsTest) + '</td>' : '') +
          (hasLists         ? '<td>' + icon(r.ListsTest) + '</td>' : '') +
          (hasTables        ? '<td>' + icon(r.TablesTest) + '</td>' : '') +
          (hasVeraPDF       ? '<td>' + vpStatus + '</td>' : '') +
          (hasVPFailed      ? '<td>' + ((vp && vp.failed_checks != null) ? vp.failed_checks : '&#x2014;') + '</td>' : '') +
          (hasVPPassed      ? '<td>' + ((vp && vp.passed_checks != null) ? vp.passed_checks : '&#x2014;') + '</td>' : '') +
          (hasVPRules       ? '<td>' + (vpRules != null ? vpRules : '&#x2014;') + '</td>' : '') +
          (hasVPError       ? '<td>' + (vpError ? esc(vpError) : '&#x2014;') + '</td>' : '') +
          '<td>' + (r.Pages  != null ? r.Pages  : '&#x2014;') + '</td>' +
          (hasFileSize ? '<td>' + formatSize(f.file_size_bytes) + '</td>' : '') +
          (hasWords  ? '<td>' + (r.Words  != null ? r.Words  : '&#x2014;') + '</td>' : '') +
          (hasImages ? '<td>' + (r.Images != null ? r.Images : '&#x2014;') + '</td>' : '') +
          '</tr>';
      }}

      function renderBody(tbl) {{
        var rows = analysed.slice();
        if (sortCol !== null) {{
          rows.sort(function (a, b) {{
            var va = colVal(a, sortCol);
            var vb = colVal(b, sortCol);
            if (va < vb) return sortAsc ? -1 : 1;
            if (va > vb) return sortAsc ?  1 : -1;
            return 0;
          }});
        }}
        tbl.tBodies[0].innerHTML = rows.map(buildRow).join('');
        writeSortToQuery();
      }}

      function updateHeaders(tbl) {{
        tbl.querySelectorAll('th[data-col]').forEach(function (th) {{
          var col = th.getAttribute('data-col');
          var lbl = th.getAttribute('data-label');
          if (col === sortCol) {{
            th.setAttribute('aria-sort', sortAsc ? 'ascending' : 'descending');
            th.querySelector('.sort-label').textContent = lbl + (sortAsc ? ' \u25b4' : ' \u25be');
          }} else {{
            th.setAttribute('aria-sort', 'none');
            th.querySelector('.sort-label').textContent = lbl;
          }}
        }});
      }}

      if (analysed.length) {{
        // Column order: identity columns, then optional metadata (shown only when
        // at least one file has that field), then accessibility checks, then
        // optional numeric columns (words/images shown only when data is present).
        var colDefs = [
          {{ key: 'file',       label: t('colFile') }},
          {{ key: 'site',       label: t('colSite') }},
          {{ key: 'date',       label: t('colPublishedDate') }},
        ];
        if (hasDocTitle)    colDefs.push({{ key: 'doc_title',   label: t('colDocTitle') }});
        if (hasAuthor)      colDefs.push({{ key: 'author',      label: t('colAuthor') }});
        if (hasSubject)     colDefs.push({{ key: 'subject',     label: t('colSubject') }});
        if (hasKeywords)    colDefs.push({{ key: 'keywords',    label: t('colKeywords') }});
        if (hasDescription) colDefs.push({{ key: 'description', label: t('colDescription') }});
        colDefs.push(
          {{ key: 'accessible', label: t('colAccessible') }},
          {{ key: 'tagged',     label: t('colTagged') }},
          {{ key: 'title',      label: t('colTitle') }},
          {{ key: 'language',   label: t('colLanguage') }},
          {{ key: 'bookmarks',  label: t('colBookmarks') }},
        );
        if (hasTaggedContent) colDefs.push({{ key: 'tagged_content', label: t('colTaggedContent') }});
        if (hasForms)         colDefs.push({{ key: 'forms',          label: t('colForms') }});
        if (hasTaggedForms)   colDefs.push({{ key: 'tagged_forms',   label: t('colTaggedForms') }});
        if (hasTaggedAnnots)  colDefs.push({{ key: 'tagged_annots',  label: t('colTaggedAnnots') }});
        if (hasFiguresAlt)    colDefs.push({{ key: 'figures_alt',    label: t('colFiguresAlt') }});
        if (hasHeadings)      colDefs.push({{ key: 'headings',       label: t('colHeadings') }});
        if (hasLists)         colDefs.push({{ key: 'lists',          label: t('colLists') }});
        if (hasTables)        colDefs.push({{ key: 'tables',         label: t('colTables') }});
        if (hasVeraPDF)       colDefs.push({{ key: 'verapdf_status', label: 'veraPDF' }});
        if (hasVPFailed)      colDefs.push({{ key: 'verapdf_failed', label: 'vPDF Fail' }});
        if (hasVPPassed)      colDefs.push({{ key: 'verapdf_passed', label: 'vPDF Pass' }});
        if (hasVPRules)       colDefs.push({{ key: 'verapdf_rules',  label: 'vPDF Rules' }});
        if (hasVPError)       colDefs.push({{ key: 'verapdf_error',  label: 'vPDF Error' }});
        colDefs.push(
          {{ key: 'pages',      label: t('colPages') }}
        );
        if (hasFileSize) colDefs.push({{ key: 'size', label: t('colSize') }});
        if (hasWords)  colDefs.push({{ key: 'words',  label: t('colWords') }});
        if (hasImages) colDefs.push({{ key: 'images', label: t('colImages') }});

        html += '<h2>' + esc(t('pdfDetails')) + '</h2>';
        html += '<p>' + esc(t('legend')) + '</p>';
        html += '<table id="pdf-table"><thead><tr>';
        colDefs.forEach(function (c) {{
          html += '<th class="sortable" data-col="' + c.key + '" data-label="' + c.label +
                  '" aria-sort="none" tabindex="0" role="columnheader">' +
                  '<span class="sort-label">' + c.label + '</span></th>';
        }});
        html += '</tr></thead><tbody>';
        analysed.forEach(function (f) {{
          html += buildRow(f);
        }});
        html += '</tbody></table>';
      }}

      root.innerHTML = html;

      // Wire up column sorting on the PDF details table
      var pdfTable = document.getElementById('pdf-table');
      if (pdfTable) {{
        readSortFromQuery();
        var validCols = Array.prototype.map.call(
          pdfTable.querySelectorAll('th[data-col]'),
          function (th) {{ return th.getAttribute('data-col'); }}
        );
        if (sortCol && validCols.indexOf(sortCol) === -1) sortCol = null;
        renderBody(pdfTable);
        updateHeaders(pdfTable);
        pdfTable.querySelectorAll('th[data-col]').forEach(function (th) {{
          th.addEventListener('click', function () {{
            var col = this.getAttribute('data-col');
            if (sortCol === col) {{
              sortAsc = !sortAsc;
            }} else {{
              sortCol = col;
              sortAsc = true;
            }}
            renderBody(pdfTable);
            updateHeaders(pdfTable);
          }});
          th.addEventListener('keydown', function (e) {{
            if (e.key === 'Enter' || e.key === ' ') {{
              e.preventDefault();
              this.click();
            }}
          }});
        }});
      }}

      function makeCsvValue(value) {{
        var s = value == null ? '' : String(value);
        if (s.indexOf('"') !== -1 || s.indexOf(',') !== -1 || s.indexOf('\\n') !== -1) {{
          return '"' + s.replace(/"/g, '""') + '"';
        }}
        return s;
      }}

      function rowFromFile(file) {{
        var r = file.report || {{}};
        return {{
          url: file.url || '',
          filename: file.filename || '',
          site: file.site || '',
          status: file.status || '',
          file_size_bytes: file.file_size_bytes == null ? '' : file.file_size_bytes,
          file_size_human: formatSize(file.file_size_bytes).replace(/&[^;]+;/g, '-'),
          published_date: r.Date || '',
          doc_title: r.Title || '',
          author: r.Author || '',
          subject: r.Subject || '',
          keywords: r.Keywords || '',
          accessible: r.Accessible == null ? '' : r.Accessible,
          totally_inaccessible: r.TotallyInaccessible == null ? '' : r.TotallyInaccessible,
          broken: r.BrokenFile == null ? '' : r.BrokenFile,
          title: r.TitleTest || '',
          language: r.LanguageTest || '',
          bookmarks: r.BookmarksTest || '',
          pages: r.Pages == null ? '' : r.Pages,
          words: r.Words == null ? '' : r.Words,
          images: r.Images == null ? '' : r.Images,
        }};
      }}

      function buildCsvText() {{
        var columns = [
          'url','filename','site','status','file_size_bytes','file_size_human',
          'published_date','doc_title','author','subject','keywords','accessible',
          'totally_inaccessible','broken','title','language','bookmarks','pages',
          'words','images'
        ];
        var lines = [columns.join(',')];
        files.forEach(function (file) {{
          var row = rowFromFile(file);
          lines.push(columns.map(function (col) {{ return makeCsvValue(row[col]); }}).join(','));
        }});
        return lines.join('\\n');
      }}

      function downloadBlob(filename, text, mime) {{
        var blob = new Blob([text], {{ type: mime }});
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () {{ URL.revokeObjectURL(a.href); }}, 0);
      }}

      var downloadCsvBtn = document.getElementById('download-current-csv');
      if (downloadCsvBtn) {{
        downloadCsvBtn.addEventListener('click', function () {{
          downloadBlob('report-current-view.csv', buildCsvText(), 'text/csv;charset=utf-8');
        }});
      }}

      var downloadJsonBtn = document.getElementById('download-current-json');
      if (downloadJsonBtn) {{
        downloadJsonBtn.addEventListener('click', function () {{
          downloadBlob('report-current-view.json', JSON.stringify({{ summary: summary, files: files }}, null, 2), 'application/json;charset=utf-8');
        }});
      }}

      function icon(v) {{
        if (v === true  || v === 'Pass') return '<span class="pass">&#x2705;</span>';
        if (v === false || v === 'Fail') return '<span class="fail">&#x274C;</span>';
        return '<span class="na">&#x2014;</span>';
      }}

      function esc(s) {{
        if (!s) return '';
        return String(s)
          .replace(/&/g,  '&amp;')
          .replace(/</g,  '&lt;')
          .replace(/>/g,  '&gt;')
          .replace(/"/g,  '&quot;')
          .replace(/'/g,  '&#x27;');
      }}
    }})();
  </script>

  <footer>
    <p>
      Powered by
      <a href="https://github.com/accessibility-luxembourg/simplA11yPDFCrawler"
         target="_blank" rel="noopener">simplA11yPDFCrawler</a>
      and
      <a href="https://github.com/mgifford/pdf-crawler"
         target="_blank" rel="noopener">mgifford/pdf-crawler</a>.
      MIT licence.
    </p>
  </footer>

  <script>
    (function () {{
      var btn  = document.getElementById('theme-toggle');
      var root = document.documentElement;

      function applyTheme(theme) {{
        root.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
        if (theme === 'dark') {{
          btn.textContent = '\u2600\uFE0F';
          btn.setAttribute('aria-label', 'Switch to light mode');
          btn.setAttribute('title', 'Switch to light mode');
        }} else {{
          btn.textContent = '\U0001F319';
          btn.setAttribute('aria-label', 'Switch to dark mode');
          btn.setAttribute('title', 'Switch to dark mode');
        }}
      }}

      var stored      = localStorage.getItem('theme');
      var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      applyTheme(stored === 'dark' || stored === 'light' ? stored : (prefersDark ? 'dark' : 'light'));

      btn.addEventListener('click', function () {{
        applyTheme(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
      }});
    }})();
  </script>

</body>
</html>
"""


def generate_html(
    entries: List[Dict[str, Any]],
    stats: Dict[str, Any],
    back_url: str = "./",
    back_label: str = "Back to submission form",
    report_assets_base: str = "reports",
) -> str:
    """Return a standalone HTML page with scan results embedded as JSON."""
    json_data = json.dumps({"summary": stats, "files": entries}, indent=2, default=str)
    return _HTML_TEMPLATE.format(
        json_data=json_data,
        back_url=back_url,
        back_label=back_label,
        report_assets_base=report_assets_base,
    )


# ---------------------------------------------------------------------------
# Reports index HTML (historical scans)
# ---------------------------------------------------------------------------

_REPORTS_INDEX_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PDF Accessibility Scan Reports</title>
  <meta name="description" content="Historical record of all PDF accessibility scans run by the PDF Accessibility Crawler." />
  <!-- Open Graph (LinkedIn, Mastodon, Bluesky) -->
  <meta property="og:title" content="PDF Accessibility Scan Reports" />
  <meta property="og:description" content="Historical record of all PDF accessibility scans run by the PDF Accessibility Crawler." />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="https://mgifford.github.io/pdf-crawler/reports.html" />
  <meta property="og:site_name" content="PDF Accessibility Crawler" />
  <!-- Twitter Card (also used by many other platforms) -->
  <meta name="twitter:card" content="summary" />
  <meta name="twitter:title" content="PDF Accessibility Scan Reports" />
  <meta name="twitter:description" content="Historical record of all PDF accessibility scans run by the PDF Accessibility Crawler." />
  <!-- Prevent flash of unstyled content when a saved theme preference is present -->
  <script>
    (function () {{
      var t = localStorage.getItem('theme');
      if (t === 'dark' || t === 'light') document.documentElement.setAttribute('data-theme', t);
    }})();
  </script>
  <style>
    /* ---- colour tokens ---- */
    :root {{
      color-scheme: light dark;
      --color-bg:           #f8f9fa;
      --color-fg:           #1a1a2e;
      --color-link:         #0d6efd;
      --color-card-bg:      #fff;
      --color-border:       #dee2e6;
      --color-th-bg:        #e9ecef;
      --color-row-stripe:   #f8f9fa;
      --color-muted:        #6c757d;
      --color-input-border: #ced4da;
      --color-input-bg:     #fff;
      --color-bar-track:    #dee2e6;
      --color-bar-high:     #198754;
      --color-bar-medium:   #fd7e14;
      --color-bar-low:      #dc3545;
      --color-error-bg:     #fff5f5;
      --color-error-border: #f5c2c7;
      --color-error-fg:     #842029;
    }}

    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]) {{
        --color-bg:           #0d1117;
        --color-fg:           #e6edf3;
        --color-link:         #4493f8;
        --color-card-bg:      #161b22;
        --color-border:       #30363d;
        --color-th-bg:        #21262d;
        --color-row-stripe:   #161b22;
        --color-muted:        #8b949e;
        --color-input-border: #30363d;
        --color-input-bg:     #0d1117;
        --color-bar-track:    #30363d;
        --color-bar-high:     #3fb950;
        --color-bar-medium:   #e3b341;
        --color-bar-low:      #f85149;
        --color-error-bg:     #2c0b0e;
        --color-error-border: #842029;
        --color-error-fg:     #f1aeb5;
      }}
    }}

    [data-theme="dark"] {{
      --color-bg:           #0d1117;
      --color-fg:           #e6edf3;
      --color-link:         #4493f8;
      --color-card-bg:      #161b22;
      --color-border:       #30363d;
      --color-th-bg:        #21262d;
      --color-row-stripe:   #161b22;
      --color-muted:        #8b949e;
      --color-input-border: #30363d;
      --color-input-bg:     #0d1117;
      --color-bar-track:    #30363d;
      --color-bar-high:     #3fb950;
      --color-bar-medium:   #e3b341;
      --color-bar-low:      #f85149;
      --color-error-bg:     #2c0b0e;
      --color-error-border: #842029;
      --color-error-fg:     #f1aeb5;
    }}

    *, *::before, *::after {{ box-sizing: border-box; }}

    body {{
      font-family: system-ui, -apple-system, sans-serif;
      max-width: 1100px;
      margin: 0 auto;
      padding: 2rem 1rem;
      color: var(--color-fg);
      background: var(--color-bg);
    }}

    nav {{
      display: flex;
      align-items: center;
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    nav a {{ color: var(--color-link); text-decoration: none; }}
    nav a:hover {{ text-decoration: underline; }}

    .theme-toggle {{
      background: none;
      border: 1px solid var(--color-border);
      border-radius: 0.375rem;
      cursor: pointer;
      font-size: 1.1rem;
      padding: 0.25rem 0.5rem;
      line-height: 1;
      color: var(--color-fg);
      margin-left: auto;
    }}
    .theme-toggle:hover {{ background: var(--color-th-bg); }}

    h1 {{ color: var(--color-link); margin-bottom: 0.5rem; }}

    .summary-bar {{
      display: flex;
      gap: 1rem;
      flex-wrap: wrap;
      margin: 1rem 0 1.5rem;
    }}
    .summary-card {{
      background: var(--color-card-bg);
      border: 1px solid var(--color-border);
      border-radius: 0.375rem;
      padding: 0.75rem 1.25rem;
      min-width: 120px;
      text-align: center;
    }}
    .summary-card .value {{ font-size: 1.6rem; font-weight: 700; color: var(--color-link); }}
    .summary-card .label {{ font-size: 0.8rem; color: var(--color-muted); margin-top: 0.2rem; }}

    .filter-bar {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 1rem;
      flex-wrap: wrap;
    }}
    .filter-bar label {{ font-weight: 600; white-space: nowrap; }}
    .filter-bar input[type="search"] {{
      padding: 0.4rem 0.75rem;
      border: 1px solid var(--color-input-border);
      border-radius: 0.375rem;
      font-size: 0.95rem;
      width: 260px;
      max-width: 100%;
      background: var(--color-input-bg);
      color: var(--color-fg);
    }}
    .filter-count {{ font-size: 0.85rem; color: var(--color-muted); }}

    table {{ width: 100%; border-collapse: collapse; margin: 0.5rem 0; font-size: 0.9rem; }}
    th {{
      background: var(--color-th-bg);
      padding: 0.5rem 0.75rem;
      text-align: left;
      border-bottom: 2px solid var(--color-border);
      white-space: nowrap;
    }}
    th.sortable {{ cursor: pointer; }}
    th.sortable:focus-visible {{ outline: 2px solid var(--color-link); outline-offset: 2px; }}
    td {{ padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--color-border); vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:nth-child(even) td {{ background: var(--color-row-stripe); }}

    a {{ color: var(--color-link); }}

    .pct-bar {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      min-width: 130px;
    }}
    .pct-bar-track {{
      flex: 1;
      height: 8px;
      background: var(--color-bar-track);
      border-radius: 4px;
      overflow: hidden;
    }}
    .pct-bar-fill {{
      height: 100%;
      border-radius: 4px;
    }}
    .pct-bar-fill.high   {{ background: var(--color-bar-high); }}
    .pct-bar-fill.medium {{ background: var(--color-bar-medium); }}
    .pct-bar-fill.low    {{ background: var(--color-bar-low); }}
    .pct-label {{ font-size: 0.8rem; white-space: nowrap; }}

    .empty-state, .error-state, .loading-state {{
      background: var(--color-card-bg);
      border: 1px solid var(--color-border);
      border-radius: 0.375rem;
      padding: 2rem;
      text-align: center;
      color: var(--color-muted);
    }}
    .error-state {{ border-color: var(--color-error-border); color: var(--color-error-fg); background: var(--color-error-bg); }}

    footer {{
      margin-top: 3rem;
      font-size: 0.8rem;
      color: var(--color-muted);
      border-top: 1px solid var(--color-border);
      padding-top: 1rem;
    }}
  </style>
</head>
<body>

  <nav>
    <a id="reports-back-link" href="./">&#8592; Back to submission form</a>
    <span class="lang-switch" aria-label="Language switch">
      <span id="lang-switch-label-index">Language: English</span>
      <a id="lang-en-index" data-lang="en" href="?lang=en" hreflang="en" lang="en">English</a>
      <span aria-hidden="true">|</span>
      <a id="lang-fr-index" data-lang="fr" href="?lang=fr" hreflang="fr" lang="fr">Français</a>
    </span>
    <button class="theme-toggle" id="theme-toggle" aria-label="Switch to dark mode" title="Switch to dark mode">&#127769;</button>
  </nav>

  <h1 id="reports-page-title">&#128202; PDF Accessibility Scan Reports</h1>
  <p id="reports-page-subtitle">Historical record of all PDF accessibility scans run by this tool.</p>

  <div id="summary-bar" class="summary-bar" aria-live="polite"></div>

  <div class="filter-bar">
    <label id="filter-label" for="filter-input">Filter by site:</label>
    <input type="search" id="filter-input" placeholder="e.g. energy.gov" aria-label="Filter reports by site name" />
    <span id="filter-count" class="filter-count" aria-live="polite"></span>
  </div>

  <div id="root" aria-live="polite">
    <div class="loading-state" id="loading-text">Loading reports&hellip;</div>
  </div>

  <script>
    (function () {{
      // i18n scaffold: add locales here with the same keys.
      var I18N = {{
        en: {{
          langSwitchLabel: 'Language:',
          langEnglish: 'English',
          langFrench: 'Français',
          backLabel: 'Back to submission form',
          title: '📊 PDF Accessibility Scan Reports',
          subtitle: 'Historical record of all PDF accessibility scans run by this tool.',
          filterLabel: 'Filter by site:',
          filterAria: 'Filter reports by site name',
          filterPlaceholder: 'e.g. energy.gov',
          loading: 'Loading reports…',
          totalScans: 'Total Scans',
          uniqueSites: 'Unique Sites',
          noReports: 'No scan reports yet.',
          noReportsCta: 'Submit a crawl request',
          noReportsCtaSuffix: ' to get started.',
          colDate: 'Date',
          colSite: 'Site',
          colIssue: 'Issue',
          colRun: 'Run',
          colEngines: 'Engines',
          colVeraPdf: 'veraPDF',
          colVpdfFails: 'vPDF Fails',
          colVpdfErrors: 'vPDF Errors',
          colTotalPdfs: 'Total PDFs',
          colAccessible: '✅ Accessible',
          colIssues: '❌ Issues',
          colPctAccessible: '% Accessible',
          colReport: 'Report',
          viewReport: 'View report',
          runLabel: 'Run',
          showing: 'Showing',
          of: 'of',
          scans: 'scans',
          loadErrorTitle: 'Could not load reports.',
          loadErrorHint: 'If you are viewing this file locally, please serve it from a web server.',
        }},
        fr: {{
          langSwitchLabel: 'Langue :',
          langEnglish: 'English',
          langFrench: 'Français',
          backLabel: 'Retour au formulaire de soumission',
          title: '📊 Rapports de scan d\\'accessibilité PDF',
          subtitle: 'Historique de tous les scans d\\'accessibilité PDF exécutés par cet outil.',
          filterLabel: 'Filtrer par site :',
          filterAria: 'Filtrer les rapports par nom de site',
          filterPlaceholder: 'ex. energy.gov',
          loading: 'Chargement des rapports…',
          totalScans: 'Scans totaux',
          uniqueSites: 'Sites uniques',
          noReports: 'Aucun rapport de scan pour le moment.',
          noReportsCta: 'Soumettre une demande de scan',
          noReportsCtaSuffix: ' pour commencer.',
          colDate: 'Date',
          colSite: 'Site',
          colIssue: 'Issue',
          colRun: 'Exécution',
          colEngines: 'Moteurs',
          colVeraPdf: 'veraPDF',
          colVpdfFails: 'vPDF Échecs',
          colVpdfErrors: 'vPDF Erreurs',
          colTotalPdfs: 'PDF totaux',
          colAccessible: '✅ Accessibles',
          colIssues: '❌ Problèmes',
          colPctAccessible: '% Accessible',
          colReport: 'Rapport',
          viewReport: 'Voir le rapport',
          runLabel: 'Exécution',
          showing: 'Affichage',
          of: 'sur',
          scans: 'scans',
          loadErrorTitle: 'Impossible de charger les rapports.',
          loadErrorHint: 'Si vous ouvrez ce fichier en local, servez-le via un serveur web.',
        }},
      }};

      function detectLang() {{
        var params = new URLSearchParams(window.location.search);
        var fromQuery = params.get('lang');
        if (fromQuery && I18N[fromQuery]) return fromQuery;
        var stored = localStorage.getItem('lang');
        if (stored && I18N[stored]) return stored;
        var navLang = (navigator.language || 'en').toLowerCase();
        return navLang.indexOf('fr') === 0 ? 'fr' : 'en';
      }}

      var currentLang = detectLang();
      var M = I18N[currentLang] || I18N.en;
      document.documentElement.setAttribute('lang', currentLang);
      localStorage.setItem('lang', currentLang);

      function t(key) {{
        return M[key] || I18N.en[key] || key;
      }}

      function withLang(url, langCode) {{
        try {{
          var parsed = new URL(url, window.location.href);
          parsed.searchParams.set('lang', langCode || currentLang);
          return parsed.toString();
        }} catch (e) {{
          return url;
        }}
      }}

      var root        = document.getElementById('root');
      var summaryBar  = document.getElementById('summary-bar');
      var filterInput = document.getElementById('filter-input');
      var filterCount = document.getElementById('filter-count');
      var allReports  = [];

      (function applyLanguageStatic() {{
        var back = document.getElementById('reports-back-link');
        if (back) back.textContent = '← ' + t('backLabel');
        var title = document.getElementById('reports-page-title');
        if (title) title.textContent = t('title');
        var subtitle = document.getElementById('reports-page-subtitle');
        if (subtitle) subtitle.textContent = t('subtitle');
        var filterLabel = document.getElementById('filter-label');
        if (filterLabel) filterLabel.textContent = t('filterLabel');
        if (filterInput) {{
          filterInput.setAttribute('placeholder', t('filterPlaceholder'));
          filterInput.setAttribute('aria-label', t('filterAria'));
        }}
        var loading = document.getElementById('loading-text');
        if (loading) loading.textContent = t('loading');
        var langLabelNode = document.getElementById('lang-switch-label-index');
        var currentLangName = currentLang === 'fr' ? t('langFrench') : t('langEnglish');
        if (langLabelNode) langLabelNode.textContent = t('langSwitchLabel') + ' ' + currentLangName;

        var langLinks = document.querySelectorAll('.lang-switch a[data-lang]');
        langLinks.forEach(function (link) {{
          var code = (link.getAttribute('data-lang') || '').toLowerCase();
          if (!code) return;
          link.href = withLang(window.location.href, code);
          if (code === 'en') link.textContent = t('langEnglish');
          else if (code === 'fr') link.textContent = t('langFrench');
          else link.textContent = code.toUpperCase();
          if (code === currentLang) link.setAttribute('aria-current', 'page');
          else link.removeAttribute('aria-current');
        }});
      }})();

      function esc(s) {{
        if (!s) return '';
        return String(s)
          .replace(/&/g,  '&amp;')
          .replace(/</g,  '&lt;')
          .replace(/>/g,  '&gt;')
          .replace(/"/g,  '&quot;')
          .replace(/'/g,  '&#x27;');
      }}

      function pctBar(accessible, analysed) {{
        if (!analysed) return '<span class="pct-label">&#x2014;</span>';
        var pct = Math.round((accessible / analysed) * 100);
        var cls = pct >= 75 ? 'high' : pct >= 40 ? 'medium' : 'low';
        return '<div class="pct-bar">' +
          '<div class="pct-bar-track"><div class="pct-bar-fill ' + cls + '" style="width:' + pct + '%"></div></div>' +
          '<span class="pct-label">' + pct + '%</span>' +
          '</div>';
      }}

      function renderSummary(reports) {{
        var sites = {{}};
        reports.forEach(function (r) {{ if (r.site) sites[r.site] = true; }});
        summaryBar.innerHTML =
          '<div class="summary-card"><div class="value">' + reports.length + '</div><div class="label">' + esc(t('totalScans')) + '</div></div>' +
          '<div class="summary-card"><div class="value">' + Object.keys(sites).length + '</div><div class="label">' + esc(t('uniqueSites')) + '</div></div>';
      }}

      var sortCol = 'date';
      var sortAsc = false;

      function issueInfo(report) {{
        var m = report.issue_url ? report.issue_url.match(/\\/issues\\/(\\d+)/) : null;
        return {{
          issueNum: m ? m[1] : '',
          issueKey: m ? ('issue:' + m[1]) : ('manual:' + (report.site || '') + '|' + (report.crawl_url || '')),
        }};
      }}

      function enrichRunInfo(reports) {{
        var byKey = {{}};
        reports.forEach(function (r) {{
          var info = issueInfo(r);
          r._issue_num = info.issueNum;
          r._issue_key = info.issueKey;
          byKey[info.issueKey] = byKey[info.issueKey] || [];
          byKey[info.issueKey].push(r);
        }});

        Object.keys(byKey).forEach(function (key) {{
          var runs = byKey[key].slice().sort(function (a, b) {{
            var da = a.date ? Date.parse(a.date) : 0;
            var db = b.date ? Date.parse(b.date) : 0;
            return da - db;
          }});
          var total = runs.length;
          runs.forEach(function (r, idx) {{
            r._run_number = idx + 1;
            r._run_total = total;
          }});
        }});
      }}

      function colVal(r, col) {{
        var analysed = r.analysed || 0;
        var accessible = r.accessible || 0;
        var issues = Math.max(0, analysed - accessible);
        var pct = analysed ? (accessible / analysed) : -1;
        var engines = Array.isArray(r.analysis_engines)
          ? r.analysis_engines.join(',')
          : (r.analysis_engines || r.engine || 'original');

        switch (col) {{
          case 'date': return r.date ? Date.parse(r.date) : 0;
          case 'site': return (r.site || '').toLowerCase();
          case 'issue': return r._issue_num ? Number(r._issue_num) : -1;
          case 'rerun': return (r._run_total || 1) * 1000 + (r._run_number || 1);
          case 'engines': return String(engines).toLowerCase();
          case 'verapdf': return r.verapdf ? 1 : 0;
          case 'vp_fail': return r.vp_fail_files != null ? r.vp_fail_files : -1;
          case 'vp_error': return r.vp_error_files != null ? r.vp_error_files : -1;
          case 'total': return r.total || 0;
          case 'accessible': return accessible;
          case 'issues': return issues;
          case 'pct': return pct;
          default: return '';
        }}
      }}

      function sortReports(reports) {{
        return reports.slice().sort(function (a, b) {{
          var va = colVal(a, sortCol);
          var vb = colVal(b, sortCol);
          if (va < vb) return sortAsc ? -1 : 1;
          if (va > vb) return sortAsc ? 1 : -1;
          return 0;
        }});
      }}

      function updateHeaders(table) {{
        table.querySelectorAll('th[data-col]').forEach(function (th) {{
          var col = th.getAttribute('data-col');
          var label = th.getAttribute('data-label');
          if (col === sortCol) {{
            th.setAttribute('aria-sort', sortAsc ? 'ascending' : 'descending');
            th.querySelector('.sort-label').textContent = label + (sortAsc ? ' \u25b4' : ' \u25be');
          }} else {{
            th.setAttribute('aria-sort', 'none');
            th.querySelector('.sort-label').textContent = label;
          }}
        }});
      }}

      function bindSorting(table, reports) {{
        table.querySelectorAll('th[data-col]').forEach(function (th) {{
          function triggerSort() {{
            var col = th.getAttribute('data-col');
            if (sortCol === col) {{
              sortAsc = !sortAsc;
            }} else {{
              sortCol = col;
              sortAsc = col === 'date' ? false : true;
            }}
            renderTable(reports);
          }}
          th.addEventListener('click', triggerSort);
          th.addEventListener('keydown', function (e) {{
            if (e.key === 'Enter' || e.key === ' ') {{
              e.preventDefault();
              triggerSort();
            }}
          }});
        }});
      }}

      function renderTable(reports) {{
        if (!reports.length) {{
          root.innerHTML =
            '<div class="empty-state">' +
            '<p>' + esc(t('noReports')) + '</p>' +
            '<p><a href="./">' + esc(t('noReportsCta')) + '</a>' + esc(t('noReportsCtaSuffix')) + '</p>' +
            '</div>';
          return;
        }}

        var sorted = sortReports(reports);
        var hasVeraPDF = reports.some(function (r) {{ return r.verapdf || (r.vp_checked_files || 0) > 0; }});
        var hasVPFail = hasVeraPDF && reports.some(function (r) {{ return r.vp_fail_files != null; }});
        var hasVPError = hasVeraPDF && reports.some(function (r) {{ return r.vp_error_files != null; }});
        var html = '<table id="reports-table"><thead><tr>' +
          '<th class="sortable" data-col="date" data-label="' + esc(t('colDate')) + '" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colDate')) + '</span></th>' +
          '<th class="sortable" data-col="site" data-label="' + esc(t('colSite')) + '" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colSite')) + '</span></th>' +
          '<th class="sortable" data-col="issue" data-label="' + esc(t('colIssue')) + '" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colIssue')) + '</span></th>' +
          '<th class="sortable" data-col="rerun" data-label="Run" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colRun')) + '</span></th>' +
          '<th class="sortable" data-col="engines" data-label="Engines" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colEngines')) + '</span></th>' +
          (hasVeraPDF ? '<th class="sortable" data-col="verapdf" data-label="veraPDF" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colVeraPdf')) + '</span></th>' : '') +
          (hasVPFail ? '<th class="sortable" data-col="vp_fail" data-label="' + esc(t('colVpdfFails')) + '" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colVpdfFails')) + '</span></th>' : '') +
          (hasVPError ? '<th class="sortable" data-col="vp_error" data-label="' + esc(t('colVpdfErrors')) + '" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colVpdfErrors')) + '</span></th>' : '') +
          '<th class="sortable" data-col="total" data-label="' + esc(t('colTotalPdfs')) + '" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colTotalPdfs')) + '</span></th>' +
          '<th class="sortable" data-col="accessible" data-label="' + esc(t('colAccessible')) + '" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colAccessible')) + '</span></th>' +
          '<th class="sortable" data-col="issues" data-label="' + esc(t('colIssues')) + '" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colIssues')) + '</span></th>' +
          '<th class="sortable" data-col="pct" data-label="' + esc(t('colPctAccessible')) + '" aria-sort="none" tabindex="0"><span class="sort-label">' + esc(t('colPctAccessible')) + '</span></th>' +
          '<th>' + esc(t('colReport')) + '</th>' +
          '</tr></thead><tbody>';

        sorted.forEach(function (r) {{
          var issues   = Math.max(0, (r.analysed || 0) - (r.accessible || 0));
          var dateStr  = r.date ? new Date(r.date).toLocaleString() : '';
          var issueLabel = r._issue_num
            ? '<a href="' + esc(r.issue_url) + '" target="_blank" rel="noopener">#' + esc(r._issue_num) + '</a>'
            : '&#x2014;';
          var runLabel = t('runLabel') + ' ' + (r._run_number || 1) + ' / ' + (r._run_total || 1);
          var enginesLabel = Array.isArray(r.analysis_engines)
            ? r.analysis_engines.join(', ')
            : (r.analysis_engines || r.engine || 'original');
          var verapdfLabel = r.verapdf ? '&#x2705;' : '&#x2014;';
          var vpFailLabel = (r.vp_fail_files != null) ? r.vp_fail_files : '&#x2014;';
          var vpErrorLabel = (r.vp_error_files != null) ? r.vp_error_files : '&#x2014;';
          var siteCell = r.crawl_url
            ? '<a href="' + esc(r.crawl_url) + '" target="_blank" rel="noopener">' + esc(r.site) + '</a>'
            : esc(r.site || '');
          var reportLink = '<a href="reports/' + esc(r.archive_file) + '">' + esc(t('viewReport')) + '</a>';
          html += '<tr>' +
            '<td>' + esc(dateStr) + '</td>' +
            '<td>' + siteCell + '</td>' +
            '<td>' + issueLabel + '</td>' +
            '<td>' + runLabel + '</td>' +
            '<td>' + esc(enginesLabel) + '</td>' +
            (hasVeraPDF ? '<td>' + verapdfLabel + '</td>' : '') +
            (hasVPFail ? '<td>' + vpFailLabel + '</td>' : '') +
            (hasVPError ? '<td>' + vpErrorLabel + '</td>' : '') +
            '<td>' + (r.total || 0) + '</td>' +
            '<td>' + (r.accessible || 0) + '</td>' +
            '<td>' + issues + '</td>' +
            '<td>' + pctBar(r.accessible || 0, r.analysed || 0) + '</td>' +
            '<td>' + reportLink + '</td>' +
            '</tr>';
        }});

        html += '</tbody></table>';
        root.innerHTML = html;
        var table = document.getElementById('reports-table');
        if (table) {{
          bindSorting(table, reports);
          updateHeaders(table);
        }}
      }}

      function applyFilter() {{
        var q = filterInput.value.trim().toLowerCase();
        var filtered = q
          ? allReports.filter(function (r) {{
              return (r.site || '').toLowerCase().indexOf(q) !== -1 ||
                     (r.crawl_url || '').toLowerCase().indexOf(q) !== -1;
            }})
          : allReports;
        filterCount.textContent = q
          ? t('showing') + ' ' + filtered.length + ' ' + t('of') + ' ' + allReports.length + ' ' + t('scans')
          : '';
        renderTable(filtered);
      }}

      filterInput.addEventListener('input', applyFilter);

      fetch('reports/index.json')
        .then(function (res) {{
          if (!res.ok) throw new Error('HTTP ' + res.status);
          return res.json();
        }})
        .then(function (data) {{
          allReports = Array.isArray(data) ? data : [];
          enrichRunInfo(allReports);
          renderSummary(allReports);
          renderTable(allReports);
        }})
        .catch(function (err) {{
          root.innerHTML =
            '<div class="error-state">' +
            '<p><strong>' + esc(t('loadErrorTitle')) + '</strong></p>' +
            '<p>Error: ' + esc(String(err)) + '</p>' +
            '<p>' + esc(t('loadErrorHint')) + '</p>' +
            '</div>';
        }});
    }})();
  </script>

  <footer>
    <p>
      Powered by
      <a href="https://github.com/accessibility-luxembourg/simplA11yPDFCrawler"
         target="_blank" rel="noopener">simplA11yPDFCrawler</a>
      and
      <a href="https://github.com/mgifford/pdf-crawler"
         target="_blank" rel="noopener">mgifford/pdf-crawler</a>.
      MIT licence.
    </p>
  </footer>

  <script>
    (function () {{
      var btn  = document.getElementById('theme-toggle');
      var root = document.documentElement;

      function applyTheme(theme) {{
        root.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
        if (theme === 'dark') {{
          btn.textContent = '\u2600\uFE0F';
          btn.setAttribute('aria-label', 'Switch to light mode');
          btn.setAttribute('title', 'Switch to light mode');
        }} else {{
          btn.textContent = '\U0001F319';
          btn.setAttribute('aria-label', 'Switch to dark mode');
          btn.setAttribute('title', 'Switch to dark mode');
        }}
      }}

      var stored      = localStorage.getItem('theme');
      var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      applyTheme(stored === 'dark' || stored === 'light' ? stored : (prefersDark ? 'dark' : 'light'));

      btn.addEventListener('click', function () {{
        applyTheme(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
      }});
    }})();
  </script>

</body>
</html>
"""


def generate_reports_index_html(reports_index: List[Dict[str, Any]]) -> str:  # pylint: disable=unused-argument
    """Return a standalone HTML page that dynamically loads scan reports from reports/index.json.

    The ``reports_index`` argument is accepted for API compatibility but the data
    is not embedded in the page; instead, the page fetches ``reports/index.json``
    at runtime so it always reflects the latest entries without needing to be
    regenerated on every workflow run.
    """
    return _REPORTS_INDEX_TEMPLATE.format()


def _scan_capabilities(
    raw_entries: List[Dict[str, Any]],
    site_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Return engine and veraPDF availability for a single archived scan."""
    scoped = (
        [e for e in raw_entries if e.get("site") == site_filter]
        if site_filter
        else raw_entries
    )

    engines: set[str] = set()
    vp_checked_files = 0
    vp_pass_files = 0
    vp_fail_files = 0
    vp_error_files = 0

    for entry in scoped:
        analyses = entry.get("analyses")
        if isinstance(analyses, dict) and analyses:
            for engine, engine_data in analyses.items():
                if isinstance(engine_data, dict):
                    engines.add(engine)
                    report = engine_data.get("report")
                    if isinstance(report, dict) and isinstance(report.get("veraPDF"), dict):
                        vp = report.get("veraPDF") or {}
                        vp_checked_files += 1
                        if vp.get("error"):
                            vp_error_files += 1
                        if vp.get("compliant") is True:
                            vp_pass_files += 1
                        elif vp.get("compliant") is False:
                            vp_fail_files += 1
        else:
            engines.add("original")
            report = entry.get("report")
            if isinstance(report, dict) and isinstance(report.get("veraPDF"), dict):
                vp = report.get("veraPDF") or {}
                vp_checked_files += 1
                if vp.get("error"):
                    vp_error_files += 1
                if vp.get("compliant") is True:
                    vp_pass_files += 1
                elif vp.get("compliant") is False:
                    vp_fail_files += 1

    if not engines:
        engines.add("original")

    return {
        "analysis_engines": sorted(engines),
      "verapdf": vp_checked_files > 0,
      "vp_checked_files": vp_checked_files,
      "vp_pass_files": vp_pass_files,
      "vp_fail_files": vp_fail_files,
      "vp_error_files": vp_error_files,
    }


def main(
    manifest_path: str = "reports/manifest.yaml",
    report_dir: str = "reports",
    site_filter: Optional[str] = None,
    issue_comment_file: Optional[str] = None,
    pages_base: str = "",
    run_url: str = "",
    crawl_url: str = "",
    html_dir: Optional[str] = None,
    archive_dir: Optional[str] = None,
    crawled_dir: Optional[str] = None,
    issue_url: str = "",
    spot_check_file: Optional[str] = None,
    engine_filter: Optional[str] = None,
    default_lang: Optional[str] = None,
) -> None:
    raw_entries = load_manifest(manifest_path)
    entries = _entries_for_reporting(raw_entries, engine_filter=engine_filter)
    stats = _summary_stats(entries)
    comparison = _engine_comparison(raw_entries)
    capabilities = _scan_capabilities(raw_entries, site_filter=site_filter)

    # Compute per-site stats for the archive index entry so that values in
    # index.json (Total PDFs, Analysed, Accessible) reflect only the current
    # site's entries rather than the cumulative totals of all sites.
    if site_filter:
        site_entries = [e for e in entries if e.get("site") == site_filter]
        site_stats = _summary_stats(site_entries)
    else:
        site_stats = stats

    # If a crawled-files directory is provided, read the crawl statistics from
    # the per-site JSON files written by the spider and copy crawled_urls.csv
    # to the report directory so it can be published via GitHub Pages.
    if crawled_dir is not None and site_filter:
        site_dir = Path(crawled_dir) / site_filter
        pages_path = site_dir / "_crawled_pages.json"
        if pages_path.exists():
            try:
                pages = json.loads(pages_path.read_text(encoding="utf-8"))
                stats["pages_crawled"] = len(pages)
            except Exception:
                pass
        crawled_csv_src = site_dir / "crawled_urls.csv"
        if crawled_csv_src.exists():
            shutil.copy2(crawled_csv_src, Path(report_dir) / "crawled_urls.csv")
            print(f"Copied: {Path(report_dir) / 'crawled_urls.csv'}")

    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Markdown report (full, all sites)
    md_path = out_dir / "report.md"
    md_content = generate_markdown(entries, stats)
    md_path.write_text(md_content, encoding="utf-8")
    print(f"Written: {md_path}")

    # JSON report (full, all sites)
    json_data = {"summary": stats, "comparison": comparison, "files": entries}
    json_path = out_dir / "report.json"
    json_path.write_text(
        json.dumps(json_data, indent=2, default=str), encoding="utf-8"
    )
    print(f"Written: {json_path}")

    # Structured JSON report (all analysed files)
    structured_json_path = out_dir / "report_structured.json"
    structured_data = generate_structured_json(entries, stats)
    structured_json_path.write_text(
        json.dumps(structured_data, indent=2, default=str), encoding="utf-8"
    )
    print(f"Written: {structured_json_path}")

    # CSV report (full, all sites)
    csv_path = out_dir / "report.csv"
    csv_path.write_text(generate_csv(entries), encoding="utf-8")
    print(f"Written: {csv_path}")

    # HTML report for GitHub Pages
    if html_dir is not None:
        html_out_dir = Path(html_dir)
        html_out_dir.mkdir(parents=True, exist_ok=True)
        html_path = html_out_dir / "report.html"
        html_path.write_text(
            generate_html(entries, stats, report_assets_base="reports"),
            encoding="utf-8",
        )
        print(f"Written: {html_path}")

    # Track the archive name so it can be included in the issue comment.
    archive_name: Optional[str] = None

    # Per-scan archive and historical reports index
    if archive_dir is not None and html_dir is not None:
        archive_out = Path(archive_dir)
        archive_out.mkdir(parents=True, exist_ok=True)

        archive_entries = site_entries if site_filter else entries
        archive_stats = site_stats if site_filter else stats
        archive_raw_entries = (
            [e for e in raw_entries if e.get("site") == site_filter]
            if site_filter
            else raw_entries
        )
        archive_comparison = _engine_comparison(archive_raw_entries)

        # Build a unique filename from the scan timestamp + site
        try:
            scan_dt = datetime.fromisoformat(stats["generated_at"])
        except Exception:
            scan_dt = datetime.now(timezone.utc)
        date_str = scan_dt.strftime("%Y-%m-%d_%H-%M-%S") + f"-{scan_dt.microsecond // 1000:03d}"
        safe_site = re.sub(r"[^a-zA-Z0-9._-]", "_", site_filter or "all")
        # Prevent directory traversal sequences in the site component
        safe_site = safe_site.replace("..", "_").strip(".")
        archive_name = f"{date_str}_{safe_site}.html"
        archive_stem = archive_name[:-5] if archive_name.endswith(".html") else archive_name
        archive_bundle_dir = archive_out / archive_stem
        archive_bundle_dir.mkdir(parents=True, exist_ok=True)

        # Write archived scan report (links back to the reports index)
        archive_path = archive_out / archive_name
        archive_path.write_text(
            generate_html(
            archive_entries,
            archive_stats,
                back_url="../reports.html",
                back_label="Back to reports index",
            report_assets_base=f"./{archive_stem}",
            ),
            encoding="utf-8",
        )
        print(f"Written: {archive_path}")

        # Update the persistent index JSON (newest first, no duplicates)
        index_path = archive_out / "index.json"
        report_index: List[Dict[str, Any]] = []
        if index_path.exists():
            try:
                report_index = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                report_index = []

        if not any(e.get("archive_file") == archive_name for e in report_index):
            report_index.insert(
                0,
                {
                    "date": stats["generated_at"],
                    "site": site_filter or "all",
                    "crawl_url": crawl_url,
                    "run_url": run_url,
                    "issue_url": issue_url,
                    "archive_file": archive_name,
                    "total": site_stats["total_files"],
                    "analysed": site_stats["analysed"],
                    "accessible": site_stats["accessible"],
                    "analysis_engines": capabilities["analysis_engines"],
                    "verapdf": capabilities["verapdf"],
                    "vp_checked_files": capabilities["vp_checked_files"],
                    "vp_pass_files": capabilities["vp_pass_files"],
                    "vp_fail_files": capabilities["vp_fail_files"],
                    "vp_error_files": capabilities["vp_error_files"],
                },
            )
            index_path.write_text(
                json.dumps(report_index, indent=2, default=str), encoding="utf-8"
            )
            print(f"Written: {index_path}")

        # Regenerate the reports index HTML page
        reports_html_path = Path(html_dir) / "reports.html"
        reports_html_path.write_text(
            generate_reports_index_html(report_index), encoding="utf-8"
        )
        print(f"Written: {reports_html_path}")

        # Copy the JSON, CSV, and manifest into the archive dir so they are
        # accessible via GitHub Pages (which serves from docs/ via _config.yml).
        pages_json = archive_bundle_dir / "report.json"
        archive_json_data = {
          "summary": archive_stats,
          "comparison": archive_comparison,
          "files": archive_entries,
        }
        pages_json.write_text(
          json.dumps(archive_json_data, indent=2, default=str),
          encoding="utf-8",
        )
        print(f"Copied:  {pages_json}")

        pages_structured_json = archive_bundle_dir / "report_structured.json"
        pages_structured_json.write_text(
          json.dumps(
            generate_structured_json(archive_entries, archive_stats),
            indent=2,
            default=str,
          ),
          encoding="utf-8",
        )
        print(f"Copied:  {pages_structured_json}")

        pages_csv = archive_bundle_dir / "report.csv"
        pages_csv.write_text(generate_csv(archive_entries), encoding="utf-8")
        print(f"Copied:  {pages_csv}")

        pages_manifest = archive_bundle_dir / "manifest.yaml"
        shutil.copy2(Path(manifest_path), pages_manifest)
        print(f"Copied:  {pages_manifest}")

        pages_crawled_urls = archive_bundle_dir / "crawled_urls.csv"
        source_crawled_urls = out_dir / "crawled_urls.csv"
        if source_crawled_urls.exists():
            shutil.copy2(source_crawled_urls, pages_crawled_urls)
            print(f"Copied:  {pages_crawled_urls}")
        else:
            pages_crawled_urls.write_text("", encoding="utf-8")
            print(f"Written: {pages_crawled_urls} (empty; source crawled_urls.csv not found)")

    # Optional per-site issue comment
    if issue_comment_file:
        # Load spot-check diagnostics if a file was provided.
        spot_check: Optional[Dict[str, Any]] = None
        if spot_check_file:
            try:
                spot_check = json.loads(Path(spot_check_file).read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                print(
                    f"Warning: could not load spot-check file {spot_check_file!r}: {exc}. "
                    "Spot-check diagnostics will be omitted from the issue comment."
                )

        comment = generate_issue_comment(
            entries,
            crawl_url=crawl_url,
            pages_base=pages_base,
            run_url=run_url,
            site_filter=site_filter,
            pages_crawled=stats.get("pages_crawled", 0),
            archive_name=archive_name,
            spot_check=spot_check,
          default_lang=default_lang,
        )
        Path(issue_comment_file).write_text(comment, encoding="utf-8")
        print(f"Written issue comment: {issue_comment_file}")


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Generate accessibility reports")
    parser.add_argument(
        "--manifest",
        default="reports/manifest.yaml",
        help="Path to the YAML manifest (default: reports/manifest.yaml)",
    )
    parser.add_argument(
        "--report-dir",
        default="reports",
        help="Directory to write reports into (default: reports)",
    )
    parser.add_argument(
        "--site",
        default=None,
        help="Site/domain to scope the issue-comment output to (e.g. energy.gov)",
    )
    parser.add_argument(
        "--issue-comment-file",
        default=None,
        help="Write a per-site GitHub issue comment body to this file path",
    )
    parser.add_argument(
        "--pages-base",
        default="",
        help="Base URL of the GitHub Pages site (for report links in the comment)",
    )
    parser.add_argument(
        "--run-url",
        default="",
        help="URL of the GitHub Actions run (for the 'View workflow run' link)",
    )
    parser.add_argument(
        "--crawl-url",
        default="",
        help="The URL that was crawled (shown in the comment header)",
    )
    parser.add_argument(
        "--html-dir",
        default=None,
        help="Directory to write the HTML report page into (e.g. docs)",
    )
    parser.add_argument(
        "--archive-dir",
        default=None,
        help=(
            "Directory to write per-scan archived HTML reports and index.json "
            "(e.g. docs/reports). Also regenerates docs/reports.html when set."
        ),
    )
    parser.add_argument(
        "--crawled-dir",
        default=None,
        help=(
            "Directory containing crawled files (e.g. crawled_files). "
            "When provided with --site, reads crawl statistics and copies "
            "crawled_urls.csv to the report directory."
        ),
    )
    parser.add_argument(
        "--issue-url",
        default="",
        help=(
            "URL of the GitHub issue comment for this scan "
            "(shown as a link in the reports index, e.g. "
            "https://github.com/owner/repo/issues/42#issuecomment-12345)"
        ),
    )
    parser.add_argument(
        "--spot-check-file",
        default=None,
        help=(
            "Path to a JSON file produced by spot_check_zero_results() in crawl.py "
            "(saved to scan-meta/spot_check.json during the crawl). When provided, "
            "its diagnostic findings are included in the issue comment when zero "
            "PDFs and zero pages were found."
        ),
    )
    parser.add_argument(
        "--engine",
        default=None,
        choices=["original", "bloom"],
        help=(
            "Report only results for this analysis engine. "
            "Use in CI so issue comments match the current scan mode."
        ),
    )
    parser.add_argument(
        "--default-lang",
        default=None,
        choices=["en", "fr"],
        help=(
            "Default language for report page links in issue comments. "
            "When set, HTML report links include ?lang=<value>."
        ),
    )
    args = parser.parse_args()
    main(
        manifest_path=args.manifest,
        report_dir=args.report_dir,
        site_filter=args.site,
        issue_comment_file=args.issue_comment_file,
        pages_base=args.pages_base,
        run_url=args.run_url,
        crawl_url=args.crawl_url,
        html_dir=args.html_dir,
        archive_dir=args.archive_dir,
        crawled_dir=args.crawled_dir,
        issue_url=args.issue_url,
        spot_check_file=args.spot_check_file,
        engine_filter=args.engine,
        default_lang=args.default_lang,
    )
