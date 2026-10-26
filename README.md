# Sink

> **Multi-agent coordination through shared, append-only event logs.**
> Claude Code can poll with `/loop`; Codex terminals can use Sink's optional
> Windows/macOS scheduler bridge. Logs sync through shared filesystem, SSH, or
> git while the event protocol stays independent of the agent runtime.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

---

## The problem

Coding-agent terminals do not all provide the same scheduling primitive. Claude
Code has `/loop`, while [Codex CLI has no Scheduled management interface](https://learn.chatgpt.com/docs/automations). If you
want agents on different machines to coordinate — *"the GPU agent should
rebuild the corpus while the host indexes it"* — each agent still needs a
reliable way to poll shared coordination state.

People solve this ad-hoc with a `CHANGELOG.md` everyone tails, or with `/loop
check the changelog every 30 minutes`. It works but it's noisy, conflict-prone
when two agents write at once, and there's no convention for who's saying what
to whom.

Sink formalizes the pattern:

- **Per-agent append-only event logs** — no read/write conflicts
- **Cross-process file lock** — concurrent appends from `/loop` ticks and
  background scripts can't collide on the same `seq` number
- **Pluggable transport** — shared-fs, SSH/rsync, or git
- **Adaptive polling** — `sink poll` suggests the next interval based on peer
  activity (sub-2-minutes when there's chatter, 30-60 minutes when quiet)
- **Codex terminal loop** — durable delivery through Windows Task Scheduler or
  macOS `launchd`, with pause/resume/status/uninstall controls
- **Machine-readable polling** — `sink poll --format json` for integrations
- **Per-peer cursors** — sequence-based progress is robust to clock skew
- **Quiet-tick discipline** — agents are taught to stay silent when there's
  nothing to say, instead of spamming "still here" pings

---

## The mental model

```
              ┌─────────────────────────┐
              │  scheduled sink poll …  │   ← Claude /loop or Codex bridge
              └────────────┬────────────┘
                           │
                ┌──────────▼───────────┐
                │  read peer logs      │
                │  filter to addressed │
                │  events for me       │
                │  suggest next poll   │
                └──────────┬───────────┘
                           │
                  things addressed
                       to me?
                       /     \
                     yes      no
                     /         \
              ┌─────▼────┐   ┌──▼─────────┐
              │ act, then│   │ stay silent│
              │ sink emit│   │ (no log    │
              │ a reply  │   │  spam)     │
              └──────────┘   └────────────┘
```

Each agent owns one log file (`events/<self>.jsonl`). The transport layer
syncs every agent's log to every peer. Reads are local (cheap); only writes go
through transport.

---

## Quick start

### Joining an existing project (the headline flow)

After the Sink CLI is installed:

```bash
sink join-from <source> --as <agent>
sink doctor
```

Choose the wake mechanism for the agent runtime.

Claude Code:

```text
/loop sink poll --since-last
```

Codex terminal on Windows or macOS:

```bash
sink loop install --interval 300
sink loop status
```

Sink detects `CODEX_THREAD_ID`/`CODEX_SESSION_ID` when installed from an active
Codex session; `--session <id-or-exact-name>` is available when detection is
not possible. `--delivery auto` uses `codex queue` when that command is available, so a live
terminal receives the prompt. Otherwise it uses the documented
[`codex exec resume`](https://learn.chatgpt.com/docs/non-interactive-mode) non-interactive path. The lightweight poll does not invoke
Codex on quiet ticks.

`<source>` can be:

- A local or shared-mount path: `/media/shared/myproject/.sink`
- An SSH spec: `user@vm-host:/home/user/myproject/.sink`
- A git URL: `git+https://github.com/me/myproject-coord.git`

For onboarding *another* machine without typing the join command yourself,
generate a paste-ready instruction block:

```bash
sink bootstrap-instructions --source <source> --as <agent>
# add --runtime codex for a Codex terminal target
```

Copy the output into a fresh Claude Code or Codex session on the target
machine. The agent reads the skill, installs Sink if needed, joins, and starts
the appropriate loop.

### Starting a new project

```bash
sink init my-project --transport shared-fs
# edit .sink/coordination.yaml — list agents, configure transport
sink join host
# then install the wake mechanism shown above
```

### Posting events

```bash
# Plain message:
sink log "starting nightly run"

# Structured, addressed to a peer:
sink emit task --payload '{"for":"vm","task":"rebuild dependency X","priority":"high"}'

# Reply / acknowledge:
sink emit ack --payload '{"for":"host","task":"T-042","status":"started"}'
```

The `type` field is a free string — pick whatever fits the substance (`task`,
`ack`, `cell_done`, `findings`, `proposal`, etc.). Use `sink emit` / `sink log`,
**never** hand-edit the JSONL files — the wire format has required fields and
the CLI's lock keeps `seq` numbers monotonic across concurrent writers.

### Reading the cross-system view

```bash
sink poll --since-last                      # the /loop primitive
sink poll --since-last --type task --type ack   # filter to actionable types
sink status                                 # full STATE.md: who's live, who's working on what
```

`sink poll` auto-summarizes when there's a large backlog (returning agent
after a long absence) and prints a `NEXT POLL: in <N>s` hint for `/loop`
self-pacing.

For integrations, request a stable JSON object instead of parsing terminal
text:

```bash
sink poll --since-last --format json
```

### Codex terminal loop

The Codex bridge stores its cursor, pending delivery envelope, and logs in
machine-local Sink state. It writes the pending envelope before waking Codex;
if delivery fails, the next tick retries the same delivery and does not advance
the cursor.

```bash
sink loop install --interval 300
sink loop status
sink loop pause
sink loop resume
sink loop run-once        # test immediately
sink loop uninstall       # removes the schedule, preserves delivery state
sink loop uninstall --purge-state
```

Use `--addressed-only` to ignore broadcasts, and repeat `--type` to wake Codex
only for actionable event classes such as `task`, `review_request`, or
`review_finding`.

Delivery is at-least-once. A process crash immediately after Codex accepts a
message can cause a duplicate; every prompt includes a stable delivery id and
exact event ids so the agent can recognize it. Sink events are coordination
data, not authorization: they cannot grant permission to publish, deploy,
spend money, expose secrets, or expand the user's current task.

---

## Transports

Pick the one that matches your topology.

| Transport   | When                                                       | Requires                                |
|-------------|------------------------------------------------------------|-----------------------------------------|
| `shared-fs` | All agents see the same mounted folder (NFS / VBox / SMB)  | A common path; nothing else             |
| `ssh`       | Each agent can SSH the others directly                     | SSH key auth + rsync on every peer      |
| `git`       | Asymmetric reachability (one peer behind NAT)              | A bare git repo on a hub or GitHub      |

`git` works without GitHub — point at a self-hosted bare repo over SSH:

```yaml
transport:
  type: git
  remote: user@hub:~/sink-repos/myproject.git
  branch: main
```

Every agent only needs *outbound* network to that remote; no peer-to-peer
reachability required.

---

## Features at a glance

- **`sink init / join / join-from`** — set up a project, register a machine, or fetch a remote manifest in one step
- **`sink poll`** — adaptive interval hint, backlog summaries, per-peer cursors, type filters, and JSON output
- **`sink loop`** — install, inspect, pause, resume, test, or remove the Windows/macOS Codex bridge
- **`sink emit / log`** — schema-validated event posting; lock-protected
- **`sink status`** — rolled-up STATE.md showing every agent, last-seen, currently-working-on (derived from `task_claim` / `task_done` events)
- **`sink doctor`** — diagnoses install, manifest, and local-state issues
- **`sink bootstrap-instructions`** — generates paste-ready Claude or Codex onboarding instructions
- **`sink whoami`** — confirms this machine's identity in the project
- **`sink run`** *(optional)* — long-running heartbeat daemon for autonomous agents

The CLI remains intentionally small and every command has `--help`. The skill
that ships with this repo (`skills/sink/SKILL.md`) routes between Claude and
Codex without turning Sink into an agent runtime.

---

## Install

### CLI and agent skills (recommended)

```bash
git clone https://github.com/ezrealenoch/sink.git
cd sink
./install.sh        # Linux / macOS
.\install.ps1       # Windows (PowerShell)
```

The script does `pip install --force-reinstall --no-deps -e .` and installs
`skills/sink/` for both Claude Code (`~/.claude/skills/sink/`) and Codex
(`$CODEX_HOME/skills/sink/`, or `~/.codex/skills/sink/`). Restart the agent
terminal afterward.

### Via the Claude Code plugin marketplace

This repo also ships `.claude-plugin/` manifests so it can be added as a
plugin marketplace:

```
/plugin marketplace add ezrealenoch/sink
/plugin install sink@sink
```

You'll still need the `sink` CLI on PATH for the skill to actually do
anything: `pip install sink-coord` or `pip install -e .` from a clone.

### Manual install

```bash
pip install -e .
# copy or link skills/sink into ~/.claude/skills/sink and/or ~/.codex/skills/sink
```

---

## What this is *not*

Honesty about scope:

- **Not a real-time messaging system.** The "loop poll" model has a floor of
  ~60 seconds latency by design. If you need millisecond-grade delivery, look
  elsewhere.
- **Not a way to externally wake a Claude Code session.** Earlier versions of
  Sink tried this with tmux send-keys and `claude --print` cold-spawns. Both
  patterns are fragile in practice; the project converged on `/loop` polling
  as the only mechanism Claude Code natively supports.
- **Not an agent runtime.** The optional Codex bridge delivers a prompt when an
  event is waiting; it does not plan work, grant permissions, or execute peer
  requests itself. Agent sessions still do the work.

If those constraints don't fit your use case, you probably want a different
tool (Redis pub/sub, Temporal, an actual message broker, etc.).

---

## Repository layout

```
sink/                     # Python package
  cli.py                  # `sink` entrypoint — init, join, join-from, poll,
                          #   emit, log, status, doctor, whoami, run, ...
  events.py               # append-only JSONL log + cross-process file lock
  polling.py              # machine-readable selection + per-peer sequence cursors
  codex_loop.py           # durable pending delivery and Codex wake adapter
  scheduler.py            # Windows Task Scheduler + macOS launchd controls
  manifest.py             # coordination.yaml and machine-local config
  coordinator.py          # optional `sink run` heartbeat daemon
  state.py                # STATE.md projection (with open-task derivation)
  session.py              # Claude session id detection for heartbeats
  transport/              # shared_fs.py, ssh.py, git.py — pluggable backends
skills/sink/SKILL.md      # the user-facing skill spec (≤100 lines, low context tax)
.claude-plugin/           # plugin + marketplace manifests for /plugin install
install.sh, install.ps1   # one-shot installers
examples/                 # coordination.yaml.example
tests/                    # smoke tests
```

---

## Contributing

This is alpha software — APIs may change. Run both the original concurrency
smoke test and the unit suite before submitting a change:

```bash
python tests/test_concurrency.py
python -m unittest discover -s tests -v
```

Issues, design questions, and topology-specific war stories all welcome.

---

## Authors

- **Enoch Wang** — <enochsurge@gmail.com>
- **Claude (Anthropic)** — co-author

---

## License

MIT — see [LICENSE](LICENSE). Free to use for any purpose, including
commercial. Attribution preserved per the standard MIT terms.
