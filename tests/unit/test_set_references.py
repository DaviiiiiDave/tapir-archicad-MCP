import json
import types
import typing
import uuid

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad import APIResponseError, SoloProjectID
from pydantic import BaseModel

from tapir_archicad_mcp.context import set_store_instance
from tapir_archicad_mcp.set_references import SET_EXPANSION_FIELDS, expand_set_references, is_set_reference
from tapir_archicad_mcp.tools.custom.functions import (
    archicad_call_tool,
    archicad_get_command_schema,
    archicad_list_commands,
    archicad_read_tool,
)
from tapir_archicad_mcp.tools.tool_registry import TOOL_CALLABLE_REGISTRY

PORT = 19723
PROJECT = "/path/to/fake_project.pln"


def _guids(count: int) -> list[str]:
    return [str(uuid.uuid4()) for _ in range(count)]


def _make_set(guids: list[str], port: int = PORT, project: str = PROJECT) -> str:
    return set_store_instance.get().create(port, project, guids, {"tool": "test"}).id


def _highlight(elements) -> dict:
    return {"port": PORT, "params": {"elements": elements, "highlightedColors": [[255, 0, 0, 255]]}}


def _is_element_id_list(annotation) -> bool:
    """True for list[X] or list[X] | None where X is exactly {"elementId": {"guid"}}."""
    for candidate in (annotation, *typing.get_args(annotation)):
        if typing.get_origin(candidate) is not list:
            continue
        (item,) = typing.get_args(candidate)
        if not (isinstance(item, type) and issubclass(item, BaseModel)) or set(item.model_fields) != {"elementId"}:
            continue
        element_id = item.model_fields["elementId"].annotation
        if isinstance(element_id, type) and issubclass(element_id, BaseModel) and set(element_id.model_fields) == {"guid"}:
            return True
    return False


def _element_list_fields() -> dict[str, tuple[str, ...]]:
    found = {}
    for name, entry in TOOL_CALLABLE_REGISTRY.items():
        model = entry.params_model
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        fields = tuple(field for field, info in model.model_fields.items() if _is_element_id_list(info.annotation))
        if fields:
            found[name] = fields
    return found


def test_expansion_table_matches_the_parameter_models(fake_archicad):
    assert _element_list_fields() == SET_EXPANSION_FIELDS


def test_is_set_reference():
    assert is_set_reference({"set": "set:ab12cd34"})
    assert not is_set_reference({"set": "set:ab12cd34", "extra": 1})
    assert not is_set_reference({"set": 5})
    assert not is_set_reference([{"set": "set:ab12cd34"}])
    assert not is_set_reference("set:ab12cd34")


def test_expansion_does_not_mutate_its_input():
    guids = _guids(2)
    arguments = {"port": PORT, "params": {"elements": {"set": "set:1"}}}

    expanded, sets = expand_set_references(
        "elements_highlight_elements", arguments, lambda set_id: types.SimpleNamespace(guids=tuple(guids))
    )

    assert arguments == {"port": PORT, "params": {"elements": {"set": "set:1"}}}
    assert expanded["params"]["elements"] == [{"elementId": {"guid": g}} for g in guids]
    assert sets == {"params.elements": "set:1"}


@pytest.mark.asyncio
async def test_set_reference_is_expanded_before_validation(fake_archicad):
    guids = _guids(3)
    set_id = _make_set(guids)
    fake_archicad.on_tapir_command("HighlightElements", {})

    await archicad_read_tool("elements_highlight_elements", _highlight({"set": set_id}))

    ((command, parameters),) = fake_archicad.calls
    assert command == "HighlightElements"
    assert parameters["elements"] == [{"elementId": {"guid": g}} for g in guids]


@pytest.mark.asyncio
async def test_job_preview_shows_the_set_id_not_the_guids(fake_archicad, job_store):
    guids = _guids(3)
    set_id = _make_set(guids)
    fake_archicad.on_tapir_command("HighlightElements", {})

    await archicad_read_tool("elements_highlight_elements", _highlight({"set": set_id}))

    (job,) = job_store.list_jobs()["jobs"]
    assert set_id in job["argumentsPreview"]
    assert guids[0] not in job["argumentsPreview"]


@pytest.mark.asyncio
async def test_set_reference_in_unsupported_field_is_refused(fake_archicad):
    set_id = _make_set(_guids(2))

    with pytest.raises(ToolError, match="elementPropertyValues"):
        await archicad_call_tool(
            "properties_set_property_values_of_elements",
            {"port": PORT, "params": {"elementPropertyValues": {"set": set_id}}},
        )

    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_malformed_set_reference_is_refused(fake_archicad):
    set_id = _make_set(_guids(2))

    with pytest.raises(ToolError, match="elements"):
        await archicad_read_tool("elements_highlight_elements", _highlight({"set": set_id, "extra": 1}))

    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_set_reference_on_other_port_is_refused(fake_archicad):
    set_id = _make_set(_guids(2), port=PORT + 1)

    with pytest.raises(ToolError, match="not port 19723"):
        await archicad_read_tool("elements_highlight_elements", _highlight({"set": set_id}))

    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_set_reference_after_project_change_is_refused(fake_archicad):
    set_id = _make_set(_guids(2))
    fake_archicad.archicad_id = SoloProjectID(projectName="Other", projectPath="/p/other.pln")

    with pytest.raises(ToolError, match="now has '/p/other.pln' open"):
        await archicad_read_tool("elements_highlight_elements", _highlight({"set": set_id}))

    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_set_reference_with_unreadable_project_says_so(fake_archicad):
    set_id = _make_set(_guids(2))
    fake_archicad.get_archicad_id = lambda timeout: APIResponseError(code=None, message="no answer")

    with pytest.raises(ToolError, match="could not be read.*no answer") as raised:
        await archicad_read_tool("elements_highlight_elements", _highlight({"set": set_id}))

    assert "created for project" not in str(raised.value)
    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_change_log_records_the_set_id(fake_archicad, change_log):
    set_id = _make_set(_guids(2))
    fake_archicad.on_tapir_command("LockElements", {})

    await archicad_call_tool("elements_lock_elements", {"port": PORT, "params": {"elements": {"set": set_id}}})

    (path,) = change_log.directory.glob("changes-*.jsonl")
    (line,) = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["sets"] == {"params.elements": set_id}


@pytest.mark.asyncio
async def test_change_log_has_no_sets_key_without_set_references(fake_archicad, change_log):
    fake_archicad.on_tapir_command("LockElements", {})

    await archicad_call_tool(
        "elements_lock_elements", {"port": PORT, "params": {"elements": [{"elementId": {"guid": _guids(1)[0]}}]}}
    )

    (path,) = change_log.directory.glob("changes-*.jsonl")
    (line,) = path.read_text(encoding="utf-8").splitlines()
    assert "sets" not in json.loads(line)


def test_schema_offers_set_reference(fake_archicad):
    schema = archicad_get_command_schema("elements_highlight_elements").input_schema

    elements = schema["$defs"]["HighlightElementsParameters"]["properties"]["elements"]
    assert elements["anyOf"][0]["type"] == "array"
    assert elements["anyOf"][1] == {"$ref": "#/$defs/SetReference"}
    reference = schema["$defs"]["SetReference"]
    assert reference["required"] == ["set"]
    assert reference["additionalProperties"] is False
    assert reference["properties"]["set"]["pattern"] == "^set:[0-9a-f]{8}$"
    assert reference["examples"] == [{"set": "set:ab12cd34"}]


def test_schema_of_command_without_element_lists_is_unchanged(fake_archicad):
    schema = archicad_get_command_schema("app_get_add_on_version").input_schema
    assert "SetReference" not in json.dumps(schema)


def test_catalog_says_which_commands_accept_sets(fake_archicad):
    overview = {command.name: command.acceptsSets for command in archicad_list_commands()}
    assert overview["elements_highlight_elements"] is True
    assert overview["app_get_add_on_version"] is False
