"""MCP proxy between Claude Code and the server under test.

It records every tool call, blocks calls that could reach beyond the run copy, drops one write
response on request (task 5), and runs out-of-band probes of Archicad around writes.
"""

import itertools
import json
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anyio
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

BLOCKED_PREFIX = "[bench-proxy] blocked: "
LOST_RESPONSE_TEXT = "Error: the connection to the server was lost before the response arrived. The outcome is unknown."
BLOCKED_IRREVERSIBLE_GROUPS = frozenset({"project", "application", "teamwork", "ifc", "navigator"})
# Write commands that create files at a path the model chooses, outside the run copy.
BLOCKED_COMMANDS = frozenset({"favorites_export_favorites", "issues_export_issues_to_bcf", "project_print_view"})
# A blocked call whose port could not be read. It never reached Archicad, and it is no attempt on another instance.
MALFORMED = "malformed call: "
META_TOOLS = frozenset({"archicad_call_tool", "archicad_read_tool"})
JOB_TOOL = "archicad_get_job"
CALL_TOOL = "archicad_call_tool"
WRITE_KINDS = frozenset({"write", "irreversible"})

_SKIP_REQUEST_HEADERS = frozenset({"host", "content-length", "connection", "transfer-encoding", "accept-encoding"})
_SKIP_RESPONSE_HEADERS = frozenset({"content-length", "connection", "transfer-encoding", "content-encoding"})


@dataclass(frozen=True)
class ProxyRules:
    bench_port: int
    kinds: Mapping[str, str]
    drop_first_response_of: str | None = None


def kind_of(rules: ProxyRules, inner_command: str | None) -> str:
    """Tools without an inner Archicad command (discovery, schema lookups, jobs) only read.
    An Archicad command missing from the table counts as a write, so it is probed and judged."""
    if inner_command is None:
        return "read"
    return rules.kinds.get(inner_command, "write")


def blocked_reason(rules: ProxyRules, inner_command: str | None, kind: str, port: int | None) -> str | None:
    if inner_command is not None and port is None:
        return MALFORMED + "no readable integer port in the call's arguments"
    if port is not None and port != rules.bench_port:
        return f"port {port} is not the benchmark instance (port {rules.bench_port})"
    if kind == "irreversible" and inner_command and inner_command.split("_", 1)[0] in BLOCKED_IRREVERSIBLE_GROUPS:
        return f"{inner_command} is not allowed in the benchmark"
    if inner_command in BLOCKED_COMMANDS:
        return f"{inner_command} writes files outside the run copy"
    return None


def _read_port(value) -> int | None:
    """The port as the arm servers read it: an int, or a string of digits (pydantic's lax mode); no bool or float."""
    if type(value) is int:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _call_target(params: dict) -> tuple[str, str | None, int | None, dict]:
    """Tool, inner Archicad command, port and arguments of a tools/call. Inner arguments sent as a JSON string are
    parsed the way the arm servers parse them, so the port guard sees the port the server will use."""
    tool = params.get("name", "")
    arguments = params.get("arguments") or {}
    if tool in META_TOOLS:
        inner = arguments.get("arguments")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except ValueError:
                inner = None
        port = _read_port(inner.get("port")) if isinstance(inner, dict) else None
        return tool, arguments.get("name"), port, arguments
    return tool, None, _read_port(arguments.get("port")), arguments


def _is_response_to(message, request_id) -> bool:
    return isinstance(message, dict) and message.get("id") == request_id and "result" in message


def _find_response(body: bytes, content_type: str, request_id) -> dict | None:
    if content_type.startswith("text/event-stream"):
        for line in body.decode("utf-8").splitlines():
            if line.startswith("data:"):
                try:
                    message = json.loads(line[len("data:") :])
                except ValueError:
                    continue
                if _is_response_to(message, request_id):
                    return message
        return None
    try:
        message = json.loads(body)
    except ValueError:
        return None
    return message if _is_response_to(message, request_id) else None


def _replace_result(body: bytes, content_type: str, request_id, result: dict) -> bytes:
    """Swap the result of the response to request_id, keeping the framing the upstream chose."""
    if content_type.startswith("text/event-stream"):
        out = []
        for line in body.decode("utf-8").splitlines(keepends=True):
            if line.startswith("data:"):
                ending = line[len(line.rstrip("\r\n")) :]
                try:
                    message = json.loads(line[len("data:") :])
                except ValueError:
                    message = None
                if _is_response_to(message, request_id):
                    line = "data: " + json.dumps({**message, "result": result}) + ending
            out.append(line)
        return "".join(out).encode("utf-8")
    message = json.loads(body)
    return json.dumps({**message, "result": result}).encode("utf-8")


def _forward_headers(request: Request) -> dict:
    return {k: v for k, v in request.headers.items() if k.lower() not in _SKIP_REQUEST_HEADERS}


def _response_headers(upstream: httpx.Response) -> dict:
    return {k: v for k, v in upstream.headers.items() if k.lower() not in _SKIP_RESPONSE_HEADERS}


def _error_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def create_proxy_app(
    upstream_url: str,
    rules: ProxyRules,
    ledger_path: Path,
    probe: Callable[[str, dict], dict] | None = None,
    client: httpx.AsyncClient | None = None,
) -> Starlette:
    upstream_url = upstream_url.rstrip("/")
    holder: dict[str, httpx.AsyncClient | None] = {"client": client}
    seq = itertools.count(1)
    state = {"dropped": False}

    @asynccontextmanager
    async def lifespan(_app):
        if holder["client"] is not None:
            yield
            return
        async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as owned:
            holder["client"] = owned
            yield

    def write_ledger(entry: dict) -> None:
        with ledger_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def run_probe(phase: str, entry: dict) -> dict | None:
        if probe is None:
            return None
        started = time.perf_counter()
        outcome = await anyio.to_thread.run_sync(probe, phase, entry)
        entry["probe_ms"] += (time.perf_counter() - started) * 1000
        return outcome

    async def passthrough(request: Request, body: bytes) -> Response:
        upstream_client = holder["client"]
        upstream_request = upstream_client.build_request(
            request.method,
            upstream_url + request.url.path,
            params=request.query_params,
            headers=_forward_headers(request),
            content=body,
        )
        upstream = await upstream_client.send(upstream_request, stream=True)
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=_response_headers(upstream),
            background=BackgroundTask(upstream.aclose),
        )

    async def tool_call(request: Request, body: bytes, message: dict) -> Response:
        request_id = message.get("id")
        tool, inner, port, arguments = _call_target(message.get("params") or {})
        kind = kind_of(rules, inner)
        entry = {
            "seq": next(seq),
            "time": datetime.now(UTC).isoformat(),
            "tool": tool,
            "inner_command": inner,
            "kind": kind,
            "port": port,
            "arguments": arguments,
            "blocked": blocked_reason(rules, inner, kind, port),
            "dropped": False,
            "upstream_ms": None,
            "is_error": None,
            "probe_before": None,
            "probe_after": None,
            "probe_ms": 0.0,
        }
        if entry["blocked"]:
            entry["is_error"] = True
            write_ledger(entry)
            reply = {"jsonrpc": "2.0", "id": request_id, "result": _error_result(BLOCKED_PREFIX + entry["blocked"])}
            return JSONResponse(reply)

        is_write = inner is not None and kind in WRITE_KINDS
        if is_write:
            entry["probe_before"] = await run_probe("before", entry)

        upstream_client = holder["client"]
        started = time.perf_counter()
        upstream = await upstream_client.request(
            request.method,
            upstream_url + request.url.path,
            params=request.query_params,
            headers=_forward_headers(request),
            content=body,
        )
        content = upstream.content
        entry["upstream_ms"] = (time.perf_counter() - started) * 1000

        if is_write or tool == JOB_TOOL:
            entry["probe_after"] = await run_probe("after", entry)

        content_type = upstream.headers.get("content-type", "")
        response = _find_response(content, content_type, request_id)
        entry["is_error"] = bool(response["result"].get("isError")) if response else None

        went_through = response is not None and tool == CALL_TOOL and not response["result"].get("isError")
        if went_through and not state["dropped"] and inner == rules.drop_first_response_of:
            state["dropped"] = True
            entry["dropped"] = True
            content = _replace_result(content, content_type, request_id, _error_result(LOST_RESPONSE_TEXT))

        write_ledger(entry)
        return Response(content, status_code=upstream.status_code, headers=_response_headers(upstream))

    async def handle(request: Request) -> Response:
        body = await request.body()
        if request.method == "POST" and body:
            try:
                message = json.loads(body)
            except ValueError:
                message = None
            if isinstance(message, list) and any(
                isinstance(m, dict) and m.get("method") == "tools/call" for m in message
            ):
                return JSONResponse({"error": BLOCKED_PREFIX + "batched tool calls are not supported"}, status_code=400)
            if isinstance(message, dict) and message.get("method") == "tools/call":
                return await tool_call(request, body, message)
        return await passthrough(request, body)

    methods = ["GET", "POST", "DELETE", "PUT", "PATCH", "OPTIONS", "HEAD"]
    return Starlette(routes=[Route("/{path:path}", handle, methods=methods)], lifespan=lifespan)


class ProxyServer:
    def __init__(self, server: uvicorn.Server, thread: threading.Thread) -> None:
        self._server = server
        self._thread = thread

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def serve_in_thread(app, port: int, startup_timeout_s: float = 10.0) -> ProxyServer:
    """Serve an ASGI app on 127.0.0.1:port in a daemon thread and return once it accepts connections."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + startup_timeout_s
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError(f"proxy did not start on port {port}")
        time.sleep(0.05)
    return ProxyServer(server, thread)
