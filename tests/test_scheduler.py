import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sink.codex_loop import CodexLoopConfig, LoopError
from sink.manifest import Manifest, TransportSpec
from sink.scheduler import MacScheduler, WindowsScheduler, runner_command


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.manifest = Manifest(
            project="demo",
            project_id="11111111-2222-3333-4444-555555555555",
            transport=TransportSpec("shared-fs", {"path": "shared"}),
            agents={},
        )
        self.config = CodexLoopConfig(
            name="review-loop",
            sink_dir="C:\\work\\demo\\.sink",
            session="thread-1",
            delivery="queue",
            interval_seconds=300,
            codex_path="codex",
        )

    def test_windows_install_uses_reversible_named_task_and_state_home(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "ok", "")

        scheduler = WindowsScheduler(fake_run)
        task = scheduler.install(self.manifest, self.config, "C:\\state")
        self.assertEqual("Sink Codex Loop sink-11111111-review-loop", task)
        self.assertEqual("schtasks.exe", calls[0][0])
        self.assertIn("--state-home", calls[0][-1])
        self.assertIn("C:\\state", calls[0][-1])
        scheduler.pause(self.manifest, self.config)
        scheduler.resume(self.manifest, self.config)
        scheduler.uninstall(self.manifest, self.config)
        self.assertIn("/DISABLE", calls[1])
        self.assertIn("/ENABLE", calls[2])
        self.assertIn("/Delete", calls[3])

    def test_windows_rejects_subminute_granularity(self):
        self.config.interval_seconds = 61
        scheduler = WindowsScheduler(lambda *args, **kwargs: None)
        with self.assertRaisesRegex(LoopError, "whole number of minutes"):
            scheduler.install(self.manifest, self.config, None)

    def test_runner_command_targets_run_once(self):
        command = runner_command(self.config)
        self.assertEqual(["-m", "sink.cli", "loop", "run-once"], command[1:5])

    def test_mac_install_writes_launch_agent_with_interval(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "ok", "")

        with tempfile.TemporaryDirectory() as td, \
                patch("sink.scheduler.Path.home", return_value=Path(td)), \
                patch("sink.scheduler.os.getuid", return_value=501, create=True):
            scheduler = MacScheduler(fake_run)
            installed = Path(scheduler.install(self.manifest, self.config, None))
            self.assertTrue(installed.exists())
            contents = installed.read_bytes()
            self.assertIn(b"StartInterval", contents)
            self.assertIn(b"300", contents)
            self.assertEqual(["launchctl", "bootout"], calls[0][:2])
            self.assertEqual(["launchctl", "bootstrap", "gui/501"], calls[1][:3])


if __name__ == "__main__":
    unittest.main()
