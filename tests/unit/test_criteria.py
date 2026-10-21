import math

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from tapir_archicad_mcp.criteria import Criterion, ensure_supported, matches
from tapir_archicad_mcp.property_values import Cell

GUID = "00000000-0000-0000-0000-000000000101"
CELLS = {
    "normal": Cell("normal", 3.0, "length"),
    "userUndefined": Cell("userUndefined", None, "length"),
    "notAvailable": Cell("notAvailable", None, "length"),
    "notEvaluated": Cell("notEvaluated", None, "length"),
    "fallback": Cell("normal", "3,000", "length", typed=False),
    "fallbackNotAvailable": Cell("notAvailable", None, "length", typed=False),
    "fallbackUserUndefined": Cell("userUndefined", None, "length", typed=False),
}
CELL_NAMES = list(CELLS)
COMPARISONS = ["Equal", "NotEqual", "Less", "LessOrEqual", "Greater", "GreaterOrEqual", "Contains"]
NUMERIC_COMPARISONS = ["Equal", "NotEqual", "Less", "LessOrEqual", "Greater", "GreaterOrEqual"]


def _criterion(operator: str, value=None) -> Criterion:
    return Criterion(property={"guid": GUID}, operator=operator, value=value)


def _row(operator: str, value, *expected):
    return pytest.param(operator, value, dict(zip(CELL_NAMES, expected)), id=operator)


# Columns: normal, userUndefined, notAvailable, notEvaluated, then the same three kinds of status on fallback cells.
@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        _row("Equal", 3.0, True, False, False, False, None, None, None),
        _row("NotEqual", 4.0, True, False, False, False, None, None, None),
        _row("Less", 5.0, True, False, False, False, None, None, None),
        _row("LessOrEqual", 3.0, True, False, False, False, None, None, None),
        _row("Greater", 2.0, True, False, False, False, None, None, None),
        _row("GreaterOrEqual", 3.0, True, False, False, False, None, None, None),
        _row("HasValue", None, True, False, False, False, None, None, None),
        _row("IsUserUndefined", None, False, True, False, False, None, None, None),
        _row("IsNotAvailable", None, False, False, True, False, None, None, None),
    ],
)
def test_every_operator_on_every_cell_kind(operator, value, expected):
    assert {name: matches(cell, _criterion(operator, value)) for name, cell in CELLS.items()} == expected


TEXT_CELLS = {
    "normal": Cell("normal", "Brandwand", "string"),
    "userUndefined": Cell("userUndefined", None, "string"),
    "notAvailable": Cell("notAvailable", None, "string"),
    "notEvaluated": Cell("notEvaluated", None, "string"),
    "fallback": Cell("normal", "Brandwand", "string", typed=False),
    "fallbackNotAvailable": Cell("notAvailable", None, "string", typed=False),
    "fallbackUserUndefined": Cell("userUndefined", None, "string", typed=False),
}


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        _row("Contains", "WAND", True, False, False, False, True, None, None),
        _row("Equal", "Brandwand", True, False, False, False, True, None, None),
        _row("NotEqual", "Decke", True, False, False, False, True, None, None),
    ],
)
def test_text_operators_on_every_cell_kind(operator, value, expected):
    assert {name: matches(cell, _criterion(operator, value)) for name, cell in TEXT_CELLS.items()} == expected


@pytest.mark.parametrize("operator", ["Equal", "NotEqual", "Contains"])
@pytest.mark.parametrize("status", ["userUndefined", "notAvailable", "notEvaluated"])
def test_fallback_cell_without_a_value_decides_nothing(operator, status):
    cell = Cell(status, None, "string", typed=False)
    assert matches(cell, _criterion(operator, "x")) is None


@pytest.mark.parametrize("operator", NUMERIC_COMPARISONS)
def test_not_available_fallback_cell_decides_nothing_for_every_comparison(operator):
    assert matches(CELLS["fallbackNotAvailable"], _criterion(operator, 3.0)) is None


@pytest.mark.parametrize("operator", ["Equal", "NotEqual"])
def test_fallback_enum_cell_decides_nothing(operator):
    cell = Cell("normal", "gerade", "singleEnum", typed=False)
    assert matches(cell, _criterion(operator, "gerade")) is None
    assert matches(Cell("normal", "gerade", "singleEnum"), _criterion(operator, "gerade")) is (operator == "Equal")


def test_fallback_contains_on_a_list_cell_decides_nothing():
    cell = Cell("normal", "A, B", "multiEnum", typed=False)
    assert matches(cell, _criterion("Contains", "A")) is None


@pytest.mark.parametrize("operator", NUMERIC_COMPARISONS)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 10**400, -(10**400)])
def test_non_finite_numbers_are_rejected(operator, value):
    for cell in (CELLS["normal"], Cell("normal", 3, "integer")):
        with pytest.raises(ToolError) as error:
            matches(cell, _criterion(operator, value))
        assert GUID in str(error.value)
        assert operator in str(error.value)
        assert str(value) not in str(error.value)
        assert "finite" in str(error.value)


def test_undefined_never_matches_not_equal():
    for name, cell in CELLS.items():
        if cell.typed and cell.status != "normal":
            assert matches(cell, _criterion("NotEqual", 3.0)) is False, name


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        ("Equal", 3.0, True),
        ("Equal", 3, True),
        ("NotEqual", 3.0, False),
        ("Less", 3.0, False),
        ("LessOrEqual", 3.0, True),
        ("Greater", 3.0, False),
        ("GreaterOrEqual", 3.0, True),
        ("Greater", 2, True),
    ],
)
def test_number_comparisons(operator, value, expected):
    assert matches(CELLS["normal"], _criterion(operator, value)) is expected


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        ("Equal", True),
        ("NotEqual", False),
        ("Less", False),
        ("LessOrEqual", True),
        ("Greater", False),
        ("GreaterOrEqual", True),
    ],
)
def test_float_noise_counts_as_equal_for_all_six_operators(operator, expected):
    cell = Cell("normal", 0.1 + 0.2, "number")
    assert matches(cell, _criterion(operator, 0.3)) is expected


def test_number_equality_still_tells_real_differences_apart():
    cell = Cell("normal", 0.30000000000000004, "length")
    assert matches(cell, _criterion("Equal", 0.31)) is False
    assert matches(cell, _criterion("Less", 0.31)) is True
    assert matches(cell, _criterion("Greater", 0.29)) is True


def test_archicad_rounding_of_a_right_angle_is_tolerated():
    cell = Cell("normal", 1.570796327, "angle")
    assert matches(cell, _criterion("Equal", math.pi / 2)) is True
    assert matches(cell, _criterion("LessOrEqual", math.pi / 2)) is True
    assert matches(cell, _criterion("GreaterOrEqual", math.pi / 2)) is True
    assert matches(cell, _criterion("Less", math.pi / 2)) is False
    assert matches(cell, _criterion("Greater", math.pi / 2)) is False


def test_a_tenth_of_a_degree_is_not_zero():
    cell = Cell("normal", 0.001745329, "angle")
    assert matches(cell, _criterion("Equal", math.radians(0.1))) is True
    assert matches(cell, _criterion("Equal", 0)) is False


def test_integers_compare_exactly():
    cell = Cell("normal", 1000000001, "integer")
    assert matches(cell, _criterion("NotEqual", 1000000000)) is True
    assert matches(cell, _criterion("Equal", 1000000000)) is False
    assert matches(cell, _criterion("Greater", 1000000000)) is True
    assert matches(cell, _criterion("LessOrEqual", 1000000000)) is False
    assert matches(cell, _criterion("Equal", 1000000001)) is True


@pytest.mark.parametrize("value_type", ["length", "area", "volume", "angle", "number", "integer"])
def test_all_numeric_types_compare_as_numbers(value_type):
    assert matches(Cell("normal", 2, value_type), _criterion("Less", 3)) is True


def test_zero_is_a_value_like_any_other():
    cell = Cell("normal", 0, "length")
    assert matches(cell, _criterion("Equal", 0)) is True
    assert matches(cell, _criterion("NotEqual", 0)) is False
    assert matches(cell, _criterion("HasValue")) is True
    assert matches(cell, _criterion("Less", 1)) is True
    assert matches(Cell("normal", 3.0, "length"), _criterion("Equal", 0)) is False


def test_boolean_equality():
    cell = Cell("normal", True, "boolean")
    assert matches(cell, _criterion("Equal", True)) is True
    assert matches(cell, _criterion("NotEqual", True)) is False
    assert matches(cell, _criterion("Equal", False)) is False


def test_false_is_a_value_like_any_other():
    cell = Cell("normal", False, "boolean")
    assert matches(cell, _criterion("Equal", False)) is True
    assert matches(cell, _criterion("NotEqual", False)) is False
    assert matches(cell, _criterion("HasValue")) is True


@pytest.mark.parametrize("value_type", ["string", "singleEnum"])
def test_text_equal_is_exact_and_contains_is_a_case_insensitive_substring(value_type):
    cell = Cell("normal", "Brandwand F90", value_type)
    assert matches(cell, _criterion("Equal", "Brandwand F90")) is True
    assert matches(cell, _criterion("Equal", "brandwand f90")) is False
    assert matches(cell, _criterion("NotEqual", "Brandwand")) is True
    assert matches(cell, _criterion("Contains", "BRANDWAND")) is True
    assert matches(cell, _criterion("Contains", "Decke")) is False


def test_contains_folds_german_case():
    cell = Cell("normal", "Außenwand", "string")
    assert matches(cell, _criterion("Contains", "AUSSEN")) is True
    assert matches(Cell("normal", "Außenwand", "string", typed=False), _criterion("Contains", "AUSSEN")) is True


def test_empty_text_cell_is_a_value():
    cell = Cell("normal", "", "string")
    assert matches(cell, _criterion("Equal", "")) is True
    assert matches(cell, _criterion("Contains", "x")) is False
    assert matches(cell, _criterion("HasValue")) is True


@pytest.mark.parametrize("value_type", ["stringList", "multiEnum"])
def test_contains_on_a_list_means_one_item_equals(value_type):
    cell = Cell("normal", ["Rohbau", "Brandschutz"], value_type)
    assert matches(cell, _criterion("Contains", "Brandschutz")) is True
    assert matches(cell, _criterion("Contains", "brandschutz")) is False
    assert matches(cell, _criterion("Contains", "Brand")) is False
    assert matches(Cell("normal", [], value_type), _criterion("Contains", "Rohbau")) is False


def test_contains_on_non_normal_cell_is_false():
    assert matches(Cell("userUndefined", None, "string"), _criterion("Contains", "x")) is False
    assert matches(Cell("notAvailable", None, "multiEnum"), _criterion("Contains", "x")) is False


WRONG_USE = [
    ("string", "Greater", 1),
    ("singleEnum", "Less", "a"),
    ("boolean", "GreaterOrEqual", True),
    ("multiEnum", "Equal", "a"),
    ("stringList", "NotEqual", "a"),
    ("length", "Contains", "3"),
    ("boolean", "Contains", "true"),
    ("length", "Equal", "3"),
    ("length", "Greater", True),
    ("integer", "Equal", False),
    ("boolean", "Equal", 1),
    ("boolean", "Equal", 1.0),
    ("string", "Equal", 3),
    ("string", "Contains", 3),
    ("stringList", "Contains", 3),
    ("lengthList", "Equal", 1.0),
]


@pytest.mark.parametrize("typed", [True, False])
@pytest.mark.parametrize(("value_type", "operator", "value"), WRONG_USE)
def test_type_mismatch_raises(value_type, operator, value, typed):
    cell = Cell("normal", None, value_type, typed=typed)
    with pytest.raises(ToolError) as error:
        matches(cell, _criterion(operator, value))
    assert GUID in str(error.value)
    assert value_type in str(error.value)
    assert operator in str(error.value)


@pytest.mark.parametrize("typed", [True, False])
def test_type_mismatch_raises_on_every_status_and_does_not_echo_the_value(typed):
    for status in ("normal", "userUndefined", "notAvailable", "notEvaluated"):
        cell = Cell(status, "x" if status == "normal" else None, "string", typed=typed)
        with pytest.raises(
            ToolError,
            match=rf"Greater cannot be used on property {GUID}, which holds string values; it needs a numeric property",
        ) as e:
            matches(cell, _criterion("Greater", "secret-value"))
        assert "secret-value" not in str(e.value)
    with pytest.raises(ToolError, match=rf"The value for Equal on property {GUID} must be a number") as e:
        matches(Cell("normal", 3.0, "length", typed=typed), _criterion("Equal", "secret-value"))
    assert "secret-value" not in str(e.value)


def test_operator_mismatch_says_what_to_use_instead():
    with pytest.raises(ToolError) as unknown_type:
        matches(Cell("normal", [1.0], "lengthList"), _criterion("Equal", 1.0))
    assert "use HasValue, IsUserUndefined or IsNotAvailable." in str(unknown_type.value)
    with pytest.raises(ToolError) as list_property:
        matches(Cell("normal", ["a"], "multiEnum"), _criterion("Equal", "a"))
    assert "Use Contains to test list items." in str(list_property.value)
    with pytest.raises(ToolError) as scalar_property:
        matches(Cell("normal", 3.0, "length"), _criterion("Contains", "3"))
    assert "Use another operator or property." in str(scalar_property.value)
    assert "Use Contains" not in str(scalar_property.value)


def test_fallback_numeric_comparison_is_undecided():
    for operator in NUMERIC_COMPARISONS:
        assert matches(CELLS["fallback"], _criterion(operator, 3.0)) is None


def test_fallback_ordering_with_a_string_raises_like_a_typed_cell():
    with pytest.raises(ToolError, match="The value for Greater"):
        matches(CELLS["fallback"], _criterion("Greater", "3,000"))


def test_fallback_string_equality_compares_the_display_string():
    cell = Cell("normal", "gerade", "string", typed=False)
    assert matches(cell, _criterion("Equal", "gerade")) is True
    assert matches(cell, _criterion("Equal", "Gerade")) is False
    assert matches(cell, _criterion("NotEqual", "gerade")) is False
    assert matches(cell, _criterion("NotEqual", "kurve")) is True


def test_fallback_contains_is_a_case_insensitive_substring():
    cell = Cell("normal", "Gerade 90,00°", "string", typed=False)
    assert matches(cell, _criterion("Contains", "GERADE")) is True
    assert matches(cell, _criterion("Contains", "90,")) is True
    assert matches(cell, _criterion("Contains", "45")) is False


def test_is_default_is_refused():
    criterion = _criterion("IsDefault")
    message = "IsDefault is not supported because the installed Tapir cannot evaluate it. Use another operator."
    with pytest.raises(ToolError) as refused:
        ensure_supported(criterion)
    assert str(refused.value) == message
    with pytest.raises(ToolError, match="IsDefault is not supported"):
        matches(CELLS["normal"], criterion)


def test_ensure_supported_accepts_everything_else():
    for operator, value in (("Equal", 1), ("HasValue", None), ("Contains", "x"), ("IsNotAvailable", None)):
        ensure_supported(_criterion(operator, value))


@pytest.mark.parametrize("operator", COMPARISONS)
def test_comparison_operators_require_a_value(operator):
    with pytest.raises(ValidationError) as error:
        Criterion(property={"guid": GUID}, operator=operator)
    assert f"{operator} needs a value; add 'value'." in str(error.value)


@pytest.mark.parametrize("operator", ["HasValue", "IsUserUndefined", "IsNotAvailable", "IsDefault"])
def test_status_operators_forbid_a_value(operator):
    with pytest.raises(ValidationError) as error:
        Criterion(property={"guid": GUID}, operator=operator, value=1)
    assert f"{operator} takes no value; remove 'value'." in str(error.value)


def test_contains_rejects_an_empty_text():
    with pytest.raises(ValidationError, match="Contains needs a non-empty text value."):
        Criterion(property={"guid": GUID}, operator="Contains", value="")
    assert Criterion(property={"guid": GUID}, operator="Equal", value="").value == ""


def test_validation_errors_do_not_echo_the_input():
    with pytest.raises(ValidationError) as error:
        Criterion(property={"guid": GUID}, operator="HasValue", value="secret-value")
    assert "secret-value" not in str(error.value)
    with pytest.raises(ValidationError) as unknown_operator:
        Criterion(property={"guid": GUID}, operator="secret-operator", value=1)
    assert "secret-operator" not in str(unknown_operator.value)
    with pytest.raises(ValidationError) as unknown_field:
        Criterion(property={"guid": GUID, "name": "secret-name"}, operator="HasValue")
    assert "secret-name" not in str(unknown_field.value)


def test_criterion_rejects_unknown_fields_and_operators():
    with pytest.raises(ValidationError):
        Criterion(property={"guid": GUID}, operator="HasValue", extra=1)
    with pytest.raises(ValidationError):
        Criterion(property={"guid": GUID, "name": "x"}, operator="HasValue")
    with pytest.raises(ValidationError):
        Criterion(property={"guid": GUID}, operator="Between", value=1)
