"""版本化指标注册表与小型纯函数。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import statistics


METRIC_VERSION = "0.2.0"


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    name: str
    unit: str
    source: str
    formula: str
    denominator: str = "none"
    status: str = "verified"

    def as_dict(self) -> dict:
        data = asdict(self)
        data["metric_version"] = METRIC_VERSION
        return data


METRIC_REGISTRY = {
    item.metric_id: item
    for item in (
        MetricDefinition(
            "IR-EVT-001", "captured_keyboard_down_count", "event",
            "interaction_buckets.keyboard_down_count", "sum(keyboard_down_count)",
        ),
        MetricDefinition(
            "IR-EVT-002", "captured_mouse_button_count", "event",
            "interaction_buckets.mouse_button_count", "sum(mouse_button_count)",
        ),
        MetricDefinition(
            "IR-EVT-003", "captured_scroll_event_count", "event",
            "interaction_buckets.scroll_event_count", "sum(scroll_event_count)",
        ),
        MetricDefinition(
            "IR-EVT-004", "captured_input_event_count", "event",
            "IR-EVT-001 + IR-EVT-002 + IR-EVT-003", "sum(input event components)",
        ),
        MetricDefinition(
            "IR-FG-001", "foreground_interval_seconds", "second",
            "foreground_intervals", "sum(clipped interval seconds)",
        ),
        MetricDefinition(
            "IR-HLT-001", "expected_observation_seconds", "second",
            "observation_segments", "sum(running or degraded responsibility intervals)",
        ),
        MetricDefinition(
            "IR-HLT-002", "healthy_observation_seconds", "second",
            "observation_segments", "sum(running intervals)",
        ),
        MetricDefinition(
            "IR-HLT-003", "observation_coverage_ratio", "ratio",
            "IR-HLT-001 and IR-HLT-002",
            "healthy_observation_seconds / expected_observation_seconds",
            "expected_observation_seconds",
        ),
        MetricDefinition(
            "IR-HLT-004", "unknown_gap_seconds", "second",
            "observation_segments", "sum(unknown or degraded intervals)",
        ),
        MetricDefinition(
            "IR-DER-001", "input_present_minute_count", "minute",
            "interaction_buckets and observation_segments",
            "distinct local minutes with at least one IR-EVT-001/002/003 event",
            "healthy or degraded observation minutes",
            "unsupported",
        ),
        MetricDefinition(
            "IR-DER-002", "covered_input_event_rate", "event_per_minute",
            "IR-EVT-004 and IR-HLT-002",
            "captured_input_event_count / healthy_observation_minutes",
            "healthy_observation_minutes",
            "unsupported",
        ),
        MetricDefinition(
            "IR-DER-003", "foreground_input_event_rate", "event_per_minute",
            "attributed interaction buckets and foreground_intervals",
            "attributed_input_event_count / covered_foreground_minutes",
            "covered_foreground_minutes",
            "unsupported",
        ),
        MetricDefinition(
            "IR-DER-004", "hourly_event_distribution", "event_by_local_hour",
            "interaction_buckets",
            "keyboard, click, scroll, and total events grouped by local hour",
            "healthy or degraded observation minutes per hour",
            "verified",
        ),
        MetricDefinition(
            "IR-SES-001", "process_foreground_session", "second",
            "foreground_intervals",
            "continuous same-process foreground interval without unknown/lock/sleep",
            "foreground interval",
            "unsupported",
        ),
        MetricDefinition(
            "IR-SES-002", "foreground_process_transition_count", "count",
            "foreground_intervals",
            "count of foreground process changes where A != B",
            "coverage-qualified interval",
            "unsupported",
        ),
        MetricDefinition(
            "IR-SES-003", "process_return_interval_seconds", "second",
            "foreground_intervals",
            "time from leaving process A until the next return to A",
            "return pair with no unknown/lock/sleep span",
            "unsupported",
        ),
        MetricDefinition(
            "IR-BAS-001", "personal_28d_reference", "distribution",
            "coverage-qualified daily metrics",
            "median, P25, P75, IQR, MAD over previous 28 local dates",
            "coverage-qualified valid days",
            "provisional",
        ),
        MetricDefinition(
            "IR-BAS-002", "personal_reference_position", "category",
            "IR-BAS-001", "below P25, within P25-P75, or above P75",
            "IR-BAS-001",
            "provisional",
        ),
        MetricDefinition(
            "IR-BAS-003", "robust_deviation_score", "score",
            "IR-BAS-001", "0.6745 * (x - median) / MAD",
            "MAD and valid-day gate",
            "provisional",
        ),
    )
}


def coverage_result(expected_seconds: float, healthy_seconds: float, unknown_seconds: float) -> dict:
    expected = max(0, round(expected_seconds))
    healthy = max(0, min(expected, round(healthy_seconds)))
    unknown = max(0, round(unknown_seconds))
    if expected <= 0:
        return {
            "status": "insufficient_data",
            "expected_seconds": 0,
            "healthy_seconds": 0,
            "unknown_seconds": unknown,
            "ratio": None,
        }
    return {
        "status": "verified",
        "expected_seconds": expected,
        "healthy_seconds": healthy,
        "unknown_seconds": unknown,
        "ratio": round(healthy / expected, 4),
    }

def registry_payload() -> dict:
    return {
        "metric_version": METRIC_VERSION,
        "metrics": {key: value.as_dict() for key, value in METRIC_REGISTRY.items()},
    }


def _percentile(values: list[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def personal_reference_summary(values: list[float], current_value: float | None = None) -> dict:
    clean = [float(value) for value in values if value is not None]
    count = len(clean)
    if count < 7:
        return {
            "metric_id": "IR-BAS-001",
            "metric_version": METRIC_VERSION,
            "status": "insufficient_data",
            "stage": "facts_only",
            "n_valid_days": count,
            "median": None,
            "p25": None,
            "p75": None,
            "iqr": None,
            "mad": None,
            "position": None,
            "robust_deviation_score": None,
        }

    median = float(statistics.median(clean))
    p25 = float(_percentile(clean, 0.25))
    p75 = float(_percentile(clean, 0.75))
    mad = float(statistics.median(abs(value - median) for value in clean))
    position = None
    score = None
    if current_value is not None:
        current = float(current_value)
        if current < p25:
            position = "below_p25"
        elif current > p75:
            position = "above_p75"
        else:
            position = "within_p25_p75"
        if count >= 14 and mad > 0:
            score = round(0.6745 * (current - median) / mad, 4)

    return {
        "metric_id": "IR-BAS-001",
        "metric_version": METRIC_VERSION,
        "status": "provisional",
        "stage": "initial_reference" if count < 14 else "typical_range",
        "n_valid_days": count,
        "median": round(median, 4),
        "p25": round(p25, 4),
        "p75": round(p75, 4),
        "iqr": round(p75 - p25, 4),
        "mad": round(mad, 4),
        "position": position,
        "position_metric_id": "IR-BAS-002" if position else None,
        "robust_deviation_score": score,
        "deviation_metric_id": "IR-BAS-003" if score is not None else None,
    }
