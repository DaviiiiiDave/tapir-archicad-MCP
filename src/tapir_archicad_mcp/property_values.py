"""Typed property values of elements from the official API, with a marked Tapir fallback for what it cannot read."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad import ConnHeader
from multiconn_archicad.errors import APIErrorBase

# SI units of the official value types; the List variants (lengthList, ...) use the same unit.
UNITS = {"length": "m", "area": "m²", "volume": "m³", "angle": "rad"}

# What Tapir shows for a property the user left undefined. Only this exact, case-sensitive text counts.
TAPIR_UNDEFINED = "<Undefined>"

# Tapir's "Not available or not evaluated property" is its only property-level error we map. It cannot tell
# notAvailable from notEvaluated, so both come back as notAvailable. Any other code is a real failure.
TAPIR_NOT_AVAILABLE_CODE = -2130312908

STATUSES = ("normal", "userUndefined", "notAvailable", "notEvaluated")
OFFICIAL_SOURCE = "the official API"
TAPIR_SOURCE = "Tapir (used for elements the official API cannot read)"


@dataclass(frozen=True)
class Cell:
    """One property value.

    Cells from the official API are typed: value_type is the official type and numbers are in SI units. Cells from the
    Tapir fallback are display strings with typed=False; their value_type is still the property's declared official
    type, taken from its definition.
    """

    status: Literal["normal", "userUndefined", "notAvailable", "notEvaluated"]
    value: float | int | str | bool | list | None
    value_type: str
    typed: bool = True


@dataclass
class ValueTable:
    cells: dict[str, dict[str, Cell]]  # element guid -> property guid -> cell
    unreadable: tuple[str, ...]  # elements neither the official API nor Tapir could read, and are absent from cells


@dataclass(frozen=True)
class _Definition:
    value_type: str
    enum_values: dict[tuple[str, str], str]  # (id type, id value) -> display value


def read_property_values(conn_header: ConnHeader, guids: Sequence[str], property_guids: Sequence[str]) -> ValueTable:
    """Read the values of the properties for the elements: the official API first, Tapir for what it cannot read.

    One official call returns the definitions of the properties. They give every cell its declared type and map enum
    ids to display values. Then the official API reads the values. A property-level error there is replaced by the
    Tapir value for that cell, an element-level one for all cells of the element; these cells are untyped display
    strings. All elements that need it share one Tapir call. If Tapir fails for such an element, its missing cells are
    notAvailable and only an element without any cell is unreadable.

    Returns an empty table without any call when there are no elements or no properties.

    Raises ToolError, and returns no partial table, when a property does not exist in the project, when a call fails,
    or when an answer has an unexpected shape, an unknown status or Tapir error code, or an enum value that its
    definition does not list.

    Blocking: call it in a thread.
    """
    if not guids or not property_guids:
        return ValueTable({}, ())
    definitions = _read_definitions(conn_header, property_guids)
    count = len(property_guids)
    rows: dict[str, list[Cell | None]] = {}  # None: still to read through Tapir
    for guid, entry in zip(guids, _read(conn_header, True, guids, property_guids)):
        if "error" in entry:
            rows[guid] = [None] * count
        else:
            rows[guid] = [
                None if "error" in value else _official_cell(value, property_guid, definitions[property_guid])
                for value, property_guid in zip(_values(entry, count, OFFICIAL_SOURCE), property_guids)
            ]
    failed = [guid for guid, row in rows.items() if None in row]
    unreadable = []
    if failed:
        for guid, entry in zip(failed, _read(conn_header, False, failed, property_guids)):
            if "error" not in entry:
                tapir = _values(entry, count, TAPIR_SOURCE)
            elif all(cell is None for cell in rows[guid]):
                unreadable.append(guid)
                del rows[guid]
                continue
            else:
                tapir = [None] * count
            rows[guid] = [
                cell if cell is not None else _tapir_cell(value, definitions[property_guid])
                for cell, value, property_guid in zip(rows[guid], tapir, property_guids)
            ]
    cells = {guid: dict(zip(property_guids, row)) for guid, row in rows.items()}
    return ValueTable(cells, tuple(unreadable))


def _read_definitions(conn_header: ConnHeader, property_guids: Sequence[str]) -> dict[str, _Definition]:
    unique = list(dict.fromkeys(property_guids))
    parameters = {"properties": [{"propertyId": {"guid": guid}} for guid in unique]}
    response = _post(conn_header, True, "API.GetDetailsOfProperties", parameters)
    entries = _entries(response, "propertyDefinitions", "definitions", "properties", len(unique), OFFICIAL_SOURCE)
    definitions = {}
    for guid, entry in zip(unique, entries):
        if "error" in entry:
            raise ToolError(
                f"Property {guid} does not exist in this project; find property ids with properties_search."
            )
        definition = entry.get("propertyDefinition")
        if not isinstance(definition, dict) or "type" not in definition:
            raise _unexpected(OFFICIAL_SOURCE, "a property definition has no type")
        definitions[guid] = _Definition(definition["type"], _enum_values(definition.get("possibleEnumValues", [])))
    return definitions


def _enum_values(possible: list) -> dict[tuple[str, str], str]:
    try:
        return {_enum_key(item["enumValue"]["enumValueId"]): item["enumValue"]["displayValue"] for item in possible}
    except (KeyError, TypeError):
        raise _unexpected(OFFICIAL_SOURCE, "a property definition lists a malformed enum value") from None


def _enum_key(enum_id: dict | None) -> tuple[str, str]:
    return enum_id["type"], enum_id[enum_id["type"]]


def _read(conn_header: ConnHeader, official: bool, guids: Sequence[str], property_guids: Sequence[str]) -> list[dict]:
    parameters = {
        "elements": [{"elementId": {"guid": guid}} for guid in guids],
        "properties": [{"propertyId": {"guid": guid}} for guid in property_guids],
    }
    command = "GetPropertyValuesOfElements"
    response = _post(conn_header, official, f"API.{command}" if official else command, parameters)
    source = OFFICIAL_SOURCE if official else TAPIR_SOURCE
    return _entries(response, "propertyValuesForElements", "entries", "elements", len(guids), source)


def _post(conn_header: ConnHeader, official: bool, command: str, parameters: dict) -> dict:
    try:
        if official:
            return conn_header.core.post_command(command=command, parameters=parameters)
        return conn_header.core.post_tapir_command(command=command, parameters=parameters)
    except APIErrorBase as e:
        raise _failed(OFFICIAL_SOURCE if official else TAPIR_SOURCE, e.message) from None


def _entries(response: dict, key: str, what: str, unit: str, asked: int, source: str) -> list[dict]:
    entries = response.get(key)
    if not isinstance(entries, list):
        raise _unexpected(source, f"it has no {key}")
    if len(entries) != asked:
        raise _unexpected(source, f"returned {what} for {len(entries)} of {asked} {unit}")
    return entries


def _failed(source: str, reason: str) -> ToolError:
    return ToolError(
        f"Reading the property values through {source} failed ({reason}); no values were returned. "
        "Retry once Archicad responds."
    )


def _unexpected(source: str, detail: str) -> ToolError:
    return ToolError(
        f"{source[:1].upper()}{source[1:]} answered in an unexpected shape ({detail}), so no values were returned. "
        "Retry; if it repeats, the installed Archicad or Tapir may not match this server."
    )


def _values(entry: dict, count: int, source: str) -> list[dict]:
    values = entry.get("propertyValues")
    if not isinstance(values, list):
        raise _unexpected(source, "an element entry has neither values nor an error")
    if len(values) != count:
        raise _unexpected(source, f"returned values for {len(values)} of {count} properties")
    return values


def _official_cell(value: dict, property_guid: str, definition: _Definition) -> Cell:
    entry = value.get("propertyValue")
    if not isinstance(entry, dict):
        raise _unexpected(OFFICIAL_SOURCE, "a value entry has neither a value nor an error")
    status, value_type = entry.get("status"), entry.get("type")
    if value_type is None:
        raise _unexpected(OFFICIAL_SOURCE, "a value has no type")
    if status not in STATUSES:
        raise _unexpected(OFFICIAL_SOURCE, "a value has no known status")
    if status != "normal":
        return Cell(status, None, value_type)
    if "value" not in entry:
        raise _unexpected(OFFICIAL_SOURCE, "a normal value has no value")
    content = entry["value"]
    if value_type == "singleEnum":
        content = _enum_text(content, property_guid, definition)
    elif value_type == "multiEnum":
        content = [
            _enum_text(item.get("enumValueId") if isinstance(item, dict) else None, property_guid, definition)
            for item in content
        ]
    return Cell("normal", content, value_type)


def _enum_text(enum_id: dict | None, property_guid: str, definition: _Definition) -> str:
    try:
        key = _enum_key(enum_id)
    except (KeyError, TypeError):
        raise _unexpected(OFFICIAL_SOURCE, "an enum value has no id") from None
    if key not in definition.enum_values:
        raise ToolError(
            f"Property {property_guid} returned an enum value that its definition does not list, so it cannot be shown."
        )
    return definition.enum_values[key]


def _tapir_cell(value: dict | None, definition: _Definition) -> Cell:
    """None: Tapir failed for the whole element."""
    unavailable = Cell("notAvailable", None, definition.value_type, typed=False)
    if value is None:
        return unavailable
    if "error" in value:
        code = value["error"].get("code")
        if code != TAPIR_NOT_AVAILABLE_CODE:
            raise _failed(TAPIR_SOURCE, f"error code {code}")
        return unavailable
    if "value" not in value.get("propertyValue", {}):
        raise _unexpected(TAPIR_SOURCE, "a value entry has neither a value nor an error")
    text = value["propertyValue"]["value"]
    if text == TAPIR_UNDEFINED:
        return Cell("userUndefined", None, definition.value_type, typed=False)
    return Cell("normal", text, definition.value_type, typed=False)
