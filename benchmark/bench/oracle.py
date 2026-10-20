"""Out-of-band reads of Archicad state. Property values come from the official API as typed values with a
status, never from Tapir display strings (German Archicad returns "0,075")."""

from typing import Literal

PropertyState = Literal["value", "empty", "undefined", "not_available", "not_evaluated", "missing"]

_STATUS = {"userUndefined": "undefined", "notAvailable": "not_available", "notEvaluated": "not_evaluated"}


def normalize_property_value(item: dict) -> tuple[PropertyState, object]:
    if "error" in item:
        error = item["error"]
        raise ValueError(f"property value error {error.get('code')}: {error.get('message')}")
    property_value = item["propertyValue"]
    status = property_value.get("status")
    if status in _STATUS:
        return _STATUS[status], None
    raw = property_value.get("value")
    if raw in ("", []):
        return "empty", raw
    return "value", raw


def property_id(ac, group: str, name: str) -> str:
    properties = ac.tapir("GetAllProperties", {})["properties"]
    matches = [p for p in properties if p["propertyGroupName"] == group and p["propertyName"] == name]
    if len(matches) != 1:
        raise ValueError(f"{len(matches)} properties named {group!r} › {name!r}, expected exactly one")
    return matches[0]["propertyId"]["guid"]


def _element_ids(guids: list[str]) -> list[dict]:
    return [{"elementId": {"guid": guid}} for guid in guids]


# Official API element errors (measured on AC29): 7204 = element not found, 7203 = element type not supported
# (curtain-wall panels). Tapir reads what the official API does not support, as display strings.
OFFICIAL_NOT_FOUND = 7204
OFFICIAL_NOT_SUPPORTED = 7203
TAPIR_UNDEFINED = "<Undefined>"
TAPIR_NOT_AVAILABLE = -2130312908  # "Not available or not evaluated property"


def _tapir_state(item: dict) -> tuple[PropertyState, object]:
    if "error" in item:
        if item["error"].get("code") == TAPIR_NOT_AVAILABLE:
            return "not_available", None
        raise ValueError(f"Tapir property value error {item['error'].get('code')}: {item['error'].get('message')}")
    raw = item["propertyValue"]["value"]
    if raw == TAPIR_UNDEFINED:
        return "undefined", None
    if raw == "":
        return "empty", ""
    return "value", raw


def _property_query(guids: list[str], property_id: str) -> dict:
    return {"elements": _element_ids(guids), "properties": [{"propertyId": {"guid": property_id}}]}


def snapshot_property(
    ac, element_ids: list[str], property_id: str, chunk: int = 1000
) -> dict[str, tuple[PropertyState, object]]:
    """State and value of one property per element, from typed official values where the official API supports
    the element and from Tapir display strings where it does not (exact for string properties only).
    An element that no longer exists is ("missing", message)."""
    snapshot: dict[str, tuple[PropertyState, object]] = {}
    unsupported: list[str] = []
    for start in range(0, len(element_ids), chunk):
        part = element_ids[start : start + chunk]
        response = ac.official("API.GetPropertyValuesOfElements", _property_query(part, property_id))
        for guid, item in zip(part, response["propertyValuesForElements"], strict=True):
            if "error" not in item:
                snapshot[guid] = normalize_property_value(item["propertyValues"][0])
            elif item["error"].get("code") == OFFICIAL_NOT_FOUND:
                snapshot[guid] = ("missing", item["error"].get("message"))
            elif item["error"].get("code") == OFFICIAL_NOT_SUPPORTED:
                unsupported.append(guid)
            else:
                raise ValueError(f"element {guid}: error {item['error'].get('code')}: {item['error'].get('message')}")
    for start in range(0, len(unsupported), chunk):
        part = unsupported[start : start + chunk]
        response = ac.tapir("GetPropertyValuesOfElements", _property_query(part, property_id))
        for guid, element in zip(part, response["propertyValuesForElements"], strict=True):
            if "propertyValues" in element:
                snapshot[guid] = _tapir_state(element["propertyValues"][0])
            else:
                snapshot[guid] = ("missing", element["error"].get("message"))
    return snapshot


def elements_of_type(ac, element_type: str, filters: list[str] | None = None) -> list[str]:
    params = {"elementType": element_type, **({"filters": filters} if filters else {})}
    return [element["elementId"]["guid"] for element in ac.tapir("GetElementsByType", params)["elements"]]


def details(ac, element_ids: list[str]) -> dict[str, dict]:
    response = ac.tapir("GetDetailsOfElements", {"elements": _element_ids(element_ids)})
    return dict(zip(element_ids, response["detailsOfElements"], strict=True))


def door_owners(ac, door_ids: list[str]) -> dict[str, str]:
    return {guid: entry["details"]["ownerElementId"]["guid"] for guid, entry in details(ac, door_ids).items()}


def door_like_elements(ac, rule: dict) -> list[str]:
    """Doors plus the visible curtain-wall panels whose panel-type property shows the door value.
    Hidden panels are deleted ones that stay in the database."""
    doors = elements_of_type(ac, "Door")
    panels = elements_of_type(ac, "CurtainWallPanel")
    if not panels:
        return doors
    visible = [guid for guid, entry in details(ac, panels).items() if not entry["details"]["isHidden"]]
    panel_type = property_id(ac, rule["door_panel_group"], rule["door_panel_property"])
    response = ac.tapir("GetPropertyValuesOfElements", _property_query(visible, panel_type))
    door_panels = [
        guid
        for guid, element in zip(visible, response["propertyValuesForElements"], strict=True)
        if (element.get("propertyValues") or [{}])[0].get("propertyValue", {}).get("value") == rule["door_panel_value"]
    ]
    return doors + door_panels


def editable(ac, ids: list[str]) -> set[str]:
    found = {
        element["elementId"]["guid"] for element in ac.tapir("GetAllElements", {"filters": ["IsEditable"]})["elements"]
    }
    return found & set(ids)
