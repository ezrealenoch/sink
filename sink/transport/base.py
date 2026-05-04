from abc import ABC, abstractmethod
from pathlib import Path


class Transport(ABC):
    """Pluggable sync backend.

    All transports speak the same minimal protocol:
      - push(local_log)  : make this agent's events visible to peers
      - pull(events_dir) : copy peer logs into events_dir, return changed agent ids
      - init_remote()    : one-time setup (create remote dir, init bare repo, etc.)
    """

    @abstractmethod
    def init_remote(self) -> None: ...

    @abstractmethod
    def push(self, log_path: Path) -> None: ...

    @abstractmethod
    def pull(self, events_dir: Path) -> list[str]: ...
