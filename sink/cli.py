from pathlib import Path
from datetime import datetime
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import click
import yaml

# Force UTF-8 stdout so Unicode payloads (em-dashes, arrows, etc.) don't crash
# on Windows cp1252 terminals. The 'replace' errors policy means worst-case
# we print '?' for an unrenderable char rather than aborting.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from . import __version__
from .manifest import (
    Manifest, LocalConfig,
    local_config_path, last_seen_path, machine_local_root,
)
from .coordinator import Coordinator, make_transport
from .events import EventLog, read_all_logs
from .state import write_state


SINK_DIR_DEFAULT = ".sink"


def _load_manifest(sink_dir: Path) -> Manifest:
    path = sink_dir / "coordination.yaml"
    if not path.exists():
        raise click.ClickException(f"No coordination.yaml in {sink_dir}. Run `sink init` first or copy one over.")
    return Manifest.load(path)


def _load_local(manifest: Manifest) -> LocalConfig:
    try:
        return LocalConfig.load(manifest.project_id)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))


@click.group()
@click.version_option(__version__)
def main():
    """Sink — multi-agent coordination via append-only event logs."""


@main.command()
@click.argument("project")
@click.option(
    "--transport",
    type=click.Choice(["ssh", "git", "shared-fs"]),
    default="shared-fs",
    show_default=True,
)
@click.option("--dir", "sink_dir", default=SINK_DIR_DEFAULT, show_default=True)
def init(project: str, transport: str, sink_dir: str):
    """Create a new coordination project on this machine."""
    sd = Path(sink_dir)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "events").mkdir(exist_ok=True)
    starter = _starter_manifest(project, transport)
    (sd / "coordination.yaml").write_text(yaml.safe_dump(starter, sort_keys=False))
    click.echo(f"Initialized project '{project}' (id: {starter['project_id']})")
    click.echo(f"  manifest: {(sd / 'coordination.yaml').resolve()}")
    click.echo(f"  transport: {transport}")
    click.echo("Next:")
    click.echo(f"  1. Edit {sd}/coordination.yaml — add agents and transport details")
    click.echo(f"  2. sink join <agent-id>")
    click.echo(f"  3. /loop sink poll --since-last   (in your Claude session)")


@main.command()
@click.argument("agent_id")
@click.option("--dir", "sink_dir", default=SINK_DIR_DEFAULT, show_default=True)
def join(agent_id: str, sink_dir: str):
    """Register THIS machine as <agent_id>.

    Per-machine state is written to ~/.sink/<project-id>/local.yaml so two
    machines sharing a folder do not clobber each other's identity.
    """
    sd = Path(sink_dir).resolve()
    manifest = _load_manifest(sd)
    if agent_id not in manifest.agents:
        click.echo(f"Note: '{agent_id}' is not declared in coordination.yaml yet.")
        if not click.confirm("Continue anyway?", default=True):
            raise click.Abort()
    local = LocalConfig(self_id=agent_id, sink_dir=sd)
    saved_to = local.save(manifest.project_id)
    click.echo(f"Joined as agent '{agent_id}'.")
    click.echo(f"  project_id: {manifest.project_id}")
    click.echo(f"  sink_dir:   {sd}")
    click.echo(f"  local cfg:  {saved_to}")
    click.echo()
    click.echo("Recommended next step in your Claude session:")
    click.echo(f"  /loop sink poll --since-last")


@main.command(name="join-from")
@click.argument("source")
@click.option("--as", "agent_id", default=None, help="Agent id to join as. If omitted, you'll be prompted from the manifest's declared agents.")
@click.option("--dir", "sink_dir", default=SINK_DIR_DEFAULT, show_default=True)
def join_from(source: str, agent_id: str | None, sink_dir: str):
    """Fetch coordination.yaml from <source> and join in one step.

    SOURCE forms:
      /path/to/.sink                     local or shared-mount path
      user@host:/path/to/.sink           scp from a remote host
      git+https://github.com/u/r.git     clone repo, find coordination.yaml inside

    Equivalent to: copy coordination.yaml -> .sink/, then `sink join <agent>`.
    """
    sd = Path(sink_dir).resolve()
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "events").mkdir(exist_ok=True)
    target = sd / "coordination.yaml"

    if source.startswith("git+"):
        _fetch_git(source[4:], target)
    elif "@" in source and ":" in source.split("@", 1)[1]:
        _fetch_scp(source, target)
    else:
        _fetch_local(source, target)

    manifest = Manifest.load(target)
    click.echo(f"Fetched manifest: project '{manifest.project}' (id: {manifest.project_id})")
    click.echo(f"Agents declared: {', '.join(sorted(manifest.agents)) or '(none)'}")
    click.echo()

    if agent_id is None:
        agent_id = click.prompt("Join as which agent")
    if agent_id not in manifest.agents:
        click.echo(f"Note: '{agent_id}' is not declared in coordination.yaml.")
        if not click.confirm("Continue anyway?", default=True):
            raise click.Abort()

    local = LocalConfig(self_id=agent_id, sink_dir=sd)
    saved_to = local.save(manifest.project_id)
    click.echo()
    click.echo(f"Joined '{manifest.project}' as agent '{agent_id}'.")
    click.echo(f"  sink_dir:   {sd}")
    click.echo(f"  local cfg:  {saved_to}")
    click.echo()
    click.echo("Recommended next step in your Claude session:")
    click.echo(f"  /loop sink poll --since-last")


def _fetch_local(source: str, target: Path) -> None:
    src = Path(source)
    if src.is_file():
        shutil.copy2(src, target)
        return
    if src.is_dir():
        candidate = src / "coordination.yaml"
        if not candidate.exists():
            raise click.ClickException(f"No coordination.yaml in {src}")
        shutil.copy2(candidate, target)
        return
    raise click.ClickException(f"Source path does not exist: {src}")


def _fetch_scp(source: str, target: Path) -> None:
    """Parse user@host[:port]:/path/.sink and scp coordination.yaml from inside it."""
    addr, _, path = source.partition(":/")
    if not path:
        raise click.ClickException(f"SCP source must be 'user@host[:port]:/path', got: {source!r}")
    path = "/" + path.rstrip("/")
    port = "22"
    if addr.count(":") == 1:
        host_part, maybe_port = addr.rsplit(":", 1)
        if maybe_port.isdigit():
            addr, port = host_part, maybe_port
    args = ["scp", "-P", port, f"{addr}:{path}/coordination.yaml", str(target)]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise click.ClickException(f"scp failed: {r.stderr.strip() or r.stdout.strip()}")


def _fetch_git(url: str, target: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            ["git", "clone", "--depth=1", url, td],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise click.ClickException(f"git clone failed: {r.stderr.strip() or r.stdout.strip()}")
        candidates = list(Path(td).rglob("coordination.yaml"))
        if not candidates:
            raise click.ClickException(f"No coordination.yaml found in cloned repo {url}")
        if len(candidates) > 1:
            click.echo(f"Note: multiple coordination.yaml in repo; using {candidates[0]}")
        shutil.copy2(candidates[0], target)


@main.command(name="init-remote")
@click.option("--dir", "sink_dir", default=SINK_DIR_DEFAULT, show_default=True)
def init_remote(sink_dir: str):
    """One-time transport setup (mkdir on peers, init bare repo, etc.)."""
    sd = Path(sink_dir)
    manifest = _load_manifest(sd)
    local = _load_local(manifest)
    transport = make_transport(manifest, local)
    transport.init_remote()
    click.echo("Remote initialized.")


@main.command()
@click.option("--dir", "sink_dir", default=SINK_DIR_DEFAULT, show_default=True)
def run(sink_dir: str):
    """Run the heartbeat daemon. Optional — most agents drive coordination from
    `/loop sink poll --since-last` instead."""
    sd = Path(sink_dir)
    manifest = _load_manifest(sd)
    local = _load_local(manifest)
    Coordinator(manifest, local).run()


@main.command()
@click.option("--dir", "sink_dir", default=SINK_DIR_DEFAULT, show_default=True)
def status(sink_dir: str):
    """Print the rolled-up STATE.md (cross-system view)."""
    sd = Path(sink_dir)
    out = sd / "STATE.md"
    write_state(sd / "events", out)
    click.echo(out.read_text(encoding="utf-8"))


@main.command()
@click.argument("type")
@click.option("--payload", "payload_json", default=None, help="JSON object to attach as payload.")
@click.option("--ref", "refs", multiple=True, help="Event id(s) this references. Repeatable.")
@click.option("--dir", "sink_dir", default=SINK_DIR_DEFAULT, show_default=True)
def emit(type: str, payload_json: str | None, refs: tuple[str, ...], sink_dir: str):
    """Append a properly-formed event. Pushes to peers immediately.

    Examples:
      sink emit log --payload '{"message": "kicking off training"}'
      sink emit task_claim --payload '{"task": "T-042", "lease_seconds": 900}'
      sink emit task_done --payload '{"task": "T-042", "files": ["src/x.py"]}' --ref <claim-id>
    """
    sd = Path(sink_dir)
    manifest = _load_manifest(sd)
    local = _load_local(manifest)
    payload = {}
    if payload_json:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"--payload must be valid JSON: {e}")
        if not isinstance(payload, dict):
            raise click.ClickException("--payload must be a JSON object")

    log = EventLog(sd / "events" / f"{local.self_id}.jsonl", local.self_id)
    ev = log.append(type=type, payload=payload, refs=list(refs) or None)
    transport = make_transport(manifest, local)
    transport.push(log.path)
    click.echo(f"emitted {type} (seq={ev.seq}, id={ev.id})")


@main.command()
@click.argument("message", nargs=-1, required=True)
@click.option("--dir", "sink_dir", default=SINK_DIR_DEFAULT, show_default=True)
def log(message: tuple[str, ...], sink_dir: str):
    """Shorthand for `sink emit log --payload '{"message": "..."}'`."""
    sd = Path(sink_dir)
    manifest = _load_manifest(sd)
    local = _load_local(manifest)
    msg = " ".join(message)
    elog = EventLog(sd / "events" / f"{local.self_id}.jsonl", local.self_id)
    ev = elog.append(type="log", payload={"message": msg})
    transport = make_transport(manifest, local)
    transport.push(elog.path)
    click.echo(f"logged (seq={ev.seq})")


# ---------- the loop primitive ----------

_QUIET_TIERS = (
    # (max_silence_seconds, suggested_next_poll_seconds, label)
    (300,    90,   "active back-and-forth (peer event in last 5m)"),
    (1800,   300,  "warm (peer event in last 30m)"),
    (7200,   900,  "moderate (peer event in last 2h)"),
    (28800,  1800, "quiet (silent > 2h)"),
    (86400,  3600, "deep quiet (silent > 8h)"),
)


def _suggest_next_poll(events_dir: Path, self_id: str) -> tuple[int, str]:
    """Return (seconds_until_next_poll, human_label) based on recent peer activity."""
    now = time.time()
    last_peer_ts = 0.0
    for ev in read_all_logs(events_dir):
        if ev.agent_id == self_id:
            continue
        if ev.type == "heartbeat":
            continue  # heartbeats from peers shouldn't drive our cadence
        if ev.ts > last_peer_ts:
            last_peer_ts = ev.ts
    if last_peer_ts == 0.0:
        return 1800, "no peer activity yet"
    elapsed = now - last_peer_ts
    for max_silence, next_poll, label in _QUIET_TIERS:
        if elapsed < max_silence:
            return next_poll, label
    return 7200, "very deep quiet (silent > 24h)"


SUMMARIZE_THRESHOLD = 20  # auto-summarize when this many new events would render


@main.command()
@click.option("--since-last", "since_last", is_flag=True, default=False,
              help="Use the persistent last-seen marker (recommended for /loop).")
@click.option("--since", "since", type=float, default=None,
              help="Show events newer than this unix timestamp (overrides --since-last).")
@click.option("--type", "type_filter", multiple=True,
              help="Only show events of this type. Repeatable. e.g. --type task --type ack")
@click.option("--all", "show_all", is_flag=True, default=False,
              help="Show ALL events (no addressing filter, no auto-summary).")
@click.option("--dir", "sink_dir", default=SINK_DIR_DEFAULT, show_default=True)
def poll(since_last: bool, since: float | None, type_filter: tuple[str, ...],
         show_all: bool, sink_dir: str):
    """The /loop primitive. Pulls peer events, prints anything addressed to you
    since last poll, suggests a next-poll interval based on peer activity.

    Recommended usage:  /loop sink poll --since-last

    Auto-summarizes when there are many new events (e.g. after a long absence).
    Use --all to force the full list.
    """
    sd = Path(sink_dir)
    manifest = _load_manifest(sd)
    local = _load_local(manifest)

    transport = make_transport(manifest, local)
    transport.pull(sd / "events")

    marker_path = last_seen_path(manifest.project_id)
    cutoff = since if since is not None else _load_marker(marker_path) if since_last else 0.0

    events = read_all_logs(sd / "events")
    new_events = []
    for ev in events:
        if ev.ts <= cutoff: continue
        if ev.agent_id == local.self_id: continue
        if ev.type in ("heartbeat", "started", "stopped"): continue
        if type_filter and ev.type not in type_filter: continue
        if not show_all:
            for_field = (ev.payload or {}).get("for")
            if for_field is not None and for_field != local.self_id:
                continue
        new_events.append(ev)

    next_poll, label = _suggest_next_poll(sd / "events", local.self_id)

    click.echo(f"sink poll [project: {manifest.project}]  [agent: {local.self_id}]  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    click.echo()

    if not new_events:
        click.echo("No new events for you.")
    elif len(new_events) > SUMMARIZE_THRESHOLD and not show_all:
        _render_digest(new_events, local.self_id)
    else:
        _render_full(new_events, local.self_id)

    click.echo()
    click.echo(f"NEXT POLL: in {next_poll}s ({label})")

    if since_last and events:
        _save_marker(marker_path, max(ev.ts for ev in events))


def _render_full(new_events, self_id):
    n_addr = sum(1 for e in new_events if (e.payload or {}).get("for") == self_id)
    click.echo(f"NEW EVENTS ({len(new_events)} new, {n_addr} addressed to you):")
    click.echo()
    for ev in new_events:
        _print_event(ev)


def _render_digest(new_events, self_id, detail_count: int = 6):
    from collections import Counter
    n_addr = sum(1 for e in new_events if (e.payload or {}).get("for") == self_id)
    by_type = Counter(e.type for e in new_events)
    by_agent = Counter(e.agent_id for e in new_events)
    click.echo(f"NEW EVENTS ({len(new_events)} new, {n_addr} addressed to you) — backlog summarized:")
    click.echo()
    click.echo(f"  By agent: {', '.join(f'{a}={c}' for a, c in by_agent.most_common())}")
    click.echo(f"  By type:  {', '.join(f'{t}={c}' for t, c in by_type.most_common(8))}")
    click.echo()
    click.echo(f"  Most recent {detail_count}:")
    for ev in new_events[-detail_count:]:
        _print_event(ev)
    click.echo()
    click.echo("  (full list: re-run with --all, or filter with --type <type>)")


def _print_event(ev):
    ts = datetime.fromtimestamp(ev.ts).strftime("%Y-%m-%d %H:%M:%S")
    target = (ev.payload or {}).get("for")
    arrow = f" -> {target}" if target else ""
    click.echo(f"  [{ts}] {ev.agent_id} {ev.type}{arrow}  (seq={ev.seq}, id={ev.id})")
    payload = ev.payload or {}
    for k in ("summary", "message", "title", "task", "note"):
        if k in payload:
            val = str(payload[k])
            if len(val) > 200: val = val[:200] + "..."
            click.echo(f"    {k}: {val}")
            return
    if payload:
        val = json.dumps(payload, sort_keys=True)
        if len(val) > 200: val = val[:200] + "..."
        click.echo(f"    payload: {val}")
    if ev.refs:
        click.echo(f"    refs: {', '.join(ev.refs)}")


def _load_marker(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        return float(json.loads(path.read_text(encoding="utf-8")).get("ts", 0.0))
    except (json.JSONDecodeError, ValueError, OSError):
        return 0.0


def _save_marker(path: Path, ts: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": ts}), encoding="utf-8")


# ---------- diagnostics ----------

@main.command()
@click.option("--dir", "sink_dir", default=SINK_DIR_DEFAULT, show_default=True)
def doctor(sink_dir: str):
    """Diagnose common setup problems on this machine."""
    import importlib
    import sink as sink_pkg

    sd = Path(sink_dir).resolve()
    issues = 0

    def fail(msg):
        nonlocal issues
        issues += 1
        click.echo(f"  [fail] {msg}")

    def warn(msg):
        click.echo(f"  [warn] {msg}")

    def ok(msg):
        click.echo(f"  [ok] {msg}")

    click.echo(f"sink CLI v{__version__} installed at {Path(sink_pkg.__file__).parent}")

    click.echo("\nManifest:")
    manifest_path = sd / "coordination.yaml"
    if not manifest_path.exists():
        fail(f"no coordination.yaml at {manifest_path}")
        click.echo(f"\n{issues} issue(s) found.")
        raise SystemExit(1)
    try:
        manifest = Manifest.load(manifest_path)
        ok(f"loaded {manifest_path}")
        ok(f"project: {manifest.project} (id: {manifest.project_id})")
        ok(f"transport: {manifest.transport.type}")
        ok(f"agents declared: {', '.join(sorted(manifest.agents))}")
    except ValueError as e:
        fail(str(e))
        raise SystemExit(1)

    click.echo("\nMachine-local config:")
    cfg_path = local_config_path(manifest.project_id)
    if not cfg_path.exists():
        fail(f"no local.yaml at {cfg_path}  (run `sink join <agent-id>`)")
    else:
        try:
            local = LocalConfig.load(manifest.project_id)
            ok(f"this machine is agent '{local.self_id}' (cfg: {cfg_path})")
            if local.self_id not in manifest.agents:
                warn(f"'{local.self_id}' is not declared in coordination.yaml")
            ok(f"sink_dir: {local.sink_dir}")
            log_path = local.sink_dir / "events" / f"{local.self_id}.jsonl"
            if log_path.exists():
                ok(f"event log: {log_path} ({log_path.stat().st_size} bytes)")
            else:
                warn(f"event log does not exist yet: {log_path}")
        except (FileNotFoundError, ValueError) as e:
            fail(str(e))

    click.echo("\nInstall sanity:")
    needed = ["init", "join", "run", "emit", "log", "status", "poll", "doctor", "whoami"]
    missing = [c for c in needed if c not in main.commands]
    if missing:
        fail(f"CLI missing subcommands: {', '.join(missing)} — run install.sh / install.ps1 to refresh")
    else:
        ok("CLI has the full command set (post-trim release)")

    click.echo()
    if issues:
        click.echo(f"{issues} issue(s) — see [fail]/[warn] above.")
        raise SystemExit(1)
    click.echo("All checks passed.")


@main.command(name="bootstrap-instructions")
@click.option("--source", "source", required=True,
              help="The sink source another agent should join (path / user@host:/path / git+url).")
@click.option("--as", "agent_id", default=None,
              help="Suggested agent id for the new instance.")
def bootstrap_instructions(source: str, agent_id: str | None):
    """Print a paste-ready onboarding paragraph for a fresh Claude session.

    Run this on the machine that already has the project; copy the output and
    paste it into a Claude Code session on the new machine that should join.
    The new Claude reads the SKILL.md and runs the listed commands.

    Example:  sink bootstrap-instructions --source user@my-vm:/home/user/proj/.sink --as gpu-box
    """
    join_cmd = f"sink join-from {source}"
    if agent_id:
        join_cmd += f" --as {agent_id}"

    instructions = f"""# Paste this into a fresh Claude Code session on the target machine:

I want this Claude session to join my Sink coordination project as a peer agent.
Use the **sink** skill to handle this.

## Step 1 — make sure sink is installed on this machine

If `sink --version` already works, skip this step. Otherwise install it:

```
# If you have the sink repo cloned locally:
./install.sh        # Linux or macOS
.\\install.ps1       # Windows (PowerShell)

# Or via pip if a published package is available:
pip install sink-coord
```

Both install paths are idempotent — safe to re-run.

## Step 2 — join the project and start the loop

Run these commands in sequence:

```
{join_cmd}
sink doctor
/loop sink poll --since-last
```

`sink join-from` will fetch the coordination manifest from the source above,
prompt for which agent id to claim ({"already set to '" + agent_id + "'" if agent_id else "you'll pick from the manifest's declared list"}),
and write the per-machine local config. `sink doctor` confirms the install
is clean. `/loop sink poll --since-last` is the canonical wake mechanism — the
loop checks the sink on an adaptive cadence (snappy when peers are active,
backed off when quiet) and surfaces events addressed to you.

## Step 3 — etiquette

When the loop fires and there's nothing addressed to you, **don't emit
anything**. Just let the next tick fire. Emit only when you have substantive
output (results, replies, state changes). Use the CLI for emits — never
hand-edit the JSONL log files.

That's it.
"""
    click.echo(instructions)


@main.command()
@click.option("--dir", "sink_dir", default=SINK_DIR_DEFAULT, show_default=True)
def whoami(sink_dir: str):
    """Show this machine's identity in the project."""
    sd = Path(sink_dir)
    manifest = _load_manifest(sd)
    try:
        local = LocalConfig.load(manifest.project_id)
    except FileNotFoundError:
        click.echo(f"Not joined to project {manifest.project} ({manifest.project_id}).")
        click.echo(f"Expected: {local_config_path(manifest.project_id)}")
        return
    click.echo(f"project:    {manifest.project}")
    click.echo(f"project_id: {manifest.project_id}")
    click.echo(f"agent:      {local.self_id}")
    click.echo(f"sink_dir:   {local.sink_dir}")
    click.echo(f"local cfg:  {local_config_path(manifest.project_id)}")


def _starter_manifest(project: str, transport: str) -> dict:
    base: dict = {
        "project": project,
        "project_id": str(uuid.uuid4()),
        "version": 2,
        "heartbeat_seconds": 30,
        "poll_seconds": 10,
    }
    if transport == "ssh":
        base["transport"] = {
            "type": "ssh",
            "remote_path": "/home/user/sink/.sink",
            "peers": {"example-peer": "user@host:22"},
        }
    elif transport == "git":
        base["transport"] = {
            "type": "git",
            "remote": "user@hub:~/sink-repos/" + project + ".git",
            "branch": "main",
        }
    else:
        base["transport"] = {
            "type": "shared-fs",
            "path": "shared",   # relative to each machine's sink_dir
        }
    base["agents"] = {
        "example-agent": {
            "role": "describe-what-this-agent-does",
            "capabilities": [],
        }
    }
    return base


if __name__ == "__main__":
    main()
