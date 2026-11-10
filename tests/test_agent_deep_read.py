import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from db import DB
from interaction_rhythm.agent_observations import (
    apply_user_corrections,
    build_observation_feed,
)
from mimo_client import (
    agent_deep_read,
    agent_evidence_payload,
    build_agent_deep_read_messages,
)
from stats import StatsServer


class FakeObservationDB:
    def day_metrics(self, date):
        return {
            "date": date,
            "metric_version": "0.2.0",
            "events": {"input_event_count": 180},
            "foreground_seconds": 7200,
            "coverage": {"status": "verified", "ratio": 0.95},
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


def valid_sentences(feed):
    facts = {fact["fact_id"]: fact for fact in feed["evidence_pack"]["facts"]}
    sentences = []
    for observation in feed["observations"][:3]:
        fact_id = observation["fact_ids"][0]
        fact = facts[fact_id]
        sentences.append({
            "text": "数据显示，这段记录保持稳定。",
            "fact_ids": [fact_id],
            "metric_ids": [fact["metric_id"]],
            "claim_id": observation["claim_ids"][0],
            "metric_version": fact["metric_version"],
            "boundary": "仅描述本地聚合数据。",
        })
    return sentences


class AgentDeepReadTests(unittest.TestCase):
    def setUp(self):
        self.feed = build_observation_feed(FakeObservationDB(), "2026-07-20")

    def test_evidence_payload_contains_only_aggregate_whitelist_fields(self):
        payload = agent_evidence_payload(self.feed)
        rendered = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["surface"] if "surface" in payload else None, None)
        self.assertTrue(payload["facts"])
        self.assertEqual(len(payload["observations"]), 3)
        self.assertNotIn("tracker.db", rendered)
        self.assertNotIn("C:", rendered)
        self.assertNotIn("window", rendered)
        self.assertNotIn("executable_path", rendered)

    def test_deep_read_messages_are_structured_and_reference_only(self):
        messages = build_agent_deep_read_messages(self.feed)

        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        user = json.loads(messages[1]["content"].rsplit("\n", 1)[1])
        self.assertEqual(user["range"]["days"], 7)
        self.assertEqual(len(user["observations"]), 3)

    def test_deep_read_falls_back_to_local_feed_on_provider_error(self):
        with mock.patch(
            "mimo_client._call_ai",
            side_effect=RuntimeError("offline"),
        ):
            result = agent_deep_read(self.feed, thinking_enabled=False, timeout=3)

        self.assertTrue(result["fallback"])
        self.assertFalse(result["model_used"])
        self.assertEqual(result["provider"], "rules")
        self.assertEqual(len(result["observations"]), 3)
        self.assertEqual(result["observations"][0]["conclusion"], self.feed["observations"][0]["conclusion"])

    def test_deep_read_accepts_valid_output_and_forwards_thinking(self):
        sentences = valid_sentences(self.feed)
        with mock.patch(
            "mimo_client._call_ai",
            return_value=(json.dumps({"sentences": sentences}, ensure_ascii=False), "ollama", "gemma"),
        ) as call_ai:
            result = agent_deep_read(self.feed, thinking_enabled=True, timeout=3)

        self.assertTrue(result["model_used"])
        self.assertTrue(result["thinking_enabled"])
        self.assertFalse(result["fallback"])
        self.assertEqual(len(result["observations"]), 3)
        self.assertEqual(call_ai.call_args.kwargs["timeout"], 3)
        self.assertTrue(call_ai.call_args.kwargs["thinking_enabled"])
        self.assertEqual(
            sorted(result["provenance"]["claim_ids"]),
            ["A09", "Q01"],
        )

    def test_deep_read_rejects_forbidden_claim_and_text(self):
        coverage = self.feed["observations"][0]
        fact_id = coverage["fact_ids"][0]
        fact = next(item for item in self.feed["evidence_pack"]["facts"] if item["fact_id"] == fact_id)
        bad = {
            "sentences": [{
                "text": "这说明效率很高。",
                "fact_ids": [fact_id],
                "metric_ids": [fact["metric_id"]],
                "claim_id": "F01",
                "metric_version": fact["metric_version"],
                "boundary": "不可推断。",
            }]
        }
        with mock.patch(
            "mimo_client._call_ai",
            return_value=(json.dumps(bad, ensure_ascii=False), "ollama", "gemma"),
        ):
            result = agent_deep_read(self.feed, thinking_enabled=False, timeout=3)

        self.assertTrue(result["fallback"])
        self.assertFalse(result["model_used"])

    def test_user_corrections_filter_and_rewrite_observations(self):
        observations = self.feed["observations"]
        corrections = [
            {
                "observation_id": observations[1]["observation_id"],
                "kind": "ignore",
                "created_at": "2026-08-02T10:00:00",
            },
            {
                "observation_id": observations[2]["observation_id"],
                "kind": "correction",
                "corrected_text": "数据显示，这段前台时长处于近期典型范围，未显示明显偏离。",
                "created_at": "2026-08-02T10:01:00",
            },
        ]
        updated = apply_user_corrections(self.feed, corrections)

        self.assertEqual(len(updated["observations"]), 2)
        self.assertNotIn(
            observations[1]["observation_id"],
            [item["observation_id"] for item in updated["observations"]],
        )
        self.assertTrue(updated["observations"][1]["corrected"])
        self.assertIn("未显示明显偏离", updated["observations"][1]["conclusion"])

    def test_corrected_feed_reaches_outbound_payload_without_ignored_items(self):
        observations = self.feed["observations"]
        corrections = [
            {
                "observation_id": observations[1]["observation_id"],
                "kind": "ignore",
                "created_at": "2026-08-02T10:00:00",
            },
            {
                "observation_id": observations[2]["observation_id"],
                "kind": "correction",
                "corrected_text": "数据显示，这段前台时长处于近期典型范围，未显示明显偏离。",
                "created_at": "2026-08-02T10:01:00",
            },
        ]
        updated = apply_user_corrections(self.feed, corrections)
        payload = agent_evidence_payload(updated)

        outbound_ids = [item["observation_id"] for item in payload["observations"]]
        self.assertNotIn(observations[1]["observation_id"], outbound_ids)
        self.assertEqual(len(payload["user_corrections"]), 2)
        self.assertTrue(any(
            item.get("kind") == "ignore"
            for item in payload["user_corrections"]
        ))
        self.assertTrue(any(
            item.get("kind") == "correction"
            and item.get("corrected_text", "").startswith("数据显示")
            for item in payload["user_corrections"]
        ))


class RealDatabaseCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DB(Path(self.temp_dir.name) / "tracker.db")

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_corrections_persist_and_roundtrip(self):
        saved = self.db.save_agent_correction(
            observation_id="coverage-qualified-days",
            end_date="2026-07-20",
            purpose_id="understand_personal_rhythm",
            kind="rename",
            label="我的稳定周",
        )
        rows = self.db.list_agent_corrections("2026-07-20", "understand_personal_rhythm")

        self.assertEqual(saved["kind"], "rename")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "我的稳定周")

    def test_corrections_api_requires_session_and_saves(self):
        server = StatsServer(self.db, port=0)
        server.start()
        try:
            root = urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=2)
            cookie = root.headers.get("Set-Cookie", "")
            root.close()
            session_cookie = cookie.split(";", 1)[0]
            headers = {
                "Content-Type": "application/json",
                "Cookie": session_cookie,
                "Origin": f"http://127.0.0.1:{server.port}",
            }
            denied = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/api/agent/corrections",
                data=b'{"kind":"rename","label":"x"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(denied, timeout=2)
            self.assertEqual(exc.exception.code, 403)

            request = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/api/agent/corrections",
                data=json.dumps({
                    "observation_id": "coverage-qualified-days",
                    "end_date": "2026-07-20",
                    "purpose_id": "understand_personal_rhythm",
                    "kind": "rename",
                    "label": "我的稳定周",
                }).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            response = urllib.request.urlopen(request, timeout=2)
            body = json.loads(response.read().decode("utf-8"))
            response.close()
        finally:
            server.stop()

        self.assertTrue(body["ok"])
        self.assertEqual(body["correction"]["label"], "我的稳定周")


if __name__ == "__main__":
    unittest.main()
