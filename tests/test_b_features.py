"""B 功能候选：应用标签、趋势基线、回返间隔、敏感应用隐藏。"""
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from db import DB
from interaction_rhythm.observations import local_now
from interaction_rhythm.runtime_paths import DATA_DIR_ENV


def _iso(value):
    return value.isoformat(timespec="seconds")


class AppLabelTests(unittest.TestCase):
    """应用自定义标签：保存、清除、榜单与详情复用。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="ir-app-label-")
        self.path = Path(self.temp_dir.name) / "tracker.db"
        self.db = DB(self.path)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_label_round_trip_and_clear(self):
        self.db.save_app_label("Code.exe", "写作台")
        self.assertEqual(self.db.app_label_map()["Code.exe"], "写作台")
        self.db.save_app_label("Code.exe", "")
        self.assertNotIn("Code.exe", self.db.app_label_map())

    def test_label_is_stripped_and_length_bounded(self):
        saved = self.db.save_app_label("Code.exe", "  写作台  ")
        self.assertEqual(saved["label"], "写作台")
        long_label = "长" * 200
        self.db.save_app_label("Code.exe", long_label)
        self.assertEqual(len(self.db.app_label_map()["Code.exe"]), 80)

    def test_app_chart_carries_label(self):
        now = local_now()
        self.db.add_keystrokes(5, "Code.exe")
        self.db.save_app_label("Code.exe", "写作台")
        chart = self.db.app_chart(now.strftime("%Y-%m-%d"))
        row = next((r for r in chart if r["app"] == "Code.exe"), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["label"], "写作台")

    def test_app_detail_and_analysis_carry_label(self):
        now = local_now()
        start = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
        end = now.isoformat(timespec="seconds")
        self.db.add_session("Code.exe", "", start, end)
        self.db.save_app_label("Code.exe", "写作台")
        detail = self.db.app_detail("Code.exe", now.strftime("%Y-%m-%d"))
        self.assertEqual(detail["label"], "写作台")
        analysis = self.db.app_analysis("Code.exe", 7)
        self.assertEqual(analysis["label"], "写作台")


class TrendSummaryTests(unittest.TestCase):
    """周趋势与个人基线带。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="ir-trend-")
        self.path = Path(self.temp_dir.name) / "tracker.db"
        self.db = DB(self.path)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_trend_weekly_aggregation_and_baseline_shape(self):
        now = local_now()
        base = now - timedelta(days=2)
        self.db.add_keystrokes(5, "Code.exe")
        self.db.add_mouse_events(3, "Code.exe")
        self.db.add_foreground_interval(
            "Code.exe", "", _iso(base), _iso(base + timedelta(minutes=10)), "running"
        )
        self.db.build_daily_totals()

        trend = self.db.trend_summary(8)
        self.assertEqual(len(trend["weeks"]), 8)
        self.assertIn("input", trend["baseline"])
        self.assertIn("foreground_seconds", trend["baseline"])
        last = trend["weeks"][-1]
        self.assertGreaterEqual(last["input_events"], 8)
        self.assertGreaterEqual(last["active_days"], 1)
        interval_week = (base.date() - timedelta(days=base.weekday())).isoformat()
        interval_row = next(row for row in trend["weeks"] if row["week_start"] == interval_week)
        self.assertGreater(interval_row["foreground_seconds"], 0)

    def test_trend_weeks_are_monday_aligned_and_ordered(self):
        trend = self.db.trend_summary(6)
        starts = [w["week_start"] for w in trend["weeks"]]
        self.assertEqual(starts, sorted(starts))
        # 每一周起点必须是周一
        from datetime import datetime

        for start in starts:
            self.assertEqual(datetime.fromisoformat(start).weekday(), 0)


class ReturnIntervalTests(unittest.TestCase):
    """回返间隔与应用转移的描述性统计。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="ir-return-")
        self.path = Path(self.temp_dir.name) / "tracker.db"
        self.db = DB(self.path)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_return_pairs_and_switch_count(self):
        now = local_now()
        base = now - timedelta(days=2)
        self.db.add_foreground_interval("AppA.exe", "", _iso(base), _iso(base + timedelta(minutes=10)), "running")
        self.db.add_foreground_interval("AppB.exe", "", _iso(base + timedelta(minutes=10)), _iso(base + timedelta(minutes=20)), "running")
        self.db.add_foreground_interval("AppA.exe", "", _iso(base + timedelta(minutes=20)), _iso(base + timedelta(minutes=30)), "running")

        result = self.db.return_interval_summary(7)
        self.assertGreaterEqual(result["switch_count"], 2)
        self.assertGreaterEqual(result["active_days"], 1)
        app_a = next(a for a in result["apps"] if a["name"] == "AppA.exe")
        self.assertEqual(app_a["returns"], 1)
        self.assertEqual(app_a["median_seconds"], 600)

    def test_gap_overlap_excludes_return_pair(self):
        now = local_now()
        base = now - timedelta(days=2)
        self.db.add_foreground_interval("AppA.exe", "", _iso(base), _iso(base + timedelta(minutes=10)), "running")
        self.db.add_foreground_interval("AppA.exe", "", _iso(base + timedelta(minutes=20)), _iso(base + timedelta(minutes=30)), "running")
        self.db.add_observation_segment(
            "collector", "unknown", _iso(base + timedelta(minutes=5)), _iso(base + timedelta(minutes=25))
        )

        result = self.db.return_interval_summary(7)
        app_a = next((a for a in result["apps"] if a["name"] == "AppA.exe"), None)
        self.assertIsNone(app_a)

    def test_short_window_bounds_days(self):
        now = local_now()
        base = now - timedelta(days=2)
        self.db.add_foreground_interval("AppA.exe", "", _iso(base), _iso(base + timedelta(minutes=10)), "running")
        result = self.db.return_interval_summary(1)
        self.assertLessEqual(result["days"], 1)


class HiddenAppsSettingsTests(unittest.TestCase):
    """敏感应用隐藏列表：持久化、去重、清洗与清空。"""

    @classmethod
    def setUpClass(cls):
        cls._previous_data_dir = os.environ.get(DATA_DIR_ENV)
        cls._tmp = tempfile.TemporaryDirectory(prefix="ir-hidden-apps-")
        os.environ[DATA_DIR_ENV] = str(Path(cls._tmp.name) / "data")
        import importlib
        import settings as settings_mod

        importlib.reload(settings_mod)
        cls.settings = settings_mod

    @classmethod
    def tearDownClass(cls):
        if cls._previous_data_dir is None:
            os.environ.pop(DATA_DIR_ENV, None)
        else:
            os.environ[DATA_DIR_ENV] = cls._previous_data_dir
        cls._tmp.cleanup()

    def setUp(self):
        self.settings.save_settings({"hidden_apps": []})

    def test_hidden_apps_round_trip_with_dedup_and_trim(self):
        saved = self.settings.save_settings(
            {"hidden_apps": ["Chrome", " WeChat ", "Chrome"]}
        )
        self.assertEqual(saved["hidden_apps"], ["Chrome", "WeChat"])
        loaded = self.settings.get_settings()
        self.assertEqual(loaded["hidden_apps"], ["Chrome", "WeChat"])

    def test_hidden_apps_can_be_cleared(self):
        self.settings.save_settings({"hidden_apps": ["Chrome"]})
        self.settings.save_settings({"hidden_apps": []})
        self.assertEqual(self.settings.get_settings()["hidden_apps"], [])

    def test_hidden_apps_list_is_bounded(self):
        long_list = [f"App{i}.exe" for i in range(60)]
        saved = self.settings.save_settings({"hidden_apps": long_list})
        self.assertLessEqual(len(saved["hidden_apps"]), 50)
