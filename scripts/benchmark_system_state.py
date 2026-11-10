from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import DB
from interaction_rhythm.observations import local_now
from monitor import SLEEP_GAP_SECONDS, SystemStateMonitor, _desktop_state


def run_system_state_benchmark(database_path: Path | None = None) -> dict:
    temporary = None
    if database_path is None:
        temporary = tempfile.TemporaryDirectory()
        database_path = Path(temporary.name) / "system-state-benchmark.db"
    else:
        database_path = Path(database_path)
        if database_path.exists():
            raise FileExistsError(f"benchmark database already exists: {database_path}")

    database = DB(database_path)
    start = datetime(2026, 7, 21, 10, 0, 0, tzinfo=local_now().tzinfo)
    monitor = SystemStateMonitor(database, lambda: True)
    try:
        monitor._initialize(start, 100.0, "running")
        monitor._tick(start + timedelta(seconds=1), 101.0, "locked")
        monitor._tick(start + timedelta(seconds=3), 103.0, "running")
        monitor._tick(start + timedelta(seconds=4), 104.0, "degraded")
        monitor._tick(start + timedelta(seconds=5), 105.0, "running")
        monitor._tick(start + timedelta(seconds=6), 106.0, "running")
        monitor._tick(start + timedelta(seconds=26), 126.0, "running")
        monitor._finish(start + timedelta(seconds=27))
        database.checkpoint()

        connection = sqlite3.connect(database_path)
        try:
            segments = connection.execute(
                "SELECT state,started_at,ended_at FROM observation_segments "
                "WHERE component='collector' ORDER BY id"
            ).fetchall()
            states = connection.execute(
                "SELECT state,detail FROM collector_states "
                "WHERE component='system' ORDER BY id"
            ).fetchall()
        finally:
            connection.close()

        durations = {}
        ordered_segments = []
        for state, started_at, ended_at in segments:
            seconds = (
                datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)
            ).total_seconds()
            durations[state] = durations.get(state, 0.0) + seconds
            ordered_segments.append({
                "state": state,
                "seconds": seconds,
            })

        metrics = database.day_metrics("2026-07-21")
        expected_segments = [
            {"state": "running", "seconds": 1.0},
            {"state": "locked", "seconds": 2.0},
            {"state": "running", "seconds": 1.0},
            {"state": "degraded", "seconds": 1.0},
            {"state": "running", "seconds": 1.0},
            {"state": "unknown", "seconds": 20.0},
            {"state": "running", "seconds": 1.0},
        ]
        expected_state_records = [
            ["running", ""],
            ["locked", ""],
            ["running", ""],
            ["degraded", ""],
            ["running", ""],
            ["running", "resume_after_gap"],
        ]
        coverage = metrics["coverage"]
        passed = (
            ordered_segments == expected_segments
            and [list(row) for row in states] == expected_state_records
            and coverage["expected_seconds"] == 25
            and coverage["healthy_seconds"] == 4
            and coverage["unknown_seconds"] == 21
            and coverage["ratio"] == 0.16
        )
        return {
            "passed": passed,
            "scope": "deterministic system-state timeline + SQLite coverage",
            "evidence_boundary": (
                "Does not lock the workstation or suspend the machine. It validates the "
                "running/locked/degraded/sleeping state machine, unknown monotonic-gap fallback, "
                "SQLite intervals, and coverage formula. Real WTS session, power broadcast, "
                "UAC, Remote Desktop, and suspend/resume events remain unverified."
            ),
            "environment": {
                "current_desktop_probe": _desktop_state(),
                "sleep_gap_threshold_seconds": SLEEP_GAP_SECONDS,
                "database_deleted_after_run": temporary is not None,
            },
            "timeline": {
                "segments": ordered_segments,
                "durations_by_state": durations,
                "collector_states": [
                    {"state": state, "detail": detail}
                    for state, detail in states
                ],
            },
            "coverage": coverage,
        }
    finally:
        database.close()
        if temporary is not None:
            temporary.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()
    report = run_system_state_benchmark(args.database)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
