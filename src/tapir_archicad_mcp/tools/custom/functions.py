import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from multiconn_archicad import (
    APIResponseError,
    ConnHeader,
    Port,
    ProductInfo,
    SoloProjectID,
    TapirInfo,
    TeamworkProjectID,
)
from multiconn_archicad.errors import AddOnCommandUnavailable, APIErrorBase
from pydantic import ValidationError

from tapir_archicad_mcp.app import mcp
from tapir_archicad_mcp.context import change_log_instance, job_store_instance, multi_conn_instance
from tapir_archicad_mcp.jobs import JobStore, arguments_preview, outcome_may_be_unknown, upstream_error
from tapir_archicad_mcp.images import build_image_result, extract_images, to_tool_result
from tapir_archicad_mcp.outcome import DEFAULT_RESULT_FIELDS, RESULT_LIST_FIELDS, summarize_execution_results
from tapir_archicad_mcp.pagination import PaginationRequest, prepare_result
from tapir_archicad_mcp.project_identity import project_fingerprint, read_project_fingerprint
from tapir_archicad_mcp.request_lifecycle import DispatchLifecycle
from tapir_archicad_mcp.validation_errors import format_validation_error
from tapir_archicad_mcp.tools.custom.models import (
    CommandOverview,
    CommandSchema,
    DiscoveryResult,
    ProjectType,
    ReadyInstance,
    UnavailableInstance,
)
from tapir_archicad_mcp.tools.tool_registry import (
    TOOL_DISCOVERY_CATALOG,
    ToolRegistryEntry,
    get_tool_entry,
    get_tool_input_schema,
)

log = logging.getLogger()


@mcp.tool(
    name="discovery_list_active_archicads",
    title="List Active Archicad Instances",
    description=(
        "Scans for and lists running Archicad instances. "
        "Returns 'active' (ready to receive commands with their target 'port') "
        "and 'unavailable' (instances that are unresponsive or missing the Tapir Add-On)."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def list_active_archicads() -> DiscoveryResult:
    log.info("Executing list_active_archicads tool...")
    try:
        multi_conn = multi_conn_instance.get()
    except LookupError:
        log.error("CRITICAL: multi_conn_instance context variable not set. Lifespan manager may have failed.")
        raise RuntimeError("Server configuration error: could not access MultiConn instance.")

    multi_conn.refresh.all_ports()
    multi_conn.connect.all()

    active_instances: list[ReadyInstance] = []
    unavailable_instances: list[UnavailableInstance] = []

    for port, header in multi_conn.open_port_headers.items():
        if not isinstance(header.product_info, ProductInfo):
            unavailable_instances.append(_build_unresponsive_instance(header.product_info))
        elif not isinstance(header.tapir_info, TapirInfo) or not header.tapir_info.is_installed or not header.tapir_info.version:
            unavailable_instances.append(_build_missing_tapir_instance())
        else:
            active_instances.append(_build_ready_instance(int(port), header))

    log.info(f"Discovery complete: {len(active_instances)} ready, {len(unavailable_instances)} unavailable.")
    return DiscoveryResult(
        activeInstances=active_instances,
        unavailableInstances=unavailable_instances,
    )


def _build_unresponsive_instance(error: APIResponseError) -> UnavailableInstance:
    """Builds diagnostic info for an unresponsive Archicad instance."""
    return UnavailableInstance(
        reason="unresponsive",
        message=(
            f"Archicad API error: '{error.message}'. "
            "If the error message is actionable, please prompt the user to resolve it."
        )
    )


def _build_missing_tapir_instance() -> UnavailableInstance:
    """Builds diagnostic info and installation instructions when Tapir is not installed."""
    return UnavailableInstance(
        reason="missing_tapir",
        message=(
            "Archicad was detected, but the Tapir Add-On is not installed. "
            "The Tapir Add-On is required for AI automation. "
            "Please prompt the user to download and install it from: "
            "https://github.com/ENZYME-APD/tapir-archicad-automation"
        )
    )


def _build_ready_instance(port: int, header: ConnHeader) -> ReadyInstance:
    """Builds a ready-to-use Archicad instance metadata model."""
    project_id = header.archicad_id
    project_type: ProjectType = "untitled"
    project_path: str | None = None

    if isinstance(project_id, TeamworkProjectID):
        project_type = "teamwork"
        project_path = project_fingerprint(project_id)
    elif isinstance(project_id, SoloProjectID):
        project_type = "solo"
        project_path = project_fingerprint(project_id)

    tapir: TapirInfo = header.tapir_info

    return ReadyInstance(
        port=port,
        projectName=getattr(project_id, "projectName", "Untitled"),
        projectType=project_type,
        archicadVersion=str(header.product_info.version),
        tapirVersion=tapir.version,
        projectPath=project_path,
        warning=_get_tapir_version_warning(tapir),
    )


def _get_tapir_version_warning(tapir: TapirInfo) -> str | None:
    """Generates a diagnostic warning if the installed Tapir version differs from supported."""
    if tapir.is_older:
        return (
            f"Installed Tapir version ({tapir.version}) is older than supported ({tapir.requiredVersion}). "
            "Most commands will work, but newer commands or parameters may fail. "
            "If you encounter command errors, prompt the user to upgrade Tapir."
        )
    if tapir.is_newer:
        return (
            f"Installed Tapir version ({tapir.version}) is newer than supported ({tapir.requiredVersion}). "
            "Operations should proceed normally."
        )
    return None


@mcp.tool(
    name="archicad_list_commands",
    title="List Available Archicad Commands",
    description=(
        "Returns the available Archicad API commands with brief descriptions and kind. "
        "Pass 'query' (e.g. 'property values'; every word must occur in the name, title or description) and/or "
        "'group' (the name prefix, e.g. 'issues') to search; call without arguments only when you need the full list. "
        "STEP 1: Use this tool FIRST to search for the right command name for your task. "
        "Do NOT guess or hallucinate command names. "
        "STEP 2: Once you find the relevant command name, you MUST use the 'archicad_get_command_schema' "
        "tool to learn its exact required arguments."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def archicad_list_commands(query: str | None = None, group: str | None = None) -> list[CommandOverview]:
    log.info("Executing archicad_list_commands tool...")
    tokens = (query or "").lower().split()
    group_prefix = f"{group.strip()}_" if group and group.strip() else None
    return [
        CommandOverview(
            name=tool["name"],
            description=tool["description"],
            kind=tool["kind"],
        )
        for tool in TOOL_DISCOVERY_CATALOG.values()
        if (group_prefix is None or tool["name"].startswith(group_prefix))
        and _matches_all_tokens(tool, tokens)
    ]


def _matches_all_tokens(tool: dict, tokens: list[str]) -> bool:
    haystack = f"{tool['name']} {tool['title']} {tool['description']}".lower()
    return all(token in haystack for token in tokens)


@mcp.tool(
    name="archicad_get_command_schema",
    title="Get Archicad Command Schema",
    description=(
        "Retrieves the exact JSON schema (required arguments) for a specific Archicad command. "
        "Provide the exact 'command_name' obtained from 'archicad_list_commands'. "
        "CRITICAL: You MUST call this tool before executing 'archicad_call_tool' to ensure you provide "
        "the correct parameters. Do NOT guess or hallucinate parameters based on the command name."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def archicad_get_command_schema(command_name: str) -> CommandSchema:
    log.info(f"Executing archicad_get_command_schema for: {command_name!r}")
    try:
        tool = TOOL_DISCOVERY_CATALOG[command_name]
    except KeyError:
        raise ToolError(
            f"Command '{command_name}' not found. Please use 'archicad_list_commands' "
            f"to verify the exact spelling of the command name."
        ) from None

    return CommandSchema(
        name=tool["name"],
        input_schema=get_tool_input_schema(command_name),
    )


@mcp.tool(
    name="archicad_call_tool",
    title="Execute Archicad API Command",
    description=(
        "Executes a specific Archicad API command by its 'name'. "
        "CRITICAL WORKFLOW: You MUST use 'archicad_get_command_schema' first to understand the exact "
        "JSON structure required for the 'arguments' parameter. "
        "The 'arguments' dictionary MUST contain a 'port' number (obtained from 'discovery_list_active_archicads'). "
        "If a tool's response includes a 'next_page_token', call this same tool again with the same parameters "
        "and add a 'page_token' key to the 'arguments' dictionary. "
        "Long executions return status='running' and a jobHandle; retrieve them with archicad_get_job. "
        "If a response is lost or cancelled, list jobs with archicad_get_job() before retrying a write. "
        "Cancelling this request does not cancel an admitted execution. "
        "Commands with kind='read' (see archicad_list_commands) should go through archicad_read_tool instead. "
        "Commands with kind='irreversible' additionally need \"confirm\": true in the arguments, "
        "which you may only set after the user agreed."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def archicad_call_tool(name: str, arguments: dict) -> Annotated[CallToolResult, dict]:
    log.info(f"Executing archicad_call_tool for tool: {name!r}")
    return to_tool_result(name, await _run_command(name, arguments, read_only=False))


@mcp.tool(
    name="archicad_read_tool",
    title="Execute Read-Only Archicad API Command",
    description=(
        "Executes an Archicad API command that does not change the project (kind='read' in archicad_list_commands). "
        "Same workflow and arguments as archicad_call_tool: get the schema with 'archicad_get_command_schema' first, "
        "pass a 'port', and follow 'next_page_token' with 'page_token'. "
        "Commands that change the project are rejected; use archicad_call_tool for those."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def archicad_read_tool(name: str, arguments: dict) -> Annotated[CallToolResult, dict]:
    log.info(f"Executing archicad_read_tool for tool: {name!r}")
    return to_tool_result(name, await _run_command(name, arguments, read_only=True))


async def _run_command(name: str, arguments: dict, *, read_only: bool) -> dict:
    tool_entry = get_tool_entry(name)
    if read_only and tool_entry.kind != "read":
        raise ToolError(f"'{name}' changes the project. Use archicad_call_tool for commands that are not kind='read'.")
    try:
        validated_arguments = tool_entry.arguments_model.model_validate(arguments)
    except ValidationError as e:
        message = format_validation_error(name, e)
        log.error(f"Validation error for arguments of {name!r}: {message}")
        raise ToolError(message) from e
    if tool_entry.kind == "irreversible" and not validated_arguments.confirm:
        raise ToolError(
            f"'{name}' is irreversible: it cannot be undone in Archicad or it affects other users. "
            f'Ask the user to confirm, then call again with "confirm": true in arguments.'
        )

    store = job_store_instance.get()
    port = validated_arguments.port
    if tool_entry.pagination_field is not None and validated_arguments.page_token is not None:
        # Continuations read retained data and do not create execution jobs.
        return await asyncio.to_thread(_read_cached_page, name, tool_entry, validated_arguments, store)
    job = store.submit(
        name, port, arguments,
        lambda handle: _execute_tool(name, tool_entry, validated_arguments, handle),
    )
    await store.wait(job)
    return store.initial_result(job)

def _read_cached_page(name: str, tool_entry: ToolRegistryEntry, arguments: Any, store: JobStore) -> dict:
    # MultiConn's public header properties can trigger discovery; keep that work
    # off the event loop, and never redispatch the original command.
    multi_conn = multi_conn_instance.get()
    connection = multi_conn.open_port_headers.get(Port(arguments.port))
    request = _pagination_request(name, tool_entry, arguments, connection)
    return store.get_page(arguments.page_token, request)


def _execute_tool(name: str, tool_entry: ToolRegistryEntry, validated_arguments: Any, job_handle: str) -> Any:
    port = validated_arguments.port
    conn_header = _get_header(port)

    call_args: dict[str, Any] = {"conn_header": conn_header}

    if tool_entry.params_model:
        call_args["params"] = validated_arguments.params

    changes_project = tool_entry.kind != "read"
    try:
        with DispatchLifecycle(name, port, job_handle):
            result = _dispatch_command(name, tool_entry, call_args, port)
    except Exception as e:
        if changes_project:
            _record_failed_change(name, tool_entry, validated_arguments, conn_header, job_handle, e)
        raise
    if changes_project:
        result = _with_outcome(name, {} if result is None else result)
        result = _record_change(name, tool_entry, validated_arguments, conn_header, job_handle, result)
    if tool_entry.pagination_field is None:
        return result
    request = _pagination_request(name, tool_entry, validated_arguments, conn_header)
    return prepare_result(result, request, job_handle.removeprefix("job:"))


def _with_outcome(name: str, result: Any) -> Any:
    fields = RESULT_LIST_FIELDS.get(TOOL_DISCOVERY_CATALOG[name]["title"], DEFAULT_RESULT_FIELDS)
    summary = summarize_execution_results(result, fields)
    if summary is None:
        return result
    if "outcome" in result:
        log.warning(f"Response of {name} already has an 'outcome' key; not adding the execution summary.")
        return result
    return {**result, "outcome": summary}


def _write_change_log(
    name: str,
    tool_entry: ToolRegistryEntry,
    validated_arguments: Any,
    conn_header: ConnHeader,
    job_handle: str,
    result: dict | None,
    failure: Exception | None,
) -> Path:
    project, project_error = read_project_fingerprint(conn_header)
    entry = {
        "port": validated_arguments.port,
        "project": project,
        "command": name,
        "kind": tool_entry.kind,
        "argumentsPreview": arguments_preview(
            validated_arguments.model_dump(mode="json", by_alias=True, exclude_none=True)
        ),
        "status": _change_status(failure),
        "outcome": result.get("outcome") if result is not None else None,
        "error": str(failure) if failure is not None else None,
        "jobHandle": job_handle,
    }
    if project_error is not None:
        entry["projectError"] = project_error
    return change_log_instance.get().record(entry)


def _change_status(failure: Exception | None) -> str:
    if failure is None:
        return "completed"
    return "unknown" if outcome_may_be_unknown(failure) else "failed"


def _record_change(
    name: str,
    tool_entry: ToolRegistryEntry,
    validated_arguments: Any,
    conn_header: ConnHeader,
    job_handle: str,
    result: dict,
) -> dict:
    """Write the change-log line. Returns the result, with the log path (or the reason it failed) added."""
    try:
        path = _write_change_log(name, tool_entry, validated_arguments, conn_header, job_handle, result, None)
    except Exception as e:
        # The command has already run in Archicad: a logging problem must not hide its result.
        log.error(f"Could not write the change log for {name}: {e}")
        return {**result, "changeLogError": f"{type(e).__name__}: {e}"}
    return {**result, "changeLog": str(path)}


def _record_failed_change(
    name: str,
    tool_entry: ToolRegistryEntry,
    validated_arguments: Any,
    conn_header: ConnHeader,
    job_handle: str,
    failure: Exception,
) -> None:
    """Write the change-log line for a failed command; if that fails too, say so in the error that is raised."""
    try:
        _write_change_log(name, tool_entry, validated_arguments, conn_header, job_handle, None, failure)
    except Exception as e:
        log.error(f"Could not write the change log for {name}: {e}")
        original = (
            str(failure)
            if isinstance(failure, (APIErrorBase, ToolError))
            else "Unexpected execution error. Archicad's actual outcome may be unknown; inspect the server logs."
        )
        # Chain from the upstream error so jobs._failure_details still sees a transport failure.
        raise ToolError(
            f"{original} Change log could not be written: {type(e).__name__}: {e}"
        ) from (upstream_error(failure) or failure)


def _dispatch_command(name: str, tool_entry: ToolRegistryEntry, call_args: dict, port: int) -> Any:
    try:
        return tool_entry.callable(**call_args)
    except AddOnCommandUnavailable as e:
        raise ToolError(
            f"Command '{name}' is not available in the installed Tapir Add-On on port {port}. "
            f"This indicates that the Tapir Add-On is outdated. "
            f"Please prompt the user to upgrade Tapir from https://github.com/ENZYME-APD/tapir-archicad-automation. "
            f"(Details: {e.message})"
        ) from e
    except APIErrorBase as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        log.error(f"Error executing dispatched tool {name}: {e}")
        raise


def _pagination_request(
    name: str, tool_entry: ToolRegistryEntry, arguments: Any, connection: ConnHeader | None,
) -> PaginationRequest:
    return PaginationRequest(
        connection=connection, port=arguments.port, command=name,
        parameters=_serialized_parameters(arguments.params if tool_entry.params_model else None),
        field=tool_entry.pagination_field,
    )


def _serialized_parameters(params: Any) -> str:
    parameters = params.model_dump(mode="json", by_alias=True, exclude_none=True) if params is not None else {}
    return json.dumps(parameters, sort_keys=True, separators=(",", ":"))


def _get_header(port: int) -> ConnHeader:
    try:
        return multi_conn_instance.get().active[Port(port)]
    except KeyError:
        raise ToolError(f"Port {port} is not an active Archicad connection.") from None


@mcp.tool(
    name="archicad_get_job",
    title="Get or List Archicad Jobs",
    description=(
        "With no job_handle, immediately lists all retained executions newest first, including fast, completed, "
        "and failed jobs, with command, port, timestamps, and a short argumentsPreview. "
        "Use this to recover after a lost response before retrying a write; similar calls may be ambiguous. "
        "With a job_handle, waits up to the configured threshold and returns running, completed with result, "
        "or failed with error. Polling never redispatches commands."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    ),
)
async def archicad_get_job(job_handle: str | None = None) -> Annotated[CallToolResult, dict]:
    store = job_store_instance.get()
    if job_handle is None:
        return store.list_jobs()
    job = store.get(job_handle)
    await store.wait(job)
    response = store.response(job)
    if "result" not in response:
        return response
    stripped, images = extract_images(job.command, response["result"])
    if not images:
        return response
    return build_image_result({**response, "result": stripped}, images)
