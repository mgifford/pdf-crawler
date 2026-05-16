from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from .models import StructureItem
from .structure import as_kids, extract_role_map, normalize_struct_type, obj_get, safe_name

FORM_STRUCT_TYPE = "Form"
WIDGET_SUBTYPE = "/Widget"

FIELD_TYPE_MAP = {
    "/Btn": "button",
    "/Tx": "text",
    "/Ch": "choice",
    "/Sig": "signature",
}

MAX_TAGGED_FORM_FIELD_ISSUES = 50


@dataclass
class FormFieldInfo:
    object_ref: str | None
    field_type_raw: str | None
    field_type: str | None
    field_name: str | None
    description: str | None
    description_source: str | None
    widget_count: int = 0
    page_refs: list[str] = field(default_factory=list)
    widgets: list[Any] = field(default_factory=list, repr=False)


def _object_ref(obj: Any) -> str | None:
    try:
        return repr(obj.objgen)
    except Exception:
        return None


def _object_key(obj: Any) -> tuple[int, int] | None:
    try:
        return tuple(obj.objgen)
    except Exception:
        return None


def _same_pdf_object(left: Any, right: Any) -> bool:
    left_key = _object_key(left)
    right_key = _object_key(right)

    if left_key is not None and right_key is not None:
        return left_key == right_key

    return left is right


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _lookup_number_tree_value(node: Any, key: int) -> Any | None:
    nums = obj_get(node, "/Nums")

    if nums is not None:
        try:
            for index in range(0, len(nums), 2):
                if int(nums[index]) == key:
                    return nums[index + 1]
        except Exception:
            pass

    kids = obj_get(node, "/Kids")

    if kids is not None:
        try:
            for kid in kids:
                limits = obj_get(kid, "/Limits")

                if limits is not None:
                    try:
                        lower = int(limits[0])
                        upper = int(limits[1])
                        if key < lower or key > upper:
                            continue
                    except Exception:
                        pass

                value = _lookup_number_tree_value(kid, key)
                if value is not None:
                    return value
        except Exception:
            pass

    return None


def _parent_tree_candidates(value: Any) -> list[Any]:
    candidates: list[Any] = []
    for item in as_kids(value):
        if obj_get(item, "/S") is not None or obj_get(item, "/K") is not None:
            candidates.append(item)
    return candidates


def _structure_element_has_objr_for_widget(struct_elem: Any, widget_obj: Any) -> bool:
    for kid in as_kids(obj_get(struct_elem, "/K")):
        if safe_name(obj_get(kid, "/Type")) != "/OBJR":
            continue

        obj = obj_get(kid, "/Obj")
        if _same_pdf_object(obj, widget_obj):
            return True

    return False


def _format_tagged_form_field_issues(issues: list[str]) -> str:
    summary = issues[:MAX_TAGGED_FORM_FIELD_ISSUES]
    if len(issues) > MAX_TAGGED_FORM_FIELD_ISSUES:
        summary.append(f"... {len(issues) - MAX_TAGGED_FORM_FIELD_ISSUES} more")
    return " | ".join(summary)


def _form_widget_tagging_issues(pdf, fields: list[FormFieldInfo]) -> list[str]:
    issues: list[str] = []

    struct_tree_root = obj_get(pdf.Root, "/StructTreeRoot")
    parent_tree = obj_get(struct_tree_root, "/ParentTree") if struct_tree_root else None
    role_map = extract_role_map(pdf)

    for field in fields:
        field_label = f"field={field.field_name!r}" if field.field_name else "field=unknown"

        for widget in field.widgets:
            ref = _object_ref(widget) or field.object_ref or "unknown-widget"
            prefix = f"{ref}: {field_label}"

            struct_parent = _int_or_none(obj_get(widget, "/StructParent"))

            if struct_parent is None:
                issues.append(f"{prefix}: widget annotation has no /StructParent")
                continue

            if parent_tree is None:
                issues.append(f"{prefix}: document has no /ParentTree")
                continue

            parent_value = _lookup_number_tree_value(parent_tree, struct_parent)
            if parent_value is None:
                issues.append(f"{prefix}: /StructParent {struct_parent} not found in /ParentTree")
                continue

            candidates = _parent_tree_candidates(parent_value)
            if not candidates:
                issues.append(f"{prefix}: /StructParent {struct_parent} maps to no structure element")
                continue

            mapped_types: list[str] = []
            compatible_candidates: list[Any] = []

            for candidate in candidates:
                mapped_type = normalize_struct_type(obj_get(candidate, "/S"), role_map)
                mapped_types.append(mapped_type or "Unknown")

                if mapped_type == FORM_STRUCT_TYPE:
                    compatible_candidates.append(candidate)

            if not compatible_candidates:
                issues.append(
                    f"{prefix}: /StructParent {struct_parent} maps to {', '.join(mapped_types)}, expected Form"
                )
                continue

            has_matching_objr = any(
                _structure_element_has_objr_for_widget(candidate, widget)
                for candidate in compatible_candidates
            )

            if not has_matching_objr:
                issues.append(
                    f"{prefix}: /StructParent {struct_parent} maps to {', '.join(mapped_types)}, but no OBJR child points back to widget"
                )

    return issues


def _normalize_field_type(value: Any) -> str | None:
    raw = safe_name(value)
    if raw is None:
        return None
    return FIELD_TYPE_MAP.get(raw, raw)


def _non_empty_text(value: Any) -> str | None:
    text = safe_name(value)
    if text is None:
        return None
    text = text.strip()
    return text or None


def _description_from_obj(obj: Any) -> tuple[str | None, str | None]:
    for key, source in (("/TU", "tooltip"), ("/TM", "mapping-name")):
        value = _non_empty_text(obj_get(obj, key))
        if value:
            return value, source
    return None, None


def _field_description(field: Any, widgets: list[Any]) -> tuple[str | None, str | None]:
    value, source = _description_from_obj(field)
    if value:
        return value, f"field-{source}"

    for widget in widgets:
        value, source = _description_from_obj(widget)
        if value:
            return value, f"widget-{source}"

    return None, None


def _collect_widget_annotations(field: Any) -> list[Any]:
    widgets: list[Any] = []

    subtype = safe_name(obj_get(field, "/Subtype"))
    if subtype == WIDGET_SUBTYPE:
        widgets.append(field)

    kids = obj_get(field, "/Kids")
    if kids is not None:
        try:
            for kid in kids:
                kid_subtype = safe_name(obj_get(kid, "/Subtype"))
                if kid_subtype == WIDGET_SUBTYPE:
                    widgets.append(kid)
        except Exception:
            pass

    return widgets


def _build_widget_page_map(pdf) -> dict[tuple[int, int], str]:
    widget_page_map: dict[tuple[int, int], str] = {}

    for page_index, page in enumerate(pdf.pages, start=1):
        annots = obj_get(page.obj, "/Annots")
        if annots is None:
            continue

        try:
            for annot in annots:
                if safe_name(obj_get(annot, "/Subtype")) != WIDGET_SUBTYPE:
                    continue

                key = _object_key(annot)
                if key is None:
                    continue

                page_ref = _object_ref(page.obj) or f"page={page_index}"
                widget_page_map[key] = page_ref
        except Exception:
            continue

    return widget_page_map


def _page_ref_from_widget(widget: Any) -> str | None:
    page = obj_get(widget, "/P")
    if page is None:
        return None
    return _object_ref(page)


def _page_refs_from_widget(widget: Any, widget_page_map: dict[tuple[int, int], str]) -> list[str]:
    page_refs: list[str] = []

    direct_page_ref = _page_ref_from_widget(widget)
    if direct_page_ref:
        page_refs.append(direct_page_ref)

    widget_key = _object_key(widget)
    if widget_key is not None:
        inferred_page_ref = widget_page_map.get(widget_key)
        if inferred_page_ref and inferred_page_ref not in page_refs:
            page_refs.append(inferred_page_ref)

    return page_refs


def iter_form_fields(pdf) -> list[FormFieldInfo]:
    acro = pdf.Root.get("/AcroForm")
    if acro is None:
        return []

    try:
        fields = acro.get("/Fields")
    except Exception:
        return []

    if not fields:
        return []

    widget_page_map = _build_widget_page_map(pdf)

    collected: list[FormFieldInfo] = []

    try:
        for field in fields:
            field_type_raw = safe_name(obj_get(field, "/FT"))
            field_type = _normalize_field_type(obj_get(field, "/FT"))
            field_name = _non_empty_text(obj_get(field, "/T"))

            widgets = _collect_widget_annotations(field)
            description, description_source = _field_description(field, widgets)

            page_refs: list[str] = []
            for widget in widgets:
                for page_ref in _page_refs_from_widget(widget, widget_page_map):
                    if page_ref and page_ref not in page_refs:
                        page_refs.append(page_ref)

            collected.append(
                FormFieldInfo(
                    object_ref=_object_ref(field),
                    field_type_raw=field_type_raw,
                    field_type=field_type,
                    field_name=field_name,
                    description=description,
                    description_source=description_source,
                    widget_count=len(widgets),
                    page_refs=page_refs,
                    widgets=widgets,
                )
            )
    except Exception:
        return collected

    return collected


def check_forms(pdf, result: dict) -> None:
    acro = pdf.Root.get("/AcroForm")
    if acro is None:
        return

    try:
        xfa = acro.get("/XFA")
        config_pos = -1
        found = False
        if xfa is not None:
            try:
                for n in range(0, len(xfa) - 1):
                    if xfa[n] == "config":
                        config_pos = n + 1
                        found = True
                        break
                if found and xfa[config_pos] is not None:
                    xml_str = xfa[config_pos].read_bytes().decode()
                    document = ET.fromstring(xml_str)
                    for d in document.iter():
                        if re.match(r".*dynamicRender", d.tag):
                            if d.text == "required":
                                result["xfa"] = True
                                result["_log"] += "xfa, "
            except TypeError:
                result["_log"] += "malformed xfa, "
    except ValueError:
        result["_log"] += "malformed xfa, "

    try:
        fields = acro.get("/Fields")
        if fields is not None and len(fields) != 0:
            result["Form"] = True
            result["Exempt"] = False
    except ValueError:
        result["_log"] += "malformed Form fields, "


def check_form_fields(pdf, structure_items: list[StructureItem], result: dict) -> None:
    result["FormFieldCount"] = 0
    result["FieldsWithoutDescription"] = ""
    result["TaggedFormFieldsTest"] = "NotApplicable"
    result["TaggedFormFieldIssues"] = ""

    fields = iter_form_fields(pdf)
    result["FormFieldCount"] = len(fields)

    if not fields:
        result["FormsTest"] = "NotApplicable"
        result["TaggedFormFieldsTest"] = "NotApplicable"
        return

    missing_descriptions: list[str] = []
    unclear_associations: list[str] = []
    summaries: list[str] = []

    for field in fields:
        ref = field.object_ref or field.field_name or "unknown-field"

        if not field.description:
            missing_descriptions.append(f"{ref}: missing description")

        if field.widget_count == 0:
            unclear_associations.append(f"{ref}: no widget annotation found")
        elif not field.page_refs:
            unclear_associations.append(f"{ref}: widget annotation has no page association")

        widget_struct_parents = [
            _int_or_none(obj_get(widget, "/StructParent")) for widget in field.widgets
        ]

        summaries.append(
            f"{ref}: "
            f"type={field.field_type or 'unknown'} "
            f"name={field.field_name!r} "
            f"desc={field.description!r} "
            f"desc_source={field.description_source or 'none'} "
            f"widgets={field.widget_count} "
            f"pages={field.page_refs} "
            f"struct_parents={widget_struct_parents}"
        )

    result["FieldsWithoutDescription"] = " | ".join(missing_descriptions)
    result["TaggedFormFieldIssues"] = " | ".join(unclear_associations)
    result["FormFieldSummary"] = " | ".join(summaries)

    if missing_descriptions:
        result["FormsTest"] = "Fail"
        result["Accessible"] = False
        result["_log"] += "forms-fail, "
    else:
        result["FormsTest"] = "Pass"

    if result.get("TaggedTest") != "Pass":
        tagged_field_issues = unclear_associations + [
            "document is not tagged, so form field structure cannot be verified"
        ]
        result["TaggedFormFieldIssues"] = _format_tagged_form_field_issues(tagged_field_issues)
        result["TaggedFormFieldsTest"] = "Fail"
        result["Accessible"] = False
        result["_log"] += "forms-untagged, "
        return

    form_structs = [
        item for item in structure_items if item.normalized_type == FORM_STRUCT_TYPE
    ]

    tagging_issues = _form_widget_tagging_issues(pdf, fields)

    structural_context_issues: list[str] = []
    if not form_structs and (unclear_associations or tagging_issues):
        structural_context_issues.append(
            "document has interactive form fields but no Form structure elements"
        )

    tagged_field_issues = unclear_associations + structural_context_issues + tagging_issues

    result["TaggedFormFieldIssues"] = _format_tagged_form_field_issues(tagged_field_issues)

    if tagged_field_issues:
        result["TaggedFormFieldsTest"] = "Fail"
        result["Accessible"] = False
        result["_log"] += "forms-tagging-fail, "
    else:
        result["TaggedFormFieldsTest"] = "Pass"
