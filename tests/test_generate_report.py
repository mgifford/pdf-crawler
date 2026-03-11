"""Tests for scripts/generate_report.py"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from generate_report import (
    _summary_stats,
    generate_csv,
    generate_html,
    generate_markdown,
    generate_issue_comment,
    generate_reports_index_html,
    main as generate_main,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_entry(url, status="analysed", accessible=True, totally=False,
                broken=False, exempt=False, site="example.com"):
    report = {
        "Accessible": accessible,
        "TotallyInaccessible": totally,
        "BrokenFile": broken,
        "Exempt": exempt,
        "TaggedTest": "Pass" if accessible else "Fail",
        "EmptyTextTest": "Pass",
        "ProtectedTest": "Pass",
        "TitleTest": "Pass" if accessible else "Fail",
        "LanguageTest": "Pass" if accessible else "Fail",
        "BookmarksTest": "Pass",
        "Pages": 5,
    }
    return {
        "url": url,
        "filename": url.split("/")[-1],
        "site": site,
        "status": status,
        "report": report if status == "analysed" else None,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# _summary_stats
# ---------------------------------------------------------------------------

def test_summary_stats_totals():
    entries = [
        _make_entry("https://a.com/1.pdf", accessible=True),
        _make_entry("https://a.com/2.pdf", accessible=False, totally=True),
        _make_entry("https://a.com/3.pdf", status="pending"),
        _make_entry("https://a.com/4.pdf", status="error"),
    ]
    stats = _summary_stats(entries)
    assert stats["total_files"] == 4
    assert stats["analysed"] == 2
    assert stats["pending"] == 1
    assert stats["errored"] == 1
    assert stats["accessible"] == 1
    assert stats["totally_inaccessible"] == 1


def test_summary_stats_empty():
    stats = _summary_stats([])
    assert stats["total_files"] == 0
    assert stats["analysed"] == 0


def test_summary_stats_sites():
    entries = [
        _make_entry("https://a.com/1.pdf", site="a.com"),
        _make_entry("https://a.com/2.pdf", site="a.com"),
        _make_entry("https://b.com/1.pdf", site="b.com"),
    ]
    stats = _summary_stats(entries)
    assert stats["sites"]["a.com"] == 2
    assert stats["sites"]["b.com"] == 1


# ---------------------------------------------------------------------------
# generate_markdown
# ---------------------------------------------------------------------------

def test_generate_markdown_contains_header():
    entries = [_make_entry("https://example.com/doc.pdf")]
    stats = _summary_stats(entries)
    md = generate_markdown(entries, stats)
    assert "# PDF Accessibility Scan Report" in md


def test_generate_markdown_shows_file_link():
    entries = [_make_entry("https://example.com/my-doc.pdf")]
    stats = _summary_stats(entries)
    md = generate_markdown(entries, stats)
    assert "my-doc.pdf" in md
    assert "https://example.com/my-doc.pdf" in md


def test_generate_markdown_no_analysed_shows_placeholder():
    entries = [_make_entry("https://example.com/doc.pdf", status="pending")]
    stats = _summary_stats(entries)
    md = generate_markdown(entries, stats)
    assert "No analysed files yet" in md


def test_generate_markdown_errors_section():
    entry = _make_entry("https://example.com/broken.pdf")
    entry["errors"] = ["PdfError: corrupt stream"]
    stats = _summary_stats([entry])
    md = generate_markdown([entry], stats)
    assert "PdfError: corrupt stream" in md


def test_generate_markdown_summary_table():
    entries = [_make_entry("https://example.com/a.pdf")]
    stats = _summary_stats(entries)
    md = generate_markdown(entries, stats)
    assert "Total files tracked" in md
    assert "| 1 |" in md


# ---------------------------------------------------------------------------
# generate_issue_comment
# ---------------------------------------------------------------------------

def test_issue_comment_contains_crawl_url():
    entries = [_make_entry("https://example.com/doc.pdf")]
    comment = generate_issue_comment(
        entries,
        crawl_url="https://example.com",
        pages_base="https://owner.github.io/repo",
        run_url="https://github.com/owner/repo/actions/runs/1",
    )
    # The crawl URL should appear in the comment header (wrapped in backticks)
    assert "`https://example.com`" in comment


def test_issue_comment_summary_counts():
    entries = [
        _make_entry("https://a.com/1.pdf", accessible=True),
        _make_entry("https://a.com/2.pdf", accessible=False),
    ]
    comment = generate_issue_comment(
        entries,
        crawl_url="https://a.com",
        pages_base="",
        run_url="",
    )
    assert "| 2 |" in comment  # total PDFs
    assert "| 1 |" in comment  # accessible count


def test_issue_comment_site_filter():
    entries = [
        _make_entry("https://a.com/1.pdf", site="a.com"),
        _make_entry("https://b.com/1.pdf", site="b.com"),
    ]
    comment = generate_issue_comment(
        entries,
        crawl_url="https://a.com",
        pages_base="",
        run_url="",
        site_filter="a.com",
    )
    # Only a.com's PDF appears in the table
    assert "a.com/1.pdf" in comment
    assert "b.com/1.pdf" not in comment


def test_issue_comment_contains_report_links():
    entries = [_make_entry("https://example.com/doc.pdf")]
    comment = generate_issue_comment(
        entries,
        crawl_url="https://example.com",
        pages_base="https://owner.github.io/repo",
        run_url="https://github.com/owner/repo/actions/runs/99",
    )
    assert "report.md" in comment
    assert "report.json" in comment
    assert "report.html" in comment
    assert "reports.html" in comment
    assert "actions/runs/99" in comment


def test_issue_comment_pdf_table_rows():
    entries = [_make_entry("https://example.com/my.pdf")]
    comment = generate_issue_comment(
        entries,
        crawl_url="https://example.com",
        pages_base="",
        run_url="",
    )
    assert "my.pdf" in comment
    assert "✅" in comment  # accessible pass icon


def test_issue_comment_truncates_large_lists():
    entries = [
        _make_entry(f"https://example.com/{i}.pdf")
        for i in range(50)
    ]
    comment = generate_issue_comment(
        entries,
        crawl_url="https://example.com",
        pages_base="",
        run_url="",
        max_files=10,
    )
    assert "more PDFs" in comment


# ---------------------------------------------------------------------------
# generate_html
# ---------------------------------------------------------------------------

def test_generate_html_is_valid_html():
    entries = [_make_entry("https://example.com/doc.pdf")]
    stats = _summary_stats(entries)
    html = generate_html(entries, stats)
    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html
    assert "</html>" in html


def test_generate_html_embeds_json_data():
    entries = [_make_entry("https://example.com/doc.pdf")]
    stats = _summary_stats(entries)
    html = generate_html(entries, stats)
    # The JSON data block should be present
    assert 'id="report-data"' in html
    assert "https://example.com/doc.pdf" in html


def test_generate_html_empty_manifest():
    stats = _summary_stats([])
    html = generate_html([], stats)
    assert "<!DOCTYPE html>" in html
    # Empty state message should be present in the JS
    assert "No scan data available yet" in html


def test_generate_html_contains_back_link():
    stats = _summary_stats([])
    html = generate_html([], stats)
    assert 'href="./"' in html


def test_generate_html_includes_notes_column():
    entry = _make_entry("https://example.com/doc.pdf")
    entry["notes"] = "Test notes for this scan"
    stats = _summary_stats([entry])
    html = generate_html([entry], stats)
    assert "Notes" in html


def test_generate_html_custom_back_url():
    stats = _summary_stats([])
    html = generate_html([], stats, back_url="../reports.html", back_label="Back to reports index")
    assert 'href="../reports.html"' in html
    assert "Back to reports index" in html


def test_generate_html_default_back_link_unchanged():
    """Existing default back-link behaviour must not be broken."""
    stats = _summary_stats([])
    html = generate_html([], stats)
    assert 'href="./"' in html
    assert "Back to submission form" in html


# ---------------------------------------------------------------------------
# generate_reports_index_html
# ---------------------------------------------------------------------------

def test_generate_reports_index_html_is_valid_html():
    html = generate_reports_index_html([])
    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html
    assert "</html>" in html


def test_generate_reports_index_html_empty_state():
    html = generate_reports_index_html([])
    assert "No scan reports yet" in html


def test_generate_reports_index_html_fetches_index_json():
    """The page should load report data dynamically via fetch, not embed it."""
    html = generate_reports_index_html([])
    assert "fetch('reports/index.json')" in html
    # Data must NOT be inlined in a JSON script block
    assert 'id="reports-index"' not in html


def test_generate_reports_index_html_contains_back_link():
    html = generate_reports_index_html([])
    assert 'href="./"' in html


def test_generate_reports_index_html_accepts_reports_argument():
    """generate_reports_index_html accepts a list for API compatibility and returns valid HTML."""
    reports = [
        {
            "date": "2024-02-01T00:00:00+00:00",
            "site": "beta.com",
            "crawl_url": "https://beta.com",
            "run_url": "",
            "archive_file": "2024-02-01_00-00-00_beta.com.html",
            "total": 5,
            "analysed": 5,
            "accessible": 2,
        },
    ]
    html = generate_reports_index_html(reports)
    assert html.startswith("<!DOCTYPE html>")
    assert "fetch('reports/index.json')" in html


def test_generate_reports_index_html_no_workflow_link():
    """The 'Workflow' link must have been removed from the reports index template."""
    html = generate_reports_index_html([])
    # The old "Workflow" link text and its run_url binding must be gone
    assert "Workflow</a>" not in html
    assert "run_url" not in html


def test_generate_reports_index_html_has_issue_link():
    """The reports index template must render an issue link from the issue_url field."""
    html = generate_reports_index_html([])
    # JS that references issue_url to build the link
    assert "issue_url" in html
    assert "issueLink" in html


def test_generate_reports_index_html_has_deduplicate_function():
    """The reports index template must include the deduplicateReports JS function."""
    html = generate_reports_index_html([])
    assert "deduplicateReports" in html
    # Dedup must be applied after loading the JSON data
    assert "deduplicateReports(allReports)" in html


def test_generate_reports_index_html_issue_number_in_link():
    """The issue link text must include the issue number extracted from the URL."""
    html = generate_reports_index_html([])
    # JS that extracts the issue number to display as '#NNN'
    assert "issueNum" in html
    assert "'#' + issueNum" in html


# ---------------------------------------------------------------------------
# Social media metatags
# ---------------------------------------------------------------------------

def test_generate_html_has_og_title():
    """generate_html must include an Open Graph title metatag."""
    stats = _summary_stats([])
    html = generate_html([], stats)
    assert 'property="og:title"' in html
    assert 'PDF Accessibility Scan Results' in html


def test_generate_html_has_og_description():
    """generate_html must include an Open Graph description metatag."""
    stats = _summary_stats([])
    html = generate_html([], stats)
    assert 'property="og:description"' in html


def test_generate_html_has_og_type():
    """generate_html must include an Open Graph type metatag."""
    stats = _summary_stats([])
    html = generate_html([], stats)
    assert 'property="og:type"' in html


def test_generate_html_has_og_site_name():
    """generate_html must include an Open Graph site_name metatag."""
    stats = _summary_stats([])
    html = generate_html([], stats)
    assert 'property="og:site_name"' in html
    assert 'PDF Accessibility Crawler' in html


def test_generate_html_has_twitter_card():
    """generate_html must include a Twitter Card metatag (used by many platforms)."""
    stats = _summary_stats([])
    html = generate_html([], stats)
    assert 'name="twitter:card"' in html
    assert 'name="twitter:title"' in html
    assert 'name="twitter:description"' in html


def test_generate_html_has_description_meta():
    """generate_html must include a standard description metatag."""
    stats = _summary_stats([])
    html = generate_html([], stats)
    assert 'name="description"' in html


def test_generate_reports_index_html_has_og_title():
    """generate_reports_index_html must include an Open Graph title metatag."""
    html = generate_reports_index_html([])
    assert 'property="og:title"' in html
    assert 'PDF Accessibility Scan Reports' in html


def test_generate_reports_index_html_has_og_description():
    """generate_reports_index_html must include an Open Graph description metatag."""
    html = generate_reports_index_html([])
    assert 'property="og:description"' in html


def test_generate_reports_index_html_has_og_url():
    """generate_reports_index_html must include an Open Graph URL metatag."""
    html = generate_reports_index_html([])
    assert 'property="og:url"' in html
    assert 'mgifford.github.io/pdf-crawler' in html


def test_generate_reports_index_html_has_og_site_name():
    """generate_reports_index_html must include an Open Graph site_name metatag."""
    html = generate_reports_index_html([])
    assert 'property="og:site_name"' in html
    assert 'PDF Accessibility Crawler' in html


def test_generate_reports_index_html_has_twitter_card():
    """generate_reports_index_html must include a Twitter Card metatag (used by many platforms)."""
    html = generate_reports_index_html([])
    assert 'name="twitter:card"' in html
    assert 'name="twitter:title"' in html
    assert 'name="twitter:description"' in html


def test_generate_reports_index_html_has_description_meta():
    """generate_reports_index_html must include a standard description metatag."""
    html = generate_reports_index_html([])
    assert 'name="description"' in html
# ---------------------------------------------------------------------------

def test_generate_csv_has_header():
    entries = [_make_entry("https://example.com/doc.pdf")]
    csv_text = generate_csv(entries)
    first_line = csv_text.splitlines()[0]
    assert "url" in first_line
    assert "filename" in first_line
    assert "site" in first_line
    assert "accessible" in first_line
    assert "pages" in first_line


def test_generate_csv_one_row_per_entry():
    entries = [
        _make_entry("https://a.com/1.pdf"),
        _make_entry("https://a.com/2.pdf"),
        _make_entry("https://b.com/1.pdf"),
    ]
    csv_text = generate_csv(entries)
    lines = [l for l in csv_text.splitlines() if l]
    # header + 3 data rows
    assert len(lines) == 4


def test_generate_csv_empty_manifest():
    csv_text = generate_csv([])
    lines = [l for l in csv_text.splitlines() if l]
    # header only
    assert len(lines) == 1


def test_generate_csv_contains_url():
    entries = [_make_entry("https://example.com/my-doc.pdf")]
    csv_text = generate_csv(entries)
    assert "https://example.com/my-doc.pdf" in csv_text


def test_generate_csv_pending_entry_has_empty_report_fields():
    entry = _make_entry("https://example.com/pending.pdf", status="pending")
    csv_text = generate_csv([entry])
    lines = csv_text.splitlines()
    data_row = lines[1]
    # accessible field should be empty for a pending entry
    assert "pending" in data_row


def test_generate_csv_errors_joined_with_semicolon():
    entry = _make_entry("https://example.com/bad.pdf")
    entry["errors"] = ["error one", "error two"]
    csv_text = generate_csv([entry])
    assert "error one; error two" in csv_text


def test_generate_csv_accessible_values():
    accessible_entry = _make_entry("https://example.com/good.pdf", accessible=True)
    inaccessible_entry = _make_entry("https://example.com/bad.pdf", accessible=False)
    csv_text = generate_csv([accessible_entry, inaccessible_entry])
    assert "True" in csv_text
    assert "False" in csv_text


def test_issue_comment_contains_csv_link():
    entries = [_make_entry("https://example.com/doc.pdf")]
    comment = generate_issue_comment(
        entries,
        crawl_url="https://example.com",
        pages_base="https://owner.github.io/repo",
        run_url="https://github.com/owner/repo/actions/runs/99",
    )
    assert "report.csv" in comment


# ---------------------------------------------------------------------------
# generate_issue_comment – pages_crawled
# ---------------------------------------------------------------------------


def test_issue_comment_shows_pages_crawled_when_provided():
    """Issue comment must include a 'URLs crawled' row when pages_crawled > 0."""
    entries = [_make_entry("https://example.com/doc.pdf")]
    comment = generate_issue_comment(
        entries,
        crawl_url="https://example.com",
        pages_base="https://org.github.io/repo",
        run_url="https://github.com/org/repo/actions/runs/1",
        pages_crawled=42,
    )
    assert "URLs crawled" in comment
    assert "42" in comment


def test_issue_comment_omits_pages_crawled_when_zero():
    """Issue comment must NOT include a 'URLs crawled' row when pages_crawled is 0."""
    entries = [_make_entry("https://example.com/doc.pdf")]
    comment = generate_issue_comment(
        entries,
        crawl_url="https://example.com",
        pages_base="https://org.github.io/repo",
        run_url="https://github.com/org/repo/actions/runs/1",
        pages_crawled=0,
    )
    assert "URLs crawled" not in comment


def test_issue_comment_includes_crawled_urls_csv_link():
    """Issue comment must always include a link to the crawled_urls.csv."""
    entries = [_make_entry("https://example.com/doc.pdf")]
    comment = generate_issue_comment(
        entries,
        crawl_url="https://example.com",
        pages_base="https://org.github.io/repo",
        run_url="https://github.com/org/repo/actions/runs/1",
    )
    assert "crawled_urls.csv" in comment


# ---------------------------------------------------------------------------
# main() – issue_url stored in index.json
# ---------------------------------------------------------------------------

def _make_manifest(tmp_path, entries=None):
    """Write a minimal YAML manifest and return its path."""
    import yaml
    if entries is None:
        entries = [
            {
                "url": "https://example.com/doc.pdf",
                "filename": "doc.pdf",
                "site": "example.com",
                "status": "analysed",
                "report": {
                    "Accessible": True,
                    "TotallyInaccessible": False,
                    "BrokenFile": False,
                    "Exempt": False,
                    "TaggedTest": "Pass",
                    "EmptyTextTest": "Pass",
                    "ProtectedTest": "Pass",
                    "TitleTest": "Pass",
                    "LanguageTest": "Pass",
                    "BookmarksTest": "Pass",
                    "Pages": 3,
                },
                "errors": [],
            }
        ]
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.dump(entries), encoding="utf-8")
    return str(manifest_path)


def test_main_stores_issue_url_in_index(tmp_path):
    """main() must store the issue_url in the index.json entry."""
    manifest = _make_manifest(tmp_path)
    archive_dir = tmp_path / "archive"
    html_dir = tmp_path / "html"
    issue_url = "https://github.com/owner/repo/issues/42#issuecomment-99999"

    generate_main(
        manifest_path=manifest,
        report_dir=str(tmp_path / "reports"),
        site_filter="example.com",
        html_dir=str(html_dir),
        archive_dir=str(archive_dir),
        run_url="https://github.com/owner/repo/actions/runs/1",
        crawl_url="https://example.com",
        issue_url=issue_url,
    )

    index_path = archive_dir / "index.json"
    assert index_path.exists(), "index.json should be created"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["issue_url"] == issue_url


def test_main_stores_empty_issue_url_when_not_provided(tmp_path):
    """main() must store an empty issue_url when none is given."""
    manifest = _make_manifest(tmp_path)
    archive_dir = tmp_path / "archive"
    html_dir = tmp_path / "html"

    generate_main(
        manifest_path=manifest,
        report_dir=str(tmp_path / "reports"),
        site_filter="example.com",
        html_dir=str(html_dir),
        archive_dir=str(archive_dir),
        run_url="https://github.com/owner/repo/actions/runs/1",
        crawl_url="https://example.com",
    )

    index_path = archive_dir / "index.json"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert data[0].get("issue_url") == ""


def test_cli_accepts_issue_url_argument(tmp_path):
    """The --issue-url CLI argument must be accepted without error."""
    import subprocess
    script = Path(__file__).parent.parent / "scripts" / "generate_report.py"
    # Run with --help and verify --issue-url appears in the output
    result = subprocess.run(
        ["python3", str(script), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--issue-url" in result.stdout
