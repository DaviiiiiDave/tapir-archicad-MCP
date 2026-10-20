from collections import Counter

import pytest
from bench.seed import assign_states, compare_states, read_manifest, set_states, wall_grid, write_manifest

PROP = "DCD338EC-BF33-DA43-9196-B71B64C746D2"
SYSTEM = {"guid": "B6636241-937D-234D-902F-2D30E4BD0FB8"}


def test_wall_grid_is_deterministic_and_non_overlapping():
    walls = wall_grid(5000, stories=[-1, 0, 1], thicknesses=[(0.30, 3000), (0.115, 2000)], length=3.0, spacing=1.0)

    assert walls == wall_grid(
        5000, stories=[-1, 0, 1], thicknesses=[(0.30, 3000), (0.115, 2000)], length=3.0, spacing=1.0
    )
    assert len(walls) == 5000
    assert Counter(w["thickness"] for w in walls) == {0.30: 3000, 0.115: 2000}
    assert Counter(w["floorIndex"] for w in walls) == {-1: 1667, 0: 1667, 1: 1666}
    assert all(w["structureType"] == "Basic" and w["height"] == 3.0 for w in walls)
    starts = {(w["floorIndex"], w["begCoordinate"]["x"], w["begCoordinate"]["y"]) for w in walls}
    assert len(starts) == 5000
    for floor in (-1, 0, 1):
        rows = sorted({w["begCoordinate"]["y"] for w in walls if w["floorIndex"] == floor})
        assert min(b - a for a, b in zip(rows, rows[1:])) >= 2 * 0.30
        row = sorted(
            w["begCoordinate"]["x"] for w in walls if w["floorIndex"] == floor and w["begCoordinate"]["y"] == rows[0]
        )
        assert min(b - a for a, b in zip(row, row[1:])) >= 3.0 + 0.5


def test_wall_grid_starts_at_the_origin_it_is_given():
    (wall,) = wall_grid(1, stories=[1], thicknesses=[(0.2, 1)], length=2.0, spacing=1.0, origin=(500.0, 100.0))

    assert wall["begCoordinate"] == {"x": 500.0, "y": 100.0}
    assert wall["endCoordinate"] == {"x": 502.0, "y": 100.0}
    assert wall["floorIndex"] == 1


def test_assign_states_gives_exact_counts():
    ids = [f"{n:04d}" for n in range(10)]

    states = assign_states(list(reversed(ids)), {"value": 4, "empty": 3, "undefined": 2, "not_available": 1})

    assert Counter(states.values()) == {"value": 4, "empty": 3, "undefined": 2, "not_available": 1}
    assert [states[i] for i in ids] == ["value"] * 4 + ["empty"] * 3 + ["undefined"] * 2 + ["not_available"]


def test_assign_states_rejects_counts_that_do_not_sum():
    with pytest.raises(ValueError, match="10.*9"):
        assign_states([str(n) for n in range(10)], {"value": 5, "empty": 4})


def test_manifest_round_trips(tmp_path):
    manifest = {
        "fixture": "C:/data/fixtures/german.pln",
        "doors": {"a": "value", "b": "not_available"},
        "t5_walls": ["c"],
    }

    path = write_manifest(tmp_path / "german.manifest.json", manifest)

    assert read_manifest(path) == manifest


class FakeAC:
    def __init__(self):
        self.calls = []

    def tapir(self, command, params=None):
        self.calls.append(("tapir", command, params))
        return {"executionResults": [{"success": True}] * len(next(iter(params.values())))}

    def official(self, command, params=None):
        self.calls.append(("official", command, params))
        return {"executionResults": [{"success": True}] * len(next(iter(params.values())))}


def test_set_states_writes_through_the_official_api():
    ac = FakeAC()
    states = {"w1": "value", "w2": "empty", "w3": "undefined", "w4": "not_available"}

    set_states(ac, states, PROP, value="REI 30", classification_system=SYSTEM, via="official")

    (set_values, unclassify) = ac.calls
    assert set_values[:2] == ("official", "API.SetPropertyValuesOfElements")
    assert [v["propertyValue"] for v in set_values[2]["elementPropertyValues"]] == [
        {"type": "string", "status": "normal", "value": "REI 30"},
        {"type": "string", "status": "normal", "value": ""},
        {"type": "string", "status": "userUndefined"},
    ]
    assert [v["elementId"]["guid"] for v in set_values[2]["elementPropertyValues"]] == ["w1", "w2", "w3"]
    assert unclassify == (
        "official",
        "API.SetClassificationsOfElements",
        {
            "elementClassifications": [
                {"elementId": {"guid": "w4"}, "classificationId": {"classificationSystemId": SYSTEM}}
            ]
        },
    )


def test_set_states_writes_panels_through_tapir_and_leaves_undefined_alone():
    ac = FakeAC()
    states = {"p1": "value", "p2": "empty", "p3": "undefined", "p4": "not_available"}

    set_states(ac, states, PROP, value="EI2 30", classification_system=SYSTEM, via="tapir")

    (set_values, unclassify) = ac.calls
    assert set_values[:2] == ("tapir", "SetPropertyValuesOfElements")
    assert [(v["elementId"]["guid"], v["propertyValue"]) for v in set_values[2]["elementPropertyValues"]] == [
        ("p1", {"value": "EI2 30"}),
        ("p2", {"value": ""}),
    ]
    assert unclassify[:2] == ("tapir", "SetClassificationsOfElements")
    assert [c["elementId"]["guid"] for c in unclassify[2]["elementClassifications"]] == ["p4"]


def test_set_states_fails_loudly_when_archicad_refuses():
    ac = FakeAC()
    ac.official = lambda command, params=None: {
        "executionResults": [{"success": False, "error": {"code": 6001, "message": "TeamWork permission denied"}}]
    }

    with pytest.raises(RuntimeError, match="6001"):
        set_states(ac, {"w1": "value"}, PROP, value="REI 30", classification_system=SYSTEM, via="official")


def test_compare_states_reports_each_mismatch():
    expected = {"a": "value", "b": "empty", "c": "undefined"}
    actual = {"a": ("value", "EI2 30"), "b": ("undefined", None), "d": ("value", "x")}

    assert compare_states(expected, actual) == [
        "b: expected empty, found undefined",
        "c: expected undefined, found nothing",
        "d: not expected, found value",
    ]
