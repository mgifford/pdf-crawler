from __future__ import annotations

from typing import Any

import pikepdf

from .models import StructureItem


def safe_name(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return str(obj)
    except Exception:
        return repr(obj)


def obj_get(obj: Any, key: str, default: Any = None) -> Any:
    try:
        return obj.get(key, default)
    except Exception:
        return default


def as_kids(value: Any) -> list[Any]:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    try:
        if (
            obj_get(value, "/S") is not None
            or obj_get(value, "/K") is not None
            or obj_get(value, "/Type") is not None
        ):
            return [value]
    except Exception:
        pass

    try:
        items = list(value)
        if items and all(not hasattr(item, "keys") for item in items):
            return [value]
        return items
    except Exception:
        return [value]


def as_attribute_list(value: Any) -> list[Any]:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    try:
        items = list(value)
        if items and all(hasattr(item, "keys") for item in items):
            return items
    except Exception:
        pass

    return [value]


def get_object_ref(obj: Any) -> str | None:
    try:
        return repr(obj.objgen)
    except Exception:
        return None


def extract_mcids(node: Any) -> list[int]:
    mcids: list[int] = []
    for kid in as_kids(obj_get(node, "/K")):
        try:
            if isinstance(kid, int):
                mcids.append(kid)
                continue
        except Exception:
            pass

        if safe_name(obj_get(kid, "/Type")) == "/MCR":
            mcid = obj_get(kid, "/MCID")
            try:
                mcids.append(int(mcid))
            except Exception:
                pass

    return mcids


def extract_page_ref(node: Any, inherited_page_ref: str | None = None) -> str | None:
    page = obj_get(node, "/Pg")
    if page is not None:
        return get_object_ref(page)

    for kid in as_kids(obj_get(node, "/K")):
        if safe_name(obj_get(kid, "/Type")) != "/MCR":
            continue

        page = obj_get(kid, "/Pg")
        if page is not None:
            return get_object_ref(page)

    return inherited_page_ref


def extract_structure_attributes(node: Any) -> dict[str, object]:
    attributes: dict[str, object] = {}

    for attr_obj in as_attribute_list(obj_get(node, "/A")):
        try:
            if not hasattr(attr_obj, "keys"):
                continue

            row_span = obj_get(attr_obj, "/RowSpan")
            col_span = obj_get(attr_obj, "/ColSpan")

            if row_span is not None:
                try:
                    attributes["row_span"] = max(1, int(row_span))
                except Exception:
                    pass

            if col_span is not None:
                try:
                    attributes["col_span"] = max(1, int(col_span))
                except Exception:
                    pass

        except Exception:
            continue

    return attributes


def extract_role_map(pdf: pikepdf.Pdf) -> dict[str, str]:
    mapping: dict[str, str] = {}
    struct_tree_root = obj_get(pdf.Root, "/StructTreeRoot")
    if not struct_tree_root:
        return mapping

    role_map = obj_get(struct_tree_root, "/RoleMap")
    if not role_map:
        return mapping

    try:
        for key, value in role_map.items():
            key_str = safe_name(key)
            value_str = safe_name(value)
            if not key_str or not value_str:
                continue

            if key_str.startswith("/"):
                key_str = key_str[1:]
            if value_str.startswith("/"):
                value_str = value_str[1:]

            mapping[key_str] = value_str
    except Exception:
        pass

    return mapping


def extract_kid_object_info(node: Any) -> tuple[bool, int, list[str]]:
    has_objr = False
    objr_count = 0
    kid_object_types: list[str] = []

    for kid in as_kids(obj_get(node, "/K")):
        try:
            kid_type = safe_name(obj_get(kid, "/Type"))
        except Exception:
            kid_type = None

        if kid_type:
            kid_object_types.append(kid_type[1:] if kid_type.startswith("/") else kid_type)

        if kid_type == "/OBJR":
            has_objr = True
            objr_count += 1

    return has_objr, objr_count, kid_object_types


def normalize_struct_type(raw_type: Any, role_map: dict[str, str]) -> str | None:
    value = safe_name(raw_type)
    if not value:
        return None

    if value.startswith("/"):
        value = value[1:]

    return role_map.get(value, value)


def find_alt_text(struct_elem: Any) -> tuple[str | None, str | None, bool, bool]:
    alt_value = obj_get(struct_elem, "/Alt")
    actual_text_value = obj_get(struct_elem, "/ActualText")

    has_alt_entry = alt_value is not None
    has_actual_text_entry = actual_text_value is not None

    for value, source in ((alt_value, "/Alt"), (actual_text_value, "/ActualText")):
        if value is None:
            continue

        text = safe_name(value)
        if text and text.strip():
            return text.strip(), source, has_alt_entry, has_actual_text_entry

    return None, None, has_alt_entry, has_actual_text_entry


def iter_structure_elements(node: Any) -> list[Any]:
    kids = obj_get(node, "/K")
    if kids is None:
        return []

    results: list[Any] = []

    for item in as_kids(kids):
        try:
            if isinstance(item, int):
                continue
        except Exception:
            pass

        try:
            item_type = obj_get(item, "/S")
            item_kids = obj_get(item, "/K")
            if item_type is not None or item_kids is not None:
                results.append(item)
        except Exception:
            continue

    return results


def build_structure_item(
    node: Any,
    role_map: dict[str, str],
    depth: int,
    parent_type: str | None = None,
    ancestor_types: list[str] | None = None,
    page_ref: str | None = None,
) -> StructureItem | None:
    raw_type = safe_name(obj_get(node, "/S"))
    normalized_type = normalize_struct_type(obj_get(node, "/S"), role_map)

    if raw_type and raw_type.startswith("/"):
        raw_type = raw_type[1:]

    title = safe_name(obj_get(node, "/T"))
    alt_text, alt_source, has_alt_entry, has_actual_text_entry = find_alt_text(node)
    kids = as_kids(obj_get(node, "/K"))
    kids_count = len(kids)

    has_objr, objr_count, kid_object_types = extract_kid_object_info(node)

    mcids = extract_mcids(node)
    resolved_page_ref = extract_page_ref(node, page_ref)

    object_ref = get_object_ref(node)

    if raw_type is None and normalized_type is None:
        return None

    normalized_ancestors = list(ancestor_types or [])

    child_types: list[str] = []
    for child in iter_structure_elements(node):
        child_type = normalize_struct_type(obj_get(child, "/S"), role_map)
        if child_type is not None:
            child_types.append(child_type)

    attributes = extract_structure_attributes(node)

    return StructureItem(
        type=raw_type,
        normalized_type=normalized_type,
        depth=depth,
        title=title,
        alt=alt_text,
        kids_count=kids_count,
        object_ref=object_ref,
        alt_source=alt_source,
        has_alt_entry=has_alt_entry,
        has_actual_text_entry=has_actual_text_entry,
        mcids=mcids,
        page_ref=resolved_page_ref,
        parent_type=parent_type,
        ancestor_types=normalized_ancestors,
        child_types=child_types,
        attributes=attributes,
        has_objr=has_objr,
        objr_count=objr_count,
        kid_object_types=kid_object_types,
    )


def walk_structure_tree(
    node: Any,
    role_map: dict[str, str],
    depth: int = 0,
    parent_type: str | None = None,
    ancestor_types: list[str] | None = None,
    page_ref: str | None = None,
) -> list[StructureItem]:
    results: list[StructureItem] = []

    item = build_structure_item(
        node,
        role_map,
        depth,
        parent_type=parent_type,
        ancestor_types=ancestor_types,
        page_ref=page_ref,
    )

    if item is None:
        return results

    results.append(item)

    next_ancestors = [*(ancestor_types or []), item.normalized_type or ""]
    next_page_ref = item.page_ref

    for child in iter_structure_elements(node):
        results.extend(
            walk_structure_tree(
                child,
                role_map,
                depth + 1,
                parent_type=item.normalized_type,
                ancestor_types=next_ancestors,
                page_ref=next_page_ref,
            )
        )

    return results


def load_structure_items(pdf: pikepdf.Pdf) -> list[StructureItem]:
    struct_tree_root = obj_get(pdf.Root, "/StructTreeRoot")
    if not struct_tree_root:
        return []

    role_map = extract_role_map(pdf)
    items: list[StructureItem] = []

    top_level = as_kids(obj_get(struct_tree_root, "/K"))
    for node in top_level:
        try:
            if isinstance(node, int):
                continue
        except Exception:
            pass

        items.extend(walk_structure_tree(node, role_map, depth=0))

    return items
