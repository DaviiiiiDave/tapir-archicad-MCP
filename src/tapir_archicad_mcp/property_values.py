"""Typed property values of elements from the official API, with a marked Tapir fallback for what it cannot read."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad import ConnHeader
from multiconn_archicad.errors import APIErrorBase

# SI units of the official value types; the List variants (lengthList, ...) use the same unit.
UNITS = {"length": "m", "area": "m²", "volume": "m³", "angle": "rad"}

# What Tapir shows for a property the user left undefined.
TAPIR_UNDEFINED = "<Undefined>"


@dataclass(frozen=True)
class Cell:
    """One property value. value_type is the official type; Tapir fallback cells are untyped display strings."""

    status: Literal["normal", "userUndefined", "notAvailable", "notEvaluated"]
    value: float | int | str | bool | list | None
    value_type: str
    typed: bool = True


@dataclass
class ValueTable:
    cells: dict[str, dict[str, Cell]]  # element guid -> property guid -> cell
    unreadable: tuple[str, ...]  # elements neither the official API nor Tapir could read


def read_property_values(conn_header: ConnHeader, guids: Sequence[str], property_guids: Sequence[str]) -> ValueTable:
    """Official read first; elements with any error in it are read again, with all properties, through Tapir.

    Blocking: call it in a thread.
    """
    if not guids:
        return ValueTable({}, ())
    cells: dict[str, dict[str, Cell]] = {}
    failed: list[str] = []
    for guid, entry in zip(guids, _read(conn_header, True, guids, property_guids)):
        values = entry.get("propertyValues")  # absent when the whole element failed
        if values is None or any("error" in value for value in values):
            failed.append(guid)
        else:
            cells[guid] = {
                property_guid: _official_cell(value["propertyValue"])
                for property_guid, value in zip(property_guids, values)
            }
    unreadable = []
    if failed:
        for guid, entry in zip(failed, _read(conn_header, False, failed, property_guids)):
            if "error" in entry:
                unreadable.append(guid)
            else:
                cells[guid] = {
                    property_guid: _tapir_cell(value)
                    for property_guid, value in zip(property_guids, entry["propertyValues"])
                }
    return ValueTable(cells, tuple(unreadable))


def _read(conn_header: ConnHeader, official: bool, guids: Sequence[str], property_guids: Sequence[str]) -> list[dict]:
    source = "official API" if official else "Tapir fallback"
    parameters = {
        "elements": [{"elementId": {"guid": guid}} for guid in guids],
        "properties": [{"propertyId": {"guid": guid}} for guid in property_guids],
    }
    try:
        if official:
            response = conn_header.core.post_command(command="API.GetPropertyValuesOfElements", parameters=parameters)
        else:
            response = conn_header.core.post_tapir_command(command="GetPropertyValuesOfElements", parameters=parameters)
    except APIErrorBase as e:
        raise ToolError(f"Reading the property values failed in the {source} ({e.message}).") from None
    entries = response["propertyValuesForElements"]
    if len(entries) != len(guids):
        raise ToolError(
            f"Reading the property values failed in the {source} (returned {len(entries)} entries for "
            f"{len(guids)} elements)."
        )
    return entries


def _official_cell(value: dict) -> Cell:
    status, value_type = value["status"], value["type"]
    if status != "normal":
        return Cell(status, None, value_type)
    content = value["value"]
    if value_type == "singleEnum":
        content = _enum_text(content)
    elif value_type == "multiEnum":
        content = [_enum_text(item["enumValueId"]) for item in content]
    return Cell("normal", content, value_type)


def _enum_text(enum_value: dict) -> str:
    return enum_value.get("displayValue", enum_value.get("nonLocalizedValue"))


def _tapir_cell(value: dict) -> Cell:
    if "error" in value:
        return Cell("notAvailable", None, "string", typed=False)
    text = value["propertyValue"]["value"]
    if text == TAPIR_UNDEFINED:
        return Cell("userUndefined", None, "string", typed=False)
    return Cell("normal", text, "string", typed=False)
