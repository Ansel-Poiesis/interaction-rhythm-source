from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.run_isolated_hook_benchmark import _isolated_database_result, _same_path


class IsolatedHookCoordinatorTests(unittest.TestCase):
    def _database(self, root: Path, marked_input_count: int = 0) -> Path:
        database = root / "tracker.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            "CREATE TABLE interaction_buckets("
            "process_name TEXT, keyboard_down_count INTEGER, mouse_button_count INTEGER, "
            "scroll_event_count INTEGER);"
            "CREATE TABLE foreground_intervals("
            "process_name TEXT, started_at TEXT, ended_at TEXT);"
        )
        connection.execute(
            "INSERT INTO interaction_buckets VALUES(?,?,?,?)",
            ("Other.exe", marked_input_count, 0, 0),
        )
        connection.executemany(
            "INSERT INTO foreground_intervals VALUES(?,?,?)",
            [
                ("IRBenchA.exe", "2026-07-21T10:00:00.000001", "2026-07-21T10:00:00.000002"),
                ("IRBenchB.exe", "2026-07-21T10:00:00.000002", "2026-07-21T10:00:00.000003"),
            ],
        )
        connection.commit()
        connection.close()
        return database

    def test_isolated_result_requires_all_ignored_events_and_transitions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = _isolated_database_result(
                self._database(Path(temp_dir)),
                expected_intervals=2,
                ignored_benchmark_event_count=2,
                expected_ignored_benchmark_event_count=2,
            )

        self.assertTrue(result["passed"])
        self.assertTrue(result["collector_ignored_benchmark_events_match"])
        self.assertEqual(result["stored_fixture_input_event_count"], 0)
        self.assertEqual(result["transition_capture_ratio"], 1.0)

    def test_isolated_result_rejects_missing_ignored_benchmark_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = _isolated_database_result(
                self._database(Path(temp_dir), marked_input_count=1),
                expected_intervals=2,
                ignored_benchmark_event_count=1,
                expected_ignored_benchmark_event_count=2,
            )

        self.assertFalse(result["passed"])

    def test_isolated_result_excludes_fixture_warmup_outside_measurement_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = self._database(Path(temp_dir))
            connection = sqlite3.connect(database)
            connection.execute(
                "INSERT INTO foreground_intervals VALUES(?,?,?)",
                (
                    "IRBenchA.exe",
                    "2026-07-21T09:59:59.000000",
                    "2026-07-21T10:00:00.000000",
                ),
            )
            connection.commit()
            connection.close()

            result = _isolated_database_result(
                database,
                expected_intervals=2,
                measurement_started_at="2026-07-21T10:00:00.000000",
                measurement_ended_at="2026-07-21T10:00:00.000003",
                ignored_benchmark_event_count=2,
                expected_ignored_benchmark_event_count=2,
            )

        self.assertTrue(result["passed"])
        self.assertEqual(result["captured_fixture_intervals"], 2)
        self.assertEqual(result["out_of_window_fixture_intervals"], 1)

    def test_windows_path_comparison_is_case_insensitive(self):
        self.assertTrue(_same_path(r"C:\Temp\App.exe", r"c:\temp\APP.exe"))


if __name__ == "__main__":
    unittest.main()
