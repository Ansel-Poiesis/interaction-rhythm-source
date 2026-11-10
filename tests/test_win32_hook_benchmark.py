from collections import Counter
from pathlib import Path
import sqlite3
import tempfile
import unittest

from monitor import WM_KEYDOWN, WM_KEYUP
from scripts.benchmark_win32_keyboard_hook import (
    _fixture_artifact_counts,
    analyze_records,
    run_live_benchmark,
)


class Win32HookBenchmarkAnalysisTests(unittest.TestCase):
    def test_analysis_separates_delivery_normalization_and_attribution(self):
        expected_raw = Counter({(1, WM_KEYDOWN): 2, (1, WM_KEYUP): 1})
        raw = [
            {"sequence": 1, "message": WM_KEYDOWN, "app": "Code.exe", "latency_ms": 1},
            {"sequence": 1, "message": WM_KEYDOWN, "app": "Code.exe", "latency_ms": 2},
            {"sequence": 1, "message": WM_KEYUP, "app": "Code.exe", "latency_ms": 3},
        ]
        normalized = [{"sequence": 1, "app": "Code.exe", "path": ""}]

        result = analyze_records(expected_raw, raw, normalized, {1: "Code.exe"})

        self.assertEqual(result["lost_event_count"], 0)
        self.assertEqual(result["duplicate_event_count"], 0)
        self.assertEqual(result["captured_normalized_press_count"], 1)
        self.assertEqual(result["foreground_attribution_error_count"], 0)
        self.assertEqual(result["callback_latency_p95_ms"], 3)

    def test_analysis_reports_wrong_foreground_process(self):
        expected_raw = Counter({(1, WM_KEYDOWN): 1, (1, WM_KEYUP): 1})
        raw = [
            {"sequence": 1, "message": WM_KEYDOWN, "app": "Edge.exe", "latency_ms": 1},
            {"sequence": 1, "message": WM_KEYUP, "app": "Edge.exe", "latency_ms": 1},
        ]
        normalized = [{"sequence": 1, "app": "Edge.exe", "path": ""}]

        result = analyze_records(expected_raw, raw, normalized, {1: "Code.exe"})

        self.assertEqual(result["foreground_attribution_error_count"], 1)

    def test_two_host_switching_refuses_an_active_recorder_database(self):
        with self.assertRaisesRegex(RuntimeError, "two-host switching is refused"):
            run_live_benchmark(
                press_count=4,
                host_count=2,
                recorder_database=Path("active-recorder.db"),
            )

    def test_recorder_guard_finds_only_benchmark_fixture_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "tracker.db"
            connection = sqlite3.connect(database)
            for table in ("app_usage", "app_paths", "app_interactions"):
                connection.execute(f"CREATE TABLE {table}(path TEXT NOT NULL)")
                connection.execute(
                    f"INSERT INTO {table}(path) VALUES(?)",
                    (r"C:\Temp\interaction-rhythm-hook-fixture\InteractionRhythm.exe",),
                )
                connection.execute(
                    f"INSERT INTO {table}(path) VALUES(?)",
                    (r"C:\Program Files\Code.exe",),
                )
            connection.commit()
            connection.close()

            counts = _fixture_artifact_counts(database)

        self.assertEqual(counts, {
            "app_usage": 1,
            "app_paths": 1,
            "app_interactions": 1,
        })


if __name__ == "__main__":
    unittest.main()
