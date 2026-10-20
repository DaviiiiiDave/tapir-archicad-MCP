"""Append-only JSONL record of every command that changed (or tried to change) the project."""

import json
import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

APP_DIR = "tapir-archicad-mcp"


def default_change_log_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_DIR / "change-log"


@contextmanager
def _exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    """Hold an operating-system lock on lock_path, so several server processes can share one log directory."""
    with lock_path.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


class ChangeLog:
    """One file per UTC day, one JSON object per line."""

    def __init__(self, directory: Path, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.directory = Path(directory)
        self._clock = clock
        self._lock = threading.Lock()

    def record(self, entry: dict) -> Path:
        """Append the entry and return the file it went to. Raises OSError if the file cannot be written.

        Appending is not atomic on Windows, and jobs run on many threads (and several servers may share the
        directory), so every append holds a thread lock and a file lock.
        """
        now = self._clock()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"changes-{now.date().isoformat()}.jsonl"
        line = json.dumps({"time": now.isoformat(), **entry}, ensure_ascii=True, separators=(",", ":"))
        with self._lock, _exclusive_file_lock(self.directory / ".lock"):
            with path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
        return path
