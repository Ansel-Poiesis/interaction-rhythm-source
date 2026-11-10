"""每周报告生成器：聚合口径、标签/隐藏过滤、叙事与 HTML 结构。"""
import re
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from db import DB
from interaction_rhythm.observations import local_now

import weekly_report


def _iso(value):
    return value.isoformat(timespec="seconds")


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="ir-weekly-report-")
        self.path = Path(self.temp_dir.name) / "tracker.db"
        self.db = DB(self.path)
        self.today = local_now().date().isoformat()
        now = local_now()
        self.db.add_keystrokes(12, "Code.exe")
        self.db.add_keystrokes(8, "ChatGPT.exe")
        self.db.add_session(
            "Code.exe", "", _iso(now - timedelta(hours=3)), _iso(now - timedelta(hours=2))
        )
        self.db.add_session(
            "ChatGPT.exe", "", _iso(now - timedelta(hours=2)), _iso(now - timedelta(hours=1))
        )
        self.db.add_session(
            "Code.exe", "", _iso(now - timedelta(hours=1)), _iso(now)
        )

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_load_data_window_and_input_counts(self):
        data = weekly_report.load_data(self.db, self.today, {})
        self.assertEqual(data["window"]["days"], 7)
        self.assertEqual(data["window"]["end"], self.today)
        self.assertGreaterEqual(data["summary"]["input_events"], 20)
        self.assertGreaterEqual(data["summary"]["foreground_seconds"], 3600)
        self.assertGreaterEqual(data["summary"]["avg_daily_input"], 1)
        self.assertGreaterEqual(data["summary"]["avg_daily_fg"], 60)
        self.assertGreater(data["summary"]["density_per_min"], 0)
        self.assertGreaterEqual(data["summary"]["session_count"], 1)
        self.assertIn("input", data["change"])
        self.assertIn("density", data["change"])
        self.assertTrue(data["analysis"]["lines"])
        self.assertTrue(data["narrative"]["insights"])
        self.assertTrue(data["narrative"]["suggestions"])

    def test_label_used_in_top_apps_and_return_apps(self):
        self.db.save_app_label("Code.exe", "写作台")
        data = weekly_report.load_data(self.db, self.today, {})
        displays = [app["display"] for app in data["top_apps"]]
        self.assertIn("写作台", displays)
        return_displays = [app["display"] for app in data["returns"]["apps"]]
        self.assertIn("写作台", return_displays)

    def test_hidden_app_filtered_but_still_counted(self):
        data = weekly_report.load_data(self.db, self.today, {"hidden_apps": ["Code.exe"]})
        displays = [app["display"] for app in data["top_apps"]]
        self.assertNotIn("VS Code", displays)
        self.assertNotIn("Code", displays)
        self.assertGreaterEqual(data["summary"]["input_events"], 20)

    def test_facts_only_note_when_baseline_insufficient(self):
        data = weekly_report.load_data(self.db, self.today, {})
        self.assertEqual(data["reference"]["input"]["position"], None)
        insight_text = "".join(item["text"] for item in data["narrative"]["insights"])
        self.assertIn("积累", insight_text)

    def test_html_contains_sections_and_no_external_links(self):
        data = weekly_report.load_data(self.db, self.today, {})
        html = weekly_report.build_html(data)
        for title in (
            "本周判断",
            "本周概览",
            "本周变化",
            "节奏画像",
            "常态对照",
            "应用主场",
            "回返节奏",
            "输入趋势 · 数据积累以来",
            "线索与解读",
            "下一步建议",
            "鼓励持续使用",
            "解读 ·",
        ):
            self.assertIn(title, html)
        self.assertIn("日均前台", html)
        self.assertIn("只显示有数据的周", html)
        self.assertIn("常态对照仍在积累", html)
        self.assertNotIn("28 天中位", html)
        self.assertNotIn("本地生成", html)
        self.assertNotIn("判断基于本周聚合数据", html)
        self.assertNotIn("以上只描述聚合记录", html)
        self.assertNotIn("基于本周数据得出的结论", html)
        # 解读按全角冒号/分号自动分段，半角时间写法（17:00）不得被切断
        split_blocks = len(re.findall(r"</div><div style=\"margin-top:4px;\">", html))
        self.assertGreaterEqual(split_blocks, 6)
        self.assertIn("前台时长只回答「窗口在最前面多久」：</div>", html)
        for marker in ("《07-", "《08-", "《09-", "《10-", "《11-", "《12-", "《13-"):
            self.assertNotIn(marker, html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_clause_blocks_split_on_fullwidth_punctuation_only(self):
        blocks = weekly_report._clause_blocks(
            "高峰集中在 17:00 前后：输入呈脉冲式分布；而不是持续高压。"
        )
        self.assertIn("17:00", blocks)
        self.assertIn("高峰集中在 17:00 前后：</div>", blocks)
        self.assertIn("<div style=\"margin-top:4px;\">输入呈脉冲式分布；</div>", blocks)

    def test_reference_table_stays_simple_under_28_days(self):
        data = {
            "reference": {
                "input": {
                    "n_valid_days": 10,
                    "avg": 1000.0,
                    "p25": 800.0,
                    "p75": 1200.0,
                },
                "foreground_seconds": {
                    "n_valid_days": 10,
                    "avg": 3600.0,
                    "p25": 3000.0,
                    "p75": 4200.0,
                },
            }
        }
        html = weekly_report._reference_table(data)
        self.assertIn("个人常态对照仍在积累", html)
        self.assertNotIn("28 天中位", html)

    def test_formatting_helpers(self):
        self.assertEqual(weekly_report._fmt_duration(3600), "1 小时")
        self.assertEqual(weekly_report._fmt_duration(3666), "1 小时 1 分钟")
        self.assertEqual(weekly_report._fmt_duration(90), "1 分钟")
        self.assertEqual(weekly_report._fmt_count(135705), "13.6 万")
        self.assertEqual(weekly_report._fmt_count(5122), "5,122")

    def test_corpus_profile_fresh_db(self):
        profile = self.db.corpus_profile()
        self.assertGreaterEqual(profile["history_days"], 1)
        self.assertGreaterEqual(profile["total_input_events"], 20)
        self.assertGreater(profile["db_size_mb"], 0)
        self.assertTrue(profile["first_date"])

    def test_detect_profile_tiers(self):
        def make(ref_days, data_weeks, history_days=0, total=0, db_mb=0.0, returns=None):
            return weekly_report.detect_profile(
                {
                    "reference": {"input": {"n_valid_days": ref_days}},
                    "trend": {"weeks": [
                        {
                            "input_events": 100 if index < data_weeks else 0,
                            "active_days": 1 if index < data_weeks else 0,
                        }
                        for index in range(12)
                    ]},
                    "returns": returns or {"apps": []},
                    "hidden_apps": [],
                    "app_labels": {},
                },
                {
                    "history_days": history_days,
                    "total_input_events": total,
                    "db_size_mb": db_mb,
                },
            )

        self.assertEqual(make(0, 0)["tier"], "S0")
        self.assertEqual(make(3, 1)["tier"], "S1")
        self.assertEqual(make(15, 2)["tier"], "S2")
        self.assertEqual(make(40, 5)["tier"], "S3")
        self.assertEqual(make(120, 12)["tier"], "S4")
        self.assertEqual(make(200, 12)["tier"], "S5")
        self.assertEqual(make(6, 12, history_days=70)["tier"], "S4")
        self.assertEqual(make(6, 12, history_days=70, db_mb=124.0)["tier"], "S4")
        self.assertEqual(make(6, 12, db_mb=600)["tier"], "S5")
        self.assertEqual(make(10, 3, total=6000000)["tier"], "S5")

    def test_high_frequency_returns_add_card(self):
        profile = weekly_report.detect_profile(
            {
                "reference": {"input": {"n_valid_days": 60}},
                "trend": {"weeks": [
                    {"input_events": 1, "active_days": 1} for _ in range(12)
                ]},
                "returns": {"apps": [{"median_seconds": 11, "returns": 1238}]},
                "hidden_apps": [],
                "app_labels": {},
            },
            {"history_days": 70, "total_input_events": 1000, "db_size_mb": 1},
        )
        self.assertEqual(profile["tier"], "S4")
        self.assertIn("R4", profile["references"])

    def test_hidden_apps_add_privacy_card(self):
        profile = weekly_report.detect_profile(
            {
                "reference": {"input": {"n_valid_days": 60}},
                "trend": {"weeks": [
                    {"input_events": 1, "active_days": 1} for _ in range(12)
                ]},
                "returns": {"apps": []},
                "hidden_apps": ["Code.exe"],
                "app_labels": {},
            },
            {"history_days": 70, "total_input_events": 1000, "db_size_mb": 1},
        )
        self.assertIn("R8", profile["references"])

    def test_s3_by_data_weeks_still_marks_accumulating_reference(self):
        # 有效日不足 28，但数据周已满 4 周：档位升到 S3，描述不得声称基线可用。
        profile = weekly_report.detect_profile(
            {
                "reference": {"input": {"n_valid_days": 7}},
                "trend": {"weeks": [
                    {"input_events": 1, "active_days": 1} for _ in range(4)
                ]},
                "returns": {"apps": []},
                "hidden_apps": [],
                "app_labels": {},
            },
            {"history_days": 40, "total_input_events": 1000, "db_size_mb": 1},
        )
        self.assertEqual(profile["tier"], "S3")
        self.assertEqual(profile["reference_stage"], "accumulating")
        self.assertIn("仍在积累", profile["description"])
        self.assertNotIn("基线可用", profile["description"])

    def test_html_contains_evidence_block(self):
        data = weekly_report.load_data(self.db, self.today, {})
        html = weekly_report.build_html(data)
        self.assertIn("证据引用 · 按数据规模", html)
        self.assertIn("当前数据规模：", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)


if __name__ == "__main__":
    unittest.main()
