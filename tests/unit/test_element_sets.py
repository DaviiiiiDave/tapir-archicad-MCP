from datetime import UTC, datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tapir_archicad_mcp.element_sets import SetStore

PROJECT = "/a.pln"
PORT = 19723


def make_store(capacity: int = 64) -> SetStore:
    counter = iter(range(1, 1000))
    return SetStore(
        capacity=capacity,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        new_id=lambda: f"set:{next(counter)}",
    )


def test_create_deduplicates_in_order():
    store = make_store()
    element_set = store.create(PORT, PROJECT, ["b", "a", "b"], {"tool": "test"})
    assert element_set.guids == ("b", "a")
    assert element_set.id == "set:1"
    assert element_set.port == PORT
    assert element_set.project == PROJECT
    assert element_set.origin == {"tool": "test"}
    assert element_set.created_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert store.get(element_set.id, PORT, PROJECT) is element_set


def test_lru_evicts_least_recently_used():
    store = make_store(capacity=2)
    s1 = store.create(PORT, PROJECT, ["a"], {})
    s2 = store.create(PORT, PROJECT, ["b"], {})
    store.get(s1.id, PORT, PROJECT)
    s3 = store.create(PORT, PROJECT, ["c"], {})
    with pytest.raises(ToolError, match="lost when the server restarts"):
        store.get(s2.id, PORT, PROJECT)
    assert store.get(s1.id, PORT, PROJECT) is s1
    assert store.get(s3.id, PORT, PROJECT) is s3


def test_unknown_set_explains_restart():
    store = make_store()
    with pytest.raises(ToolError) as error:
        store.get("set:nope", PORT, PROJECT)
    assert "lost when the server restarts" in str(error.value)
    assert "create it again from its query or definition" in str(error.value)


def test_get_refuses_other_port():
    store = make_store()
    element_set = store.create(PORT, PROJECT, ["a"], {})
    with pytest.raises(ToolError) as error:
        store.get(element_set.id, 19724, PROJECT)
    assert str(PORT) in str(error.value)
    assert "19724" in str(error.value)


def test_get_refuses_changed_project():
    store = make_store()
    element_set = store.create(PORT, "/a.pln", ["a"], {})
    with pytest.raises(ToolError) as error:
        store.get(element_set.id, PORT, "/b.pln")
    message = str(error.value)
    assert "/a.pln" in message
    assert "/b.pln" in message
    assert "Save as" in message
