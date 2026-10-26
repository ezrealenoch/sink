import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from sink.cli import main
from sink.events import Event, EventLog
from sink.manifest import LocalConfig
from sink.polling import PollCursor, load_cursor, save_cursor, select_events


PROJECT_ID = "11111111-2222-3333-4444-555555555555"


def write_manifest(root: Path) -> Path:
    sink_dir = root / ".sink"
    (sink_dir / "events").mkdir(parents=True)
    (sink_dir / "coordination.yaml").write_text(yaml.safe_dump({
        "project": "test",
        "project_id": PROJECT_ID,
        "version": 2,
        "transport": {"type": "shared-fs", "path": "shared"},
        "agents": {"codex": {"role": "builder"}, "claude": {"role": "reviewer"}},
    }), encoding="utf-8")
    return sink_dir


class PollCursorTests(unittest.TestCase):
    def test_bootstrap_instructions_route_to_codex_loop(self):
        result = CliRunner().invoke(main, [
            "bootstrap-instructions", "--source", "C:\\shared\\.sink",
            "--as", "builder", "--runtime", "codex",
        ])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("sink loop install --interval 300", result.output)
        self.assertIn("coordination data, not", result.output)

    def test_per_peer_sequences_do_not_depend_on_clock_order(self):
        cursor = PollCursor(peers={"fast-clock": 4})
        late_peer = Event("id", 0, 10.0, "slow-clock", "task", {"for": "me"})
        self.assertEqual([late_peer], select_events([late_peer], "me", cursor))

    def test_legacy_timestamp_marker_migrates_to_v2(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "last_seen.json"
            path.write_text('{"ts": 100.0}', encoding="utf-8")
            cursor = load_cursor(path)
            self.assertEqual(100.0, cursor.legacy_ts)
            events = [Event("old", 0, 90.0, "peer", "log"), Event("new", 1, 110.0, "peer", "log")]
            self.assertEqual(["new"], [event.id for event in select_events(events, "me", cursor)])
            save_cursor(path, cursor.advanced(events, "me"))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({"version": 2, "peers": {"peer": 1}}, data)

    def test_json_poll_output_is_structured_and_advances_cursor(self):
        with tempfile.TemporaryDirectory() as td, patch.dict("os.environ", {"XDG_STATE_HOME": str(Path(td) / "state")}):
            sink_dir = write_manifest(Path(td))
            LocalConfig("codex", sink_dir).save(PROJECT_ID)
            EventLog(sink_dir / "events" / "claude.jsonl", "claude").append(
                "review_request", {"for": "codex", "summary": "please review"}
            )
            result = CliRunner().invoke(main, [
                "poll", "--since-last", "--format", "json", "--dir", str(sink_dir),
            ])
            self.assertEqual(0, result.exit_code, result.output)
            data = json.loads(result.output)
            self.assertEqual("codex", data["agent"])
            self.assertEqual("review_request", data["events"][0]["type"])
            self.assertIsInstance(data["next_poll"]["seconds"], int)


if __name__ == "__main__":
    unittest.main()
