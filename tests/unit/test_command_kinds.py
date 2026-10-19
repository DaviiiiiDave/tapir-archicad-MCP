import pytest

from tapir_archicad_mcp.tools.tool_registry import TOOL_CALLABLE_REGISTRY, get_tool_entry, register_tool_for_dispatch


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
    ],
)
def test_known_commands_are_classified(fake_archicad, name, kind):
    assert get_tool_entry(name).kind == kind


def test_every_registered_command_has_a_valid_kind(fake_archicad):
    assert {entry.kind for entry in TOOL_CALLABLE_REGISTRY.values()} <= {"read", "write", "irreversible"}


def test_registration_without_kind_fails():
    with pytest.raises(TypeError):
        register_tool_for_dispatch(lambda conn_header: None, name="test_no_kind", title="T", description="D")
