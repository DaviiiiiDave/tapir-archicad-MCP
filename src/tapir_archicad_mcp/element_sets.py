"""In-memory snapshots of element GUIDs, so element lists never have to travel through the model."""

import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from mcp.server.mcpserver.exceptions import ToolError


@dataclass(frozen=True)
class ElementSet:
    id: str
    port: int
    project: str
    guids: tuple[str, ...]
    created_at: datetime
    origin: dict


class SetStore:
    """Least-recently-used store of element sets, each bound to the Archicad port and project it was made in."""

    def __init__(
        self,
        capacity: int = 64,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], str] = lambda: f"set:{secrets.token_hex(4)}",
    ) -> None:
        self._capacity = capacity
        self._clock = clock
        self._new_id = new_id
        self._sets: OrderedDict[str, ElementSet] = OrderedDict()
        self._lock = threading.Lock()

    def create(self, port: int, project: str, guids: Iterable[str], origin: dict) -> ElementSet:
        """Store the GUIDs, de-duplicated in first-seen order, and evict the least recently used set if full."""
        element_set = ElementSet(
            id=self._new_id(),
            port=port,
            project=project,
            guids=tuple(dict.fromkeys(guids)),
            created_at=self._clock(),
            origin=origin,
        )
        with self._lock:
            self._sets[element_set.id] = element_set
            while len(self._sets) > self._capacity:
                self._sets.popitem(last=False)
        return element_set

    def __len__(self) -> int:
        with self._lock:
            return len(self._sets)

    def get(self, set_id: str, port: int, project: str) -> ElementSet:
        """Return the set and mark it as recently used. Raises ToolError if it is unknown or bound elsewhere."""
        with self._lock:
            element_set = self._sets.get(set_id)
            if element_set is None:
                raise ToolError(
                    f"Unknown set '{set_id}'. Sets are lost when the server restarts and the least recently used "
                    f"are dropped when the store is full; create it again from its query or definition."
                )
            if element_set.port != port:
                raise ToolError(
                    f"Set '{set_id}' was created on Archicad port {element_set.port}, not port {port}. "
                    f"Use port {element_set.port}, or create the set again on port {port}."
                )
            if element_set.project != project:
                raise ToolError(
                    f"Set '{set_id}' was created for project '{element_set.project}', but port {port} now has "
                    f"'{project}' open (for example after Save as or opening another project). "
                    f"Element GUIDs are not valid across projects; create the set again."
                )
            self._sets.move_to_end(set_id)
            return element_set
