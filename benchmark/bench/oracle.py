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


def snapshot_property(
    ac, element_ids: list[str], property_id: str, chunk: int = 1000
) -> dict[str, tuple[PropertyState, object]]:
    """State and value of one property per element. An element that no longer exists is ("missing", message)."""
    snapshot: dict[str, tuple[PropertyState, object]] = {}
    for start in range(0, len(element_ids), chunk):
        part = element_ids[start : start + chunk]
        response = ac.official(
            "API.GetPropertyValuesOfElements",
            {"elements": _element_ids(part), "properties": [{"propertyId": {"guid": property_id}}]},
        )
        for guid, item in zip(part, response["propertyValuesForElements"], strict=True):
            if "error" in item:
                snapshot[guid] = ("missing", item["error"].get("message"))
            else:
                snapshot[guid] = normalize_property_value(item["propertyValues"][0])
    return snapshot


def elements_of_type(ac, element_type: str, filters: list[str] | None = None) -> list[str]:
    params = {"elementType": element_type, **({"filters": filters} if filters else {})}
    return [element["elementId"]["guid"] for element in ac.tapir("GetElementsByType", params)["elements"]]


def details(ac, element_ids: list[str]) -> dict[str, dict]:
    response = ac.tapir("GetDetailsOfElements", {"elements": _element_ids(element_ids)})
    return dict(zip(element_ids, response["detailsOfElements"], strict=True))


def door_owners(ac, door_ids: list[str]) -> dict[str, str]:
    return {guid: entry["details"]["ownerElementId"]["guid"] for guid, entry in details(ac, door_ids).items()}


def editable(ac, ids: list[str]) -> set[str]:
    found = {
        element["elementId"]["guid"] for element in ac.tapir("GetAllElements", {"filters": ["IsEditable"]})["elements"]
    }
    return found & set(ids)
