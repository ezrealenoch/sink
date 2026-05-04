---
name: sink
description: Coordinate multiple Claude Code instances on different machines via a shared, append-only event log. Each Claude session uses /loop to periodically poll the sink for new events addressed to it. Use when the user wants agents on different systems to communicate by leaving each other messages and reacting on a polling cadence — the formalization of the "shared CHANGELOG.md polled every 30 minutes" pattern.
---

# Sink — multi-agent coordination

Sink is the formalization of "shared file the agents check periodically." Each
agent owns a JSONL log; logs sync between machines via shared filesystem, SSH,
or git. Agents talk by appending events; they react via a `/loop` that polls.

Claude Code provides no way to externally inject prompts into a running Claude
session. Sink does not try. The pattern is always: each Claude session runs
`/loop sink poll --since-last`, and the loop's tick is what reads peer events.

## Use this skill when

- The user has Claudes on multiple machines that need to coordinate.
- The user references a "shared changelog" / "30-minute poll" pattern.
- The user says "the X agent should…" about a peer Claude.

## Joining an existing project (the headline flow)

```bash
sink join-from <source> --as <agent>
sink doctor
# in the Claude session:
/loop sink poll --since-last
```

`<source>` can be a local path, an SSH spec (`user@host:/path/.sink`), or
`git+https://...`. That's the entire onboarding — three commands.

## Setting up a new project

```bash
sink init <project-name> --transport <shared-fs|ssh|git>
# edit .sink/coordination.yaml — add agents
sink join <this-agent-id>
/loop sink poll --since-last
```

If the install is missing or stale on this machine, run `./install.sh` (Linux/Mac)
or `.\install.ps1` (Windows) from the cloned Sink repo — both are idempotent.

## The /loop discipline

When `sink poll --since-last` returns nothing addressed to you, **do NOT emit a
"still here" or "Nth quiet tick" event.** Just let the next tick fire on
schedule. Silence is information enough.

Emit only when you have substantive output: results, state changes, or replies
addressing a peer. The event `type` is a free string — pick one that describes
the substance (`task`, `ack`, `cell_done`, `findings`, `proposal`, etc.).

## Posting events

```bash
# plain message:
sink log "starting nightly run"

# addressed to a specific agent:
sink emit task --payload '{"for":"vm","task":"rebuild dependency X"}'

# acknowledge or reply:
sink emit ack --payload '{"for":"host","task":"T-042","status":"started"}'
```

Use the CLI — never hand-edit JSONL. `started`/`heartbeat`/`stopped` are
reserved lifecycle types; don't emit those manually.

## Reading the cross-system view

```bash
sink poll --since-last           # incremental, advances marker (use in /loop)
sink poll --since-last --type task --type ack   # filter to specific event types
sink status                      # full STATE.md: who's live, who's currently working on what
```

`sink poll` auto-summarizes when there's a large backlog (returning agent after
a long absence) and prints a `NEXT POLL: in <N>s` hint that `/loop` self-pacing
uses.

## CLI reference

| Command | Purpose |
|---|---|
| `sink init <name>` | create a new project's coordination.yaml |
| `sink join <agent>` | mark this machine as `<agent>` |
| `sink join-from <src>` | fetch a remote manifest + join in one step |
| `sink poll --since-last` | the /loop primitive |
| `sink emit <type> --payload <json>` | append an event |
| `sink log <message>` | shorthand for `emit log` |
| `sink status` | rolled-up cross-system view |
| `sink doctor` | diagnose install / manifest issues |
| `sink whoami` | show this machine's identity |
| `sink run` | optional heartbeat daemon |

`sink --help` and `sink <cmd> --help` cover all flags.
