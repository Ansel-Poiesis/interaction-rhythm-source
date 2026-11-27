"""Regression tests for indexed date-switch queries, busy_timeout, and archive consistency."""
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from db import DB
from interaction_rhythm.observations import InteractionBucket


class DateQueryOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "tracker.db"
        self.db = DB(self.path)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_busy_timeout_is_set_on_main_and_worker_connections(self):
        main_timeout = self.db._conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertGreaterEqual(main_timeout, 5000)

        worker_timeout = []

        def read_timeout():
            try:
                worker_timeout.append(self.db._conn.execute("PRAGMA busy_timeout").fetchone()[0])
            finally:
                self.db.close_thread_connection()

        thread = threading.Thread(target=read_timeout)
        thread.start()
        thread.join(timeout=5)
        self.assertEqual(len(worker_timeout), 1)
        self.assertGreaterEqual(worker_timeout[0], 5000)

    def test_date_switch_queries_use_time_indexes_without_full_table_scans(self):
        # Seed several days so SQLite planner uses indexes realistically.
        for day_offset in range(5):
            dt = datetime(2026, 11, 20 + day_offset, 14, 15, 0)
            self.db.add_interaction_bucket(InteractionBucket(
                started_at=dt,
                ended_at=dt + timedelta(seconds=1),
                app="Code.exe",
                path=r"C:\Code.exe",
                keyboard_down_count=10,
                mouse_button_count=5,
                scroll_event_count=2,
            ))
            self.db.add_session(
                "Code.exe",
                "",
                dt.isoformat(),
                (dt + timedelta(minutes=10)).isoformat(),
                r"C:\Code.exe",
            )
            self.db.add_observation_segment("collector", "running", dt, dt + timedelta(minutes=10))

        plans = {}
        queries = {
            "keystrokes_day": (
                "SELECT substr(ts,12,2) as h, SUM(count) FROM keystrokes WHERE ts >= ? AND ts < ? GROUP BY h",
                ("2026-11-24", "2026-11-25"),
            ),
            "mouse_day": (
                "SELECT substr(ts,12,2) as h, SUM(count) FROM mouse_events WHERE ts >= ? AND ts < ? GROUP BY h",
                ("2026-11-24", "2026-11-25"),
            ),
            "app_interactions_day": (
                "SELECT ai.app, SUM(ai.keyboard), SUM(ai.mouse) FROM app_interactions ai "
                "WHERE ai.ts >= ? AND ai.ts < ? GROUP BY ai.app",
                ("2026-11-24", "2026-11-25"),
            ),
            "app_interactions_hour": (
                "SELECT ai.app, SUM(ai.keyboard), SUM(ai.mouse) FROM app_interactions ai "
                "WHERE ai.ts >= ? AND ai.ts < ? GROUP BY ai.app",
                ("2026-11-24T14:", "2026-11-24T14;"),
            ),
            "obs_segments_covering": (
                "SELECT MIN(started_at), MAX(ended_at) FROM observation_segments "
                "WHERE component='collector' AND started_at < ? AND ended_at > ?",
                ("2026-11-25T00:00:00+08:00", "2026-11-24T00:00:00+08:00"),
            ),
            "fg_intervals_covering": (
                "SELECT COUNT(*) FROM foreground_intervals WHERE +started_at < ? AND ended_at > ?",
                ("2026-11-27", "2026-11-22"),
            ),
        }
        for name, (sql, params) in queries.items():
            plans[name] = " | ".join(
                row[3] for row in self.db._conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
            )

        self.assertIn("USING INDEX idx_key_ts", plans["keystrokes_day"])
        self.assertIn("USING INDEX idx_mouse_ts", plans["mouse_day"])
        self.assertIn("USING INDEX idx_interact_ts", plans["app_interactions_day"])
        self.assertIn("USING INDEX idx_interact_ts", plans["app_interactions_hour"])
        self.assertIn("USING COVERING INDEX idx_obs_comp_ended_started", plans["obs_segments_covering"])
        self.assertIn("USING COVERING INDEX idx_fg_ended_started", plans["fg_intervals_covering"])

    def test_app_usage_open_ended_session_and_hour_detail_boundaries(self):
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        # Open-ended session (end IS NULL) started yesterday should be included for both yesterday and today.
        self.db._conn.execute(
            "INSERT INTO app_usage(app,title,path,start,end) VALUES(?,?,?,?,?)",
            ("ActiveApp.exe", "", r"C:\ActiveApp.exe", f"{yesterday}T23:55:00", None),
        )
        # Closed session on yesterday
        self.db._conn.execute(
            "INSERT INTO app_usage(app,title,path,start,end) VALUES(?,?,?,?,?)",
            ("ClosedApp.exe", "", r"C:\ClosedApp.exe", f"{yesterday}T14:00:00", f"{yesterday}T14:05:00"),
        )
        # Hour boundary interactions: 13:59:59, 14:00:00, 14:59:59, 15:00:00
        for ts, app, k in (
            (f"{yesterday}T13:59:59+08:00", "PrevHour.exe", 11),
            (f"{yesterday}T14:00:00+08:00", "InHourA.exe", 22),
            (f"{yesterday}T14:59:59+08:00", "InHourB.exe", 33),
            (f"{yesterday}T15:00:00+08:00", "NextHour.exe", 44),
        ):
            self.db._conn.execute(
                "INSERT INTO app_interactions(ts,app,path,keyboard,mouse) VALUES(?,?,?,?,?)",
                (ts, app, "", k, 0),
            )
        self.db._conn.commit()

        y_stats = self.db.day_stats(yesterday)
        y_apps = {item["app"]: item["seconds"] for item in y_stats["apps"]}
        self.assertIn("ActiveApp.exe", y_apps)
        self.assertIn("ClosedApp.exe", y_apps)
        self.assertEqual(y_apps["ClosedApp.exe"], 300)

        t_apps = {item["app"] for item in self.db.app_chart(today)}
        self.assertIn("ActiveApp.exe", t_apps)

        hour14 = self.db.hour_detail(yesterday, 14)
        hour14_apps = {item["app"]: item["keyboard"] for item in hour14["apps"]}
        self.assertEqual(hour14_apps, {"InHourA.exe": 22, "InHourB.exe": 33})

    def test_month_and_year_heatmap_merge_raw_and_archived_without_double_counting(self):
        # Raw row exists for 2026-03-10 (including zero-count edge case), archived only for 2026-03-11.
        self.db._conn.execute("INSERT INTO keystrokes(ts,count) VALUES(?,?)", ("2026-03-10T10:00:00+08:00", 0))
        self.db._conn.execute("INSERT INTO keystrokes(ts,count) VALUES(?,?)", ("2026-03-12T10:00:00+08:00", 50))
        self.db._conn.execute("INSERT INTO mouse_events(ts,count) VALUES(?,?)", ("2026-03-12T10:00:00+08:00", 7))
        self.db._conn.execute(
            "INSERT INTO daily_totals(date,keyboard,mouse,responses,updated_at) VALUES(?,?,?,?,?)",
            ("2026-03-10", 999, 888, 1887, "2026-03-11T00:00:00"),
        )
        self.db._conn.execute(
            "INSERT INTO daily_totals(date,keyboard,mouse,responses,updated_at) VALUES(?,?,?,?,?)",
            ("2026-03-11", 120, 30, 150, "2026-03-12T00:00:00"),
        )
        self.db._conn.execute(
            "INSERT INTO daily_totals(date,keyboard,mouse,responses,updated_at) VALUES(?,?,?,?,?)",
            ("2026-03-12", 50, 7, 57, "2026-03-13T00:00:00"),
        )
        self.db._conn.commit()

        month = self.db.month_heatmap("2026-03")
        by_day = {item["date"]: item for item in month["days"]}
        # Raw keystrokes row on 2026-03-10 exists (even with count 0), so keystrokes must not fall back to 999.
        self.assertEqual(by_day["2026-03-10"]["keyboard"], 0)
        # No raw mouse_events row on 2026-03-10, so mouse falls back to archived 888 (matching NOT EXISTS semantics).
        self.assertEqual(by_day["2026-03-10"]["mouse"], 888)
        self.assertEqual(by_day["2026-03-11"]["keyboard"], 120)
        self.assertEqual(by_day["2026-03-11"]["mouse"], 30)
        self.assertEqual(by_day["2026-03-12"]["responses"], 57)

        year = self.db.year_heatmap("2026")
        self.assertEqual(len(year["months"]), 12)
        self.assertEqual(year["months"][2], month)

    def test_schema_version_stays_compatible_with_v2(self):
        conn = sqlite3.connect(self.path)
        try:
            ver = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(ver, "2")


if __name__ == "__main__":
    unittest.main()
