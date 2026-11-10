import unittest

from scripts.monitor_runtime_soak import (
    analyze_report,
    build_report,
    percentile,
    write_report_checkpoint,
)
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class RuntimeSoakTests(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self):
        self.assertEqual(percentile([1, 2, 3, 4], 0.95), 4)
        self.assertEqual(percentile([], 0.95), 0)

    def test_analysis_requires_duration_continuity_health_and_budgets(self):
        samples = [
            {
                "pid": 7,
                "alive": True,
                "cpu_percent_normalized": 0.0,
                "working_set_bytes": 80 * 1024 * 1024,
                "private_bytes": 70 * 1024 * 1024,
                "threads": 9,
                "handles": 200,
            },
            {
                "pid": 7,
                "alive": True,
                "cpu_percent_normalized": 0.4,
                "working_set_bytes": 82 * 1024 * 1024,
                "private_bytes": 71 * 1024 * 1024,
                "threads": 9,
                "handles": 204,
            },
        ]
        component_states = {
            name: {"state": "running"}
            for name in (
                "database_writer",
                "foreground_watcher",
                "keyboard_hook",
                "mouse_hook",
                "system",
                "system_events",
            )
        }
        before = {
            "interaction": {
                "rows": 10,
                "keyboard_down_count": 20,
                "mouse_button_count": 3,
                "scroll_event_count": 4,
            },
            "database_bytes": 1000,
        }
        after = {
            "quick_check": "ok",
            "latest_component_states": component_states,
            "interaction": {
                "rows": 12,
                "keyboard_down_count": 25,
                "mouse_button_count": 4,
                "scroll_event_count": 6,
            },
            "database_bytes": 1200,
        }
        result = analyze_report(
            samples,
            before,
            after,
            {"max_unobserved_gap_seconds": 1, "state_seconds": {"running": 60}},
            60,
            60,
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["database_growth_bytes"], 200)
        self.assertEqual(result["event_delta"]["keyboard_down_count"], 5)

    def test_analysis_blocks_short_or_unhealthy_runs(self):
        sample = {
            "pid": 7,
            "alive": True,
            "cpu_percent_normalized": 2.0,
            "working_set_bytes": 160 * 1024 * 1024,
            "private_bytes": 160 * 1024 * 1024,
            "threads": 9,
            "handles": 200,
        }
        before = {
            "interaction": {key: 0 for key in (
                "rows", "keyboard_down_count", "mouse_button_count", "scroll_event_count"
            )},
            "database_bytes": 1000,
        }
        after = {
            "quick_check": "ok",
            "latest_component_states": {"system": {"state": "degraded"}},
            "interaction": dict(before["interaction"]),
            "database_bytes": 1000,
        }
        result = analyze_report(
            [sample, sample], before, after,
            {"max_unobserved_gap_seconds": 50, "state_seconds": {"degraded": 10}},
            10, 60,
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["minimum_duration_reached"])
        self.assertFalse(result["checks"]["all_components_running"])

    def test_interrupted_checkpoint_is_written_atomically(self):
        sample = {
            "pid": 7,
            "alive": False,
            "cpu_percent_normalized": 0.0,
            "working_set_bytes": 1,
            "private_bytes": 1,
            "threads": 0,
            "handles": 0,
            "path": "InteractionRhythm.exe",
        }
        snapshot = {
            "quick_check": "ok",
            "latest_component_states": {},
            "interaction": {key: 0 for key in (
                "rows", "keyboard_down_count", "mouse_button_count", "scroll_event_count"
            )},
            "database_bytes": 1000,
        }
        started = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)
        with patch("scripts.monitor_runtime_soak.read_segment_window", return_value={
            "max_unobserved_gap_seconds": 0,
            "state_seconds": {},
        }):
            report = build_report(
                database=Path("tracker.db"),
                pid=7,
                started=started,
                ended=started + timedelta(seconds=5),
                before=snapshot,
                after=snapshot,
                samples=[sample],
                minimum_duration_seconds=60,
                status="interrupted",
                interruption={"reason": "process_exited"},
            )
        with TemporaryDirectory() as directory:
            json_path = Path(directory) / "soak.json"
            markdown_path = Path(directory) / "soak.md"
            write_report_checkpoint(report, json_path, markdown_path)
            self.assertIn('"status": "interrupted"', json_path.read_text(encoding="utf-8"))
            self.assertIn("`process_exited`", markdown_path.read_text(encoding="utf-8"))
            self.assertFalse(Path(str(json_path) + ".tmp").exists())

    def test_report_survives_segment_analysis_failure(self):
        snapshot = {
            "quick_check": "ok",
            "latest_component_states": {},
            "interaction": {key: 0 for key in (
                "rows", "keyboard_down_count", "mouse_button_count", "scroll_event_count"
            )},
            "database_bytes": 1000,
        }
        started = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)
        with patch(
            "scripts.monitor_runtime_soak.read_segment_window",
            side_effect=OSError("database unavailable"),
        ):
            report = build_report(
                database=Path("missing.db"),
                pid=7,
                started=started,
                ended=started + timedelta(seconds=5),
                before=snapshot,
                after=snapshot,
                samples=[],
                minimum_duration_seconds=60,
                status="interrupted",
                interruption={"reason": "process_exited"},
            )

        self.assertIn("OSError", report["result"]["segment_window"]["analysis_error"])
        self.assertFalse(report["result"]["passed"])


if __name__ == "__main__":
    unittest.main()
