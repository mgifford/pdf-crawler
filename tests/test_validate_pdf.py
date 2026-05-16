"""Tests for scripts/validate_pdf.py."""

import json
import subprocess
import sys
from pathlib import Path

import pikepdf


REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "validate_pdf.py"


def _make_pdf(path: Path) -> None:
    pdf = pikepdf.Pdf.new()
    page = pikepdf.Page(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=[0, 0, 612, 792],
        )
    )
    pdf.pages.append(page)
    pdf.save(str(path))


def test_validate_pdf_structured_mode(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    _make_pdf(pdf_path)

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(pdf_path), "--mode", "structured"],
        cwd=str(REPO),
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(proc.stdout)
    assert "Summary" in payload
    assert "Detailed Report" in payload


def test_validate_pdf_raw_mode(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    _make_pdf(pdf_path)

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(pdf_path), "--mode", "raw"],
        cwd=str(REPO),
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(proc.stdout)
    assert "TaggedTest" in payload
    assert "Accessible" in payload
