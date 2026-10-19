---
stages:
  3: "Command classification and read tool (Task 1-2)"
  4: "Error hygiene, catalog search, response envelope (Task 3-5)"
  5: "Images and change log (Task 6-7)"
current: 1
---

# Runtime Hygiene Implementation Plan (Plan 1 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing server safe and cheap to use before element sets exist. This covers spec steps 1–4, 11 and 12: read/write/irreversible classification with a read-only tool and confirmation for irreversible commands, validation errors without input echo, catalog search, a response envelope, images as MCP image content, and a change log.

**Architecture:** All changes sit in the MCP runtime (`tools/custom/functions.py` plus small single-purpose modules next to `jobs.py` / `pagination.py`) and in the generator config. Generated wrappers change only by one generated `kind=` argument. Each task is self-contained and becomes one upstream PR later.

**Tech Stack:** Python ≥3.12, `mcp` 2.2.0 (`mcp.server.mcpserver`), `multiconn-archicad` 0.9.4, pydantic v2, pytest + pytest-asyncio, uv.

**Spec:** `docs/superpowers/specs/2026-10-19-token-efficient-element-sets-design.md` (§4, §5.1–5.3, §5.8, §5.9, §6 steps 1–4, 11, 12). If the plan and the spec disagree, the spec wins. Stop and ask.

**Follow-up plans (not in scope here):** Plan 2 benchmark harness and baseline (spec §7), Plan 3 element sets, reads and filters (spec §5.4–5.6, steps 5–9), Plan 4 set writes and revert (spec §5.7, step 10).

## Global Constraints

- Target Archicad 29. Nothing may break the older Archicad versions Tapir supports.
- The four meta-tools and `discovery_list_active_archicads` keep their names, arguments and behaviour. The only new top-level tool is `archicad_read_tool`.
- Generated files under `src/tapir_archicad_mcp/tools/generated/` are never edited by hand. They change only by running `scripts/generate_tools.py`.
- Code, docs, tool descriptions and error messages are in English.
- The repo lives in OneDrive. Every `uv` command runs with the environment outside it: prefix with `UV_PROJECT_ENVIRONMENT="$LOCALAPPDATA/uv-envs/tapir-archicad-mcp"`. Never create `.venv` inside the repo.
- All commands run from the worktree root `.claude/worktrees/spec-token-efficiency`. Unit tests: `UV_PROJECT_ENVIRONMENT="$LOCALAPPDATA/uv-envs/tapir-archicad-mcp" uv run pytest tests/unit -q`. They must all pass at the end of every task.
- Follow the maintainer's style: one flat module per concern, explicit tables instead of inference, injectable clocks, fail loudly.
- Commit messages use `feat:` / `refactor:` / `test:` / `docs:` and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Upstream PR branches later must not contain `docs/superpowers/` or `.atrium.yaml`.

## Review Focus

1. **Page continuation through `archicad_read_tool`.** A model that pages a large read result through the read tool must get the same continuation behaviour as through `archicad_call_tool`. → Task 2 test `test_read_tool_continues_pages`.
2. **`"confirm": "true"` as a string.** Pydantic's lax mode would turn it into `True`. An irreversible command must run only with a JSON boolean `true`. → Task 2 test `test_confirm_must_be_a_boolean`.
3. **Image results fetched later via `archicad_get_job`.** A slow preview image must not arrive as base64 text through the job path. → Task 6 test `test_get_job_returns_image_content`.
4. **Change-log directory not writable.** The Archicad command has already run, so its result must still reach the model, marked with `changeLogError`, not replaced by an exception. → Task 7 test `test_unwritable_change_log_does_not_hide_the_result`.
5. **Whitespace-only catalog query.** `query="  "` must behave like no query, not return nothing. → Task 4 test `test_blank_query_lists_everything`.

---

### Task 1: Command kinds in generator config and registry

**Files:**
- Modify: `scripts/generator_config.py` (`ApiSourceConfig` + both configs)
- Modify: `scripts/generate_tools.py` (`_generate_tool_function_code`, `run_generation_for_source`, new `classify_command`)
- Modify: `src/tapir_archicad_mcp/tools/tool_registry.py`
- Regenerate: `src/tapir_archicad_mcp/tools/generated/**`
- Modify: every test that calls `register_tool_for_dispatch` (`tests/unit/test_custom_tools.py` and any other hit of `grep -rn register_tool_for_dispatch tests`)
- Test: `tests/unit/test_command_kinds.py`, `tests/unit/test_generator_classification.py`

**Interfaces:**
- Produces: `CommandKind = Literal["read", "write", "irreversible"]` in `tool_registry.py`; `register_tool_for_dispatch(func, name, title, description, params_model=None, pagination_field=None, *, kind: CommandKind)` (keyword-only, required); `ToolRegistryEntry.kind: CommandKind`; `TOOL_DISCOVERY_CATALOG[name]["kind"]`.
- Produces: `classify_command(name_camel: str, config: ApiSourceConfig) -> str` in `scripts/generate_tools.py`; `ApiSourceConfig.read_commands`, `.write_commands`, `.irreversible_commands: set[str]` (API command names, e.g. `"GetAllElements"`).

- [ ] **Step 1: Record the baseline.** Run the unit tests. Expected: all pass. Note the count.

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_command_kinds.py`:
```python
@pytest.mark.parametrize("name, kind", [
    ("elements_get_all_elements", "read"),
    ("elements_filter_elements", "read"),
    ("elements_highlight_elements", "read"),
    ("elements_create_walls", "write"),
    ("properties_set_property_values_of_elements", "write"),
    ("elements_delete_elements", "irreversible"),
    ("project_save_project", "irreversible"),
    ("teamwork_teamwork_send", "irreversible"),
    ("navigator_publish_publisher_set", "irreversible"),
])
def test_known_commands_are_classified(fake_archicad, name, kind):
    assert get_tool_entry(name).kind == kind

def test_every_registered_command_has_a_valid_kind(fake_archicad):
    assert {e.kind for e in TOOL_CALLABLE_REGISTRY.values()} <= {"read", "write", "irreversible"}

def test_registration_without_kind_fails():
    with pytest.raises(TypeError):
        register_tool_for_dispatch(lambda conn_header: None, name="test_no_kind", title="T", description="D")
```

`tests/unit/test_generator_classification.py` (put `scripts/` on `sys.path` at the top):
```python
def _config(**lists):  # minimal ApiSourceConfig with the three sets
    ...
def test_classify_returns_the_single_matching_kind():
    assert classify_command("GetX", _config(read_commands={"GetX"})) == "read"

@pytest.mark.parametrize("lists", [{}, {"read_commands": {"GetX"}, "write_commands": {"GetX"}}])
def test_unclassified_or_double_classified_fails_loudly(lists):
    with pytest.raises(ValueError, match="GetX"):
        classify_command("GetX", _config(**lists))

@pytest.mark.parametrize("config", [TAPIR_CONFIG, OFFICIAL_CONFIG])
def test_real_lists_are_disjoint(config):
    r, w, i = config.read_commands, config.write_commands, config.irreversible_commands
    assert not (r & w) and not (r & i) and not (w & i)
```

- [ ] **Step 3: Run them.** Expected: FAIL (`kind` attribute / `classify_command` / config fields missing).

- [ ] **Step 4: Implement the registry side.** Add `CommandKind`, the keyword-only `kind` parameter, `ToolRegistryEntry.kind`, and `"kind"` in the catalog entry.

- [ ] **Step 5: Implement the generator side.**
  - Add the three `set[str]` fields to `ApiSourceConfig` (default empty).
  - `classify_command` raises `ValueError` naming the command when it is in zero lists or in more than one.
  - `run_generation_for_source` classifies every command before writing any file. If any fail, it raises `SystemExit` listing all of them.
  - `_generate_tool_function_code` emits `kind="<kind>"` after `params_model=` in the registration call.

- [ ] **Step 6: Fill the lists in both configs as explicit sets** of the command names that are not excluded. Derive them once with this rule, then review by hand:
  - **irreversible:** the name starts with `Delete` or `Save`, or is one of `CloseProject`, `OpenProject`, `TeamworkSend`, `TeamworkReceive`, `PublishPublisherSet`.
  - **read:** the name starts with `Get`, `Is`, `Find` or `Search`, or is one of `FilterElements`, `HighlightElements`, `ChangeSelectionOfElements`, `FitInWindow`, `RebuildView`.
  - **write:** everything else.

  Read means "does not change project data or files".

- [ ] **Step 7: Regenerate.** Run `… uv run python scripts/generate_tools.py`, then
  `git diff -U0 -- src/tapir_archicad_mcp/tools/generated | grep -E '^[-+][^-+]' | grep -v 'kind="'`.
  Expected: no output. **If any line prints, STOP.** Run `git checkout -- src/tapir_archicad_mcp/tools/generated` and report the diff. Upstream schemas have moved, and that is not this task.

- [ ] **Step 8: Update the existing tests** that register tools: add `kind="write"`, or `kind="read"` where the test exercises a read.

- [ ] **Step 9: Run all unit tests.** Expected: PASS, with the baseline count plus the new tests.

- [ ] **Step 10: Commit** `feat: classify every command as read, write or irreversible`.

---

### Task 2: Read-only tool, confirmation for irreversible commands, kind in the catalog

**Files:**
- Modify: `src/tapir_archicad_mcp/tools/tool_registry.py` (`_build_tool_arguments_model`)
- Modify: `src/tapir_archicad_mcp/tools/custom/functions.py`
- Modify: `src/tapir_archicad_mcp/tools/custom/models.py` (`CommandOverview`)
- Modify: `manifest.json`, `README.md` (Key Features, Usage)
- Test: `tests/unit/test_custom_tools.py`, `tests/unit/test_tool_annotations.py`, `tests/unit/test_jobs.py` (bundle test)

**Interfaces:**
- Consumes: `ToolRegistryEntry.kind` (Task 1).
- Produces: `archicad_read_tool(name: str, arguments: dict) -> dict` (MCP tool, annotations `readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True`); the private `_run_command(name: str, arguments: dict, *, read_only: bool) -> dict` shared by both call tools; `CommandOverview.kind: Literal["read", "write", "irreversible"]`. Irreversible argument models gain `confirm: StrictBool = False`.

- [ ] **Step 1: Write the failing tests** (in `test_custom_tools.py` unless noted):
  - `test_read_tool_runs_read_commands`: the canned Tapir `GetAddOnVersion` response comes back unchanged from `archicad_read_tool("app_get_add_on_version", {"port": 19723})`.
  - `test_read_tool_rejects_write_commands`: `archicad_read_tool("elements_create_walls", {"port": 19723})` raises (the kind check runs before validation) `ToolError` matching `"archicad_call_tool"`, and `fake_archicad.calls == []`.
  - `test_irreversible_requires_confirm`: `archicad_call_tool("project_save_project", {"port": 19723})` raises `ToolError` matching `'"confirm": true'`, with no Tapir call. With `"confirm": True` the canned `SaveProject` response is returned.
  - `test_confirm_must_be_a_boolean`: `{"port": 19723, "confirm": "true"}` raises `ToolError` (validation), with no Tapir call.
  - `test_confirm_only_in_irreversible_schemas`: `"confirm"` is in the `archicad_get_command_schema("project_save_project")` properties and not in `elements_create_walls`.
  - `test_catalog_reports_kind`: the `archicad_list_commands()` entry for `elements_delete_elements` has `kind == "irreversible"`.
  - `test_read_tool_continues_pages`: canned `GetAllElements` with 150 elements. The first `archicad_read_tool` call returns 100 plus `next_page_token`. Calling `archicad_read_tool` again with that `page_token` returns the remaining 50.
  - `test_tool_annotations.py`: add `"archicad_read_tool": True` to `READ_ONLY_TOOLS`, with idempotent and open-world expectations as above. Adjust the dict's comment.
  - `test_jobs.py::test_bundle_declares_job_tool`: also assert `"archicad_read_tool"` in the manifest tools.

- [ ] **Step 2: Run them.** Expected: FAIL.

- [ ] **Step 3: Implement.**
  - Move the body of `archicad_call_tool` into `_run_command`. Both tools call it.
  - With `read_only=True`, any non-`read` kind raises `ToolError(f"'{name}' changes the project. Use archicad_call_tool for write commands.")` before validation.
  - After validation, an `irreversible` command without `confirm` raises `ToolError(f"'{name}' is irreversible: it cannot be undone in Archicad or it affects other users. Ask the user to confirm, then call again with \"confirm\": true in arguments.")`.
  - `_build_tool_arguments_model` gets a `kind` parameter and adds `confirm` only for irreversible commands.
  - Read-tool description: one sentence saying it runs commands that do not change the project, the same workflow as `archicad_call_tool`.
  - Add the manifest entry. Add one README line under Key Features and one under Usage.

- [ ] **Step 4: Run all unit tests.** Expected: PASS.

- [ ] **Step 5: Commit** `feat: add read-only call tool and require confirmation for irreversible commands`.

---

### Task 3: Validation errors without input echo

**Files:**
- Create: `src/tapir_archicad_mcp/validation_errors.py`
- Modify: `src/tapir_archicad_mcp/tools/custom/functions.py` (the `ValidationError` handler, today `functions.py:240-242`)
- Test: `tests/unit/test_validation_errors.py`

**Interfaces:**
- Produces: `format_validation_error(command_name: str, error: pydantic.ValidationError, max_errors: int = 10) -> str`.

- [ ] **Step 1: Write the failing test**
```python
@pytest.mark.asyncio
async def test_invalid_arguments_do_not_echo_input(fake_archicad):
    elements = [{"elementId": {"guid": f"not-a-guid-{i:05d}"}} for i in range(3000)]
    with pytest.raises(ToolError) as exc:
        await archicad_read_tool("elements_get_types_of_elements", {"port": 19723, "params": {"elements": elements}})
    msg = str(exc.value)
    assert "not-a-guid" not in msg
    assert len(msg) < 2000
    assert "params.elements.0.elementId.guid" in msg
    assert "and 2990 more errors" in msg
    assert "archicad_get_command_schema('elements_get_types_of_elements')" in msg
```

- [ ] **Step 2: Run it.** Expected: FAIL (the message contains the input).

- [ ] **Step 3: Implement.**
  - Use `error.errors(include_input=False, include_url=False)`.
  - One line per error: `- <dotted loc>: <msg>`.
  - At most `max_errors` lines, then `…and N more errors`, then `Call archicad_get_command_schema('<name>') for the exact schema.`
  - The handler in `_run_command` raises `ToolError(format_validation_error(...))`. The server log may keep the full error.

- [ ] **Step 4: Run all unit tests.** Expected: PASS.

- [ ] **Step 5: Commit** `fix: keep validation errors short and free of input values`.

---

### Task 4: Catalog search

**Files:**
- Modify: `src/tapir_archicad_mcp/tools/custom/functions.py` (`archicad_list_commands`)
- Modify: `README.md` (Usage step 5)
- Test: `tests/unit/test_custom_tools.py`

**Interfaces:**
- Produces: `archicad_list_commands(query: str | None = None, group: str | None = None) -> list[CommandOverview]`.
  - `query`: case-insensitive. Split on whitespace. A command matches when **every** token occurs in its name, title or description.
  - `group`: matches names starting with `f"{group}_"`.
  - Blank or `None` means no filter.

- [ ] **Step 1: Write the failing tests:**
  - `test_list_without_arguments_is_unchanged`: the length equals `len(TOOL_DISCOVERY_CATALOG)`.
  - `test_query_matches_all_tokens`: `query="property value"` contains `properties_get_property_values_of_elements`, and every result contains both tokens.
  - `test_group_filter`: `group="issues"` is non-empty and every name starts with `issues_`.
  - `test_no_match_returns_empty_list`.
  - `test_blank_query_lists_everything`: `query="  "` returns the full list.

- [ ] **Step 2: Run them.** Expected: FAIL (unexpected keyword).

- [ ] **Step 3: Implement.** Update the tool description: "Pass `query` (e.g. 'property values') to search; call without arguments only when you need the full list."

- [ ] **Step 4: Run all unit tests.** Expected: PASS.

- [ ] **Step 5: Commit** `feat: search the command catalog by query and group`.

---

### Task 5: Response envelope

**Files:**
- Modify: `src/tapir_archicad_mcp/pagination.py` (`PaginationSnapshot.page`)
- Create: `src/tapir_archicad_mcp/outcome.py`
- Modify: `src/tapir_archicad_mcp/tools/custom/functions.py` (`_execute_tool`)
- Test: `tests/unit/test_outcome.py`, plus updated exact-dict assertions in `tests/unit/test_jobs.py`

**Interfaces:**
- Produces: every paginated page carries `"envelope": {<field>: {"total": int, "returned": int, "truncated": bool}}`, with `truncated = returned < total`. Results of 100 items or fewer stay unchanged.
- Produces: `summarize_execution_results(result: Any) -> dict | None` returning `{"requested", "succeeded", "failed", "failuresByCause": [{"code", "message", "count", "examples": [index, ≤3]}]}`, sorted by `count` descending. It returns `None` when the result has no top-level list `executionResults`.
- `_execute_tool` adds the summary under `"outcome"` for `write` and `irreversible` commands only.

- [ ] **Step 1: Write the failing tests:**
```python
def test_outcome_groups_failures_by_cause():
    result = {"executionResults": [
        {"success": True},
        {"success": False, "error": {"code": 4003, "message": "locked"}},
        {"success": False, "error": {"code": 4003, "message": "locked"}},
        {"success": False, "error": {"code": 4012, "message": "hotlink"}},
    ]}
    s = summarize_execution_results(result)
    assert (s["requested"], s["succeeded"], s["failed"]) == (4, 1, 3)
    assert s["failuresByCause"][0] == {"code": 4003, "message": "locked", "count": 2, "examples": [1, 2]}

def test_no_outcome_without_execution_results():
    assert summarize_execution_results({"elements": []}) is None
```
  - `test_write_response_carries_outcome`: `elements_delete_elements` with `confirm`, and a canned response with one failure, gives `result["outcome"]["failed"] == 1`.
  - `test_read_response_has_no_outcome`.
  - `test_pages_carry_envelope`: 150 canned elements give a first page with `{"elements": {"total": 150, "returned": 100, "truncated": True}}`, and a continuation with `{"total": 150, "returned": 50, "truncated": True}`.

- [ ] **Step 2: Run them.** Expected: FAIL.

- [ ] **Step 3: Implement.**
  - The envelope is added in `page()`.
  - The summary is added in `_execute_tool` after dispatch, before `prepare_result`.
  - If the result already has an `outcome` key, leave it untouched and log a warning.

- [ ] **Step 4: Run all unit tests.** Expected: PASS. Update the exact-dict pagination assertions in `test_jobs.py` for the new key.

- [ ] **Step 5: Commit** `feat: report list totals and write outcomes in responses`.

---

### Task 6: Images as MCP image content

**Files:**
- Create: `src/tapir_archicad_mcp/images.py`
- Modify: `src/tapir_archicad_mcp/tools/custom/functions.py` (`archicad_read_tool`, `archicad_call_tool`, `archicad_get_job`)
- Test: `tests/unit/test_images.py`

**Interfaces:**
- Produces: `IMAGE_FIELDS: dict[str, str]` (tool name → result field). At least these entries:
  - `"elements_get_element_preview_image": "previewImage"`
  - `"favorites_get_favorite_preview_image": "previewImage"`
  - `"elements_get_room_image": "roomImage"`

  Complete it from base64 fields in multiconn's models (`grep -in base64 multiconn_archicad/models`).
- Produces: `to_tool_result(tool_name: str, result: Any, image_format: str = "png") -> CallToolResult | Any`.
  - For a dict whose field in `IMAGE_FIELDS` holds a string, it returns `CallToolResult(content=[TextContent(json of stripped dict), ImageContent(data=<base64>, mime=image/png|image/jpeg)], structured_content=<stripped dict>)`.
  - In the stripped dict, the field is replaced by the string `"returned as MCP image content #1"`.
  - Anything else is returned unchanged.
- The three tools are annotated `-> Annotated[CallToolResult, dict]`. With that, the SDK accepts both plain dicts and `CallToolResult` (`mcp/server/mcpserver/utilities/func_metadata.py` `convert_result`, and the `Annotated[CallToolResult, ReturnType]` handling around line 418).
- `image_format` comes from the call's `params.format` when present (read the `Format` enum values in multiconn).

- [ ] **Step 1: Write the failing tests:**
  - `test_preview_image_is_returned_as_image_content`: canned `GetElementPreviewImage` returns `{"previewImage": B64}`, where `B64` is a tiny PNG in base64. The result is a `CallToolResult`, `content[1].type == "image"`, `content[1].data == B64`, and `B64 not in json.dumps(result.structured_content)`.
  - `test_non_image_results_are_unchanged`: still a plain dict.
  - `test_get_job_returns_image_content`: after the call above, `archicad_get_job(<handle from list_jobs>)` also returns a `CallToolResult` with the image block, and the job envelope (`jobHandle`, `status`) is in `structured_content`.

- [ ] **Step 2: Run them.** Expected: FAIL.

- [ ] **Step 3: Implement.** In `archicad_get_job`, apply `to_tool_result` to `response["result"]` for completed jobs of image commands. The job's command name is on the `Job`.

- [ ] **Step 4: Run all unit tests.** Expected: PASS.

- [ ] **Step 5: Commit** `feat: return Archicad images as MCP image content`.

---

### Task 7: Change log

**Files:**
- Create: `src/tapir_archicad_mcp/project_identity.py`, `src/tapir_archicad_mcp/change_log.py`
- Modify: `src/tapir_archicad_mcp/context.py` (`change_log_instance`), `src/tapir_archicad_mcp/app.py` (lifespan), `src/tapir_archicad_mcp/server.py` (`--change-log`), `src/tapir_archicad_mcp/tools/custom/functions.py` (`_execute_tool`, `_build_ready_instance` reuses the fingerprint helper), `tests/conftest.py` (autouse `ChangeLog(tmp_path)`), `README.md` (configuration table)
- Test: `tests/unit/test_change_log.py`, `tests/unit/test_server_args.py`

**Interfaces:**
- Produces: `project_fingerprint(project_id) -> str`, giving the Solo path, `teamwork://{serverAddress}/{projectPath}`, or `"untitled"`. Plan 3 reuses it for set binding.
- Produces: `default_change_log_dir() -> Path`:
  - Windows: `%APPDATA%\tapir-archicad-mcp\change-log`
  - macOS: `~/Library/Application Support/tapir-archicad-mcp/change-log`
  - otherwise: `${XDG_DATA_HOME:-~/.local/share}/tapir-archicad-mcp/change-log`
- Produces: `ChangeLog(directory: Path, clock: Callable[[], datetime] = lambda: datetime.now(UTC))`. `.record(entry: dict) -> Path` appends one JSON line to `changes-YYYY-MM-DD.jsonl` (UTC date), creates the directory if needed, and returns the file path.
- Entry keys: `time`, `port`, `project`, `command`, `kind`, `argumentsPreview` (`jobs.arguments_preview`), `status` (`"completed"` or `"failed"`), `outcome` (Task 5 or `null`), `error` (message or `null`), `jobHandle`.
- CLI: `--change-log` / `TAPIR_MCP_CHANGE_LOG`, default `default_change_log_dir()`.

- [ ] **Step 1: Write the failing tests:**
  - `test_write_command_is_logged`: after a write through `archicad_call_tool` there is exactly one line. Its `command`, `kind == "write"` and `project == "/path/to/fake_project.pln"` are correct, and the response has `changeLog == str(that file)`.
  - `test_read_command_is_not_logged`.
  - `test_failed_write_is_logged_as_failed`: when the canned dispatch raises, the line has `status == "failed"` and the `ToolError` still reaches the caller.
  - `test_unwritable_change_log_does_not_hide_the_result`: the change-log directory points at an existing file. The command result is still returned, with a `changeLogError` key, and no exception is raised.
  - `test_fingerprints`: solo, teamwork and untitled values as above.
  - `test_server_args.py`: the default resolves to `default_change_log_dir()`, and `TAPIR_MCP_CHANGE_LOG` overrides it. Add the variable to `_clear_http_env`.

- [ ] **Step 2: Run them.** Expected: FAIL.

- [ ] **Step 3: Implement.**
  - Record in `_execute_tool` for `write` and `irreversible` commands, on both success and failure.
  - A logging failure is caught, logged to the server log, and surfaced as `changeLogError`.
  - Add one row to the README configuration table.

- [ ] **Step 4: Run all unit tests.** Expected: PASS.

- [ ] **Step 5: Commit** `feat: append every write to a JSONL change log`.

---

### After Task 7

- [ ] Run the full suite including integration tests: `… uv run pytest -q`. Expected: unit tests pass. Integration tests that need a live Archicad are skipped or reported as such.
- [ ] Live smoke test on Archicad 29 (`Demo_Sonderfaelle`), read-only plus one confirmed no-op:
  - `archicad_list_commands(query="wall")`.
  - `archicad_read_tool` on `elements_get_elements_by_type` Wall: check the envelope.
  - `elements_get_element_preview_image` for one wall: check that the image arrives.
  - `archicad_call_tool("project_save_project", {"port": …})` without `confirm`: check that it is refused.

  Record the results in the PR description.
- [ ] Stage 5 → review (pipeline per CLAUDE.md, then Dave's OK).
