---
stages:
  3: "PR 1: compact validation errors grouped by error type"
  4: "PR 2: image responses, mappings in the generator config"
  5: "PR 3: pagination counts"
  6: "PR 4: read/write classification and a read dispatcher"
current: 2
---

# Upstream PR Series Implementation Plan (Plan 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Offer the four pieces of fork work that the maintainer accepted to `SzamosiMate/tapir-archicad-MCP` as small, focused PRs, each built fresh on upstream master and shaped by his review notes.

**Architecture:** One branch per PR, cut from `upstream/master` and pushed to the fork. Each PR ports one feature from the fork's Plan 1 code, trimmed to what the maintainer asked for and adapted to upstream's current code (multiconn 0.9.5, Tapir 1.7.2, generator pinned to the tagged multiconn release). No fork-only machinery travels along.

**Tech Stack:** Python 3.12+, `mcp` (`mcp.server.mcpserver`), pydantic 2, multiconn_archicad 0.9.5 (upstream pin), pytest, uv.

**Source of requirements:**
- The maintainer's Discord replies of 2026-10-21/22, summarised in memory `project_tapir_mcp_fork.md`.
- The fork's Plan 1 code on fork master, which these PRs port:
  - `src/tapir_archicad_mcp/validation_errors.py` (commit ae46b72)
  - `images.py` (fb6d41e)
  - the list envelope from 709694e
  - the classification in `scripts/generator_config.py`
- Spec `docs/superpowers/specs/2026-10-19-token-efficient-element-sets-design.md`, steps 1–4. It is the background, but where it and the maintainer disagree, his notes win.

**Not in this plan:**
- Element sets and queries: the maintainer asked us to hold off; Plan 3 is paused after Stage 4.
- Confirm gate and the "irreversible" kind.
- Outcome summaries, change log, command search: he wants a different direction and has not yet said whether he wants PRs from us.
- The benchmark harness.

These items wait for his answer and get their own plan.

## Global Constraints

- **Base.** Every PR branch starts from a fresh `upstream/master` (`git fetch upstream`), is named `upstream/<topic>`, is pushed to `origin`, and opens against `SzamosiMate/tapir-archicad-MCP` `master`.
- **Never in an upstream PR:** `docs/superpowers/`, `.atrium.yaml`, `benchmark/`, `change_log.py`, `outcome.py`, `project_identity.py`, element-set modules, the `confirm` field, the `irreversible` kind.
- **Environment.** `UV_PROJECT_ENVIRONMENT=$LOCALAPPDATA/uv-envs/tapir-archicad-mcp-upstream`. This is a separate env, because upstream pins multiconn 0.9.5 and the fork env holds 0.9.4. Run `uv sync --locked` first. Never create `.venv`, and never put an env inside OneDrive.
- **Generated code.** A PR that touches `scripts/` regenerates with upstream's generator (`uv run python scripts/generate_tools.py`) at the pinned multiconn version. The diff of `src/tapir_archicad_mcp/tools/generated/` contains only the lines the PR intends.
- **Style.** Match upstream's code, comments and test style. English, plain, sparse comments. The smallest diff that does the job; no neighbouring clean-up.
- **Error text** never echoes caller input values.
- **Evidence per PR:** upstream's full test suite green, `ruff check --select F,I` clean on changed files, and for PRs 2 and 4 a live smoke on Archicad 29 against `Benchmark.pln`. Check the port and path via discovery first, and never save the project.
- **PR description:** short. What changed, why (one line on the problem), how it was tested, and a pointer to the Discord discussion. No internal plan or ledger references.
- **Gate before `gh pr create`.** This is outward-facing, so every PR needs the PR pipeline from CLAUDE.md first: review bundle, `/security-review`, verification. Then Dave's explicit Go for that PR.
- **Order and waiting.** A stage ends when its PR is open upstream. The next stage may start before the maintainer merges. If an earlier PR changes on review, later branches rebase on the updated `upstream/master`.

## Review Focus

1. **A rejected bulk call with thousands of the same error** gives one short line per error type with a count, not thousands of lines, and no input values. Test: Task 1 `test_many_errors_of_one_type_give_one_line`.
2. **A new image command added to the generator config** gets image content without any edit outside the config. Test: Task 2 `test_image_mapping_comes_from_the_registry`.
3. **The last page of a paged list** says `truncated: false` and `returned` equals the items on that page. Test: Task 3 `test_last_page_is_not_truncated`.
4. **A command that the generator meets but nobody classified** stops generation with a message naming it, and the generated wrappers never guess a kind. Test: Task 4 `test_generator_refuses_unclassified_commands`.
5. **`archicad_read_tool` given a write command** refuses before anything reaches Archicad, and the refusal names `archicad_call_tool`. Test: Task 4 `test_read_tool_refuses_write_commands`.

---

## Stage 3: PR 1 — compact validation errors

### Task 1: Group validation errors by error type

**Files:**
- Create: `src/tapir_archicad_mcp/validation_errors.py`
- Modify: `src/tapir_archicad_mcp/tools/custom/functions.py` (the `except ValidationError` in `archicad_call_tool`)
- Test: `tests/unit/test_validation_errors.py`, plus `tests/unit/test_custom_tools.py` where it asserts the old message

**Interfaces:**
- Produces: `format_validation_error(command_name: str, error: ValidationError, max_types: int = 5, max_locations: int = 3) -> str`.
  - It reads `error.errors(include_input=False, include_url=False)` and groups by pydantic `type`, in order of first occurrence.
  - Output:
    ```
    Invalid arguments for tool '<name>': <N> errors of <K> types.
    - <type> (<count>): <msg of the first error of this type> at <loc1>, <loc2>, <loc3>, …
    ...and <K - max_types> more error types.
    Call archicad_get_command_schema('<name>') for the exact schema.
    ```
  - The `…` appears only when a type has more locations than `max_locations`. The "more error types" line appears only when `K > max_types`.
  - A location is the dotted `loc`, for example `params.wallsData.3.begCoordinate`.

- [ ] **Step 1: Write the failing tests**
  - `test_many_errors_of_one_type_give_one_line`: 1,000 list items each missing the same field give exactly one type line, `missing (1000)`, three locations and `…`.
  - `test_types_keep_first_occurrence_order`.
  - `test_more_than_max_types_adds_a_tail_line`: 7 types give 5 lines plus "...and 2 more error types."
  - `test_input_values_are_not_echoed`: a rejected value `"secret-123"` is absent from the message.
  - `test_schema_hint_is_the_last_line`.
- [ ] **Step 2: Run them.** Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `format_validation_error`. In `archicad_call_tool`, raise `ToolError(format_validation_error(name, e)) from e`. The log line keeps the full error.
- [ ] **Step 4: Run the full suite.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: group validation errors by error type and omit input values`.

**Stage 3 ends** when the PR is open upstream. Before that: review, pipeline, and Dave's Go.

## Stage 4: PR 2 — image responses

### Task 2: Images as MCP image content, mapped in the generator config

**Files:**
- Modify: `scripts/generator_config.py`. Add the fields `image_fields: dict[str, str]` and `image_lists: dict[str, str]` to `ApiSourceConfig`, keyed by the camel command name like `paginated_commands`. Fill them in `TAPIR_CONFIG`:
  - `image_fields`: `GetElementPreviewImage` → `previewImage`, `GetFavoritePreviewImage` → `previewImage`, `GetRoomImage` → `roomImage`.
  - `image_lists`: `GetProfileAttributePreview` → `previewImages`.
  - Check every name against the 0.9.5 command list first. If a name is not there, stop and report it.
- Modify: `scripts/generate_tools.py`. Emit `image_field="…"` or `image_list_field="…"` into `register_tool_for_dispatch(...)`, the same way `pagination_field` is emitted.
- Modify: `src/tapir_archicad_mcp/tools/tool_registry.py`. The `register_tool_for_dispatch` keyword arguments `image_field: str | None = None` and `image_list_field: str | None = None` are stored on `ToolRegistryEntry`.
- Create: `src/tapir_archicad_mcp/images.py`, ported from the fork. `extract_images`, `image_content` and `to_tool_result` read the field names from the registry entry instead of module-level dicts.
- Modify: `src/tapir_archicad_mcp/tools/custom/functions.py`. Results of image commands go out as a `CallToolResult`: text and structured JSON with the base64 replaced by a reference, plus one `ImageContent` per image. This applies on the direct path and in `archicad_get_job`.
- Regenerate the wrappers. Only the four image commands' registration lines change.
- Test: `tests/unit/test_images.py`, `tests/unit/test_generator_config.py`.

**Interfaces:**
- Produces: `ToolRegistryEntry.image_field`, `ToolRegistryEntry.image_list_field`.
- Produces: `to_tool_result(entry: ToolRegistryEntry, result: Any) -> Any`.

- [ ] **Step 1: Write the failing tests**
  - `test_image_mapping_comes_from_the_registry`: a test-registered fake command with `image_field="snapshot"` returns image content. No edit to `images.py`.
  - `test_png_and_jpeg_mime_types`.
  - `test_profile_previews_keep_errors_and_number_images`.
  - `test_job_result_of_an_image_command_is_image_content`.
  - `test_generator_config_image_commands_exist`: every configured name is in the command list, as `paginated_commands` is checked today.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement, then regenerate.** Check that the generated diff touches only the four image commands.
- [ ] **Step 4: Run the full suite, then the live smoke.** `elements_get_element_preview_image` on one element of `Benchmark.pln` returns one PNG image block.
- [ ] **Step 5: Commit** `feat: return Archicad images as MCP image content, mapped in the generator config`.

**Stage 4 ends** when the PR is open upstream.

## Stage 5: PR 3 — pagination counts

### Task 3: Counts on paged lists

**Files:**
- Modify: `src/tapir_archicad_mcp/pagination.py` (`PaginationSnapshot.page`). Every paged response carries `envelope: {<field>: {"total": n, "returned": k, "truncated": bool}}`, where `truncated` means more pages follow.
- Modify: `src/tapir_archicad_mcp/tools/custom/functions.py`. Add one sentence to the `archicad_call_tool` description about `envelope`.
- Modify: `README.md`. Add one line where pagination is described.
- Test: the existing pagination tests in `tests/unit/test_custom_tools.py`, or a new `tests/unit/test_pagination.py` if upstream has none.

**Interfaces:**
- Produces the response key `envelope`. It is added only on paginated commands, on the first page and on continuations alike.

- [ ] **Step 1: Write the failing tests**
  - `test_first_page_reports_total_and_truncated`: 250 items give total 250, returned 100, truncated true.
  - `test_last_page_is_not_truncated`: the third page gives returned 50 and truncated false.
  - `test_short_list_fits_one_page`: 30 items give truncated false and no token.
  - `test_unpaginated_commands_have_no_envelope`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit** `feat: report total, returned and truncated for paged lists`.

**Stage 5 ends** when the PR is open upstream.

## Stage 6: PR 4 — read/write classification and a read dispatcher

### Task 4: Classify every command and add `archicad_read_tool`

**Files:**
- Modify: `scripts/generator_config.py`. Add explicit `read_commands: set[str]` and `write_commands: set[str]` per `ApiSourceConfig`, using camel names.
  - Port the fork's classification and fold the fork's `irreversible` set into `write`.
  - Classify every command that upstream's 0.9.5 / Tapir 1.7.2 list adds. **Ambiguous new commands go to Dave before Step 3**, as a short table with the command, the proposed kind and the reason.
  - "Read" means: it changes no project data, no files and no shared Teamwork state.
- Modify: `scripts/generate_tools.py`.
  - Emit `kind="read"|"write"` into each registration.
  - If a command is in neither set, or in both, stop generation and name it.
- Modify: `src/tapir_archicad_mcp/tools/tool_registry.py`. Add `kind: Literal["read", "write"]` to `register_tool_for_dispatch` and `ToolRegistryEntry`, and add `kind` to the catalog entry.
- Modify: `src/tapir_archicad_mcp/tools/custom/functions.py` and `models.py`.
  - `archicad_list_commands` shows each command's `kind`.
  - Add a new MCP tool `archicad_read_tool(name, arguments)` with annotations `readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False`. It runs read commands through the same path as `archicad_call_tool`.
  - A write command makes it refuse before validation: "'<name>' changes the project; use archicad_call_tool."
  - `archicad_call_tool` keeps running every command, unchanged.
  - Both descriptions say which tool to use for what.
- Regenerate the wrappers. Only `kind=` lines change.
- Test: `tests/unit/test_command_kinds.py`, `tests/unit/test_custom_tools.py`, `tests/unit/test_tool_annotations.py`, `tests/unit/test_generator_config.py`.

**Interfaces:**
- Produces `ToolRegistryEntry.kind`, the catalog `kind`, and the MCP tool `archicad_read_tool`.

- [ ] **Step 1: Write the failing tests**
  - `test_generator_refuses_unclassified_commands`: a fake command list with one extra name raises naming it.
  - `test_command_in_both_sets_is_refused`.
  - `test_every_registered_command_has_a_kind`.
  - `test_read_tool_refuses_write_commands`: `calls == []`.
  - `test_read_tool_runs_read_commands_and_pages`.
  - `test_read_tool_annotations`.
  - `test_catalog_shows_kind`.
  - A pin test of a handful of well-known kinds. For example `GetAllElements` read, `CreateWalls` write, `DeleteElements` write, `SaveProject` write, `IFCFileOperation` write, `ReserveElements` write.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Classify.**
  - Get Dave's OK on the ambiguous table.
  - Implement, regenerate, and check the generated diff.
- [ ] **Step 4: Run the full suite, then the live smoke.**
  - `archicad_read_tool` with `elements_get_elements_by_type` returns walls.
  - `archicad_read_tool` with `elements_create_walls` is refused, and no element is created.
- [ ] **Step 5: Commit** `feat: classify commands as read or write and add a read-only dispatcher`.

**Stage 6 ends** when the PR is open upstream. After the last PR: update memory, and write the follow-up plan for the "different direction" items once the maintainer has answered.
