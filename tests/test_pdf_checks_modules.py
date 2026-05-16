"""Unit tests for scripts/pdf_checks modules."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from pdf_checks.alt_text import check_hides_annotation, check_nested_alt_text
from pdf_checks.headings import check_headings
from pdf_checks.lists import check_lists
from pdf_checks.models import StructureItem
from pdf_checks.tables import check_tables
from pdf_checks.tagged_content import check_tagged_content


def _base_result():
    return {"TaggedTest": "Pass", "Accessible": True, "_log": ""}


def test_check_tagged_content_fails_when_untagged():
    result = {"TaggedTest": "Fail", "Accessible": True, "_log": ""}
    check_tagged_content(pdf=None, result=result)
    assert result["TaggedContentTest"] == "Fail"


def test_check_headings_warns_for_no_headings():
    result = _base_result()
    check_headings([], result)
    assert result["HeadingsTest"] == "Warn"


def test_check_lists_not_applicable_when_no_lists():
    result = _base_result()
    check_lists([], result)
    assert result["ListsTest"] == "NotApplicable"


def test_check_tables_not_applicable_when_no_tables():
    result = _base_result()
    check_tables([], result)
    assert result["TablesTest"] == "NotApplicable"


def test_check_nested_alt_text_detects_nested_alt():
    root = StructureItem(
        type="Figure",
        normalized_type="Figure",
        depth=0,
        title=None,
        alt="root alt",
        kids_count=1,
    )
    child = StructureItem(
        type="Figure",
        normalized_type="Figure",
        depth=1,
        title=None,
        alt="child alt",
        kids_count=0,
    )
    result = _base_result()
    check_nested_alt_text([root, child], result)
    assert result["NestedAltTextTest"] == "Fail"


def test_check_hides_annotation_warns_on_alt_form_with_objr():
    form = StructureItem(
        type="Form",
        normalized_type="Form",
        depth=0,
        title=None,
        alt="desc",
        kids_count=1,
        has_objr=True,
        objr_count=1,
    )
    result = _base_result()
    check_hides_annotation([form], result)
    assert result["HidesAnnotationTest"] == "Warn"
