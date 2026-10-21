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
    """Official read first; Tapir fills the cells it could not read, as untyped strings.

    A property-level official error replaces only that cell, an element-level one all cells of the element. Elements
    that need it are read in one Tapir call; if Tapir fails for such an element, its missing cells are notAvailable
    and only an element without any cell is unreadable.

    Blocking: call it in a thread.
    """
    if not guids:
        return ValueTable({}, ())
    count = len(property_guids)
    rows: dict[str, list[Cell | None]] = {}  # None: still to read through Tapir
    for guid, entry in zip(guids, _read(conn_header, True, guids, property_guids)):
        if "error" in entry:
            rows[guid] = [None] * count
        else:
            rows[guid] = [
                None if "error" in value else _official_cell(value) for value in _values(entry, count, "official API")
            ]
    failed = [guid for guid, row in rows.items() if None in row]
    unreadable = []
    if failed:
        for guid, entry in zip(failed, _read(conn_header, False, failed, property_guids)):
            if "error" not in entry:
                fallback = [_tapir_cell(value) for value in _values(entry, count, "Tapir fallback")]
            elif all(cell is None for cell in rows[guid]):
                unreadable.append(guid)
                del rows[guid]
                continue
            else:
                fallback = [Cell("notAvailable", None, "string", typed=False)] * count
            rows[guid] = [tapir if cell is None else cell for cell, tapir in zip(rows[guid], fallback)]
    cells = {guid: dict(zip(property_guids, row)) for guid, row in rows.items()}
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
        raise _read_error(source, e.message) from None
    entries = response["propertyValuesForElements"]
    if len(entries) != len(guids):
        raise _read_error(source, f"returned {len(entries)} entries for {len(guids)} elements")
    return entries


def _read_error(source: str, reason: str) -> ToolError:
    return ToolError(f"Reading the property values failed in the {source} ({reason}).")


def _values(entry: dict, count: int, source: str) -> list[dict]:
    values = entry.get("propertyValues")
    if not isinstance(values, list):
        raise _read_error(source, "an element entry has neither values nor an error")
    if len(values) != count:
        raise _read_error(source, f"returned {len(values)} values for {count} properties")
    return values


def _official_cell(value: dict) -> Cell:
    if "propertyValue" not in value:
        raise _read_error("official API", "a value entry has neither a value nor an error")
    value = value["propertyValue"]
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
    for key in ("displayValue", "nonLocalizedValue"):
        if key in enum_value:
            return enum_value[key]
    raise _read_error("official API", "an enum value has neither a display nor a non-localized value")


def _tapir_cell(value: dict) -> Cell:
    if "error" in value:
        return Cell("notAvailable", None, "string", typed=False)
    if "value" not in value.get("propertyValue", {}):
        raise _read_error("Tapir fallback", "a value entry has neither a value nor an error")
    text = value["propertyValue"]["value"]
    if text == TAPIR_UNDEFINED:
        return Cell("userUndefined", None, "string", typed=False)
    return Cell("normal", text, "string", typed=False)
