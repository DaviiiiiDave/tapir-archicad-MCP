import hashlib
import re

import pytest
from multiconn_archicad import APIResponseError, UntitledProjectID

import tapir_archicad_mcp.app
from tapir_archicad_mcp.auto_sets import AUTO_SET_FIELDS, INLINE_THRESHOLD, UNTITLED_WARNING
from tapir_archicad_mcp.tools.custom.functions import archicad_read_tool
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


def _canned(fake_archicad, command: str, count: int) -> None:
    fake_archicad.on_tapir_command(command, {"elements": _elements(count)})
    fake_archicad.on_tapir_command("API.GetTypesOfElements", _types(count))


@pytest.mark.asyncio
async def test_large_result_is_replaced_by_a_summary(fake_archicad, set_store):
    _canned(fake_archicad, "GetAllElements", 150)

    result = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert "elements" not in result
    assert "next_page_token" not in result
    assert "envelope" not in result
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
    assert "elements" not in above_threshold
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

    assert summary["next"] == {
        "rows": (
            f"archicad_read_tool(name='sets_rows', arguments={{'port': {PORT}, "
            f"'params': {{'set': '{summary['set']}', 'columns': [...]}}}})"
        ),
        "aggregate": (
            f"archicad_read_tool(name='sets_aggregate', arguments={{'port': {PORT}, "
            f"'params': {{'set': '{summary['set']}', 'groupBy': ...}}}})"
        ),
    }
    assert re.fullmatch(r"set:[0-9a-f]{8}", summary["set"])


@pytest.mark.asyncio
async def test_untitled_project_set_carries_a_warning(fake_archicad, set_store):
    fake_archicad.archicad_id = UntitledProjectID()
    _canned(fake_archicad, "GetAllElements", 30)

    summary = (await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}}))["set"]

    assert summary["warnings"] == [UNTITLED_WARNING]
    assert set_store.get(summary["set"], PORT, "untitled").project == "untitled"


@pytest.mark.asyncio
async def test_unreadable_types_are_counted_separately(fake_archicad):
    fake_archicad.on_tapir_command("GetAllElements", {"elements": _elements(3)})
    fake_archicad.on_tapir_command(
        "API.GetTypesOfElements",
        {
            "typesOfElements": [
                {"typeOfElement": {"elementId": {"guid": _guid(0)}, "elementType": "Wall"}},
                {"error": {"code": 1, "message": "not found"}},
                {"error": {"code": 1, "message": "not found"}},
            ]
        },
    )

    result = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert result["set"]["byType"] == {"Wall": 1, "(unreadable)": 2}


@pytest.mark.asyncio
async def test_unknown_project_creates_no_set_and_keeps_the_paged_result(fake_archicad, set_store):
    fake_archicad.get_archicad_id = lambda timeout: APIResponseError(code=None, message="no answer")
    _canned(fake_archicad, "GetAllElements", 150)

    result = await archicad_read_tool("elements_get_all_elements", {"port": PORT, "params": {}})

    assert len(result["elements"]) == 100
    assert "next_page_token" in result
    assert "set" not in result
    assert result["setError"] == "no answer; no set was created"
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
