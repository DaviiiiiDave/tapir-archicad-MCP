"""Builders for stream-json events, shaped like the events Claude Code 2.1.81 emits with --verbose."""

import json

SESSION = "4bdd33ae-3762-4904-a803-71e93fad6881"


def init(model="claude-haiku-5-5", tools=(), mcp_servers=(), plugins=()):
    return {
        "type": "system",
        "subtype": "init",
        "cwd": "C:/bench/run",
        "session_id": SESSION,
        "tools": list(tools),
        "mcp_servers": list(mcp_servers),
        "model": model,
        "permissionMode": "dontAsk",
        "slash_commands": [],
        "apiKeySource": "none",
        "claude_code_version": "2.1.81",
        "output_style": "default",
        "agents": [],
        "skills": [],
        "plugins": list(plugins),
        "uuid": "00000000-0000-0000-0000-000000000001",
        "fast_mode_state": "off",
    }


def hook_started(name="SessionStart:startup"):
    return {
        "type": "system",
        "subtype": "hook_started",
        "hook_id": "97d19347-c707-468f-9bad-d9539d63556e",
        "hook_name": name,
        "hook_event": name.split(":")[0],
        "uuid": "00000000-0000-0000-0000-000000000002",
        "session_id": SESSION,
    }


def rate_limit(status="allowed"):
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": status,
            "resetsAt": 1792459800,
            "rateLimitType": "five_hour",
            "overageStatus": "allowed",
            "overageResetsAt": 1793491200,
            "isUsingOverage": False,
        },
        "uuid": "00000000-0000-0000-0000-000000000003",
        "session_id": SESSION,
    }


def text(value):
    return {"type": "text", "text": value}


def tool_use(block_id, name, tool_input):
    return {"type": "tool_use", "id": block_id, "name": name, "input": tool_input}


def assistant(
    msg_id, blocks, input_tokens=0, cache_read=0, cache_creation=0, output_tokens=0, model="claude-haiku-5-5"
):
    return {
        "type": "assistant",
        "message": {
            "model": model,
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": list(blocks),
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": cache_creation},
                "output_tokens": output_tokens,
                "service_tier": "standard",
            },
        },
        "parent_tool_use_id": None,
        "session_id": SESSION,
        "uuid": "00000000-0000-0000-0000-000000000004",
    }


def tool_result(tool_use_id, content, is_error=False):
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error}],
        },
        "parent_tool_use_id": None,
        "session_id": SESSION,
        "uuid": "00000000-0000-0000-0000-000000000005",
    }


def result(
    input_tokens=0,
    cache_creation=0,
    cache_read=0,
    output_tokens=0,
    cost=0.0,
    turns=1,
    subtype="success",
    is_error=False,
    text_value="OK",
    model="claude-haiku-5-5",
    context_window=200000,
):
    usage = {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output_tokens,
        "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        "service_tier": "standard",
        "cache_creation": {"ephemeral_1h_input_tokens": cache_creation, "ephemeral_5m_input_tokens": 0},
    }
    return {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "duration_ms": 2782,
        "duration_api_ms": 2571,
        "num_turns": turns,
        "result": text_value,
        "stop_reason": "end_turn",
        "session_id": SESSION,
        "total_cost_usd": cost,
        "usage": usage,
        "modelUsage": {
            model: {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "cacheReadInputTokens": cache_read,
                "cacheCreationInputTokens": cache_creation,
                "webSearchRequests": 0,
                "costUSD": cost,
                "contextWindow": context_window,
                "maxOutputTokens": 32000,
            }
        },
        "permission_denials": [],
        "fast_mode_state": "off",
        "uuid": "00000000-0000-0000-0000-000000000006",
    }


def lines(*events):
    return [json.dumps(event, ensure_ascii=False) for event in events]
