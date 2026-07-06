"""Compatibility tests for legacy simplA11y-style output fields."""

from pathlib import Path
import sys

import pikepdf

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def _make_minimal_pdf(path: Path) -> None:
    pdf = pikepdf.Pdf.new()
    page = pikepdf.Page(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=[0, 0, 612, 792],
        )
    )
    pdf.pages.append(page)
    pdf.save(str(path))


def test_check_file_includes_legacy_identity_fields(tmp_path):
    """check_file() should include legacy Site/File fields for CSV compatibility."""
    from pdf_analyser import check_file

    p = tmp_path / "legacy.pdf"
    _make_minimal_pdf(p)

    result = check_file(str(p), site="example.com")

    assert result["Site"] == "example.com"
    assert result["File"] == "legacy.pdf"


def test_check_file_includes_legacy_text_metrics_fields(tmp_path):
    """check_file() should include legacy fonts/numTxtObjects keys."""
    from pdf_analyser import check_file

    p = tmp_path / "metrics.pdf"
    _make_minimal_pdf(p)

    result = check_file(str(p), site="example.com")

    assert "fonts" in result
    assert "numTxtObjects" in result
    # For an empty single-page PDF these are expected to be numeric counters.
    assert isinstance(result["fonts"], int)
    assert isinstance(result["numTxtObjects"], int)
