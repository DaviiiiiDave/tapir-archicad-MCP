---
stages:
  3: "Element sets core: store, set references, auto-sets (Task 1-3, spec step 5)"
  4: "Typed values and criteria semantics (Task 4-5, spec step 6)"
  5: "Property search, rows, aggregates, export (Task 6-8, spec step 7)"
  6: "Set definitions and set commands (Task 9-10, spec step 8)"
current: 2
---

# Element Sets Implementation Plan (Plan 3 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Element data stays in the server. The model works with set references, counts, summaries and small samples. This covers spec steps 5 to 8: sets, typed values, rows/aggregates/export, and definitions with the fallback filter path.

**Architecture:**
- **New modules.** Flat runtime modules next to `jobs.py` / `pagination.py`, one per concern: `element_sets.py`, `set_references.py`, `auto_sets.py`, `property_values.py`, `criteria.py`, `property_search.py`, `set_reads.py`, `set_definitions.py`.
- **New commands.** They are dispatchable commands in `tools/custom/set_commands.py`, registered with `register_tool_for_dispatch`. There are no new top-level MCP tools.
- **Changes to existing code.** Set references are expanded on the raw arguments in `_run_command` before validation. Auto-sets are created in `_execute_tool`.

**Tech Stack:** Python ≥3.12, `mcp` 2.2.0, `multiconn-archicad` 0.9.4 (generator pinned to `v0.9.4`), pydantic v2, pytest + pytest-asyncio, uv. Stdlib only for fuzzy matching (`difflib`) and CSV.

**Spec:** `docs/superpowers/specs/2026-10-19-token-efficient-element-sets-design.md`, §5.4–5.6 and §6 steps 5–8. Step 9 (in-process filter path) stays deferred until Tapir ships it; the spec's two-path equivalence test arrives with it, since only the fallback path exists until then. If plan and spec disagree, the spec wins. Stop and ask, except for the decisions Dave approves below.

**Follow-up:** Plan 4 covers set writes and revert (spec §5.7, step 10). Arm C of the benchmark runs after Plan 4. The read tasks T1 and T3 can be measured after Stage 6.

## Decisions for Dave's review

1. **Built-in `nonLocalizedName` source.** Spec §5.5 says it comes from the official `GetDetailsOfProperties`. Its `PropertyDefinition` has no such field (multiconn 0.9.4 models).
   - The official API gives it through `GetAllPropertyNames` (`BuiltIn` items carry `nonLocalizedName`) plus `GetPropertyIds` (name → GUID).
   - Task 6 uses those two. The join stays "by GUID", as the spec says.
2. **`sets_export` is kind `write`.** It creates a file, and Plan 1 defines read as "does not change project data or files". So it runs through `archicad_call_tool` and is recorded in the change log.
   - It never overwrites: the server picks a new file name every time. So it is not `irreversible`.
3. **Fallback values (§9, builder's call).** Cells read through Tapir's string path are marked `typed: false`.
   - On them, `Equal`/`NotEqual` compare the display string exactly, and `Contains` is a case-insensitive substring match.
   - Numeric comparisons and status operators cannot be decided. Those elements never match, and every response counts them as `notComparable`.
4. **Numbers compare with a tolerance.** Numeric `Equal`/`NotEqual` use `math.isclose(rel_tol=1e-9, abs_tol=1e-12)`, because SI lengths are floats (`0.1 + 0.2`).
5. **One PR per stage.** Spec §6 makes each step its own PR to the fork's master. Each stage (one spec step) ends with review, then a PR from `feature/element-sets`, then Dave's merge. Work continues on the same branch.
6. **§9 values.**
   - `SetStore` LRU capacity: 64.
   - Set id format: `set:` plus 8 hex characters.
   - Inline threshold: 20, as in the spec.
   - Aggregate shows the top 20 groups plus `others`.
   - Export directory default: `<Documents>/tapir-archicad-mcp/exports`.
   - CSV is UTF-8 with BOM, so Excel shows umlauts.

## Global Constraints

- **Compatibility.** Target Archicad 29. Nothing may break the older Archicad versions Tapir supports.
- **Top-level tools.** The meta-tools and `discovery_list_active_archicads` keep their names, arguments and behaviour. New capabilities are dispatchable commands registered with `register_tool_for_dispatch`. There are no new top-level tools.
- **Generated code.** Generated files under `src/tapir_archicad_mcp/tools/generated/` are never edited by hand.
- **Explicit tables.** Behaviour is driven by explicit tables (`SET_EXPANSION_FIELDS`, `AUTO_SET_FIELDS`), never by inference at runtime. Tests may introspect, in order to pin a table.
- **Language.** Code, docs, tool descriptions and error messages are in English.
- **Statuses.** Status values are never collapsed into "empty". An undefined, not-available or not-evaluated value never matches a comparison, including `NotEqual`.
- **No reserving.** Nothing in this plan reserves Teamwork elements.
- **Sets live in memory.** Sets are lost on server restart. Every "unknown set" error says so and tells the model to recreate the set from its query or definition.
- **Flag.** `--element-sets on|off` (env `TAPIR_MCP_ELEMENT_SETS`) turns off the replacement of result lists. Default: `on`.
- **uv environment.** Every `uv` command runs with `UV_PROJECT_ENVIRONMENT="$LOCALAPPDATA/uv-envs/tapir-archicad-mcp"`. Never create `.venv` in the repo.
- **Tests.** Run `uv run pytest` from the worktree root. All tests pass at the end of every task.
- **Commits and upstream.** Commit messages use `feat:` / `fix:` / `test:` / `docs:` and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Upstream PR branches later must not contain `docs/superpowers/` or `.atrium.yaml`.
- **Test doubles.** `FakeArchicad` (`tests/conftest.py`) serves official commands too: `core.post_command` uses the same canned table. Register them as `"API.<Name>"`.

## Review Focus

1. **The project on a port changes under a set**, through Save as or opening another file. Using the set must fail with a message that names the change, never act on the new project. Tests: Task 1 `test_get_refuses_changed_project`, Task 2 `test_set_reference_after_project_change_is_refused`.
2. **A set reference where none is accepted, or a malformed one** (`{"set": "set:x", "extra": 1}`). The error must name the field and the accepted fields, and Archicad must not be called. Tests: Task 2 `test_set_reference_in_unsupported_field_is_refused`, `test_malformed_set_reference_is_refused`.
3. **Float equality on SI values**, for example a wall thickness of 0.30000000000000004 m against criterion value 0.3. It must match. Test: Task 5 `test_number_equality_tolerates_float_noise`.
4. **Sub-element values through the Tapir fallback.** They must stay in rows as `typed: false`, never dropped. Numeric criteria on them must be counted as `notComparable`, never silently "no match". Tests: Task 4 `test_failed_official_read_falls_back_to_tapir_strings`, Task 9 `test_untyped_cells_are_counted_not_dropped`.
5. **The inline threshold boundary and the flag.** 20 elements stay inline with a set id. 21 are replaced by a summary with no `next_page_token`. With the flag off, the result and its pagination are exactly as before. Tests: Task 3 `test_threshold_boundary`, `test_flag_off_keeps_raw_lists_and_pages`.

---

## Stage 3: Element sets core (spec step 5)

### Task 1: SetStore and set binding

**Files:**
- Create: `src/tapir_archicad_mcp/element_sets.py`
- Modify: `src/tapir_archicad_mcp/context.py` (`set_store_instance`), `src/tapir_archicad_mcp/app.py` (lifespan creates `SetStore()`), `tests/conftest.py` (autouse fixture like `change_log`)
- Test: `tests/unit/test_element_sets.py`

**Interfaces:**
- Produces: `ElementSet`, a frozen dataclass with fields `id: str`, `port: int`, `project: str`, `guids: tuple[str, ...]`, `created_at: datetime`, `origin: dict`.
- Produces: `SetStore(capacity: int = 64, clock: Callable[[], datetime] = lambda: datetime.now(UTC), new_id: Callable[[], str] = lambda: f"set:{secrets.token_hex(4)}")`.
  - `.create(port: int, project: str, guids: Iterable[str], origin: dict) -> ElementSet` de-duplicates and keeps first-seen order.
  - `.get(set_id: str, port: int, project: str) -> ElementSet` marks the set as recently used and raises `ToolError` for the cases tested below.
- Produces: `set_store_instance: ServerState[SetStore]`.

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_element_sets.py`):
  - `test_create_deduplicates_in_order`: `["b", "a", "b"]` gives `("b", "a")`.
  - `test_lru_evicts_least_recently_used`: capacity 2. Create s1 and s2, `get(s1)`, then create s3. `get(s2)` raises, `get(s1)` works.
  - `test_unknown_set_explains_restart`: the message contains `"lost when the server restarts"` and `"create it again"`.
  - `test_get_refuses_other_port`: the message names both ports.
  - `test_get_refuses_changed_project`: the set was created for `/a.pln`, `get` is called with `/b.pln`. The message contains `"/a.pln"`, `"/b.pln"` and `"Save as"`.
- [ ] **Step 2: Run them.** Expected: FAIL (module missing).
- [ ] **Step 3: Implement** with an `OrderedDict` for the LRU. Wire `set_store_instance` into the app lifespan, set and clear it the same way as `change_log_instance`. Add the autouse fixture.
- [ ] **Step 4: Run all tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add an in-memory element set store bound to port and project`.

### Task 2: Set references in element-list parameters

**Files:**
- Create: `src/tapir_archicad_mcp/set_references.py`
- Modify: `src/tapir_archicad_mcp/tools/custom/functions.py` (`_run_command`, `_write_change_log`, `archicad_list_commands`), `src/tapir_archicad_mcp/tools/tool_registry.py` (`get_tool_input_schema`), `src/tapir_archicad_mcp/tools/custom/models.py` (`CommandOverview`)
- Test: `tests/unit/test_set_references.py`

**Interfaces:**
- Consumes: `SetStore.get`, `set_store_instance` (Task 1); `read_project_fingerprint` (`project_identity.py`).
- Produces: `SET_EXPANSION_FIELDS: dict[str, tuple[str, ...]]`, mapping tool name to the `params` fields that accept `{"set": ...}`.
  - It is a literal table: every `params` field typed `list[ElementIdArrayItem]` (items are exactly `{"elementId": {"guid"}}`).
  - Today that is 43 fields on 41 commands, for example `elements_highlight_elements: ("elements",)` and `elements_get_collisions: ("elementsGroup1", "elementsGroup2")`.
  - Element pairs (`elementPropertyValues`, `elementClassifications`, …) are not in it.
- Produces: `is_set_reference(value: Any) -> bool`, true for a dict whose only key is `"set"` and whose value is a string.
- Produces: `resolve_set(conn_header: ConnHeader, port: int, set_id: str) -> ElementSet`. It reads the project fresh with `read_project_fingerprint(conn_header)`, then calls `set_store_instance.get().get(...)`. It is blocking, so call it in a thread. It takes the header, not just the port, so `set_references.py` never imports `functions.py`; `_run_command` passes `_get_header(port)`.
- Produces: `expand_set_references(name: str, arguments: dict, resolve: Callable[[str], ElementSet]) -> tuple[dict, dict[str, str]]`. It returns new arguments, and the expanded field paths mapped to set ids (`{"params.elements": "set:ab12cd34"}`). It never mutates its input.
- Produces: `patch_schema(name: str, schema: dict) -> dict`, which gives each expandable property `anyOf: [<original>, {"$ref": "#/$defs/SetReference"}]`.
  - `$defs.SetReference` is `{"type": "object", "properties": {"set": {"type": "string", "pattern": "^set:[0-9a-f]{8}$"}}, "required": ["set"], "additionalProperties": false, "examples": [{"set": "set:ab12cd34"}]}`.
- Produces: `CommandOverview.acceptsSets: bool` (spec §5.2).

- [ ] **Step 1: Write the failing tests:**
  - `test_expansion_table_matches_the_parameter_models`: introspect every registered `params_model` for `list[ElementIdArrayItem]` fields. The result must equal `SET_EXPANSION_FIELDS`. This is the snapshot the spec asks for: a Tapir bump shows up as a failing diff.
  - `test_set_reference_is_expanded_before_validation`:
    - Create a set with 3 guids.
    - Call `archicad_read_tool("elements_highlight_elements", {"port": 19723, "params": {"elements": {"set": id}}})` against a canned `HighlightElements`.
    - The Tapir call receives the 3 `{"elementId": {"guid": ...}}` items in order.
  - `test_set_reference_in_unsupported_field_is_refused`: a set reference in `properties_set_property_values_of_elements.elementPropertyValues`. The `ToolError` names the field, and `fake_archicad.calls == []`.
  - `test_malformed_set_reference_is_refused`: `{"set": id, "extra": 1}` gives a `ToolError` and no call.
  - `test_set_reference_on_other_port_is_refused`, and `test_set_reference_after_project_change_is_refused` (change `fake_archicad.archicad_id` between create and use).
  - `test_change_log_records_the_set_id`: a write with a set reference (`elements_lock_elements`) logs `"sets": {"params.elements": id}`.
  - `test_schema_offers_set_reference`: `archicad_get_command_schema("elements_highlight_elements")` has the `anyOf` and `$defs.SetReference`.
  - `test_catalog_says_which_commands_accept_sets`: `acceptsSets` is true for `elements_highlight_elements` and false for `app_get_add_on_version`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - In `_run_command`, after the read-only check and before `model_validate`: when `arguments.get("port")` is an int and any top-level `params` field holds a set reference, expand in `asyncio.to_thread`.
  - `store.submit` keeps the *original* arguments for its preview, so jobs show the set id, not thousands of guids.
  - Pass the field-to-set mapping down to `_write_change_log`, which adds it as `"sets"` when it is not empty.
  - `get_tool_input_schema` applies `patch_schema` before caching.
- [ ] **Step 4: Run all tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: accept set references in element-list parameters`.

### Task 3: Auto-sets from results and the set summary

**Files:**
- Create: `src/tapir_archicad_mcp/auto_sets.py`
- Modify: `src/tapir_archicad_mcp/tools/custom/functions.py` (`_execute_tool`), `src/tapir_archicad_mcp/app.py` (`element_sets_enabled: bool = True`), `src/tapir_archicad_mcp/server.py` (`--element-sets` / `TAPIR_MCP_ELEMENT_SETS`), `README.md` (configuration table)
- Test: `tests/unit/test_auto_sets.py`, `tests/unit/test_server_args.py`

**Interfaces:**
- Consumes: Task 1 and `read_project_fingerprint`.
- Produces: `AUTO_SET_FIELDS: dict[str, str]`:
  - `"elements_get_all_elements": "elements"`
  - `"elements_get_elements_by_type": "elements"`
  - `"elements_get_elements_by_classification": "elements"`
  - `"elements_get_selected_elements": "elements"`
  - `"elements_filter_elements": "elements"`
  - `"issues_get_elements_attached_to_issue": "elements"`
- Produces: `INLINE_THRESHOLD = 20`.
- Produces: `summarize_set(conn_header, element_set: ElementSet, port: int, warnings: Sequence[str] = ()) -> dict`, which returns `{"set", "count", "byType", "createdAt", "warnings", "next"}`.
  - `byType` comes from one official `API.GetTypesOfElements` call over all guids. Elements whose type the API reports as an error are counted under `"(unreadable)"`.
  - `next` holds the two templates `"archicad_read_tool(name='sets_rows', arguments={'port': <port>, 'params': {'set': '<id>', 'columns': [...]}})"` and the same shape for `sets_aggregate` with `'groupBy': ...`.
  - `warnings` always includes `UNTITLED_WARNING` when `project == "untitled"`.
- Produces: `apply_auto_set(name: str, result: dict, conn_header, port: int, parameters: str) -> dict`.
  - Origin: `{"command": name, "parametersHash": sha256(parameters)[:16]}`.
  - With 20 or fewer items, the list stays and `"set": <summary>` is added.
  - Above that, the list field is removed and `"set": <summary>` is added.

- [ ] **Step 1: Write the failing tests:**
  - `test_large_result_is_replaced_by_a_summary`: 150 canned elements of mixed types. There is no `"elements"`, no `next_page_token` and no `envelope`. The `count` is 150 and `byType` matches.
  - `test_threshold_boundary`: 20 elements keep the list plus `set`, 21 give a summary only.
  - `test_flag_off_keeps_raw_lists_and_pages`: with `element_sets_enabled = False`, 150 elements give page one of 100, an `envelope`, a `next_page_token`, and no set in the store.
  - `test_filter_elements_result_becomes_a_set`.
  - `test_summary_next_templates_name_the_set_and_port`.
  - `test_untitled_project_set_carries_a_warning`.
  - `test_server_args.py`: `--element-sets off` and `TAPIR_MCP_ELEMENT_SETS=off` both give `False`. The default is `True`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Call `apply_auto_set` in `_execute_tool` for read commands in `AUTO_SET_FIELDS` when the flag is on, before `prepare_result`. When the list was removed, return without pagination. `parameters` is `_serialized_parameters(...)`, already used for pagination.
- [ ] **Step 4: Run all tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: store element-list results as sets and summarise large ones`.

**Stage 3 ends here:** review, PR, Dave's merge, then Stage 3→4.

## Stage 4: Typed values and criteria (spec step 6)

### Task 4: Typed value cells with the Tapir fallback

**Files:**
- Create: `src/tapir_archicad_mcp/property_values.py`
- Test: `tests/unit/test_property_values.py`

**Interfaces:**
- Produces: `Cell`, a frozen dataclass:
  - `status: Literal["normal", "userUndefined", "notAvailable", "notEvaluated"]`
  - `value: float | int | str | bool | list | None`
  - `value_type: str`: the official `type`, for example `"length"`, `"singleEnum"`, `"stringList"`. Fallback cells use `"string"`.
  - `typed: bool = True`
- Produces: `UNITS = {"length": "m", "area": "m²", "volume": "m³", "angle": "rad"}`. List variants (`lengthList`, …) use the same unit.
- Produces: `ValueTable`, a dataclass with `cells: dict[str, dict[str, Cell]]` (element guid → property guid → cell) and `unreadable: tuple[str, ...]`.
- Produces: `read_property_values(conn_header, guids: Sequence[str], property_guids: Sequence[str]) -> ValueTable`.
  - It makes one official `API.GetPropertyValuesOfElements` call.
  - Every element with an element- or property-level `ErrorItem` goes into one Tapir `GetPropertyValuesOfElements` call. Its cells are `status="normal"`, the display string, `typed=False`.
  - Elements that fail in both calls go to `unreadable`.
  - Enum values: `singleEnum` becomes its `displayValue` (or its `nonLocalizedValue` when that is all the API gives). `multiEnum` becomes a list of those.

- [ ] **Step 1: Write the failing tests** with canned official and Tapir responses:
  - `test_normal_length_is_si`: `{"type": "length", "status": "normal", "value": 0.3}` gives `Cell("normal", 0.3, "length")`.
  - `test_statuses_are_kept_apart`: `userUndefined`, `notAvailable` and `notEvaluated` give three distinct statuses with `value is None`.
  - `test_enum_display_values`.
  - `test_failed_official_read_falls_back_to_tapir_strings`: one element has an `ErrorItem`. Its cell is `typed=False` with Tapir's string, and the other elements stay typed.
  - `test_element_unreadable_in_both_is_listed`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: read property values typed, with a marked Tapir fallback`.

### Task 5: Criteria semantics

**Files:**
- Create: `src/tapir_archicad_mcp/criteria.py`
- Test: `tests/unit/test_criteria.py`

**Interfaces:**
- Consumes: `Cell` (Task 4).
- Produces: `Operator = Literal["Equal", "NotEqual", "Less", "LessOrEqual", "Greater", "GreaterOrEqual", "Contains", "HasValue", "IsUserUndefined", "IsNotAvailable", "IsDefault"]`.
- Produces: `Criterion(BaseModel)` with `property: PropertyRef` (`{"guid": str}`), `operator: Operator` and `value: Any = None`.
  - A validator requires `value` for the comparison operators and `Contains`, and forbids it for the status operators.
  - `extra="forbid"`.
- Produces: `matches(cell: Cell, criterion: Criterion) -> bool | None`. `None` means "cannot be decided on this cell". It is only returned for `typed=False` cells (decision 3).
- Produces: `ensure_supported(criterion: Criterion) -> None`. It raises `ToolError` for `IsDefault`: "IsDefault needs Archicad's own criteria evaluation, which the installed Tapir does not offer yet."
- Rules (spec §5.6 plus decisions 3 and 4):
  - Only `status == "normal"` can match a comparison.
  - `HasValue`, `IsUserUndefined` and `IsNotAvailable` test the status.
  - Enum: exact display value.
  - `Contains`: case-insensitive substring on strings, "one item equals" on lists and multi-enums.
  - Numbers: `math.isclose` for `Equal`/`NotEqual`.
  - A type mismatch, such as `Greater` on a string or a non-numeric `value` on a length, raises `ToolError` naming the property guid and the value type. It never quietly returns `False`.

- [ ] **Step 1: Write the failing tests.** Use one parametrized table covering every operator × {normal, userUndefined, notAvailable, notEvaluated, untyped}. It includes:
  - `test_undefined_never_matches_not_equal`.
  - `test_number_equality_tolerates_float_noise`: 0.30000000000000004 `Equal` 0.3 gives `True`.
  - `test_contains_on_multi_enum_means_one_item_equals`.
  - `test_type_mismatch_raises`.
  - `test_is_default_is_refused_on_the_fallback_path`.
  - `test_untyped_numeric_comparison_is_undecided` (`None`).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add one module for criteria and value-status semantics`.

**Stage 4 ends here:** review, PR, merge, then Stage 4→5.

## Stage 5: Property search, rows, aggregates, export (spec step 7)

### Task 6: `properties_search`

**Files:**
- Create: `src/tapir_archicad_mcp/property_search.py`, `src/tapir_archicad_mcp/tools/custom/set_commands.py` (first dispatchable custom command; imported from `tools/custom/__init__.py` next to `functions`)
- Test: `tests/unit/test_property_search.py`

**Interfaces:**
- Produces: `search_properties(conn_header, query: str, alternative_queries: Sequence[str] = (), group: str | None = None, limit: int = 10) -> list[dict]`.
  - Each item: `{"guid", "group", "name", "valueType", "measureType", "builtIn": bool, "nonLocalizedName": str | None, "score": float}`.
- Data sources:
  - Tapir `GetAllProperties` gives names in the project's language, the group, value and measure type, and the guid.
  - Official `API.GetAllPropertyNames` gives the `BuiltIn` names. `API.GetPropertyIds` turns them into guids. The join is by guid (decision 1).
- Score: the maximum over the query and its alternatives of `difflib.SequenceMatcher(None, q, c).ratio()`. Compare against the casefolded, whitespace-collapsed name, `"group name"`, and `nonLocalizedName`. Add `+ 0.2` when every query token is a substring of the name. Sort by score, then name.
- Convention for every command in `set_commands.py`: the callable is `(conn_header: ConnHeader, params: <Model>) -> dict`, like the generated wrappers. It takes the port from `conn_header.port` (`FakeArchicad` has it too).
- Produces: the dispatchable command `properties_search`, kind `read`, with params `PropertySearchParameters(query: str, alternativeQueries: list[str] = [], group: str | None = None, limit: int = Field(10, ge=1, le=50))`. It returns `{"properties": [...]}`.

- [ ] **Step 1: Write the failing tests:**
  - `test_finds_localized_name_without_umlaut_typing`: "feuerwiderstand" finds "Feuerwiderstandsklasse 13501-2".
  - `test_finds_built_in_by_non_localized_name`.
  - `test_alternative_queries_raise_the_score`.
  - `test_group_filter_and_limit`.
  - `test_command_is_registered_as_read`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add properties_search over localized and built-in names`.

### Task 7: Columns and `sets_rows`

**Files:**
- Create: `src/tapir_archicad_mcp/set_reads.py`
- Modify: `src/tapir_archicad_mcp/tools/custom/set_commands.py`
- Test: `tests/unit/test_set_reads.py`

**Interfaces:**
- Consumes: `resolve_set` (Task 2), `read_property_values` (Task 4), `Criterion` / `matches` / `ensure_supported` (Task 5).
- Produces: `Column = PropertyColumn | Literal["type", "layer", "story", "isEditable"]`, where `PropertyColumn` is `{"property": {"guid": str}}`.
- Produces: `read_columns(conn_header, guids: Sequence[str], columns: Sequence[Column]) -> ColumnTable`.
  - `ColumnTable` has `headers: list[dict]` (`key`, `label`, `unit`), `rows: dict[str, list[Any]]` (guid → rendered cells), `unreadable: tuple[str, ...]` and `not_comparable: int`.
  - `type` comes from official `API.GetTypesOfElements`.
  - `layer` and `story` come from one Tapir `GetDetailsOfElements` call with `fields: ["layerIndex", "floorIndex"]`. The layer is resolved through Tapir `GetAttributesByType` (`attributeType: "Layer"`) to `{"guid", "name"}`, the story through Tapir `GetStories` to `{"index", "name"}`.
  - `isEditable` is membership in one Tapir `FilterElements` result with `filters: ["IsEditable"]`.
  - Rendering:
    - A normal cell becomes its value.
    - A status becomes `{"status": "<status>"}`.
    - Untyped becomes `{"value": "<string>", "typed": false}`.
  - The header label of a property is `<group> / <name>` from Tapir `GetAllProperties`, with the unit from `UNITS`.
- Produces: `filter_guids(conn_header, guids, where: Sequence[Criterion]) -> tuple[tuple[str, ...], int]`. It returns the matching guids and the count of not-comparable elements. All criteria must match (AND), and each criterion is one bulk read over the remaining guids.
- Produces: the command `sets_rows`, kind `read`.
  - Params: `{set: str, columns: list[Column] (min 1), where: list[Criterion] = [], orderBy: {"column": int (index into columns), "descending": bool = False} | None, offset: int = 0, limit: int = Field(20, ge=1, le=100)}`.
  - Returns: `{"set", "columns": headers, "rows": [{"elementId": {"guid"}, "values": [...]}], "envelope": {"rows": {"total", "returned", "truncated"}}, "notComparable", "unreadable"}`.
  - Statuses sort after normal values.

- [ ] **Step 1: Write the failing tests:**
  - `test_rows_default_20_and_hard_maximum_100`.
  - `test_columns_resolve_layer_and_story_names`.
  - `test_status_cells_are_never_empty_strings`.
  - `test_where_filters_and_counts_not_comparable`.
  - `test_order_by_puts_statuses_last`.
  - `test_unreadable_elements_are_counted`.
  - `test_rows_on_unknown_set_explains_restart`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: read bounded rows of a set with typed columns`.

### Task 8: `sets_aggregate` and `sets_export`

**Files:**
- Modify: `src/tapir_archicad_mcp/set_reads.py`, `src/tapir_archicad_mcp/tools/custom/set_commands.py`, `src/tapir_archicad_mcp/app.py` (`export_directory: Path | None`), `src/tapir_archicad_mcp/server.py` (`--export-dir` / `TAPIR_MCP_EXPORT_DIR`), `README.md`
- Test: `tests/unit/test_set_reads.py`, `tests/unit/test_server_args.py`

**Interfaces:**
- Produces: `MAX_GROUPS = 20`.
- Produces: `default_export_dir() -> Path`:
  - Windows: `%USERPROFILE%\Documents\tapir-archicad-mcp\exports`
  - macOS: `~/Documents/tapir-archicad-mcp/exports`
  - Otherwise: `${XDG_DOCUMENTS_DIR:-~/Documents}/tapir-archicad-mcp/exports`
- Produces: the command `sets_aggregate`, kind `read`.
  - Params: `{set, groupBy: Column, metrics: list[{"op": "sum" | "min" | "max", "column": PropertyColumn}] = [], where: list[Criterion] = []}`.
  - Returns: `{"set", "groupBy": header, "groups": [{"value" | "status", "count", "metrics"?}], "others": {"groups", "count"} | None, "cardinality", "notComparable", "unreadable"}`.
  - Status groups come first, then normal values by count descending. Only the top `MAX_GROUPS` groups are listed.
  - Metrics use normal numeric cells only and say how many cells they used.
- Produces: the command `sets_export`, kind `write` (decision 2).
  - Params: `{set, columns: list[Column], where: list[Criterion] = [], format: Literal["csv"] = "csv"}`.
  - It writes `<export dir>/<set hex>-<UTC %Y%m%d-%H%M%S>.csv` in UTF-8 with BOM. Header labels carry the unit, and status cells are written as `<userUndefined>` and so on.
  - Returns: `{"path", "rows", "columns"}`.

- [ ] **Step 1: Write the failing tests:**
  - `test_aggregate_lists_status_buckets_first`.
  - `test_aggregate_caps_groups_with_others`.
  - `test_aggregate_metrics_use_normal_numbers_only`.
  - `test_export_writes_every_row_with_units_and_statuses`: 150 rows and a BOM, read back with `csv`.
  - `test_export_never_overwrites`: two exports give two files.
  - `test_export_is_write_and_logged`.
  - The server args default and env override.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: aggregate sets and export them as CSV`.

**Stage 5 ends here:** review, PR, merge, then Stage 5→6.

## Stage 6: Set definitions and commands (spec step 8)

### Task 9: Set definitions and the fallback evaluation

**Files:**
- Create: `src/tapir_archicad_mcp/set_definitions.py`
- Test: `tests/unit/test_set_definitions.py`

**Interfaces:**
- Consumes: `filter_guids` (Task 7) and `Criterion` (Task 5).
- Produces: `SetDefinition(BaseModel, extra="forbid")`, the normative schema of spec §5.6:
  - `source: {types: list[ElementType] = [], flags: list[ElementFilter] = [], databases: list[DatabaseIdArrayItem] | None = None}`
  - `layers: list[{"guid": str}] = []`
  - `stories: list[int] = []`
  - `where: list[Criterion] = []`
  - `ElementType`, `ElementFilter` and `DatabaseIdArrayItem` are the multiconn Tapir types.
- Produces: `VIEW_DEPENDENT_FLAGS = {"IsVisibleByLayer", "IsVisibleByRenovation", "IsVisibleByStructureDisplay", "IsVisibleIn3D", "OnActualFloor", "OnActualLayout", "InCroppedView", "IsOverriddenByRenovation"}`.
- Produces: `evaluate_definition(conn_header, definition: SetDefinition) -> Evaluation`.
  - `Evaluation` has `guids: tuple[str, ...]`, `warnings: list[str]`, `scope: dict` (`databases`, `includesSubElements`), `not_comparable: int` and `stories: list[dict]`.
  - Steps:
    1. Get candidates: Tapir `GetElementsByType` per type with `filters` and `databases`, unioned in order. With no types, use `GetAllElements`.
    2. Check every layer guid against `GetAttributesByType` (Layer) and every story index against `GetStories`. An unknown one raises `ToolError` naming it.
    3. Filter layers and stories with one `GetDetailsOfElements` call.
    4. Apply `filter_guids` with `where`, after `ensure_supported` on every criterion.
  - A warning is added for every view-dependent flag used. `stories` holds `{index, name}` of the stories used, as the summary must name them.

- [ ] **Step 1: Write the failing tests:**
  - `test_types_flags_and_databases_reach_tapir`.
  - `test_all_criteria_must_match`.
  - `test_layer_guid_resolves_to_index`, and `test_unknown_layer_guid_is_named`.
  - `test_unknown_story_index_is_named`.
  - `test_view_dependent_flag_warns`.
  - `test_untyped_cells_are_counted_not_dropped`.
  - `test_is_default_is_refused`.
  - `test_sub_elements_only_when_requested`, which also checks `scope.includesSubElements`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: evaluate set definitions on the fallback filter path`.

### Task 10: `sets_create`, `sets_refresh`, `sets_combine`, `sets_from_selection`

**Files:**
- Modify: `src/tapir_archicad_mcp/tools/custom/set_commands.py`, `src/tapir_archicad_mcp/auto_sets.py` (`summarize_set(..., extras: Mapping[str, Any] = {})` merges `stories`, `scope` and `notComparable` into the summary), `README.md` (an "Element sets" section)
- Test: `tests/unit/test_set_commands.py`

**Interfaces:**
- Consumes: Tasks 1, 3 and 9.
- Produces these commands, all kind `read`, each returning a set summary:
  - **`sets_create {definition: SetDefinition}`.** Origin is `{"definition": <json>}`. The summary adds `stories`, `scope` and `notComparable`.
  - **`sets_refresh {set: str}`.** It needs a definition origin; otherwise `ToolError`: "`<id>` was produced by `<command>`; run that command again for a fresh set." It returns a new set plus `{"added": n, "removed": m}`.
  - **`sets_combine {op: "union" | "intersect" | "subtract", sets: list[str] (min 2)}`.** All sets must be on the same port and project. Union keeps first-seen order; intersect and subtract keep the order of the first set. Origin is `{"combine": op, "sets": ids}`.
  - **`sets_from_selection {}`.** It uses Tapir `GetSelectedElements`. Origin is `{"command": "sets_from_selection"}`. The description mentions Tapir #695 (selection cleared on AC29).
- Every set command's description says sets are kept in memory and lost on restart, and names the known gaps (spec §5.4): untitled projects share one fingerprint; reopening the same file, reverting or a Teamwork receive keep the fingerprint while the content may differ — sets are snapshots, use `sets_refresh`.

- [ ] **Step 1: Write the failing tests:**
  - `test_create_returns_summary_with_stories_and_scope`.
  - `test_refresh_reports_added_and_removed`.
  - `test_refresh_of_a_command_set_is_explained`.
  - `test_combine_ops_keep_order`.
  - `test_combine_refuses_sets_from_two_projects`.
  - `test_from_selection`.
  - `test_set_command_kinds`: all four are `read`, `sets_export` is `write`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: create, refresh and combine sets from definitions and the selection`.

### After Task 10

- [ ] Run `uv run pytest` and the benchmark suite. Run `ruff check --select F,I` on the changed files.
- [ ] Run a live smoke on Archicad 29 with a server started from the branch over stdio, read-only. Check the port and project path via discovery first.
  - `sets_create` (Door) gives a summary with `byType`.
  - `sets_rows` with a fire-rating column found by `properties_search` shows statuses kept apart.
  - `sets_aggregate` by that column.
  - `elements_highlight_elements` with `{"set": ...}`.
  - Record the results in the PR.
- [ ] Stage 6 → review (pipeline per CLAUDE.md, Dave's OK), then PR.
