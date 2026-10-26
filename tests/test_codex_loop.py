import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sink.codex_loop import DeliveryError, create_config, loop_root, run_once
from sink.events import EventLog
from sink.manifest import LocalConfig, Manifest
from tests.test_polling import PROJECT_ID, write_manifest


class CodexLoopTests(unittest.TestCase):
    def test_failed_delivery_is_retried_before_cursor_advances(self):
        with tempfile.TemporaryDirectory() as td, patch.dict("os.environ", {"XDG_STATE_HOME": str(Path(td) / "state")}):
            sink_dir = write_manifest(Path(td))
            manifest = Manifest.load(sink_dir / "coordination.yaml")
            LocalConfig("codex", sink_dir).save(PROJECT_ID)
            create_config(
                manifest, sink_dir, "review-loop", "thread-1", "queue", 300, True,
                codex_path="codex", types=("review_request",),
            )
            ignored = EventLog(sink_dir / "events" / "claude.jsonl", "claude").append(
                "log", {"message": "status-only"}
            )
            event = EventLog(sink_dir / "events" / "claude.jsonl", "claude").append(
                "review_request", {"for": "codex", "summary": "review commit abc"}
            )
            prompts = []

            def fail(config, prompt):
                prompts.append(prompt)
                return subprocess.CompletedProcess([], 1, "", "offline")

            with self.assertRaises(DeliveryError):
                run_once(sink_dir, "review-loop", fail)

            root = loop_root(PROJECT_ID, "review-loop")
            self.assertTrue((root / "pending.json").exists())
            self.assertFalse((root / "cursor.json").exists())

            def succeed(config, prompt):
                prompts.append(prompt)
                return subprocess.CompletedProcess([], 0, "queued", "")

            result = run_once(sink_dir, "review-loop", succeed)
            self.assertTrue(result["retried"])
            self.assertFalse((root / "pending.json").exists())
            self.assertTrue((root / "cursor.json").exists())
            self.assertEqual(prompts[0], prompts[1])
            self.assertIn(event.id, prompts[1])
            self.assertNotIn(ignored.id, prompts[1])

            calls = []
            quiet = run_once(
                sink_dir,
                "review-loop",
                lambda config, prompt: calls.append(prompt),
            )
            self.assertEqual("quiet", quiet["status"])
            self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
