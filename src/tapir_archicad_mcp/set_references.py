"""Set references ({"set": "set:ab12cd34"}) in element-list parameters, expanded to element lists before validation."""

import copy
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad import ConnHeader

from tapir_archicad_mcp.context import set_store_instance
from tapir_archicad_mcp.element_sets import ElementSet
from tapir_archicad_mcp.project_identity import UNKNOWN, read_project_fingerprint

# Every top-level `params` field typed list[ElementIdArrayItem] (items exactly {"elementId": {"guid"}}), per command.
# Element pairs (elementPropertyValues, elementClassifications, ...) are deliberately not in it.
# A test compares this table with the registered params models, so a Tapir bump shows up as a failing diff.
SET_EXPANSION_FIELDS: dict[str, tuple[str, ...]] = {
    "classifications_get_classifications_of_elements": ("elements",),
    "components_get_components_of_elements": ("elements",),
    "design_options_get_design_option_for_elements": ("elements",),
    "elements_change_selection_of_elements": ("addElementsToSelection", "removeElementsFromSelection"),
    "elements_delete_elements": ("elements",),
    "elements_filter_elements": ("elements",),
    "elements_get2_d_bounding_boxes": ("elements",),
    "elements_get3_d_bounding_boxes": ("elements",),
    "elements_get_collisions": ("elementsGroup1", "elementsGroup2"),
    "elements_get_connected_elements": ("elements",),
    "elements_get_details_of_elements": ("elements",),
    "elements_get_dimension_data": ("elements",),
    "elements_get_elements_related_to_zones": ("zones",),
    "elements_get_gdl_parameters_of_elements": ("elements",),
    "elements_get_marker_parameters_of_elements": ("elements",),
    "elements_get_relations_of_elements": ("elements",),
    "elements_get_subelements_of_hierarchical_elements": ("elements",),
    "elements_get_types_of_elements": ("elements",),
    "elements_get_zone_boundaries": ("zones",),
    "elements_highlight_elements": ("elements",),
    "elements_lock_elements": ("elements",),
    "elements_unlock_elements": ("elements",),
    "grouping_get_groups_of_elements": ("elements",),
    "ifc_get_ifc_ids_of_elements": ("elements",),
    "ifc_get_ifc_properties_of_elements": ("elements",),
    "ifc_get_ifc_type_of_elements": ("elements",),
    "issues_attach_elements_to_issue": ("elements",),
    "issues_detach_elements_from_issue": ("elements",),
    "mep_get_mep_ports": ("elements",),
    "mep_get_mep_routing_elements": ("elements",),
    "navigator_fit_in_window": ("elements",),
    "navigator_update_drawings": ("elements",),
    "project_save_as_module_file": ("elements",),
    "properties_get_all_property_ids_of_elements": ("elements",),
    "properties_get_property_values_of_elements": ("elements",),
    "revisions_get_revision_changes_of_elements": ("elements",),
    "solid_ops__get_element_trims": ("elements",),
    "solid_ops__get_solid_element_links": ("elements",),
    "solid_ops__trim_elements": ("elements",),
    "teamwork_release_elements": ("elements",),
    "teamwork_reserve_elements": ("elements",),
}

SET_REFERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"set": {"type": "string", "pattern": "^set:[0-9a-f]{8}$"}},
    "required": ["set"],
    "additionalProperties": False,
    "examples": [{"set": "set:ab12cd34"}],
}


def is_set_reference(value: Any) -> bool:
    """True for a dict whose only key is "set" and whose value is a string."""
    return isinstance(value, dict) and list(value) == ["set"] and isinstance(value["set"], str)


def mentions_set_reference(value: Any) -> bool:
    """True for any dict with a "set" key, well formed or not."""
    return isinstance(value, dict) and "set" in value


def resolve_set(conn_header: ConnHeader, port: int, set_id: str) -> ElementSet:
    """Look the set up for the project open on the port right now. Blocking: call it in a thread."""
    project, reason = read_project_fingerprint(conn_header)
    if project == UNKNOWN:
        raise ToolError(
            f"The project identity on port {port} could not be read ({reason}), so set '{set_id}' cannot be "
            f"checked against the open project. Try again once Archicad responds."
        )
    return set_store_instance.get().get(set_id, port, project)


def expand_set_references(
    name: str, arguments: dict, resolve: Callable[[str], ElementSet]
) -> tuple[dict, dict[str, str]]:
    """Replace set references in top-level `params` fields with element lists.

    Returns new arguments and the expanded field paths mapped to set ids; the input is never mutated.
    """
    params = arguments["params"]
    allowed = SET_EXPANSION_FIELDS.get(name, ())
    expanded_params = dict(params)
    sets: dict[str, str] = {}
    for field, value in params.items():
        if field in allowed:
            if not mentions_set_reference(value):
                continue
            if not is_set_reference(value):
                raise ToolError(
                    f'params.{field}: a set reference must be exactly {{"set": "set:xxxxxxxx"}} with no other keys, '
                    f"got {value!r}."
                )
            expanded_params[field] = [{"elementId": {"guid": guid}} for guid in resolve(value["set"]).guids]
            sets[f"params.{field}"] = value["set"]
        elif is_set_reference(value):
            accepted = ", ".join(f"params.{f}" for f in allowed) or "none"
            raise ToolError(
                f"'{name}' does not accept a set reference in params.{field}. "
                f"Fields that accept set references: {accepted}."
            )
    return {**arguments, "params": expanded_params}, sets


def patch_schema(name: str, schema: dict) -> dict:
    """Return the command's argument schema with `{"set": ...}` offered next to each expandable element list."""
    fields = SET_EXPANSION_FIELDS.get(name)
    if fields is None:
        return schema
    patched = copy.deepcopy(schema)
    definition_name = patched["properties"]["params"]["$ref"].rsplit("/", 1)[-1]
    properties = patched["$defs"][definition_name]["properties"]
    for field in fields:
        properties[field] = {"anyOf": [properties[field], {"$ref": "#/$defs/SetReference"}]}
    patched["$defs"]["SetReference"] = copy.deepcopy(SET_REFERENCE_SCHEMA)
    return patched
