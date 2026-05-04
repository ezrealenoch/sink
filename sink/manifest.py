from dataclasses import dataclass, field
from pathlib import Path
import os
import uuid
import yaml


@dataclass
class AgentSpec:
    id: str
    role: str = ""
    capabilities: list[str] = field(default_factory=list)
    ssh: str | None = None  # informational; rendered in `sink status` for humans
    sink_dir: str | None = None  # path to .sink on that machine, informational


@dataclass
class TransportSpec:
    type: str  # "ssh" | "git" | "shared-fs"
    config: dict = field(default_factory=dict)


@dataclass
class Manifest:
    project: str
    project_id: str
    transport: TransportSpec
    agents: dict[str, AgentSpec]
    version: int = 2
    heartbeat_seconds: int = 30
    poll_seconds: int = 10

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        agents = {}
        for aid, spec in (data.get("agents") or {}).items():
            spec = spec or {}
            agents[aid] = AgentSpec(
                id=aid,
                role=spec.get("role", ""),
                capabilities=spec.get("capabilities", []) or [],
                ssh=spec.get("ssh"),
                sink_dir=spec.get("sink_dir"),
            )
        t = dict(data["transport"])
        ttype = t.pop("type")

        project_id = data.get("project_id")
        if not project_id:
            raise ValueError(
                f"coordination.yaml at {path} is missing 'project_id'.\n"
                f"Add a stable UUID at the top of the file (the SAME value on every machine):\n\n"
                f"    project_id: {uuid.uuid4()}\n\n"
                f"Then re-run `sink join <agent>` on every machine that participates."
            )

        return cls(
            project=data["project"],
            project_id=project_id,
            version=data.get("version", 2),
            transport=TransportSpec(type=ttype, config=t),
            agents=agents,
            heartbeat_seconds=data.get("heartbeat_seconds", 30),
            poll_seconds=data.get("poll_seconds", 10),
        )

    def save(self, path: Path) -> None:
        out = {
            "project": self.project,
            "project_id": self.project_id,
            "version": self.version,
            "heartbeat_seconds": self.heartbeat_seconds,
            "poll_seconds": self.poll_seconds,
            "transport": {"type": self.transport.type, **self.transport.config},
            "agents": {
                aid: {
                    k: v
                    for k, v in {
                        "role": a.role,
                        "capabilities": a.capabilities,
                        "ssh": a.ssh,
                        "sink_dir": a.sink_dir,
                    }.items()
                    if v
                }
                for aid, a in self.agents.items()
            },
        }
        Path(path).write_text(yaml.safe_dump(out, sort_keys=False), encoding="utf-8")


def machine_local_root() -> Path:
    """Where per-machine sink state lives. Honors XDG_STATE_HOME on Linux."""
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "sink"
    return Path.home() / ".sink"


def local_config_path(project_id: str) -> Path:
    """Per-machine local.yaml lives outside any shared filesystem, keyed by the
    project's stable UUID so multiple projects on one machine don't collide and
    multiple machines on one shared mount don't clobber each other.
    """
    return machine_local_root() / project_id / "local.yaml"


def last_seen_path(project_id: str) -> Path:
    """Per-machine last-seen-event-id-per-peer marker for `sink poll --since-last`."""
    return machine_local_root() / project_id / "last_seen.json"


@dataclass
class LocalConfig:
    """Per-machine state: which agent am I, where is the project's .sink dir on
    THIS machine. Stored at `~/.sink/<project-id>/local.yaml`, never inside the
    project (which may be on a shared filesystem).
    """
    self_id: str
    sink_dir: Path

    @classmethod
    def load(cls, project_id: str) -> "LocalConfig":
        path = local_config_path(project_id)
        if not path.exists():
            raise FileNotFoundError(
                f"No local.yaml for project {project_id} at {path}. "
                f"Run `sink join <agent-id>` on this machine first."
            )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(self_id=data["self"], sink_dir=Path(data["sink_dir"]))

    def save(self, project_id: str) -> Path:
        path = local_config_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"self": self.self_id, "sink_dir": str(self.sink_dir)}),
            encoding="utf-8",
        )
        return path
