"""Compact validation errors that never echo the rejected input."""

from pydantic import ValidationError


def format_validation_error(command_name: str, error: ValidationError, max_errors: int = 10) -> str:
    """Name the failing fields without repeating their values, so a rejected bulk call stays short."""
    errors = error.errors(include_input=False, include_url=False)
    lines = [f"Invalid arguments provided for tool '{command_name}':"]
    for item in errors[:max_errors]:
        lines.append(f"- {'.'.join(str(part) for part in item['loc'])}: {item['msg']}")
    if len(errors) > max_errors:
        lines.append(f"...and {len(errors) - max_errors} more errors")
    lines.append(f"Call archicad_get_command_schema('{command_name}') for the exact schema.")
    return "\n".join(lines)
