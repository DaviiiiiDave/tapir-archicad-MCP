import pytest
from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad.errors import CommandTimeoutError, StandardCommandUnavailable

from tapir_archicad_mcp.property_values import UNITS, Cell, read_property_values

OFFICIAL = "API.GetPropertyValuesOfElements"
TAPIR = "GetPropertyValuesOfElements"
TAPIR_PROPERTY_ERROR = {"error": {"code": -2130312908, "message": "Not available or not evaluated property"}}
TAPIR_ELEMENT_ERROR = {"error": {"code": -2130313115, "message": "Failed to get property values for element"}}
OFFICIAL_PROPERTY_ERROR = {"error": {"code": 7201, "message": "Property not found"}}
NOT_SUPPORTED = {"error": {"code": 7203, "message": "Element not supported"}}
NOT_FOUND = {"error": {"code": 7204, "message": "Element not found"}}


def _guid(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


WALL, TEXT, GONE = _guid(1), _guid(2), _guid(3)
HEIGHT, WIDTH = _guid(101), _guid(102)


def _wrap(entry: dict) -> dict:
    return entry if "error" in entry else {"propertyValue": entry}


def _official(*elements: list[dict] | dict) -> dict:
    """Elements are lists of property values (or property errors), or an element-level error."""
    return {
        "propertyValuesForElements": [
            element if isinstance(element, dict) else {"propertyValues": [_wrap(entry) for entry in element]}
            for element in elements
        ]
    }


def _tapir(*elements: list[str | dict] | dict) -> dict:
    """Elements are lists of display strings (or property errors), or an element-level error."""
    return {
        "propertyValuesForElements": [
            element
            if isinstance(element, dict)
            else {
                "propertyValues": [
                    _wrap(entry) if isinstance(entry, dict) else {"propertyValue": {"value": entry}}
                    for entry in element
                ]
            }
            for element in elements
        ]
    }


def _length(value: float) -> dict:
    return {"type": "length", "status": "normal", "value": value}


def test_normal_length_is_si(fake_archicad):
    fake_archicad.on_tapir_command(OFFICIAL, _official([_length(0.3)]))

    table = read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert table.cells == {WALL: {HEIGHT: Cell("normal", 0.3, "length")}}
    assert table.unreadable == ()
    assert UNITS == {"length": "m", "area": "m²", "volume": "m³", "angle": "rad"}


def test_request_names_elements_and_properties(fake_archicad):
    fake_archicad.on_tapir_command(OFFICIAL, _official([_length(3), _length(2)]))

    read_property_values(fake_archicad, [WALL], [HEIGHT, WIDTH])

    assert fake_archicad.calls == [
        (
            OFFICIAL,
            {
                "elements": [{"elementId": {"guid": WALL}}],
                "properties": [{"propertyId": {"guid": HEIGHT}}, {"propertyId": {"guid": WIDTH}}],
            },
        )
    ]


def test_statuses_are_kept_apart(fake_archicad):
    fake_archicad.on_tapir_command(
        OFFICIAL,
        _official(
            [
                {"type": "number", "status": "userUndefined"},
                {"type": "string", "status": "notAvailable"},
                {"type": "boolean", "status": "notEvaluated"},
            ]
        ),
    )

    cells = read_property_values(fake_archicad, [WALL], [_guid(1), _guid(2), _guid(3)]).cells[WALL]

    assert list(cells.values()) == [
        Cell("userUndefined", None, "number"),
        Cell("notAvailable", None, "string"),
        Cell("notEvaluated", None, "boolean"),
    ]


def test_enum_display_values(fake_archicad):
    non_localized = {"type": "nonLocalizedValue", "nonLocalizedValue": "Straight"}
    display = {"type": "displayValue", "displayValue": "Uniform"}
    fake_archicad.on_tapir_command(
        OFFICIAL,
        _official(
            [
                {"type": "singleEnum", "status": "normal", "value": non_localized},
                {"type": "singleEnum", "status": "normal", "value": display},
            ]
        ),
    )

    cells = read_property_values(fake_archicad, [WALL], [HEIGHT, WIDTH]).cells[WALL]

    assert cells[HEIGHT] == Cell("normal", "Straight", "singleEnum")
    assert cells[WIDTH] == Cell("normal", "Uniform", "singleEnum")


def test_multi_enum_normal_value_is_a_list_of_display_values(fake_archicad):
    value = [
        {"enumValueId": {"type": "displayValue", "displayValue": "A"}},
        {"enumValueId": {"type": "nonLocalizedValue", "nonLocalizedValue": "B"}},
    ]
    fake_archicad.on_tapir_command(OFFICIAL, _official([{"type": "multiEnum", "status": "normal", "value": value}]))

    table = read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert table.cells[WALL][HEIGHT] == Cell("normal", ["A", "B"], "multiEnum")


def test_failed_official_read_falls_back_to_tapir_strings(fake_archicad):
    # The wall reads fine; the text element has one failing property.
    fake_archicad.on_tapir_command(
        OFFICIAL,
        _official(
            [_length(3), {"type": "boolean", "status": "normal", "value": True}], [_length(2), OFFICIAL_PROPERTY_ERROR]
        ),
    )
    fake_archicad.on_tapir_command(TAPIR, _tapir(["2,000", "gerade"]))

    table = read_property_values(fake_archicad, [WALL, TEXT], [HEIGHT, WIDTH])

    assert table.cells[WALL] == {HEIGHT: Cell("normal", 3, "length"), WIDTH: Cell("normal", True, "boolean")}
    assert table.cells[TEXT] == {
        HEIGHT: Cell("normal", "2,000", "string", typed=False),
        WIDTH: Cell("normal", "gerade", "string", typed=False),
    }
    assert table.unreadable == ()
    # Only the failed element goes to Tapir, with all requested properties.
    assert fake_archicad.calls[1] == (
        TAPIR,
        {
            "elements": [{"elementId": {"guid": TEXT}}],
            "properties": [{"propertyId": {"guid": HEIGHT}}, {"propertyId": {"guid": WIDTH}}],
        },
    )


def test_drafting_element_rejected_by_official_is_read_through_tapir(fake_archicad):
    fake_archicad.on_tapir_command(OFFICIAL, _official([_length(3)], NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, _tapir(["Hello"]))

    table = read_property_values(fake_archicad, [WALL, TEXT], [HEIGHT])

    assert table.cells[WALL][HEIGHT].typed
    assert table.cells[TEXT] == {HEIGHT: Cell("normal", "Hello", "string", typed=False)}


def test_tapir_property_error_is_not_available(fake_archicad):
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, _tapir([TAPIR_PROPERTY_ERROR]))

    table = read_property_values(fake_archicad, [TEXT], [HEIGHT])

    assert table.cells[TEXT][HEIGHT] == Cell("notAvailable", None, "string", typed=False)
    assert table.unreadable == ()


def test_tapir_undefined_marker_is_user_undefined(fake_archicad):
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, _tapir(["<Undefined>", "<undefined>"]))

    cells = read_property_values(fake_archicad, [TEXT], [HEIGHT, WIDTH]).cells[TEXT]

    assert cells[HEIGHT] == Cell("userUndefined", None, "string", typed=False)
    # Only the exact marker counts; any other string stays a string.
    assert cells[WIDTH] == Cell("normal", "<undefined>", "string", typed=False)


def test_tapir_strings_are_never_parsed(fake_archicad):
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, _tapir(["3,000", "90,00°"]))

    cells = read_property_values(fake_archicad, [TEXT], [HEIGHT, WIDTH]).cells[TEXT]

    assert [cell.value for cell in cells.values()] == ["3,000", "90,00°"]


def test_element_unreadable_in_both_is_listed(fake_archicad):
    fake_archicad.on_tapir_command(OFFICIAL, _official([_length(3)], NOT_FOUND))
    fake_archicad.on_tapir_command(TAPIR, _tapir(TAPIR_ELEMENT_ERROR))

    table = read_property_values(fake_archicad, [WALL, GONE], [HEIGHT])

    assert table.unreadable == (GONE,)
    assert GONE not in table.cells
    assert WALL in table.cells


def test_no_tapir_call_when_the_official_read_is_clean(fake_archicad):
    fake_archicad.on_tapir_command(OFFICIAL, _official([{"type": "number", "status": "userUndefined"}]))

    read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert [command for command, _ in fake_archicad.calls] == [OFFICIAL]


def test_no_elements_makes_no_call(fake_archicad):
    table = read_property_values(fake_archicad, [], [HEIGHT])

    assert table.cells == {}
    assert table.unreadable == ()
    assert fake_archicad.calls == []


def test_official_api_error_names_the_read_that_failed(fake_archicad):
    error = StandardCommandUnavailable("boom")

    def failing(command, parameters=None):
        raise error

    fake_archicad.core.post_command = failing

    with pytest.raises(ToolError) as raised:
        read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert str(raised.value) == "Reading the property values failed in the official API (boom)."
    assert raised.value.__suppress_context__


def test_tapir_api_error_names_the_read_that_failed(fake_archicad):
    error = CommandTimeoutError("boom")
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))

    def failing(command, parameters=None):
        raise error

    fake_archicad.core.post_tapir_command = failing

    with pytest.raises(ToolError) as raised:
        read_property_values(fake_archicad, [TEXT], [HEIGHT])

    assert str(raised.value) == "Reading the property values failed in the Tapir fallback (boom)."


def test_short_official_answer_is_an_error(fake_archicad):
    fake_archicad.on_tapir_command(OFFICIAL, _official([_length(3)]))

    with pytest.raises(ToolError, match="1 entries for 2 elements"):
        read_property_values(fake_archicad, [WALL, TEXT], [HEIGHT])
