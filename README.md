# Sink

> **Multi-agent coordination for Claude Code via shared, append-only event logs.**
> Each Claude session uses `/loop` to periodically poll the sink for events addressed to it. Logs sync between machines via shared filesystem, SSH, or git. That's the whole pattern.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

---

## The problem

Claude Code provides no way to externally inject prompts into a running Claude
session. So if you want two or more Claudes on different machines to
coordinate — *"the GPU agent should rebuild the corpus while the host indexes
it"* — each Claude has to **poll** a shared place periodically.

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
- **Quiet-tick discipline** — agents are taught to stay silent when there's
  nothing to say, instead of spamming "still here" pings

---

## The mental model

```
              ┌─────────────────────────┐
              │  /loop sink poll …      │   ← every Claude session, on each machine
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

In a Claude Code session on the new machine, after the sink CLI is installed:

```bash
sink join-from <source> --as <agent>
sink doctor
# in your Claude session:
/loop sink poll --since-last
```

`<source>` can be:

- A local or shared-mount path: `/media/shared/myproject/.sink`
- An SSH spec: `user@vm-host:/home/user/myproject/.sink`
- A git URL: `git+https://github.com/me/myproject-coord.git`

For onboarding *another* machine without typing the join command yourself,
generate a paste-ready instruction block:

```bash
sink bootstrap-instructions --source <source> --as <agent>
```

Copy the output, paste it into a fresh Claude Code session on the target
machine; that Claude reads the skill, installs sink if needed, joins, and
starts the loop. Three machines onboarded with one command per machine.

### Starting a new project

```bash
sink init my-project --transport shared-fs
# edit .sink/coordination.yaml — list agents, configure transport
sink join host
# in your Claude session:
/loop sink poll --since-last
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
- **`sink poll`** — the canonical `/loop` primitive. Adaptive interval hint, auto-summarize large backlogs, `--type` filter, `--since-last` marker
- **`sink emit / log`** — schema-validated event posting; lock-protected
- **`sink status`** — rolled-up STATE.md showing every agent, last-seen, currently-working-on (derived from `task_claim` / `task_done` events)
- **`sink doctor`** — diagnoses install, manifest, and local-state issues
- **`sink bootstrap-instructions`** — generates a paste-ready Claude prompt for onboarding a peer machine
- **`sink whoami`** — confirms this machine's identity in the project
- **`sink run`** *(optional)* — long-running heartbeat daemon for autonomous agents

The full CLI surface is small — under a dozen commands, all with `--help`.
The skill that ships with this repo (`skills/sink/SKILL.md`) is ~100 lines so
it doesn't blow Claude's context budget.

---

## Install

### As a Claude Code skill (recommended)

```bash
git clone https://github.com/ezrealenoch/sink.git
cd sink
./install.sh        # Linux / macOS
.\install.ps1       # Windows (PowerShell)
```

The script does `pip install --force-reinstall --no-deps -e .` and symlinks
`skills/sink/` into `~/.claude/skills/sink/` (with copy fallback). Restart
Claude Code afterwards.

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
# place skills/sink/SKILL.md under ~/.claude/skills/sink/SKILL.md yourself
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
- **Not an agent runtime.** Sink doesn't run code on your behalf or execute
  background jobs. It's a coordination *log*; the Claude sessions themselves
  do the work.

If those constraints don't fit your use case, you probably want a different
tool (Redis pub/sub, Temporal, an actual message broker, etc.).

---

## Repository layout

```
sink/                     # Python package
  cli.py                  # `sink` entrypoint — init, join, join-from, poll,
                          #   emit, log, status, doctor, whoami, run, ...
  events.py               # append-only JSONL log + cross-process file lock
  manifest.py             # coordination.yaml, machine-local config, last-seen marker
  coordinator.py          # optional `sink run` heartbeat daemon
  state.py                # STATE.md projection (with open-task derivation)
  session.py              # Claude session id detection
  transport/              # shared_fs.py, ssh.py, git.py — pluggable backends
skills/sink/SKILL.md      # the user-facing skill spec (≤100 lines, low context tax)
.claude-plugin/           # plugin + marketplace manifests for /plugin install
install.sh, install.ps1   # one-shot installers
examples/                 # coordination.yaml.example
tests/                    # smoke tests
```

---

## Contributing

This is alpha software — APIs may change. The verify script in
`tests/test_concurrency.py` is the smoke test for the lock + manifest
behaviour; run it after any change to those modules.

Issues, design questions, and topology-specific war stories all welcome.

---

## Authors

- **Enoch Wang** — <enochsurge@gmail.com>
- **Claude (Anthropic)** — co-author

---

## License

MIT — see [LICENSE](LICENSE). Free to use for any purpose, including
commercial. Attribution preserved per the standard MIT terms.
