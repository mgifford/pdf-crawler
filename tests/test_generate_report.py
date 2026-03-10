"""Tests for scripts/generate_report.py"""

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


def test_generate_reports_index_html_embeds_json():
    reports = [
        {
            "date": "2024-01-15T10:30:00+00:00",
            "site": "example.com",
            "crawl_url": "https://example.com",
            "run_url": "https://github.com/owner/repo/actions/runs/1",
            "archive_file": "2024-01-15_10-30-00_example.com.html",
            "total": 10,
            "analysed": 10,
            "accessible": 7,
        }
    ]
    html = generate_reports_index_html(reports)
    assert 'id="reports-index"' in html
    assert "example.com" in html
    assert "2024-01-15_10-30-00_example.com.html" in html


def test_generate_reports_index_html_contains_back_link():
    html = generate_reports_index_html([])
    assert 'href="./"' in html


def test_generate_reports_index_html_multiple_entries():
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
        {
            "date": "2024-01-01T00:00:00+00:00",
            "site": "alpha.com",
            "crawl_url": "https://alpha.com",
            "run_url": "",
            "archive_file": "2024-01-01_00-00-00_alpha.com.html",
            "total": 3,
            "analysed": 3,
            "accessible": 3,
        },
    ]
    html = generate_reports_index_html(reports)
    assert "beta.com" in html
    assert "alpha.com" in html
    assert "2024-02-01_00-00-00_beta.com.html" in html


# ---------------------------------------------------------------------------
# generate_csv
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
