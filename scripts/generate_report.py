"""
Report generator.

Reads the YAML manifest and produces:
  - reports/report.md   – human-readable Markdown summary
  - reports/report.json – machine-readable JSON summary
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

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from manifest import load_manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = "✅ Pass"
_FAIL = "❌ Fail"
_NA = "—"


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

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_files": total,
        "analysed": len(analysed),
        "pending": len(pending),
        "errored": len(errored),
        "accessible": accessible,
        "totally_inaccessible": totally_inaccessible,
        "broken": broken,
        "exempt": exempt,
        "sites": sites,
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
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total files tracked | {stats['total_files']} |",
        f"| Analysed | {stats['analysed']} |",
        f"| Pending analysis | {stats['pending']} |",
        f"| Errors during analysis | {stats['errored']} |",
        f"| Accessible | {stats['accessible']} |",
        f"| Totally inaccessible | {stats['totally_inaccessible']} |",
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

    header = (
        "## File Details\n\n"
        "| File | Site | Published Date | Accessible | Tagged | EmptyText | Protected"
        " | Title | Language | Bookmarks | Exempt | Pages | Words | Images |\n"
        "|------|------|----------------|------------|--------|-----------|---------|"
        "-------|----------|-----------|--------|-------|-------|--------|\n"
    )

    rows = []
    for e in analysed:
        r = e.get("report") or {}
        url = e.get("url", "")
        filename = e.get("filename", url)
        site = e.get("site", "")
        date_val = r.get("Date")
        date_str = str(date_val)[:10] if date_val else _NA
        rows.append(
            f"| [{filename}]({url}) "
            f"| {site} "
            f"| {date_str} "
            f"| {_fmt(r.get('Accessible'))} "
            f"| {_fmt(r.get('TaggedTest'))} "
            f"| {_fmt(r.get('EmptyTextTest'))} "
            f"| {_fmt(r.get('ProtectedTest'))} "
            f"| {_fmt(r.get('TitleTest'))} "
            f"| {_fmt(r.get('LanguageTest'))} "
            f"| {_fmt(r.get('BookmarksTest'))} "
            f"| {_fmt(r.get('Exempt'))} "
            f"| {r.get('Pages', _NA)} "
            f"| {r.get('Words') if r.get('Words') is not None else _NA} "
            f"| {r.get('Images') if r.get('Images') is not None else _NA} |"
        )
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
    "status",
    "crawled_at",
    "published_date",
    "accessible",
    "totally_inaccessible",
    "broken",
    "tagged",
    "empty_text",
    "protected",
    "title",
    "language",
    "bookmarks",
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
                "status": e.get("status", ""),
                "crawled_at": e.get("crawled_at", ""),
                "published_date": r.get("Date", ""),
                "accessible": r.get("Accessible", ""),
                "totally_inaccessible": r.get("TotallyInaccessible", ""),
                "broken": r.get("BrokenFile", ""),
                "tagged": r.get("TaggedTest", ""),
                "empty_text": r.get("EmptyTextTest", ""),
                "protected": r.get("ProtectedTest", ""),
                "title": r.get("TitleTest", ""),
                "language": r.get("LanguageTest", ""),
                "bookmarks": r.get("BookmarksTest", ""),
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
) -> str:
    """Return a Markdown string suitable for posting as a GitHub issue comment.

    If *site_filter* is provided, only entries for that site are included in
    the per-file table (the summary counts use those same filtered entries).
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
                "> - **Different subdomain** – PDFs hosted on another subdomain (e.g. `files.example.com`) are out of scope.",
                "> - **Deeper pages** – try submitting a more specific starting URL (e.g. a `/documents` or `/resources` sub-page).",
                ">",
                f"> Review the [Crawled URLs]({pages_base}/reports/crawled_urls.csv) to see which pages were visited,",
                f"> and the [workflow run]({run_url}) logs for any crawl warnings.",
                "",
            ]

    if analysed:
        lines += [
            "## PDFs Scanned",
            "",
            "| PDF | Accessible | Tagged | Title | Language | Bookmarks | Pages | Words | Images |",
            "|-----|-----------|--------|-------|----------|-----------|-------|-------|--------|",
        ]
        for e in analysed[:max_files]:
            r = e.get("report") or {}
            url = e.get("url", "")
            filename = e.get("filename", url.split("/")[-1])
            words = r.get("Words")
            images = r.get("Images")
            lines.append(
                f"| [{filename}]({url})"
                f" | {_icon(r.get('Accessible'))}"
                f" | {_icon(r.get('TaggedTest'))}"
                f" | {_icon(r.get('TitleTest'))}"
                f" | {_icon(r.get('LanguageTest'))}"
                f" | {_icon(r.get('BookmarksTest'))}"
                f" | {r.get('Pages', '—')}"
                f" | {words if words is not None else '—'}"
                f" | {images if images is not None else '—'} |"
            )
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
        f"- [HTML report]({pages_base}/report.html)",
        f"- [Reports history]({pages_base}/reports.html)",
        f"- [Markdown report]({pages_base}/reports/report.md)",
        f"- [JSON report]({pages_base}/reports/report.json)",
        f"- [CSV report]({pages_base}/reports/report.csv)",
        f"- [Crawled URLs CSV]({pages_base}/reports/crawled_urls.csv)",
        f"- [YAML manifest]({pages_base}/reports/manifest.yaml)",
        f"- [View workflow run]({run_url})",
    ]

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
    <a href="{back_url}">&#8592; {back_label}</a>
    <button class="theme-toggle" id="theme-toggle" aria-label="Switch to dark mode" title="Switch to dark mode">&#127769;</button>
  </nav>

  <h1>&#128202; PDF Accessibility Scan Results</h1>
  <p id="generated-at"></p>

  <div id="root"></div>

  <script type="application/json" id="report-data">
{json_data}
  </script>

  <script>
    (function () {{
      var raw  = document.getElementById('report-data').textContent;
      var data = JSON.parse(raw);
      var summary = data.summary || {{}};
      var files   = data.files   || [];
      var root    = document.getElementById('root');

      if (!summary.total_files) {{
        root.innerHTML =
          '<div class="empty-state">' +
          '<p>No scan data available yet.</p>' +
          '<p><a href="./">Submit a crawl request</a> to get started.</p>' +
          '</div>';
        return;
      }}

      // Generated-at timestamp
      if (summary.generated_at) {{
        document.getElementById('generated-at').textContent =
          'Last updated: ' + new Date(summary.generated_at).toLocaleString();
      }}

      var html = '';

      // --- Summary cards ---
      var cards = [
        {{ value: summary.total_files,         label: 'Total PDFs' }},
        {{ value: summary.analysed,            label: 'Analysed' }},
        {{ value: summary.accessible,          label: '&#x2705; Accessible' }},
        {{ value: summary.totally_inaccessible,label: '&#x274C; Inaccessible' }},
        {{ value: summary.pending,             label: '&#x23F3; Pending' }},
        {{ value: summary.errored,             label: '&#x26A0;&#xFE0F; Errors' }},
      ];
      html += '<h2>Summary</h2><div class="stats-grid">';
      cards.forEach(function (c) {{
        html += '<div class="stat-card"><div class="value">' + (c.value || 0) +
                '</div><div class="label">' + c.label + '</div></div>';
      }});
      html += '</div>';

      // --- Sites table ---
      var sites = summary.sites || {{}};
      var siteNames = Object.keys(sites).sort();
      if (siteNames.length) {{
        html += '<h2>Sites Scanned</h2>';
        html += '<table><thead><tr><th>Site</th><th>PDFs</th></tr></thead><tbody>';
        siteNames.forEach(function (s) {{
          html += '<tr><td>' + esc(s) + '</td><td>' + sites[s] + '</td></tr>';
        }});
        html += '</tbody></table>';
      }}

      // --- File details table ---
      var analysed = files.filter(function (f) {{ return f.status === 'analysed'; }});

      // Determine which optional columns have data
      var hasWords  = analysed.some(function (f) {{ return f.report && f.report.Words  != null; }});
      var hasImages = analysed.some(function (f) {{ return f.report && f.report.Images != null; }});

      // Sort state
      var sortCol = null;
      var sortAsc = true;

      function colVal(f, col) {{
        var r = f.report || {{}};
        switch (col) {{
          case 'file':       return (f.filename || f.url || '').toLowerCase();
          case 'site':       return (f.site || '').toLowerCase();
          case 'date':       return r.Date ? String(r.Date) : '';
          case 'accessible': return r.Accessible === true ? 1 : r.Accessible === false ? 0 : -1;
          case 'tagged':     return r.TaggedTest    === 'Pass' ? 1 : r.TaggedTest    === 'Fail' ? 0 : -1;
          case 'title':      return r.TitleTest     === 'Pass' ? 1 : r.TitleTest     === 'Fail' ? 0 : -1;
          case 'language':   return r.LanguageTest  === 'Pass' ? 1 : r.LanguageTest  === 'Fail' ? 0 : -1;
          case 'bookmarks':  return r.BookmarksTest === 'Pass' ? 1 : r.BookmarksTest === 'Fail' ? 0 : -1;
          case 'pages':      return r.Pages  != null ? r.Pages  : -1;
          case 'words':      return r.Words  != null ? r.Words  : -1;
          case 'images':     return r.Images != null ? r.Images : -1;
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
        return '<tr>' +
          '<td><a href="' + esc(f.url) + '" target="_blank" rel="noopener">' + esc(f.filename || f.url) + '</a></td>' +
          '<td>' + esc(f.site || '') + '</td>' +
          '<td>' + (dateStr ? esc(dateStr) : '&#x2014;') + '</td>' +
          '<td>' + icon(r.Accessible)    + '</td>' +
          '<td>' + icon(r.TaggedTest)    + '</td>' +
          '<td>' + icon(r.TitleTest)     + '</td>' +
          '<td>' + icon(r.LanguageTest)  + '</td>' +
          '<td>' + icon(r.BookmarksTest) + '</td>' +
          '<td>' + (r.Pages  != null ? r.Pages  : '&#x2014;') + '</td>' +
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
        var colDefs = [
          {{ key: 'file',       label: 'File' }},
          {{ key: 'site',       label: 'Site' }},
          {{ key: 'date',       label: 'Published Date' }},
          {{ key: 'accessible', label: 'Accessible' }},
          {{ key: 'tagged',     label: 'Tagged' }},
          {{ key: 'title',      label: 'Title' }},
          {{ key: 'language',   label: 'Language' }},
          {{ key: 'bookmarks',  label: 'Bookmarks' }},
          {{ key: 'pages',      label: 'Pages' }},
        ];
        if (hasWords)  colDefs.push({{ key: 'words',  label: 'Words' }});
        if (hasImages) colDefs.push({{ key: 'images', label: 'Images' }});

        html += '<h2>PDF Details</h2>';
        html += '<p>&#x2705; = Pass/Accessible &nbsp; &#x274C; = Fail/Inaccessible &nbsp; &#x2014; = Not applicable</p>';
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
) -> str:
    """Return a standalone HTML page with scan results embedded as JSON."""
    json_data = json.dumps({"summary": stats, "files": entries}, indent=2, default=str)
    return _HTML_TEMPLATE.format(json_data=json_data, back_url=back_url, back_label=back_label)


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
    <a href="./">&#8592; Back to submission form</a>
    <button class="theme-toggle" id="theme-toggle" aria-label="Switch to dark mode" title="Switch to dark mode">&#127769;</button>
  </nav>

  <h1>&#128202; PDF Accessibility Scan Reports</h1>
  <p>Historical record of all PDF accessibility scans run by this tool.</p>

  <div id="summary-bar" class="summary-bar" aria-live="polite"></div>

  <div class="filter-bar">
    <label for="filter-input">Filter by site:</label>
    <input type="search" id="filter-input" placeholder="e.g. energy.gov" aria-label="Filter reports by site name" />
    <span id="filter-count" class="filter-count" aria-live="polite"></span>
  </div>

  <div id="root" aria-live="polite">
    <div class="loading-state">Loading reports&hellip;</div>
  </div>

  <script>
    (function () {{
      var root        = document.getElementById('root');
      var summaryBar  = document.getElementById('summary-bar');
      var filterInput = document.getElementById('filter-input');
      var filterCount = document.getElementById('filter-count');
      var allReports  = [];

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
          '<div class="summary-card"><div class="value">' + reports.length + '</div><div class="label">Total Scans</div></div>' +
          '<div class="summary-card"><div class="value">' + Object.keys(sites).length + '</div><div class="label">Unique Sites</div></div>';
      }}

      function deduplicateReports(reports) {{
        // Keep only the latest entry per issue.
        // index.json is sorted newest-first so the first occurrence is the
        // most-recent scan.  When an issue_url is present the issue number is
        // used as the key so that all re-runs of the same issue collapse to
        // one row regardless of any site-name differences between runs.
        // Entries with no issue_url (manual runs) are grouped by site alone.
        var seen = {{}};
        var result = [];
        reports.forEach(function (r) {{
          var m = r.issue_url ? r.issue_url.match(/\\/issues\\/(\\d+)/) : null;
          var issueKey = m ? m[1] : '';
          var key = issueKey || (r.site || '');
          if (!seen[key]) {{
            seen[key] = true;
            result.push(r);
          }}
        }});
        return result;
      }}

      function renderTable(reports) {{
        if (!reports.length) {{
          root.innerHTML =
            '<div class="empty-state">' +
            '<p>No scan reports yet.</p>' +
            '<p><a href="./">Submit a crawl request</a> to get started.</p>' +
            '</div>';
          return;
        }}

        var html = '<table><thead><tr>' +
          '<th>Date</th><th>Site</th><th>Total PDFs</th>' +
          '<th>&#x2705; Accessible</th><th>&#x274C; Issues</th>' +
          '<th>% Accessible</th><th>Report</th>' +
          '</tr></thead><tbody>';

        reports.forEach(function (r) {{
          var issues   = Math.max(0, (r.analysed || 0) - (r.accessible || 0));
          var dateStr  = r.date ? new Date(r.date).toLocaleString() : '';
          var siteCell = r.crawl_url
            ? '<a href="' + esc(r.crawl_url) + '" target="_blank" rel="noopener">' + esc(r.site) + '</a>'
            : esc(r.site || '');
          var reportLink = '<a href="reports/' + esc(r.archive_file) + '">View report</a>';
          var issueNum  = r.issue_url ? (r.issue_url.match(/\\/issues\\/(\\d+)/) || [])[1] : null;
          var issueLink = r.issue_url
            ? ' &nbsp;<a href="' + esc(r.issue_url) + '" target="_blank" rel="noopener">' +
              (issueNum ? '#' + issueNum : 'Issue') + '</a>'
            : '';
          html += '<tr>' +
            '<td>' + esc(dateStr) + '</td>' +
            '<td>' + siteCell + '</td>' +
            '<td>' + (r.total || 0) + '</td>' +
            '<td>' + (r.accessible || 0) + '</td>' +
            '<td>' + issues + '</td>' +
            '<td>' + pctBar(r.accessible || 0, r.analysed || 0) + '</td>' +
            '<td>' + reportLink + issueLink + '</td>' +
            '</tr>';
        }});

        html += '</tbody></table>';
        root.innerHTML = html;
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
          ? 'Showing ' + filtered.length + ' of ' + allReports.length + ' scans'
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
          allReports = deduplicateReports(allReports);
          renderSummary(allReports);
          renderTable(allReports);
        }})
        .catch(function (err) {{
          root.innerHTML =
            '<div class="error-state">' +
            '<p><strong>Could not load reports.</strong></p>' +
            '<p>Error: ' + esc(String(err)) + '</p>' +
            '<p>If you are viewing this file locally, please serve it from a web server.</p>' +
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


def generate_reports_index_html(reports_index: List[Dict[str, Any]]) -> str:
    """Return a standalone HTML page that dynamically loads scan reports from reports/index.json.

    The ``reports_index`` argument is accepted for API compatibility but the data
    is not embedded in the page; instead, the page fetches ``reports/index.json``
    at runtime so it always reflects the latest entries without needing to be
    regenerated on every workflow run.
    """
    return _REPORTS_INDEX_TEMPLATE.format()


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
) -> None:
    entries = load_manifest(manifest_path)
    stats = _summary_stats(entries)

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
                import json as _json
                pages = _json.loads(pages_path.read_text(encoding="utf-8"))
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
    json_data = {"summary": stats, "files": entries}
    json_path = out_dir / "report.json"
    json_path.write_text(
        json.dumps(json_data, indent=2, default=str), encoding="utf-8"
    )
    print(f"Written: {json_path}")

    # CSV report (full, all sites)
    csv_path = out_dir / "report.csv"
    csv_path.write_text(generate_csv(entries), encoding="utf-8")
    print(f"Written: {csv_path}")

    # HTML report for GitHub Pages
    if html_dir is not None:
        html_out_dir = Path(html_dir)
        html_out_dir.mkdir(parents=True, exist_ok=True)
        html_path = html_out_dir / "report.html"
        html_path.write_text(generate_html(entries, stats), encoding="utf-8")
        print(f"Written: {html_path}")

    # Per-scan archive and historical reports index
    if archive_dir is not None and html_dir is not None:
        archive_out = Path(archive_dir)
        archive_out.mkdir(parents=True, exist_ok=True)

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

        # Write archived scan report (links back to the reports index)
        archive_path = archive_out / archive_name
        archive_path.write_text(
            generate_html(
                site_entries if site_filter else entries,
                site_stats,
                back_url="../reports.html",
                back_label="Back to reports index",
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
        pages_json = archive_out / "report.json"
        shutil.copy2(json_path, pages_json)
        print(f"Copied:  {pages_json}")

        pages_csv = archive_out / "report.csv"
        shutil.copy2(csv_path, pages_csv)
        print(f"Copied:  {pages_csv}")

        pages_manifest = archive_out / "manifest.yaml"
        shutil.copy2(Path(manifest_path), pages_manifest)
        print(f"Copied:  {pages_manifest}")

    # Optional per-site issue comment
    if issue_comment_file:
        comment = generate_issue_comment(
            entries,
            crawl_url=crawl_url,
            pages_base=pages_base,
            run_url=run_url,
            site_filter=site_filter,
            pages_crawled=stats.get("pages_crawled", 0),
        )
        Path(issue_comment_file).write_text(comment, encoding="utf-8")
        print(f"Written issue comment: {issue_comment_file}")


if __name__ == "__main__":
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
    )
