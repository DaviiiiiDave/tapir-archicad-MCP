from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad import (
    APIResponseError,
    Port,
    ProductInfo,
    SoloProjectID,
    TapirInfo,
    TeamworkCredentials,
    TeamworkProjectID,
    UntitledProjectID,
)
from multiconn_archicad.constants import SUPPORTED_TAPIR_VERSION
from multiconn_archicad.errors import AddOnCommandUnavailable
from pydantic import BaseModel

from tapir_archicad_mcp.context import multi_conn_instance
from tapir_archicad_mcp.tools.custom.functions import (
    archicad_call_tool,
    archicad_get_command_schema,
    archicad_list_commands,
    archicad_read_tool,
    list_active_archicads,
)
from tapir_archicad_mcp.tools.custom.models import (
    CommandOverview,
    CommandSchema,
    DiscoveryResult,
)
from tapir_archicad_mcp.tools.tool_registry import (
    TOOL_DISCOVERY_CATALOG,
    get_tool_entry,
    register_tool_for_dispatch,
)

# ============================================================================
# Helpers & Fixtures
# ============================================================================


def make_header(
    project_id=SoloProjectID(projectName="House", projectPath="/p/house.pln"),
    version: str | None = SUPPORTED_TAPIR_VERSION,
    is_installed: bool = True,
    product_info=ProductInfo(version=28, buildNumber=3001, languageCode="INT"),
):
    if is_installed and version:
        tapir = TapirInfo(version=version, is_installed=True)
    else:
        tapir = TapirInfo.not_installed()

    return SimpleNamespace(
        product_info=product_info,
        archicad_id=project_id,
        tapir_info=tapir,
    )


@pytest.fixture
def set_multi_conn():
    def _set(headers: dict[int, Any]) -> None:
        typed = {Port(p): h for p, h in headers.items()}
        mc = SimpleNamespace(
            refresh=SimpleNamespace(all_ports=Mock()),
            connect=SimpleNamespace(all=Mock()),
            open_port_headers=typed,
            open_ports=list(typed.keys()),
            active=typed,
        )
        multi_conn_instance.set(mc)

    yield _set
    multi_conn_instance.clear()


# ============================================================================
# 1. Command & Schema Discovery Tests
# ============================================================================


def test_archicad_list_commands(fake_archicad):
    commands = archicad_list_commands()

    assert isinstance(commands, list)
    assert len(commands) > 0

    first_cmd = commands[0]
    assert isinstance(first_cmd, CommandOverview)
    assert first_cmd.name and first_cmd.description

    all_names = [cmd.name for cmd in commands]
    assert "elements_get_selected_elements" in all_names
    assert "elements_create_slabs" in all_names


def test_list_without_arguments_is_unchanged(fake_archicad):
    assert len(archicad_list_commands()) == len(TOOL_DISCOVERY_CATALOG)


def test_query_matches_all_tokens(fake_archicad):
    names = [command.name for command in archicad_list_commands(query="property value")]

    assert "properties_get_property_values_of_elements" in names
    for command in archicad_list_commands(query="property value"):
        text = f"{command.name} {command.description}".lower()
        assert "property" in text and "value" in text


def test_group_filter(fake_archicad):
    names = [command.name for command in archicad_list_commands(group="issues")]

    assert names
    assert all(name.startswith("issues_") for name in names)


def test_no_match_returns_empty_list(fake_archicad):
    assert archicad_list_commands(query="zzzz-no-such-command") == []


def test_blank_query_lists_everything(fake_archicad):
    assert len(archicad_list_commands(query="  ")) == len(TOOL_DISCOVERY_CATALOG)


def test_archicad_get_command_schema_valid(fake_archicad):
    schema_result = archicad_get_command_schema("elements_get_selected_elements")

    assert isinstance(schema_result, CommandSchema)
    assert schema_result.name == "elements_get_selected_elements"
    assert schema_result.input_schema["type"] == "object"
    assert "port" in schema_result.input_schema["properties"]
    assert "port" in schema_result.input_schema["required"]


def test_command_schemas_describe_the_exact_runtime_envelopes(fake_archicad):
    no_params = archicad_get_command_schema("app_get_add_on_version").input_schema
    with_params = archicad_get_command_schema("elements_create_slabs").input_schema
    paginated_no_params = archicad_get_command_schema("issues_get_issues").input_schema
    paginated_with_params = archicad_get_command_schema("elements_get_all_elements").input_schema

    assert no_params["additionalProperties"] is False
    assert set(no_params["properties"]) == {"port"}
    assert no_params["required"] == ["port"]
    assert no_params["properties"]["port"]["minimum"] == 19723
    assert no_params["properties"]["port"]["exclusiveMaximum"] == 19744

    assert set(with_params["properties"]) == {"port", "params"}
    assert set(with_params["required"]) == {"port", "params"}
    assert set(paginated_no_params["properties"]) == {"port", "page_token"}
    assert paginated_no_params["required"] == ["port"]
    assert set(paginated_with_params["properties"]) == {"port", "params", "page_token"}
    assert set(paginated_with_params["required"]) == {"port", "params"}


def test_command_schema_nested_references_are_locally_resolvable(fake_archicad):
    schema = archicad_get_command_schema("elements_create_slabs").input_schema

    def resolve_local_references(value: Any) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                target: Any = schema
                for segment in value["$ref"].removeprefix("#/").split("/"):
                    target = target[segment.replace("~1", "/").replace("~0", "~")]
                assert target
            for child in value.values():
                resolve_local_references(child)
        elif isinstance(value, list):
            for child in value:
                resolve_local_references(child)

    resolve_local_references(schema)


def test_reregistered_command_keeps_discovery_and_runtime_models_in_sync():
    class Params(BaseModel):
        value: int

    def without_params(conn_header) -> None:
        pass

    def with_params(conn_header, params: Params) -> None:
        pass

    name = "test_reregistered_command"
    register_tool_for_dispatch(without_params, name=name, title="Before", description="Before", kind="write")
    register_tool_for_dispatch(
        with_params, name=name, title="After", description="After", params_model=Params, kind="write"
    )

    runtime_schema = get_tool_entry(name).arguments_model.model_json_schema()
    discovery_schema = archicad_get_command_schema(name).input_schema

    assert discovery_schema == runtime_schema
    assert set(discovery_schema["required"]) == {"port", "params"}


@pytest.mark.asyncio
async def test_union_parameter_models_validate_and_dispatch(fake_archicad):
    class FirstParams(BaseModel):
        first: int

    class SecondParams(BaseModel):
        second: str

    received: list[FirstParams | SecondParams] = []

    def union_command(conn_header, params: FirstParams | SecondParams) -> None:
        received.append(params)

    name = "test_union_parameter_command"
    register_tool_for_dispatch(
        union_command,
        name=name,
        title="Union",
        description="Union parameters",
        params_model=FirstParams | SecondParams,
        kind="write",
    )

    result = await archicad_call_tool(name, {"port": 19723, "params": {"second": "value"}})
    schema = archicad_get_command_schema(name).input_schema

    assert result == {}
    assert isinstance(received[0], SecondParams)
    assert schema == get_tool_entry(name).arguments_model.model_json_schema()
    assert len(schema["properties"]["params"]["anyOf"]) == 2


def test_archicad_get_command_schema_invalid(fake_archicad):
    with pytest.raises(ToolError, match="not found. Please use 'archicad_list_commands'"):
        archicad_get_command_schema("made_up_command_name")


# ============================================================================
# 2. Instance Discovery & Version Checking Tests
# ============================================================================


def test_empty_instance_discovery(set_multi_conn):
    set_multi_conn({})
    result = list_active_archicads()
    assert isinstance(result, DiscoveryResult)
    assert result.active == [] and result.unavailable == []


@pytest.mark.parametrize(
    "project_id, expected_type, expected_path",
    [
        (SoloProjectID(projectName="House", projectPath="/p/h.pln"), "solo", "/p/h.pln"),
        (
            TeamworkProjectID(
                projectName="Tower",
                projectPath="P/Tower",
                serverAddress="https://bim.local",
                teamworkCredentials=TeamworkCredentials(username="user"),
            ),
            "teamwork",
            "teamwork://https://bim.local/P/Tower",
        ),
        (UntitledProjectID(), "untitled", None),
    ],
)
def test_healthy_instances_and_project_types(set_multi_conn, project_id, expected_type, expected_path):
    set_multi_conn({19723: make_header(project_id=project_id)})
    result = list_active_archicads()

    assert len(result.active) == 1
    inst = result.active[0]
    assert inst.port == 19723
    assert inst.project_type == expected_type
    assert inst.project_path == expected_path
    assert inst.warning is None


@pytest.mark.parametrize(
    "version, expected_warning_substring",
    [
        ("1.0.0", "older than supported"),
        ("99.0.0", "newer than supported"),
    ],
)
def test_tapir_version_warnings(set_multi_conn, version, expected_warning_substring):
    set_multi_conn({19723: make_header(version=version)})
    result = list_active_archicads()

    assert len(result.active) == 1
    assert result.active[0].warning is not None
    assert expected_warning_substring in result.active[0].warning


def test_unresponsive_instance_reported(set_multi_conn):
    header = make_header(product_info=APIResponseError(code=5003, message="Modal dialog open."))
    set_multi_conn({19723: header})
    result = list_active_archicads()

    assert result.active == []
    assert len(result.unavailable) == 1
    assert result.unavailable[0].reason == "unresponsive"
    assert "Modal dialog open." in result.unavailable[0].message


def test_missing_tapir_reported(set_multi_conn):
    header = make_header(is_installed=False, version=None)
    set_multi_conn({19724: header})
    result = list_active_archicads()

    assert result.active == []
    assert len(result.unavailable) == 1
    assert result.unavailable[0].reason == "missing_tapir"
    assert "https://github.com/ENZYME-APD/tapir-archicad-automation" in result.unavailable[0].message


def test_broken_instance_does_not_block_healthy_one(set_multi_conn):
    broken = make_header(product_info=APIResponseError(code=None, message="Timeout"))
    healthy = make_header()
    set_multi_conn({19723: broken, 19724: healthy})

    result = list_active_archicads()
    assert len(result.active) == 1 and result.active[0].port == 19724
    assert len(result.unavailable) == 1 and result.unavailable[0].reason == "unresponsive"


@pytest.mark.asyncio
async def test_call_tool_intercepts_addon_unavailable(set_multi_conn):
    mock_func = Mock(side_effect=AddOnCommandUnavailable("Unknown Add-On command."))
    register_tool_for_dispatch(mock_func, name="test_cmd", title="T", description="D", kind="write")
    set_multi_conn({19723: make_header()})

    with pytest.raises(ToolError, match="not available in the installed Tapir Add-On"):
        await archicad_call_tool("test_cmd", {"port": 19723})


# ============================================================================
# 3. Read-only tool and irreversible commands
# ============================================================================


@pytest.mark.asyncio
async def test_read_tool_runs_read_commands(fake_archicad):
    fake_archicad.on_tapir_command("GetAddOnVersion", {"version": "1.7.1"})

    result = await archicad_read_tool("app_get_add_on_version", {"port": 19723})

    assert result == {"version": "1.7.1"}


@pytest.mark.asyncio
async def test_read_tool_rejects_write_commands(fake_archicad):
    with pytest.raises(ToolError, match="archicad_call_tool"):
        await archicad_read_tool("elements_create_walls", {"port": 19723})

    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_irreversible_requires_confirm(fake_archicad):
    fake_archicad.on_tapir_command("SaveProject", {})

    with pytest.raises(ToolError, match='"confirm": true'):
        await archicad_call_tool("project_save_project", {"port": 19723})
    assert fake_archicad.calls == []

    result = await archicad_call_tool("project_save_project", {"port": 19723, "confirm": True})

    assert result == {}
    assert [command for command, _ in fake_archicad.calls] == ["SaveProject"]


@pytest.mark.asyncio
async def test_confirm_must_be_a_boolean(fake_archicad):
    with pytest.raises(ToolError):
        await archicad_call_tool("project_save_project", {"port": 19723, "confirm": "true"})

    assert fake_archicad.calls == []


def test_confirm_only_in_irreversible_schemas(fake_archicad):
    irreversible = archicad_get_command_schema("project_save_project").input_schema
    write = archicad_get_command_schema("elements_create_walls").input_schema

    assert "confirm" in irreversible["properties"]
    assert "confirm" not in write["properties"]


def test_catalog_reports_kind(fake_archicad):
    kinds = {command.name: command.kind for command in archicad_list_commands()}

    assert kinds["elements_delete_elements"] == "irreversible"
    assert kinds["elements_create_walls"] == "write"
    assert kinds["elements_get_all_elements"] == "read"


@pytest.mark.asyncio
async def test_read_tool_continues_pages(fake_archicad):
    elements = [{"elementId": {"guid": f"00000000-0000-0000-0000-{i:012d}"}} for i in range(150)]
    fake_archicad.on_tapir_command("GetAllElements", {"elements": elements})
    arguments = {"port": 19723, "params": {}}

    first = await archicad_read_tool("elements_get_all_elements", arguments)
    second = await archicad_read_tool(
        "elements_get_all_elements", {**arguments, "page_token": first["next_page_token"]}
    )

    assert len(first["elements"]) == 100
    assert len(second["elements"]) == 50
    assert "next_page_token" not in second
