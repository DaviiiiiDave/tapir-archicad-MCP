"""Guarded multiconn access to the Benchmark instance. Every harness call to Archicad goes through here."""

import os
import time
from collections.abc import Callable
from pathlib import Path

from multiconn_archicad import ConnHeader, Port, SoloProjectID


def _connect(port: int):
    return ConnHeader(Port(port))


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _inside(path: Path, root: Path) -> bool:
    return _normalized(path).startswith(_normalized(root) + os.sep)


class BenchArchicad:
    """Refuses every port but the benchmark port, and any instance whose open project is neither the base
    project nor a copy in the data dir. Opens and saves only files in the data dir."""

    def __init__(
        self,
        port: int,
        bench_port: int,
        data_dir: Path,
        base_project: Path,
        connect: Callable[[int], object] = _connect,
        poll_s: float = 0.5,
    ) -> None:
        if port != bench_port:
            raise ValueError(f"port {port} is not the benchmark instance (port {bench_port})")
        self.port = port
        self.data_dir = Path(data_dir)
        self.base_project = Path(base_project)
        self._connect = connect
        self._poll_s = poll_s
        self._header = connect(port)
        path = self.project_path()
        if path is None or not (self._is_base(path) or _inside(path, self.data_dir)):
            raise ValueError(
                f"port {port} has {path or 'no saved project'} open, "
                f"which is neither the base project nor a copy in {self.data_dir}"
            )

    def _is_base(self, path: Path) -> bool:
        return _normalized(path) == _normalized(self.base_project)

    def project_path(self, header=None) -> Path | None:
        archicad_id = (header or self._header).archicad_id
        return Path(archicad_id.projectPath) if isinstance(archicad_id, SoloProjectID) else None

    def tapir(self, command: str, params: dict | None = None) -> dict:
        return self._header.core.post_tapir_command(command, params or {})

    def official(self, command: str, params: dict | None = None) -> dict:
        return self._header.core.post_command(command, params or {})

    def open_project(self, path: Path, timeout_s: float = 600.0) -> None:
        """Open a copy in the data dir and return once the instance reports it as its project."""
        path = Path(path)
        if not _inside(path, self.data_dir):
            raise ValueError(f"{path} is not in the data dir {self.data_dir}")
        if self._is_base(path):
            raise ValueError(f"refusing to open {path}: it is the base project, only copies of it are opened")
        self.tapir("OpenProject", {"projectFilePath": str(path)})
        deadline = time.monotonic() + timeout_s
        while True:
            time.sleep(self._poll_s)
            try:
                header = self._connect(self.port)
                opened = self.project_path(header)
            except Exception:  # noqa: BLE001 - Archicad does not answer while it loads the project
                opened = None
            if opened is not None and _normalized(opened) == _normalized(path):
                self._header = header
                return
            if time.monotonic() > deadline:
                raise TimeoutError(f"{path} did not open on port {self.port}")

    def save_project(self) -> None:
        path = self.project_path()
        if path is None or not _inside(path, self.data_dir) or self._is_base(path):
            raise ValueError(
                f"refusing to save {path}: only copies in {self.data_dir} are saved, never the base project"
            )
        self.tapir("SaveProject", {})
