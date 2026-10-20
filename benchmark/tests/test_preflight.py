import pytest
from bench.preflight import check_init, context_probe_ok
from bench.stream_metrics import parse_stream
from streams import hook_started, init, lines, result

ARCHICAD_TOOLS = ["mcp__archicad__archicad_call_tool", "mcp__archicad__discovery_list_active_archicads"]


def test_check_init_accepts_clean_mcp_run():
    metrics = parse_stream(
        lines(
            init(tools=ARCHICAD_TOOLS, mcp_servers=[{"name": "archicad", "status": "connected"}]),
            result(),
        )
    )

    assert check_init(metrics, expect_server=True, expected_model="claude-haiku-5-5") == []


def test_check_init_flags_hooks_plugins_foreign_tools_and_wrong_model():
    denied = result()
    denied["permission_denials"] = [{"tool_name": "mcp__archicad__archicad_call_tool", "tool_use_id": "toolu_1"}]
    metrics = parse_stream(
        lines(
            hook_started("SessionStart:startup"),
            init(
                model="claude-haiku-4-5-20251001",
                tools=[*ARCHICAD_TOOLS, "Bash"],
                mcp_servers=[{"name": "archicad", "status": "failed"}],
                plugins=[{"name": "superpowers", "path": "C:/plugins/superpowers"}],
            ),
            denied,
        )
    )

    problems = check_init(metrics, expect_server=True, expected_model="claude-haiku-5-5")

    joined = "\n".join(problems)
    assert len(problems) == 6
    for needle in ("SessionStart:startup", "superpowers", "Bash", "failed", "claude-haiku-4-5-20251001", "permission"):
        assert needle in joined


def test_check_init_rejects_servers_when_none_are_expected():
    metrics = parse_stream(lines(init(mcp_servers=[{"name": "archicad", "status": "connected"}]), result()))

    assert check_init(metrics, expect_server=False, expected_model="claude-haiku-5-5") != []


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("NONE", True),
        ("# currentDate", True),
        ("  # currentDate\n", True),
        ("# currentDate\n# Persona — Mein Claude", False),
        ("C:\\Users\\davet\\.claude\\CLAUDE.md", False),
        ("", False),
    ],
)
def test_context_probe_accepts_only_none_or_current_date(answer, ok):
    assert context_probe_ok(answer) is ok
