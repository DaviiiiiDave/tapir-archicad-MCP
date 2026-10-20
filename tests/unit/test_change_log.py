import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from multiconn_archicad import (
    APIResponseError,
    SoloProjectID,
    TeamworkCredentials,
    TeamworkProjectID,
    UntitledProjectID,
)
from multiconn_archicad.errors import CommandTimeoutError, StandardAPIError

from tapir_archicad_mcp.change_log import ChangeLog, default_change_log_dir
from tapir_archicad_mcp.context import change_log_instance
from tapir_archicad_mcp.project_identity import project_fingerprint
from tapir_archicad_mcp.tools.custom.functions import archicad_call_tool, archicad_read_tool
from tapir_archicad_mcp.tools.tool_registry import register_tool_for_dispatch


def _lines(change_log: ChangeLog) -> list[dict]:
    return [
        json.loads(line)
        for path in sorted(change_log.directory.glob("changes-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.asyncio
async def test_write_command_is_logged(fake_archicad, change_log):
    register_tool_for_dispatch(
        Mock(return_value={"ok": True}), name="test_write", title="T", description="D", kind="write"
    )

    result = await archicad_call_tool("test_write", {"port": 19723})

    (entry,) = _lines(change_log)
    assert entry["command"] == "test_write"
    assert entry["kind"] == "write"
    assert entry["status"] == "completed"
    assert entry["project"] == "/path/to/fake_project.pln"
    assert entry["port"] == 19723
    assert entry["jobHandle"].startswith("job:")
    assert result["changeLog"] == str(next(change_log.directory.glob("changes-*.jsonl")))


@pytest.mark.asyncio
async def test_read_command_is_not_logged(fake_archicad, change_log):
    fake_archicad.on_tapir_command("GetAddOnVersion", {"version": "1.7.1"})

    result = await archicad_read_tool("app_get_add_on_version", {"port": 19723})

    assert _lines(change_log) == []
    assert "changeLog" not in result


@pytest.mark.asyncio
async def test_failed_write_is_logged_as_failed(fake_archicad, change_log):
    register_tool_for_dispatch(
        Mock(side_effect=RuntimeError("boom")), name="test_failing_write", title="T", description="D", kind="write"
    )

    with pytest.raises(Exception):
        await archicad_call_tool("test_failing_write", {"port": 19723})

    (entry,) = _lines(change_log)
    assert entry["status"] == "failed"
    assert "boom" in entry["error"]


@pytest.mark.asyncio
async def test_unwritable_change_log_does_not_hide_the_result(fake_archicad, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    change_log_instance.set(ChangeLog(blocker))
    register_tool_for_dispatch(
        Mock(return_value={"ok": True}), name="test_write_2", title="T", description="D", kind="write"
    )

    result = await archicad_call_tool("test_write_2", {"port": 19723})

    assert result["ok"] is True
    assert "changeLogError" in result


@pytest.mark.asyncio
async def test_failed_command_with_unwritable_log_reports_both(fake_archicad, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    change_log_instance.set(ChangeLog(blocker))
    register_tool_for_dispatch(
        Mock(side_effect=StandardAPIError(code=7, message="bad input")),
        name="test_failing_unlogged", title="T", description="D", kind="write",
    )

    with pytest.raises(Exception) as raised:
        await archicad_call_tool("test_failing_unlogged", {"port": 19723})

    assert "bad input" in str(raised.value)
    assert "Change log could not be written" in str(raised.value)


@pytest.mark.asyncio
async def test_timed_out_command_with_unwritable_log_keeps_the_transport_cause(fake_archicad, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    change_log_instance.set(ChangeLog(blocker))
    register_tool_for_dispatch(
        Mock(side_effect=CommandTimeoutError("timed out")),
        name="test_timeout_unlogged", title="T", description="D", kind="write",
    )

    with pytest.raises(Exception) as raised:
        await archicad_call_tool("test_timeout_unlogged", {"port": 19723})

    assert "Change log could not be written" in str(raised.value)
    # jobs._failure_details reads the cause to add "outcome may be unknown".
    assert isinstance(raised.value.__cause__, CommandTimeoutError)


@pytest.mark.asyncio
async def test_transport_failure_is_logged_as_unknown(fake_archicad, change_log):
    register_tool_for_dispatch(
        Mock(side_effect=CommandTimeoutError("timed out")),
        name="test_timeout_write", title="T", description="D", kind="write",
    )

    with pytest.raises(Exception):
        await archicad_call_tool("test_timeout_write", {"port": 19723})

    (entry,) = _lines(change_log)
    assert entry["status"] == "unknown"


@pytest.mark.asyncio
async def test_project_is_read_fresh_at_log_time(fake_archicad, change_log):
    fake_archicad.get_archicad_id = lambda timeout: SoloProjectID(projectName="Other", projectPath="/p/other.pln")
    register_tool_for_dispatch(
        Mock(return_value={"ok": True}), name="test_fresh_project", title="T", description="D", kind="write"
    )

    await archicad_call_tool("test_fresh_project", {"port": 19723})

    (entry,) = _lines(change_log)
    assert entry["project"] == "/p/other.pln"


def _raise(error: Exception):
    def get_archicad_id(timeout):
        raise error

    return get_archicad_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity, expected_error",
    [
        (lambda timeout: APIResponseError(code=None, message="no answer"), "no answer"),
        (_raise(RuntimeError("socket gone")), "socket gone"),
    ],
)
async def test_unreadable_project_is_logged_as_unknown_not_untitled(
    fake_archicad, change_log, identity, expected_error
):
    fake_archicad.get_archicad_id = identity
    register_tool_for_dispatch(
        Mock(return_value={"ok": True}), name="test_unknown_project", title="T", description="D", kind="write"
    )

    result = await archicad_call_tool("test_unknown_project", {"port": 19723})

    (entry,) = _lines(change_log)
    assert entry["project"] == "unknown"
    assert expected_error in entry["projectError"]
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_write_command_returning_nothing_still_reports_the_change_log(fake_archicad, change_log):
    register_tool_for_dispatch(
        Mock(return_value=None), name="test_write_none", title="T", description="D", kind="write"
    )

    result = await archicad_call_tool("test_write_none", {"port": 19723})

    assert result["changeLog"] == str(next(change_log.directory.glob("changes-*.jsonl")))


@pytest.mark.asyncio
async def test_change_log_error_is_never_empty(fake_archicad):
    change_log_instance.set(Mock(record=Mock(side_effect=OSError())))
    register_tool_for_dispatch(
        Mock(return_value={"ok": True}), name="test_write_3", title="T", description="D", kind="write"
    )

    result = await archicad_call_tool("test_write_3", {"port": 19723})

    assert result["changeLogError"].startswith("OSError")


def test_line_separators_in_values_cannot_split_an_entry(tmp_path):
    path = ChangeLog(tmp_path).record({"command": "a b cd"})

    text = path.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 1
    assert json.loads(text)["command"] == "a b cd"


def test_entries_go_to_a_file_per_utc_day(tmp_path):
    clock = Mock(return_value=datetime(2026, 10, 19, 23, 59, tzinfo=UTC))
    log = ChangeLog(tmp_path, clock=clock)

    first = log.record({"command": "a"})
    clock.return_value = datetime(2026, 10, 20, 0, 1, tzinfo=UTC)
    second = log.record({"command": "b"})

    assert first.name == "changes-2026-10-19.jsonl"
    assert second.name == "changes-2026-10-20.jsonl"
    assert json.loads(first.read_text())["time"].startswith("2026-10-19T23:59")


def test_concurrent_records_from_many_threads_are_all_kept(tmp_path):
    log = ChangeLog(tmp_path)

    def write(worker: int) -> None:
        for i in range(60):
            log.record({"worker": worker, "i": i, "padding": "x" * 200})

    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(write, range(32)))

    lines = [line for path in tmp_path.glob("changes-*.jsonl") for line in path.read_text().splitlines()]
    assert len(lines) == 32 * 60
    assert len({(json.loads(line)["worker"], json.loads(line)["i"]) for line in lines}) == 32 * 60


def test_concurrent_records_from_two_processes_are_all_kept(tmp_path):
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from tapir_archicad_mcp.change_log import ChangeLog\n"
        "log = ChangeLog(Path(sys.argv[1]))\n"
        "for i in range(150):\n"
        "    log.record({'proc': sys.argv[2], 'i': i, 'padding': 'x' * 200})\n"
    )
    processes = [
        subprocess.Popen([sys.executable, "-c", script, str(tmp_path), name], stderr=subprocess.PIPE)
        for name in ("a", "b")
    ]
    for process in processes:
        assert process.wait(timeout=60) == 0, process.stderr.read().decode()

    lines = [line for path in tmp_path.glob("changes-*.jsonl") for line in path.read_text().splitlines()]
    assert len(lines) == 300
    assert all(json.loads(line) for line in lines)


def test_fingerprints():
    team = TeamworkProjectID(
        projectName="Tower",
        projectPath="P/Tower",
        serverAddress="https://bim.local",
        teamworkCredentials=TeamworkCredentials(username="user"),
    )

    assert project_fingerprint(SoloProjectID(projectName="House", projectPath="/p/h.pln")) == "/p/h.pln"
    assert project_fingerprint(team) == "teamwork://https://bim.local/P/Tower"
    assert project_fingerprint(UntitledProjectID()) == "untitled"


def test_default_directory_is_an_absolute_per_user_path():
    assert default_change_log_dir().name == "change-log"
    assert default_change_log_dir().is_absolute()
    assert isinstance(default_change_log_dir(), Path)
