"""Read-only projection of all event logs into a human-readable STATE.md.

Regenerated on demand, never written to by coordinating agents — no conflicts.
"""
from datetime import datetime
from pathlib import Path

from .events import read_all_logs


def _open_tasks_per_agent(events) -> dict[str, list[tuple[str, float]]]:
    """Return {agent_id: [(task_id, claimed_at), ...]} for tasks claimed but not done.

    Pairs `task_claim` with `task_done` events by `payload.task`. The task_done
    can come from any agent (peers ack each other), so we clear from every
    agent's open list when a done lands.
    """
    open_tasks: dict[str, list[tuple[str, float]]] = {}
    for ev in events:
        payload = ev.payload or {}
        task = payload.get("task")
        if not task:
            continue
        if ev.type == "task_claim":
            open_tasks.setdefault(ev.agent_id, []).append((task, ev.ts))
        elif ev.type == "task_done":
            for agent in list(open_tasks):
                open_tasks[agent] = [(t, ts) for t, ts in open_tasks[agent] if t != task]
                if not open_tasks[agent]:
                    open_tasks.pop(agent)
    return open_tasks


def render_state(events_dir: Path, recent: int = 50) -> str:
    events = read_all_logs(events_dir)
    out: list[str] = []
    out.append("# Coordination state")
    out.append(f"_Generated {datetime.utcnow().isoformat(timespec='seconds')}Z_\n")

    last_by_agent: dict[str, dict] = {}
    for ev in events:
        if ev.type in ("started", "heartbeat", "stopped"):
            last_by_agent[ev.agent_id] = {
                "ts": ev.ts, "type": ev.type,
                "session": ev.session_id, "pid": ev.pid,
            }

    open_tasks = _open_tasks_per_agent(events)

    out.append("## Agents\n")
    if not last_by_agent:
        out.append("_No agents have reported in yet._\n")
    else:
        for aid in sorted(last_by_agent):
            info = last_by_agent[aid]
            ts = datetime.fromtimestamp(info["ts"]).isoformat(timespec="seconds")
            session_short = (info["session"] or "n/a")[:12]
            out.append(
                f"- **{aid}** — {info['type']} @ {ts} "
                f"(session `{session_short}`, pid `{info['pid']}`)"
            )
            for task, claimed in open_tasks.get(aid, []):
                age = (datetime.now().timestamp() - claimed) / 60
                out.append(f"    - currently working on: `{task}` (claimed {age:.0f}m ago)")
        out.append("")

    out.append(f"## Last {recent} events\n")
    for ev in events[-recent:]:
        ts = datetime.fromtimestamp(ev.ts).strftime("%Y-%m-%d %H:%M:%S")
        suffix = f" {ev.payload}" if ev.payload else ""
        out.append(f"- `{ts}` **{ev.agent_id}** `{ev.type}`{suffix}")

    return "\n".join(out) + "\n"


def write_state(events_dir: Path, out_path: Path) -> None:
    Path(out_path).write_text(render_state(events_dir), encoding="utf-8")
