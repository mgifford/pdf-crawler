"""
Report generator.

Reads the YAML manifest and produces:
  - reports/report.md   – human-readable Markdown summary
  - reports/report.json – machine-readable JSON summary

Usage:
    python generate_report.py [--manifest reports/manifest.yaml]
    python generate_report.py --site energy.gov --issue-comment-file /tmp/comment.md
"""

from __future__ import annotations

import json
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
        "| File | Site | Accessible | Tagged | EmptyText | Protected"
        " | Title | Language | Bookmarks | Exempt | Pages |\n"
        "|------|------|------------|--------|-----------|---------|"
        "-------|----------|-----------|--------|-------|\n"
    )

    rows = []
    for e in analysed:
        r = e.get("report") or {}
        url = e.get("url", "")
        filename = e.get("filename", url)
        site = e.get("site", "")
        rows.append(
            f"| [{filename}]({url}) "
            f"| {site} "
            f"| {_fmt(r.get('Accessible'))} "
            f"| {_fmt(r.get('TaggedTest'))} "
            f"| {_fmt(r.get('EmptyTextTest'))} "
            f"| {_fmt(r.get('ProtectedTest'))} "
            f"| {_fmt(r.get('TitleTest'))} "
            f"| {_fmt(r.get('LanguageTest'))} "
            f"| {_fmt(r.get('BookmarksTest'))} "
            f"| {_fmt(r.get('Exempt'))} "
            f"| {r.get('Pages', _NA)} |"
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

    if analysed:
        lines += [
            "## PDFs Scanned",
            "",
            "| PDF | Accessible | Tagged | Title | Language | Bookmarks | Pages |",
            "|-----|-----------|--------|-------|----------|-----------|-------|",
        ]
        for e in analysed[:max_files]:
            r = e.get("report") or {}
            url = e.get("url", "")
            filename = e.get("filename", url.split("/")[-1])
            lines.append(
                f"| [{filename}]({url})"
                f" | {_icon(r.get('Accessible'))}"
                f" | {_icon(r.get('TaggedTest'))}"
                f" | {_icon(r.get('TitleTest'))}"
                f" | {_icon(r.get('LanguageTest'))}"
                f" | {_icon(r.get('BookmarksTest'))}"
                f" | {r.get('Pages', '—')} |"
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
        f"- [Markdown report]({pages_base}/reports/report.md)",
        f"- [JSON report]({pages_base}/reports/report.json)",
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
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}

    body {{
      font-family: system-ui, -apple-system, sans-serif;
      max-width: 1000px;
      margin: 0 auto;
      padding: 2rem 1rem;
      color: #1a1a2e;
      background: #f8f9fa;
    }}

    nav {{ margin-bottom: 1.5rem; }}
    nav a {{ color: #0d6efd; text-decoration: none; }}
    nav a:hover {{ text-decoration: underline; }}

    h1 {{ color: #0d6efd; }}
    h2 {{ margin-top: 2rem; }}

    #generated-at {{ font-size: 0.85rem; color: #6c757d; margin-top: -0.5rem; }}

    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 1rem;
      margin: 1.5rem 0;
    }}
    .stat-card {{
      background: #fff;
      border: 1px solid #dee2e6;
      border-radius: 0.375rem;
      padding: 1rem;
      text-align: center;
    }}
    .stat-card .value {{
      font-size: 2rem;
      font-weight: 700;
      color: #0d6efd;
      line-height: 1.1;
    }}
    .stat-card .label {{ font-size: 0.8rem; color: #6c757d; margin-top: 0.25rem; }}

    table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.9rem; }}
    th {{
      background: #e9ecef;
      padding: 0.5rem 0.75rem;
      text-align: left;
      border-bottom: 2px solid #dee2e6;
    }}
    td {{ padding: 0.5rem 0.75rem; border-bottom: 1px solid #dee2e6; vertical-align: top; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:nth-child(even) td {{ background: #f8f9fa; }}

    .pass {{ color: #198754; }}
    .fail {{ color: #dc3545; }}
    .na   {{ color: #6c757d; }}

    a {{ color: #0d6efd; }}

    .empty-state {{
      background: #fff;
      border: 1px solid #dee2e6;
      border-radius: 0.375rem;
      padding: 2rem;
      text-align: center;
      color: #6c757d;
    }}

    footer {{
      margin-top: 3rem;
      font-size: 0.8rem;
      color: #6c757d;
      border-top: 1px solid #dee2e6;
      padding-top: 1rem;
    }}
  </style>
</head>
<body>

  <nav><a href="./">&#8592; Back to submission form</a></nav>

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
      if (analysed.length) {{
        html += '<h2>PDF Details</h2>';
        html += '<p>&#x2705; = Pass/Accessible &nbsp; &#x274C; = Fail/Inaccessible &nbsp; &#x2014; = Not applicable</p>';
        html += '<table><thead><tr>' +
          '<th>File</th><th>Site</th><th>Notes</th><th>Accessible</th>' +
          '<th>Tagged</th><th>Title</th><th>Language</th><th>Bookmarks</th><th>Pages</th>' +
          '</tr></thead><tbody>';
        analysed.forEach(function (f) {{
          var r = f.report || {{}};
          html += '<tr>' +
            '<td><a href="' + esc(f.url) + '" target="_blank" rel="noopener">' +
              esc(f.filename || f.url) + '</a></td>' +
            '<td>' + esc(f.site || '') + '</td>' +
            '<td>' + esc(f.notes || '') + '</td>' +
            '<td>' + icon(r.Accessible)     + '</td>' +
            '<td>' + icon(r.TaggedTest)     + '</td>' +
            '<td>' + icon(r.TitleTest)      + '</td>' +
            '<td>' + icon(r.LanguageTest)   + '</td>' +
            '<td>' + icon(r.BookmarksTest)  + '</td>' +
            '<td>' + (r.Pages != null ? r.Pages : '&#x2014;') + '</td>' +
            '</tr>';
        }});
        html += '</tbody></table>';
      }}

      root.innerHTML = html;

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

</body>
</html>
"""


def generate_html(entries: List[Dict[str, Any]], stats: Dict[str, Any]) -> str:
    """Return a standalone HTML page with scan results embedded as JSON."""
    json_data = json.dumps({"summary": stats, "files": entries}, indent=2, default=str)
    return _HTML_TEMPLATE.format(json_data=json_data)




def main(
    manifest_path: str = "reports/manifest.yaml",
    report_dir: str = "reports",
    site_filter: Optional[str] = None,
    issue_comment_file: Optional[str] = None,
    pages_base: str = "",
    run_url: str = "",
    crawl_url: str = "",
    html_dir: Optional[str] = None,
) -> None:
    entries = load_manifest(manifest_path)
    stats = _summary_stats(entries)

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

    # HTML report for GitHub Pages
    if html_dir is not None:
        html_out_dir = Path(html_dir)
        html_out_dir.mkdir(parents=True, exist_ok=True)
        html_path = html_out_dir / "report.html"
        html_path.write_text(generate_html(entries, stats), encoding="utf-8")
        print(f"Written: {html_path}")

    # Optional per-site issue comment
    if issue_comment_file:
        comment = generate_issue_comment(
            entries,
            crawl_url=crawl_url,
            pages_base=pages_base,
            run_url=run_url,
            site_filter=site_filter,
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
    )
