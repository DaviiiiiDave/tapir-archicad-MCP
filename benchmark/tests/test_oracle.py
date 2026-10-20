import pytest
from bench.oracle import (
    door_like_elements,
    door_owners,
    editable,
    normalize_property_value,
    property_id,
    snapshot_property,
)

GUID_A = "11111111-1111-1111-1111-111111111111"
GUID_B = "22222222-2222-2222-2222-222222222222"
PROP = "CFC3AA5C-5A5E-8D4F-8359-08D842ADD73E"


class FakeAC:
    """Records every call; answers through per-command handlers."""

    def __init__(self, tapir=None, official=None):
        self.calls: list = []
        self._tapir = tapir or {}
        self._official = official or {}

    def tapir(self, command, params=None):
        self.calls.append(("tapir", command, params))
        return self._tapir[command](params)

    def official(self, command, params=None):
        self.calls.append(("official", command, params))
        return self._official[command](params)


def value(type_, status, **extra):
    return {"propertyValue": {"type": type_, "status": status, **extra}}


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (value("string", "normal", value="EI2 30"), ("value", "EI2 30")),
        (value("string", "normal", value=""), ("empty", "")),
        (value("string", "userUndefined"), ("undefined", None)),
        (value("string", "notAvailable"), ("not_available", None)),
        (value("string", "notEvaluated"), ("not_evaluated", None)),
        (value("number", "normal", value=0.075), ("value", 0.075)),
        (value("singleEnum", "normal", value={"displayValue": "F30"}), ("value", {"displayValue": "F30"})),
    ],
)
def test_normalize_typed_values_and_statuses(item, expected):
    state, normalized = normalize_property_value(item)

    assert (state, normalized) == expected
    assert type(normalized) is type(expected[1])


def test_normalize_rejects_error_items():
    with pytest.raises(ValueError, match="4002"):
        normalize_property_value({"error": {"code": 4002, "message": "Invalid property"}})


def all_properties(*entries):
    return lambda _params: {
        "properties": [
            {"propertyId": {"guid": guid}, "propertyGroupName": group, "propertyName": name, "propertyType": "Custom"}
            for guid, group, name in entries
        ]
    }


def test_property_lookup_finds_the_one_match():
    ac = FakeAC(
        tapir={"GetAllProperties": all_properties((PROP, "Allgemeine Werte", "Feuerwiderstandsklasse 13501-2"))}
    )

    assert property_id(ac, "Allgemeine Werte", "Feuerwiderstandsklasse 13501-2") == PROP


@pytest.mark.parametrize(
    "entries",
    [
        (),
        ((PROP, "Allgemeine Werte", "Brandschutz"), (GUID_A, "Allgemeine Werte", "Brandschutz")),
    ],
)
def test_property_lookup_requires_exactly_one_match(entries):
    ac = FakeAC(tapir={"GetAllProperties": all_properties(*entries)})

    with pytest.raises(ValueError, match="Allgemeine Werte.*Brandschutz"):
        property_id(ac, "Allgemeine Werte", "Brandschutz")


def echo_values(params):
    """Every element gets the string value of its own guid, so order mistakes show."""
    return {
        "propertyValuesForElements": [
            {"propertyValues": [value("string", "normal", value=element["elementId"]["guid"])]}
            for element in params["elements"]
        ]
    }


def test_snapshot_reads_in_chunks():
    ids = [f"00000000-0000-0000-0000-{n:012d}" for n in range(2500)]
    ac = FakeAC(official={"API.GetPropertyValuesOfElements": echo_values})

    snapshot = snapshot_property(ac, ids, PROP, chunk=1000)

    assert [len(call[2]["elements"]) for call in ac.calls] == [1000, 1000, 500]
    assert ac.calls[0][2]["properties"] == [{"propertyId": {"guid": PROP}}]
    assert snapshot[ids[0]] == ("value", ids[0])
    assert snapshot[ids[2499]] == ("value", ids[2499])
    assert len(snapshot) == 2500


GONE = {"error": {"code": 7204, "message": 'Element not found (element guid: "22222222-2222-2222-2222-222222222222")'}}
UNSUPPORTED = {"error": {"code": 7203, "message": 'Element not supported (element guid: "x")'}}


def test_snapshot_marks_elements_that_are_gone_as_missing():
    def answer(_params):
        return {"propertyValuesForElements": [{"propertyValues": [value("string", "userUndefined")]}, GONE]}

    ac = FakeAC(official={"API.GetPropertyValuesOfElements": answer})

    assert snapshot_property(ac, [GUID_A, GUID_B], PROP) == {
        GUID_A: ("undefined", None),
        GUID_B: ("missing", GONE["error"]["message"]),
    }


def tapir_display(*items):
    return lambda _params: {"propertyValuesForElements": [{"propertyValues": [item]} for item in items]}


@pytest.mark.parametrize(
    ("tapir_item", "expected"),
    [
        ({"propertyValue": {"value": "<Undefined>"}}, ("undefined", None)),
        ({"propertyValue": {"value": ""}}, ("empty", "")),
        ({"propertyValue": {"value": "EI2 30"}}, ("value", "EI2 30")),
        (
            {"error": {"code": -2130312908, "message": "Not available or not evaluated property"}},
            ("not_available", None),
        ),
    ],
)
def test_snapshot_reads_elements_the_official_api_does_not_support_through_tapir(tapir_item, expected):
    def official(_params):
        return {
            "propertyValuesForElements": [{"propertyValues": [value("string", "normal", value="EI2 90")]}, UNSUPPORTED]
        }

    ac = FakeAC(
        official={"API.GetPropertyValuesOfElements": official},
        tapir={"GetPropertyValuesOfElements": tapir_display(tapir_item)},
    )

    snapshot = snapshot_property(ac, [GUID_A, GUID_B], PROP)

    assert snapshot == {GUID_A: ("value", "EI2 90"), GUID_B: expected}
    tapir_call = ac.calls[-1]
    assert tapir_call[:2] == ("tapir", "GetPropertyValuesOfElements")
    assert tapir_call[2] == {
        "elements": [{"elementId": {"guid": GUID_B}}],
        "properties": [{"propertyId": {"guid": PROP}}],
    }


def test_snapshot_raises_on_other_element_errors():
    ac = FakeAC(
        official={
            "API.GetPropertyValuesOfElements": lambda _p: {
                "propertyValuesForElements": [{"error": {"code": 9999, "message": "?"}}]
            }
        }
    )

    with pytest.raises(ValueError, match="9999"):
        snapshot_property(ac, [GUID_A], PROP)


def panel_details(params):
    hidden = {GUID_C: True}
    return {
        "detailsOfElements": [
            {
                "type": "CurtainWallPanel",
                "id": "Glaspaneel-005",
                "floorIndex": 0,
                "layerIndex": 7,
                "drawIndex": 7,
                "details": {
                    "polygonCoordinates": [
                        {"x": 0, "y": 0, "z": 0},
                        {"x": 1, "y": 0, "z": 0},
                        {"x": 1, "y": 0, "z": 2},
                    ],
                    "isHidden": hidden.get(e["elementId"]["guid"], False),
                    "segmentIndex": 0,
                    "className": "",
                    "frames": [],
                },
            }
            for e in params["elements"]
        ]
    }


GUID_C = "33333333-3333-3333-3333-333333333333"
GUID_D = "44444444-4444-4444-4444-444444444444"
DOOR = "55555555-5555-5555-5555-555555555555"
PANEL_TYPE = "E57D9AC4-0C1B-4862-A394-BE488C026A4F"
RULE = {"door_panel_group": "Fassadenpaneel", "door_panel_property": "Paneel-Typ", "door_panel_value": "Fassaden Tür"}


def test_door_like_elements_are_doors_plus_visible_door_panels():
    by_type = {"Door": [DOOR], "CurtainWallPanel": [GUID_A, GUID_C, GUID_D]}
    panel_types = {GUID_A: "Fassaden Tür", GUID_C: "Fassaden Tür", GUID_D: "Integriertes Fassaden-Paneel"}
    ac = FakeAC(
        tapir={
            "GetElementsByType": lambda p: {
                "elements": [{"elementId": {"guid": g}} for g in by_type[p["elementType"]]]
            },
            "GetDetailsOfElements": panel_details,
            "GetAllProperties": all_properties((PANEL_TYPE, "Fassadenpaneel", "Paneel-Typ")),
            "GetPropertyValuesOfElements": lambda p: {
                "propertyValuesForElements": [
                    {"propertyValues": [{"propertyValue": {"value": panel_types[e["elementId"]["guid"]]}}]}
                    for e in p["elements"]
                ]
            },
        }
    )

    assert door_like_elements(ac, RULE) == [DOOR, GUID_A]


def test_door_owners_reads_owner_wall():
    def details(params):
        assert [e["elementId"]["guid"] for e in params["elements"]] == [GUID_A]
        return {
            "detailsOfElements": [
                {
                    "type": "Door",
                    "id": "D-001",
                    "floorIndex": 0,
                    "layerIndex": 12,
                    "drawIndex": 3,
                    "details": {
                        "libPart": {"name": "Tür 25"},
                        "ownerElementId": {"guid": GUID_B},
                        "ownerElementType": "Wall",
                        "width": 0.885,
                        "height": 2.135,
                        "sillHeight": 0,
                        "centerOffset": 1.5,
                        "reflected": False,
                        "refSide": True,
                        "oSide": True,
                    },
                }
            ]
        }

    ac = FakeAC(tapir={"GetDetailsOfElements": details})

    assert door_owners(ac, [GUID_A]) == {GUID_A: GUID_B}


def test_editable_intersects_ids():
    ac = FakeAC(tapir={"GetAllElements": lambda params: {"elements": [{"elementId": {"guid": GUID_A}}]}})

    assert editable(ac, [GUID_A, GUID_B]) == {GUID_A}
    assert ac.calls == [("tapir", "GetAllElements", {"filters": ["IsEditable"]})]
