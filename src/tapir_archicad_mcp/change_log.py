"""Append-only JSONL record of every command that changed (or tried to change) the project."""

import json
import os
import sys
from collections.abc import Callable
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


class ChangeLog:
    """One file per UTC day, one JSON object per line."""

    def __init__(self, directory: Path, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.directory = Path(directory)
        self._clock = clock

    def record(self, entry: dict) -> Path:
        """Append the entry and return the file it went to. Raises OSError if the file cannot be written."""
        now = self._clock()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"changes-{now.date().isoformat()}.jsonl"
        line = json.dumps({"time": now.isoformat(), **entry}, ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        return path
