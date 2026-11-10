"""Run the deterministic observation pipeline benchmark and emit JSON evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from interaction_rhythm.benchmarking import run_observation_pipeline_benchmark


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=30_000)
    parser.add_argument("--query-samples", type=int, default=200)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()

    report = run_observation_pipeline_benchmark(
        event_count=args.events,
        query_samples=args.query_samples,
        database_path=args.database,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
