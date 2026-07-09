"""Regression checks for crawl workflow parameter parsing and metadata export.

These tests intentionally validate critical shell snippets in
`.github/workflows/crawl.yml` so accidental edits do not silently break:
- issue-body `Number:` / `PDFs:` parsing
- default crawl limits for issue-triggered scans
- crawler version propagation into scan metadata
"""

from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
CRAWL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "crawl.yml"


def _workflow_text() -> str:
    return CRAWL_WORKFLOW.read_text(encoding="utf-8")


def test_issue_body_parses_number_limit():
    """Issue-body `Number:` lines must be parsed into MAX_PAGES."""
    content = _workflow_text()
    assert "grep -Ei '^Number:[[:space:]]*[0-9]+'" in content
    assert "MAX_PAGES=\"2500\"" in content


def test_issue_body_parses_pdfs_limit():
    """Issue-body `PDFs:` lines must be parsed into MAX_PDFS."""
    content = _workflow_text()
    assert "grep -Ei '^PDFs:[[:space:]]*[0-9]+'" in content
    assert "MAX_PDFS=\"200\"" in content


def test_crawler_version_written_to_scan_metadata():
    """Crawler version output must be persisted for the analysis workflow."""
    content = _workflow_text()
    assert "CRAWLER_VERSION: ${{ steps.params.outputs.crawler_version }}" in content
    assert 'echo "$CRAWLER_VERSION" > scan-meta/crawler_version.txt' in content


def test_scan_language_written_to_scan_metadata():
    """Scan language output must be persisted for the analysis workflow."""
    content = _workflow_text()
    assert "scan_language=" in content
    assert "CRAWL_LANGUAGE:  ${{ steps.params.outputs.scan_language }}" in content
    assert 'echo "$CRAWL_LANGUAGE"  > scan-meta/language.txt' in content
