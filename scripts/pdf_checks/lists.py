"""List structure validation checks for tagged PDF documents."""

from .models import StructureItem

LIST_CONTAINER = "L"
LIST_ITEM = "LI"
LIST_LABEL = "Lbl"
LIST_BODY = "LBody"
LIST_CAPTION = "Caption"
DIV = "Div"

ALLOWED_L_CHILDREN = {LIST_ITEM, LIST_CAPTION, LIST_CONTAINER, DIV}
ALLOWED_LI_CHILDREN_WITHOUT_WARNING = {
    LIST_LABEL,
    LIST_BODY,
    LIST_CONTAINER,
    DIV,
}


def check_lists(structure_items: list[StructureItem], result: dict) -> None:
    result["ListCount"] = 0
    result["InvalidListItemParents"] = ""
    result["InvalidListChildren"] = ""
    result["MalformedListNodes"] = ""

    if result.get("TaggedTest") != "Pass":
        result["ListsTest"] = "NotApplicable"
        return

    lists = [item for item in structure_items if item.normalized_type == LIST_CONTAINER]
    list_items = [item for item in structure_items if item.normalized_type == LIST_ITEM]
    lbodies = [item for item in structure_items if item.normalized_type == LIST_BODY]

    result["ListCount"] = len(lists)

    if not lists and not list_items:
        result["ListsTest"] = "NotApplicable"
        return

    failures: list[str] = []
    warnings: list[str] = []
    invalid_children: list[str] = []

    for item in lists:
        ref = item.object_ref or "unknown-object"
        child_types = item.child_types

        if item.kids_count == 0:
            warnings.append(f"{ref}: L is empty")
            continue

        disallowed_children = [
            child_type for child_type in child_types if child_type not in ALLOWED_L_CHILDREN
        ]
        if disallowed_children:
            failures.append(
                f"{ref}: L has disallowed children {', '.join(disallowed_children)}"
            )

    for item in list_items:
        ref = item.object_ref or "unknown-object"
        child_types = item.child_types
        child_type_set = set(child_types)

        if item.parent_type != LIST_CONTAINER:
            failures.append(
                f"{ref}: LI parent is {item.parent_type or 'None'}, expected L"
            )

        if item.kids_count == 0:
            warnings.append(f"{ref}: LI is empty")
            continue

        has_lbody = LIST_BODY in child_type_set

        unusual_children = [
            child_type
            for child_type in child_types
            if child_type not in ALLOWED_LI_CHILDREN_WITHOUT_WARNING
        ]

        if not has_lbody:
            if not child_types:
                warnings.append(f"{ref}: LI missing LBody but contains direct content")
            else:
                non_label_children = [
                    child_type for child_type in child_types if child_type != LIST_LABEL
                ]

                if not non_label_children:
                    warnings.append(f"{ref}: LI missing LBody and no body content found")
                else:
                    warnings.append(f"{ref}: LI missing LBody but contains direct content")

        if unusual_children:
            invalid_children.append(
                f"{ref}: LI has unusual children {', '.join(unusual_children)}"
            )

    for item in lbodies:
        ref = item.object_ref or "unknown-object"

        if item.parent_type != LIST_ITEM:
            failures.append(
                f"{ref}: LBody parent is {item.parent_type or 'None'}, expected LI"
            )

        if item.kids_count == 0:
            warnings.append(f"{ref}: LBody is empty")

    result["InvalidListItemParents"] = " | ".join(
        msg for msg in failures if "LI parent is" in msg
    )
    result["InvalidListChildren"] = " | ".join(invalid_children)
    result["MalformedListNodes"] = " | ".join(
        [msg for msg in warnings if "missing LBody" in msg or "is empty" in msg]
        + [
            msg
            for msg in failures
            if "L has disallowed children" in msg or "LBody parent is" in msg
        ]
    )

    if failures:
        result["ListsTest"] = "Fail"
        result["Accessible"] = False
        result["_log"] += "lists-fail, "
    elif warnings or invalid_children:
        result["ListsTest"] = "Warn"
        result["_log"] += "lists-warn, "
    else:
        result["ListsTest"] = "Pass"
