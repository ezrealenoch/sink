"""The `sink run` daemon: heartbeat + push/pull on an interval.

Optional. The primary coordination flow is now `/loop sink poll --since-last`,
which runs ad-hoc on each Claude tick. `sink run` only exists for autonomous
agents that want to advertise liveness via heartbeats without being driven by
a Claude `/loop`.
"""
import os
import time
from pathlib import Path

from .events import EventLog
from .manifest import Manifest, LocalConfig
from .session import current_session_id
from .transport import Transport, SharedFsTransport, SshTransport, GitTransport


def make_transport(manifest: Manifest, local: LocalConfig) -> Transport:
    t = manifest.transport
    cfg = t.config
    if t.type == "shared-fs":
        p = Path(cfg["path"])
        if not p.is_absolute():
            # Relative paths resolve against the per-machine sink_dir so the
            # same manifest works on Windows and Linux peers.
            p = local.sink_dir / p
        return SharedFsTransport(p, local.self_id)
    if t.type == "ssh":
        return SshTransport(cfg.get("peers", {}), local.self_id, cfg["remote_path"])
    if t.type == "git":
        repo_dir = Path(cfg.get("repo_dir") or local.sink_dir)
        return GitTransport(repo_dir, cfg["remote"], local.self_id, cfg.get("branch", "main"))
    raise ValueError(f"unknown transport: {t.type}")


class Coordinator:
    """Emit heartbeats, push my log, pull peer logs. No triggers, no respawn."""

    def __init__(self, manifest: Manifest, local: LocalConfig):
        self.manifest = manifest
        self.local = local
        self.events_dir = local.sink_dir / "events"
        self.log = EventLog(self.events_dir / f"{local.self_id}.jsonl", local.self_id)
        self.transport = make_transport(manifest, local)
        self.session_id = current_session_id()
        self.pid = os.getpid()

    def start(self) -> None:
        self.transport.pull(self.events_dir)
        self.log.append("started", session_id=self.session_id, pid=self.pid)
        self.transport.push(self.log.path)

    def heartbeat(self) -> None:
        self.log.append("heartbeat", session_id=self.session_id, pid=self.pid)
        self.transport.push(self.log.path)

    def stop(self, reason: str = "graceful") -> None:
        self.log.append(
            "stopped", payload={"reason": reason},
            session_id=self.session_id, pid=self.pid,
        )
        self.transport.push(self.log.path)

    def poll(self) -> list[str]:
        return self.transport.pull(self.events_dir)

    def run(self) -> None:
        self.start()
        try:
            last_hb = time.time()
            while True:
                time.sleep(self.manifest.poll_seconds)
                changed = self.poll()
                if changed:
                    print(f"[sink] new events from: {', '.join(changed)}")
                if time.time() - last_hb >= self.manifest.heartbeat_seconds:
                    self.heartbeat()
                    last_hb = time.time()
        except KeyboardInterrupt:
            self.stop("interrupted")
