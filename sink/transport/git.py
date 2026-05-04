import subprocess
from pathlib import Path
from .base import Transport


class GitTransport(Transport):
    """Git-backed transport. Use when peers can't SSH each other directly but all have
    outbound network to a shared remote (self-hosted bare repo or GitHub).

    Config:
      remote: user@hub:~/sink-repos/potato.git    # or https://github.com/u/r.git
      branch: main
      repo_dir: .sink                              # local working tree (defaults to sink dir)

    The events/ directory is committed and pushed; pull rebases new commits in.
    """

    def __init__(self, repo_dir: Path, remote: str, self_id: str, branch: str = "main"):
        self.repo_dir = Path(repo_dir)
        self.remote = remote
        self.self_id = self_id
        self.branch = branch

    def init_remote(self) -> None:
        if not (self.repo_dir / ".git").exists():
            self._git("init", "-b", self.branch)
            self._git("remote", "add", "origin", self.remote)
        try:
            self._git("fetch", "origin")
            self._git("checkout", self.branch)
        except subprocess.CalledProcessError:
            # remote is empty — initial push will create the branch
            (self.repo_dir / ".gitkeep").touch()
            self._git("add", ".gitkeep")
            self._git("commit", "-m", f"sink init from {self.self_id}")
            self._git("push", "-u", "origin", self.branch)

    def push(self, log_path: Path) -> None:
        rel = Path(log_path).resolve().relative_to(self.repo_dir.resolve())
        self._git("add", str(rel))
        r = subprocess.run(
            ["git", "-C", str(self.repo_dir), "commit", "-m", f"event from {self.self_id}"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            self._git("push", "origin", self.branch)

    def pull(self, events_dir: Path) -> list[str]:
        events_dir = Path(events_dir)
        before = self._snapshot(events_dir)
        try:
            self._git("pull", "--rebase", "origin", self.branch)
        except subprocess.CalledProcessError:
            return []
        after = self._snapshot(events_dir)
        return [aid for aid, mt in after.items() if before.get(aid) != mt and aid != self.self_id]

    def _snapshot(self, events_dir: Path) -> dict[str, float]:
        if not events_dir.exists():
            return {}
        return {p.stem: p.stat().st_mtime for p in events_dir.glob("*.jsonl")}

    def _git(self, *args: str) -> str:
        r = subprocess.run(
            ["git", "-C", str(self.repo_dir), *args],
            check=True, capture_output=True, text=True,
        )
        return r.stdout
