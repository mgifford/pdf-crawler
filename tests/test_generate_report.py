"""Tests for scripts/generate_report.py"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from generate_report import _summary_stats, generate_html, generate_markdown, generate_issue_comment


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
