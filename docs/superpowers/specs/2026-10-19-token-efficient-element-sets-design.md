# Token-efficient element sets for tapir-archicad-MCP — design

**Date:** 2026-10-19
**Repo:** fork `DaviiiiiDave/tapir-archicad-MCP` of `SzamosiMate/tapir-archicad-MCP` (base: v0.6.5, Tapir 1.7.1, multiconn_archicad 0.9.x)
**Target:** Archicad 29
**Status:** draft for approval

## 1. Intent

Make the Tapir MCP server usable on large, real Archicad projects: an agent must be able to find, inspect, check and change thousands of elements without moving their data through the model's context, and without silent wrong results.

Today every element-level workflow pushes data through the model. "All walls plus property X" means paging GUIDs into context in pages of 100 (`pagination.py:9`), then having the model re-emit them as arguments. Output tokens are expensive and slow, and a dropped or mutated GUID produces a silent partial result that the model reports as done.

The maintainer (Máté Szamosi) has stated the same direction: handles instead of raw JSON, plus serializable filters, built on his multiconn results work. He has no time to build it and will only merge code he understands. This design therefore aims at **small, separately explainable changes** that can be offered upstream one at a time, built and measured in the fork first.

Archicad 30 ships a native MCP server with element sets (`successful_element_set_id` / `failed_element_set_id`), a `criteria_evaluator` and `property_search` (read from the tool definitions embedded in `Archicad 30/Automatisch aktualisierte Add-Ons/MCP Commands.apx`). It is **not** a target of this work. It is used as the reference design: where reasonable, this design uses the same concepts (set references, status operators, separate success/failure sets) so the two feel alike to an agent.

## 2. Goals, non-goals, anti-goals

**Goals**
- Element data stays in the server. The model handles set references, counts, summaries and small samples.
- Every number the server reports can be reconciled: totals, truncation, and per-element outcomes by cause are always explicit.
- Reads cannot be mistaken for writes by the MCP client (permission prompts become meaningful again).
- Bulk property writes are previewed, confirmed with a token, applied, and read back.
- Works with every Archicad language version and template. Nothing assumes German, English or a particular office standard.
- Proven by a benchmark, not asserted.

**Non-goals**
- Archicad 30 or its native MCP.
- Rewriting or hand-editing the generated command wrappers.
- A GUI, or binding a GUI to filter definitions.
- Code-Mode (model-written Python executed by the server) as a product feature. It appears only as a deferred, throwaway benchmark arm (§7).
- OR groups or nested boolean filter logic.

**Anti-goals** (failure even if it technically works)
- A result that looks complete but is not: missing elements, truncated lists or skipped values without the response saying so.
- "Missing value" findings that do not distinguish *undefined*, *not available for this element* and *default*.
- A write that can be applied twice by a retry.
- Changes so large or so clever that the maintainer cannot review them in one sitting.

## 3. Constraints

- Archicad 29 is the build and test target. Nothing may break the older Archicad versions Tapir supports.
- The four meta-tools (`archicad_list_commands`, `archicad_get_command_schema`, `archicad_call_tool`, `archicad_get_job`) and `discovery_list_active_archicads` keep their names and behaviour. New capabilities are registered as dispatchable commands through `register_tool_for_dispatch`, not as new top-level tools. The single exception is the read-only call tool in §5.1, which MCP annotations require.
- Generated code stays generated. New behaviour goes into the runtime (`tools/custom/`, new runtime modules) or the generator config, driven by **explicit tables**, never by inference from schema shapes (the maintainer's own precedent: `paginated_commands`, `commands_to_exclude` in `scripts/generator_config.py`).
- Code, docs, tool descriptions and errors are in English.
- Library-level logic that a GUI could also use (filter evaluation, value comparison) belongs in multiconn, the MCP layer consumes it. Until it is accepted there, it lives in the fork's MCP package behind the same interface.

## 4. Current state (measured or read from code)

- 308 dispatchable commands. `archicad_list_commands` returns all of them: about 47,000 characters (≈12k tokens by a chars/4 estimate). With prompt caching this is mostly a cache read after the first turn, so the real saving from a search is smaller than the raw number suggests.
- `archicad_call_tool` is annotated `readOnlyHint=False, destructiveHint=True, idempotentHint=False` for every command (`tools/custom/functions.py:227-232`). Clients prompt for every read, users click "allow always", and the prompt stops protecting writes.
- Validation errors are raised with the full pydantic message (`functions.py:242`), which includes input values. A rejected call with thousands of GUIDs echoes them back.
- Paginated results are snapshots held as job results (`jobs.py`, `pagination.py`), at most 128 finished jobs, 24 h TTL.
- The typed official property commands are excluded in favour of Tapir's, because Tapir's also work on sub-elements (`scripts/generator_config.py:112-113`). Tapir returns property values as display strings, the official API returns typed values in SI units with a status.
- Tapir's `FilterElements` filters only by 13 boolean flags. PR ENZYME-APD/tapir-archicad-automation#917 (open) adds layer, story and element type. A request for property-value criteria was posted to the Tapir Discord on 2026-10-19 (issue number pending).

## 5. Design

### 5.1 Read/write split

- Every dispatchable command appears in exactly one of two explicit lists in the generator config: `read_commands` or `write_commands`. Initial classification by name (`Get*`, `Is*`, `Find*`, `Search*` → read), then reviewed by hand and committed.
- A unit test fails if any registered command, generated or custom, is in neither list or in both. A Tapir bump that adds commands therefore fails until someone classifies them.
- New top-level tool `archicad_read_tool(name, arguments)`, annotated `readOnlyHint=True, destructiveHint=False`. It rejects write commands with an error that names `archicad_call_tool`.
- `archicad_call_tool` keeps accepting every command (compatibility) and keeps its destructive annotations. Its description directs reads to `archicad_read_tool`.
- `archicad_list_commands` output gains `kind: "read" | "write"` per command.

### 5.2 Catalog search

- `archicad_list_commands` gains optional `query` (and optional `group`). With a query it returns only matching commands (case-insensitive token match over name, title and description). Without arguments it behaves as today.
- Each catalog entry also says whether the command accepts set references (§5.4).

### 5.3 Response envelope and error hygiene

- Every response that contains a list the server truncated, paged or replaced by a set carries `total`, `returned`, `truncated: bool` for that list.
- Every write response that contains per-element execution results carries `requested`, `succeeded`, `failed`, plus failures grouped by cause (the error code/message), with at most a few examples per cause. The builder confirms the exact result field names in the Tapir and official schemas.
- Validation errors no longer include input values. They name the failing field path and the expected shape, with one example.

### 5.4 Element sets

**What a set is.** A server-side, immutable snapshot of element GUIDs:
- `id`: `set:<short random hex>`.
- Bound to a `port` and a **project fingerprint** (Solo path, or Teamwork server + path). The connection header is a cached snapshot (it is only refreshed by discovery, `functions.py:66-67`), so every set use re-reads the project identity from Archicad before expanding or reading the set. Using a set on another port, or after the project on that port changed, is an error that says so. Save-as therefore invalidates sets. That is intended: fail loud.
- Known gaps, documented in the tool descriptions rather than hidden:
  - Untitled projects all share one fingerprint, so sets on untitled projects carry a warning.
  - Reopening the same file, reverting, or a Teamwork receive keeps the fingerprint while the content may differ. Snapshot semantics apply (§5.6 `sets_refresh`), and writes report missing elements through the read-back (§5.7).
- All set commands take `port` like every dispatchable command (the arguments envelope requires it, `tool_registry.py:34-44`). It must match the set's port.
- `created_at`, ordered and de-duplicated GUIDs, and `origin`: either the producing command plus a hash of its parameters, or the set definition (§5.6).
- Stored in a dedicated in-memory `SetStore` with an LRU cap, separate from the job store. Sets are data, jobs are executions. Fast reads must not evict a set the agent is still working with. Sets are lost on server restart. The "unknown set" error says this and tells the model to recreate the set from its query.

**How sets are created**
1. **Automatically from results.** When a command result contains an element-id list (listed in an explicit table, like `paginated_commands`), the server stores it as a set. Up to a small inline threshold (20 elements, a tunable constant) the list is returned as today, plus the set id. Above it, the list is replaced by a set summary. A server flag (`--element-sets on|off`) turns this replacement off for anyone depending on raw lists. Default: on in the fork, upstream decides its own default.
2. `sets_create(port, definition)` — from a set definition (§5.6).
3. `sets_from_selection(port)` — from the current Archicad selection.
4. `sets_combine(op, sets)` — `union`, `intersect`, `subtract`. This replaces OR groups in filters.
5. `sets_refresh(set)` — re-evaluates a set definition and returns a new set, plus counts of elements added and removed relative to the old one.

**How sets are used.** In every command whose element-list parameter is listed in an explicit expansion table, the parameter accepts `{"set": "set:ab12"}` in place of the array.
- Expansion happens on the raw arguments **before** pydantic validation (`functions.py:239`).
- `archicad_get_command_schema` returns the patched schema (`anyOf` original array or set reference) with an example, so models that follow the schema see the option.
- Only plain element-id arrays are expandable. Commands taking pairs (element + value, element + design option, …) are not in the table. Bulk writes go through the set commands in §5.7.
- A snapshot test lists, per command, the parameters that accept sets. A Tapir or JSON API bump that changes them shows up as a diff in review.
- Highlighting or selecting a set in Archicad needs no new command: `elements_highlight_elements` / `elements_change_selection_of_elements` with a set reference. Known risk: Tapir issue #695 reports the selection being cleared on AC29.

**Set summary** (returned whenever a set is created):
```json
{
  "set": "set:ab12",
  "count": 4312,
  "byType": {"Wall": 4100, "Column": 212},
  "createdAt": "…",
  "next": ["sets_rows(set='set:ab12', columns=[...])", "sets_aggregate(set='set:ab12', groupBy=...)"]
}
```
`byType` comes from the official `GetTypesOfElements`. `next` gives concrete call templates, because models use a summary and ignore drill-down tools unless the response shows them how.

### 5.5 Reading values: columns, rows, aggregates, export

**Columns.** A column is a property (by property GUID) or one of a few element fields:
- `type`, from the official `GetTypesOfElements`.
- `layer` (layer attribute GUID plus its name as shown in the project) and `story` (story index plus its name), from Tapir `GetDetailsOfElements` with a field mask (`layerIndex`, `floorIndex`), resolved through the attribute and story lists.
- `isEditable`, derived as membership in the result of `FilterElements` with the `IsEditable` flag. `GetDetailsOfElements` has no such field.

**Property identity and search.** `properties_search(port, query, alternativeQueries?, group?)` does a fuzzy search over the open project's property definitions: names as they appear in that project, in its language, plus built-in non-localized names. It returns property GUID, group, name, value type, measure type, and for built-ins the `nonLocalizedName`. Names come from Tapir `GetAllProperties`. `nonLocalizedName` comes from the official `GetDetailsOfProperties`, joined by GUID. Everything downstream uses GUIDs only. The model never sends a localized name to a read or write.

**Value cells.** Property values are read **typed** through the official `GetPropertyValuesOfElements` (via multiconn, not via the excluded generated wrapper):
- A normal value: the value, with numbers in SI base units, and the unit named once per column (`m`, `m²`, `m³`, `rad`).
- Otherwise a status, one of `userUndefined`, `notAvailable`, `notEvaluated` (the statuses the official API reports). These are never collapsed into "empty". The official API does not report whether a value is the definition's default, so cells carry no default flag.
- Elements the official command cannot read (sub-elements such as curtain-wall panels or stair parts) fall back to Tapir's string read. Those cells are marked `typed: false`. They are never silently dropped. The builder defines and tests how filters compare against fallback values (§5.6).

**Commands**
- `sets_rows(set, columns, where?, orderBy?, offset?, limit?)` returns bounded rows (default 20, hard maximum 100) plus the envelope.
- `sets_aggregate(set, groupBy, metrics?)` returns, per group value, the count (and optional sum/min/max of a numeric column). It shows the top groups plus an "others" bucket and a cardinality cap. Status buckets (`userUndefined`, `notAvailable`, …) are listed first, because they are what a practitioner asks about.
- `sets_export(set, columns, format="csv")` writes the full table to a file in a server-side export directory (`--export-dir`, default under the user's documents) and returns the path and row count. A practitioner's deliverable is a file, and 3,000 rows relayed through the model are 3,000 chances for a wrong row.

### 5.6 Set definitions and filtering

**Definition** (plain JSON, serializable, stored as the set's origin):
```json
{
  "source": {"types": ["Door"], "flags": ["IsEditable"]},
  "layers": [{"guid": "…"}], "stories": [0, 1],
  "where": [
    {"property": {"guid": "…"}, "operator": "IsUserUndefined"},
    {"property": {"guid": "…"}, "operator": "GreaterOrEqual", "value": 0.3}
  ]
}
```
- This section is the normative schema. The Tapir request mirrors it, not the other way round.
- All criteria must match (AND). OR comes from `sets_combine`.
- **Layers are identified by layer attribute GUID**: stable when the layer list is edited, independent of language. They are resolved to indices at evaluation time. **Stories are identified by story index**, because Archicad stories have no GUID. Inserting a story below shifts indices. This is a documented limit, and the set summary names each story index together with its current name.
- Operators: `Equal`, `NotEqual`, `Less`, `LessOrEqual`, `Greater`, `GreaterOrEqual`, `Contains` (comparisons, need `value`); `HasValue`, `IsUserUndefined`, `IsNotAvailable` (status, no `value`); `IsDefault` (status, see below).
- Comparisons work on typed values in SI units. Only a normal value can match a comparison. **An undefined, not-available or not-evaluated value never matches, including `NotEqual`.**
- Enumeration `value` is the display value of the item as it appears in the project, matched exactly. `Contains` is a case-insensitive substring match on strings. On list and multi-enumeration values it means "one item equals `value`".
- `IsDefault` is only available on the in-process path, because only Archicad's own API reports the default state. The fallback path rejects it with an error that says so. It does not approximate it by comparing against the definition's default value, since a user can type the same value by hand.
- Flag filters that depend on the current view state (`IsVisibleByRenovation`, `OnActualFloor`, `InCroppedView`, …) are applied only when the definition lists them. They are stored in the definition, so `sets_refresh` re-evaluates under the same explicit filters. The set summary warns when such a flag was used, because the result then depends on what is open on screen.

**Execution, two paths with identical semantics**
1. **In-process (preferred, step 9, deferred until Tapir ships the feature):** if the installed Tapir version supports property criteria and layer/story/type on `FilterElements`, the candidates from `source` are passed to one `FilterElements` call. `FilterElements` tests a given element list and does not search the model (`multiconn models/tapir/commands.py:1271-1274`). Candidate GUIDs therefore still travel between server and Archicad, but never through the model, and property values never leave Archicad. The version gate uses `tapir_info.version`. The version number is set once the Tapir feature is released. Until then this path is off.
2. **Fallback (always available):** candidates via Tapir `GetElementsByType` / `GetAllElements` (with `databases` and flags), then layer/story via `GetDetailsOfElements` with a field mask, then property values via the typed read, compared in Python. Cost: one bulk read per criterion over the remaining candidates. Cheap criteria are evaluated first.

The comparison and status semantics live in one module with one test suite. An equivalence test runs both paths on the same fixture and requires identical results when the in-process path is available.

**Scope.** Definitions may name `databases`, the plan database is the default. Sub-elements are included only when the definition sets `IncludeSubElemObjects`. Both are stated in the set summary.

### 5.7 Bulk property writes on sets

`sets_set_property_values(set, assignments, planId?)`, where `assignments` is a list of `{property: {guid}, value}`.

**Without `planId`: preview.** Nothing is written. The preview returns:
- `planId` (single use, bound to the set id, a hash of the GUID list and a hash of the assignments).
- How many elements would change, how many already hold the target value, and a sample diff old → new (up to 10 elements).
- A failure forecast from read-only checks: not editable (`IsEditable` / `HasAccessRight` flags), split by cause where the API allows it. Hotlinked elements have a `hotlinkId` (`GetDetailsOfElements`). In Teamwork projects, elements outside the user's workspace fail the `InMyWorkspace` flag. Locked layers are known from the layer attributes. Anything else not editable falls into "other". Elements where the property is not available form their own group. The builder verifies that these flags separate the causes as described.

**Teamwork: the command never reserves elements, neither in preview nor in apply.** Elements not reserved by the current user are reported as their own failure group (with a set), and the user reserves them in Archicad. An opt-in `reserve` switch may come later; it is not part of v1.

**With `planId`: apply.**
- The server re-checks that the set and assignments still match the plan, and refuses otherwise.
- It writes with the typed official `SetPropertyValuesOfElements`. For elements only Tapir can write (sub-elements), it formats SI values into Tapir's string form. The builder verifies how Tapir parses such strings, including locale decimal separators and units, before enabling this branch.
- Then it **reads back** every written value and reports `requested`, `applied`, `unchanged`, `failed`. Failures are grouped by cause, and there are sets for `applied` and `failed`.
- Applying a used `planId` again returns the stored result of the first apply and does not write again.

Generic write commands stay available through `archicad_call_tool` without preview. Their descriptions point bulk property writes to this command.

### 5.8 What stays out of v1, deliberately

- Persisting sets or definitions to disk.
- Change notifications (Tapir `SetElementNotificationClient`) to mark sets stale.
- Chunking or per-port serialization of large calls. The job store runs up to 32 workers (`jobs.py:24`). Whether parallel calls into one Archicad stall its UI is measured in the benchmark, not assumed.
- Classification criteria in definitions (Tapir #1098 / #1101 pending).
- New typed top-level tools for hot paths. They become an option only if the benchmark shows the `get_command_schema` round trip dominating.

## 6. Layer ownership and upstream PR sequence

Each step is a self-contained PR to the fork's `master`. After it is measured, it is offered upstream as a separate PR with the benchmark numbers in its description. Small, medium and large are relative sizes.

| # | Change | Layer | Size | Upstream |
|---|---|---|---|---|
| 1 | Read/write classification table, `archicad_read_tool`, `kind` in catalog | MCP runtime + generator config | small | yes |
| 2 | Validation errors without input echo | MCP runtime | small | yes |
| 3 | `query` / `group` on `archicad_list_commands` | MCP runtime | small | yes |
| 4 | Response envelope (`total`/`returned`/`truncated`, write outcome counts) | MCP runtime | small | yes |
| 5 | `SetStore`, auto-sets from results, expansion table + snapshot test, set summary | MCP runtime | medium | yes |
| 6 | Value cells, comparison/status semantics module | multiconn (fork MCP until accepted) | medium | yes, to multiconn |
| 7 | `properties_search`, `sets_rows`, `sets_aggregate`, `sets_export` | MCP `tools/custom/` | medium | yes |
| 8 | Set definitions, `sets_create` / `sets_refresh` / `sets_combine` / `sets_from_selection`, fallback filter path | multiconn evaluator + MCP commands | medium | yes |
| 9 | In-process filter path gated on Tapir version | MCP runtime | small | yes, once Tapir ships it |
| 10 | Two-phase `sets_set_property_values` with read-back | MCP `tools/custom/` | medium | offered, fork-only if declined |
| — | Benchmark harness, Code-Mode arm | fork only | medium | no |

## 7. Benchmark and evaluation (fork only)

**Driver.** A scripted agent loop (Claude Agent SDK or Messages API tool runner) connects to the server over streamable HTTP, as the repo's integration tests do. Per run it logs input, output, cache-read and cache-write tokens, tool-result bytes, call count, wall time and errors by class.

**Scope (slim first round).** Three arms × two models × five tasks × three runs = 90 agent runs. A full round (more tasks, five runs, Code-Mode arm) follows only if the result is borderline.

**Arms**
- A: upstream v0.6.5.
- B: A + steps 1–4.
- C: B + sets (steps 5–8, plus 10 for write tasks).
- A throwaway Code-Mode arm (the model writing Python against multiconn, fixture copies only, never shipped) is deferred to the full round.

**Models.** One Haiku-class and one Sonnet-class model, N=3 runs per task, model and arm, at a fixed temperature.

**Fixtures.** Only a German Archicad 29 is available, so language neutrality is tested through project content, not through the application language:
- **German fixture:** a German AC29 project with a German template.
- **International-template fixture:** Tapir's own `archicad-addon/Test/TestProject.pla` (ENZYME-APD/tapir-archicad-automation). Its recorded test outputs show English property groups and classifications, so it comes from an international template. Opened in German AC29, its user-defined properties and classification items keep their English names while built-in names are shown in German. That mix is exactly the case where an agent that assumes German names fails.
- **Scale fixture:** a copy of the German fixture with about 5,000 walls added by script.
- Documented limit: built-in property display names are only ever seen in German.
- Every run starts from a fresh copy of the saved project. End states are verified out-of-band through multiconn directly, never through the server under test.

**Tasks** (each needs an oracle, the project's own interactive schedule or a direct multiconn query):
1. Doors without a fire rating on the scale fixture, including curtain-wall sub-elements, with *undefined* and *not available* reported separately. Output CSV.
2. Assign a property value to about 3,000 walls by rule on the scale fixture: preview, apply, read back. Target: zero wrong writes.
3. The check from task 1 on the international-template fixture, with property and group names as they appear in that project.
4. Bulk write where some elements are on a locked layer or hotlinked. Failures must be bucketed by cause, with no silent partial result.
5. Recovery: the response to a write is dropped mid-task (fault-injecting proxy). The model must recover without a double write.

**Metrics.** Success by end-state diff, silent-error count (reported success with a wrong end state), tokens (input/output, cached and uncached, cost-weighted), calls, error classes, peak context. Context overflow counts as failure. Double-applied or out-of-scope writes are a hard fail.

**"Drastically better" is fixed before the first run.** On the 5,000-element tasks, arm C versus A must achieve:
- median total tokens at least 10× lower,
- median calls at least 5× lower,
- success not lower (paired bootstrap CI),
- zero unsafe writes.

On small tasks, no more than a 10 % token regression. With N=3 the confidence interval is wide. A borderline result therefore triggers the full round rather than a claim.

## 8. Risks

- **The Tapir property-filter feature** may not land, may land differently, or may take a while. The fallback path is a full design, not a stub, so nothing depends on it.
- **View-state dependence:** the active design option combination, renovation filter and database can change what queries return. These are tested explicitly in the fixtures, and any dependence found is reported in set summaries.
- **The fallback filter path is slow on very large models** (bulk reads on Archicad's main thread). This is measured. The in-process path is the remedy.
- **Upstream may decline** steps 5–10. The fork stays usable on its own, with each change kept small so it can still be rebased.

## 9. Left to the builder

- Exact result field names for element lists and execution results per command, i.e. the contents of the expansion and auto-set tables.
- `SetStore` LRU size, and the inline threshold if 20 proves wrong in the benchmark.
- How filters compare against Tapir string fallback values for sub-elements, defined and covered by tests.
- Fuzzy-matching algorithm for `properties_search`.
- Export directory default per OS.
- Fault-injection proxy and benchmark report format.
