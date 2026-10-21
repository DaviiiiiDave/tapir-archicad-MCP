"""Criteria semantics: whether one property cell matches one filter criterion. Every filter path uses this module."""

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

# Official value type -> kind. A type missing here (e.g. lengthList) cannot be compared.
KINDS = {
    **dict.fromkeys(("length", "area", "volume", "angle", "number", "integer"), "number"),
    **dict.fromkeys(("string", "singleEnum"), "text"),
    **dict.fromkeys(("stringList", "multiEnum"), "list"),
    "boolean": "boolean",
}
VALUE_NOUNS = {"number": "numeric", "text": "text", "list": "text", "boolean": "boolean"}


class PropertyRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guid: str


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property: PropertyRef
    operator: Operator
    value: Any = None

    @model_validator(mode="after")
    def _check_value(self) -> "Criterion":
        takes_value = self.operator not in (*STATUS_OPERATORS, "IsDefault")
        if takes_value and self.value is None:
            raise ValueError(f"{self.operator} needs a value.")
        if not takes_value and self.value is not None:
            raise ValueError(f"{self.operator} takes no value.")
        return self


def ensure_supported(criterion: Criterion) -> None:
    if criterion.operator == "IsDefault":
        raise ToolError(
            "IsDefault needs Archicad's own criteria evaluation, which the installed Tapir does not offer yet."
        )


def matches(cell: Cell, criterion: Criterion) -> bool | None:
    """None: the cell is an untyped fallback value and cannot decide this criterion."""
    ensure_supported(criterion)
    operator = criterion.operator
    if operator in STATUS_OPERATORS:
        return cell.status == STATUS_OPERATORS[operator] if cell.typed else None
    if not cell.typed:
        return _match_untyped(cell, operator, criterion.value)
    kind = _check_types(cell, criterion)
    if cell.status != "normal":
        return False
    value = criterion.value
    if operator in ORDERING:
        return ORDERING[operator](cell.value, value)
    if operator == "Contains":
        return value in cell.value if kind == "list" else value.casefold() in cell.value.casefold()
    equal = math.isclose(cell.value, value, rel_tol=1e-9, abs_tol=1e-12) if kind == "number" else cell.value == value
    return equal if operator == "Equal" else not equal


def _check_types(cell: Cell, criterion: Criterion) -> str:
    """Return the kind of the property; raise when the operator or value does not fit it."""
    operator, value = criterion.operator, criterion.value
    kind = KINDS.get(cell.value_type)
    if operator in ORDERING:
        needs, kinds = "a numeric property", ("number",)
    elif operator == "Contains":
        needs, kinds = "a text or list property", ("text", "list")
    else:
        needs, kinds = "a number, text or boolean property", ("number", "text", "boolean")
    if kind not in kinds:
        raise _mismatch(operator, needs, criterion.property.guid, cell.value_type)
    right_type = {
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "text": isinstance(value, str),
        "list": isinstance(value, str),
        "boolean": isinstance(value, bool),
    }[kind]
    if not right_type:
        raise _mismatch(operator, f"a {VALUE_NOUNS[kind]} value", criterion.property.guid, cell.value_type)
    if kind == "number" and not math.isfinite(value):
        raise _mismatch(operator, "a finite numeric value", criterion.property.guid, cell.value_type)
    return kind


def _mismatch(operator: str, needs: str, guid: str, value_type: str) -> ToolError:
    return ToolError(f"{operator} needs {needs}; property {guid} holds {value_type} values.")


def _match_untyped(cell: Cell, operator: str, value: Any) -> bool | None:
    """Fallback cells are display strings: only text comparisons are decidable."""
    if cell.status != "normal":
        return False
    if not isinstance(value, str) or operator in ORDERING:
        return None
    if operator == "Contains":
        return value.casefold() in cell.value.casefold()
    return (cell.value == value) == (operator == "Equal")
