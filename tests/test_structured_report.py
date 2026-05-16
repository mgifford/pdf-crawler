"""Tests for scripts/structured_report.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from structured_report import build_json_report


def _base_result():
    return {
        "File": "doc.pdf",
        "Site": "example.com",
        "Accessible": True,
        "TotallyInaccessible": False,
        "BrokenFile": False,
        "Exempt": False,
        "Date": "2024-01-01 00:00:00+00:00",
        "Pages": 3,
        "PDFVersion": "1.7",
        "Creator": "Test",
        "Producer": "Test",
        "hasXmp": True,
        "Form": False,
        "xfa": False,
        "TaggedTest": "Pass",
        "TaggedContentTest": "Pass",
        "ProtectedTest": "Pass",
        "EmptyTextTest": "Pass",
        "LanguageTest": "Pass",
        "TitleTest": "Pass",
        "BookmarksTest": "Pass",
        "ImageAltTextTest": "Pass",
        "NestedAltTextTest": "Pass",
        "HidesAnnotationTest": "Pass",
        "TablesTest": "Pass",
        "ListsTest": "Pass",
        "HeadingsTest": "Pass",
        "TaggedAnnotationsTest": "NotApplicable",
        "FormsTest": "NotApplicable",
        "TaggedFormFieldsTest": "NotApplicable",
    }


def test_build_json_report_shape():
    report = build_json_report(_base_result())
    assert "Summary" in report
    assert "Detailed Report" in report
    assert "PDF Metadata" in report


def test_build_json_report_compatible_mode_includes_manual_rules():
    normal = build_json_report(_base_result(), compatible=False)
    compatible = build_json_report(_base_result(), compatible=True)

    normal_count = sum(len(v) for v in normal["Detailed Report"].values())
    compat_count = sum(len(v) for v in compatible["Detailed Report"].values())

    assert compat_count > normal_count


def test_build_json_report_fallback_uses_image_alt_text_test():
    result = _base_result()
    result.pop("ImageAltTextTest", None)
    result["FiguresAltTextTest"] = "Fail"
    result["FiguresAltTextIssues"] = "missing alt"

    report = build_json_report(result)
    alt_rules = report["Detailed Report"]["Alternate Text"]
    figures_rule = next(r for r in alt_rules if r["Rule"] == "Figures alternate text")
    assert figures_rule["Status"] == "Failed"


def test_build_json_report_open_pdf_only_for_broken_files():
    ok = build_json_report(_base_result())
    doc_rules_ok = [r["Rule"] for r in ok["Detailed Report"]["Document"]]
    assert "Open PDF" not in doc_rules_ok

    broken = _base_result()
    broken["BrokenFile"] = True
    broken["_log"] = "PdfError"
    broken_report = build_json_report(broken)
    doc_rules_broken = [r["Rule"] for r in broken_report["Detailed Report"]["Document"]]
    assert "Open PDF" in doc_rules_broken
