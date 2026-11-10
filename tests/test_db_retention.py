import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from db import DB


class DatabaseRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DB(Path(self.temp_dir.name) / "tracker.db")
        self.now = datetime.now()

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def _insert_detail(self, days_ago, keyboard=10, mouse=5):
        ts = (self.now - timedelta(days=days_ago)).isoformat(timespec="seconds")
        conn = self.db._conn
        conn.execute("INSERT INTO keystrokes(ts,count) VALUES(?,?)", (ts, keyboard))
        conn.execute("INSERT INTO mouse_events(ts,count) VALUES(?,?)", (ts, mouse))
        conn.execute(
            "INSERT INTO app_usage(app,title,start,end,path) VALUES(?,?,?,?,?)",
            ("app", "", ts, ts, ""),
        )
        conn.execute(
            "INSERT INTO app_interactions(ts,app,path,keyboard,mouse) VALUES(?,?,?,?,?)",
            (ts, "app", "", keyboard, mouse),
        )
        conn.execute(
            "INSERT INTO interaction_buckets("
            "started_at,ended_at,process_name,executable_path,"
            "keyboard_down_count,mouse_button_count,scroll_event_count,"
            "collector_version,health_state) VALUES(?,?,?,?,?,?,?,?,?)",
            (ts, ts, "app", "", keyboard, mouse, 0, "test", "running"),
        )
        conn.commit()

    def test_build_daily_totals_materializes_raw_counts(self):
        self._insert_detail(days_ago=2)
        self._insert_detail(days_ago=3, keyboard=20, mouse=7)

        count = self.db.build_daily_totals()

        self.assertGreaterEqual(count, 2)
        rows = self.db._conn.execute(
            "SELECT date, keyboard, mouse, responses FROM daily_totals "
            "WHERE date >= ?",
            ((self.now - timedelta(days=5)).strftime("%Y-%m-%d"),),
        ).fetchall()
        by_date = {date: (keyboard, mouse, responses) for date, keyboard, mouse, responses in rows}
        recent = (self.now - timedelta(days=2)).strftime("%Y-%m-%d")
        older = (self.now - timedelta(days=3)).strftime("%Y-%m-%d")
        self.assertEqual(by_date[recent], (10, 5, 15))
        self.assertEqual(by_date[older], (20, 7, 27))

    def test_cleanup_retains_aggregates_longer_than_raw_detail(self):
        self._insert_detail(days_ago=10)
        self._insert_detail(days_ago=220, keyboard=99, mouse=99)

        deleted = self.db.cleanup(days=365, hourly_days=30)

        self.assertGreater(deleted, 0)
        conn = self.db._conn
        recent_ts = (self.now - timedelta(days=10)).isoformat(timespec="seconds")
        old_ts = (self.now - timedelta(days=220)).isoformat(timespec="seconds")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM keystrokes WHERE ts=?", (recent_ts,)).fetchone()[0],
            1,
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM keystrokes WHERE ts=?", (old_ts,)).fetchone()[0],
            0,
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM interaction_buckets WHERE started_at=?", (old_ts,)).fetchone()[0],
            0,
        )
        # Aggregates follow the longer window, so the old app_usage row survives.
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM app_usage WHERE start=?", (old_ts,)).fetchone()[0],
            1,
        )
        # The old raw detail was materialized into daily totals before deletion.
        old_date = (self.now - timedelta(days=220)).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT keyboard FROM daily_totals WHERE date=?",
            (old_date,),
        ).fetchone()
        self.assertEqual(row[0], 99)

    def test_daily_list_and_month_heatmap_survive_detail_cleanup(self):
        self._insert_detail(days_ago=220, keyboard=42, mouse=8)
        self.db.cleanup(days=365, hourly_days=30)

        daily = {item["date"]: item["count"] for item in self.db.daily_keystrokes(365)}
        old_date = (self.now - timedelta(days=220)).strftime("%Y-%m-%d")
        self.assertEqual(daily.get(old_date), 42)

        month = self.db.month_heatmap(
            (self.now - timedelta(days=220)).strftime("%Y-%m")
        )
        day = next(item for item in month["days"] if item["date"] == old_date)
        self.assertEqual(day["keyboard"], 42)
        self.assertEqual(day["responses"], 50)

    def test_compact_and_file_size_report(self):
        self._insert_detail(days_ago=1)

        self.assertGreater(self.db.file_size(), 0)
        self.db.compact()
        self.assertGreater(self.db.file_size(), 0)


if __name__ == "__main__":
    unittest.main()
