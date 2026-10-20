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
    assert "next_page_token" in first
    assert second["envelope"] == {"elements": {"total": 150, "returned": 50, "truncated": False}}
    assert "next_page_token" not in second


def test_outcome_summarises_a_single_execution_result_as_one_item():
    failed = summarize_execution_results({"executionResult": {"success": False, "error": {"code": 1, "message": "x"}}})
    succeeded = summarize_execution_results({"executionResult": {"success": True}})

    assert (failed["requested"], failed["succeeded"], failed["failed"]) == (1, 0, 1)
    assert failed["failuresByCause"] == [{"code": 1, "message": "x", "count": 1, "examples": [0]}]
    assert (succeeded["requested"], succeeded["succeeded"], succeeded["failed"]) == (1, 1, 0)


def test_outcome_counts_reservation_conflicts():
    conflicts = [{"elementId": {"guid": "g1"}, "user": {"userId": 1, "userName": "a"}}] * 2

    summary = summarize_execution_results({"executionResult": {"success": True}, "conflicts": conflicts})

    assert summary["conflicts"] == 2


@pytest.mark.parametrize("conflicts", [None, []])
def test_outcome_has_no_conflicts_key_without_conflicts(conflicts):
    summary = summarize_execution_results({"executionResult": {"success": True}, "conflicts": conflicts})

    assert "conflicts" not in summary


def test_outcome_keeps_top_ten_causes_and_counts_the_rest():
    failures = [
        {"success": False, "error": {"code": 1, "message": f"element {cause}"}}
        for cause in range(12)
        for _ in range(12 - cause)
    ]

    summary = summarize_execution_results({"executionResults": failures})

    assert len(summary["failuresByCause"]) == 10
    assert summary["failuresByCause"][0]["count"] == 12
    assert summary["otherCauses"] == {"causes": 2, "count": 2 + 1}
    shown = sum(cause["count"] for cause in summary["failuresByCause"])
    assert shown + summary["otherCauses"]["count"] == summary["failed"]


@pytest.mark.asyncio
async def test_reservation_with_conflicts_carries_outcome(fake_archicad):
    guid = "00000000-0000-0000-0000-000000000001"
    conflict = {"elementId": {"guid": guid}, "user": {"userId": 2, "userName": "other"}}
    fake_archicad.on_tapir_command("ReserveElements", {"executionResult": {"success": True}, "conflicts": [conflict]})
    arguments = {"port": 19723, "confirm": True, "params": {"elements": [{"elementId": {"guid": guid}}]}}

    result = await archicad_call_tool("teamwork_reserve_elements", arguments)

    assert result["outcome"]["requested"] == 1
    assert result["outcome"]["conflicts"] == 1


def test_outcome_has_no_other_causes_within_the_limit():
    failure = {"success": False, "error": {"code": 1, "message": "x"}}

    assert "otherCauses" not in summarize_execution_results({"executionResults": [failure]})


def test_outcome_keeps_the_text_of_a_string_error():
    summary = summarize_execution_results({"executionResults": [{"success": False, "error": "boom"}]})

    assert summary["failuresByCause"] == [{"code": None, "message": "boom", "count": 1, "examples": [0]}]


def _is_list_field(field) -> bool:
    import types
    import typing

    annotation = field.annotation
    options = (
        typing.get_args(annotation)
        if typing.get_origin(annotation) in (typing.Union, types.UnionType)
        else (annotation,)
    )
    return any(typing.get_origin(option) is list for option in options)


def _changing_tools():
    """Yield (title, namespace, result model or None) of every registered write or irreversible command."""
    from multiconn_archicad.models.official import commands as official
    from multiconn_archicad.models.tapir import commands as tapir

    from tapir_archicad_mcp.tools.tool_registry import TOOL_CALLABLE_REGISTRY, TOOL_DISCOVERY_CATALOG

    modules = {"tapir": tapir, "official": official}
    for name, entry in TOOL_CALLABLE_REGISTRY.items():
        # Other tests register fake tools in this registry.
        if entry.kind == "read" or not entry.callable.__module__.startswith("tapir_archicad_mcp.tools."):
            continue
        namespace = next((space for space in modules if f".{space}." in entry.callable.__module__), None)
        assert namespace, f"{name} is neither a Tapir nor an official command"
        title = TOOL_DISCOVERY_CATALOG[name]["title"]
        yield title, namespace, getattr(modules[namespace], f"{title}Result", None)


def _has_execution_results(model) -> bool:
    fields = getattr(model, "model_fields", {})
    return "executionResults" in fields or "executionResult" in fields


def _uncovered_titles() -> set[str]:
    """Titles of changing commands that neither the default fields nor the RESULT_LIST_FIELDS table cover."""
    from tapir_archicad_mcp.outcome import RESULT_LIST_FIELDS

    return {
        title
        for title, namespace, model in _changing_tools()
        if not (_has_execution_results(model) or (namespace == "tapir" and title in RESULT_LIST_FIELDS))
    }


def test_every_changing_command_result_is_summarisable_or_allowlisted(fake_archicad):
    from tapir_archicad_mcp.outcome import NO_ITEM_RESULTS

    assert not _uncovered_titles() - NO_ITEM_RESULTS.keys(), "Add to RESULT_LIST_FIELDS or NO_ITEM_RESULTS"


def test_result_list_fields_are_list_fields_of_the_tapir_result_models(fake_archicad):
    from multiconn_archicad.models.tapir import commands as tapir

    from tapir_archicad_mcp.outcome import RESULT_LIST_FIELDS

    for title, fields in RESULT_LIST_FIELDS.items():
        model_fields = getattr(tapir, f"{title}Result").model_fields
        for field in fields:
            assert field in model_fields and _is_list_field(model_fields[field]), (title, field)


def test_no_item_results_allowlist_has_no_stale_entries(fake_archicad):
    from tapir_archicad_mcp.outcome import NO_ITEM_RESULTS

    assert set(NO_ITEM_RESULTS) == _uncovered_titles()
    assert all(NO_ITEM_RESULTS.values())
