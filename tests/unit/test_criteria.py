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
    "untyped": Cell("normal", "3,000", "string", typed=False),
    "untypedNotAvailable": Cell("notAvailable", None, "string", typed=False),
    "untypedUserUndefined": Cell("userUndefined", None, "string", typed=False),
}
KINDS = list(CELLS)
COMPARISONS = ["Equal", "NotEqual", "Less", "LessOrEqual", "Greater", "GreaterOrEqual", "Contains"]


def _criterion(operator: str, value=None) -> Criterion:
    return Criterion(property={"guid": GUID}, operator=operator, value=value)


def _row(operator: str, value, *expected):
    return pytest.param(operator, value, dict(zip(KINDS, expected)), id=operator)


# Columns: normal, userUndefined, notAvailable, notEvaluated, untyped, untypedNotAvailable, untypedUserUndefined
@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        _row("Equal", 3.0, True, False, False, False, None, False, False),
        _row("NotEqual", 4.0, True, False, False, False, None, False, False),
        _row("Less", 5.0, True, False, False, False, None, False, False),
        _row("LessOrEqual", 3.0, True, False, False, False, None, False, False),
        _row("Greater", 2.0, True, False, False, False, None, False, False),
        _row("GreaterOrEqual", 3.0, True, False, False, False, None, False, False),
        _row("HasValue", None, True, False, False, False, None, None, None),
        _row("IsUserUndefined", None, False, True, False, False, None, None, None),
        _row("IsNotAvailable", None, False, False, True, False, None, None, None),
    ],
)
def test_every_operator_on_every_cell_kind(operator, value, expected):
    assert {kind: matches(cell, _criterion(operator, value)) for kind, cell in CELLS.items()} == expected


TEXT_CELLS = {
    "normal": Cell("normal", "Brandwand", "string"),
    "userUndefined": Cell("userUndefined", None, "string"),
    "notAvailable": Cell("notAvailable", None, "string"),
    "notEvaluated": Cell("notEvaluated", None, "string"),
    "untyped": Cell("normal", "Brandwand", "string", typed=False),
    "untypedNotAvailable": Cell("notAvailable", None, "string", typed=False),
    "untypedUserUndefined": Cell("userUndefined", None, "string", typed=False),
}


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        _row("Contains", "WAND", True, False, False, False, True, False, False),
        _row("Equal", "Brandwand", True, False, False, False, True, False, False),
        _row("NotEqual", "Decke", True, False, False, False, True, False, False),
    ],
)
def test_text_operators_on_every_cell_kind(operator, value, expected):
    assert {kind: matches(cell, _criterion(operator, value)) for kind, cell in TEXT_CELLS.items()} == expected


@pytest.mark.parametrize("operator", ["Equal", "NotEqual", "Greater", "GreaterOrEqual", "Less", "LessOrEqual"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_rejected(operator, value):
    with pytest.raises(ToolError) as error:
        matches(CELLS["normal"], _criterion(operator, value))
    assert GUID in str(error.value)
    assert operator in str(error.value)
    assert "nan" not in str(error.value).lower()
    assert "inf" not in str(error.value).lower()


def test_undefined_never_matches_not_equal():
    for kind, cell in CELLS.items():
        if cell.status != "normal":
            assert matches(cell, _criterion("NotEqual", 3.0 if cell.typed else "x")) is False, kind


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


def test_number_equality_tolerates_float_noise():
    cell = Cell("normal", 0.30000000000000004, "length")
    assert matches(cell, _criterion("Equal", 0.3)) is True
    assert matches(cell, _criterion("NotEqual", 0.3)) is False
    assert matches(cell, _criterion("Equal", 0.31)) is False


@pytest.mark.parametrize("value_type", ["length", "area", "volume", "angle", "number", "integer"])
def test_all_numeric_types_compare_as_numbers(value_type):
    assert matches(Cell("normal", 2, value_type), _criterion("Less", 3)) is True


def test_boolean_equality():
    cell = Cell("normal", True, "boolean")
    assert matches(cell, _criterion("Equal", True)) is True
    assert matches(cell, _criterion("NotEqual", True)) is False
    assert matches(cell, _criterion("Equal", False)) is False


@pytest.mark.parametrize("value_type", ["string", "singleEnum"])
def test_text_equal_is_exact_and_contains_is_a_case_insensitive_substring(value_type):
    cell = Cell("normal", "Brandwand F90", value_type)
    assert matches(cell, _criterion("Equal", "Brandwand F90")) is True
    assert matches(cell, _criterion("Equal", "brandwand f90")) is False
    assert matches(cell, _criterion("NotEqual", "Brandwand")) is True
    assert matches(cell, _criterion("Contains", "BRANDWAND")) is True
    assert matches(cell, _criterion("Contains", "Decke")) is False


@pytest.mark.parametrize("value_type", ["stringList", "multiEnum"])
def test_contains_on_multi_enum_means_one_item_equals(value_type):
    cell = Cell("normal", ["Rohbau", "Brandschutz"], value_type)
    assert matches(cell, _criterion("Contains", "Brandschutz")) is True
    assert matches(cell, _criterion("Contains", "brandschutz")) is False
    assert matches(cell, _criterion("Contains", "Brand")) is False
    assert matches(Cell("normal", [], value_type), _criterion("Contains", "Rohbau")) is False


def test_contains_on_non_normal_cell_is_false():
    assert matches(Cell("userUndefined", None, "string"), _criterion("Contains", "x")) is False
    assert matches(Cell("notAvailable", None, "multiEnum"), _criterion("Contains", "x")) is False


@pytest.mark.parametrize(
    ("value_type", "operator", "value"),
    [
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
    ],
)
def test_type_mismatch_raises(value_type, operator, value):
    cell = Cell("normal", None, value_type)
    with pytest.raises(ToolError) as error:
        matches(cell, _criterion(operator, value))
    assert GUID in str(error.value)
    assert value_type in str(error.value)
    assert operator in str(error.value)


def test_type_mismatch_raises_on_every_status_and_does_not_echo_the_value():
    for status in ("normal", "userUndefined", "notAvailable", "notEvaluated"):
        cell = Cell(status, "x" if status == "normal" else None, "string")
        with pytest.raises(
            ToolError, match=r"Greater needs a numeric property; property \S+ holds string values\."
        ) as e:
            matches(cell, _criterion("Greater", "secret-value"))
        assert "secret-value" not in str(e.value)


def test_untyped_numeric_comparison_is_undecided():
    for operator in ("Equal", "NotEqual", "Less", "LessOrEqual", "Greater", "GreaterOrEqual"):
        assert matches(CELLS["untyped"], _criterion(operator, 3.0)) is None


def test_untyped_ordering_is_undecided_even_with_a_string():
    assert matches(CELLS["untyped"], _criterion("Greater", "3,000")) is None


def test_untyped_string_equality_compares_the_display_string():
    cell = Cell("normal", "gerade", "string", typed=False)
    assert matches(cell, _criterion("Equal", "gerade")) is True
    assert matches(cell, _criterion("Equal", "Gerade")) is False
    assert matches(cell, _criterion("NotEqual", "gerade")) is False
    assert matches(cell, _criterion("NotEqual", "kurve")) is True


def test_untyped_contains_is_a_case_insensitive_substring():
    cell = Cell("normal", "Gerade 90,00°", "string", typed=False)
    assert matches(cell, _criterion("Contains", "GERADE")) is True
    assert matches(cell, _criterion("Contains", "90,")) is True
    assert matches(cell, _criterion("Contains", "45")) is False
    assert matches(cell, _criterion("Contains", 90)) is None


def test_is_default_is_refused_on_the_fallback_path():
    criterion = _criterion("IsDefault")
    with pytest.raises(ToolError, match="IsDefault needs Archicad's own criteria evaluation"):
        ensure_supported(criterion)
    with pytest.raises(ToolError, match="IsDefault needs Archicad's own criteria evaluation"):
        matches(CELLS["normal"], criterion)


def test_ensure_supported_accepts_everything_else():
    for operator, value in (("Equal", 1), ("HasValue", None), ("Contains", "x"), ("IsNotAvailable", None)):
        ensure_supported(_criterion(operator, value))


@pytest.mark.parametrize("operator", COMPARISONS)
def test_comparison_operators_require_a_value(operator):
    with pytest.raises(ValidationError, match="needs a value"):
        Criterion(property={"guid": GUID}, operator=operator)


@pytest.mark.parametrize("operator", ["HasValue", "IsUserUndefined", "IsNotAvailable", "IsDefault"])
def test_status_operators_forbid_a_value(operator):
    with pytest.raises(ValidationError, match="takes no value"):
        Criterion(property={"guid": GUID}, operator=operator, value=1)


def test_criterion_rejects_unknown_fields_and_operators():
    with pytest.raises(ValidationError):
        Criterion(property={"guid": GUID}, operator="HasValue", extra=1)
    with pytest.raises(ValidationError):
        Criterion(property={"guid": GUID, "name": "x"}, operator="HasValue")
    with pytest.raises(ValidationError):
        Criterion(property={"guid": GUID}, operator="Between", value=1)
