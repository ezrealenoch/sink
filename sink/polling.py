"""Reusable polling and per-peer cursor primitives.

The original CLI stored one wall-clock timestamp.  That can skip an event when
peer clocks disagree or a transport delivers an older event late.  Version 2
cursors track the highest sequence observed in each append-only peer log.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import os
import tempfile

from .events import Event


LIFECYCLE_TYPES = frozenset({"heartbeat", "started", "stopped"})


@dataclass
class PollCursor:
    peers: dict[str, int] = field(default_factory=dict)
    legacy_ts: float | None = None

    def contains(self, event: Event) -> bool:
        if self.legacy_ts is not None:
            return event.ts <= self.legacy_ts
        return event.seq <= self.peers.get(event.agent_id, -1)

    def advanced(self, events: list[Event], self_id: str) -> "PollCursor":
        peers = dict(self.peers)
        for event in events:
            if event.agent_id == self_id:
                continue
            peers[event.agent_id] = max(peers.get(event.agent_id, -1), event.seq)
        return PollCursor(peers=peers)

    def to_dict(self) -> dict:
        return {"version": 2, "peers": dict(sorted(self.peers.items()))}


def load_cursor(path: Path) -> PollCursor:
    path = Path(path)
    if not path.exists():
        return PollCursor()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError):
        return PollCursor()
    if data.get("version") == 2 and isinstance(data.get("peers"), dict):
        peers: dict[str, int] = {}
        for agent, seq in data["peers"].items():
            if isinstance(agent, str) and isinstance(seq, int) and seq >= 0:
                peers[agent] = seq
        return PollCursor(peers=peers)
    try:
        return PollCursor(legacy_ts=float(data.get("ts", 0.0)))
    except (TypeError, ValueError):
        return PollCursor()


def save_cursor(path: Path, cursor: PollCursor) -> None:
    """Atomically persist a cursor in machine-local state."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cursor.to_dict(), handle, sort_keys=True)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp_name, path)
    finally:
        try:
            Path(tmp_name).unlink()
        except FileNotFoundError:
            pass


def select_events(
    events: list[Event],
    self_id: str,
    cursor: PollCursor,
    type_filter: tuple[str, ...] = (),
    show_all: bool = False,
    include_broadcast: bool = True,
) -> list[Event]:
    selected: list[Event] = []
    for event in events:
        if event.agent_id == self_id or cursor.contains(event):
            continue
        if event.type in LIFECYCLE_TYPES:
            continue
        if type_filter and event.type not in type_filter:
            continue
        if not show_all:
            target = (event.payload or {}).get("for")
            if target is None and not include_broadcast:
                continue
            if target is not None and target != self_id:
                continue
        selected.append(event)
    return selected


def event_dict(event: Event) -> dict:
    return asdict(event)
