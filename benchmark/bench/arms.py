"""Benchmark arms: a git ref of this repository, installed into its own environment, served over HTTP."""

import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from bench.config import BenchConfig

KINDS_SCRIPT = (
    "import json, tapir_archicad_mcp.tools\n"
    "from tapir_archicad_mcp.tools.tool_registry import TOOL_DISCOVERY_CATALOG\n"
    "print(json.dumps({name: entry['kind'] for name, entry in TOOL_DISCOVERY_CATALOG.items()}))\n"
)


def _bin_dir(env_dir: Path) -> Path:
    return env_dir / ("Scripts" if sys.platform == "win32" else "bin")


def _executable(env_dir: Path, name: str) -> Path:
    return _bin_dir(env_dir) / (f"{name}.exe" if sys.platform == "win32" else name)


def arm_dir(cfg: BenchConfig, name: str) -> Path:
    return cfg.data_dir / "arms" / name


def prepare_arm(cfg: BenchConfig, name: str) -> Path:
    """Export the arm's ref with git archive, install it into a fresh env, and return its server script."""
    base = arm_dir(cfg, name)
    sha = subprocess.run(
        ["git", "rev-parse", "--verify", f"{cfg.arms[name].ref}^{{commit}}"],
        cwd=cfg.repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    archive = subprocess.run(
        ["git", "archive", "--format=tar", sha], cwd=cfg.repo_root, capture_output=True, check=True
    )

    src, env = base / "src", base / "env"
    for directory in (src, env):
        if directory.exists():
            shutil.rmtree(directory)
    src.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(src, filter="data")

    subprocess.run(["uv", "venv", "--python", sys.executable, str(env)], check=True, capture_output=True)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(_executable(env, "python")), str(src)], check=True, capture_output=True
    )
    (base / "ref.txt").write_text(sha + "\n", encoding="utf-8")
    return _executable(env, "archicad-server")


def dump_kinds(cfg: BenchConfig, name: str = "B") -> Path:
    """Write {tool name: kind} from the arm's generated catalog. Tool names are the same in every arm."""
    output = subprocess.run(
        [str(_executable(arm_dir(cfg, name) / "env", "python")), "-c", KINDS_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    kinds = json.loads(output)
    path = cfg.data_dir / "kinds.json"
    path.write_text(json.dumps(kinds, indent=1, sort_keys=True), encoding="utf-8")
    return path


def server_command(cfg: BenchConfig, name: str, port: int, run_dir: Path) -> list[str]:
    cmd = [
        str(_executable(arm_dir(cfg, name) / "env", "archicad-server")),
        "--transport",
        "streamable-http",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    if cfg.arms[name].change_log_flag:
        cmd += ["--change-log", str(run_dir / "change-log")]
    return cmd


def start_process(
    cmd: list[str], cwd: Path, env: dict, log_path: Path, stderr_path: Path | None = None, stdin=None
) -> subprocess.Popen:
    """Start cmd in its own process group, so kill_tree can stop it and its children by its PID.
    stdout goes to log_path; stderr goes to stderr_path, or to log_path as well when it is None."""
    group = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    with log_path.open("ab") as out:
        if stderr_path is None:
            return subprocess.Popen(cmd, cwd=cwd, env=env, stdin=stdin, stdout=out, stderr=subprocess.STDOUT, **group)
        with stderr_path.open("ab") as err:
            return subprocess.Popen(cmd, cwd=cwd, env=env, stdin=stdin, stdout=out, stderr=err, **group)


def kill_tree(process) -> None:
    """Stop process and its children through its own PID. Never by image name."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=30)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_port(port: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"nothing listens on port {port}") from None
            time.sleep(0.2)
