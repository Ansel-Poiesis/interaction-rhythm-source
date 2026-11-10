from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from interaction_rhythm.windows_system_events import Win32SystemEventSource


FIXTURE_SEQUENCE = (
    "session_lock",
    "session_unlock",
    "suspend",
    "resume_automatic",
    "resume_user",
    "session_end",
)


def run_windows_system_event_benchmark() -> dict:
    events = []
    source = Win32SystemEventSource(lambda event: events.append(event.as_dict()))
    started = source.start()
    registration_stats = source.stats
    posted = []
    query_end_session_result = 0
    if started:
        query_end_session_result = source.query_end_session_for_test()
        for kind in FIXTURE_SEQUENCE:
            posted.append({"kind": kind, "accepted": source.post_test_event(kind)})
        deadline = time.monotonic() + 2.0
        while len(events) < len(FIXTURE_SEQUENCE) and time.monotonic() < deadline:
            time.sleep(0.01)
    delivery_stats = source.stats
    stopped = source.stop()
    final_stats = source.stats
    actual_kinds = [event["kind"] for event in events]
    passed = (
        started
        and query_end_session_result == 1
        and all(item["accepted"] for item in posted)
        and actual_kinds == list(FIXTURE_SEQUENCE)
        and registration_stats["window_created"]
        and registration_stats["window_active"]
        and registration_stats["class_registered"]
        and registration_stats["wts_registered"]
        and delivery_stats["event_count"] == len(FIXTURE_SEQUENCE)
        and delivery_stats["callback_error_count"] == 0
        and stopped
        and not final_stats["healthy"]
        and not final_stats["window_active"]
        and not final_stats["class_registered"]
        and not final_stats["wts_registered"]
        and not final_stats["thread_alive"]
    )
    return {
        "passed": passed,
        "scope": "hidden top-level Win32 window + WTS/power message transport",
        "evidence_boundary": (
            "Registers a real WTS notification window and validates message dispatch with "
            "fixture PostMessage calls sent only to that hidden test window. It does not lock "
            "the session, suspend the machine, prove OS broadcast delivery, or validate UAC, "
            "Remote Desktop, fast user switching, or real resume ordering."
        ),
        "architecture": {
            "window_kind": "hidden_top_level",
            "message_only_rejected_for_power_broadcast": True,
            "production_collector_integration": True,
            "integration_exercised_by_this_benchmark": False,
            "integration_evidence": "scripts/run_isolated_hook_benchmark.py",
        },
        "posted": posted,
        "query_end_session_result": query_end_session_result,
        "events": events,
        "registration_stats": registration_stats,
        "delivery_stats": delivery_stats,
        "final_stats": final_stats,
        "resource_released": stopped,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()
    report = run_windows_system_event_benchmark()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
