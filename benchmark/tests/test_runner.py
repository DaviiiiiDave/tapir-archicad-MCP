import json

import pytest
from bench import runner
from bench.runner import (
    RunSpec,
    UsageLimitReached,
    make_probe,
    plan_matrix,
    run_record,
    run_round,
    usage_limit_reached,
)
from bench.tasks import TASKS, Verdict
from configs import make_config
from runs import metrics, outcome


def test_matrix_interleaves_arms_within_each_cell():
    specs = plan_matrix(["T1", "T2"], ["A", "B"], ["haiku", "sonnet"], runs=2, round_id="r")

    assert len(specs) == 16
    assert [s.run_id for s in specs[:5]] == [
        "T1-A-haiku-1",
        "T1-B-haiku-1",
        "T1-A-sonnet-1",
        "T1-B-sonnet-1",
        "T2-A-haiku-1",
    ]
    assert specs[8].run_id == "T1-A-haiku-2"
    assert all(s.round_id == "r" for s in specs)


def fake_run_one(calls, stop_at=None):
    def run_one(cfg, spec):
        if spec.run_id == stop_at:
            raise UsageLimitReached("rejected", "You've hit your usage limit", None)
        calls.append(spec.run_id)
        run_dir = cfg.data_dir / "runs" / spec.round_id / spec.run_id
        run_dir.mkdir(parents=True)
        (run_dir / "verdict.json").write_text("{}", encoding="utf-8")
        return Verdict(True, False, False, True, [])

    return run_one


def test_round_skips_runs_with_a_verdict(tmp_path):
    cfg = make_config(tmp_path)
    specs = plan_matrix(["T1"], ["A", "B"], ["haiku"], runs=2, round_id="r")
    done = cfg.data_dir / "runs" / "r" / specs[0].run_id
    done.mkdir(parents=True)
    (done / "verdict.json").write_text("{}", encoding="utf-8")
    calls = []

    result = run_round(cfg, "r", specs, run_one=fake_run_one(calls))

    assert calls == [s.run_id for s in specs[1:]]
    assert result.skipped == [specs[0].run_id]
    assert result.stopped is None


def test_round_stops_on_rate_limit_and_resumes(tmp_path):
    cfg = make_config(tmp_path)
    specs = plan_matrix(["T1"], ["A", "B"], ["haiku"], runs=2, round_id="r")
    calls = []

    first = run_round(cfg, "r", specs, run_one=fake_run_one(calls, stop_at=specs[2].run_id))

    round_dir = cfg.data_dir / "runs" / "r"
    assert calls == [specs[0].run_id, specs[1].run_id]
    assert first.stopped is not None
    stopped = json.loads((round_dir / "stopped.json").read_text(encoding="utf-8"))
    assert stopped["run_id"] == specs[2].run_id
    assert stopped["status"] == "rejected"
    assert not (round_dir / specs[2].run_id / "verdict.json").exists()

    second = run_round(cfg, "r", specs, run_one=fake_run_one(calls))

    assert calls[2:] == [specs[2].run_id, specs[3].run_id]
    assert second.stopped is None
    assert not (round_dir / "stopped.json").exists()


@pytest.mark.parametrize(
    ("status", "text", "stops"),
    [
        ("allowed", "done", False),
        (None, "done", False),
        ("allowed_warning", "done", False),
        ("rejected", "done", True),
        ("allowed", "Claude AI usage limit reached|1760000000", True),
        ("allowed", "You've reached your usage limit. It resets at 9pm.", True),
    ],
)
def test_usage_limit_detection(status, text, stops):
    found = usage_limit_reached(metrics(rate_limit_status=status, result_text=text))

    assert (found is not None) is stops


def test_usage_limit_extracts_the_reset_time_when_the_message_carries_one():
    found = usage_limit_reached(metrics(result_text="Claude AI usage limit reached|1760000000"))

    assert found.resets_at == "2025-10-09T08:53:20+00:00"


def test_probe_reports_only_walls_that_differ_from_before(monkeypatch):
    before = {"walls": {"w1": ["value", "REI 30"], "w2": ["undefined", None]}}
    now = {"walls": {"w1": ["value", "REI 90"], "w2": ["undefined", None]}}
    seen = []

    def fake_snapshot(ac, task, manifest, rule, like=None):
        seen.append(like)
        return now

    monkeypatch.setattr(runner, "snapshot", fake_snapshot)

    probe = make_probe(object(), TASKS["T2"], {}, before, {})

    assert probe("after", {}) == {"changed": {"w1": ["value", "REI 90"]}}
    assert seen == [before]


def test_t5_probe_reports_the_new_doors(monkeypatch):
    before = {"doors": {"old": "wall0"}}
    monkeypatch.setattr(
        runner, "snapshot", lambda *a, **k: {"doors": {"old": "wall0", "new1": "wall1", "new2": "wall1"}}
    )

    probe = make_probe(object(), TASKS["T5"], {}, before, {})

    assert probe("before", {}) == {"new_doors": {"new1": "wall1", "new2": "wall1"}}


def test_run_record_nets_probe_time_out_of_the_wall_time():
    ledger = [
        {"inner_command": "a", "kind": "write", "blocked": None, "probe_ms": 2000.0},
        {"inner_command": None, "kind": "read", "blocked": None, "probe_ms": 0.0},
        {"inner_command": "b", "kind": "write", "blocked": None, "probe_ms": 1000.0},
    ]
    spec = RunSpec("r", "T4", "B", "haiku", 2)

    record = run_record(
        spec,
        Verdict(False, True, False, True, ["why"], false_claim=True, extras={"causes_separated": False}),
        metrics(input_tokens=100, output_tokens=50, cache_read_tokens=1000, cache_creation_tokens=200, cost_usd=0.5),
        outcome(wall_s=100.0),
        ledger,
    )

    assert record["run_id"] == "T4-B-haiku-2"
    assert (record["task"], record["arm"], record["model"], record["index"]) == ("T4", "B", "haiku", 2)
    assert record["success"] is False
    assert record["silent_error"] is True
    assert record["false_claim"] is True
    assert record["extras"] == {"causes_separated": False}
    assert record["tokens"] == {"input": 100, "output": 50, "cache_read": 1000, "cache_creation": 200, "total": 1350}
    assert record["probe_ms"] == 3000.0
    assert record["wall_net_s"] == 97.0
    assert record["cost_usd"] == 0.5
    assert record["ledger_calls"] == 3


def test_run_record_counts_tool_errors_by_class():
    from bench.stream_metrics import ToolCall

    calls = [
        ToolCall("1", "archicad_call_tool", "x", 19724, True, 10, "archicad"),
        ToolCall("2", "archicad_call_tool", "x", 19724, True, 10, "archicad"),
        ToolCall("3", "archicad_call_tool", "y", 19724, True, 10, "validation"),
        ToolCall("4", "archicad_call_tool", "y", 19724, False, 5_000, None),
    ]

    record = run_record(
        RunSpec("r", "T1", "A", "haiku", 1),
        Verdict(True, False, False, True, []),
        metrics(tool_calls=calls, tool_result_bytes=5_030),
        outcome(),
        [],
    )

    assert record["calls"] == 4
    assert record["tool_errors"] == {"archicad": 2, "validation": 1}
    assert record["tool_result_kb"] == pytest.approx(5_030 / 1024)


def test_a_stopped_attempt_is_set_aside_before_the_run_starts(tmp_path):
    run_dir = tmp_path / "runs" / "r" / "T1-A-haiku-1"
    run_dir.mkdir(parents=True)
    (run_dir / "stream.jsonl").write_text("half a stream", encoding="utf-8")

    fresh = runner.fresh_run_dir(tmp_path, RunSpec("r", "T1", "A", "haiku", 1))

    assert fresh == run_dir
    assert list(fresh.iterdir()) == []
    kept = [p for p in run_dir.parent.iterdir() if p.name.startswith("T1-A-haiku-1.aborted")]
    assert len(kept) == 1
    assert (kept[0] / "stream.jsonl").read_text(encoding="utf-8") == "half a stream"
