from dataclasses import dataclass, asdict, field, fields
from contextlib import contextmanager
from pathlib import Path
import json
import os
import threading
import time
import uuid


# Two-tier serialization for concurrent appends:
#   - In-process: a threading.Lock keyed by file path. Cheap and unconditional.
#   - Cross-process: O_EXCL lockfile with stale-lock breakage so a crashed holder
#     doesn't wedge the system. Portable across Windows / Linux / vboxsf shared
#     mounts in a way `msvcrt.locking` and `fcntl.flock` are not.
_inproc_locks: dict[str, threading.Lock] = {}
_inproc_meta = threading.Lock()


def _inproc_lock_for(path: Path) -> threading.Lock:
    key = str(Path(path).resolve())
    with _inproc_meta:
        if key not in _inproc_locks:
            _inproc_locks[key] = threading.Lock()
        return _inproc_locks[key]


@contextmanager
def _append_lock(target: Path, timeout: float = 30.0, stale_seconds: float = 60.0):
    """Exclusive lock around a file append. Holds across same-process threads
    AND across separate processes on the same machine.

    Without this, `sink run` and `sink emit` racing on the same per-agent log
    both compute the same `next_seq`, producing duplicate seq numbers (best
    case) or lost writes on shared filesystems where O_APPEND atomicity is not
    guaranteed (worst case — vboxsf, SMB).
    """
    inproc = _inproc_lock_for(target)
    inproc.acquire()
    try:
        lock_path = Path(str(target) + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        deadline = time.time() + timeout
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    age = time.time() - lock_path.stat().st_mtime
                    if age > stale_seconds:
                        try:
                            lock_path.unlink()
                        except OSError:
                            pass
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError(f"could not acquire {lock_path} within {timeout}s")
                time.sleep(0.005)
        try:
            yield
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                lock_path.unlink()
            except OSError:
                pass
    finally:
        inproc.release()


class MalformedEventError(ValueError):
    """Raised when a JSONL line cannot be parsed into an Event."""
    def __init__(self, line: str, reason: str):
        super().__init__(f"malformed event ({reason}): {line[:200]}")
        self.line = line
        self.reason = reason


@dataclass
class Event:
    id: str
    seq: int
    ts: float
    agent_id: str
    type: str  # started | heartbeat | stopped | log | task_claim | task_done | respawn | duplicate_detected | duplicate_resolved | bug_report | corrupt_event | <user-defined>
    payload: dict = field(default_factory=dict)
    session_id: str | None = None
    pid: int | None = None
    refs: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> "Event":
        """Parse one JSONL line. Filters unknown keys; raises MalformedEventError on failure.

        Filters unknown keys and validates required fields. A malformed line
        raises MalformedEventError without taking down the caller — readers
        skip and quarantine; writers continue past it.
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            raise MalformedEventError(line, f"json decode: {e}") from e
        if not isinstance(data, dict):
            raise MalformedEventError(line, f"top-level not object: {type(data).__name__}")

        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        for required in ("id", "seq", "ts", "agent_id", "type"):
            if required not in filtered:
                raise MalformedEventError(line, f"missing required field: {required}")
        try:
            return cls(**filtered)
        except TypeError as e:
            raise MalformedEventError(line, f"type error: {e}") from e


def parse_lines(text: str, on_corrupt: str = "skip") -> tuple[list[Event], list[tuple[int, str, str]]]:
    """Parse JSONL text. Returns (events, corruptions).

    on_corrupt:
      - "skip"  -> drop corrupt lines, return them in `corruptions`
      - "raise" -> raise MalformedEventError on first corruption
    """
    events: list[Event] = []
    corruptions: list[tuple[int, str, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            events.append(Event.from_json(line))
        except MalformedEventError as e:
            if on_corrupt == "raise":
                raise
            corruptions.append((lineno, line, e.reason))
    return events, corruptions


class EventLog:
    """Append-only JSONL log scoped to one agent.

    Reads tolerate corrupt lines: a single malformed append does not kill the
    runner. Corrupt lines are skipped and (optionally) recorded to a sidecar
    quarantine file for operator visibility.
    """

    def __init__(self, path: Path, agent_id: str, quarantine: bool = True):
        self.path = Path(path)
        self.agent_id = agent_id
        self.quarantine = quarantine
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _quarantine_path(self) -> Path:
        return self.path.with_name(self.path.stem + ".corrupt.jsonl")

    def _quarantine_line(self, lineno: int, raw: str, reason: str) -> None:
        if not self.quarantine:
            return
        record = {
            "ts": time.time(),
            "agent_id": self.agent_id,
            "source_log": str(self.path),
            "source_lineno": lineno,
            "reason": reason,
            "raw": raw,
        }
        with self._quarantine_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def _next_seq(self) -> int:
        last = self._last_event()
        return 0 if last is None else last.seq + 1

    def _last_event(self) -> Event | None:
        """Return the last successfully-parsed event, skipping any trailing corrupt lines."""
        if self.path.stat().st_size == 0:
            return None
        text = self.path.read_text(encoding="utf-8")
        lines = [l for l in text.splitlines() if l.strip()]
        for lineno in range(len(lines), 0, -1):
            line = lines[lineno - 1]
            try:
                return Event.from_json(line)
            except MalformedEventError as e:
                self._quarantine_line(lineno, line, e.reason)
                continue
        return None

    def append(
        self,
        type: str,
        payload: dict | None = None,
        session_id: str | None = None,
        pid: int | None = None,
        refs: list[str] | None = None,
    ) -> Event:
        # Hold an exclusive lock across read-last-seq AND write so two
        # concurrent appenders cannot both observe the same predecessor and
        # emit the same seq number.
        with _append_lock(self.path):
            ev = Event(
                id=str(uuid.uuid4()),
                seq=self._next_seq(),
                ts=time.time(),
                agent_id=self.agent_id,
                type=type,
                payload=payload or {},
                session_id=session_id,
                pid=pid,
                refs=refs or [],
            )
            with self.path.open("a", encoding="utf-8") as f:
                f.write(ev.to_json() + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass  # vboxsf etc. may not support fsync; the lock still serializes order
        return ev


def read_log(path: Path, on_corrupt: str = "skip") -> list[Event]:
    """Read all events from one agent's log. Corrupt lines are skipped by default."""
    p = Path(path)
    if not p.exists():
        return []
    events, _ = parse_lines(p.read_text(encoding="utf-8"), on_corrupt=on_corrupt)
    return events


def read_all_logs(events_dir: Path, on_corrupt: str = "skip") -> list[Event]:
    events: list[Event] = []
    for p in Path(events_dir).glob("*.jsonl"):
        if p.name.endswith(".corrupt.jsonl"):
            continue
        events.extend(read_log(p, on_corrupt=on_corrupt))
    events.sort(key=lambda e: e.ts)
    return events
