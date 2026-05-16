"""Structured PDF accessibility report builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

PASSED = "Passed"
FAILED = "Failed"
WARNING = "Warning"
SKIPPED = "Skipped"
NEEDS_MANUAL_CHECK = "Needs manual check"

REPORT_CATEGORIES = [
    "Document",
    "Page Content",
    "Forms",
    "Alternate Text",
    "Tables",
    "Lists",
    "Headings",
]


@dataclass(frozen=True)
class ReportRule:
    category: str
    rule: str
    description: str
    resolver: Callable[[dict, bool], dict]
    compatible_only: bool = False

    def to_report_item(
        self,
        result: dict,
        debug: bool = False,
        warnings_as_failures: bool = False,
    ) -> dict:
        outcome = self.resolver(result, debug)

        if warnings_as_failures and outcome["Status"] == WARNING:
            outcome = {
                **outcome,
                "Status": FAILED,
                "Severity": WARNING,
            }

        item = {
            "Rule": self.rule,
            "Status": outcome["Status"],
            "Description": self.description,
        }

        if debug:
            for key in ("Original status", "Severity", "Details", "Note"):
                value = outcome.get(key)
                if value not in (None, ""):
                    item[key] = value

        return item


def _clean_parts(*values: object) -> str:
    parts: list[str] = []
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            parts.append(text)
    return " | ".join(parts)


def _failed(*, original: str | None = None, details: str | None = None, severity: str | None = None, note: str | None = None) -> dict:
    outcome = {"Status": FAILED}
    if original is not None:
        outcome["Original status"] = original
    if severity is not None:
        outcome["Severity"] = severity
    if details:
        outcome["Details"] = details
    if note:
        outcome["Note"] = note
    return outcome


def _warning(*, original: str | None = None, details: str | None = None, note: str | None = None) -> dict:
    outcome = {"Status": WARNING}
    if original is not None:
        outcome["Original status"] = original
    if details:
        outcome["Details"] = details
    if note:
        outcome["Note"] = note
    return outcome


def _passed(*, original: str | None = None, details: str | None = None, note: str | None = None) -> dict:
    outcome = {"Status": PASSED}
    if original is not None:
        outcome["Original status"] = original
    if details:
        outcome["Details"] = details
    if note:
        outcome["Note"] = note
    return outcome


def _skipped(*, original: str | None = None, details: str | None = None, note: str | None = None) -> dict:
    outcome = {"Status": SKIPPED}
    if original is not None:
        outcome["Original status"] = original
    if details:
        outcome["Details"] = details
    if note:
        outcome["Note"] = note
    return outcome


def _needs_manual_check(note: str | None = None) -> dict:
    outcome = {"Status": NEEDS_MANUAL_CHECK}
    if note:
        outcome["Note"] = note
    return outcome


def _status_from_test(
    result: dict,
    field: str,
    *,
    not_applicable_status: str = PASSED,
    details_field: str | None = None,
) -> dict:
    raw = result.get(field)
    details = result.get(details_field) if details_field else None

    status_resolvers = {
        "Pass": _passed,
        "Fail": _failed,
        "Warn": _warning,
    }
    resolver = status_resolvers.get(raw)
    if resolver is not None:
        return resolver(original=raw, details=details)

    if raw == "NotApplicable":
        if not_applicable_status == PASSED:
            return _passed(original=raw, details=details)
        if not_applicable_status == FAILED:
            return _failed(original=raw, details=details)
        return _skipped(original=raw, details=details)

    return _skipped(original=raw, details=details)


def _direct_test(field: str, details_field: str | None = None):
    def resolver(result: dict, _debug: bool = False) -> dict:
        return _status_from_test(result, field, details_field=details_field)

    return resolver


def _manual_rule(_result: dict, _debug: bool = False) -> dict:
    return _needs_manual_check()


def _unsupported_rule(_result: dict, _debug: bool = False) -> dict:
    return _skipped(note="This rule is not checked by this scanner.")


def _unsupported_structure_rule(result: dict, _debug: bool = False) -> dict:
    if result.get("TaggedTest") == "Fail":
        return _failed(details="Document is not tagged; this structure-dependent rule cannot be verified.")
    return _skipped(note="This rule is not checked by this scanner.")


def _open_pdf_rule(result: dict, _debug: bool = False) -> dict:
    if result.get("BrokenFile"):
        return _failed(details=result.get("_log"))
    return _passed()


def _tagged_content_rule(result: dict, _debug: bool = False) -> dict:
    if result.get("TaggedTest") == "Fail":
        return _failed(details="Document is not tagged; page content cannot be verified as tagged.")

    raw = result.get("TaggedContentTest")
    if raw is None:
        return _skipped(note="Tagged content was not checked in this result.")

    if raw == "Warn":
        return _warning(
            original=raw,
            details=result.get("UntaggedWhitespaceContentSummary"),
            note=(
                "Only whitespace-only untagged text operations were found. "
                "This may still appear as untagged content in some checkers."
            ),
        )

    return _status_from_test(result, "TaggedContentTest", details_field="UntaggedContentSummary")


def _tagged_annotations_rule(result: dict, _debug: bool = False) -> dict:
    raw = result.get("TaggedAnnotationsTest")
    if raw == "NotApplicable":
        return _passed(original=raw)

    return _status_from_test(result, "TaggedAnnotationsTest", details_field="TaggedAnnotationIssues")


def _forms_tagged_fields_rule(result: dict, _debug: bool = False) -> dict:
    if not result.get("FormFieldCount"):
        return _passed(original=result.get("TaggedFormFieldsTest"))

    return _status_from_test(result, "TaggedFormFieldsTest", details_field="TaggedFormFieldIssues")


def _forms_field_descriptions_rule(result: dict, _debug: bool = False) -> dict:
    if not result.get("FormFieldCount"):
        return _passed(original=result.get("FormsTest"))

    return _status_from_test(result, "FormsTest", details_field="FieldsWithoutDescription")


def _structure_dependent_test(field: str, *, details_field: str | None = None):
    def resolver(result: dict, _debug: bool = False) -> dict:
        if result.get("TaggedTest") == "Fail":
            return _failed(details="Document is not tagged; this structure-dependent rule cannot be verified.")

        return _status_from_test(
            result,
            field,
            not_applicable_status=PASSED,
            details_field=details_field,
        )

    return resolver


def _figure_alt_rule(result: dict, debug: bool = False) -> dict:
    if "FiguresAltTextTest" in result and result.get("FiguresAltTextTest") is not None:
        return _structure_dependent_test(
            "FiguresAltTextTest",
            details_field="FiguresAltTextIssues",
        )(result, debug)

    return _structure_dependent_test(
        "ImageAltTextTest",
        details_field="FiguresAltTextIssues",
    )(result, debug)


def _table_rows_rule(result: dict, _debug: bool = False) -> dict:
    if result.get("TaggedTest") == "Fail":
        return _failed(details="Document is not tagged; table row structure cannot be verified.")

    details = result.get("InvalidTRParents")
    if details:
        return _failed(details=details)

    return _passed()


def _table_cells_rule(result: dict, _debug: bool = False) -> dict:
    if result.get("TaggedTest") == "Fail":
        return _failed(details="Document is not tagged; table cell structure cannot be verified.")

    details = result.get("InvalidCellParents")
    if details:
        return _failed(details=details)

    return _passed()


def _table_headers_rule(result: dict, _debug: bool = False) -> dict:
    if result.get("TaggedTest") == "Fail":
        return _failed(details="Document is not tagged; table headers cannot be verified.")

    details = result.get("TablesWithoutHeaders")
    if details:
        return _failed(details=details)

    return _passed()


def _table_regularity_rule(result: dict, _debug: bool = False) -> dict:
    if result.get("TaggedTest") == "Fail":
        return _failed(details="Document is not tagged; table regularity cannot be verified.")

    details = result.get("IrregularTables")
    if details:
        return _warning(
            original="Warn",
            details=details,
            note=(
                "Uneven row widths may indicate an irregular table, but this "
                "cannot always be determined reliably from the tag tree alone."
            ),
        )

    return _passed()


def _table_summary_rule(_result: dict, _debug: bool = False) -> dict:
    return _skipped(note="Table summary is not checked by this scanner.")


def _list_items_rule(result: dict, _debug: bool = False) -> dict:
    if result.get("TaggedTest") == "Fail":
        return _failed(details="Document is not tagged; list item structure cannot be verified.")

    malformed = result.get("MalformedListNodes") or ""
    l_child_failures = " | ".join(part for part in malformed.split(" | ") if "L has disallowed children" in part)

    details = _clean_parts(result.get("InvalidListItemParents"), l_child_failures)
    if details:
        return _failed(details=details)

    return _passed()


def _lbl_lbody_rule(result: dict, _debug: bool = False) -> dict:
    if result.get("TaggedTest") == "Fail":
        return _failed(details="Document is not tagged; list label/body structure cannot be verified.")

    malformed = result.get("MalformedListNodes") or ""
    non_l_container_malformed = " | ".join(part for part in malformed.split(" | ") if "L has disallowed children" not in part)

    details = _clean_parts(result.get("InvalidListChildren"), non_l_container_malformed)
    if details:
        if result.get("ListsTest") == "Warn":
            return _warning(original="Warn", details=details)
        return _failed(details=details)

    return _passed()


def _headings_rule(result: dict, _debug: bool = False) -> dict:
    if result.get("TaggedTest") == "Fail":
        return _failed(details="Document is not tagged; heading structure cannot be verified.")

    heading_status = result.get("HeadingsTest")
    heading_issues = result.get("HeadingIssues") or ""

    if heading_status == "Warn" and heading_issues == "No headings found in tagged document":
        return _passed(
            original=heading_status,
            details=heading_issues,
            note="No headings were found, so no heading nesting problem was detected.",
        )

    return _status_from_test(
        result,
        "HeadingsTest",
        not_applicable_status=PASSED,
        details_field="HeadingIssues",
    )


REPORT_RULES = [
    ReportRule("Document", "Open PDF", "Document can be opened and read", _open_pdf_rule, compatible_only=False),
    ReportRule("Document", "Accessibility permission flag", "Accessibility permission flag must be set", _direct_test("ProtectedTest")),
    ReportRule("Document", "Image-only PDF", "Document is not image-only PDF", _direct_test("EmptyTextTest")),
    ReportRule("Document", "Tagged PDF", "Document is tagged PDF", _direct_test("TaggedTest")),
    ReportRule("Document", "Logical Reading Order", "Document structure provides a logical reading order", _manual_rule, compatible_only=True),
    ReportRule("Document", "Primary language", "Text language is specified", _direct_test("LanguageTest")),
    ReportRule("Document", "Title", "Document title is showing in title bar", _direct_test("TitleTest")),
    ReportRule("Document", "Bookmarks", "Bookmarks are present in large documents", _direct_test("BookmarksTest")),
    ReportRule("Document", "Color contrast", "Document has appropriate color contrast", _manual_rule, compatible_only=True),
    ReportRule("Page Content", "Tagged content", "All page content is tagged", _tagged_content_rule),
    ReportRule("Page Content", "Tagged annotations", "All annotations are tagged", _tagged_annotations_rule),
    ReportRule("Page Content", "Tab order", "Tab order is consistent with structure order", _unsupported_structure_rule, compatible_only=True),
    ReportRule("Page Content", "Character encoding", "Reliable character encoding is provided", _unsupported_rule, compatible_only=True),
    ReportRule("Page Content", "Tagged multimedia", "All multimedia objects are tagged", _unsupported_rule, compatible_only=True),
    ReportRule("Page Content", "Screen flicker", "Page will not cause screen flicker", _unsupported_rule, compatible_only=True),
    ReportRule("Page Content", "Scripts", "No inaccessible scripts", _unsupported_rule, compatible_only=True),
    ReportRule("Page Content", "Timed responses", "Page does not require timed responses", _unsupported_rule, compatible_only=True),
    ReportRule("Page Content", "Navigation links", "Navigation links are not repetitive", _unsupported_rule, compatible_only=True),
    ReportRule("Forms", "Tagged form fields", "All form fields are tagged", _forms_tagged_fields_rule),
    ReportRule("Forms", "Field descriptions", "All form fields have description", _forms_field_descriptions_rule),
    ReportRule("Alternate Text", "Figures alternate text", "Figures require alternate text", _figure_alt_rule),
    ReportRule("Alternate Text", "Nested alternate text", "Alternate text that will never be read", _structure_dependent_test("NestedAltTextTest", details_field="NestedAltTextIssues")),
    ReportRule("Alternate Text", "Associated with content", "Alternate text must be associated with some content", _unsupported_structure_rule, compatible_only=True),
    ReportRule("Alternate Text", "Hides annotation", "Alternate text should not hide annotation", _structure_dependent_test("HidesAnnotationTest", details_field="HidesAnnotationIssues")),
    ReportRule("Alternate Text", "Other elements alternate text", "Other elements that require alternate text", _unsupported_structure_rule, compatible_only=True),
    ReportRule("Tables", "Rows", "TR must be a child of Table, THead, TBody, or TFoot", _table_rows_rule),
    ReportRule("Tables", "TH and TD", "TH and TD must be children of TR", _table_cells_rule),
    ReportRule("Tables", "Headers", "Tables should have headers", _table_headers_rule),
    ReportRule("Tables", "Regularity", "Tables must contain the same number of columns in each row and rows in each column", _table_regularity_rule),
    ReportRule("Tables", "Summary", "Tables must have a summary", _table_summary_rule, compatible_only=True),
    ReportRule("Lists", "List items", "LI must be a child of L", _list_items_rule),
    ReportRule("Lists", "Lbl and LBody", "Lbl and LBody must be children of LI", _lbl_lbody_rule),
    ReportRule("Headings", "Appropriate nesting", "Appropriate nesting", _headings_rule),
]


def _build_pdf_metadata(result: dict, debug: bool = False) -> dict:
    metadata = {
        "File": result.get("File"),
        "Site": result.get("Site"),
        "Accessible": result.get("Accessible"),
        "TotallyInaccessible": result.get("TotallyInaccessible"),
        "BrokenFile": result.get("BrokenFile"),
        "Exempt": result.get("Exempt"),
        "Date": result.get("Date"),
        "Pages": result.get("Pages"),
        "PDFVersion": result.get("PDFVersion"),
        "Creator": result.get("Creator"),
        "Producer": result.get("Producer"),
        "hasXmp": result.get("hasXmp"),
        "Form": result.get("Form"),
        "xfa": result.get("xfa"),
    }

    if debug:
        metadata["_log"] = result.get("_log")

    return metadata


def _build_summary(detailed_report: dict[str, list[dict]]) -> dict:
    counts = {
        "Needs manual check": 0,
        "Passed manually": 0,
        "Failed manually": 0,
        "Skipped": 0,
        "Passed": 0,
        "Warning": 0,
        "Failed": 0,
    }

    for rules in detailed_report.values():
        for rule in rules:
            status = rule["Status"]
            if status in counts:
                counts[status] += 1

    if counts["Failed"] > 0:
        description = "The checker found problems which may prevent the document from being fully accessible."
    elif counts["Warning"] > 0:
        description = "The checker found warnings which may require manual review."
    else:
        description = "The checker found no problems in this document."

    return {
        "Description": description,
        **counts,
    }


def build_json_report(
    result: dict,
    *,
    compatible: bool = False,
    debug: bool = False,
) -> dict:
    detailed_report: dict[str, list[dict]] = {category: [] for category in REPORT_CATEGORIES}

    for rule in REPORT_RULES:
        if rule.compatible_only and not compatible:
            continue

        if rule.rule == "Open PDF" and not result.get("BrokenFile"):
            continue

        detailed_report[rule.category].append(
            rule.to_report_item(
                result,
                debug=debug,
                warnings_as_failures=compatible,
            )
        )

    return {
        "Summary": _build_summary(detailed_report),
        "Detailed Report": detailed_report,
        "PDF Metadata": _build_pdf_metadata(result, debug=debug),
    }
