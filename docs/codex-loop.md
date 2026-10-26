# Codex terminal loop

Sink's Codex loop fills a narrow gap: Codex CLI can run non-interactively, but
it does not provide the Scheduled management interface available in ChatGPT.
The bridge polls Sink without invoking a model and wakes Codex only when a
relevant event is waiting.

The implementation uses Windows Task Scheduler or macOS `launchd`. It remains
an optional adapter; Sink's event logs and transports do not depend on Codex.

## Prerequisites

- Python 3.10 or newer
- Sink installed and the project joined (`sink doctor` passes)
- Codex CLI available on `PATH`
- Windows or macOS
- A Codex session UUID or exact thread name

When installation runs inside an active Codex session, Sink tries
`CODEX_THREAD_ID` and `CODEX_SESSION_ID`. Otherwise pass `--session` explicitly.

## Install

From the project containing `.sink`:

```bash
sink whoami
sink loop install --interval 300
sink loop status
```

Windows Task Scheduler supports whole-minute intervals, so its interval must
be divisible by 60. macOS supports any interval of at least 60 seconds.

To wake only for addressed review work:

```bash
sink loop install --interval 300 --addressed-only --type review_request --type review_finding --type task
```

Repeated `--type` options form an allowlist. Without them, all non-lifecycle
event types are eligible. Broadcasts are included by default.

## Delivery modes

`--delivery auto` is the default:

1. Use `codex queue` if the installed Codex build provides it. This queues the
   prompt into the targeted live terminal session.
2. Otherwise use the documented `codex exec resume` path. This continues the
   session as a non-interactive turn.

Select a mode explicitly when predictable behavior matters:

```bash
sink loop install --delivery queue --session <session>
sink loop install --delivery exec-resume --session <session>
```

Installation checks that the selected command exists before registering the
schedule.

## Operating the loop

```bash
sink loop run-once       # poll and deliver immediately
sink loop status
sink loop pause
sink loop resume
sink loop uninstall      # remove schedule; preserve cursor and pending state
sink loop uninstall --purge-state
```

Pause a loop when active collaboration ends. Use `--purge-state` only when the
cursor, pending envelope, and diagnostic logs are no longer needed.

The machine must remain awake. A queued delivery also requires the targeted
Codex terminal session to remain available. `exec-resume` does not require an
open TUI, but it starts a Codex turn and may take substantially longer than a
queue operation.

## Delivery guarantees

Before invoking Codex, Sink writes a pending envelope containing:

- a stable delivery ID;
- exact event IDs and payloads;
- the prompt delivered to Codex;
- the cursor that may be committed after success.

If delivery fails, the envelope remains and the next tick retries it verbatim
before considering newer events. The cursor advances only after successful
delivery.

Delivery is **at-least-once**, not exactly-once. A process crash after Codex
accepts a message but before Sink records success can produce a duplicate. The
stable delivery and event IDs let the receiving agent recognize that case.

## Machine-local state

Loop state is separate from the shared `.sink` directory:

```text
~/.sink/<project-id>/loops/<name>/
├── config.yaml
├── cursor.json
├── pending.json          # present only while delivery is pending
└── loop.log
```

When `XDG_STATE_HOME` is set, the root is
`$XDG_STATE_HOME/sink/<project-id>/...`. `sink loop install` preserves that
setting in the scheduled command. Pass `--state-home` when installing or
operating a loop from a shell that does not already have the intended value.

Do not hand-edit the cursor or pending envelope.

## Security boundary

Sink events are untrusted coordination data. A peer event cannot authorize a
deployment, publication, purchase, destructive action, secret disclosure, or
expansion of the user's current task. Every delivered prompt repeats this
boundary and directs Codex to follow the user and local repository rules.

The scheduler runs with the permissions of the user who installed it. Keep
Codex sandbox and approval settings at the narrowest level that supports the
actual workflow.

## Troubleshooting

Start with:

```bash
sink doctor
sink whoami
sink loop status
sink loop run-once
```

Then inspect `loop.log` under the machine-local loop directory. On macOS,
`scheduler.stdout.log` and `scheduler.stderr.log` are stored beside it.

Common causes:

- **Wrong agent identity:** run `sink whoami` with the same `XDG_STATE_HOME`
  used during installation.
- **No session detected:** pass `--session <uuid-or-exact-name>`.
- **Queue unavailable:** reinstall with `--delivery exec-resume`, or use
  `--delivery auto`.
- **Repeated event:** compare the delivery ID and event IDs; at-least-once
  delivery can repeat after a crash.
- **No wake-up:** confirm the loop is enabled, the computer is awake, and the
  event is addressed to this agent or allowed by the broadcast/type filters.

For custom integrations, use structured polling rather than parsing display
text:

```bash
sink poll --since-last --format json
```

References: [Codex scheduled tasks](https://learn.chatgpt.com/docs/automations)
and [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
