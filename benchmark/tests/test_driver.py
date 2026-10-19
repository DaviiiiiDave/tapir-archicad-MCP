import json
import subprocess
import sys

from bench.driver import build_claude_command, run_claude, write_mcp_config
from configs import make_config


def flag_value(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_claude_command_has_isolation_flags(tmp_path):
    cfg = make_config(tmp_path)
    mcp_config = tmp_path / "mcp.json"

    cmd = build_claude_command(cfg, "sonnet", mcp_config, system_prompt="System.")

    assert cmd[0] == str(cfg.claude_exe)
    assert "-p" in cmd
    assert flag_value(cmd, "--model") == "claude-sonnet-5-5"
    assert flag_value(cmd, "--max-budget-usd") == "6.00"
    assert flag_value(cmd, "--system-prompt") == "System."
    assert flag_value(cmd, "--tools") == ""
    assert flag_value(cmd, "--setting-sources") == "project"
    assert flag_value(cmd, "--mcp-config") == str(mcp_config)
    assert flag_value(cmd, "--allowedTools") == "mcp__archicad"
    assert flag_value(cmd, "--permission-mode") == "dontAsk"
    assert flag_value(cmd, "--output-format") == "stream-json"
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--verbose"):
        assert flag in cmd
    assert "--bare" not in cmd


def test_claude_command_without_server_still_ignores_configured_servers(tmp_path):
    cmd = build_claude_command(make_config(tmp_path), "haiku", None, system_prompt="System.")

    assert "--strict-mcp-config" in cmd
    assert "--mcp-config" not in cmd
    assert "--allowedTools" not in cmd


def test_claude_command_excludes_every_claude_md(tmp_path):
    cmd = build_claude_command(make_config(tmp_path), "haiku", None, system_prompt="System.")

    settings = json.loads(flag_value(cmd, "--settings"))
    assert "**/CLAUDE.md" in settings["claudeMdExcludes"]


def test_mcp_config_points_at_the_proxy(tmp_path):
    path = tmp_path / "mcp.json"

    write_mcp_config(path, 8123)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "mcpServers": {"archicad": {"type": "http", "url": "http://127.0.0.1:8123/mcp"}}
    }


def test_run_claude_feeds_the_prompt_on_stdin(tmp_path):
    echo = [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"]

    outcome = run_claude(make_config(tmp_path), echo, "reply with ok", tmp_path, timeout_s=60)

    assert (outcome.exit_code, outcome.timed_out) == (0, False)
    assert outcome.stream_path.read_text(encoding="utf-8") == "REPLY WITH OK"


def test_timeout_marks_run_and_kills_process(tmp_path):
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]

    outcome = run_claude(make_config(tmp_path), sleeper, "", tmp_path, timeout_s=1)

    assert outcome.timed_out is True
    assert outcome.wall_s < 30
    if sys.platform == "win32":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {outcome.pid}", "/NH"], capture_output=True, text=True, check=True
        ).stdout
        assert str(outcome.pid) not in listing
