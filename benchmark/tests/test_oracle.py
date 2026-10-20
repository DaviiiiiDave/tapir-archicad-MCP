import pytest
from bench.oracle import door_owners, editable, normalize_property_value, property_id, snapshot_property

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


def test_snapshot_marks_elements_that_are_gone_as_missing():
    def answer(_params):
        return {
            "propertyValuesForElements": [
                {"propertyValues": [value("string", "userUndefined")]},
                {"error": {"code": 4001, "message": "The element does not exist."}},
            ]
        }

    ac = FakeAC(official={"API.GetPropertyValuesOfElements": answer})

    assert snapshot_property(ac, [GUID_A, GUID_B], PROP) == {
        GUID_A: ("undefined", None),
        GUID_B: ("missing", "The element does not exist."),
    }


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
