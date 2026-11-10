"""Run one production-path DeepSeek request with synthetic aggregate facts."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import mimo_client
from interaction_rhythm.ai_config import public_base_url


DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"


def synthetic_context() -> dict:
    date = "2026-07-20"
    version = "0.2.0"
    return {
        "date": date,
        "metric_version": version,
        "keyboard": 1248,
        "mouse": 436,
        "mouse_buttons": 318,
        "scroll_events": 118,
        "mouse_split_available": True,
        "responses": 1684,
        "input_type": "键盘为主",
        "kb_ratio": 74,
        "foreground_minutes": 216,
        "peak_hour": 10,
        "peak_range": "10:00-11:00",
        "coverage": {"status": "good", "ratio": 0.96, "healthy_seconds": 20736, "expected_seconds": 21600},
        "top_hours": [
            {"range": "10:00-11:00", "responses": 412},
            {"range": "15:00-16:00", "responses": 356},
        ],
        "top_apps": [
            {"app": "Editor", "foreground_seconds": 6420, "responses": 814},
            {"app": "Browser", "foreground_seconds": 4180, "responses": 392},
        ],
        "segments": [
            {"range": "09:40-11:10", "foreground_minutes": 78, "responses": 612},
            {"range": "14:30-16:05", "foreground_minutes": 82, "responses": 548},
        ],
        "comparison": {"status": "ready", "valid_days": 18, "responses_delta_percent": 12},
        "facts": [
            {"fact_id": f"{date}:keyboard", "metric_id": "IR-EVT-001", "metric_version": version, "value": 1248, "unit": "event"},
            {"fact_id": f"{date}:mouse-buttons", "metric_id": "IR-EVT-002", "metric_version": version, "value": 318, "unit": "event"},
            {"fact_id": f"{date}:scroll", "metric_id": "IR-EVT-003", "metric_version": version, "value": 118, "unit": "event"},
            {"fact_id": f"{date}:foreground", "metric_id": "IR-FG-001", "metric_version": version, "value": 12960, "unit": "second"},
            {"fact_id": f"{date}:hourly-distribution", "metric_id": "IR-DER-004", "metric_version": version, "value": [{"range": "10:00-11:00", "responses": 412}], "unit": "event_by_local_hour"},
            {"fact_id": f"{date}:coverage", "metric_id": "IR-HLT-003", "metric_version": version, "value": {"status": "good", "ratio": 0.96}, "unit": "ratio"},
        ],
    }


def main() -> int:
    api_key = os.getenv("INTERACTION_RHYTHM_AI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("INTERACTION_RHYTHM_AI_API_KEY is required")

    context = synthetic_context()
    messages = mimo_client._build_ai_messages(context)
    started = time.perf_counter()
    detailed = mimo_client._call_openai_compatible_detailed(
        messages,
        "deepseek",
        api_key,
        DEEPSEEK_BASE_URL,
        DEEPSEEK_MODEL,
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    sentences, validation_error = mimo_client._validate_structured_sentences(detailed["text"], context)
    report = {
        "provider": "deepseek",
        "base_url": public_base_url(DEEPSEEK_BASE_URL),
        "model": DEEPSEEK_MODEL,
        "http_status": detailed["http_status"],
        "latency_ms": latency_ms,
        "usage": detailed["usage"],
        "structured_contract_passed": bool(sentences),
        "validation_error": validation_error,
        "sentence_count": len(sentences or []),
        "request_field_names": list(mimo_client._ai_context_payload(context)),
        "excluded_fields": [
            "key_values",
            "input_text",
            "mouse_coordinates",
            "screenshots",
            "window_titles",
            "browser_page_content",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if sentences else 2


if __name__ == "__main__":
    raise SystemExit(main())
