#!/usr/bin/env bash
# Install Sink as a Claude Code skill on this machine.
#   - pip install -e . (so the `sink` CLI is on PATH)
#   - link skill/ into ~/.claude/skills/sink so Claude Code sees it on next start
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$REPO_DIR/skills/sink"
SKILL_DST="$HOME/.claude/skills/sink"

echo "[sink] installing CLI (force-reinstall to refresh stale copies)..."
python -m pip install --force-reinstall --no-deps -e "$REPO_DIR"

echo "[sink] linking skill into $SKILL_DST"
mkdir -p "$HOME/.claude/skills"
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

echo "[sink] verifying CLI..."
if sink --version > /dev/null 2>&1; then
    echo "[sink] OK: $(sink --version)"
else
    echo "[sink] WARNING: 'sink' is not on PATH. Make sure your pip bin dir is in PATH."
fi

echo
echo "[sink] Done. Restart Claude Code and the 'sink' skill will be available."
echo "[sink] Try: claude  -> /sink   (or just ask 'set up sink coordination')"
