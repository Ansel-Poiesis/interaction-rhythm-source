"""Deterministic correctness and latency fixtures for the observation pipeline."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from time import perf_counter
import platform
import sqlite3
import sys
import tempfile

from db import DB
from interaction_rhythm.observations import ForegroundSnapshot, ObservationBuffer


EVENT_KINDS = ("keyboard", "mouse_button", "scroll")
FIXTURE_TIMEZONE = datetime.now().astimezone().tzinfo or timezone.utc


def compare_distributions(expected: Counter, actual: Counter) -> dict:
    """Separate total loss/duplication from events assigned to the wrong bucket."""
    keys = set(expected) | set(actual)
    missing = sum(max(0, expected[key] - actual[key]) for key in keys)
    unexpected = sum(max(0, actual[key] - expected[key]) for key in keys)
    expected_total = sum(expected.values())
    actual_total = sum(actual.values())
    loss = max(0, expected_total - actual_total)
    duplicates = max(0, actual_total - expected_total)
    return {
        "expected_event_count": expected_total,
        "captured_event_count": actual_total,
        "lost_event_count": loss,
        "duplicate_event_count": duplicates,
        "attribution_error_count": min(missing - loss, unexpected - duplicates),
    }


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _event_distribution(database: Path) -> Counter:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT process_name, keyboard_down_count, mouse_button_count, "
            "scroll_event_count FROM interaction_buckets"
        ).fetchall()
    finally:
        connection.close()
    distribution = Counter()
    for app, keyboard, buttons, scroll in rows:
        distribution[("keyboard", app)] += int(keyboard or 0)
        distribution[("mouse_button", app)] += int(buttons or 0)
        distribution[("scroll", app)] += int(scroll or 0)
    return +distribution


def _daily_expected(events: list[tuple[datetime, str, str]]) -> dict[str, Counter]:
    result: dict[str, Counter] = {}
    for occurred_at, kind, _app in events:
        date = occurred_at.astimezone(FIXTURE_TIMEZONE).date().isoformat()
        result.setdefault(date, Counter())[kind] += 1
    return result


def _daily_actual(metrics: dict) -> Counter:
    events = metrics["events"]
    return Counter({
        "keyboard": events["keyboard_down_count"],
        "mouse_button": events["mouse_button_count"],
        "scroll": events["scroll_event_count"],
    })


def run_observation_pipeline_benchmark(
    event_count: int = 30_000,
    query_samples: int = 200,
    database_path: Path | None = None,
) -> dict:
    """Exercise normalized observations without generating real OS input."""
    if event_count < 3:
        raise ValueError("event_count must be at least 3")
    if query_samples < 1:
        raise ValueError("query_samples must be positive")

    temporary = None
    if database_path is None:
        temporary = tempfile.TemporaryDirectory()
        database_path = Path(temporary.name) / "collector-benchmark.db"
    else:
        database_path = Path(database_path)
        if database_path.exists():
            raise FileExistsError(f"benchmark database already exists: {database_path}")

    db = DB(database_path)
    try:
        buffer = ObservationBuffer(collector_version="benchmark-fixture-v1")
        start = datetime(2026, 7, 20, 23, 59, 55, tzinfo=FIXTURE_TIMEZONE)
        apps = (
            ForegroundSnapshot("Code.exe", r"C:\Fixture\Code.exe"),
            ForegroundSnapshot("msedge.exe", r"C:\Fixture\msedge.exe"),
            ForegroundSnapshot("Obsidian.exe", r"C:\Fixture\Obsidian.exe"),
            ForegroundSnapshot("explorer.exe", r"C:\Windows\explorer.exe"),
        )
        expected = Counter()
        events: list[tuple[datetime, str, str]] = []

        ingest_started = perf_counter()
        for index in range(event_count):
            occurred_at = start + timedelta(microseconds=index * 10_000_000 / event_count)
            kind = EVENT_KINDS[index % len(EVENT_KINDS)]
            foreground = apps[(index * 7) % len(apps)]
            health_state = "running" if occurred_at < start + timedelta(seconds=8) else "unknown"
            if kind == "keyboard":
                buffer.record_keyboard(occurred_at, foreground, health_state)
            elif kind == "mouse_button":
                buffer.record_mouse_button(occurred_at, foreground, health_state)
            else:
                buffer.record_scroll(occurred_at, foreground, health_state)
            expected[(kind, foreground.app)] += 1
            events.append((occurred_at, kind, foreground.app))
        ingest_ms = (perf_counter() - ingest_started) * 1000

        buckets = buffer.drain(force=True)
        write_started = perf_counter()
        db.add_interaction_buckets(buckets)
        write_ms = (perf_counter() - write_started) * 1000

        end = start + timedelta(seconds=20)
        for state, segment_start, segment_end in (
            ("running", start, start + timedelta(seconds=8)),
            ("unknown", start + timedelta(seconds=8), start + timedelta(seconds=10)),
            ("locked", start + timedelta(seconds=10), start + timedelta(seconds=12)),
            ("sleeping", start + timedelta(seconds=12), start + timedelta(seconds=15)),
            ("running", start + timedelta(seconds=15), end),
        ):
            db.add_observation_segment("collector", state, segment_start, segment_end)
        db.add_foreground_interval("Code.exe", apps[0].path, start, start + timedelta(seconds=6))
        db.add_foreground_interval("msedge.exe", apps[1].path, start + timedelta(seconds=6), end)

        distribution = compare_distributions(expected, _event_distribution(database_path))
        daily_expected = _daily_expected(events)
        daily_metrics = {date: db.day_metrics(date) for date in sorted(daily_expected)}
        daily_match = all(
            _daily_actual(daily_metrics[date]) == expected_counts
            for date, expected_counts in daily_expected.items()
        )

        coverage_expected = {
            "2026-07-20": {"expected_seconds": 5, "healthy_seconds": 5, "unknown_seconds": 0},
            "2026-07-21": {"expected_seconds": 10, "healthy_seconds": 8, "unknown_seconds": 2},
        }
        coverage_match = all(
            all(daily_metrics[date]["coverage"][key] == value for key, value in values.items())
            for date, values in coverage_expected.items()
        )
        foreground_match = (
            daily_metrics["2026-07-20"]["foreground_seconds"] == 5
            and daily_metrics["2026-07-21"]["foreground_seconds"] == 15
        )

        query_times = []
        for sample in range(query_samples):
            date = "2026-07-20" if sample % 2 == 0 else "2026-07-21"
            query_started = perf_counter()
            db.day_metrics(date)
            query_times.append((perf_counter() - query_started) * 1000)
        db.checkpoint()

        correctness = {
            **distribution,
            "cross_midnight_event_totals_match": daily_match,
            "coverage_formula_matches": coverage_match,
            "foreground_clipping_matches": foreground_match,
        }
        passed = (
            distribution["lost_event_count"] == 0
            and distribution["duplicate_event_count"] == 0
            and distribution["attribution_error_count"] == 0
            and daily_match
            and coverage_match
            and foreground_match
        )
        return {
            "passed": passed,
            "scope": "normalized observation pipeline only",
            "evidence_boundary": (
                "Does not measure WH_* hook delivery, Raw Input, UAC, Remote Desktop, "
                "multiple devices, touchpads, or sleep/lock detection latency."
            ),
            "scenario": {
                "event_count": event_count,
                "bucket_count": len(buckets),
                "query_samples": query_samples,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "states": ["running", "unknown", "locked", "sleeping", "running"],
            },
            "environment": {
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "timezone": str(FIXTURE_TIMEZONE),
            },
            "correctness": correctness,
            "performance": {
                "buffer_ingest_ms": round(ingest_ms, 3),
                "buffer_events_per_second": round(
                    event_count / max(ingest_ms / 1000, 0.000001)
                ),
                "sqlite_batch_write_ms": round(write_ms, 3),
                "day_query_p50_ms": round(_percentile(query_times, 0.50), 3),
                "day_query_p95_ms": round(_percentile(query_times, 0.95), 3),
                "database_bytes": database_path.stat().st_size,
            },
            "daily_metrics": daily_metrics,
        }
    finally:
        db.close()
        if temporary is not None:
            temporary.cleanup()
