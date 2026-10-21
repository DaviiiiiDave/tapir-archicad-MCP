"""Element lists returned by the read commands in AUTO_SET_FIELDS become sets; lists longer than INLINE_THRESHOLD
are replaced by a summary of the set."""

import hashlib
from collections import Counter
from collections.abc import Sequence

from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad import ConnHeader
from multiconn_archicad.errors import APIErrorBase

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

# Lists of up to this many unique elements stay in the result next to the set summary; longer ones are replaced by it.
INLINE_THRESHOLD = 20

UNREADABLE_TYPE = "(unreadable)"
UNTITLED_WARNING = (
    "The project is untitled and all untitled projects share one identity, so this set cannot be told apart "
    "from a set of another untitled project. Do not use it after switching projects."
)


def summarize_set(conn_header: ConnHeader, element_set: ElementSet, port: int, warnings: Sequence[str] = ()) -> dict:
    """Count, element types, and follow-up call templates for a set. Blocking: call it in a thread."""
    return _summary(element_set, port, warnings, _count_types(conn_header, element_set.guids))


def _summary(element_set: ElementSet, port: int, warnings: Sequence[str], by_type: dict[str, int]) -> dict:
    warnings = [*warnings, UNTITLED_WARNING] if element_set.project == UNTITLED else list(warnings)
    set_id = element_set.id
    reference = f"{{'set': '{set_id}'}}"

    def template(command: str, params: str) -> str:
        return f"archicad_read_tool(name='{command}', arguments={{'port': {port}, 'params': {{{params}}}}})"

    return {
        "set": set_id,
        "count": len(element_set.guids),
        "byType": by_type,
        "createdAt": element_set.created_at.isoformat(),
        "warnings": warnings,
        "next": [
            template("elements_change_selection_of_elements", f"'addElementsToSelection': {reference}"),
            template("navigator_fit_in_window", f"'elements': {reference}"),
            template(
                "elements_filter_elements", f"'elements': {reference}, 'filters': [<filter such as 'IsEditable'>]"
            ),
        ],
    }


def _count_types(conn_header: ConnHeader, guids: Sequence[str]) -> dict[str, int]:
    if not guids:
        return {}
    try:
        response = conn_header.core.post_command(
            command="API.GetTypesOfElements",
            parameters={"elements": [{"elementId": {"guid": guid}} for guid in guids]},
        )
    except APIErrorBase as e:
        raise _type_count_error(e.message) from None
    entries = response["typesOfElements"]
    if len(entries) != len(guids):
        raise _type_count_error(f"Archicad returned {len(entries)} type entries for {len(guids)} elements")
    counts = Counter(
        entry["typeOfElement"]["elementType"] if "typeOfElement" in entry else UNREADABLE_TYPE for entry in entries
    )
    return dict(counts)


def _type_count_error(reason: str) -> ToolError:
    return ToolError(
        f"The elements were read, but counting their types for the set summary failed ({reason}); no set was "
        f"created. This needs Archicad 26 or later; start the server with --element-sets off to get the raw list."
    )


def apply_auto_set(name: str, result: dict, conn_header: ConnHeader, port: int, parameters: str) -> dict:
    """Store the result's element list as a set and add its summary under "set".

    The threshold and the "count" both use the de-duplicated elements; a list that stays is returned de-duplicated.
    A list longer than INLINE_THRESHOLD is removed from the result. When the project identity cannot be read,
    no set is made and the result is returned with a "setError". A replaced list leaves an "envelope" entry for
    its field. Blocking: call it in a thread.
    """
    field = AUTO_SET_FIELDS[name]
    project, reason = read_project_fingerprint(conn_header)
    if project == UNKNOWN:
        return {
            **result,
            "setError": (
                f"Could not read the project identity ({reason}), so no set was created. The full element list is "
                f"returned instead (paginated); retry once Archicad responds if you need a set."
            ),
        }
    origin = {"command": name, "parametersHash": hashlib.sha256(parameters.encode()).hexdigest()[:16]}
    guids = list(dict.fromkeys(item["elementId"]["guid"] for item in result[field]))
    # Ask Archicad before storing, so a failing request leaves no set behind.
    by_type = _count_types(conn_header, guids)
    element_set = set_store_instance.get().create(port, project, guids, origin)
    summary = _summary(element_set, port, (), by_type)
    if len(guids) <= INLINE_THRESHOLD:
        return {**result, field: [{"elementId": {"guid": guid}} for guid in guids], "set": summary}
    envelope = {field: {"total": len(guids), "returned": 0, "truncated": True}}
    return {**{key: value for key, value in result.items() if key != field}, "set": summary, "envelope": envelope}
