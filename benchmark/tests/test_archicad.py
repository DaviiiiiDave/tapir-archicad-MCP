from pathlib import Path

import pytest
from bench.archicad import BenchArchicad
from multiconn_archicad import SoloProjectID

DATA = Path("C:/bench-data")
BASE = Path("C:/Pruefstand/Benchmark.pln")


class FakeCore:
    def __init__(self, log):
        self.log = log

    def post_tapir_command(self, command, parameters=None, timeout=None):
        self.log.append((command, parameters))
        return {}

    def post_command(self, command, parameters=None, timeout=None):
        self.log.append((command, parameters))
        return {}


class FakeHeader:
    def __init__(self, path, log):
        self.archicad_id = SoloProjectID(projectPath=str(path), projectName=Path(path).stem)
        self.core = FakeCore(log)


class Instance:
    """Hands out one header per connect, each showing the next project path in the list."""

    def __init__(self, *paths):
        self.paths = list(paths)
        self.log: list = []
        self.connects = 0

    def connect(self, port):
        self.connects += 1
        path = self.paths.pop(0) if len(self.paths) > 1 else self.paths[0]
        return FakeHeader(path, self.log)


def test_other_port_is_refused():
    instance = Instance(BASE)

    with pytest.raises(ValueError, match="19723"):
        BenchArchicad(19723, 19725, DATA, BASE, connect=instance.connect)
    assert instance.connects == 0


def test_instance_with_a_foreign_project_is_refused():
    instance = Instance(Path("C:/Users/x/Desktop/Demo_Ziel.pln"))

    with pytest.raises(ValueError, match="Demo_Ziel"):
        BenchArchicad(19725, 19725, DATA, BASE, connect=instance.connect)


def test_open_outside_data_dir_is_refused():
    instance = Instance(BASE)
    ac = BenchArchicad(19725, 19725, DATA, BASE, connect=instance.connect)

    with pytest.raises(ValueError, match="elsewhere"):
        ac.open_project(Path("C:/elsewhere/copy.pln"))
    assert instance.log == []


def test_open_project_waits_for_the_new_project():
    copy = DATA / "runs" / "r1" / "project.pln"
    instance = Instance(BASE, BASE, BASE, copy)
    ac = BenchArchicad(19725, 19725, DATA, BASE, connect=instance.connect, poll_s=0)

    ac.open_project(copy)

    assert instance.log == [("OpenProject", {"projectFilePath": str(copy)})]
    assert ac.project_path() == copy
    assert instance.connects == 4


def test_save_refuses_the_base_project():
    instance = Instance(BASE)
    ac = BenchArchicad(19725, 19725, DATA, BASE, connect=instance.connect)

    with pytest.raises(ValueError, match="base project"):
        ac.save_project()
    assert instance.log == []


def test_save_writes_a_copy():
    copy = DATA / "fixtures" / "german.pln"
    instance = Instance(copy)
    ac = BenchArchicad(19725, 19725, DATA, BASE, connect=instance.connect)

    ac.save_project()

    assert instance.log == [("SaveProject", {})]


INNER_BASE = DATA / "sources" / "german-base.pln"


def test_a_base_project_inside_the_data_dir_is_never_saved():
    instance = Instance(INNER_BASE)
    ac = BenchArchicad(19725, 19725, DATA, INNER_BASE, connect=instance.connect)

    with pytest.raises(ValueError, match="base project"):
        ac.save_project()
    assert instance.log == []


def test_a_base_project_inside_the_data_dir_is_never_opened():
    copy = DATA / "runs" / "r1" / "project.pln"
    instance = Instance(copy)
    ac = BenchArchicad(19725, 19725, DATA, INNER_BASE, connect=instance.connect)

    with pytest.raises(ValueError, match="base project"):
        ac.open_project(INNER_BASE)
    assert instance.log == []
