import sys

from bench import arms
from bench.arms import kill_tree, server_command
from configs import make_config


def test_server_command_passes_change_log_only_to_arms_that_accept_it(tmp_path):
    cfg = make_config(tmp_path)
    run_dir = tmp_path / "run"
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    exe = "archicad-server.exe" if sys.platform == "win32" else "archicad-server"

    assert server_command(cfg, "A", 8100, run_dir) == [
        str(cfg.data_dir / "arms" / "A" / "env" / scripts / exe),
        "--transport",
        "streamable-http",
        "--host",
        "127.0.0.1",
        "--port",
        "8100",
    ]
    assert server_command(cfg, "B", 8101, run_dir) == [
        str(cfg.data_dir / "arms" / "B" / "env" / scripts / exe),
        "--transport",
        "streamable-http",
        "--host",
        "127.0.0.1",
        "--port",
        "8101",
        "--change-log",
        str(run_dir / "change-log"),
    ]


class RunningProcess:
    pid = 4242

    def __init__(self):
        self.waited = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.waited = True
        return 1


def test_kill_tree_targets_own_pid_only(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(arms.subprocess, "run", lambda cmd, **kwargs: calls.append(cmd))
    process = RunningProcess()

    kill_tree(process)

    assert calls == [["taskkill", "/PID", "4242", "/T", "/F"]]
    assert process.waited


def test_kill_tree_leaves_finished_processes_alone(monkeypatch):
    calls = []
    monkeypatch.setattr(arms.subprocess, "run", lambda cmd, **kwargs: calls.append(cmd))
    process = RunningProcess()
    process.poll = lambda: 0

    kill_tree(process)

    assert calls == []
