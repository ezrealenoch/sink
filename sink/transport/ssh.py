import subprocess
from pathlib import Path
from .base import Transport


def _parse_addr(addr: str) -> tuple[str, int]:
    """'user@host' or 'user@host:port' -> (user@host, port)."""
    if addr.count(":") == 1 and not addr.startswith("["):
        host, port = addr.rsplit(":", 1)
        if port.isdigit():
            return host, int(port)
    return addr, 22


class SshTransport(Transport):
    """Default transport. Each agent rsyncs its own log file to every peer's events dir.

    Config (in coordination.yaml under transport):
      remote_path: /home/<user>/<project>/.sink   # path to .sink on every peer (uniform for simplicity)
      peers:
        gpu-box: <user>@<gpu-host>:22
        vm:      <user>@<vm-host>:22

    The current agent's id is filtered out — you only push/pull peers, not yourself.
    """

    def __init__(self, peers: dict[str, str], self_id: str, remote_path: str):
        self.peers = {aid: addr for aid, addr in peers.items() if aid != self_id}
        self.self_id = self_id
        self.remote_path = remote_path.rstrip("/")

    def init_remote(self) -> None:
        for addr in self.peers.values():
            host, port = _parse_addr(addr)
            subprocess.run(
                ["ssh", "-p", str(port), host, f"mkdir -p {self.remote_path}/events"],
                check=True,
            )

    def push(self, log_path: Path) -> None:
        for addr in self.peers.values():
            host, port = _parse_addr(addr)
            dst = f"{host}:{self.remote_path}/events/{self.self_id}.jsonl"
            subprocess.run(
                ["rsync", "-az", "-e", f"ssh -p {port}", str(log_path), dst],
                check=False,
            )

    def pull(self, events_dir: Path) -> list[str]:
        events_dir = Path(events_dir)
        events_dir.mkdir(parents=True, exist_ok=True)
        changed: list[str] = []
        for agent_id, addr in self.peers.items():
            host, port = _parse_addr(addr)
            src = f"{host}:{self.remote_path}/events/{agent_id}.jsonl"
            local = events_dir / f"{agent_id}.jsonl"
            r = subprocess.run(
                [
                    "rsync", "-az", "-e", f"ssh -p {port}",
                    "--itemize-changes", src, str(local),
                ],
                capture_output=True, text=True,
            )
            # rsync prints itemize lines only when something actually transferred
            if r.returncode == 0 and any(
                line and not line.startswith(("sending", "sent", "total", "receiving"))
                for line in r.stdout.splitlines()
            ):
                changed.append(agent_id)
        return changed
