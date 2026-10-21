"""Criteria semantics: whether one property cell matches one filter criterion.

Filter paths must use this module so that all of them give the same answer.
"""

import math
import operator as ops
from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, model_validator

from tapir_archicad_mcp.property_values import Cell

Operator = Literal[
    "Equal",
    "NotEqual",
    "Less",
    "LessOrEqual",
    "Greater",
    "GreaterOrEqual",
    "Contains",
    "HasValue",
    "IsUserUndefined",
    "IsNotAvailable",
    "IsDefault",
]

# Status operators and the one cell status each one tests.
STATUS_OPERATORS = {"HasValue": "normal", "IsUserUndefined": "userUndefined", "IsNotAvailable": "notAvailable"}
ORDERING = {"Less": ops.lt, "LessOrEqual": ops.le, "Greater": ops.gt, "GreaterOrEqual": ops.ge}

# Archicad rounds values to 9 decimals (a right angle comes back as 1.570796327), so numbers within this are equal.
TOLERANCE = 1e-9

# Official value type -> kind. A type missing here (e.g. lengthList) cannot be compared.
KINDS = {
    **dict.fromkeys(("length", "area", "volume", "angle", "number", "integer"), "number"),
    **dict.fromkeys(("string", "singleEnum"), "text"),
    **dict.fromkeys(("stringList", "multiEnum"), "list"),
    "boolean": "boolean",
}
# What the value of a criterion must be for each kind; a list property is tested with one text value.
VALUE_NOUNS = {"number": "a number", "text": "text", "list": "text", "boolean": "true or false"}


class PropertyRef(BaseModel):
    """Identifies the property of a criterion by its guid."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    guid: str


class Criterion(BaseModel):
    """A condition on one property: its operator, and the value to compare with for the comparison operators.

    A comparison never matches a cell without a value, so NotEqual does not match undefined cells either; the status
    operators HasValue, IsUserUndefined and IsNotAvailable test the status and take no value. Contains finds a
    case-insensitive substring in text, and one equal item (case-sensitive) in a list. Numbers are compared in SI
    units, within a small tolerance except for integer properties.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    property: PropertyRef
    operator: Operator
    value: Any = None

    @model_validator(mode="after")
    def _check_value(self) -> "Criterion":
        takes_value = self.operator not in (*STATUS_OPERATORS, "IsDefault")
        if takes_value and self.value is None:
            raise ValueError(f"{self.operator} needs a value; add 'value'.")
        if not takes_value and self.value is not None:
            raise ValueError(f"{self.operator} takes no value; remove 'value'.")
        if self.operator == "Contains" and self.value == "":
            raise ValueError("Contains needs a non-empty text value.")
        return self


def ensure_supported(criterion: Criterion) -> None:
    """Raise ToolError for an operator that this server cannot evaluate. Call it before reading any values."""
    if criterion.operator == "IsDefault":
        raise ToolError(
            "IsDefault is not supported because the installed Tapir cannot evaluate it. Use another operator."
        )


def matches(cell: Cell, criterion: Criterion) -> bool | None:
    """Whether the cell satisfies the criterion: True or False, or None when the cell cannot decide it.

    A cell that is not normal matches no value comparison, not even NotEqual. A Tapir fallback cell holds a display
    string, so it decides only Equal, NotEqual and Contains on a string property; everything else gives None, which
    callers count as not comparable. The operator and value are checked against the property type on every cell,
    whatever its status, so a wrong criterion fails on every element.

    Raises ToolError for IsDefault and when the operator or the value does not fit the property type.
    """
    ensure_supported(criterion)
    operator = criterion.operator
    if operator in STATUS_OPERATORS:
        return cell.status == STATUS_OPERATORS[operator] if cell.typed else None
    kind = _check_types(cell, criterion)
    if not cell.typed:
        return _match_fallback(cell, operator, criterion.value)
    if cell.status != "normal":
        return False
    value = criterion.value
    if kind == "number":
        return _compare_numbers(operator, cell.value, value, exact=cell.value_type == "integer")
    if operator == "Contains":
        return value in cell.value if kind == "list" else value.casefold() in cell.value.casefold()
    return (cell.value == value) == (operator == "Equal")


def _compare_numbers(operator: str, number: float, value: float, exact: bool) -> bool:
    if exact:
        return {"Equal": ops.eq, "NotEqual": ops.ne, **ORDERING}[operator](number, value)
    close = math.isclose(number, value, rel_tol=TOLERANCE, abs_tol=TOLERANCE)
    less, greater = number < value, number > value
    return {
        "Equal": close,
        "NotEqual": not close,
        "Less": less and not close,
        "LessOrEqual": less or close,
        "Greater": greater and not close,
        "GreaterOrEqual": greater or close,
    }[operator]


def _check_types(cell: Cell, criterion: Criterion) -> str:
    """Return the kind of the property; raise when the operator or value does not fit it."""
    operator, value = criterion.operator, criterion.value
    guid, value_type = criterion.property.guid, cell.value_type
    kind = KINDS.get(value_type)
    if operator in ORDERING:
        needs, kinds = "a numeric property", ("number",)
    elif operator == "Contains":
        needs, kinds = "a text or list property", ("text", "list")
    else:
        needs, kinds = "a numeric, text or boolean property", ("number", "text", "boolean")
    if kind not in kinds:
        message = f"{operator} cannot be used on property {guid}, which holds {value_type} values; it needs {needs}."
        if kind is None:
            message += " To test such a property, use HasValue, IsUserUndefined or IsNotAvailable."
        elif kind == "list" and operator in ("Equal", "NotEqual"):
            message += " Use Contains to test list items."
        else:
            message += " Use another operator or property."
        raise ToolError(message)
    right_type = {
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "text": isinstance(value, str),
        "list": isinstance(value, str),
        "boolean": isinstance(value, bool),
    }[kind]
    if not right_type:
        raise ToolError(
            f"The value for {operator} on property {guid} must be {VALUE_NOUNS[kind]}, because the property holds "
            f"{value_type} values. Send numbers as JSON numbers and text as strings."
        )
    if kind == "number" and not _is_finite(value):
        raise ToolError(f"The value for {operator} on property {guid} must be a finite number.")
    return kind


def _is_finite(number: float) -> bool:
    try:
        return math.isfinite(number)
    except OverflowError:  # an int too large for a float
        return False


def _match_fallback(cell: Cell, operator: str, value: str) -> bool | None:
    """Fallback cells are display strings: only text comparisons on a string property can be decided."""
    if cell.value_type != "string" or cell.status != "normal" or operator not in ("Equal", "NotEqual", "Contains"):
        return None
    if operator == "Contains":
        return value.casefold() in cell.value.casefold()
    return (cell.value == value) == (operator == "Equal")
