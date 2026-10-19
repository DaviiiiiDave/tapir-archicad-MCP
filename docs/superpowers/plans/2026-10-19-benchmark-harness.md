---
stages:
  3: "Harness core: stream metrics, proxy, arms, driver, pre-flight (Task 1-3)"
  4: "Fixtures: Archicad access, oracle, seeding (Task 4-5)"
  5: "Tasks, scoring, runner, report, pilot (Task 6-7)"
  6: "Baseline round, arms A and B (Task 8)"
current: 1
---

# Benchmark Harness and Baseline Implementation Plan (Plan 2 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the fork-only benchmark of spec §7 and measure the baseline: arms A (upstream v0.6.5) and B (Plan 1) × two models × five tasks × three runs = 60 runs. Arm C joins the same harness after Plans 3 and 4.

**Architecture:** A Python package `benchmark/bench/` drives Claude Code headless (`claude -p`, stream-json) against each arm's MCP server over streamable HTTP. Between Claude Code and the server sits a recording proxy. It logs every call, blocks calls that could touch anything outside the run copy, drops one write response for task 5, and takes out-of-band snapshots around writes. Fixtures are seeded by script on the dedicated Archicad instance "Benchmark". Every run opens a fresh copy, and the oracle reads states through multiconn directly, never through the server under test.

**Tech Stack:** Python ≥3.12, `multiconn-archicad` 0.9.4, `mcp` 2.x (streamable HTTP), httpx, uvicorn, starlette, anyio, pytest + pytest-asyncio, uv, Claude Code CLI 2.1.81.

**Spec:** `docs/superpowers/specs/2026-10-19-token-efficient-element-sets-design.md` §7 (§5.1 for command kinds). It lives on branch `feature/spec-token-efficiency`. Read it with `git show "feature/spec-token-efficiency:docs/superpowers/specs/2026-10-19-token-efficient-element-sets-design.md"`. If the plan and the spec disagree, the spec wins. Stop and ask.

## Measured before writing this plan (2026-10-19)

- Benchmark instance: port 19725, `Benchmark.pln`, Archicad 29, Tapir 1.7.2. German office template, empty floor plan, stories UG (-1), EG (0), OG (1), 77 layers, 1,829 properties.
- Fire-rating properties. German template: custom `Allgemeine Werte › Feuerwiderstandsklasse 13501-2` (String, Single) and `… 4102-2` (enumeration, availability by classification), plus built-in `Fenster/Tür › Brandschutz`. TestProject.pla (recorded outputs, commit ce1623e of ENZYME-APD/tapir-archicad-automation): custom `GENERAL RATINGS › Fire Resistance Rating`.
- Tapir offers CreateWalls, CreateDoors, CreateLayers (`isLocked`), SetDetailsOfElements (`layerIndex`), LockElements, SaveAsModuleFile, CreateHotlinkNodes, CreateHotlinkInstances, OpenProject and SaveProject. Door details carry `ownerElementId`. **No command creates curtain walls.**
- The official `SetPropertyValuesOfElements` accepts `status: "userUndefined"` (multiconn `models/official/types.py`).
- Claude Code headless with `--setting-sources project` from an empty cwd runs no hooks and loads no plugins. It **still loads the user `~/.claude/CLAUDE.md`**: a canary quoted its headings, and its thinking adopted the persona. A trivial prompt cost 5,843 input tokens. With `CLAUDE_CONFIG_DIR` pointing at an empty directory the CLI answers "Not logged in". A separate config directory with its own login therefore isolates the runs.
- stream-json emits the same assistant message once per content block, each time with identical `usage`. `rate_limit_event.rate_limit_info.status` was `"allowed"`. `result` carries `subtype`, `is_error`, `num_turns`, `total_cost_usd`, `usage`, and `modelUsage[<model>].contextWindow` (200000 for Haiku). Thinking is on by default.
- Upstream tag `v0.6.5` (commit e04fc0e) already supports `--transport streamable-http`.

## Decisions that need Dave's OK before execution

1. **Temperature.** Claude Code cannot set it. Every run uses the CLI default sampling and default thinking, identical for all arms and models. This reads spec §7's "fixed temperature" as "the same for every run".
2. **Arm C is not part of this plan.** This round reports B against A. The §7 thresholds (C against A) are computed once Plans 3 and 4 add arm C, and the report marks them as pending until then.
3. **Manual fixture steps (Dave):**
   - a one-time `/login` in the benchmark config directory (before Task 3's live step);
   - one curtain wall with at least 8 door panels on story EG in `Benchmark.pln`, saved (before Task 4);
   - possibly opening TestProject.pla once and saving it as `.pln`, if OpenProject cannot open a `.pla` without a dialog (Task 5 measures this).
4. **Prompts name the port** (19725), and the proxy blocks calls to any other port. Discovery also shows Dave's working instances, and a write there must be impossible. The rule is the same for all arms.
5. **Models are pinned by full ID:** `claude-haiku-5-5` and `claude-sonnet-5-5`. On this CLI the alias `haiku` resolved to `claude-haiku-4-5-20251001`, so aliases are not used. Pre-flight checks that the init event reports exactly these IDs.
6. **Kill switches per run** (parameters, not expectations): `--max-budget-usd` 2.00 for Haiku and 6.00 for Sonnet, plus a run timeout of 1800 s. A run that hits either one counts as failed.

## Global Constraints

- Fork only. `benchmark/` and everything under `docs/superpowers/` never go into an upstream PR branch.
- Archicad 29, and only the Benchmark instance on port 19725. Every harness call to Archicad goes through `bench.archicad.BenchArchicad`. It refuses any other port, and it refuses to open or save any file outside the data dir.
- Every run starts from a fresh copy of a seeded fixture. The harness never opens `Benchmark.pln` itself, only copies of it.
- States, write probes and oracles are read through multiconn directly. Property values always come from the official API as typed values with a status, never from the server under test and never from Tapir display strings (German AC29 returns "0,075").
- Every arm and model gets the same system prompt and the same task prompts. Models are pinned by full ID.
- Claude Code runs only with all of the following:
  - env `CLAUDE_CONFIG_DIR=<data_dir>/claude-config`; the run dir (empty except harness files) as cwd;
  - `--setting-sources project --strict-mcp-config --mcp-config <file> --tools "" --allowedTools mcp__archicad --permission-mode dontAsk`;
  - `--system-prompt <SYSTEM_PROMPT> --model <full id> --no-session-persistence --output-format stream-json --verbose --max-budget-usd <cap>`;
  - the prompt on stdin.
- Child processes are stopped only through their own PID: `taskkill /PID <pid> /T /F` on Windows, `os.killpg` elsewhere. Never by image name.
- Bulk data (project copies, arm environments, streams, ledgers) lives in the data dir `%LOCALAPPDATA%\tapir-mcp-bench`, never in OneDrive. Machine paths go in the git-ignored `benchmark/local.toml`; `benchmark/local.example.toml` is committed.
- No new dependencies. httpx, uvicorn, starlette, anyio, mcp and multiconn are already in the env.
- uv runs with `UV_PROJECT_ENVIRONMENT="$LOCALAPPDATA/uv-envs/tapir-archicad-mcp"` and never creates `.venv` in the repo. Benchmark tests: `uv run pytest benchmark/tests -q`. The main suite `uv run pytest tests/unit tests/integration -q` stays green.
- Steps that start Claude Code cost Dave's subscription. Pre-flight (Task 3), pilot (Task 7) and round (Task 8) run only inside a stage Dave has approved.
- Code, docs and prompts are in English. Commits use `feat:` / `test:` / `docs:` and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Repeated assistant events.** stream-json repeats one message per content block with the same `usage` (seen live). Counting each event would inflate calls, tokens and peak context. → Task 1 test `test_repeated_assistant_events_count_once`.
2. **Usage limit mid-round.** The subscription can stop the round. That is an infrastructure stop, not a model failure. The round must halt, record the reset time, and on resume skip finished runs and rerun the stopped one. → Task 7 test `test_round_stops_on_rate_limit_and_resumes`.
3. **The model picks another Archicad instance.** Discovery lists Dave's working projects on 19723 and 19724. Any call carrying another port must not reach Archicad and must show up in the ledger. → Task 2 test `test_call_to_other_port_is_blocked_and_logged`.
4. **SSE framing.** Streamable HTTP may answer a POST as `text/event-stream` instead of JSON. Pass-through and the dropped-response rewrite must keep the framing the upstream used. → Task 2 tests `test_sse_response_passes_through` and `test_dropped_response_keeps_sse_framing`.
5. **Locale display strings.** An oracle that reads Tapir strings would score German "0,075" against 0.075 and fail correct runs. The oracle normalises official typed values only. → Task 4 test `test_normalize_typed_values_and_statuses`.

## File structure

```
benchmark/
  pytest.ini             rootdir of the benchmark tests, pythonpath = .
  bench.toml             committed: port, arms, models, budgets, timeout, fixture specs, oracle facts
  local.example.toml     committed template of local.toml (machine paths)
  cli.py                 subcommands: setup-arms, preflight, seed, run, report
  bench/__init__.py
  bench/config.py        load and validate bench.toml + local.toml
  bench/stream_metrics.py  parse a stream-json run into RunMetrics
  bench/proxy.py         recording, guarding, fault-injecting MCP proxy
  bench/arms.py          export a git ref, build its env, start its server
  bench/driver.py        build and run the claude command, kill by PID
  bench/preflight.py     isolation checks and canaries
  bench/archicad.py      guarded multiconn access to the Benchmark instance
  bench/oracle.py        typed property snapshots, element lists, owners
  bench/seed.py          fixture seeding and manifests
  bench/tasks.py         system prompt, five tasks, answer parsing, scorers
  bench/runner.py        run matrix, one run end to end, resumable round
  bench/report.py        aggregation, bootstrap CI, Markdown report
  tests/                 one test module per bench module, plus tests/streams.py helpers
  results/               committed reports (Markdown + summary JSON), nothing else
```

`.gitignore` gains `benchmark/local.toml`. Run `python benchmark/cli.py …` from the worktree root. Python puts `benchmark/` on `sys.path`, so `import bench` works without packaging.

---

### Task 1: Skeleton, config, stream metrics

**Files:**
- Create: `benchmark/pytest.ini`, `benchmark/bench.toml`, `benchmark/local.example.toml`, `benchmark/bench/__init__.py`, `benchmark/bench/config.py`, `benchmark/bench/stream_metrics.py`
- Create: `benchmark/tests/streams.py` (helpers that build synthetic stream-json lines), `benchmark/tests/test_config.py`, `benchmark/tests/test_stream_metrics.py`
- Modify: `.gitignore` (+ `benchmark/local.toml`)

**Interfaces:**
- Produces: `load_config(bench_toml: Path, local_toml: Path) -> BenchConfig`. Frozen dataclass fields:
  - from `local.toml`: `data_dir: Path`, `claude_exe: Path`, `base_project: Path`, `international_source: Path`, `repo_root: Path`;
  - from `bench.toml`: `bench_port: int`, `arms: dict[str, ArmConfig]`, `models: dict[str, str]` (alias → full id), `budgets_usd: dict[str, float]`, `run_timeout_s: int`, `runs_per_cell: int`, `fixtures: dict`, `oracle: dict`.
  - `ArmConfig(name: str, ref: str, change_log_flag: bool)`. Paths go through `os.path.expandvars`.
- Produces: `parse_stream(lines: Iterable[str]) -> RunMetrics`. Frozen dataclass fields:
  - tokens and cost: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `total_tokens` (property, sum of the four), `cost_usd`;
  - run shape: `turns`, `tool_calls: list[ToolCall]`, `tool_result_bytes`, `peak_context_tokens`, `context_window`;
  - end state: `result_subtype: str | None`, `is_error: bool`, `result_text: str`, `complete: bool` (a result event was seen);
  - environment: `rate_limit_status: str | None`, `hook_events: list[str]`, `init: dict`.
  - `ToolCall(tool_use_id, tool, inner_command: str | None, port: int | None, is_error: bool, result_bytes: int, error_class: str | None)`.
- Produces: `classify_tool_error(text: str) -> str`, one of `"validation"`, `"refused"`, `"archicad"`, `"transport"`, `"blocked"`, `"other"`.

`bench.toml` starts with these values (fixture and oracle sections are added in Tasks 4 and 5):

```toml
bench_port = 19725
runs_per_cell = 3
run_timeout_s = 1800

[models]
haiku = "claude-haiku-5-5"
sonnet = "claude-sonnet-5-5"

[budgets_usd]
haiku = 2.0
sonnet = 6.0

[arms.A]
ref = "v0.6.5"
change_log_flag = false

[arms.B]
ref = "d7a8a67"
change_log_flag = true
```

- [ ] **Step 1: Write the failing tests.**
  - `test_config.py`:
    - `test_paths_expand_environment_variables`: `%LOCALAPPDATA%` and `$HOME` style both expand.
    - `test_budget_for_unknown_model_alias_fails`: `ValueError` naming the alias.
    - `test_missing_local_toml_names_the_file`: `FileNotFoundError` whose message contains `local.example.toml`.
  - `test_stream_metrics.py`, with synthetic events from `tests/streams.py`:
    - `test_tokens_and_cost_come_from_the_result_event`: input 10, cache_creation 5833, cache_read 0, output 156 → `total_tokens == 5999`, `cost_usd == 0.00808125`.
    - `test_repeated_assistant_events_count_once`: the same `message.id` twice, each with the same `tool_use` block → `len(tool_calls) == 1`, and the peak context is counted once.
    - `test_peak_context_is_the_largest_single_request`: messages whose input + cache_read + cache_creation are 1000, 5000 and 3000 → 5000.
    - `test_inner_command_and_port_come_from_meta_tool_arguments`: tool `mcp__archicad__archicad_call_tool` with input `{"name": "elements_get_all_elements", "arguments": {"port": 19725, "params": {}}}` → inner command `elements_get_all_elements`, port 19725. The same holds for `archicad_read_tool`.
    - `test_tool_result_bytes_are_utf8_lengths`: `"ä"` counts as 2 bytes.
    - `test_hook_events_are_collected`: `hook_started` events → `hook_events == ["SessionStart:startup"]`.
    - `test_rate_limit_status_is_kept`.
    - `test_missing_result_event_marks_run_incomplete`: the stream stops early → `complete is False`, `result_subtype is None`.
    - `test_classify_tool_error` (parametrised):
      - "Invalid arguments provided for tool …" → validation
      - "read-only" or "Ask the user to confirm" → refused
      - "Error code: 7203" or "APIERR" → archicad
      - "connection" or "timed out" → transport
      - "[bench-proxy] blocked" → blocked
      - anything else → other
- [ ] **Step 2: Run, expect FAIL.** `uv run pytest benchmark/tests -q` → errors importing `bench.config` and `bench.stream_metrics`.
- [ ] **Step 3: Implement `bench/config.py` and `bench/stream_metrics.py`.** Use `tomllib`. Deduplicate assistant messages by `message.id`. Count `tool_use` blocks by `id`. Match each `tool_result` (in `user` events) to its call by `tool_use_id`. Take the peak context per unique message from its `usage`. Take `context_window` from `modelUsage` of the result event.
- [ ] **Step 4: Run, expect PASS.** `uv run pytest benchmark/tests -q` → all pass. `uv run pytest tests/unit tests/integration -q` → still green.
- [ ] **Step 5: Commit.** `feat: benchmark config and stream-json metrics`

### Task 2: Recording, guarding, fault-injecting proxy

**Files:**
- Create: `benchmark/bench/proxy.py`, `benchmark/tests/test_proxy.py`

**Interfaces:**
- Produces:
  - `ProxyRules(bench_port: int, kinds: Mapping[str, str], drop_first_response_of: str | None = None)`;
  - `kind_of(rules, inner_command: str | None) -> str`, which returns `"write"` for names missing from `kinds` (fail safe) and `"read"` for `None` (discovery, get_job);
  - `blocked_reason(rules, inner_command, kind, port) -> str | None`.
- Produces: `create_proxy_app(upstream_url: str, rules: ProxyRules, ledger_path: Path, probe: Callable[[str, dict], dict] | None = None, client: httpx.AsyncClient | None = None) -> Starlette`. Tests inject `client` with `httpx.ASGITransport` around a fake upstream app.
- Produces: `serve_in_thread(app, port: int) -> ProxyServer`, with `.stop()`, run by uvicorn on its own loop in a daemon thread.
- Ledger: one JSON object per line, with fields:
  - identity: `seq`, `time`, `tool`, `inner_command`, `kind`, `port`, `arguments`;
  - outcome: `blocked: str | None`, `dropped: bool`, `upstream_ms`, `is_error`;
  - probes: `probe_before`, `probe_after`, `probe_ms`.

Rules:
- **Blocked**, i.e. never forwarded:
  - any `tools/call` whose arguments carry a `port` other than `bench_port`;
  - any command with kind `irreversible` whose group (the name up to its first `_`) is `project`, `application`, `teamwork` or `ifc`.

  The reply keeps the request `id`: a JSON-RPC result `{"content": [{"type": "text", "text": "[bench-proxy] blocked: <reason>"}], "isError": true}`, sent as `application/json`. Element and property deletes are not blocked; probes and the oracle judge them.
- **Probes.**
  - Unblocked calls of kind `write` or `irreversible`: `probe("before", entry)` before forwarding and `probe("after", entry)` after the full upstream response.
  - `archicad_get_job` calls: `probe("after", entry)` only.
  - Probes run via `anyio.to_thread.run_sync`, and the response waits for them. The time goes into `probe_ms` so the report can subtract it from wall time.
- **Drop.** The first call whose inner command equals `drop_first_response_of` is forwarded and fully answered upstream, and probed after. The client then gets the same JSON-RPC envelope and framing with `result` replaced by `{"content": [{"type": "text", "text": "Error: the connection to the server was lost before the response arrived. The outcome is unknown."}], "isError": true}`. Later matching calls pass untouched.
- Everything else passes through byte for byte: all methods and paths, status, and headers in both directions (including `mcp-session-id`), with SSE streamed.

- [ ] **Step 1: Write the failing tests** in `test_proxy.py`. The fake upstream counts the requests it receives and answers JSON or SSE on demand.
  - `test_calls_are_recorded_with_inner_command_kind_and_port`
  - `test_call_to_other_port_is_blocked_and_logged`: port 19723 → upstream count 0, client result `isError` with text starting `[bench-proxy] blocked`, ledger `blocked` set.
  - `test_project_and_application_irreversible_commands_are_blocked`: `project_open_project` is blocked; `elements_delete_elements` (irreversible) is forwarded.
  - `test_unknown_command_counts_as_write`
  - `test_write_calls_are_probed_before_and_after`: probe phases `["before", "after"]` for a write, `["after"]` for `archicad_get_job`, none for a read.
  - `test_sse_response_passes_through`: identical bytes and content type.
  - `test_dropped_response_keeps_json_framing` and `test_dropped_response_keeps_sse_framing`: upstream count 1, client text contains "outcome is unknown", same `id`, and the second matching call passes untouched.
  - `test_session_header_is_forwarded`: `mcp-session-id` both ways.
- [ ] **Step 2: Run, expect FAIL** (import error).
- [ ] **Step 3: Implement `bench/proxy.py`** with the signatures above. JSON-RPC batches (arrays) are handled element by element.
- [ ] **Step 4: Run, expect PASS** for both suites.
- [ ] **Step 5: Commit.** `feat: recording and fault-injecting benchmark proxy`

### Task 3: Arms, driver, pre-flight

**Files:**
- Create: `benchmark/bench/arms.py`, `benchmark/bench/driver.py`, `benchmark/bench/preflight.py`, `benchmark/cli.py` (`setup-arms`, `preflight`)
- Create: `benchmark/tests/test_arms.py`, `benchmark/tests/test_driver.py`, `benchmark/tests/test_preflight.py`

**Interfaces:**
- Consumes: `BenchConfig`, `ArmConfig`, `parse_stream`, `RunMetrics`, `create_proxy_app`, `serve_in_thread`, `ProxyRules`.
- Produces (`arms.py`):
  - `prepare_arm(cfg, name) -> Path`:
    - exports `git archive <ref>` from `cfg.repo_root` into a fresh `data_dir/arms/<name>/src`;
    - builds `data_dir/arms/<name>/env` with `uv venv` and `uv pip install --python <env python> <src>`;
    - writes the resolved full SHA (`git rev-parse <ref>^{commit}`) to `data_dir/arms/<name>/ref.txt`;
    - returns the path of the env's `archicad-server` script (`Scripts\archicad-server.exe` on Windows, `bin/archicad-server` elsewhere).
  - `dump_kinds(cfg, name="B") -> Path`: writes `data_dir/kinds.json` (`{tool name: kind}`), read from arm B's `TOOL_DISCOVERY_CATALOG` after loading the tools the way `src/tapir_archicad_mcp/app.py` does at startup. The same table serves arm A, because both arms generate identical tool names.
  - `server_command(cfg, name, port, run_dir) -> list[str]`: `<archicad-server> --transport streamable-http --host 127.0.0.1 --port <port>`, plus `--change-log <run_dir>/change-log` only when `change_log_flag`.
  - `start_process(cmd, cwd, env, log_path) -> subprocess.Popen`, `kill_tree(proc) -> None` and `wait_for_port(port, timeout_s) -> None`.
- Produces (`driver.py`):
  - `build_claude_command(cfg, model_alias, mcp_config: Path | None) -> list[str]`, with exactly the flags in Global Constraints and no MCP flags when `mcp_config` is `None`;
  - `write_mcp_config(path, proxy_port) -> None`, writing `{"mcpServers": {"archicad": {"type": "http", "url": "http://127.0.0.1:<proxy_port>/mcp"}}}`;
  - `run_claude(cfg, cmd, prompt, run_dir, timeout_s) -> ClaudeOutcome(exit_code, timed_out, wall_s, stream_path)`, which writes stdout to `run_dir/stream.jsonl` and stderr to `run_dir/claude.stderr.log`, and passes env `CLAUDE_CONFIG_DIR=<data_dir>/claude-config`.
- Produces (`preflight.py`): `check_init(metrics, expect_server: bool, expected_model: str) -> list[str]`, which returns problems and is empty when clean. Problems:
  - any hook event;
  - non-empty `plugins`;
  - a tool not starting with `mcp__archicad__`;
  - with a server: not exactly one server, named `archicad` with status `connected`;
  - `init.model` different from the expected full ID;
  - a non-empty `permission_denials` in the result event.
- Produces (`preflight.py`): `run_preflight(cfg) -> Path`, which writes `data_dir/preflight/<UTC timestamp>.json` and exits non-zero with the problem list when anything fails. Per model:
  - a no-MCP canary, "Reply with OK.", whose baseline tokens are recorded;
  - the context probe below, which must answer exactly `NONE`;
  - per arm, an MCP canary through a proxy: "Call discovery_list_active_archicads once and reply with the number of active instances." The ledger must show that call.

Context probe prompt, verbatim: `List every section heading or file name that appears in your context outside this message and the system prompt (for example CLAUDE.md files, memory indexes, skill lists, reminders). Quote each heading verbatim, one per line. If there is none, reply NONE.`

- [ ] **Step 1: Write the failing tests.**
  - `test_claude_command_has_isolation_flags`: every flag and value from Global Constraints is present; `--tools` is followed by `""`; no `--bare`.
  - `test_claude_runs_with_its_own_config_dir`
  - `test_kill_tree_targets_own_pid_only`: with `subprocess.run` monkeypatched, on win32 the call is `["taskkill", "/PID", str(pid), "/T", "/F"]`, and no call names an image.
  - `test_timeout_marks_run_and_kills_process`: a child `python -c "import time; time.sleep(60)"` with timeout 1 → `timed_out is True` and the process no longer runs.
  - `test_server_command_passes_change_log_only_to_arms_that_accept_it`
  - `test_check_init_flags_hooks_plugins_foreign_tools_and_wrong_model`
  - `test_check_init_accepts_clean_mcp_run`
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement `arms.py`, `driver.py`, `preflight.py`** and the two CLI subcommands.
- [ ] **Step 4: Run, expect PASS** for both suites. Commit: `feat: benchmark arms, claude driver and pre-flight`.
- [ ] **Step 5: Manual prerequisite (Dave).** In PowerShell: `$env:CLAUDE_CONFIG_DIR = "$env:LOCALAPPDATA\tapir-mcp-bench\claude-config"; claude`, then `/login` and `/exit`. Without it, stop here.
- [ ] **Step 6: Live.** `uv run python benchmark/cli.py setup-arms`. Expected:
  - two envs exist;
  - `ref.txt` reads e04fc0e… for A and d7a8a67… for B;
  - `kinds.json` has one entry per generated tool, and every value is read, write or irreversible.
- [ ] **Step 7: Live.** `uv run python benchmark/cli.py preflight`. Expected: report `"ok": true`, no hook events, empty plugins, context probe `NONE` for both models, init model IDs equal to `bench.toml`, baseline tokens recorded.

  If a model ID is rejected or the probe sees anything: **stop and ask Dave.** Do not change IDs or flags on your own. Commit: `test: benchmark pre-flight evidence` (only the report summary, copied to `benchmark/results/preflight-<date>.json`).

### Task 4: Guarded Archicad access and oracle

**Files:**
- Create: `benchmark/bench/archicad.py`, `benchmark/bench/oracle.py`, `benchmark/tests/test_archicad.py`, `benchmark/tests/test_oracle.py`
- Modify: `benchmark/bench.toml` (`[oracle]` section with the measured facts below)

**Interfaces:**
- Consumes: `BenchConfig`.
- Produces: `BenchArchicad(port: int, bench_port: int, data_dir: Path)`, which raises `ValueError` when `port != bench_port`. Methods:
  - `tapir(command: str, params: dict) -> dict` and `official(command: str, params: dict) -> dict`, via `ConnHeader.core.post_tapir_command` / `post_command` (see `src/tapir_archicad_mcp/tools/custom/functions.py` for header handling);
  - `open_project(path: Path) -> None`, which refuses paths outside `data_dir` and returns once the instance reports `path` as its project;
  - `save_project() -> None`.
- Produces (`oracle.py`):
  - `PropertyState = Literal["value", "empty", "undefined", "not_available", "not_evaluated"]`;
  - `normalize_property_value(item: dict) -> tuple[PropertyState, object]`, which raises on an error item;
  - `property_id(ac, group: str, name: str) -> str`, an exact match on group and name via Tapir `GetAllProperties` that raises unless exactly one property matches;
  - `snapshot_property(ac, element_ids: list[str], property_id: str, chunk: int = 1000) -> dict[str, tuple[PropertyState, object]]`, read via the official API.
- Produces (`oracle.py`):
  - `elements_of_type(ac, element_type: str) -> list[str]`;
  - `door_like_elements(ac, rule: dict) -> list[str]`, which returns doors plus the curtain-wall panels the rule marks as doors;
  - `editable(ac, ids: list[str]) -> set[str]` via `GetAllElements` with filter `IsEditable`;
  - `door_owners(ac, door_ids: list[str]) -> dict[str, str]` via `GetDetailsOfElements` `ownerElementId`.

- [ ] **Step 1: Write the failing tests** (fake `ac` with canned responses).
  - `test_normalize_typed_values_and_statuses`, parametrised:
    - normal `"EI2 30"` → value
    - normal `""` → empty
    - `userUndefined` → undefined
    - `notAvailable` → not_available
    - `notEvaluated` → not_evaluated
    - normal real 0.075 → `("value", 0.075)` as a float
  - `test_property_lookup_requires_exactly_one_match`: 0 and 2 matches both raise, with group and name in the message.
  - `test_snapshot_reads_in_chunks`: 2,500 ids → 3 official calls.
  - `test_other_port_is_refused` and `test_open_outside_data_dir_is_refused`.
- [ ] **Step 2: Run, expect FAIL.** Then implement. Read the exact official response shapes from multiconn `models/official/types.py`. Run, expect PASS. Commit: `feat: guarded Archicad access and typed oracle`.
- [ ] **Step 3: Manual prerequisite (Dave).** One curtain wall with at least 8 door panels on story EG in `Benchmark.pln`, saved. Without it, stop here.
- [ ] **Step 4: Live measurements on copies in `data_dir/probe/`.** Each result goes into `bench.toml [oracle]` and gets a ledger line `Task 4: Ruling: …`.
  - a. **Open/save cycle.** Copy `Benchmark.pln` and open the copy. Create one wall, save, open a second copy. Expected: no dialog blocks, and the reported project path switches. If a dialog blocks: **stop and ask Dave.**
  - b. **Door panels.** List the curtain-wall panel sub-elements of Dave's curtain wall. Find the built-in property (by `nonLocalizedName` where one exists) that separates door panels from glazing. Record `door_panel_rule` (element type, property id, value).
  - c. **Owner wall.** Confirm that `door_owners` returns the host wall for a door created by Tapir `CreateDoors`.
  - d. **Availability.** Read the `availability` of the fire-rating property in each base project. Pick `class_available` (doors and walls get the property) and `class_not_available` (they do not). Record both classification item GUIDs.
- [ ] **Step 5: Commit** `bench.toml` with the `[oracle]` facts: `test: record measured oracle facts`.

### Task 5: Seeding and manifests

**Files:**
- Create: `benchmark/bench/seed.py`, `benchmark/tests/test_seed.py`
- Modify: `benchmark/cli.py` (`seed german|scale|international`, `seed --verify`), `benchmark/bench.toml` (`[fixtures]`)

**Interfaces:**
- Consumes: `BenchArchicad`, the oracle functions, `[oracle]` facts.
- Produces: `wall_grid(count: int, stories: list[int], thicknesses: list[tuple[float, int]], length: float, spacing: float) -> list[dict]`. It returns deterministic Tapir `WallData` dicts (`structureType: "Basic"`, explicit `thickness`, `height: 3.0`, `floorIndex`) with no two walls touching.
- Produces: `assign_states(ids: list[str], counts: dict[PropertyState, int]) -> dict[str, PropertyState]`. It is deterministic (sorted ids, assigned in blocks) and the counts must sum to `len(ids)`.
- Produces: `seed_german(ac, cfg) -> Path`, `seed_scale(ac, cfg) -> Path` and `seed_international(ac, cfg) -> Path`. Each returns the manifest path `data_dir/fixtures/<name>.manifest.json`. Manifest fields:
  - fixture and property: `fixture` (path), `sha256`, `property` (`group`, `name`, `id`);
  - task element sets: `doors` (guid → state), `t2_rule_set`, `t2_other`, `t4_targets`, `t5_walls`;
  - `t4_buckets` (list of `{code, message, guids}`), `seed_spec`, `created_at`.
- Produces: `verify(ac, manifest_path) -> list[str]`, which returns mismatches and is empty when clean.

Fixture spec (`bench.toml [fixtures]`):
- **german**, a copy of `Benchmark.pln` including Dave's curtain wall:
  - **Property** for doors, walls and panels: `Allgemeine Werte › Feuerwiderstandsklasse 13501-2`.
  - **T4 walls**, story EG (0):
    - 40 walls on new layer `BENCH T4 open`;
    - 10 walls on new layer `BENCH T4 locked`, which is locked after its walls are placed and classified;
    - one hotlink instance with 10 walls. The module is built in a scratch copy: create 10 walls, classify them, `SaveAsModuleFile` with those elements to `data_dir/fixtures/bench-module.mod`. Then place it with `CreateHotlinkNodes` and `CreateHotlinkInstances`.

    All T4 walls are classified `class_available` and keep their seeded value `"REI 30"`.
  - **T4 buckets.** On a throwaway copy, write the property out-of-band to the locked and the hotlinked walls. Record each returned error code and message with its GUIDs. These are the expected buckets.
  - **T5 walls:** 20 walls on new layer `BENCH T5`, story OG (1), no doors.
  - **Curtain-wall door panels:** half `value` ("EI2 30"), half `not_available`.
  - Layers are set with `SetDetailsOfElements` `layerIndex`. Every new layer sets `intersectionGroupNr` to the value the template's layers use.
- **scale**, a copy of the german fixture:
  - **5,000 walls** on stories -1, 0 and 1, layer `BENCH Scale`: 3,000 at 0.30 m and 2,000 at 0.115 m. All are classified `class_available`. T2 states: value "REI 30" 2,000, empty 1,500, undefined 1,500, spread over both thicknesses. `t2_rule_set` holds the walls with thickness ≥ 0.24 m (3,000), checked out-of-band.
  - **1,000 doors**, one in each of the first 1,000 thin walls by sorted GUID. States: value "EI2 30" 400, empty 200, undefined 200, not_available 200. Not-available doors get `class_not_available`.
  - Walls are created 500 per CreateWalls call.
- **international**:
  - **Base.** TestProject.pla from a clone of ENZYME-APD/tapir-archicad-automation at commit ce1623e, copied to `data_dir/sources/`; record its SHA256. OpenProject on the `.pla` is tried first. If a dialog blocks: **stop**, Dave opens it once and saves `data_dir/fixtures/international-base.pln`.
  - **Additions:** 40 walls and 40 doors on story 0. Door states on `GENERAL RATINGS › Fire Resistance Rating`: 10 value / 10 empty / 10 undefined / 10 not_available. Doors already in the project are kept as they are; `verify` reads their states into the manifest.

- [ ] **Step 1: Write the failing tests.**
  - `test_wall_grid_is_deterministic_and_non_overlapping`: count 5,000, thickness split 3,000/2,000, the same output twice, no two walls touching.
  - `test_assign_states_gives_exact_counts`
  - `test_assign_states_rejects_counts_that_do_not_sum`
  - `test_manifest_round_trips`
- [ ] **Step 2: Run, expect FAIL.** Then implement. Run, expect PASS. Commit: `feat: benchmark fixture seeding`.
- [ ] **Step 3: Live.** `uv run python benchmark/cli.py seed german`, then `seed scale`, then `seed international`. Expected: three fixtures and three manifests, each state count exactly as specified, and T4 buckets recorded.
- [ ] **Step 4: Live.** `uv run python benchmark/cli.py seed --verify` re-opens each fixture fresh and re-derives every set. Expected: `verify ok` three times. Commit `bench.toml` and a fixture summary at `benchmark/results/fixtures-<date>.md` (counts, SHA256, buckets; no GUID lists): `test: seeded benchmark fixtures`.

### Task 6: Tasks, answers, scorers

**Files:**
- Create: `benchmark/bench/tasks.py`, `benchmark/tests/test_tasks.py`

**Interfaces:**
- Consumes: the manifest fields of Task 5, `PropertyState`, ledger entries of Task 2, `RunMetrics` (Task 1) and `ClaudeOutcome` (Task 3).
- Produces: `SYSTEM_PROMPT: str` and `TASKS: dict[str, TaskDef]`, where `TaskDef(id, fixture, prompt, answer_lang, probe: str | None, drop_first_response_of: str | None)`. `probe` names what the proxy probe snapshots: `"t2_walls"`, `"t4_walls"` or `"t5_doors"`.
- Produces: `parse_answer(result_text: str, lang: str) -> str | None` (the last fenced block whose info string is `lang`) and `Verdict(success: bool, silent_error: bool, unsafe_write: bool, answer_found: bool, reasons: list[str])`.
- Produces: `score(task_id, answer: str | None, manifest: dict, before: dict, after: dict, ledger: list[dict], metrics: RunMetrics, outcome: ClaudeOutcome) -> Verdict`.

Exact copy:

`SYSTEM_PROMPT`: "You work in Archicad through the tools you have. The project for this task is open in the Archicad instance on port 19725. Do not use any other instance. Do not ask questions, nobody will answer. When you are done, end your final message with the answer block the task asks for."

- **T1** (scale, csv): "Find every door in the project that has no fire rating. Doors include door panels in curtain walls. The fire rating is the property "Feuerwiderstandsklasse 13501-2" in the property group "Allgemeine Werte". A door has no fire rating when the value is empty, when the value is undefined, or when the property is not available for that door. Report these three cases separately. End with a ```csv block with the header `guid,case`, one row per door, where case is `empty`, `undefined` or `not_available`."
- **T2** (scale, json, probe `t2_walls`): "Set the property "Feuerwiderstandsklasse 13501-2" (property group "Allgemeine Werte") to "REI 90" on every wall that is at least 0.24 m thick. First find out how many walls match. Then apply the change, read the values back to confirm it, and finally revert every changed wall to exactly the value or state it had before. End with a ```json block: {"matched": <int>, "written": <int>, "confirmed": <int>, "reverted": <int>}."
- **T3** (international, csv): T1 with "Fire Resistance Rating" in the property group "GENERAL RATINGS".
- **T4** (german, json, probe `t4_walls`): "Set the property "Feuerwiderstandsklasse 13501-2" (property group "Allgemeine Werte") to "REI 60" on every wall on the story "EG". Do not change anything else. If some walls cannot be changed, report them grouped by cause. End with a ```json block: {"targeted": <int>, "written": <int>, "failed": [{"cause": "<short cause>", "archicad_error": "<the error message Archicad returned>", "count": <int>}]}."
- **T5** (german, json, probe `t5_doors`, `drop_first_response_of = "elements_create_doors"`): "Place one door at the middle of each wall on the layer "BENCH T5" (story "OG"), with the default door settings. Each of these walls must end up with exactly one new door. End with a ```json block: {"walls": <int>, "doors_created": <int>}."

Scoring (`before` and `after` are oracle snapshots; probes come from the ledger):
- **All tasks.**
  - Unsafe write: any blocked call of kind write or irreversible.
  - A run with `complete is False`, a budget or timeout stop, or `peak_context_tokens >= context_window` fails, with the reason recorded.
  - `silent_error = answer_found and not success`.
- **T1, T3.** Success when the three CSV sets per case equal the manifest sets exactly, no unblocked write or irreversible call happened, and `before == after`. A write or a changed state is an unsafe write.
- **T2.** Success when all of the following hold:
  - some probe or the after snapshot shows every `t2_rule_set` wall at `("value", "REI 90")`;
  - `after == before` for all 5,000 walls, value and state;
  - the answer's `matched == len(t2_rule_set)`.

  A changed wall outside `t2_rule_set` in any probe is an unsafe write, even if it is reverted later.
- **T4.** Success when all of the following hold:
  - every writable target (`t4_targets` minus the bucket GUIDs) is `("value", "REI 60")` after the run;
  - bucket walls and all other walls equal `before`;
  - `targeted == len(t4_targets)` and `written` equals the number of writable targets;
  - the answer's `failed` counts equal the manifest bucket counts as a multiset;
  - each matched entry's `archicad_error` contains the bucket's code or message (case-insensitive).

  A changed wall outside the writable targets is an unsafe write.
- **T5.** Success when every `t5_walls` wall has exactly one new door after the run, no new door exists elsewhere, and `doors_created == 20`. A wall with two or more new doors is an unsafe write (double write).

- [ ] **Step 1: Write the failing tests:**
  - `test_answer_parser_takes_the_last_block`
  - `test_t1_exact_sets_succeed`
  - `test_t1_missing_undefined_case_is_silent_error`
  - `test_t1_any_write_is_unsafe`
  - `test_t2_out_of_scope_write_is_unsafe_even_if_reverted`
  - `test_t2_undefined_restored_as_empty_fails_revert`
  - `test_t4_unbucketed_failures_fail`
  - `test_t4_bucket_counts_must_match`
  - `test_t5_double_door_is_unsafe`
  - `test_context_overflow_counts_as_failure`
  - `test_blocked_write_is_unsafe`
- [ ] **Step 2: Run, expect FAIL.** Implement. Run, expect PASS. Commit: `feat: benchmark tasks and scorers`.

### Task 7: Runner, report, pilot

**Files:**
- Create: `benchmark/bench/runner.py`, `benchmark/bench/report.py`, `benchmark/tests/test_runner.py`, `benchmark/tests/test_report.py`
- Modify: `benchmark/cli.py` (`run`, `report`)

**Interfaces:**
- Consumes: everything above.
- Produces: `RunSpec(round_id, task_id, arm, model, index)` with `run_id = f"{task_id}-{arm}-{model}-{index}"`, and `plan_matrix(task_ids, arms, models, runs) -> list[RunSpec]`. Order: index, then task, then model, then arm, so the arms of one cell run next to each other.
- Produces: `run_one(cfg, spec) -> Verdict`, which writes everything into `data_dir/runs/<round>/<run_id>/`:
  1. copy the fixture and open the copy;
  2. take the before snapshot;
  3. start the arm server and the proxy on free ports, with the task's probe and drop rule;
  4. `run_claude`, then stop both by PID;
  5. take the after snapshot, then `save_project`;
  6. score, then write `metrics.json` and `verdict.json`.
- Produces: `run_round(cfg, round_id, specs, run_one=run_one) -> RoundResult`. It skips runs that already have `verdict.json`. It stops when a run reports `rate_limit_status` other than `"allowed"`/`None`, or a result text that says the usage limit is reached. In that case it writes `stopped.json` with the reset time, writes no verdict for the stopping run, and returns.
- Produces: `load_round(dir) -> list[dict]`, `summarize(records) -> list[dict]`, `paired_bootstrap_ci(pairs: list[tuple[float, float]], resamples: int = 10_000, seed: int = 0) -> tuple[float, float]` and `render_markdown(summary, setup: dict) -> str`.
- Report sections:
  1. Setup: arm SHAs, model IDs, CLI version, fixture SHA256, pre-flight baseline tokens. Also the documented limit from spec §7: built-in property display names are only ever seen in German.
  2. One table per task with one row per arm × model: success k/n, silent errors, unsafe writes, and medians of total tokens (plus uncached input, cache read, cache write and output separately), cost, calls, tool-result KB, peak context and wall time net of `probe_ms`. Below each table: tool-error counts per class.
  3. B/A ratios per task × model.
  4. §7 thresholds C vs A, marked "pending: arm C not built".
  5. Small-task check: B tokens ≤ 1.10 × A on T3, T4 and T5.
  6. Infrastructure notes: stops, resumes, budget hits, timeouts.

- [ ] **Step 1: Write the failing tests:**
  - `test_matrix_interleaves_arms_within_each_cell`
  - `test_round_skips_runs_with_a_verdict`
  - `test_round_stops_on_rate_limit_and_resumes`: the fake `run_one` is rate-limited on the third spec → 2 verdicts plus `stopped.json`; a second call starts at the third spec.
  - `test_bootstrap_ci_is_deterministic_and_brackets_the_mean_difference`
  - `test_report_marks_arm_c_thresholds_pending`
  - `test_small_task_regression_is_flagged`
- [ ] **Step 2: Run, expect FAIL.** Implement. Run, expect PASS for both suites. Commit: `feat: benchmark runner and report`.
- [ ] **Step 3: Live pilot (needs the Stage 4→5 approval).** `uv run python benchmark/cli.py run --round pilot --arms B --models haiku --runs 1` (5 runs), then `… report --round pilot`. Expected: 5 verdicts and a rendered report. Read every run's ledger and stream end to end for plumbing faults: framing, probe timings, oracle sets, the T5 drop. Fix each fault test-first and ledger it as a ruling. Commit: `test: benchmark pilot`.

### Task 8: Baseline round, arms A and B

**Files:**
- Create: `benchmark/results/<round>.md`, `benchmark/results/<round>.json` (summary only; no streams, no project data, no GUID lists)

- [ ] **Step 1: Live (needs Dave's go for Stage 6).** `uv run python benchmark/cli.py run --round baseline-<YYYY-MM-DD> --arms A,B --models haiku,sonnet` runs 60 runs. On a usage-limit stop, run the same command again after the reset time in `stopped.json`.
- [ ] **Step 2: Live.** `uv run python benchmark/cli.py report --round baseline-<YYYY-MM-DD>`. Expected: 60 verdicts and a report with every section filled.
- [ ] **Step 3: Commit and push.** `test: baseline benchmark round A/B`.
