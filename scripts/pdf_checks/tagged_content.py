"""Detection of untagged page and XObject content in tagged PDFs."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Number
from typing import Any

import pikepdf

from .structure import obj_get, safe_name

TEXT_SHOWING_OPERATORS = {"Tj", "TJ", "'", '"'}
MARKED_CONTENT_START_OPERATORS = {"BMC", "BDC"}
MARKED_CONTENT_END_OPERATOR = "EMC"
XOBJECT_PAINT_OPERATOR = "Do"
FORM_XOBJECT_SUBTYPE = "/Form"
IMAGE_XOBJECT_SUBTYPE = "/Image"

MAX_SUMMARY_ITEMS = 50
MAX_XOBJECT_DEPTH = 10


@dataclass(slots=True)
class UntaggedContentIssue:
    page_number: int
    operator: str
    text: str
    source: str = "page"
    whitespace_only: bool = False


@dataclass(frozen=True, slots=True)
class MarkedContentScope:
    tag: str | None
    mcid: int | None = None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _marked_content_scope(operator_name: str, operands: list[Any]) -> MarkedContentScope:
    tag = _marked_content_tag(operands)
    mcid = None
    if operator_name == "BDC" and len(operands) > 1:
        mcid = _int_or_none(obj_get(operands[1], "/MCID"))
    return MarkedContentScope(tag=tag, mcid=mcid)


def _is_inside_artifact_scope(stack: list[MarkedContentScope]) -> bool:
    return any(scope.tag == "Artifact" for scope in stack)


def _is_inside_structurally_tagged_scope(stack: list[MarkedContentScope]) -> bool:
    return any(scope.mcid is not None for scope in stack)


def _object_ref(obj: Any) -> str | None:
    try:
        return repr(obj.objgen)
    except Exception:
        return None


def _operator_name(operator: Any) -> str:
    return str(operator)


def _normalize_tag(value: Any) -> str | None:
    tag = safe_name(value)
    if tag is None:
        return None
    if tag.startswith("/"):
        tag = tag[1:]
    return tag


def _marked_content_tag(operands: list[Any]) -> str | None:
    if not operands:
        return None
    return _normalize_tag(operands[0])


def _extract_tj_array_text(value: Any) -> str:
    parts: list[str] = []
    try:
        for tj_element in value:
            if isinstance(tj_element, Number):
                continue
            text = safe_name(tj_element)
            if text:
                parts.append(text)
    except TypeError:
        text = safe_name(value)
        if text:
            parts.append(text)
    return "".join(parts)


def _extract_text_from_text_showing_operator(operator_name: str, operands: list[Any]) -> str:
    if not operands:
        return ""
    if operator_name == "TJ":
        return _extract_tj_array_text(operands[0])
    text = safe_name(operands[-1])
    return text or ""


def _has_meaningful_text(text: str) -> bool:
    return bool(text and text.strip())


def _has_any_text(text: str) -> bool:
    return text != ""


def _resolve_xobject(content: Any, operands: list[Any]) -> tuple[str | None, Any | None]:
    if not operands:
        return None, None

    xobject_name = operands[0]
    xobject_name_text = safe_name(xobject_name)

    resources = obj_get(content, "/Resources")
    if resources is None:
        return xobject_name_text, None

    xobjects = obj_get(resources, "/XObject")
    if xobjects is None:
        return xobject_name_text, None

    try:
        return xobject_name_text, xobjects.get(xobject_name)
    except Exception:
        pass

    if xobject_name_text:
        try:
            return xobject_name_text, xobjects.get(xobject_name_text)
        except Exception:
            pass

    return xobject_name_text, None


def _is_form_xobject(xobject: Any) -> bool:
    if xobject is None:
        return False
    return safe_name(obj_get(xobject, "/Subtype")) == FORM_XOBJECT_SUBTYPE


def _is_image_xobject(xobject: Any) -> bool:
    if xobject is None:
        return False
    return safe_name(obj_get(xobject, "/Subtype")) == IMAGE_XOBJECT_SUBTYPE


def _format_image_xobject_text(xobject_name: str | None, xobject: Any | None) -> str:
    parts = ["Image XObject"]
    if xobject_name:
        parts.append(xobject_name)
    xobject_ref = _object_ref(xobject)
    if xobject_ref:
        parts.append(xobject_ref)
    return " ".join(parts)


def _iter_untagged_content_in_content_stream(
    content: Any,
    *,
    page_number: int,
    marked_content_stack: list[MarkedContentScope],
    source: str,
    visited: set[str],
    depth: int = 0,
) -> list[UntaggedContentIssue]:
    issues: list[UntaggedContentIssue] = []

    if depth > MAX_XOBJECT_DEPTH:
        return issues

    object_ref = _object_ref(content)
    if object_ref:
        visited_key = f"{page_number}:{object_ref}:{source}"
        if visited_key in visited:
            return issues
        visited.add(visited_key)

    try:
        operations = pikepdf.parse_content_stream(content)
    except Exception:
        return issues

    stack = list(marked_content_stack)

    for operands, operator in operations:
        operator_name = _operator_name(operator)

        if operator_name in MARKED_CONTENT_START_OPERATORS:
            stack.append(_marked_content_scope(operator_name, operands))
            continue

        if operator_name == MARKED_CONTENT_END_OPERATOR:
            if stack:
                stack.pop()
            continue

        if operator_name in TEXT_SHOWING_OPERATORS:
            if _is_inside_artifact_scope(stack) or _is_inside_structurally_tagged_scope(stack):
                continue

            text = _extract_text_from_text_showing_operator(operator_name, operands)
            if _has_any_text(text):
                issues.append(
                    UntaggedContentIssue(
                        page_number=page_number,
                        operator=operator_name,
                        text=text,
                        source=source,
                        whitespace_only=not _has_meaningful_text(text),
                    )
                )
            continue

        if operator_name == XOBJECT_PAINT_OPERATOR:
            xobject_name, xobject = _resolve_xobject(content, operands)

            if _is_image_xobject(xobject):
                if _is_inside_artifact_scope(stack) or _is_inside_structurally_tagged_scope(stack):
                    continue

                issues.append(
                    UntaggedContentIssue(
                        page_number=page_number,
                        operator=operator_name,
                        text=_format_image_xobject_text(xobject_name, xobject),
                        source=source,
                        whitespace_only=False,
                    )
                )
                continue

            if not _is_form_xobject(xobject):
                continue

            xobject_ref = _object_ref(xobject)
            xobject_source = "xobject"
            if xobject_name:
                xobject_source += f" {xobject_name}"
            if xobject_ref:
                xobject_source += f" {xobject_ref}"

            issues.extend(
                _iter_untagged_content_in_content_stream(
                    xobject,
                    page_number=page_number,
                    marked_content_stack=stack,
                    source=xobject_source,
                    visited=visited,
                    depth=depth + 1,
                )
            )

    return issues


def iter_untagged_text_showing_operations(pdf) -> list[UntaggedContentIssue]:
    issues: list[UntaggedContentIssue] = []
    for page_number, page in enumerate(pdf.pages, start=1):
        issues.extend(
            _iter_untagged_content_in_content_stream(
                page,
                page_number=page_number,
                marked_content_stack=[],
                source="page",
                visited=set(),
            )
        )
    return issues


def _format_issue_summary(issues: list[UntaggedContentIssue]) -> str:
    summary_parts = [
        (
            f"page={issue.page_number} "
            f"source={issue.source} "
            f"op={issue.operator} "
            f"text={issue.text!r}"
        )
        for issue in issues[:MAX_SUMMARY_ITEMS]
    ]
    if len(issues) > MAX_SUMMARY_ITEMS:
        summary_parts.append(f"... {len(issues) - MAX_SUMMARY_ITEMS} more")
    return " | ".join(summary_parts)


def check_tagged_content(pdf, result: dict) -> None:
    result["TaggedContentTest"] = "NotApplicable"
    result["UntaggedContentCount"] = 0
    result["UntaggedContentSummary"] = ""
    result["UntaggedWhitespaceContentCount"] = 0
    result["UntaggedWhitespaceContentSummary"] = ""

    if result.get("TaggedTest") != "Pass":
        result["TaggedContentTest"] = "Fail"
        result["Accessible"] = False
        return

    issues = iter_untagged_text_showing_operations(pdf)

    meaningful_issues = [issue for issue in issues if not issue.whitespace_only]
    whitespace_issues = [issue for issue in issues if issue.whitespace_only]

    result["UntaggedContentCount"] = len(meaningful_issues)
    result["UntaggedWhitespaceContentCount"] = len(whitespace_issues)

    result["UntaggedContentSummary"] = _format_issue_summary(meaningful_issues)
    result["UntaggedWhitespaceContentSummary"] = _format_issue_summary(whitespace_issues)

    if meaningful_issues:
        result["TaggedContentTest"] = "Fail"
        result["Accessible"] = False
        result["_log"] += "tagged-content-fail, "
    elif whitespace_issues:
        result["TaggedContentTest"] = "Warn"
        result["_log"] += "tagged-content-whitespace-warn, "
    else:
        result["TaggedContentTest"] = "Pass"
