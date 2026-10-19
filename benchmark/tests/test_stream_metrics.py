import pytest
from bench.stream_metrics import classify_tool_error, parse_stream
from streams import assistant, hook_started, init, lines, rate_limit, result, text, tool_result, tool_use


def test_tokens_and_cost_come_from_the_result_event():
    metrics = parse_stream(
        lines(
            init(),
            assistant("msg_1", [text("OK")], input_tokens=10, cache_creation=5833, output_tokens=6),
            result(input_tokens=10, cache_creation=5833, cache_read=0, output_tokens=156, cost=0.00808125),
        )
    )

    assert metrics.output_tokens == 156
    assert metrics.total_tokens == 5999
    assert metrics.cost_usd == 0.00808125
    assert metrics.complete is True
    assert metrics.result_subtype == "success"
    assert metrics.context_window == 200000


def test_repeated_assistant_events_count_once():
    call = tool_use(
        "toolu_1",
        "mcp__archicad__archicad_call_tool",
        {"name": "elements_get_all_elements", "arguments": {"port": 19725, "params": {}}},
    )
    metrics = parse_stream(
        lines(
            assistant("msg_1", [call], input_tokens=1000),
            assistant("msg_1", [call], input_tokens=1000),
            tool_result("toolu_1", "{}"),
            result(input_tokens=1000),
        )
    )

    assert len(metrics.tool_calls) == 1
    assert metrics.peak_context_tokens == 1000


def test_peak_context_is_the_largest_single_request():
    metrics = parse_stream(
        lines(
            assistant("msg_1", [text("a")], input_tokens=100, cache_read=900),
            assistant("msg_2", [text("b")], input_tokens=50, cache_read=4000, cache_creation=950),
            assistant("msg_3", [text("c")], input_tokens=10, cache_read=2990),
            result(),
        )
    )

    assert metrics.peak_context_tokens == 5000


@pytest.mark.parametrize(
    ("tool", "tool_input", "inner", "port"),
    [
        (
            "mcp__archicad__archicad_call_tool",
            {"name": "elements_get_all_elements", "arguments": {"port": 19725, "params": {}}},
            "elements_get_all_elements",
            19725,
        ),
        (
            "mcp__archicad__archicad_read_tool",
            {"name": "elements_get_all_elements", "arguments": {"port": 19725, "params": {}}},
            "elements_get_all_elements",
            19725,
        ),
        ("mcp__archicad__discovery_list_active_archicads", {}, None, None),
    ],
)
def test_inner_command_and_port_come_from_meta_tool_arguments(tool, tool_input, inner, port):
    metrics = parse_stream(lines(assistant("msg_1", [tool_use("toolu_1", tool, tool_input)]), result()))

    (call,) = metrics.tool_calls
    assert call.tool == tool
    assert call.inner_command == inner
    assert call.port == port


def test_tool_result_bytes_are_utf8_lengths():
    metrics = parse_stream(
        lines(
            assistant(
                "msg_1",
                [
                    tool_use("toolu_1", "mcp__archicad__archicad_get_job", {}),
                    tool_use("toolu_2", "mcp__archicad__archicad_get_job", {}),
                ],
            ),
            tool_result("toolu_1", "ä"),
            tool_result("toolu_2", [{"type": "text", "text": "äb"}]),
            result(),
        )
    )

    assert [call.result_bytes for call in metrics.tool_calls] == [2, 3]
    assert metrics.tool_result_bytes == 5


def test_failed_tool_result_gets_an_error_class():
    metrics = parse_stream(
        lines(
            assistant(
                "msg_1",
                [
                    tool_use("toolu_1", "mcp__archicad__archicad_call_tool", {"name": "x", "arguments": {}}),
                    tool_use("toolu_2", "mcp__archicad__archicad_get_job", {}),
                ],
            ),
            tool_result(
                "toolu_1",
                "Error executing tool archicad_call_tool: Invalid arguments provided for tool 'x'.",
                is_error=True,
            ),
            tool_result("toolu_2", "{}"),
            result(),
        )
    )

    failed, ok = metrics.tool_calls
    assert (failed.is_error, failed.error_class) == (True, "validation")
    assert (ok.is_error, ok.error_class) == (False, None)


def test_hook_events_are_collected():
    metrics = parse_stream(lines(hook_started("SessionStart:startup"), init(), result()))

    assert metrics.hook_events == ["SessionStart:startup"]
    assert metrics.init["model"] == "claude-haiku-5-5"


def test_rate_limit_status_is_kept():
    metrics = parse_stream(lines(init(), rate_limit("allowed"), rate_limit("rejected"), result()))

    assert metrics.rate_limit_status == "rejected"


def test_missing_result_event_marks_run_incomplete():
    cut_off_line = '{"type": "assistant", "message": {"id": "msg_3"'
    metrics = parse_stream(
        lines(
            init(),
            assistant("msg_1", [text("a")], input_tokens=100, cache_read=900, output_tokens=7),
            assistant("msg_2", [text("b")], input_tokens=20, cache_read=1000, cache_creation=30, output_tokens=5),
        )
        + [cut_off_line]
    )

    assert metrics.complete is False
    assert metrics.result_subtype is None
    assert metrics.turns == 2
    assert (metrics.input_tokens, metrics.cache_read_tokens, metrics.cache_creation_tokens, metrics.output_tokens) == (
        120,
        1900,
        30,
        12,
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Error executing tool archicad_call_tool: Invalid arguments provided for tool 'elements_get_all_elements'.",
            "validation",
        ),
        ("'elements_create_walls' changes the project; archicad_read_tool is read-only.", "refused"),
        ('This command cannot be undone. Ask the user to confirm, then call again with "confirm": true.', "refused"),
        ("Archicad returned an error. Error code: 7203, Element not supported", "archicad"),
        ("APIERR_BADID", "archicad"),
        ("Error: the connection to the server was lost before the response arrived.", "transport"),
        ("Request timed out", "transport"),
        ("[bench-proxy] blocked: port 19723 is not the benchmark instance", "blocked"),
        ("Something unexpected", "other"),
    ],
)
def test_classify_tool_error(message, expected):
    assert classify_tool_error(message) == expected
