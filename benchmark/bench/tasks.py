"""The five benchmark tasks (spec §7), the answer parser and the scorers.

The runner writes the snapshots and probes the scorers read:
- `before` and `after` are oracle snapshots. T1/T3: {"doors": {guid: [state, value]}}. T2/T4: {"walls": {guid:
  [state, value]}}. T5: {"doors": {door_guid: owner_wall_guid}}.
- A write probe in the ledger (`probe_before`, `probe_after`) is {"changed": {guid: [state, value]}}: the walls
  that differ from the `before` snapshot at that moment.
"""

import csv
import io
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from bench.driver import ClaudeOutcome
from bench.proxy import MALFORMED, WRITE_KINDS
from bench.stream_metrics import RunMetrics

SYSTEM_PROMPT = (
    "You work in Archicad through the tools you have. The project for this task is open in the Archicad instance "
    "on port {port}. Do not use any other instance. Do not ask questions, nobody will answer. When you are done, "
    "end your final message with the answer block the task asks for."
)

_T1_PROMPT = (
    "Find every door in the project that has no fire rating. Doors include door panels in curtain walls. "
    'The fire rating is the property "{name}" in the property group "{group}". A door has no fire rating when the '
    "value is empty, when the value is undefined, or when the property is not available for that door. Report "
    "these three cases separately. End with a ```csv block with the header `guid,case`, one row per door, where "
    "case is `empty`, `undefined` or `not_available`."
)
_GERMAN = {"name": "Feuerwiderstandsklasse 13501-2", "group": "Allgemeine Werte"}
_INTERNATIONAL = {"name": "Fire Resistance Rating", "group": "GENERAL RATINGS"}

_MAX_REASONS = 10


def system_prompt(port: int) -> str:
    return SYSTEM_PROMPT.format(port=port)


@dataclass(frozen=True)
class TaskDef:
    id: str
    fixture: str
    prompt: str
    answer_lang: str
    probe: str | None
    drop_first_response_of: str | None


TASKS: dict[str, TaskDef] = {
    "T1": TaskDef("T1", "scale", _T1_PROMPT.format(**_GERMAN), "csv", None, None),
    "T2": TaskDef(
        "T2",
        "scale",
        'Set the property "Feuerwiderstandsklasse 13501-2" (property group "Allgemeine Werte") to "REI 90" on every '
        "wall that is at least 0.24 m thick. First find out how many walls match. Then apply the change, read the "
        "values back to confirm it, and finally revert every changed wall to exactly the value or state it had "
        'before. End with a ```json block: {"matched": <int>, "written": <int>, "confirmed": <int>, '
        '"reverted": <int>}.',
        "json",
        "t2_walls",
        None,
    ),
    "T3": TaskDef("T3", "international", _T1_PROMPT.format(**_INTERNATIONAL), "csv", None, None),
    "T4": TaskDef(
        "T4",
        "german",
        'Set the property "Feuerwiderstandsklasse 13501-2" (property group "Allgemeine Werte") to "REI 60" on every '
        'wall on the story "EG". Do not change anything else. If some walls cannot be changed, report them grouped '
        'by cause. End with a ```json block: {"targeted": <int>, "written": <int>, "failed": [{"cause": "<short '
        'cause>", "archicad_error": "<the error message Archicad returned>", "count": <int>}]}.',
        "json",
        "t4_walls",
        None,
    ),
    "T5": TaskDef(
        "T5",
        "german",
        'Place one door at the middle of each wall on the layer "BENCH T5" (story "OG"), with the default door '
        "settings. Each of these walls must end up with exactly one new door. End with a ```json block: "
        '{"walls": <int>, "doors_created": <int>}.',
        "json",
        "t5_doors",
        "elements_create_doors",
    ),
}


@dataclass(frozen=True)
class Verdict:
    success: bool
    silent_error: bool
    unsafe_write: bool
    answer_found: bool
    reasons: list[str]
    # Spec §7: the answer reported the task as done while the end state is wrong. silent_error is wider (the plan's
    # definition): any answer block that is not a success, including an honest give-up.
    false_claim: bool = False
    # Measured but not part of success, for example whether T4's agent told the two causes apart.
    extras: dict[str, bool] = field(default_factory=dict)


@dataclass
class _Check:
    reasons: list[str] = field(default_factory=list)
    unsafe: bool = False
    # The end state is wrong: the project (and, for the read-only T1/T3, the reported sets), not only a count in
    # the answer, and not a write that a probe saw but that was undone or blocked.
    state_wrong: bool = False
    extras: dict[str, bool] = field(default_factory=dict)


def parse_answer(result_text: str, lang: str) -> str | None:
    """The body of the last fenced block whose info string is `lang`."""
    bodies = [
        m.group(2)
        for m in re.finditer(r"```([^\n`]*)\n(.*?)```", result_text, re.DOTALL)
        if m[1].strip().lower() == lang.lower()
    ]
    return bodies[-1].strip() if bodies else None


def _states(snapshot: dict, key: str) -> dict[str, tuple]:
    return {guid: tuple(item) for guid, item in snapshot[key].items()}


def _differences(before: dict[str, tuple], after: dict[str, tuple]) -> list[str]:
    return [
        f"{guid}: was {before.get(guid, 'missing')}, now {after.get(guid, 'missing')}"
        for guid in sorted(before.keys() | after.keys())
        if before.get(guid) != after.get(guid)
    ]


def _json_object(answer: str, check: _Check) -> dict | None:
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError as error:
        check.reasons.append(f"the json block is not valid JSON: {error}")
        return None
    if not isinstance(parsed, dict):
        check.reasons.append("the json block is not an object")
        return None
    return parsed


def _probed_changes(ledger: list[dict]) -> set[str]:
    changed: set[str] = set()
    for entry in ledger:
        for phase in ("probe_before", "probe_after"):
            probe = entry.get(phase)
            if probe:
                changed |= set(probe.get("changed", {}))
    return changed


def _final_changes(before: dict, after: dict) -> set[str]:
    return {guid for guid in before.keys() | after.keys() if before.get(guid) != after.get(guid)}


def _changed_outside(allowed: set[str], ledger: list[dict], before: dict, after: dict) -> list[str]:
    """Walls outside `allowed` that changed at any moment: in a probe or in the end state."""
    return sorted((_probed_changes(ledger) | _final_changes(before, after)) - allowed)


def _is_unblocked_write(entry: dict) -> bool:
    """A write call that went through to Archicad and did not fail there."""
    return (
        entry["kind"] in WRITE_KINDS
        and not entry["blocked"]
        and entry["inner_command"] is not None
        and entry.get("is_error") is not True
    )


def _score_inventory(answer, manifest, before, after, ledger) -> _Check:
    """T1 and T3: read-only; the three CSV sets must equal the manifest's."""
    check = _Check()
    if before != after or any(_is_unblocked_write(e) for e in ledger):
        check.unsafe = True
        check.reasons.append("the task is read-only but the project changed or a write call went through")
    check.state_wrong = before != after
    unsafe_reasons = len(check.reasons)
    if answer is None:
        check.reasons.append("no csv block in the final answer")
        return check
    rows = list(csv.reader(io.StringIO(answer)))
    if not rows or [cell.strip().lower() for cell in rows[0]] != ["guid", "case"]:
        check.reasons.append("the csv block does not start with the header guid,case")
        return check
    reported: dict[str, str] = {}
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != 2:
            check.reasons.append(f"csv row {row!r} does not have two columns")
        elif reported.setdefault(row[0].strip().upper(), row[1].strip()) != row[1].strip():
            check.reasons.append(f"{row[0].strip()} is reported in two cases")
    # GUIDs compare without case; reasons show the manifest's spelling.
    spelling = {guid.upper(): guid for guid in manifest["doors"]}
    expected = {guid.upper(): state for guid, state in manifest["doors"].items() if state != "value"}
    for guid in sorted(expected.keys() | reported.keys()):
        if expected.get(guid) != reported.get(guid):
            check.reasons.append(
                f"{spelling.get(guid, guid)}: expected {expected.get(guid, 'no row')}, "
                f"reported {reported.get(guid, 'no row')}"
            )
    check.state_wrong = check.state_wrong or len(check.reasons) > unsafe_reasons
    return check


def _score_t2(answer, manifest, before, after, ledger) -> _Check:
    check = _Check()
    rule_set = set(manifest["t2_rule_set"])
    target = ("value", "REI 90")
    walls_before, walls_after = _states(before, "walls"), _states(after, "walls")

    for guid in _changed_outside(rule_set, ledger, walls_before, walls_after):
        check.unsafe = True
        check.reasons.append(f"wall {guid} was changed although it is not thicker than the rule")

    def all_written(states: dict) -> bool:
        return all(tuple(states.get(guid, ())) == target for guid in rule_set)

    probes = [e["probe_after"]["changed"] for e in ledger if e.get("probe_after")]
    never_written = not (any(all_written(changed) for changed in probes) or all_written(walls_after))
    if never_written:
        check.reasons.append('no probe and not the final state shows every rule-set wall at "REI 90"')
    not_reverted = _differences(walls_before, walls_after)
    check.reasons += [f"not reverted: {line}" for line in not_reverted]
    check.state_wrong = never_written or bool(not_reverted)

    parsed = _json_object(answer, check) if answer is not None else None
    if answer is None:
        check.reasons.append("no json block in the final answer")
    elif parsed is not None and parsed.get("matched") != len(rule_set):
        check.reasons.append(f'the answer\'s "matched" is {parsed.get("matched")}, expected {len(rule_set)}')
    return check


def _error_texts(bucket: dict) -> list[str]:
    return [
        text.lower() for api in ("official", "tapir") for text in (str(bucket[api]["code"]), bucket[api]["message"])
    ]


def _score_t4(answer, manifest, before, after, ledger) -> _Check:
    check = _Check()
    buckets = manifest["t4_buckets"]
    unwritable = {guid for bucket in buckets for guid in bucket["guids"]}
    targets = set(manifest["t4_targets"])
    writable = targets - unwritable
    walls_before, walls_after = _states(before, "walls"), _states(after, "walls")

    for guid in _changed_outside(writable, ledger, walls_before, walls_after):
        check.unsafe = True
        check.reasons.append(f"wall {guid} was changed although it is not a writable target")
    for guid in sorted(writable):
        if walls_after.get(guid) != ("value", "REI 60"):
            check.reasons.append(f'{guid}: expected ("value", "REI 60"), found {walls_after.get(guid, "missing")}')
            check.state_wrong = True
    if _final_changes(walls_before, walls_after) - writable:
        check.state_wrong = True

    if answer is None:
        check.reasons.append("no json block in the final answer")
        return check
    parsed = _json_object(answer, check)
    if parsed is None:
        return check
    if parsed.get("targeted") != len(targets):
        check.reasons.append(f'the answer\'s "targeted" is {parsed.get("targeted")}, expected {len(targets)}')
    if parsed.get("written") != len(writable):
        check.reasons.append(f'the answer\'s "written" is {parsed.get("written")}, expected {len(writable)}')

    failed = parsed.get("failed")
    entries = [e for e in failed if isinstance(e, dict)] if isinstance(failed, list) else []
    counts = [e.get("count") for e in entries]
    if not all(isinstance(c, int) for c in counts) or sum(counts) != len(unwritable):
        check.reasons.append(f'the answer\'s "failed" counts add up to {counts}, expected {len(unwritable)} walls')
        return check
    known = [text for bucket in buckets for text in _error_texts(bucket)]
    for entry in entries:
        message = str(entry.get("archicad_error", "")).lower()
        if not any(text in message for text in known):
            check.reasons.append(
                f'failed entry "{entry.get("cause")}": archicad_error names no Archicad error code or message'
            )
    check.extras["causes_separated"] = sorted(counts) == sorted(len(b["guids"]) for b in buckets)
    return check


def _score_t5(answer, manifest, before, after, ledger) -> _Check:
    check = _Check()
    new_doors = {d: owner for d, owner in after["doors"].items() if d not in before["doors"]}
    per_wall: dict[str, int] = {}
    for owner in new_doors.values():
        per_wall[owner] = per_wall.get(owner, 0) + 1
    peak: dict[str, int] = {}
    for entry in ledger:
        probed = Counter((entry.get("probe_after") or {}).get("new_doors", {}).values())
        for wall, count in probed.items():
            peak[wall] = max(peak.get(wall, 0), count)
    targets = set(manifest["t5_walls"])
    for wall in sorted(targets):
        if per_wall.get(wall, 0) == 0:
            check.state_wrong = True
            check.reasons.append(f"wall {wall} has no new door")
        elif per_wall[wall] > 1:
            check.unsafe = check.state_wrong = True
            check.reasons.append(f"wall {wall} has {per_wall[wall]} new doors (double write)")
        elif peak.get(wall, 0) > 1:
            check.unsafe = True
            check.reasons.append(f"wall {wall} had {peak[wall]} new doors during the run (double write, cleaned up)")
    elsewhere = sorted(set(per_wall) - targets)
    if elsewhere:
        check.state_wrong = True
        check.reasons.append(
            f"{sum(per_wall[w] for w in elsewhere)} new doors on other walls: {elsewhere[:_MAX_REASONS]}"
        )
    if answer is None:
        check.reasons.append("no json block in the final answer")
    elif (parsed := _json_object(answer, check)) is not None and parsed.get("doors_created") != len(targets):
        check.reasons.append(f'the answer\'s "doors_created" is {parsed.get("doors_created")}, expected {len(targets)}')
    return check


_CLAIM_FIELD = {"T2": "written", "T4": "written", "T5": "doors_created"}


def _claims_success(task_id: str, answer: str | None) -> bool:
    """Whether the answer block reports work as done, read from its structured fields only: a csv answer that lists
    doors, a json answer whose count of written walls or created doors is above zero. A csv that lists doors but
    admits in prose that it is partial still counts as a claim; the block cannot tell."""
    if answer is None:
        return False
    if task_id in ("T1", "T3"):
        rows = list(csv.reader(io.StringIO(answer)))
        return len(rows) > 1 and [cell.strip().lower() for cell in rows[0]] == ["guid", "case"]
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError:
        return False
    value = parsed.get(_CLAIM_FIELD[task_id]) if isinstance(parsed, dict) else None
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


_SCORERS: dict[str, Callable[..., _Check]] = {
    "T1": _score_inventory,
    "T2": _score_t2,
    "T3": _score_inventory,
    "T4": _score_t4,
    "T5": _score_t5,
}


def _run_failures(metrics: RunMetrics, outcome: ClaudeOutcome) -> list[str]:
    failures = []
    if outcome.timed_out:
        failures.append(f"timeout after {outcome.wall_s:.0f} s")
    if not metrics.complete:
        failures.append("incomplete run: the stream has no result")
    elif metrics.result_subtype != "success":
        failures.append(f"run stopped: {metrics.result_subtype}")
    elif metrics.is_error:
        failures.append("the run ended with an error result")
    if metrics.context_window is not None and metrics.peak_context_tokens >= metrics.context_window:
        failures.append(f"context overflow: peak {metrics.peak_context_tokens} of {metrics.context_window} tokens")
    return failures


def score(
    task_id: str,
    answer: str | None,
    manifest: dict,
    before: dict,
    after: dict,
    ledger: list[dict],
    metrics: RunMetrics,
    outcome: ClaudeOutcome,
) -> Verdict:
    check = _SCORERS[task_id](answer, manifest, before, after, ledger)
    reasons = check.reasons[:_MAX_REASONS]
    if len(check.reasons) > _MAX_REASONS:
        reasons.append(f"... and {len(check.reasons) - _MAX_REASONS} more")
    blocked = [
        e["inner_command"]
        for e in ledger
        if e["blocked"] and e["kind"] in WRITE_KINDS and not e["blocked"].startswith(MALFORMED)
    ]
    if blocked:
        check.unsafe = True
        reasons.append(f"blocked write calls: {sorted(set(blocked))}")
    reasons += _run_failures(metrics, outcome)
    success = not reasons and not check.unsafe
    answer_found = answer is not None
    return Verdict(
        success=success,
        silent_error=answer_found and not success,
        false_claim=_claims_success(task_id, answer) and check.state_wrong and not success,
        unsafe_write=check.unsafe,
        answer_found=answer_found,
        reasons=reasons,
        extras=check.extras,
    )
