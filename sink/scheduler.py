"""OS scheduler adapters for the lightweight Codex Sink loop."""

from __future__ import annotations

from pathlib import Path
from typing import Callable
import math
import os
import platform
import plistlib
import re
import subprocess
import sys

from .codex_loop import CodexLoopConfig, LoopError, loop_root
from .manifest import Manifest


Runner = Callable[..., subprocess.CompletedProcess]


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "default"


def scheduler_id(manifest: Manifest, config: CodexLoopConfig) -> str:
    return f"sink-{manifest.project_id[:8]}-{_slug(config.name)}"


def runner_command(config: CodexLoopConfig, state_home: str | None = None) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "sink.cli",
        "loop",
        "run-once",
        "--dir",
        config.sink_dir,
        "--name",
        config.name,
    ]
    if state_home:
        command.extend(["--state-home", state_home])
    return command


def _run_checked(runner: Runner, args: list[str]) -> subprocess.CompletedProcess:
    result = runner(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise LoopError((result.stderr or result.stdout or "scheduler command failed").strip())
    return result


class WindowsScheduler:
    def __init__(self, runner: Runner = subprocess.run):
        self.runner = runner

    @staticmethod
    def task_name(manifest: Manifest, config: CodexLoopConfig) -> str:
        return f"Sink Codex Loop {scheduler_id(manifest, config)}"

    def install(self, manifest: Manifest, config: CodexLoopConfig, state_home: str | None) -> str:
        if config.interval_seconds % 60:
            raise LoopError("Windows Task Scheduler intervals must be a whole number of minutes")
        minutes = max(1, math.ceil(config.interval_seconds / 60))
        action = subprocess.list2cmdline(runner_command(config, state_home))
        name = self.task_name(manifest, config)
        _run_checked(self.runner, [
            "schtasks.exe", "/Create", "/F", "/SC", "MINUTE", "/MO", str(minutes),
            "/TN", name, "/TR", action,
        ])
        return name

    def status(self, manifest: Manifest, config: CodexLoopConfig) -> str:
        result = _run_checked(self.runner, [
            "schtasks.exe", "/Query", "/TN", self.task_name(manifest, config), "/FO", "LIST", "/V",
        ])
        return result.stdout.strip()

    def pause(self, manifest: Manifest, config: CodexLoopConfig) -> None:
        _run_checked(self.runner, [
            "schtasks.exe", "/Change", "/TN", self.task_name(manifest, config), "/DISABLE",
        ])

    def resume(self, manifest: Manifest, config: CodexLoopConfig) -> None:
        _run_checked(self.runner, [
            "schtasks.exe", "/Change", "/TN", self.task_name(manifest, config), "/ENABLE",
        ])

    def uninstall(self, manifest: Manifest, config: CodexLoopConfig) -> None:
        _run_checked(self.runner, [
            "schtasks.exe", "/Delete", "/F", "/TN", self.task_name(manifest, config),
        ])


class MacScheduler:
    def __init__(self, runner: Runner = subprocess.run):
        self.runner = runner

    @staticmethod
    def label(manifest: Manifest, config: CodexLoopConfig) -> str:
        return f"io.sink.codex.{scheduler_id(manifest, config)}"

    def plist_path(self, manifest: Manifest, config: CodexLoopConfig) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{self.label(manifest, config)}.plist"

    def _domain(self) -> str:
        return f"gui/{os.getuid()}"

    def install(self, manifest: Manifest, config: CodexLoopConfig, state_home: str | None) -> str:
        path = self.plist_path(manifest, config)
        root = loop_root(manifest.project_id, config.name)
        payload = {
            "Label": self.label(manifest, config),
            "ProgramArguments": runner_command(config, state_home),
            "StartInterval": config.interval_seconds,
            "RunAtLoad": False,
            "StandardOutPath": str(root / "scheduler.stdout.log"),
            "StandardErrorPath": str(root / "scheduler.stderr.log"),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            plistlib.dump(payload, handle, sort_keys=True)
        # Make install/update idempotent when the label is already loaded.
        self.runner(
            ["launchctl", "bootout", f"{self._domain()}/{self.label(manifest, config)}"],
            capture_output=True, text=True, check=False,
        )
        _run_checked(self.runner, ["launchctl", "bootstrap", self._domain(), str(path)])
        return str(path)

    def status(self, manifest: Manifest, config: CodexLoopConfig) -> str:
        result = self.runner([
            "launchctl", "print", f"{self._domain()}/{self.label(manifest, config)}",
        ], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return result.stdout.strip()
        path = self.plist_path(manifest, config)
        if path.exists():
            return f"state: paused (LaunchAgent not loaded)\nplist: {path}"
        raise LoopError((result.stderr or result.stdout or "LaunchAgent not found").strip())

    def pause(self, manifest: Manifest, config: CodexLoopConfig) -> None:
        result = self.runner([
            "launchctl", "bootout", f"{self._domain()}/{self.label(manifest, config)}",
        ], capture_output=True, text=True, check=False)
        if result.returncode != 0 and not self.plist_path(manifest, config).exists():
            raise LoopError((result.stderr or result.stdout or "LaunchAgent not found").strip())

    def resume(self, manifest: Manifest, config: CodexLoopConfig) -> None:
        self.pause(manifest, config)
        _run_checked(self.runner, [
            "launchctl", "bootstrap", self._domain(), str(self.plist_path(manifest, config)),
        ])

    def uninstall(self, manifest: Manifest, config: CodexLoopConfig) -> None:
        try:
            self.pause(manifest, config)
        except LoopError:
            pass
        path = self.plist_path(manifest, config)
        if path.exists():
            path.unlink()


def current_scheduler(system: str | None = None, runner: Runner = subprocess.run):
    system = system or platform.system()
    if system == "Windows":
        return WindowsScheduler(runner)
    if system == "Darwin":
        return MacScheduler(runner)
    raise LoopError("Codex loop scheduling currently supports Windows and macOS")
