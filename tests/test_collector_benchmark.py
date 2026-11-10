from collections import Counter
import json
import subprocess
import sys
import unittest
from pathlib import Path

from interaction_rhythm.benchmarking import (
    compare_distributions,
    run_observation_pipeline_benchmark,
)


ROOT = Path(__file__).resolve().parents[1]


class CollectorBenchmarkTests(unittest.TestCase):
    def test_distribution_comparison_separates_loss_duplicates_and_attribution(self):
        expected = Counter({("keyboard", "Code.exe"): 3, ("scroll", "Edge.exe"): 2})

        lost = compare_distributions(
            expected,
            Counter({("keyboard", "Code.exe"): 2, ("scroll", "Edge.exe"): 2}),
        )
        duplicated = compare_distributions(
            expected,
            Counter({("keyboard", "Code.exe"): 4, ("scroll", "Edge.exe"): 2}),
        )
        moved = compare_distributions(
            expected,
            Counter({("keyboard", "Edge.exe"): 3, ("scroll", "Edge.exe"): 2}),
        )

        self.assertEqual(lost["lost_event_count"], 1)
        self.assertEqual(lost["attribution_error_count"], 0)
        self.assertEqual(duplicated["duplicate_event_count"], 1)
        self.assertEqual(duplicated["attribution_error_count"], 0)
        self.assertEqual(moved["attribution_error_count"], 3)

    def test_pipeline_fixture_covers_midnight_health_and_attribution(self):
        report = run_observation_pipeline_benchmark(event_count=3_000, query_samples=20)

        self.assertTrue(report["passed"])
        self.assertEqual(report["correctness"]["lost_event_count"], 0)
        self.assertEqual(report["correctness"]["duplicate_event_count"], 0)
        self.assertEqual(report["correctness"]["attribution_error_count"], 0)
        self.assertTrue(report["correctness"]["cross_midnight_event_totals_match"])
        self.assertTrue(report["correctness"]["coverage_formula_matches"])
        self.assertIn("Does not measure WH_* hook delivery", report["evidence_boundary"])

    def test_command_line_benchmark_emits_machine_readable_evidence(self):
        result = subprocess.run(
            [
                sys.executable,
                "scripts/benchmark_observation_pipeline.py",
                "--events",
                "300",
                "--query-samples",
                "5",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        report = json.loads(result.stdout)
        self.assertTrue(report["passed"])
        self.assertEqual(report["scenario"]["event_count"], 300)


if __name__ == "__main__":
    unittest.main()
