"""Turn one Claude Code stream-json run into token, call and error metrics."""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

META_TOOLS = ("archicad_call_tool", "archicad_read_tool")

# Checked in order; the first match wins.
_ERROR_CLASSES = (
    ("blocked", re.compile(r"\[bench-proxy\] blocked", re.IGNORECASE)),
    ("validation", re.compile(r"invalid arguments|validation error", re.IGNORECASE)),
    ("refused", re.compile(r"read-only|ask the user to confirm", re.IGNORECASE)),
    ("archicad", re.compile(r"error code|APIERR", re.IGNORECASE)),
    ("transport", re.compile(r"connection|timed out|timeout", re.IGNORECASE)),
)


@dataclass(frozen=True)
class ToolCall:
    tool_use_id: str
    tool: str
    inner_command: str | None
    port: int | None
    is_error: bool
    result_bytes: int
    error_class: str | None


@dataclass(frozen=True)
class RunMetrics:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: float
    turns: int
    tool_calls: list[ToolCall]
    tool_result_bytes: int
    peak_context_tokens: int
    context_window: int | None
    result_subtype: str | None
    is_error: bool
    result_text: str
    complete: bool
    rate_limit_status: str | None
    hook_events: list[str]
    init: dict
    permission_denials: list

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_creation_tokens


def classify_tool_error(text: str) -> str:
    for name, pattern in _ERROR_CLASSES:
        if pattern.search(text):
            return name
    return "other"


def _inner_command_and_port(tool: str, tool_input: dict) -> tuple[str | None, int | None]:
    if not tool.endswith(META_TOOLS):
        return None, None
    arguments = tool_input.get("arguments") or {}
    port = arguments.get("port") if isinstance(arguments, dict) else None
    return tool_input.get("name"), port if isinstance(port, int) else None


def _content_text_and_bytes(content) -> tuple[str, int]:
    if isinstance(content, str):
        return content, len(content.encode("utf-8"))
    texts, size = [], 0
    for block in content or []:
        if block.get("type") == "text":
            texts.append(block["text"])
            size += len(block["text"].encode("utf-8"))
        else:
            size += len(json.dumps(block).encode("utf-8"))
    return "\n".join(texts), size


def parse_stream(lines: Iterable[str]) -> RunMetrics:
    """Claude Code repeats an assistant message once per content block, so messages count once per id
    and tool calls once per block id. Without a result event (timeout, kill) tokens are summed per message."""
    init: dict = {}
    hook_events: list[str] = []
    rate_limit_status = None
    seen_messages: dict[str, dict] = {}
    calls: dict[str, dict] = {}
    final: dict | None = None

    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            init = event
        elif kind == "system" and event.get("subtype") == "hook_started":
            hook_events.append(event.get("hook_name", ""))
        elif kind == "rate_limit_event":
            rate_limit_status = (event.get("rate_limit_info") or {}).get("status")
        elif kind == "assistant":
            message = event["message"]
            seen_messages.setdefault(message["id"], message.get("usage") or {})
            for block in message.get("content", []):
                if block.get("type") == "tool_use" and block["id"] not in calls:
                    inner, port = _inner_command_and_port(block["name"], block.get("input") or {})
                    calls[block["id"]] = {"tool": block["name"], "inner": inner, "port": port, "result": None}
        elif kind == "user":
            for block in (event.get("message") or {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                    calls[block["tool_use_id"]]["result"] = block
        elif kind == "result":
            final = event

    tool_calls = []
    for block_id, call in calls.items():
        block = call["result"] or {}
        content_text, size = _content_text_and_bytes(block.get("content"))
        is_error = bool(block.get("is_error"))
        tool_calls.append(
            ToolCall(
                tool_use_id=block_id,
                tool=call["tool"],
                inner_command=call["inner"],
                port=call["port"],
                is_error=is_error,
                result_bytes=size,
                error_class=classify_tool_error(content_text) if is_error else None,
            )
        )

    peak = max(
        (
            u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            for u in seen_messages.values()
        ),
        default=0,
    )
    usage = final.get("usage") if final else None
    if usage is None:
        usage = {
            key: sum(u.get(key, 0) for u in seen_messages.values())
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        }
    windows = [
        m.get("contextWindow") for m in ((final or {}).get("modelUsage") or {}).values() if m.get("contextWindow")
    ]

    return RunMetrics(
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        cost_usd=float((final or {}).get("total_cost_usd") or 0.0),
        turns=int(final["num_turns"]) if final else len(seen_messages),
        tool_calls=tool_calls,
        tool_result_bytes=sum(call.result_bytes for call in tool_calls),
        peak_context_tokens=peak,
        context_window=max(windows) if windows else None,
        result_subtype=final.get("subtype") if final else None,
        is_error=bool(final.get("is_error")) if final else True,
        result_text=str(final.get("result") or "") if final else "",
        complete=final is not None,
        rate_limit_status=rate_limit_status,
        hook_events=hook_events,
        init=init,
        permission_denials=list(final.get("permission_denials") or []) if final else [],
    )
