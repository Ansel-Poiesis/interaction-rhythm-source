import json
import tempfile
import unittest
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from interaction_rhythm.agent_observations import (
    TheoryCard,
    build_observation_feed,
    build_weekly_evidence_pack,
    purpose_catalog,
    validate_theory_card,
)
from interaction_rhythm.theory_catalog import REVIEWED_THEORY_CARDS
from stats import StatsServer


class FakeObservationDB:
    def __init__(self, coverage_ratio=0.95):
        self.coverage_ratio = coverage_ratio

    def list_agent_corrections(self, end_date, purpose_id):
        return []

    def day_metrics(self, date):
        return {
            "date": date,
            "metric_version": "0.2.0",
            "events": {"input_event_count": 180},
            "foreground_seconds": 7200,
            "coverage": {
                "status": "verified",
                "ratio": self.coverage_ratio,
            },
        }

    def personal_reference(self, metric_id, date, minimum_coverage_ratio):
        position = "above_p75" if metric_id == "IR-EVT-004" else "within_p25_p75"
        return {
            "source_metric_id": metric_id,
            "status": "provisional",
            "stage": "typical_range",
            "n_valid_days": 16,
            "position": position,
            "p25": 100,
            "p75": 150,
            "coverage_gate": {"minimum_ratio": minimum_coverage_ratio},
            "current": {"date": date},
        }


class ShortReferenceDB(FakeObservationDB):
    def personal_reference(self, metric_id, date, minimum_coverage_ratio):
        position = "above_p75" if metric_id == "IR-EVT-004" else "within_p25_p75"
        return {
            "source_metric_id": metric_id,
            "status": "provisional",
            "stage": "initial_reference",
            "n_valid_days": 10,
            "position": position,
            "p25": 100,
            "p75": 150,
            "coverage_gate": {"minimum_ratio": minimum_coverage_ratio},
            "current": {"date": date},
        }


class ColdReferenceDB(FakeObservationDB):
    def personal_reference(self, metric_id, date, minimum_coverage_ratio):
        return {
            "source_metric_id": metric_id,
            "status": "insufficient_data",
            "stage": "facts_only",
            "n_valid_days": 2,
            "position": None,
            "p25": None,
            "p75": None,
            "coverage_gate": {"minimum_ratio": minimum_coverage_ratio},
            "current": {"date": date},
        }


class AgentObservationFrameworkTests(unittest.TestCase):
    def test_catalog_exposes_only_curated_purposes(self):
        catalog = purpose_catalog()

        self.assertEqual(catalog["framework_version"], "0.2.0")
        self.assertEqual(
            [item["purpose_id"] for item in catalog["purposes"]],
            ["understand_personal_rhythm", "notice_rhythm_change"],
        )

    def test_evidence_pack_is_stable_for_identical_local_facts(self):
        db = FakeObservationDB()

        first = build_weekly_evidence_pack(db, "2026-07-20")
        second = build_weekly_evidence_pack(db, "2026-07-20")

        self.assertEqual(first["evidence_hash"], second["evidence_hash"])
        self.assertEqual(first["range"], {"start": "2026-07-14", "end": "2026-07-20", "days": 7})
        self.assertEqual(first["anchor_date"], "2026-07-20")

    def test_feed_returns_observational_conclusions_without_advice_or_dialogue(self):
        feed = build_observation_feed(FakeObservationDB(), "2026-07-20")
        rendered = "\n".join(item["conclusion"] for item in feed["observations"])

        self.assertTrue(feed["ok"])
        self.assertFalse(feed["model_used"])
        self.assertFalse(feed["conversation_enabled"])
        self.assertFalse(feed["advice_enabled"])
        self.assertEqual(len(feed["observations"]), 3)
        self.assertEqual(feed["observations"][1]["result"], "reference_change_observed")
        self.assertEqual(feed["observations"][2]["result"], "within_typical_range")
        self.assertTrue(feed["observations"][1]["prompt"])
        self.assertTrue(feed["observations"][2]["prompt"])
        self.assertNotIn("建议", rendered)
        self.assertNotIn("你可以", rendered)
        self.assertNotIn("效率", rendered)
        self.assertNotIn("专注", rendered)

    def test_insufficient_coverage_withholds_reference_conclusions(self):
        feed = build_observation_feed(FakeObservationDB(coverage_ratio=0.5), "2026-07-20")

        self.assertEqual(len(feed["observations"]), 1)
        self.assertEqual(feed["observations"][0]["result"], "insufficient_data")
        self.assertEqual(feed["evidence_pack"]["anchor_date"], None)
        self.assertIn("稳定记录还不够", feed["observations"][0]["conclusion"])
        self.assertIn("暂时无法回看本周变化", feed["observations"][0]["conclusion"])

    def test_user_facing_conclusions_are_concise_and_hide_research_language(self):
        feed = build_observation_feed(FakeObservationDB(), "2026-07-20")
        conclusions = [item["conclusion"] for item in feed["observations"]]

        self.assertEqual(conclusions[0], "最近 7 天都有稳定记录，可以用来回看本周变化。")
        self.assertEqual(conclusions[1], "7月20日的输入事件高于你近期通常的范围。")
        self.assertEqual(conclusions[2], "7月20日的前台时长落在你近期通常的范围内。")
        rendered = "\n".join(conclusions)
        self.assertNotIn("数据显示", rendered)
        self.assertNotIn("典型范围", rendered)
        self.assertNotIn("这段", rendered)

    def test_reference_status_remains_visible_during_cold_start(self):
        feed = build_observation_feed(ColdReferenceDB(), "2026-07-20")

        self.assertEqual(len(feed["observations"]), 1)
        self.assertEqual(feed["observations"][0]["result"], "qualified_data_available")
        status = feed["evidence_pack"]["reference_status"]
        self.assertEqual(status["IR-EVT-004"]["stage"], "facts_only")
        self.assertEqual(status["IR-EVT-004"]["n_valid_days"], 2)
        self.assertFalse(status["IR-EVT-004"]["position_available"])
        self.assertEqual(
            status["IR-EVT-004"]["minimum_valid_days_for_position"],
            7,
        )

    def test_theory_cards_only_attach_when_metric_and_sample_conditions_match(self):
        card = TheoryCard(
            theory_id="personal-informatics-reflection",
            title="Personal informatics reflection",
            source_url="https://example.test/research",
            evidence_level="R3",
            lens="Use the change as a reflection lens, not a causal explanation.",
            original_domain="personal informatics",
            transfer_boundary="It does not establish productivity, attention, or causality.",
            observation_kinds=("reference_position",),
            required_metric_ids=("IR-EVT-004", "IR-BAS-001"),
            minimum_valid_days=14,
        )

        feed = build_observation_feed(FakeObservationDB(), "2026-07-20", theory_cards=(card,))
        input_observation = feed["observations"][1]
        foreground_observation = feed["observations"][2]

        self.assertEqual(validate_theory_card(card), "")
        self.assertEqual(
            [item["theory_id"] for item in input_observation["theory_context"]],
            ["personal-informatics-reflection"],
        )
        self.assertEqual(foreground_observation["theory_context"], [])
        self.assertNotIn("reflection", input_observation["conclusion"].lower())

    def test_reference_position_cards_respect_metric_and_sample_gates(self):
        feed = build_observation_feed(ShortReferenceDB(), "2026-07-20", theory_cards=REVIEWED_THEORY_CARDS)

        self.assertEqual(
            [item["theory_id"] for item in feed["observations"][1]["theory_context"]],
            ["reflection-nature-critical-examination"],
        )
        self.assertEqual(
            [item["theory_id"] for item in feed["observations"][2]["theory_context"]],
            ["reflection-nature-critical-examination"],
        )

    def test_invalid_purpose_and_theory_card_are_rejected_or_ignored(self):
        invalid_card = TheoryCard(
            theory_id="",
            title="Untitled",
            source_url="not-a-url",
            evidence_level="R9",
            lens="",
            original_domain="",
            transfer_boundary="",
            observation_kinds=(),
            required_metric_ids=(),
        )

        self.assertEqual(validate_theory_card(invalid_card), "theory_id_and_title_required")
        with self.assertRaisesRegex(ValueError, "unsupported observation purpose"):
            build_observation_feed(FakeObservationDB(), "2026-07-20", "free-form-goal")

    def test_loopback_api_is_read_only_and_exposes_the_framework(self):
        self.assertEqual(len(REVIEWED_THEORY_CARDS), 8)
        self.assertEqual(
            [card.theory_id for card in REVIEWED_THEORY_CARDS[:3]],
            [
                "stage-based-personal-informatics",
                "quantified-self-collection-exploration",
                "flexible-minimalist-self-tracking",
            ],
        )
        self.assertEqual(
            [card.theory_id for card in REVIEWED_THEORY_CARDS[3:]],
            [
                "reflection-nature-critical-examination",
                "people-like-me-cohort-comparison",
                "reflection-theory-practice-gaps",
                "daily-mood-variability-stability",
                "weekend-recovery-rituals",
            ],
        )

        server = StatsServer(FakeObservationDB(), port=0)
        server.start()
        try:
            purposes_response = urllib.request.urlopen(
                f"http://127.0.0.1:{server.port}/api/agent/purposes", timeout=2
            )
            observations_response = urllib.request.urlopen(
                f"http://127.0.0.1:{server.port}/api/agent/observations"
                "?date=2026-07-20&purpose=notice_rhythm_change",
                timeout=2,
            )
            try:
                purposes = json.loads(purposes_response.read().decode("utf-8"))
                observations = json.loads(observations_response.read().decode("utf-8"))
            finally:
                purposes_response.close()
                observations_response.close()
        finally:
            server.stop()

        self.assertEqual(purposes["framework_version"], "0.2.0")
        self.assertEqual(observations["surface"], "weekly_observations")
        self.assertEqual(len(observations["observations"]), 3)
        self.assertFalse(observations["model_used"])
        self.assertEqual(
            [item["theory_id"] for item in observations["observations"][0]["theory_context"]],
            [REVIEWED_THEORY_CARDS[0].theory_id, REVIEWED_THEORY_CARDS[1].theory_id],
        )
        self.assertEqual(
            [item["theory_id"] for item in observations["observations"][1]["theory_context"]],
            [
                "reflection-nature-critical-examination",
                "people-like-me-cohort-comparison",
            ],
        )
        self.assertEqual(
            [item["theory_id"] for item in observations["observations"][2]["theory_context"]],
            [
                "reflection-nature-critical-examination",
                "reflection-theory-practice-gaps",
            ],
        )


class RealDatabaseAgentObservationTests(unittest.TestCase):
    def setUp(self):
        from db import DB

        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DB(Path(self.temp_dir.name) / "tracker.db")
        start = datetime(2026, 7, 14, 9, 0, 0)
        for offset in range(7):
            day = start + timedelta(days=offset)
            self.db.add_observation_segment(
                "collector", "running", day, day.replace(hour=17)
            )
            self.db.add_foreground_interval(
                "Code.exe", "", day, day.replace(hour=17)
            )

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_real_sqlite_v2_feed_returns_traceable_observations(self):
        feed = build_observation_feed(self.db, "2026-07-20")

        self.assertTrue(feed["ok"])
        self.assertFalse(feed["model_used"])
        self.assertEqual(feed["surface"], "weekly_observations")
        self.assertEqual(feed["observations"][0]["kind"], "coverage")
        self.assertEqual(feed["observations"][0]["claim_ids"], ["A09"])
        self.assertIn("IR-HLT-003", feed["observations"][0]["metric_ids"])
        self.assertEqual(feed["evidence_pack"]["anchor_date"], "2026-07-20")

    def test_real_sqlite_v2_coverage_gate_matches_daily_metrics(self):
        feed = build_observation_feed(self.db, "2026-07-20")
        qualified = feed["observations"][0]["n_valid_days"]

        self.assertEqual(qualified, 7)
        self.assertEqual(self.db.day_metrics("2026-07-20")["coverage"]["ratio"], 1.0)


class VariedObservationDB(FakeObservationDB):
    """Daily input varies: weekdays higher, weekends lower, one clear peak."""

    def day_metrics(self, date):
        events_by_date = {
            "2026-07-14": 200,
            "2026-07-15": 210,
            "2026-07-16": 220,
            "2026-07-17": 205,
            "2026-07-18": 120,
            "2026-07-19": 110,
            "2026-07-20": 230,
        }
        base = dict(super().day_metrics(date))
        base["events"] = dict(base.get("events") or {})
        base["events"]["input_event_count"] = events_by_date.get(date, 180)
        return base


class RichWeeklyObservationTests(unittest.TestCase):
    def test_daily_variation_and_weekend_pattern_observations_appear(self):
        feed = build_observation_feed(VariedObservationDB(), "2026-07-20")

        kinds = [item["kind"] for item in feed["observations"]]
        self.assertIn("daily_variation", kinds)
        self.assertIn("weekend_pattern", kinds)
        self.assertEqual(len(feed["observations"]), 5)

        variation = next(item for item in feed["observations"] if item["kind"] == "daily_variation")
        self.assertIn("输入量是本周最高", variation["conclusion"])
        self.assertIn("倍", variation["conclusion"])
        self.assertTrue(variation["prompt"])
        self.assertEqual(variation["result"], "wide_daily_variation")

        weekend = next(item for item in feed["observations"] if item["kind"] == "weekend_pattern")
        self.assertIn("比周末高约", weekend["conclusion"])
        self.assertEqual(weekend["result"], "weekend_weekday_gap")
        self.assertTrue(weekend["prompt"])

    def test_new_observations_stay_factual_without_advice_language(self):
        feed = build_observation_feed(VariedObservationDB(), "2026-07-20")
        rendered = "\n".join(item["conclusion"] for item in feed["observations"])

        for banned in ("建议", "你可以", "效率", "专注", "数据显示", "典型范围"):
            self.assertNotIn(banned, rendered)

    def test_daily_variation_and_weekend_pattern_attach_reviewed_cards(self):
        feed = build_observation_feed(
            VariedObservationDB(),
            "2026-07-20",
            theory_cards=REVIEWED_THEORY_CARDS,
        )

        variation = next(item for item in feed["observations"] if item["kind"] == "daily_variation")
        weekend = next(item for item in feed["observations"] if item["kind"] == "weekend_pattern")

        self.assertIn(
            "daily-mood-variability-stability",
            [item["theory_id"] for item in variation["theory_context"]],
        )
        self.assertIn(
            "weekend-recovery-rituals",
            [item["theory_id"] for item in weekend["theory_context"]],
        )


if __name__ == "__main__":
    unittest.main()
