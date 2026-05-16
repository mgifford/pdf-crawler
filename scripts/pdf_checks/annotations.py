from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import StructureItem
from .structure import as_kids, extract_role_map, normalize_struct_type, obj_get, safe_name

LINK_SUBTYPE = "/Link"
WIDGET_SUBTYPE = "/Widget"
LINK_STRUCT_TYPE = "Link"

LINK_COMPATIBLE_STRUCT_TYPES = {"Link", "Reference"}
MAX_TAGGED_ANNOTATION_ISSUES = 50


@dataclass
class AnnotationInfo:
    object_ref: str | None
    page_number: int
    subtype_raw: str | None
    subtype: str | None
    flags: int | None
    rect: list[float] | None
    action_type: str | None = None
    destination: str | None = None
    is_widget: bool = False
    field_name: str | None = None
    struct_parent: int | None = None
    obj: Any | None = field(default=None, repr=False)


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


def _normalize_rect(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        rect = [float(x) for x in value]
        if len(rect) == 4:
            return rect
    except Exception:
        pass
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _field_name_from_widget(widget: Any) -> str | None:
    direct_name = safe_name(obj_get(widget, "/T"))
    if direct_name:
        return direct_name

    parent = obj_get(widget, "/Parent")
    if parent is not None:
        parent_name = safe_name(obj_get(parent, "/T"))
        if parent_name:
            return parent_name

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


def _structure_element_has_objr_for_annotation(struct_elem: Any, annotation_obj: Any) -> bool:
    for kid in as_kids(obj_get(struct_elem, "/K")):
        if safe_name(obj_get(kid, "/Type")) != "/OBJR":
            continue

        obj = obj_get(kid, "/Obj")
        if _same_pdf_object(obj, annotation_obj):
            return True

    return False


def _link_annotation_tagging_issues(pdf, link_annotations: list[AnnotationInfo]) -> list[str]:
    issues: list[str] = []

    struct_tree_root = obj_get(pdf.Root, "/StructTreeRoot")
    parent_tree = obj_get(struct_tree_root, "/ParentTree") if struct_tree_root else None
    role_map = extract_role_map(pdf)

    for annot in link_annotations:
        ref = annot.object_ref or "unknown-annotation"
        destination = f" dest={annot.destination!r}" if annot.destination else ""
        prefix = f"{ref}: page={annot.page_number}{destination}"

        if annot.struct_parent is None:
            issues.append(f"{prefix}: link annotation has no /StructParent")
            continue

        if parent_tree is None:
            issues.append(f"{prefix}: document has no /ParentTree")
            continue

        parent_value = _lookup_number_tree_value(parent_tree, annot.struct_parent)
        if parent_value is None:
            issues.append(
                f"{prefix}: /StructParent {annot.struct_parent} not found in /ParentTree"
            )
            continue

        candidates = _parent_tree_candidates(parent_value)
        if not candidates:
            issues.append(
                f"{prefix}: /StructParent {annot.struct_parent} maps to no structure element"
            )
            continue

        mapped_types: list[str] = []
        compatible_candidates: list[Any] = []

        for candidate in candidates:
            mapped_type = normalize_struct_type(obj_get(candidate, "/S"), role_map)
            mapped_types.append(mapped_type or "Unknown")

            if mapped_type in LINK_COMPATIBLE_STRUCT_TYPES:
                compatible_candidates.append(candidate)

        if not compatible_candidates:
            issues.append(
                f"{prefix}: /StructParent {annot.struct_parent} maps to {', '.join(mapped_types)}, expected Link"
            )
            continue

        if annot.obj is None:
            issues.append(f"{prefix}: scanner could not verify raw annotation object")
            continue

        has_matching_objr = any(
            _structure_element_has_objr_for_annotation(candidate, annot.obj)
            for candidate in compatible_candidates
        )

        if not has_matching_objr:
            issues.append(
                f"{prefix}: /StructParent {annot.struct_parent} maps to {', '.join(mapped_types)}, but no OBJR child points back to annotation"
            )

    return issues


def _format_tagged_annotation_issues(issues: list[str]) -> str:
    summary = issues[:MAX_TAGGED_ANNOTATION_ISSUES]
    if len(issues) > MAX_TAGGED_ANNOTATION_ISSUES:
        summary.append(f"... {len(issues) - MAX_TAGGED_ANNOTATION_ISSUES} more")
    return " | ".join(summary)


def _destination_summary(annot: Any) -> tuple[str | None, str | None]:
    dest = obj_get(annot, "/Dest")
    if dest is not None:
        return "Dest", safe_name(dest)

    action = obj_get(annot, "/A")
    if action is None:
        return None, None

    action_type = safe_name(obj_get(action, "/S"))

    uri = obj_get(action, "/URI")
    if uri is not None:
        return action_type, safe_name(uri)

    action_dest = obj_get(action, "/D")
    if action_dest is not None:
        return action_type, safe_name(action_dest)

    return action_type, None


def iter_page_annotations(pdf) -> list[AnnotationInfo]:
    collected: list[AnnotationInfo] = []

    for page_index, page in enumerate(pdf.pages, start=1):
        annots = obj_get(page.obj, "/Annots")
        if annots is None:
            continue

        try:
            for annot in annots:
                subtype_raw = safe_name(obj_get(annot, "/Subtype"))
                subtype = subtype_raw[1:] if subtype_raw and subtype_raw.startswith("/") else subtype_raw

                flags = _int_or_none(obj_get(annot, "/F"))
                rect = _normalize_rect(obj_get(annot, "/Rect"))
                action_type, destination = _destination_summary(annot)

                is_widget = subtype_raw == WIDGET_SUBTYPE
                field_name = _field_name_from_widget(annot) if is_widget else None
                struct_parent = _int_or_none(obj_get(annot, "/StructParent"))

                collected.append(
                    AnnotationInfo(
                        object_ref=_object_ref(annot),
                        page_number=page_index,
                        subtype_raw=subtype_raw,
                        subtype=subtype,
                        flags=flags,
                        rect=rect,
                        action_type=action_type,
                        destination=destination,
                        is_widget=is_widget,
                        field_name=field_name,
                        struct_parent=struct_parent,
                        obj=annot,
                    )
                )
        except Exception:
            continue

    return collected


def check_annotations(pdf, structure_items: list[StructureItem], result: dict) -> None:
    result["AnnotationCount"] = 0
    result["AnnotationsFound"] = False
    result["AnnotationSubtypeCounts"] = ""
    result["LinkAnnotationCount"] = 0
    result["WidgetAnnotationCount"] = 0
    result["LinkStructureCount"] = 0
    result["ExternalLinkAnnotationCount"] = 0
    result["InternalLinkAnnotationCount"] = 0
    result["AnnotationPagesWithLinks"] = 0
    result["TaggedAnnotationsTest"] = "NotApplicable"
    result["TaggedAnnotationIssues"] = ""
    result["AnnotationSummary"] = ""

    annotations = iter_page_annotations(pdf)

    result["AnnotationCount"] = len(annotations)
    result["AnnotationsFound"] = len(annotations) > 0

    if not annotations:
        return

    subtype_counts: dict[str, int] = {}
    summaries: list[str] = []
    link_pages: set[int] = set()

    for annot in annotations:
        subtype_key = annot.subtype or "Unknown"
        subtype_counts[subtype_key] = subtype_counts.get(subtype_key, 0) + 1

        if annot.subtype_raw == LINK_SUBTYPE:
            result["LinkAnnotationCount"] += 1
            link_pages.add(annot.page_number)

            if annot.action_type == "/URI":
                result["ExternalLinkAnnotationCount"] += 1
            elif annot.action_type == "Dest" or annot.destination is not None:
                result["InternalLinkAnnotationCount"] += 1

        if annot.is_widget:
            result["WidgetAnnotationCount"] += 1

        summaries.append(
            f"{annot.object_ref or 'unknown'}: "
            f"page={annot.page_number} "
            f"subtype={annot.subtype or 'Unknown'} "
            f"flags={annot.flags!r} "
            f"rect={annot.rect!r} "
            f"action={annot.action_type!r} "
            f"dest={annot.destination!r} "
            f"widget={annot.is_widget} "
            f"field={annot.field_name!r} "
            f"struct_parent={annot.struct_parent!r}"
        )

    result["AnnotationSubtypeCounts"] = " | ".join(
        f"{key}={value}" for key, value in sorted(subtype_counts.items())
    )
    result["AnnotationSummary"] = " | ".join(summaries)
    result["AnnotationPagesWithLinks"] = len(link_pages)

    link_annotations = [a for a in annotations if a.subtype_raw == LINK_SUBTYPE]
    link_structs = [item for item in structure_items if item.normalized_type == LINK_STRUCT_TYPE]
    result["LinkStructureCount"] = len(link_structs)

    if not link_annotations:
        result["TaggedAnnotationsTest"] = "NotApplicable"
        return

    if result.get("TaggedTest") != "Pass":
        result["TaggedAnnotationsTest"] = "Fail"
        result["Accessible"] = False
        result["_log"] += "annotations-untagged, "
        return

    tagging_issues = _link_annotation_tagging_issues(pdf, link_annotations)
    result["TaggedAnnotationIssues"] = _format_tagged_annotation_issues(tagging_issues)

    if tagging_issues:
        result["TaggedAnnotationsTest"] = "Fail"
        result["Accessible"] = False
        result["_log"] += "annotations-tagging-fail, "
    else:
        result["TaggedAnnotationsTest"] = "Pass"
