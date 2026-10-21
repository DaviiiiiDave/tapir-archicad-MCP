import pytest
from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad.errors import CommandTimeoutError, StandardCommandUnavailable

from tapir_archicad_mcp.property_values import UNITS, Cell, read_property_values

DEFINITIONS = "API.GetDetailsOfProperties"
OFFICIAL = "API.GetPropertyValuesOfElements"
TAPIR = "GetPropertyValuesOfElements"
TAPIR_PROPERTY_ERROR = {"error": {"code": -2130312908, "message": "Not available or not evaluated property"}}
TAPIR_ELEMENT_ERROR = {"error": {"code": -2130313115, "message": "Failed to get property values for element"}}
# Code 7201 is from the ErrorItem schema, not recorded live.
OFFICIAL_PROPERTY_ERROR = {"error": {"code": 7201, "message": "Property not found"}}
NOT_SUPPORTED = {"error": {"code": 7203, "message": "Element not supported"}}
NOT_FOUND = {"error": {"code": 7204, "message": "Element not found"}}
UNEXPECTED = "answered in an unexpected shape"
NO_VALUES = (
    ", so no values were returned. Retry; if it repeats, the installed Archicad or Tapir may not match this server."
)
TAPIR_SOURCE = "Tapir (used for elements the official API cannot read)"

STRAIGHT = {"type": "nonLocalizedValue", "nonLocalizedValue": "Straight"}
UNIFORM = {"type": "nonLocalizedValue", "nonLocalizedValue": "Uniform"}
XC1 = {"type": "displayValue", "displayValue": "XC1"}
XC2 = {"type": "displayValue", "displayValue": "XC2"}
WALL_ENUMS = [(STRAIGHT, "gerade"), (UNIFORM, "Uni")]


def _guid(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


WALL, TEXT, MISSING, PARTIAL = _guid(1), _guid(2), _guid(3), _guid(4)
HEIGHT, WIDTH = _guid(101), _guid(102)


def _define(fake_archicad, *properties):
    """Register the definitions the read asks for. A property is (guid, type) or (guid, type, enum values)."""
    entries = []
    for guid, value_type, *enums in properties:
        definition = {"propertyId": {"guid": guid}, "type": value_type}
        if enums:
            definition["possibleEnumValues"] = [
                {"enumValue": {"enumValueId": enum_id, "displayValue": display}} for enum_id, display in enums[0]
            ]
        entries.append({"propertyDefinition": definition})
    fake_archicad.on_tapir_command(DEFINITIONS, {"propertyDefinitions": entries})


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


def _enum(value) -> dict:
    return {"type": "singleEnum", "status": "normal", "value": value}


def test_normal_length_is_si(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official([_length(0.3)]))

    table = read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert table.cells == {WALL: {HEIGHT: Cell("normal", 0.3, "length")}}
    assert table.unreadable == ()
    assert UNITS == {"length": "m", "area": "m²", "volume": "m³", "angle": "rad"}


def test_request_names_elements_and_properties(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"), (WIDTH, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official([_length(3), _length(2)]))

    read_property_values(fake_archicad, [WALL], [HEIGHT, WIDTH])

    assert fake_archicad.calls == [
        (DEFINITIONS, {"properties": [{"propertyId": {"guid": HEIGHT}}, {"propertyId": {"guid": WIDTH}}]}),
        (
            OFFICIAL,
            {
                "elements": [{"elementId": {"guid": WALL}}],
                "properties": [{"propertyId": {"guid": HEIGHT}}, {"propertyId": {"guid": WIDTH}}],
            },
        ),
    ]


def test_definitions_are_requested_once_per_property_in_order(fake_archicad):
    _define(fake_archicad, (WIDTH, "length"), (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official([_length(1), _length(2), _length(3)]))

    read_property_values(fake_archicad, [WALL], [WIDTH, HEIGHT, WIDTH])

    assert fake_archicad.calls[0] == (
        DEFINITIONS,
        {"properties": [{"propertyId": {"guid": WIDTH}}, {"propertyId": {"guid": HEIGHT}}]},
    )


def test_unknown_property_is_an_error_naming_its_guid(fake_archicad):
    fake_archicad.on_tapir_command(
        DEFINITIONS,
        {
            "propertyDefinitions": [
                {"propertyDefinition": {"propertyId": {"guid": HEIGHT}, "type": "length"}},
                {"error": {"code": 7201, "message": 'Property not found (property guid: "x")'}},
            ]
        },
    )

    with pytest.raises(ToolError) as raised:
        read_property_values(fake_archicad, [WALL], [HEIGHT, WIDTH])

    assert str(raised.value) == (
        f"Property {WIDTH} does not exist in this project; find property ids with properties_search."
    )
    assert [command for command, _ in fake_archicad.calls] == [DEFINITIONS]


@pytest.mark.parametrize(("guids", "property_guids"), [([], [HEIGHT]), ([WALL], [])])
def test_nothing_to_read_makes_no_call(fake_archicad, guids, property_guids):
    table = read_property_values(fake_archicad, guids, property_guids)

    assert table.cells == {}
    assert table.unreadable == ()
    assert fake_archicad.calls == []


def test_statuses_are_kept_apart(fake_archicad):
    _define(fake_archicad, (_guid(1), "number"), (_guid(2), "string"), (_guid(3), "boolean"))
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


def test_enum_ids_map_to_display_values(fake_archicad):
    # Shapes as recorded on Archicad 29: built-in enums answer with the non-localized id, the project shows "gerade".
    _define(fake_archicad, (HEIGHT, "singleEnum", WALL_ENUMS), (WIDTH, "singleEnum", WALL_ENUMS))
    fake_archicad.on_tapir_command(OFFICIAL, _official([_enum(STRAIGHT), _enum(UNIFORM)]))

    cells = read_property_values(fake_archicad, [WALL], [HEIGHT, WIDTH]).cells[WALL]

    assert cells[HEIGHT] == Cell("normal", "gerade", "singleEnum")
    assert cells[WIDTH] == Cell("normal", "Uni", "singleEnum")


def test_custom_enum_with_display_value_ids(fake_archicad):
    _define(fake_archicad, (HEIGHT, "singleEnum", [(XC1, "XC1"), (XC2, "XC2")]))
    fake_archicad.on_tapir_command(OFFICIAL, _official([_enum(XC2)]))

    cells = read_property_values(fake_archicad, [WALL], [HEIGHT]).cells[WALL]

    assert cells[HEIGHT] == Cell("normal", "XC2", "singleEnum")


def test_multi_enum_maps_each_item(fake_archicad):
    _define(fake_archicad, (HEIGHT, "multiEnum", [(XC1, "XC1"), (XC2, "XC2 (feucht)")]))
    value = [{"enumValueId": XC2}, {"enumValueId": XC1}]
    fake_archicad.on_tapir_command(OFFICIAL, _official([{"type": "multiEnum", "status": "normal", "value": value}]))

    table = read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert table.cells[WALL][HEIGHT] == Cell("normal", ["XC2 (feucht)", "XC1"], "multiEnum")


def test_enum_id_the_definition_does_not_list_is_an_error(fake_archicad):
    _define(fake_archicad, (HEIGHT, "singleEnum", [(XC1, "XC1")]))
    fake_archicad.on_tapir_command(OFFICIAL, _official([_enum(XC2)]))

    with pytest.raises(ToolError) as raised:
        read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert str(raised.value) == (
        f"Property {HEIGHT} returned an enum value that its definition does not list, so it cannot be shown."
    )


def test_enum_ids_match_on_type_and_value(fake_archicad):
    # The same text under the other id type is a different id.
    _define(fake_archicad, (HEIGHT, "singleEnum", [({"type": "nonLocalizedValue", "nonLocalizedValue": "XC1"}, "A")]))
    fake_archicad.on_tapir_command(OFFICIAL, _official([_enum(XC1)]))

    with pytest.raises(ToolError, match="does not list"):
        read_property_values(fake_archicad, [WALL], [HEIGHT])


def test_unlisted_item_of_a_multi_enum_is_an_error(fake_archicad):
    _define(fake_archicad, (HEIGHT, "multiEnum", [(XC1, "XC1")]))
    value = [{"enumValueId": XC1}, {"enumValueId": XC2}]
    fake_archicad.on_tapir_command(OFFICIAL, _official([{"type": "multiEnum", "status": "normal", "value": value}]))

    with pytest.raises(ToolError, match=f"Property {HEIGHT} returned an enum value"):
        read_property_values(fake_archicad, [WALL], [HEIGHT])


def test_property_error_replaces_only_that_cell(fake_archicad):
    # The wall reads fine; the text element has one failing property.
    _define(fake_archicad, (HEIGHT, "length"), (WIDTH, "boolean"))
    fake_archicad.on_tapir_command(
        OFFICIAL,
        _official(
            [_length(3), {"type": "boolean", "status": "normal", "value": True}], [_length(2), OFFICIAL_PROPERTY_ERROR]
        ),
    )
    fake_archicad.on_tapir_command(TAPIR, _tapir(["2,000", "True"]))

    table = read_property_values(fake_archicad, [WALL, TEXT], [HEIGHT, WIDTH])

    assert table.cells[WALL] == {HEIGHT: Cell("normal", 3, "length"), WIDTH: Cell("normal", True, "boolean")}
    assert table.cells[TEXT] == {
        HEIGHT: Cell("normal", 2, "length"),
        WIDTH: Cell("normal", "True", "boolean", typed=False),
    }
    assert table.unreadable == ()
    # Only the failed element goes to Tapir, with all requested properties.
    assert fake_archicad.calls[2] == (
        TAPIR,
        {
            "elements": [{"elementId": {"guid": TEXT}}],
            "properties": [{"propertyId": {"guid": HEIGHT}}, {"propertyId": {"guid": WIDTH}}],
        },
    )


def test_property_error_with_tapir_element_error_keeps_the_typed_cells(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"), (WIDTH, "area"))
    fake_archicad.on_tapir_command(OFFICIAL, _official([_length(2), OFFICIAL_PROPERTY_ERROR]))
    fake_archicad.on_tapir_command(TAPIR, _tapir(TAPIR_ELEMENT_ERROR))

    table = read_property_values(fake_archicad, [TEXT], [HEIGHT, WIDTH])

    assert table.cells[TEXT] == {
        HEIGHT: Cell("normal", 2, "length"),
        WIDTH: Cell("notAvailable", None, "area", typed=False),
    }
    assert table.unreadable == ()


def test_failed_elements_share_one_tapir_call_and_keep_their_own_strings(fake_archicad):
    # WALL reads fine; TEXT fails as a whole, PARTIAL has one failing property.
    _define(fake_archicad, (HEIGHT, "length"), (WIDTH, "length"))
    fake_archicad.on_tapir_command(
        OFFICIAL, _official([_length(3), _length(4)], NOT_SUPPORTED, [OFFICIAL_PROPERTY_ERROR, _length(5)])
    )
    fake_archicad.on_tapir_command(TAPIR, _tapir(["text-h", "text-w"], ["partial-h", "partial-w"]))

    table = read_property_values(fake_archicad, [WALL, TEXT, PARTIAL], [HEIGHT, WIDTH])

    assert [command for command, _ in fake_archicad.calls] == [DEFINITIONS, OFFICIAL, TAPIR]
    assert fake_archicad.calls[2][1]["elements"] == [{"elementId": {"guid": TEXT}}, {"elementId": {"guid": PARTIAL}}]
    assert table.cells[TEXT] == {
        HEIGHT: Cell("normal", "text-h", "length", typed=False),
        WIDTH: Cell("normal", "text-w", "length", typed=False),
    }
    assert table.cells[PARTIAL] == {
        HEIGHT: Cell("normal", "partial-h", "length", typed=False),
        WIDTH: Cell("normal", 5, "length"),
    }
    assert table.cells[WALL] == {HEIGHT: Cell("normal", 3, "length"), WIDTH: Cell("normal", 4, "length")}
    assert list(table.cells) == [WALL, TEXT, PARTIAL]


def test_drafting_element_rejected_by_official_is_read_through_tapir(fake_archicad):
    _define(fake_archicad, (HEIGHT, "string"))
    wall = [{"type": "string", "status": "normal", "value": "x"}]
    fake_archicad.on_tapir_command(OFFICIAL, _official(wall, NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, _tapir(["Hello"]))

    table = read_property_values(fake_archicad, [WALL, TEXT], [HEIGHT])

    assert table.cells[WALL][HEIGHT].typed
    assert table.cells[TEXT] == {HEIGHT: Cell("normal", "Hello", "string", typed=False)}


def test_fallback_cells_carry_the_declared_type_and_stay_untyped(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"), (WIDTH, "singleEnum", WALL_ENUMS))
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, _tapir(["3,000", "gerade"]))

    cells = read_property_values(fake_archicad, [TEXT], [HEIGHT, WIDTH]).cells[TEXT]

    assert cells == {
        HEIGHT: Cell("normal", "3,000", "length", typed=False),
        WIDTH: Cell("normal", "gerade", "singleEnum", typed=False),
    }


def test_tapir_property_error_is_not_available(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, _tapir([TAPIR_PROPERTY_ERROR]))

    table = read_property_values(fake_archicad, [TEXT], [HEIGHT])

    assert table.cells[TEXT][HEIGHT] == Cell("notAvailable", None, "length", typed=False)
    assert table.unreadable == ()


def test_other_tapir_property_error_codes_are_errors(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, _tapir([{"error": {"code": -2130312000, "message": "No access"}}]))

    with pytest.raises(ToolError) as raised:
        read_property_values(fake_archicad, [TEXT], [HEIGHT])

    assert str(raised.value) == (
        f"Reading the property values through {TAPIR_SOURCE} failed (error code -2130312000); no values were returned. "
        "Retry once Archicad responds."
    )


def test_tapir_undefined_marker_is_user_undefined(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"), (WIDTH, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, _tapir(["<Undefined>", "<undefined>"]))

    cells = read_property_values(fake_archicad, [TEXT], [HEIGHT, WIDTH]).cells[TEXT]

    assert cells[HEIGHT] == Cell("userUndefined", None, "length", typed=False)
    # Only the exact marker counts; any other string stays a string.
    assert cells[WIDTH] == Cell("normal", "<undefined>", "length", typed=False)


def test_tapir_strings_are_never_parsed(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"), (WIDTH, "angle"))
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, _tapir(["3,000", "90,00°"]))

    cells = read_property_values(fake_archicad, [TEXT], [HEIGHT, WIDTH]).cells[TEXT]

    assert [cell.value for cell in cells.values()] == ["3,000", "90,00°"]


def test_element_unreadable_in_both_is_listed(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official([_length(3)], NOT_FOUND))
    fake_archicad.on_tapir_command(TAPIR, _tapir(TAPIR_ELEMENT_ERROR))

    table = read_property_values(fake_archicad, [WALL, MISSING], [HEIGHT])

    assert table.unreadable == (MISSING,)
    assert MISSING not in table.cells
    assert WALL in table.cells


def test_no_tapir_call_when_the_official_read_is_clean(fake_archicad):
    _define(fake_archicad, (HEIGHT, "number"))
    fake_archicad.on_tapir_command(OFFICIAL, _official([{"type": "number", "status": "userUndefined"}]))

    read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert [command for command, _ in fake_archicad.calls] == [DEFINITIONS, OFFICIAL]


def _failing(error):
    def failing(command, parameters=None):
        raise error

    return failing


def test_official_api_error_names_the_read_that_failed(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.core.post_command = _failing(StandardCommandUnavailable("boom"))

    with pytest.raises(ToolError) as raised:
        read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert str(raised.value) == (
        "Reading the property values through the official API failed (boom); no values were returned. "
        "Retry once Archicad responds."
    )
    assert raised.value.__suppress_context__


def test_definitions_call_failure_is_wrapped_like_the_other_reads(fake_archicad):
    fake_archicad.core.post_command = _failing(CommandTimeoutError("boom"))

    with pytest.raises(ToolError) as raised:
        read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert str(raised.value).startswith("Reading the property values through the official API failed (boom);")


def test_tapir_api_error_names_the_read_that_failed(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.core.post_tapir_command = _failing(CommandTimeoutError("boom"))

    with pytest.raises(ToolError) as raised:
        read_property_values(fake_archicad, [TEXT], [HEIGHT])

    assert str(raised.value) == (
        f"Reading the property values through {TAPIR_SOURCE} failed (boom); no values were returned. "
        "Retry once Archicad responds."
    )


def test_short_official_answer_is_an_error(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official([_length(3)]))

    with pytest.raises(ToolError) as raised:
        read_property_values(fake_archicad, [WALL, TEXT], [HEIGHT])

    assert str(raised.value) == f"The official API {UNEXPECTED} (returned entries for 1 of 2 elements){NO_VALUES}"


@pytest.mark.parametrize("values", [[_length(1)], [_length(1)] * 3])
def test_wrong_official_value_count_is_an_error(fake_archicad, values):
    _define(fake_archicad, (HEIGHT, "length"), (WIDTH, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official(values))

    with pytest.raises(ToolError) as raised:
        read_property_values(fake_archicad, [WALL], [HEIGHT, WIDTH])

    assert str(raised.value) == (
        f"The official API {UNEXPECTED} (returned values for {len(values)} of 2 properties){NO_VALUES}"
    )


def test_short_tapir_value_list_is_an_error(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"), (WIDTH, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, _tapir(["only one"]))

    with pytest.raises(ToolError) as raised:
        read_property_values(fake_archicad, [TEXT], [HEIGHT, WIDTH])

    assert str(raised.value) == (
        f"Tapir (used for elements the official API cannot read) {UNEXPECTED} (returned values for 1 of 2 properties){NO_VALUES}"
    )


def test_enum_value_without_any_id_is_an_error(fake_archicad):
    _define(fake_archicad, (HEIGHT, "singleEnum", WALL_ENUMS))
    fake_archicad.on_tapir_command(OFFICIAL, _official([_enum({"other": "x"})]))

    with pytest.raises(ToolError, match=r"official API answered in an unexpected shape \(an enum value has no id\)"):
        read_property_values(fake_archicad, [WALL], [HEIGHT])


def test_malformed_official_entry_is_an_error_not_a_fallback(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, {"propertyValuesForElements": [{}]})

    with pytest.raises(ToolError, match="official API.*neither values nor an error"):
        read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert [command for command, _ in fake_archicad.calls] == [DEFINITIONS, OFFICIAL]


def test_malformed_official_value_is_an_error(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, {"propertyValuesForElements": [{"propertyValues": [{}]}]})

    with pytest.raises(ToolError, match="official API.*neither a value nor an error"):
        read_property_values(fake_archicad, [WALL], [HEIGHT])


@pytest.mark.parametrize(
    "value",
    [
        {"status": "normal", "value": 1},
        {"type": "length", "value": 1},
        {"type": "length", "status": "normal"},
        {"type": "length", "status": "unknown"},
    ],
    ids=["no-status", "no-type", "no-value", "unknown-status"],
)
def test_incomplete_official_value_is_a_tool_error(fake_archicad, value):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official([value]))

    with pytest.raises(ToolError, match=f"official API {UNEXPECTED}"):
        read_property_values(fake_archicad, [WALL], [HEIGHT])


def test_multi_enum_item_without_an_id_is_a_tool_error(fake_archicad):
    _define(fake_archicad, (HEIGHT, "multiEnum", [(XC1, "XC1")]))
    value = [{"somethingElse": XC1}]
    fake_archicad.on_tapir_command(OFFICIAL, _official([{"type": "multiEnum", "status": "normal", "value": value}]))

    with pytest.raises(ToolError, match=f"official API {UNEXPECTED}"):
        read_property_values(fake_archicad, [WALL], [HEIGHT])


def test_official_answer_without_entries_is_a_tool_error(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, {})

    with pytest.raises(ToolError, match=f"official API {UNEXPECTED}"):
        read_property_values(fake_archicad, [WALL], [HEIGHT])


def test_tapir_answer_without_entries_is_a_tool_error(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, {})

    with pytest.raises(ToolError, match=f"Tapir.*{UNEXPECTED}"):
        read_property_values(fake_archicad, [TEXT], [HEIGHT])


@pytest.mark.parametrize(
    "definitions",
    [
        {},
        {"propertyDefinitions": []},
        {"propertyDefinitions": [{}]},
        {"propertyDefinitions": [{"propertyDefinition": {"propertyId": {"guid": HEIGHT}}}]},
        {"propertyDefinitions": [{"propertyDefinition": {"type": "singleEnum", "possibleEnumValues": [{}]}}]},
    ],
    ids=["no-list", "short", "empty-entry", "no-type", "malformed-enum"],
)
def test_malformed_definitions_are_a_tool_error(fake_archicad, definitions):
    fake_archicad.on_tapir_command(DEFINITIONS, definitions)

    with pytest.raises(ToolError, match=f"official API {UNEXPECTED}"):
        read_property_values(fake_archicad, [WALL], [HEIGHT])

    assert [command for command, _ in fake_archicad.calls] == [DEFINITIONS]


def test_malformed_tapir_entries_are_errors(fake_archicad):
    _define(fake_archicad, (HEIGHT, "length"))
    fake_archicad.on_tapir_command(OFFICIAL, _official(NOT_SUPPORTED))
    fake_archicad.on_tapir_command(TAPIR, {"propertyValuesForElements": [{}]})

    with pytest.raises(ToolError, match="Tapir.*neither values nor an error"):
        read_property_values(fake_archicad, [TEXT], [HEIGHT])

    fake_archicad.on_tapir_command(TAPIR, {"propertyValuesForElements": [{"propertyValues": [{}]}]})

    with pytest.raises(ToolError, match="Tapir.*neither a value nor an error"):
        read_property_values(fake_archicad, [TEXT], [HEIGHT])
