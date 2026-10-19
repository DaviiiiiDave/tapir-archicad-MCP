import sys
from pathlib import Path

import pytest

from tapir_archicad_mcp.tools.tool_registry import (
    TOOL_CALLABLE_REGISTRY,
    TOOL_DISCOVERY_CATALOG,
    get_tool_entry,
    register_tool_for_dispatch,
)


@pytest.mark.parametrize(
    "name, kind",
    [
        ("elements_get_all_elements", "read"),
        ("elements_filter_elements", "read"),
        ("elements_highlight_elements", "read"),
        ("elements_create_walls", "write"),
        ("properties_set_property_values_of_elements", "write"),
        ("elements_delete_elements", "irreversible"),
        ("project_save_project", "irreversible"),
        ("teamwork_teamwork_send", "irreversible"),
        ("navigator_publish_publisher_set", "irreversible"),
        ("ifc_ifc_file_operation", "irreversible"),
        ("teamwork_reserve_elements", "irreversible"),
        ("teamwork_release_elements", "irreversible"),
    ],
)
def test_known_commands_are_classified(fake_archicad, name, kind):
    assert get_tool_entry(name).kind == kind


def test_every_registered_command_has_a_valid_kind(fake_archicad):
    assert {entry.kind for entry in TOOL_CALLABLE_REGISTRY.values()} <= {"read", "write", "irreversible"}


def test_generated_kinds_match_the_generator_config(fake_archicad):
    """A config change without regenerating (or the reverse) must not go unnoticed."""
    sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
    from generator_config import OFFICIAL_CONFIG, TAPIR_CONFIG

    expected: dict[str, set[str]] = {}
    for config in (TAPIR_CONFIG, OFFICIAL_CONFIG):
        for kind, names in (
            ("read", config.read_commands),
            ("write", config.write_commands),
            ("irreversible", config.irreversible_commands),
        ):
            for name in names:
                expected.setdefault(name, set()).add(kind)

    checked = 0
    for entry in TOOL_DISCOVERY_CATALOG.values():
        if entry["title"] in expected:
            assert {entry["kind"]} == expected[entry["title"]], entry["name"]
            checked += 1
    assert checked > 250


def test_registration_without_kind_fails():
    with pytest.raises(TypeError):
        register_tool_for_dispatch(lambda conn_header: None, name="test_no_kind", title="T", description="D")
