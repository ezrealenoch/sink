import shutil
from pathlib import Path
from .base import Transport


class SharedFsTransport(Transport):
    """For agents that share a mounted filesystem (e.g. host <-> VM via shared folder).

    No network involved — peers just read each other's logs from the same path.
    """

    def __init__(self, shared_path: Path, self_id: str):
        self.shared = Path(shared_path) / "events"
        self.self_id = self_id
        self._mtimes: dict[str, float] = {}

    def init_remote(self) -> None:
        self.shared.mkdir(parents=True, exist_ok=True)

    def push(self, log_path: Path) -> None:
        self.shared.mkdir(parents=True, exist_ok=True)
        shutil.copy2(log_path, self.shared / f"{self.self_id}.jsonl")

    def pull(self, events_dir: Path) -> list[str]:
        events_dir = Path(events_dir)
        events_dir.mkdir(parents=True, exist_ok=True)
        changed: list[str] = []
        if not self.shared.exists():
            return changed
        for src in self.shared.glob("*.jsonl"):
            agent_id = src.stem
            if agent_id == self.self_id:
                continue
            mt = src.stat().st_mtime
            if self._mtimes.get(agent_id) == mt:
                continue
            shutil.copy2(src, events_dir / src.name)
            self._mtimes[agent_id] = mt
            changed.append(agent_id)
        return changed
