#!/usr/bin/env bash
# Install Sink CLI plus Claude Code and Codex skills on this machine.
#   - pip install -e . (so the `sink` CLI is on PATH)
#   - link skills/sink into each agent's skill directory
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$REPO_DIR/skills/sink"
CLAUDE_SKILL_DST="$HOME/.claude/skills/sink"
CODEX_SKILL_DST="${CODEX_HOME:-$HOME/.codex}/skills/sink"

echo "[sink] installing CLI (force-reinstall to refresh stale copies)..."
python -m pip install --force-reinstall --no-deps -e "$REPO_DIR"

for SKILL_DST in "$CLAUDE_SKILL_DST" "$CODEX_SKILL_DST"; do
    echo "[sink] linking skill into $SKILL_DST"
    mkdir -p "$(dirname "$SKILL_DST")"
    if [ -e "$SKILL_DST" ] || [ -L "$SKILL_DST" ]; then
        echo "[sink] $SKILL_DST already exists — removing it"
        rm -rf "$SKILL_DST"
    fi
    if ln -s "$SKILL_SRC" "$SKILL_DST" 2>/dev/null; then
        echo "[sink] linked"
    else
        echo "[sink] symlink failed (likely permissions) — copying instead"
        cp -r "$SKILL_SRC" "$SKILL_DST"
    fi
done

echo "[sink] verifying CLI..."
if sink --version > /dev/null 2>&1; then
    echo "[sink] OK: $(sink --version)"
else
    echo "[sink] WARNING: 'sink' is not on PATH. Make sure your pip bin dir is in PATH."
fi

echo
echo "[sink] Done. Restart Claude Code or Codex and the 'sink' skill will be available."
echo "[sink] Ask the agent to set up Sink coordination, or run: sink --help"
