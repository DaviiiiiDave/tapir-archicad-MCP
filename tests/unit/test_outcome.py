import pytest

from tapir_archicad_mcp.outcome import summarize_execution_results
from tapir_archicad_mcp.tools.custom.functions import archicad_call_tool, archicad_read_tool


def test_outcome_groups_failures_by_cause():
    result = {
        "executionResults": [
            {"success": True},
            {"success": False, "error": {"code": 4003, "message": "locked"}},
            {"success": False, "error": {"code": 4003, "message": "locked"}},
            {"success": False, "error": {"code": 4012, "message": "hotlink"}},
        ]
    }

    summary = summarize_execution_results(result)

    assert (summary["requested"], summary["succeeded"], summary["failed"]) == (4, 1, 3)
    assert summary["failuresByCause"][0] == {"code": 4003, "message": "locked", "count": 2, "examples": [1, 2]}
    assert summary["failuresByCause"][1] == {"code": 4012, "message": "hotlink", "count": 1, "examples": [3]}


def test_outcome_limits_examples_to_three():
    failure = {"success": False, "error": {"code": 1, "message": "x"}}

    summary = summarize_execution_results({"executionResults": [failure] * 5})

    assert summary["failuresByCause"][0]["count"] == 5
    assert summary["failuresByCause"][0]["examples"] == [0, 1, 2]


@pytest.mark.parametrize("result", [{"elements": []}, [], None, "text"])
def test_no_outcome_without_execution_results(result):
    assert summarize_execution_results(result) is None


def test_outcome_reads_tabled_result_lists_with_error_items():
    result = {
        "elements": [
            {"elementId": {"guid": "00000000-0000-0000-0000-000000000001"}},
            {"error": {"code": 4003, "message": "locked layer"}},
            {"error": {"code": 4003, "message": "locked layer"}},
        ]
    }

    summary = summarize_execution_results(result, ("elements",))

    assert (summary["requested"], summary["succeeded"], summary["failed"]) == (3, 1, 2)
    assert summary["failuresByCause"] == [{"code": 4003, "message": "locked layer", "count": 2, "examples": [1, 2]}]


def test_outcome_without_tabled_field_in_response_is_none():
    assert summarize_execution_results({"other": []}, ("elements",)) is None


@pytest.mark.asyncio
async def test_partially_failed_create_walls_carries_outcome(fake_archicad):
    ok = {"elementId": {"guid": "00000000-0000-0000-0000-000000000001"}}
    failed = {"error": {"code": 4010, "message": "bad polygon"}}
    fake_archicad.on_tapir_command("CreateWalls", {"elements": [ok, failed]})

    result = await archicad_call_tool("elements_create_walls", {"port": 19723, "params": {"wallsData": []}})

    assert result["outcome"]["failed"] == 1
    assert result["outcome"]["failuresByCause"][0]["message"] == "bad polygon"


def test_every_tabled_command_is_a_registered_changing_command(fake_archicad):
    from tapir_archicad_mcp.outcome import RESULT_LIST_FIELDS
    from tapir_archicad_mcp.tools.tool_registry import TOOL_DISCOVERY_CATALOG

    by_title = {entry["title"]: entry for entry in TOOL_DISCOVERY_CATALOG.values()}
    for command in RESULT_LIST_FIELDS:
        assert command in by_title, command
        assert by_title[command]["kind"] in ("write", "irreversible"), command


@pytest.mark.asyncio
async def test_write_response_carries_outcome(fake_archicad):
    fake_archicad.on_tapir_command(
        "DeleteElements",
        {"executionResults": [{"success": True}, {"success": False, "error": {"code": 4003, "message": "locked"}}]},
    )
    guid = "00000000-0000-0000-0000-000000000001"
    arguments = {"port": 19723, "confirm": True, "params": {"elements": [{"elementId": {"guid": guid}}] * 2}}

    result = await archicad_call_tool("elements_delete_elements", arguments)

    assert result["outcome"]["failed"] == 1
    assert result["outcome"]["succeeded"] == 1
    assert len(result["executionResults"]) == 2


@pytest.mark.asyncio
async def test_read_response_has_no_outcome(fake_archicad):
    fake_archicad.on_tapir_command("GetAddOnVersion", {"executionResults": [{"success": True}]})

    result = await archicad_read_tool("app_get_add_on_version", {"port": 19723})

    assert "outcome" not in result


@pytest.mark.asyncio
async def test_pages_carry_envelope(fake_archicad):
    elements = [{"elementId": {"guid": f"00000000-0000-0000-0000-{i:012d}"}} for i in range(150)]
    fake_archicad.on_tapir_command("GetAllElements", {"elements": elements})
    arguments = {"port": 19723, "params": {}}

    first = await archicad_read_tool("elements_get_all_elements", arguments)
    second = await archicad_read_tool(
        "elements_get_all_elements", {**arguments, "page_token": first["next_page_token"]}
    )

    assert first["envelope"] == {"elements": {"total": 150, "returned": 100, "truncated": True}}
    assert second["envelope"] == {"elements": {"total": 150, "returned": 50, "truncated": True}}
