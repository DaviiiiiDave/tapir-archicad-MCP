import asyncio
import hashlib
import re
import threading

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from multiconn_archicad import APIResponseError, UntitledProjectID
from multiconn_archicad.errors import CommandTimeoutError, StandardCommandUnavailable

import tapir_archicad_mcp.app
from tapir_archicad_mcp.auto_sets import AUTO_SET_FIELDS, INLINE_THRESHOLD, UNTITLED_WARNING, summarize_set
from tapir_archicad_mcp.jobs import JobSettings
from tapir_archicad_mcp.tools.custom.functions import archicad_get_job, archicad_read_tool
from tapir_archicad_mcp.tools.tool_registry import TOOL_CALLABLE_REGISTRY, TOOL_DISCOVERY_CATALOG

# Captured while the test module is imported, before any fixture can override the flag.
PRODUCTION_DEFAULT = tapir_archicad_mcp.app.element_sets_enabled

PORT = 19723
PROJECT = "/path/to/fake_project.pln"
TYPES = ("Wall", "Slab", "Column")


@pytest.fixture(autouse=True)
def element_sets_on(monkeypatch):
    monkeypatch.setattr("tapir_archicad_mcp.app.element_sets_enabled", True)


def _guid(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


def _elements(count: int) -> list[dict]:
    return [{"elementId": {"guid": _guid(i)}} for i in range(count)]


def _types(count: int) -> dict:
    return {
        "typesOfElements": [
            {"typeOfElement": {"elementId": {"guid": _guid(i)}, "elementType": TYPES[i % len(TYPES)]}}
            for i in range(count)
        ]
    }


# Minimal valid arguments per auto-set command (None: the command takes no params).
AUTO_SET_PARAMS = {
    "elements_get_all_elements": {},
    "elements_get_elements_by_type": {"elementType": "Wall"},
    "elements_get_elements_by_classification": {"classificationItemId": {"guid": _guid(900)}},
    "elements_get_selected_elements": None,
    "elements_filter_elements": {"elements": _elements(5), "filters": ["IsEditable"]},
    "issues_get_elements_attached_to_issue": {"issueId": {"guid": _guid(901)}, "type": "Creation"},
}


def _canned(fake_archicad, command: str, count: int) -> None:
    fake_archicad.on_tapir_command(command, {"elements": _elements(count)})
    fake_archicad.on_tapir_command("API.GetTypesOfElements", _types(count))


@pytest.mark.asyncio
async def test_large_result_is_replaced_by_a_summary(fake_archicad, set_store):
    _canned(fake_archicad, "GetAllElements", 150)

    result = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert "elements" not in result
    assert "next_page_token" not in result
    assert result["envelope"] == {"elements": {"total": 150, "returned": 0, "truncated": True}}
    summary = result["set"]
    assert summary["count"] == 150
    assert summary["byType"] == {"Wall": 50, "Slab": 50, "Column": 50}
    stored = set_store.get(summary["set"], PORT, PROJECT)
    assert stored.guids == tuple(_guid(i) for i in range(150))
    parameters = "{}"
    assert stored.origin == {
        "command": "elements_get_all_elements",
        "parametersHash": hashlib.sha256(parameters.encode()).hexdigest()[:16],
    }
    assert summary["createdAt"] == stored.created_at.isoformat()
    assert summary["warnings"] == []
    types_call = [parameters for command, parameters in fake_archicad.calls if command == "API.GetTypesOfElements"]
    assert len(types_call) == 1
    assert len(types_call[0]["elements"]) == 150


@pytest.mark.asyncio
async def test_threshold_boundary(fake_archicad):
    assert INLINE_THRESHOLD == 20
    _canned(fake_archicad, "GetAllElements", INLINE_THRESHOLD)
    at_threshold = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    _canned(fake_archicad, "GetAllElements", INLINE_THRESHOLD + 1)
    above_threshold = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert at_threshold["elements"] == _elements(INLINE_THRESHOLD)
    assert at_threshold["set"]["count"] == INLINE_THRESHOLD
    assert "envelope" not in at_threshold
    assert "elements" not in above_threshold
    total = INLINE_THRESHOLD + 1
    assert above_threshold["envelope"] == {"elements": {"total": total, "returned": 0, "truncated": True}}
    assert above_threshold["set"]["count"] == INLINE_THRESHOLD + 1


@pytest.mark.asyncio
async def test_flag_off_keeps_raw_lists_and_pages(fake_archicad, set_store, monkeypatch):
    monkeypatch.setattr("tapir_archicad_mcp.app.element_sets_enabled", False)
    _canned(fake_archicad, "GetAllElements", 150)

    result = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert len(result["elements"]) == 100
    assert result["envelope"] == {"elements": {"total": 150, "returned": 100, "truncated": True}}
    assert "next_page_token" in result
    assert "set" not in result
    assert len(set_store) == 0
    assert all(command != "API.GetTypesOfElements" for command, _ in fake_archicad.calls)


@pytest.mark.asyncio
async def test_filter_elements_result_becomes_a_set(fake_archicad, set_store):
    fake_archicad.on_tapir_command("FilterElements", {"elements": _elements(3)})
    fake_archicad.on_tapir_command("API.GetTypesOfElements", _types(3))

    result = await archicad_read_tool(
        "elements_filter_elements", {"port": PORT, "params": {"elements": _elements(5), "filters": ["IsEditable"]}}
    )

    assert result["elements"] == _elements(3)
    assert result["set"]["count"] == 3
    stored = set_store.get(result["set"]["set"], PORT, PROJECT)
    assert stored.origin["command"] == "elements_filter_elements"


def test_auto_set_table_matches_the_registered_read_commands(fake_archicad):
    assert AUTO_SET_FIELDS
    for name, field in AUTO_SET_FIELDS.items():
        assert name in TOOL_DISCOVERY_CATALOG, f"{name} is not a registered command"
        assert TOOL_DISCOVERY_CATALOG[name]["kind"] == "read", f"{name} is not a read command"
        pagination_field = TOOL_CALLABLE_REGISTRY[name].pagination_field
        assert pagination_field in (None, field), f"{name} paginates '{pagination_field}', the table says '{field}'"


def test_element_sets_are_on_by_default():
    assert PRODUCTION_DEFAULT is True


@pytest.mark.asyncio
async def test_summary_next_templates_name_the_set_and_port(fake_archicad):
    _canned(fake_archicad, "GetAllElements", 30)

    summary = (await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}}))["set"]

    set_id = summary["set"]
    assert re.fullmatch(r"set:[0-9a-f]{8}", set_id)
    assert summary["next"] == [
        f"archicad_read_tool(name='elements_change_selection_of_elements', arguments={{'port': {PORT}, "
        f"'params': {{'addElementsToSelection': {{'set': '{set_id}'}}}}}})",
        f"archicad_read_tool(name='navigator_fit_in_window', arguments={{'port': {PORT}, "
        f"'params': {{'elements': {{'set': '{set_id}'}}}}}})",
        f"archicad_read_tool(name='elements_filter_elements', arguments={{'port': {PORT}, "
        f"'params': {{'elements': {{'set': '{set_id}'}}, 'filters': [<filter such as 'IsEditable'>]}}}})",
    ]
    for template in summary["next"]:
        command = re.search(r"name='([a-z0-9_]+)'", template).group(1)
        assert command in TOOL_CALLABLE_REGISTRY, f"{command} is not a registered command"
        assert TOOL_CALLABLE_REGISTRY[command].kind == "read", f"{command} is not kind='read'"
        assert f"'set': '{set_id}'" in template
        assert f"'port': {PORT}" in template


@pytest.mark.asyncio
async def test_untitled_project_set_carries_a_warning(fake_archicad, set_store):
    fake_archicad.archicad_id = UntitledProjectID()
    _canned(fake_archicad, "GetAllElements", 30)

    summary = (await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}}))["set"]

    assert summary["warnings"] == [UNTITLED_WARNING]
    assert set_store.get(summary["set"], PORT, "untitled").project == "untitled"


REJECTED_BY_OFFICIAL_API = {
    "typesOfElements": [
        {"typeOfElement": {"elementId": {"guid": _guid(0)}, "elementType": "Wall"}},
        {"error": {"code": 7203, "message": "Element not supported"}},
        {"error": {"code": 7203, "message": "Element not supported"}},
    ]
}


TAPIR_TYPES_FAILED = (
    "The elements were read, but looking up the types of drafting elements through Tapir failed ({reason}); "
    "no set was created. Start the server with --element-sets off to get the raw list."
)


@pytest.mark.asyncio
async def test_types_the_official_api_rejects_come_from_tapir(fake_archicad):
    fake_archicad.on_tapir_command("GetAllElements", {"elements": _elements(3)})
    fake_archicad.on_tapir_command("API.GetTypesOfElements", REJECTED_BY_OFFICIAL_API)
    fake_archicad.on_tapir_command(
        "GetDetailsOfElements",
        {"detailsOfElements": [{"type": "Text"}, {"error": {"code": -1, "message": "Failed to get the details"}}]},
    )

    result = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert result["set"]["byType"] == {"Wall": 1, "Text": 1, "(unreadable)": 1}
    assert fake_archicad.calls[-1] == (
        "GetDetailsOfElements",
        {"elements": [{"elementId": {"guid": _guid(1)}}, {"elementId": {"guid": _guid(2)}}], "fields": ["type"]},
    )


@pytest.mark.asyncio
async def test_failing_tapir_type_lookup_is_explained(fake_archicad, set_store):
    fake_archicad.on_tapir_command("GetAllElements", {"elements": _elements(3)})
    fake_archicad.on_tapir_command("API.GetTypesOfElements", REJECTED_BY_OFFICIAL_API)
    post = fake_archicad.core.post_tapir_command

    def failing_details(command, parameters=None):
        if command == "GetDetailsOfElements":
            raise StandardCommandUnavailable("no fields filter")
        return post(command, parameters)

    fake_archicad.core.post_tapir_command = failing_details

    with pytest.raises(ToolError) as raised:
        await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert str(raised.value) == TAPIR_TYPES_FAILED.format(reason="no fields filter")
    assert len(set_store) == 0


@pytest.mark.asyncio
async def test_tapir_returning_a_different_number_of_types_is_refused(fake_archicad, set_store):
    fake_archicad.on_tapir_command("GetAllElements", {"elements": _elements(3)})
    fake_archicad.on_tapir_command("API.GetTypesOfElements", REJECTED_BY_OFFICIAL_API)
    fake_archicad.on_tapir_command("GetDetailsOfElements", {"detailsOfElements": [{"type": "Text"}]})

    with pytest.raises(ToolError) as raised:
        await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert str(raised.value) == TAPIR_TYPES_FAILED.format(reason="Tapir returned 1 type entries for 2 elements")
    assert len(set_store) == 0


@pytest.mark.asyncio
async def test_unknown_project_creates_no_set_and_keeps_the_paged_result(fake_archicad, set_store):
    fake_archicad.get_archicad_id = lambda timeout: APIResponseError(code=None, message="no answer")
    _canned(fake_archicad, "GetAllElements", 150)

    result = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert len(result["elements"]) == 100
    assert "next_page_token" in result
    assert "set" not in result
    assert result["setError"] == (
        "Could not read the project identity (no answer), so no set was created. "
        "The full element list is returned instead (paginated); retry once Archicad responds if you need a set."
    )
    assert len(set_store) == 0


@pytest.mark.asyncio
async def test_empty_result_makes_an_empty_set_without_asking_for_types(fake_archicad, set_store):
    fake_archicad.on_tapir_command("GetAllElements", {"elements": []})

    result = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert result["elements"] == []
    assert result["set"]["count"] == 0
    assert result["set"]["byType"] == {}
    assert all(command != "API.GetTypesOfElements" for command, _ in fake_archicad.calls)


@pytest.mark.asyncio
async def test_failing_types_call_fails_the_read_and_leaves_no_set(fake_archicad, set_store):
    fake_archicad.on_tapir_command("GetAllElements", {"elements": _elements(30)})  # no types response: the call raises

    with pytest.raises(Exception, match="GetTypesOfElements"):
        await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert len(set_store) == 0


TYPES_FAILED = (
    "The elements were read, but counting their types for the set summary failed ({reason}); no set was created. "
    "This needs Archicad 26 or later; start the server with --element-sets off to get the raw list."
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [StandardCommandUnavailable("Unknown command API.GetTypesOfElements"), CommandTimeoutError("timed out")],
)
async def test_archicad_error_in_the_type_count_is_explained(fake_archicad, set_store, error):
    fake_archicad.on_tapir_command("GetAllElements", {"elements": _elements(30)})

    def failing(command, parameters=None):
        raise error

    fake_archicad.core.post_command = failing

    with pytest.raises(ToolError) as raised:
        await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert str(raised.value) == TYPES_FAILED.format(reason=error.message)
    assert len(set_store) == 0


@pytest.mark.asyncio
async def test_type_count_with_a_different_number_of_entries_is_refused(fake_archicad, set_store):
    fake_archicad.on_tapir_command("GetAllElements", {"elements": _elements(30)})
    fake_archicad.on_tapir_command("API.GetTypesOfElements", _types(29))

    with pytest.raises(ToolError) as raised:
        await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert str(raised.value) == TYPES_FAILED.format(reason="Archicad returned 29 type entries for 30 elements")
    assert len(set_store) == 0


@pytest.mark.asyncio
async def test_threshold_counts_unique_elements(fake_archicad):
    unique = _elements(INLINE_THRESHOLD)
    fake_archicad.on_tapir_command("GetAllElements", {"elements": [*unique, unique[0]]})
    fake_archicad.on_tapir_command("API.GetTypesOfElements", _types(INLINE_THRESHOLD))

    result = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert result["elements"] == unique
    assert result["set"]["count"] == INLINE_THRESHOLD


@pytest.mark.asyncio
async def test_duplicates_above_the_threshold_are_counted_once(fake_archicad):
    unique = _elements(INLINE_THRESHOLD + 1)
    fake_archicad.on_tapir_command("GetAllElements", {"elements": [*unique, *unique]})
    fake_archicad.on_tapir_command("API.GetTypesOfElements", _types(INLINE_THRESHOLD + 1))

    result = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert "elements" not in result
    assert result["set"]["count"] == INLINE_THRESHOLD + 1
    types_calls = [parameters for command, parameters in fake_archicad.calls if command == "API.GetTypesOfElements"]
    assert len(types_calls[0]["elements"]) == INLINE_THRESHOLD + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("name", AUTO_SET_FIELDS)
async def test_every_auto_set_command_summarises_a_large_result(fake_archicad, set_store, name):
    assert AUTO_SET_PARAMS.keys() == AUTO_SET_FIELDS.keys()
    title = TOOL_DISCOVERY_CATALOG[name]["title"]
    for command in (title, f"API.{title}"):  # official commands are posted as API.<title>
        fake_archicad.on_tapir_command(command, {"elements": _elements(30)})
    fake_archicad.on_tapir_command("API.GetTypesOfElements", _types(30))
    params = AUTO_SET_PARAMS[name]
    arguments = {"port": PORT} if params is None else {"port": PORT, "params": params}

    result = await archicad_read_tool(name, arguments)

    assert "elements" not in result
    assert result["set"]["count"] == 30
    assert result["envelope"] == {"elements": {"total": 30, "returned": 0, "truncated": True}}
    assert set_store.get(result["set"]["set"], PORT, PROJECT).origin["command"] == name


@pytest.mark.asyncio
async def test_slow_read_still_returns_the_summary_through_the_job(fake_archicad, job_store):
    job_store.settings = JobSettings(async_threshold_seconds=0.01)
    _canned(fake_archicad, "GetAllElements", 150)
    release = threading.Event()
    original = fake_archicad.core.post_tapir_command

    def slow(command, parameters=None):
        if command == "GetAllElements":
            release.wait(5)
        return original(command, parameters)

    fake_archicad.core.post_tapir_command = slow

    started = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})
    assert started["status"] == "running"
    release.set()
    async with asyncio.timeout(5):
        response = await archicad_get_job(started["jobHandle"])
        while response["status"] == "running":
            response = await archicad_get_job(started["jobHandle"])

    result = response["result"]
    assert "elements" not in result
    assert result["set"]["count"] == 150
    assert result["envelope"] == {"elements": {"total": 150, "returned": 0, "truncated": True}}


def test_summarize_set_returns_count_types_and_call_templates(fake_archicad, set_store):
    fake_archicad.on_tapir_command("API.GetTypesOfElements", _types(4))
    element_set = set_store.create(PORT, PROJECT, [_guid(i) for i in range(4)], {"command": "test"})

    summary = summarize_set(fake_archicad, element_set, PORT, warnings=["careful"])

    assert summary == {
        "set": element_set.id,
        "count": 4,
        "byType": {"Wall": 2, "Slab": 1, "Column": 1},
        "createdAt": element_set.created_at.isoformat(),
        "warnings": ["careful"],
        "next": [
            f"archicad_read_tool(name='elements_change_selection_of_elements', arguments={{'port': {PORT}, "
            f"'params': {{'addElementsToSelection': {{'set': '{element_set.id}'}}}}}})",
            f"archicad_read_tool(name='navigator_fit_in_window', arguments={{'port': {PORT}, "
            f"'params': {{'elements': {{'set': '{element_set.id}'}}}}}})",
            f"archicad_read_tool(name='elements_filter_elements', arguments={{'port': {PORT}, "
            f"'params': {{'elements': {{'set': '{element_set.id}'}}, 'filters': [<filter such as 'IsEditable'>]}}}})",
        ],
    }
