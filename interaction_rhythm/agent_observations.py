"""Local, evidence-bound observation framework for the weekly Agent surface.

The framework deliberately separates three decisions:

* metrics establish what happened;
* a purpose selects which supported observations matter;
* a reviewed theory card adds an interpretive lens without changing the fact.

It does not generate advice, open a conversation, or infer psychological states.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Iterable


AGENT_FRAMEWORK_VERSION = "0.2.0"
DEFAULT_DAYS = 7
DEFAULT_MINIMUM_COVERAGE_RATIO = 0.8
REFERENCE_POSITION_MIN_VALID_DAYS = 7


@dataclass(frozen=True)
class PurposeSpec:
    """A curated observation lens, not a free-form goal interpretation."""

    purpose_id: str
    title: str
    description: str
    supported_metric_ids: tuple[str, ...]
    allowed_observation_kinds: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "purpose_id": self.purpose_id,
            "title": self.title,
            "description": self.description,
            "supported_metric_ids": list(self.supported_metric_ids),
            "allowed_observation_kinds": list(self.allowed_observation_kinds),
        }


PURPOSES = {
    "understand_personal_rhythm": PurposeSpec(
        purpose_id="understand_personal_rhythm",
        title="理解个人数字节律",
        description="根据本地聚合记录与个人参考，形成可追溯的节律观察结论。",
        supported_metric_ids=(
            "IR-EVT-004",
            "IR-FG-001",
            "IR-HLT-003",
            "IR-BAS-001",
            "IR-BAS-002",
        ),
        allowed_observation_kinds=("coverage", "reference_position", "daily_variation", "weekend_pattern"),
    ),
    "notice_rhythm_change": PurposeSpec(
        purpose_id="notice_rhythm_change",
        title="识别近期节律变化",
        description="识别本地指标相对个人近期典型范围的可观察变化。",
        supported_metric_ids=(
            "IR-EVT-004",
            "IR-FG-001",
            "IR-HLT-003",
            "IR-BAS-001",
            "IR-BAS-002",
        ),
        allowed_observation_kinds=("coverage", "reference_position", "daily_variation", "weekend_pattern"),
    ),
}


@dataclass(frozen=True)
class TheoryCard:
    """Reviewed knowledge that can frame, but never upgrade, an observation."""

    theory_id: str
    title: str
    source_url: str
    evidence_level: str
    lens: str
    original_domain: str
    transfer_boundary: str
    observation_kinds: tuple[str, ...]
    required_metric_ids: tuple[str, ...]
    minimum_valid_days: int = 0

    def as_public_dict(self) -> dict:
        return {
            "theory_id": self.theory_id,
            "title": self.title,
            "source_url": self.source_url,
            "evidence_level": self.evidence_level,
            "lens": self.lens,
            "original_domain": self.original_domain,
            "transfer_boundary": self.transfer_boundary,
        }


def purpose_catalog() -> dict:
    return {
        "framework_version": AGENT_FRAMEWORK_VERSION,
        "purposes": [purpose.as_dict() for purpose in PURPOSES.values()],
    }


def _purpose(purpose_id: str) -> PurposeSpec:
    purpose = PURPOSES.get(str(purpose_id or "").strip())
    if not purpose:
        raise ValueError("unsupported observation purpose")
    return purpose


def _date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value)).replace(hour=0, minute=0, second=0, microsecond=0)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid observation end date") from exc


def _canonical_hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _coverage_qualified(metrics: dict, minimum_coverage_ratio: float) -> bool:
    coverage = metrics.get("coverage") or {}
    ratio = coverage.get("ratio")
    return (
        coverage.get("status") == "verified"
        and isinstance(ratio, (int, float))
        and not isinstance(ratio, bool)
        and float(ratio) >= minimum_coverage_ratio
    )


def build_weekly_evidence_pack(
    db,
    end_date: str,
    purpose_id: str = "understand_personal_rhythm",
    days: int = DEFAULT_DAYS,
    minimum_coverage_ratio: float = DEFAULT_MINIMUM_COVERAGE_RATIO,
) -> dict:
    """Create an immutable, model-independent packet from approved local metrics."""
    purpose = _purpose(purpose_id)
    end = _date(end_date)
    day_count = max(1, min(int(days), 31))
    coverage_gate = max(0.0, min(float(minimum_coverage_ratio), 1.0))
    dates = [
        (end - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(day_count - 1, -1, -1)
    ]

    daily_coverage = []
    qualified_days = []
    for date in dates:
        metrics = db.day_metrics(date)
        coverage = metrics.get("coverage") or {}
        item = {
            "date": date,
            "coverage_status": coverage.get("status", "insufficient_data"),
            "coverage_ratio": coverage.get("ratio"),
        }
        events = metrics.get("events") or {}
        item["input_events"] = int(events.get("input_event_count") or 0)
        item["foreground_seconds"] = int(metrics.get("foreground_seconds") or 0)
        daily_coverage.append(item)
        if _coverage_qualified(metrics, coverage_gate):
            qualified_days.append({"date": date, "metrics": metrics})

    facts = [{
        "fact_id": f"{dates[0]}:{dates[-1]}:coverage",
        "metric_id": "IR-HLT-003",
        "metric_version": qualified_days[-1]["metrics"].get("metric_version", "") if qualified_days else "",
        "value": {
            "qualified_days": len(qualified_days),
            "observed_days": day_count,
            "minimum_coverage_ratio": coverage_gate,
            "daily_coverage": daily_coverage,
        },
        "unit": "qualified_day_count",
    }]

    references = {}
    reference_status = {}
    anchor_date = qualified_days[-1]["date"] if qualified_days else None
    if anchor_date:
        anchor_metrics = qualified_days[-1]["metrics"]
        extractors = {
            "IR-EVT-004": lambda metrics: int((metrics.get("events") or {}).get("input_event_count") or 0),
            "IR-FG-001": lambda metrics: int(metrics.get("foreground_seconds") or 0),
        }
        for metric_id, extractor in extractors.items():
            reference = db.personal_reference(metric_id, anchor_date, coverage_gate)
            current_value = extractor(anchor_metrics)
            fact_id = f"{anchor_date}:{metric_id}:current"
            reference_id = f"{anchor_date}:{metric_id}:reference"
            facts.append({
                "fact_id": fact_id,
                "metric_id": metric_id,
                "metric_version": anchor_metrics.get("metric_version", ""),
                "value": current_value,
                "unit": "event" if metric_id == "IR-EVT-004" else "second",
            })
            facts.append({
                "fact_id": reference_id,
                "metric_id": "IR-BAS-001",
                "metric_version": anchor_metrics.get("metric_version", ""),
                "value": {
                    "source_metric_id": metric_id,
                    "status": reference.get("status"),
                    "stage": reference.get("stage"),
                    "n_valid_days": reference.get("n_valid_days"),
                    "position": reference.get("position"),
                    "p25": reference.get("p25"),
                    "p75": reference.get("p75"),
                },
                "unit": "personal_reference",
            })
            references[metric_id] = {
                "current_fact_id": fact_id,
                "reference_fact_id": reference_id,
                "reference": reference,
            }
            reference_status[metric_id] = {
                "metric_id": metric_id,
                "status": reference.get("status", "insufficient_data"),
                "stage": reference.get("stage", "facts_only"),
                "n_valid_days": int(reference.get("n_valid_days") or 0),
                "position_available": bool(reference.get("position")),
                "minimum_valid_days_for_position": REFERENCE_POSITION_MIN_VALID_DAYS,
            }

    packet = {
        "framework_version": AGENT_FRAMEWORK_VERSION,
        "purpose": purpose.as_dict(),
        "range": {"start": dates[0], "end": dates[-1], "days": day_count},
        "coverage_gate": {
            "minimum_ratio": coverage_gate,
            "metric_id": "IR-HLT-003",
            "status": "provisional",
        },
        "anchor_date": anchor_date,
        "facts": facts,
        "references": references,
        "reference_status": reference_status,
    }
    try:
        packet["week_summary"] = db.week_summary(end_date, day_count)
    except Exception:
        packet["week_summary"] = {}
    packet["evidence_hash"] = _canonical_hash(packet)
    return packet


def validate_theory_card(card: TheoryCard) -> str:
    if not card.theory_id or not card.title:
        return "theory_id_and_title_required"
    if not card.source_url or not card.source_url.startswith(("https://", "http://")):
        return "theory_source_url_required"
    if card.evidence_level not in {"R1", "R2", "R3", "R4"}:
        return "theory_evidence_level_invalid"
    if not card.lens or not card.original_domain or not card.transfer_boundary:
        return "theory_context_required"
    if not card.observation_kinds or not card.required_metric_ids:
        return "theory_applicability_required"
    if card.minimum_valid_days < 0:
        return "theory_minimum_valid_days_invalid"
    return ""


def _matching_theories(
    observation: dict,
    theory_cards: Iterable[TheoryCard],
    n_valid_days: int,
) -> list[dict]:
    observation_metrics = set(observation.get("metric_ids") or [])
    matched = []
    for card in theory_cards:
        if validate_theory_card(card):
            continue
        if observation.get("kind") not in card.observation_kinds:
            continue
        if not set(card.required_metric_ids).issubset(observation_metrics):
            continue
        if n_valid_days < card.minimum_valid_days:
            continue
        matched.append(card.as_public_dict())
    return matched


def _coverage_conclusion(packet: dict) -> dict:
    fact = packet["facts"][0]
    value = fact["value"]
    qualified_days = int(value["qualified_days"])
    observed_days = int(value["observed_days"])
    if not qualified_days:
        conclusion = (
            f"最近 {observed_days} 天的稳定记录还不够，"
            "暂时无法回看本周变化。"
        )
        result = "insufficient_data"
    elif qualified_days == observed_days:
        conclusion = f"最近 {observed_days} 天都有稳定记录，可以用来回看本周变化。"
        result = "qualified_data_available"
    else:
        conclusion = (
            f"最近 {observed_days} 天中有 {qualified_days} 天记录稳定，"
            "可以用来回看这些日期的变化。"
        )
        result = "qualified_data_available"
    return {
        "observation_id": "coverage-qualified-days",
        "kind": "coverage",
        "result": result,
        "conclusion": conclusion,
        "boundary": "覆盖只描述采集是否稳定，不代表全天完整记录、实际使用或目的达成。",
        "fact_ids": [fact["fact_id"]],
        "metric_ids": ["IR-HLT-003"],
        "claim_ids": ["A09"],
        "n_valid_days": qualified_days,
    }


def _reference_conclusion(packet: dict, metric_id: str) -> dict | None:
    reference_item = packet["references"].get(metric_id)
    if not reference_item:
        return None
    reference = reference_item["reference"]
    position = reference.get("position")
    if reference.get("stage") not in {"initial_reference", "typical_range"} or not position:
        return None
    title = "输入事件" if metric_id == "IR-EVT-004" else "前台时长"
    position_text = {
        "above_p75": "高于你近期通常的范围",
        "below_p25": "低于你近期通常的范围",
        "within_p25_p75": "落在你近期通常的范围内",
    }.get(position)
    if not position_text:
        return None
    if position == "within_p25_p75":
        result = "within_typical_range"
        prompt = "那天是否延续了你近期熟悉的节奏？"
    else:
        result = "reference_change_observed"
        prompt = "那天的使用方式是否和平时不同？"
    anchor = datetime.fromisoformat(packet["anchor_date"])
    display_date = f"{anchor.month}月{anchor.day}日"
    return {
        "observation_id": f"{metric_id.lower()}-reference-position",
        "kind": "reference_position",
        "result": result,
        "conclusion": f"{display_date}的{title}{position_text}。",
        "prompt": prompt,
        "boundary": (
            "这只比较本地聚合指标与个人参考，不能解释原因，也不表示输入内容、"
            "注意、效率、产出或价值。"
        ),
        "fact_ids": [reference_item["current_fact_id"], reference_item["reference_fact_id"]],
        "metric_ids": [metric_id, "IR-BAS-001", "IR-BAS-002", "IR-HLT-003"],
        "claim_ids": ["Q01", "Q08"],
        "n_valid_days": int(reference.get("n_valid_days") or 0),
    }




def _daily_variation_conclusion(packet: dict) -> dict | None:
    """Compare the highest and lowest input days inside the week itself."""
    fact = packet["facts"][0]
    value = fact["value"]
    gate = float(value.get("minimum_coverage_ratio") or 0.8)
    daily = []
    for item in value.get("daily_coverage") or []:
        if item.get("coverage_status") != "verified":
            continue
        ratio = item.get("coverage_ratio")
        if not isinstance(ratio, (int, float)) or float(ratio) < gate:
            continue
        input_events = int(item.get("input_events") or 0)
        if input_events <= 0:
            continue
        daily.append({"date": item["date"], "input_events": input_events})
    if len(daily) < 2:
        return None
    peak = max(daily, key=lambda item: item["input_events"])
    valley = min(daily, key=lambda item: item["input_events"])
    if peak["input_events"] < valley["input_events"] * 1.5:
        return None

    def _label(iso: str) -> str:
        dt = datetime.fromisoformat(iso)
        return f"{dt.month}月{dt.day}日"

    factor = round(peak["input_events"] / valley["input_events"], 1)
    conclusion = (
        f"{_label(peak['date'])}的输入量是本周最高，"
        f"约为最低日（{_label(valley['date'])}）的 {factor} 倍。"
    )
    return {
        "observation_id": "weekly-input-peak-valley",
        "kind": "daily_variation",
        "result": "wide_daily_variation",
        "conclusion": conclusion,
        "prompt": "回想那两天前后的安排，是什么让输入量差别这么大？",
        "boundary": "极差只描述本周内部的高低起伏，不说明原因，也不代表哪一天更有价值。",
        "fact_ids": [fact["fact_id"]],
        "metric_ids": ["IR-EVT-004"],
        "claim_ids": ["Q01"],
        "n_valid_days": len(daily),
    }


def _weekend_pattern_conclusion(packet: dict) -> dict | None:
    """Compare weekend vs weekday average input within the same week."""
    fact = packet["facts"][0]
    value = fact["value"]
    gate = float(value.get("minimum_coverage_ratio") or 0.8)
    daily = []
    for item in value.get("daily_coverage") or []:
        if item.get("coverage_status") != "verified":
            continue
        ratio = item.get("coverage_ratio")
        if not isinstance(ratio, (int, float)) or float(ratio) < gate:
            continue
        dt = datetime.fromisoformat(item["date"])
        daily.append({
            "date": item["date"],
            "input_events": int(item.get("input_events") or 0),
            "weekend": dt.weekday() >= 5,
        })
    weekdays = [item["input_events"] for item in daily if not item["weekend"]]
    weekends = [item["input_events"] for item in daily if item["weekend"]]
    if not weekdays or not weekends:
        return None
    avg_weekday = sum(weekdays) / len(weekdays)
    avg_weekend = sum(weekends) / len(weekends)
    if avg_weekday <= 0 and avg_weekend <= 0:
        return None
    denominator = max(avg_weekday, avg_weekend)
    if (abs(avg_weekday - avg_weekend) / denominator) < 0.25:
        return None
    if avg_weekday > avg_weekend:
        percent = round((avg_weekday - avg_weekend) / avg_weekday * 100)
        conclusion = f"本周工作日的平均输入量比周末高约 {percent}%。"
    else:
        percent = round((avg_weekend - avg_weekday) / avg_weekend * 100)
        conclusion = f"本周周末的平均输入量比工作日高约 {percent}%。"
    return {
        "observation_id": "weekly-weekend-weekday-gap",
        "kind": "weekend_pattern",
        "result": "weekend_weekday_gap",
        "conclusion": conclusion,
        "prompt": "回想一下周末和工作日的节奏差异。",
        "boundary": "周末与工作日的差异只描述聚合记录，不代表哪一段安排更好。",
        "fact_ids": [fact["fact_id"]],
        "metric_ids": ["IR-EVT-004"],
        "claim_ids": ["Q01"],
        "n_valid_days": len(daily),
    }


def build_observation_feed(
    db,
    end_date: str,
    purpose_id: str = "understand_personal_rhythm",
    theory_cards: Iterable[TheoryCard] = (),
) -> dict:
    """Return at most three observational conclusions, never advice or dialogue."""
    packet = build_weekly_evidence_pack(db, end_date, purpose_id)
    observations = [_coverage_conclusion(packet)]
    if packet["anchor_date"]:
        for metric_id in ("IR-EVT-004", "IR-FG-001"):
            observation = _reference_conclusion(packet, metric_id)
            if observation:
                observations.append(observation)
    for builder in (_daily_variation_conclusion, _weekend_pattern_conclusion):
        observation = builder(packet)
        if observation:
            observations.append(observation)

    purpose = packet["purpose"]
    public_observations = []
    for observation in observations:
        if observation["kind"] not in purpose["allowed_observation_kinds"]:
            continue
        observation = dict(observation)
        observation["theory_context"] = _matching_theories(
            observation,
            theory_cards,
            int(observation.get("n_valid_days") or 0),
        )
        public_observations.append(observation)

    return {
        "ok": True,
        "framework_version": AGENT_FRAMEWORK_VERSION,
        "surface": "weekly_observations",
        "purpose": purpose,
        "evidence_pack": packet,
        "observations": public_observations[:6],
        "model_used": False,
        "conversation_enabled": False,
        "advice_enabled": False,
        "notification_enabled": False,
    }


def apply_user_corrections(feed: dict, corrections: Iterable[dict]) -> dict:
    """Apply local user corrections without changing facts or boundaries."""
    updated = dict(feed or {})
    observations = [dict(item) for item in updated.get("observations", [])]
    by_id = {str(item.get("observation_id") or ""): index for index, item in enumerate(observations)}
    applied = []
    for correction in corrections or []:
        if not isinstance(correction, dict):
            continue
        observation_id = str(correction.get("observation_id") or "")
        kind = str(correction.get("kind") or "").strip().lower()
        index = by_id.get(observation_id)
        if index is None:
            continue
        item = observations[index]
        if kind in {"ignore", "retract"}:
            observations[index] = None
            applied.append({
                "observation_id": observation_id,
                "kind": kind,
                "applied": True,
            })
            continue
        if kind == "correction":
            corrected_text = str(correction.get("corrected_text") or "").strip()
            if corrected_text:
                item["original_conclusion"] = item.get("conclusion", "")
                item["conclusion"] = corrected_text
                item["corrected"] = True
                item["corrected_at"] = str(correction.get("created_at") or "")
                applied.append({
                    "observation_id": observation_id,
                    "kind": kind,
                    "applied": True,
                })
        elif kind == "rename":
            label = str(correction.get("label") or "").strip()
            if label:
                item["user_label"] = label
                item["renamed_at"] = str(correction.get("created_at") or "")
                applied.append({
                    "observation_id": observation_id,
                    "kind": kind,
                    "applied": True,
                })
    observations = [item for item in observations if item is not None]
    updated["observations"] = observations
    updated["user_corrections"] = [dict(item) for item in (corrections or [])]
    updated["correction_applied"] = applied
    return updated
