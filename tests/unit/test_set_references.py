import json
import types
import typing
import uuid
from unittest.mock import Mock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad import APIResponseError, SoloProjectID, UntitledProjectID
from multiconn_archicad.errors import CommandTimeoutError
from pydantic import BaseModel

from tapir_archicad_mcp.app import app_lifespan, mcp
from tapir_archicad_mcp.context import set_store_instance
from tapir_archicad_mcp.element_sets import SetStore
from tapir_archicad_mcp.set_references import (
    SET_EXPANSION_FIELDS,
    expand_set_references,
    is_set_reference,
    resolve_set,
)
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


@pytest.mark.parametrize("set_id", ["set:1", "set:AB12CD34", "set:ab12cd34\n", "set:ab12cd345", "ab12cd34", ""])
def test_malformed_set_id_is_not_a_set_reference(set_id):
    assert not is_set_reference({"set": set_id})


def test_resolve_set_refuses_a_malformed_id_without_echoing_it(fake_archicad):
    junk = "set:" + "y" * 500

    with pytest.raises(ToolError) as raised:
        resolve_set(fake_archicad, PORT, junk)

    assert "yyyy" not in str(raised.value)
    assert fake_archicad.calls == []


def test_expansion_does_not_mutate_its_input():
    guids = _guids(2)
    arguments = {"port": PORT, "params": {"elements": {"set": "set:00000001"}}}
    element_set = types.SimpleNamespace(id="set:00000001", guids=tuple(guids))

    expanded, sets = expand_set_references("elements_highlight_elements", arguments, lambda set_id: element_set)

    assert arguments == {"port": PORT, "params": {"elements": {"set": "set:00000001"}}}
    assert expanded["params"]["elements"] == [{"elementId": {"guid": g}} for g in guids]
    assert sets == {"params.elements": element_set}


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
    arguments = _highlight([])
    arguments["params"]["highlightedColors"] = {"set": set_id}

    with pytest.raises(ToolError) as raised:
        await archicad_read_tool("elements_highlight_elements", arguments)

    assert str(raised.value) == (
        "'elements_highlight_elements' does not accept a set reference in params.highlightedColors. "
        "Pass an explicit list there. Fields that accept set references: params.elements."
    )
    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_set_reference_for_a_command_without_set_fields_says_so(fake_archicad):
    set_id = _make_set(_guids(2))

    with pytest.raises(ToolError) as raised:
        await archicad_call_tool(
            "properties_set_property_values_of_elements",
            {"port": PORT, "params": {"elementPropertyValues": {"set": set_id}}},
        )

    assert str(raised.value) == (
        "'properties_set_property_values_of_elements' accepts no set references; pass an explicit list in "
        "params.elementPropertyValues."
    )
    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_malformed_set_reference_is_refused(fake_archicad):
    set_id = _make_set(_guids(2))
    guids = _guids(3)

    with pytest.raises(ToolError) as raised:
        await archicad_read_tool("elements_highlight_elements", _highlight({"set": set_id, "extra": guids}))

    message = str(raised.value)
    assert message.startswith("params.elements: a set reference must be exactly")
    assert '{"set": "<set id from a read result>"}' in message
    assert "a string id (like set:ab12cd34) and no other keys" in message
    assert "Got keys: extra, set." in message
    assert guids[0] not in message
    assert fake_archicad.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [{"set": "set:" + "y" * 300}, {"set": 5}, {"set": ["a" * 300]}])
async def test_malformed_set_id_is_refused_without_echo(fake_archicad, value):
    with pytest.raises(ToolError) as raised:
        await archicad_read_tool("elements_highlight_elements", _highlight(value))

    assert "yyyy" not in str(raised.value)
    assert "aaaa" not in str(raised.value)
    assert str(raised.value) == (
        "params.elements: the set id is not valid. Set ids look like set:ab12cd34 and come from the 'set' "
        "summary of a read result."
    )
    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_malformed_reference_does_not_echo_long_key_names(fake_archicad):
    long_key = "k" * 300

    with pytest.raises(ToolError) as raised:
        await archicad_read_tool("elements_highlight_elements", _highlight({"set": "set:00000000", long_key: 1}))

    assert long_key not in str(raised.value)
    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_set_reference_on_other_port_is_refused(fake_archicad):
    set_id = _make_set(_guids(2), port=PORT + 1)

    with pytest.raises(ToolError, match="not port 19723.*re-run the read command on port 19723"):
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


def _log_entry(change_log) -> dict:
    (path,) = change_log.directory.glob("changes-*.jsonl")
    (line,) = path.read_text(encoding="utf-8").splitlines()
    return json.loads(line)


@pytest.mark.asyncio
async def test_change_log_records_the_set_with_its_elements(fake_archicad, change_log):
    guids = _guids(3)
    set_id = _make_set(guids)
    fake_archicad.on_tapir_command("LockElements", {})

    await archicad_call_tool("elements_lock_elements", {"port": PORT, "params": {"elements": {"set": set_id}}})

    entry = _log_entry(change_log)
    assert entry["sets"] == {"params.elements": {"set": set_id, "count": 3, "elements": guids}}
    assert json.loads(entry["argumentsPreview"]) == {"port": PORT, "params": {"elements": {"set": set_id}}}
    assert entry["status"] == "completed"


@pytest.mark.asyncio
async def test_failed_set_write_is_logged_with_the_set(fake_archicad, change_log):
    guids = _guids(2)
    set_id = _make_set(guids)  # no LockElements response: the fake raises

    with pytest.raises(Exception):
        await archicad_call_tool("elements_lock_elements", {"port": PORT, "params": {"elements": {"set": set_id}}})

    entry = _log_entry(change_log)
    assert entry["status"] == "failed"
    assert entry["sets"] == {"params.elements": {"set": set_id, "count": 2, "elements": guids}}
    assert json.loads(entry["argumentsPreview"])["params"]["elements"] == {"set": set_id}


@pytest.mark.asyncio
async def test_set_write_with_unknown_outcome_is_logged_with_the_set(fake_archicad, change_log):
    guids = _guids(2)
    set_id = _make_set(guids)
    fake_archicad.core.post_tapir_command = Mock(side_effect=CommandTimeoutError("timed out"))

    with pytest.raises(Exception):
        await archicad_call_tool("elements_lock_elements", {"port": PORT, "params": {"elements": {"set": set_id}}})

    entry = _log_entry(change_log)
    assert entry["status"] == "unknown"
    assert entry["sets"] == {"params.elements": {"set": set_id, "count": 2, "elements": guids}}
    assert json.loads(entry["argumentsPreview"])["params"]["elements"] == {"set": set_id}


@pytest.mark.asyncio
async def test_preview_keeps_the_parameters_that_follow_a_set(fake_archicad, change_log):
    set_id = _make_set(_guids(10))
    fake_archicad.on_tapir_command("AttachElementsToIssue", {})

    await archicad_call_tool(
        "issues_attach_elements_to_issue",
        {
            "port": PORT,
            "confirm": True,
            "params": {"issueId": {"guid": _guids(1)[0]}, "elements": {"set": set_id}, "type": "Highlight"},
        },
    )

    preview = json.loads(_log_entry(change_log)["argumentsPreview"])
    assert preview["params"]["elements"] == {"set": set_id}
    assert preview["params"]["type"] == "Highlight"


@pytest.mark.asyncio
async def test_two_set_fields_are_both_expanded(fake_archicad):
    first, second = _guids(2), _guids(3)
    fake_archicad.on_tapir_command("GetCollisions", {"collisions": []})

    await archicad_read_tool(
        "elements_get_collisions",
        {
            "port": PORT,
            "params": {"elementsGroup1": {"set": _make_set(first)}, "elementsGroup2": {"set": _make_set(second)}},
        },
    )

    ((_, parameters),) = fake_archicad.calls
    assert parameters["elementsGroup1"] == [{"elementId": {"guid": g}} for g in first]
    assert parameters["elementsGroup2"] == [{"elementId": {"guid": g}} for g in second]


@pytest.mark.asyncio
async def test_delete_with_a_set_needs_confirm(fake_archicad, change_log):
    set_id = _make_set(_guids(2))

    with pytest.raises(ToolError, match="irreversible"):
        await archicad_call_tool("elements_delete_elements", {"port": PORT, "params": {"elements": {"set": set_id}}})

    assert fake_archicad.calls == []
    assert list(change_log.directory.glob("changes-*.jsonl")) == []


@pytest.mark.asyncio
async def test_confirmed_delete_runs_with_the_expanded_list(fake_archicad, change_log):
    guids = _guids(2)
    set_id = _make_set(guids)
    fake_archicad.on_tapir_command("DeleteElements", {})

    await archicad_call_tool(
        "elements_delete_elements", {"port": PORT, "confirm": True, "params": {"elements": {"set": set_id}}}
    )

    ((command, parameters),) = fake_archicad.calls
    assert command == "DeleteElements"
    assert parameters["elements"] == [{"elementId": {"guid": g}} for g in guids]
    assert _log_entry(change_log)["sets"]["params.elements"]["elements"] == guids


@pytest.mark.asyncio
async def test_read_tool_refuses_a_write_command_with_a_set_reference(fake_archicad):
    set_id = _make_set(_guids(2))

    with pytest.raises(ToolError, match="changes the project"):
        await archicad_read_tool("elements_lock_elements", {"port": PORT, "params": {"elements": {"set": set_id}}})

    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_app_lifespan_installs_a_set_store(monkeypatch):
    monkeypatch.setattr("tapir_archicad_mcp.app.MultiConn", Mock())
    set_store_instance.clear()

    async with app_lifespan(mcp):
        assert isinstance(set_store_instance.get(), SetStore)

    with pytest.raises(LookupError):
        set_store_instance.get()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("set_port, set_id", [(PORT, "set:00000000"), (PORT + 1, None)])
async def test_unknown_or_foreign_set_costs_no_archicad_round_trip(fake_archicad, set_port, set_id):
    asked = []
    fake_archicad.get_archicad_id = lambda timeout: asked.append(timeout)
    set_id = set_id or _make_set(_guids(2), port=set_port)

    with pytest.raises(ToolError, match="Unknown set|not port"):
        await archicad_read_tool("elements_highlight_elements", _highlight({"set": set_id}))

    assert asked == []
    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_unknown_set_with_element_sets_off_says_no_sets_are_created(fake_archicad, monkeypatch):
    monkeypatch.setattr("tapir_archicad_mcp.app.element_sets_enabled", False)

    with pytest.raises(ToolError) as raised:
        await archicad_read_tool("elements_highlight_elements", _highlight({"set": "set:00000000"}))

    assert "lost when the server restarts" in str(raised.value)
    assert "Automatic sets are off on this server (--element-sets off), so read results create no sets." in str(
        raised.value
    )


@pytest.mark.asyncio
async def test_unknown_set_with_element_sets_on_does_not_mention_the_flag(fake_archicad, monkeypatch):
    monkeypatch.setattr("tapir_archicad_mcp.app.element_sets_enabled", True)

    with pytest.raises(ToolError) as raised:
        await archicad_read_tool("elements_highlight_elements", _highlight({"set": "set:00000000"}))

    assert "element-sets" not in str(raised.value)


@pytest.mark.asyncio
async def test_empty_set_is_refused_before_anything_reaches_archicad(fake_archicad):
    set_id = _make_set([])

    with pytest.raises(ToolError) as raised:
        await archicad_read_tool("elements_highlight_elements", _highlight({"set": set_id}))

    assert str(raised.value) == f"Set {set_id} is empty; pass an explicit element list or re-run the read command."
    assert fake_archicad.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("command, kwargs", [
    ("elements_lock_elements", {}),
    ("elements_delete_elements", {"confirm": True}),
])
async def test_untitled_set_is_refused_for_writes(fake_archicad, change_log, command, kwargs):
    fake_archicad.archicad_id = UntitledProjectID()
    set_id = _make_set(_guids(2), project="untitled")

    with pytest.raises(ToolError) as raised:
        await archicad_call_tool(command, {"port": PORT, "params": {"elements": {"set": set_id}}, **kwargs})

    message = str(raised.value)
    assert "unsaved project" in message
    assert "cannot be told apart" in message
    assert "save the project and re-run the read command" in message
    assert "explicit element list" in message
    assert fake_archicad.calls == []
    assert list(change_log.directory.glob("changes-*.jsonl")) == []


@pytest.mark.asyncio
async def test_untitled_set_still_works_for_reads(fake_archicad):
    fake_archicad.archicad_id = UntitledProjectID()
    guids = _guids(2)
    set_id = _make_set(guids, project="untitled")
    fake_archicad.on_tapir_command("HighlightElements", {})

    await archicad_read_tool("elements_highlight_elements", _highlight({"set": set_id}))

    ((_, parameters),) = fake_archicad.calls
    assert parameters["elements"] == [{"elementId": {"guid": g}} for g in guids]


@pytest.mark.parametrize("tool", ["archicad_call_tool", "archicad_read_tool"])
def test_tool_descriptions_explain_sets(tool):
    description = {t.name: t.description for t in mcp._tool_manager.list_tools()}[tool]

    assert 'Results of element-list reads may carry a "set" summary' in description
    assert 'pass {"set": "<id>"}' in description
    assert "acceptsSets" in description
