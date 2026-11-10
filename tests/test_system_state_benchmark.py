import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.benchmark_system_state import run_system_state_benchmark
from monitor import SystemStateMonitor


ROOT = Path(__file__).resolve().parents[1]


class SystemStateBenchmarkTests(unittest.TestCase):
    def test_timeline_excludes_locked_and_keeps_unconfirmed_gaps_unknown(self):
        report = run_system_state_benchmark()

        self.assertTrue(report["passed"])
        self.assertEqual(report["timeline"]["durations_by_state"]["locked"], 2)
        self.assertEqual(report["timeline"]["durations_by_state"]["unknown"], 20)
        self.assertEqual(report["coverage"]["expected_seconds"], 25)
        self.assertEqual(report["coverage"]["healthy_seconds"], 4)
        self.assertEqual(report["coverage"]["unknown_seconds"], 21)
        self.assertEqual(report["coverage"]["ratio"], 0.16)

    def test_command_line_emits_machine_readable_evidence(self):
        result = subprocess.run(
            [sys.executable, "scripts/benchmark_system_state.py"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        report = json.loads(result.stdout)
        self.assertTrue(report["passed"])
        self.assertIn("Does not lock the workstation", report["evidence_boundary"])

    def test_explicit_power_events_deduplicate_dual_resume_messages(self):
        class FakeDB:
            def __init__(self):
                self.segments = []
                self.states = []

            def add_observation_segment(self, component, state, start, end):
                self.segments.append((component, state, start, end))

            def record_collector_state(
                self, component, state, observed_at=None, detail=""
            ):
                self.states.append((component, state, observed_at, detail))

        class FakeSource:
            def __init__(self, callback):
                self.callback = callback
                self.stats = {"last_error": "", "healthy": False}

            def start(self):
                return True

            def stop(self):
                return True

        database = FakeDB()
        monitor = SystemStateMonitor(database, lambda: True, FakeSource)
        start = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
        monitor._initialize(start, 100.0, "running")

        monitor._handle_windows_event(
            "suspend", start + timedelta(seconds=1), 101.0, "running"
        )
        monitor._handle_windows_event(
            "resume_automatic", start + timedelta(seconds=11), 111.0, "running"
        )
        monitor._handle_windows_event(
            "resume_user", start + timedelta(seconds=11, milliseconds=100), 111.1, "running"
        )
        monitor._finish(start + timedelta(seconds=12))

        self.assertEqual(
            [(state, (end - begin).total_seconds()) for _, state, begin, end in database.segments],
            [("running", 1.0), ("sleeping", 10.0), ("running", 1.0)],
        )
        self.assertEqual(
            monitor.event_stats["deduplicated_event_counts"],
            {"resume_user": 1},
        )

    def test_monotonic_gap_resumes_an_explicit_suspend_when_message_is_missing(self):
        class FakeDB:
            def __init__(self):
                self.segments = []
                self.states = []

            def add_observation_segment(self, component, state, start, end):
                self.segments.append((component, state, start, end))

            def record_collector_state(
                self, component, state, observed_at=None, detail=""
            ):
                self.states.append((component, state, observed_at, detail))

        class FakeSource:
            def __init__(self, callback):
                self.stats = {"last_error": "", "healthy": False}

            def start(self):
                return True

            def stop(self):
                return True

        database = FakeDB()
        monitor = SystemStateMonitor(database, lambda: True, FakeSource)
        start = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
        monitor._initialize(start, 100.0, "running")
        monitor._handle_windows_event(
            "suspend", start + timedelta(seconds=1), 101.0, "running"
        )
        monitor._tick(start + timedelta(seconds=21), 121.0, "running")

        self.assertEqual(
            [(state, (end - begin).total_seconds()) for _, state, begin, end in database.segments],
            [("running", 1.0), ("sleeping", 20.0)],
        )
        self.assertFalse(monitor.event_stats["explicit_suspend"])
        self.assertEqual(database.states[-1][3], "resume_after_gap")

    def test_unconfirmed_monotonic_gap_stays_unknown_and_resets_foreground(self):
        class FakeDB:
            def __init__(self):
                self.segments = []
                self.states = []

            def add_observation_segment(self, component, state, start, end, detail=""):
                self.segments.append((component, state, start, end, detail))

            def record_collector_state(self, component, state, observed_at=None, detail=""):
                self.states.append((component, state, observed_at, detail))

        class FakeSource:
            def __init__(self, callback):
                self.stats = {"last_error": "", "healthy": False}

            def start(self):
                return True

            def stop(self):
                return True

        paused = []
        resumed = []
        database = FakeDB()
        monitor = SystemStateMonitor(
            database,
            lambda: True,
            FakeSource,
            pause_observation=paused.append,
            resume_observation=resumed.append,
        )
        start = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
        monitor._initialize(start, 100.0, "running")
        monitor._tick(start + timedelta(seconds=20), 120.0, "running")

        self.assertEqual(database.segments[0][1], "unknown")
        self.assertEqual(database.segments[0][4], "unobserved_monotonic_gap")
        self.assertEqual(paused, [start])
        self.assertEqual(resumed, [start + timedelta(seconds=20)])

    def test_suspend_and_session_end_close_observation_boundary(self):
        class FakeDB:
            def add_observation_segment(self, *args, **kwargs):
                pass

            def record_collector_state(self, *args, **kwargs):
                pass

        class FakeSource:
            def __init__(self, callback):
                self.stats = {"last_error": "", "healthy": False}

        paused = []
        monitor = SystemStateMonitor(
            FakeDB(),
            lambda: True,
            FakeSource,
            pause_observation=paused.append,
        )
        start = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
        monitor._initialize(start, 100.0, "running")
        monitor._handle_windows_event("suspend", start + timedelta(seconds=1), 101.0, "running")
        monitor._handle_windows_event("session_end", start + timedelta(seconds=2), 102.0, "running")

        self.assertEqual(paused, [start + timedelta(seconds=1), start + timedelta(seconds=2)])

    def test_desktop_probe_lock_fallback_closes_and_restarts_foreground(self):
        class FakeDB:
            def add_observation_segment(self, *args, **kwargs):
                pass

            def record_collector_state(self, *args, **kwargs):
                pass

        class FakeSource:
            def __init__(self, callback):
                self.stats = {"last_error": "", "healthy": False}

        paused = []
        resumed = []
        monitor = SystemStateMonitor(
            FakeDB(),
            lambda: True,
            FakeSource,
            pause_observation=paused.append,
            resume_observation=resumed.append,
        )
        start = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
        monitor._initialize(start, 100.0, "running")
        monitor._tick(start + timedelta(seconds=1), 101.0, "locked")
        monitor._tick(start + timedelta(seconds=2), 102.0, "running")

        self.assertEqual(paused, [start + timedelta(seconds=1)])
        self.assertEqual(resumed, [start + timedelta(seconds=2)])


if __name__ == "__main__":
    unittest.main()
