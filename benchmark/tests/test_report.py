import json

import pytest
from bench.report import load_round, paired_bootstrap_ci, render_markdown, summarize

SETUP = {
    "round_id": "r",
    "arms": {"A": {"ref": "v0.6.5", "sha": "e04fc0e"}, "B": {"ref": "d7a8a67", "sha": "d7a8a67"}},
    "models": {"haiku": "claude-haiku-5-5"},
    "cli_version": "2.1.81",
    "fixtures": {"german": "7091383d"},
    "baseline_tokens": {"haiku": 189},
    "aborted_attempts": 0,
}


def record(task, arm, index, total, model="haiku", success=True, **overrides):
    values = {
        "run_id": f"{task}-{arm}-{model}-{index}",
        "task": task,
        "arm": arm,
        "model": model,
        "index": index,
        "success": success,
        "silent_error": not success,
        "false_claim": False,
        "unsafe_write": False,
        "answer_found": True,
        "reasons": [],
        "extras": {},
        "tokens": {"input": 10, "output": 20, "cache_read": total - 130, "cache_creation": 100, "total": total},
        "cost_usd": total / 10_000,
        "turns": 4,
        "calls": 6,
        "tool_errors": {},
        "tool_result_kb": 3.0,
        "peak_context_tokens": 9_000,
        "context_window": 200_000,
        "result_subtype": "success",
        "timed_out": False,
        "wall_s": 60.0,
        "probe_ms": 0.0,
        "wall_net_s": 60.0,
        "ledger_calls": 6,
    }
    return values | overrides


def test_load_round_reads_verdicts_and_ignores_set_aside_attempts(tmp_path):
    for name in ("T1-A-haiku-1", "T1-A-haiku-1.aborted-20261020T101010", "T1-B-haiku-1"):
        (tmp_path / name).mkdir()
    (tmp_path / "T1-A-haiku-1" / "verdict.json").write_text(json.dumps(record("T1", "A", 1, 1000)), encoding="utf-8")
    (tmp_path / "T1-A-haiku-1.aborted-20261020T101010" / "verdict.json").write_text("{}", encoding="utf-8")
    (tmp_path / "stopped.json").write_text("{}", encoding="utf-8")

    records = load_round(tmp_path)

    assert [r["run_id"] for r in records] == ["T1-A-haiku-1"]


def test_summarize_counts_and_takes_medians_per_cell():
    records = [
        record("T4", "A", 1, 1000, extras={"causes_separated": True}),
        record("T4", "A", 2, 3000, success=False, false_claim=True, tool_errors={"archicad": 2}),
        record("T4", "A", 3, 2000, tool_errors={"archicad": 1, "validation": 1}, extras={"causes_separated": False}),
        record("T4", "B", 1, 500),
    ]

    rows = {(r["task"], r["arm"], r["model"]): r for r in summarize(records)}

    a = rows[("T4", "A", "haiku")]
    assert (a["n"], a["successes"], a["silent_errors"], a["false_claims"], a["unsafe_writes"]) == (3, 2, 1, 1, 0)
    assert a["median"]["total"] == 2000
    assert a["tool_errors"] == {"archicad": 3, "validation": 1}
    assert a["causes_separated"] == (1, 2)
    assert a["per_index_total"] == {1: 1000, 2: 3000, 3: 2000}
    assert rows[("T4", "B", "haiku")]["causes_separated"] is None


def test_summarize_counts_budget_stops_and_timeouts():
    records = [
        record("T1", "A", 1, 1000, result_subtype="error_max_budget_usd"),
        record("T1", "A", 2, 1000, timed_out=True),
        record("T1", "A", 3, 1000),
    ]

    (row,) = summarize(records)

    assert (row["budget_stops"], row["timeouts"]) == (1, 1)


def test_bootstrap_ci_is_deterministic_and_brackets_the_mean_difference():
    pairs = [(100.0, 110.0), (100.0, 130.0), (100.0, 120.0), (100.0, 140.0)]

    low, high = paired_bootstrap_ci(pairs)

    assert (low, high) == paired_bootstrap_ci(pairs)
    assert low <= 25.0 <= high
    assert low < high


def test_bootstrap_ci_of_a_constant_difference_has_no_width():
    assert paired_bootstrap_ci([(1.0, 3.0)] * 4) == (2.0, 2.0)


def test_bootstrap_ci_needs_pairs():
    with pytest.raises(ValueError, match="pairs"):
        paired_bootstrap_ci([])


def full_summary(b_t4_total):
    records = []
    for index in (1, 2, 3):
        for task in ("T1", "T2", "T3", "T4", "T5"):
            records.append(record(task, "A", index, 1000 + index))
            records.append(record(task, "B", index, b_t4_total + index if task == "T4" else 900 + index))
    return summarize(records)


def test_report_marks_arm_c_thresholds_pending():
    markdown = render_markdown(full_summary(900), SETUP)

    assert "pending: arm C not built" in markdown
    for heading in ("Setup", "B/A", "Small-task check", "Infrastructure"):
        assert heading in markdown
    assert "claude-haiku-5-5" in markdown
    assert "German" in markdown


def test_small_task_regression_is_flagged():
    flagged = render_markdown(full_summary(1300), SETUP)
    clean = render_markdown(full_summary(1000), SETUP)

    assert "REGRESSION" in flagged
    assert "REGRESSION" not in clean


def test_report_shows_the_success_rate_and_the_t4_cause_split():
    records = [record("T4", "A", i, 1000, extras={"causes_separated": i == 1}) for i in (1, 2)]

    markdown = render_markdown(summarize(records), SETUP)

    assert "2/2" in markdown
    assert "causes separated 1/2" in markdown


def test_report_with_one_arm_says_the_comparison_needs_both():
    markdown = render_markdown(summarize([record("T4", "B", 1, 1000)]), SETUP)

    assert markdown.count("No cell has both arms yet.") == 2
    assert "| task | model | tokens B/A" not in markdown


def test_report_states_that_cost_is_the_cli_estimate_for_subscription_runs():
    markdown = render_markdown(summarize([record("T4", "B", 1, 1000)]), SETUP)

    assert "subscription" in markdown


def test_report_shows_wrong_answers_and_false_claims_in_two_columns():
    records = [
        record("T2", "B", 1, 1000, success=False),
        record("T2", "B", 2, 1000, success=False, false_claim=True),
    ]

    markdown = render_markdown(summarize(records), SETUP)

    assert "| wrong answers | false claims |" in markdown
    assert "| 0/2 | 2 | 1 |" in markdown


def test_ratios_over_failed_runs_are_marked_as_not_a_saving():
    records = []
    for index in (1, 2, 3):
        records.append(record("T2", "A", index, 1_000_000, success=False))
        records.append(record("T2", "B", index, 90_000, success=False))

    markdown = render_markdown(summarize(records), SETUP)

    row = next(line for line in markdown.splitlines() if line.startswith("| T2 | haiku |"))
    assert "| 0/3 | 0/3 |" in row
    assert "0.09 *" in row
    assert "* At least one arm failed runs" in markdown


def test_ratios_where_both_arms_solve_every_run_carry_no_mark():
    records = [record("T4", arm, i, 1000) for arm in ("A", "B") for i in (1, 2, 3)]

    markdown = render_markdown(summarize(records), SETUP)

    row = next(line for line in markdown.splitlines() if line.startswith("| T4 | haiku |"))
    assert "| 3/3 | 3/3 | 1.00 |" in row


def test_runs_without_a_result_are_left_out_of_the_medians_and_never_crash_the_report():
    unfinished = {"result_subtype": None, "cost_usd": 0.0}
    records = [
        record("T4", "A", 1, 0, success=False, **unfinished),
        record("T4", "A", 2, 0, success=False, **unfinished),
        record("T4", "B", 1, 1000),
        record("T4", "B", 2, 1000),
    ]

    rows = {(r["arm"]): r for r in summarize(records)}
    markdown = render_markdown(summarize(records), SETUP)

    assert rows["A"]["median"]["total"] is None
    assert rows["A"]["unfinished"] == 2
    assert "n/a" in markdown
