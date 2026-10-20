import json
import socket

import httpx
import pytest
from bench.proxy import MALFORMED, ProxyRules, create_proxy_app, serve_in_thread
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

KINDS = {
    "elements_get_all_elements": "read",
    "properties_set_property_values_of_elements": "write",
    "elements_create_doors": "write",
    "elements_delete_elements": "irreversible",
    "project_open_project": "irreversible",
    "favorites_export_favorites": "write",
    "issues_export_issues_to_bcf": "write",
    "project_print_view": "write",
    "navigator_publish_publisher_set": "irreversible",
}


class FakeUpstream:
    """Answers every JSON-RPC request with a fixed tool result, framed as JSON or as one SSE event."""

    def __init__(self, framing="json", error_ids=()):
        self.framing = framing
        self.error_ids = set(error_ids)
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self.app = Starlette(routes=[Route("/mcp", self.handle, methods=["POST"])])

    async def handle(self, request: Request) -> Response:
        body = json.loads(await request.body())
        self.requests.append(body)
        self.headers.append(dict(request.headers))
        if body.get("method") == "tools/call":
            payload = {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"content": [{"type": "text", "text": "ok"}], "isError": body["id"] in self.error_ids},
            }
        else:
            payload = {"jsonrpc": "2.0", "id": body.get("id"), "result": {"protocolVersion": "2025-06-18"}}
        headers = {"mcp-session-id": "session-from-upstream"}
        if self.framing == "sse":
            content = f"event: message\r\ndata: {json.dumps(payload)}\r\n\r\n"
            return Response(content, media_type="text/event-stream", headers=headers)
        return Response(json.dumps(payload), media_type="application/json", headers=headers)


def call(request_id, tool, arguments):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }


def archicad_call(request_id, command, port=19725, tool="archicad_call_tool"):
    return call(request_id, tool, {"name": command, "arguments": {"port": port, "params": {}}})


def result_of(response: httpx.Response) -> dict:
    if response.headers["content-type"].startswith("text/event-stream"):
        data = [line[len("data:") :].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[0])
    return response.json()


@pytest.fixture
def make_proxy(tmp_path):
    def build(framing="json", drop=None, probe=None, error_ids=()):
        upstream = FakeUpstream(framing, error_ids)
        upstream_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream.app), base_url="http://upstream")
        ledger = tmp_path / "ledger.jsonl"
        rules = ProxyRules(bench_port=19725, kinds=KINDS, drop_first_response_of=drop)
        app = create_proxy_app("http://upstream", rules, ledger, probe=probe, client=upstream_client)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy")
        return upstream, client, ledger

    return build


def read_ledger(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.mark.asyncio
async def test_calls_are_recorded_with_inner_command_kind_and_port(make_proxy):
    _upstream, client, ledger = make_proxy()

    response = await client.post("/mcp", json=archicad_call(1, "elements_get_all_elements"), headers=HEADERS)

    assert result_of(response)["result"]["isError"] is False
    (entry,) = read_ledger(ledger)
    assert (entry["seq"], entry["tool"], entry["inner_command"], entry["kind"], entry["port"]) == (
        1,
        "archicad_call_tool",
        "elements_get_all_elements",
        "read",
        19725,
    )
    assert (entry["blocked"], entry["dropped"], entry["is_error"]) == (None, False, False)


@pytest.mark.asyncio
async def test_call_to_other_port_is_blocked_and_logged(make_proxy):
    upstream, client, ledger = make_proxy()

    response = await client.post(
        "/mcp", json=archicad_call(7, "elements_get_all_elements", port=19723), headers=HEADERS
    )

    assert upstream.requests == []
    body = result_of(response)
    assert body["id"] == 7
    assert body["result"]["isError"] is True
    assert body["result"]["content"][0]["text"].startswith("[bench-proxy] blocked")
    (entry,) = read_ledger(ledger)
    assert "19723" in entry["blocked"]


@pytest.mark.asyncio
async def test_project_and_application_irreversible_commands_are_blocked(make_proxy):
    upstream, client, ledger = make_proxy()

    await client.post("/mcp", json=archicad_call(1, "project_open_project"), headers=HEADERS)
    await client.post("/mcp", json=archicad_call(2, "elements_delete_elements"), headers=HEADERS)

    forwarded = [r["params"]["arguments"]["name"] for r in upstream.requests]
    assert forwarded == ["elements_delete_elements"]
    blocked = {e["inner_command"]: e["blocked"] for e in read_ledger(ledger)}
    assert blocked["project_open_project"] is not None
    assert blocked["elements_delete_elements"] is None


@pytest.mark.asyncio
async def test_unknown_command_counts_as_write(make_proxy):
    phases = []
    _upstream, client, ledger = make_proxy(probe=lambda phase, entry: phases.append(phase) or {"phase": phase})

    await client.post("/mcp", json=archicad_call(1, "elements_brand_new_command"), headers=HEADERS)

    (entry,) = read_ledger(ledger)
    assert entry["kind"] == "write"
    assert phases == ["before", "after"]


@pytest.mark.asyncio
async def test_write_calls_are_probed_before_and_after(make_proxy):
    seen = []

    def probe(phase, entry):
        seen.append((entry["tool"], entry["inner_command"], phase))
        return {"changed": len(seen)}

    _upstream, client, ledger = make_proxy(probe=probe)

    await client.post("/mcp", json=archicad_call(1, "properties_set_property_values_of_elements"), headers=HEADERS)
    await client.post("/mcp", json=call(2, "archicad_get_job", {"job_handle": "j-1"}), headers=HEADERS)
    await client.post("/mcp", json=archicad_call(3, "elements_get_all_elements"), headers=HEADERS)

    assert seen == [
        ("archicad_call_tool", "properties_set_property_values_of_elements", "before"),
        ("archicad_call_tool", "properties_set_property_values_of_elements", "after"),
        ("archicad_get_job", None, "after"),
    ]
    write, job, read = read_ledger(ledger)
    assert (write["probe_before"], write["probe_after"]) == ({"changed": 1}, {"changed": 2})
    assert (job["probe_before"], job["probe_after"]) == (None, {"changed": 3})
    assert (read["probe_before"], read["probe_after"]) == (None, None)


@pytest.mark.asyncio
async def test_sse_response_passes_through(make_proxy):
    _upstream, client, _ledger = make_proxy(framing="sse")
    request = archicad_call(1, "elements_get_all_elements")
    expected = f"event: message\r\ndata: {json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': 'ok'}], 'isError': False}})}\r\n\r\n"

    response = await client.post("/mcp", json=request, headers=HEADERS)

    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == expected


@pytest.mark.parametrize("framing", ["json", "sse"])
@pytest.mark.asyncio
async def test_dropped_response_keeps_framing(make_proxy, framing):
    upstream, client, ledger = make_proxy(framing=framing, drop="elements_create_doors")

    first = await client.post("/mcp", json=archicad_call(11, "elements_create_doors"), headers=HEADERS)
    second = await client.post("/mcp", json=archicad_call(12, "elements_create_doors"), headers=HEADERS)

    assert len(upstream.requests) == 2
    expected_type = "text/event-stream" if framing == "sse" else "application/json"
    assert first.headers["content-type"].startswith(expected_type)
    dropped = result_of(first)
    assert dropped["id"] == 11
    assert dropped["result"]["isError"] is True
    assert "outcome is unknown" in dropped["result"]["content"][0]["text"]
    assert result_of(second)["result"] == {"content": [{"type": "text", "text": "ok"}], "isError": False}
    assert [e["dropped"] for e in read_ledger(ledger)] == [True, False]


@pytest.mark.asyncio
async def test_session_header_is_forwarded(make_proxy):
    upstream, client, _ledger = make_proxy()
    initialize = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}

    response = await client.post("/mcp", json=initialize, headers={**HEADERS, "mcp-session-id": "session-from-client"})

    assert upstream.headers[0]["mcp-session-id"] == "session-from-client"
    assert response.headers["mcp-session-id"] == "session-from-upstream"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_proxy_serves_in_a_thread_against_a_real_upstream(tmp_path):
    upstream = FakeUpstream()
    upstream_port, proxy_port = free_port(), free_port()
    ledger = tmp_path / "ledger.jsonl"
    app = create_proxy_app(f"http://127.0.0.1:{upstream_port}", ProxyRules(bench_port=19725, kinds=KINDS), ledger)
    servers = [serve_in_thread(upstream.app, upstream_port), serve_in_thread(app, proxy_port)]
    try:
        response = httpx.post(
            f"http://127.0.0.1:{proxy_port}/mcp", json=archicad_call(1, "elements_get_all_elements"), headers=HEADERS
        )
    finally:
        for server in reversed(servers):
            server.stop()

    assert result_of(response)["result"]["isError"] is False
    assert [entry["inner_command"] for entry in read_ledger(ledger)] == ["elements_get_all_elements"]


@pytest.mark.asyncio
async def test_batched_tool_calls_are_rejected(make_proxy):
    upstream, client, _ledger = make_proxy()

    response = await client.post("/mcp", json=[archicad_call(1, "elements_get_all_elements")], headers=HEADERS)

    assert response.status_code == 400
    assert upstream.requests == []


@pytest.mark.parametrize(
    "inner",
    [
        json.dumps({"port": 19723, "params": {}}),
        {"port": "19723", "params": {}},
    ],
    ids=["arguments-as-json-string", "port-as-string"],
)
@pytest.mark.asyncio
async def test_another_port_in_any_shape_is_blocked(make_proxy, inner):
    upstream, client, ledger = make_proxy()
    request = call(1, "archicad_call_tool", {"name": "properties_set_property_values_of_elements", "arguments": inner})

    response = await client.post("/mcp", json=request, headers=HEADERS)

    assert upstream.requests == []
    assert result_of(response)["result"]["isError"] is True
    (entry,) = read_ledger(ledger)
    assert entry["port"] == 19723
    assert "19723" in entry["blocked"]
    assert not entry["blocked"].startswith(MALFORMED)


@pytest.mark.parametrize(
    "inner",
    ['{"port": 19725, "params": {}}}', {"params": {}}, {"port": True, "params": {}}, {"port": 19725.5}],
    ids=["unparsable-string", "no-port", "bool-port", "float-port"],
)
@pytest.mark.asyncio
async def test_a_call_without_a_readable_port_is_blocked_as_malformed(make_proxy, inner):
    upstream, client, ledger = make_proxy()
    request = call(1, "archicad_call_tool", {"name": "elements_get_all_elements", "arguments": inner})

    await client.post("/mcp", json=request, headers=HEADERS)

    assert upstream.requests == []
    (entry,) = read_ledger(ledger)
    assert entry["blocked"].startswith(MALFORMED)


@pytest.mark.parametrize(
    "inner",
    [json.dumps({"port": 19725, "params": {}}), {"port": "19725", "params": {}}],
    ids=["arguments-as-json-string", "port-as-string"],
)
@pytest.mark.asyncio
async def test_the_benchmark_port_in_another_shape_passes(make_proxy, inner):
    upstream, client, ledger = make_proxy()
    request = call(1, "archicad_call_tool", {"name": "elements_get_all_elements", "arguments": inner})

    await client.post("/mcp", json=request, headers=HEADERS)

    assert len(upstream.requests) == 1
    (entry,) = read_ledger(ledger)
    assert (entry["port"], entry["blocked"]) == (19725, None)


@pytest.mark.parametrize(
    "command",
    [
        "favorites_export_favorites",
        "issues_export_issues_to_bcf",
        "project_print_view",
        "navigator_publish_publisher_set",
    ],
)
@pytest.mark.asyncio
async def test_commands_that_write_files_outside_the_project_are_blocked(make_proxy, command):
    upstream, client, ledger = make_proxy()

    await client.post("/mcp", json=archicad_call(1, command), headers=HEADERS)

    assert upstream.requests == []
    (entry,) = read_ledger(ledger)
    assert entry["blocked"] is not None


@pytest.mark.asyncio
async def test_the_drop_waits_for_a_create_call_that_went_through(make_proxy):
    _upstream, client, ledger = make_proxy(drop="elements_create_doors", error_ids={1})

    await client.post("/mcp", json=archicad_call(1, "elements_create_doors"), headers=HEADERS)
    await client.post(
        "/mcp", json=archicad_call(2, "elements_create_doors", tool="archicad_read_tool"), headers=HEADERS
    )
    third = await client.post("/mcp", json=archicad_call(3, "elements_create_doors"), headers=HEADERS)

    assert [e["dropped"] for e in read_ledger(ledger)] == [False, False, True]
    assert "outcome is unknown" in result_of(third)["result"]["content"][0]["text"]
