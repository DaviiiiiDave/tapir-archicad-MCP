"""Element lists returned by read commands become sets; large lists are replaced by a summary of the set."""

import hashlib
from collections import Counter
from collections.abc import Sequence

from multiconn_archicad import ConnHeader

from tapir_archicad_mcp.context import set_store_instance
from tapir_archicad_mcp.element_sets import ElementSet
from tapir_archicad_mcp.project_identity import UNKNOWN, UNTITLED, read_project_fingerprint

# Read command -> the result field holding its element list ({"elementId": {"guid": ...}} items).
AUTO_SET_FIELDS: dict[str, str] = {
    "elements_get_all_elements": "elements",
    "elements_get_elements_by_type": "elements",
    "elements_get_elements_by_classification": "elements",
    "elements_get_selected_elements": "elements",
    "elements_filter_elements": "elements",
    "issues_get_elements_attached_to_issue": "elements",
}

# Lists up to this length stay in the result next to the set summary; longer ones are replaced by it.
INLINE_THRESHOLD = 20

UNREADABLE_TYPE = "(unreadable)"
UNTITLED_WARNING = (
    "The project is untitled and all untitled projects share one identity, so this set cannot be told apart "
    "from a set of another untitled project. Do not use it after switching projects."
)


def summarize_set(conn_header: ConnHeader, element_set: ElementSet, port: int, warnings: Sequence[str] = ()) -> dict:
    """Count, element types, and ready-to-use follow-up calls for a set. Blocking: call it in a thread."""
    warnings = [*warnings, UNTITLED_WARNING] if element_set.project == UNTITLED else list(warnings)
    prefix = f"archicad_read_tool(name='%s', arguments={{'port': {port}, 'params': {{'set': '{element_set.id}', %s}}}})"
    return {
        "set": element_set.id,
        "count": len(element_set.guids),
        "byType": _count_types(conn_header, element_set.guids),
        "createdAt": element_set.created_at.isoformat(),
        "warnings": warnings,
        "next": {
            "rows": prefix % ("sets_rows", "'columns': [...]"),
            "aggregate": prefix % ("sets_aggregate", "'groupBy': ..."),
        },
    }


def _count_types(conn_header: ConnHeader, guids: Sequence[str]) -> dict[str, int]:
    if not guids:
        return {}
    response = conn_header.core.post_command(
        command="API.GetTypesOfElements",
        parameters={"elements": [{"elementId": {"guid": guid}} for guid in guids]},
    )
    counts = Counter(
        entry["typeOfElement"]["elementType"] if "typeOfElement" in entry else UNREADABLE_TYPE
        for entry in response["typesOfElements"]
    )
    return dict(counts)


def apply_auto_set(name: str, result: dict, conn_header: ConnHeader, port: int, parameters: str) -> dict:
    """Store the result's element list as a set and add its summary under "set".

    A list longer than INLINE_THRESHOLD is removed from the result. When the project identity cannot be read,
    no set is made and the result is returned with a "setError". Blocking: call it in a thread.
    """
    field = AUTO_SET_FIELDS[name]
    project, reason = read_project_fingerprint(conn_header)
    if project == UNKNOWN:
        return {**result, "setError": f"{reason}; no set was created"}
    origin = {"command": name, "parametersHash": hashlib.sha256(parameters.encode()).hexdigest()[:16]}
    guids = [item["elementId"]["guid"] for item in result[field]]
    element_set = set_store_instance.get().create(port, project, guids, origin)
    summary = summarize_set(conn_header, element_set, port)
    if len(result[field]) <= INLINE_THRESHOLD:
        return {**result, "set": summary}
    return {**{key: value for key, value in result.items() if key != field}, "set": summary}
