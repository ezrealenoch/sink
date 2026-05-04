"""Claude Code session id discovery.

Sink records the current Claude session id with `started`/`heartbeat` events so
operators can correlate event-log activity with Claude transcripts on disk.
That's it — no respawn, no remote-spawn, no duplicate kill machinery. Those
were premised on the idea that sink could externally wake a Claude session,
which Claude Code doesn't actually allow.
"""
import os
import re
import uuid
from pathlib import Path

CLAUDE_SESSION_ID_ENV = "CLAUDE_SESSION_ID"


def _sanitize_cwd_for_claude(cwd: Path) -> str:
    """Mirror Claude Code's project-dir naming: replace path separators and colons with '-'.

    Example: /home/user/myproject -> -home-user-myproject
    """
    return re.sub(r"[\\/:]", "-", str(cwd))


def _claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def detect_claude_session_id(cwd: Path | None = None) -> str | None:
    """Find the current Claude Code session id by reading the project dir.

    Claude Code stores each session as `~/.claude/projects/<sanitized-cwd>/<uuid>.jsonl`
    and continually appends to the active session's file. The most recently modified
    jsonl in that dir is the live session.
    """
    cwd = cwd or Path.cwd()
    project_dir = _claude_projects_dir() / _sanitize_cwd_for_claude(cwd)
    if not project_dir.exists():
        return None
    sessions = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return sessions[-1].stem if sessions else None


def current_session_id() -> str:
    """Resolve a session id, in priority order:
      1. CLAUDE_SESSION_ID env var (if explicitly set)
      2. The actual live Claude Code session for this cwd
      3. A synthetic sink-<uuid> as a last resort (non-Claude callers)
    """
    env = os.environ.get(CLAUDE_SESSION_ID_ENV)
    if env:
        return env
    detected = detect_claude_session_id()
    if detected:
        return detected
    return f"sink-{uuid.uuid4().hex[:12]}"
