"""Offline personal-report evidence. Never imported by the collector or sender."""
from __future__ import annotations

from collections import Counter, defaultdict
from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import json
import math
import sqlite3
import statistics
from zoneinfo import ZoneInfo

from interaction_rhythm.metrics import METRIC_VERSION, _percentile

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "DR-REPORT/1.0"
CHANNELS = ("keyboard", "click", "scroll")
PARAMETERS = {
    "reference_days": 28, "window_seconds": 900,
    "minimum_window_healthy_ratio": 0.8, "minimum_tool_input_minutes": 3,
    "minimum_day_healthy_ratio": 0.8,
    "minimum_hour_healthy_seconds": 2880, "continuity_gap_seconds": 1,
    "peak_quantile": 0.9, "low_quantile": 0.25, "followup_days": 3,
}
# AB-LINE/0 + AB-VOICE/1 interpretation bands; recorded in the packet for reproducibility.
STATE_BANDS = {"leisure_kb_below": 15, "steady_kb_below": 50, "fluent_kb_below": 100, "intense_kb_below": 130}
DAY_RUNG_THRESHOLDS = {"normal_min": 5000, "diligent_min": 10000, "elite_min": 15000, "frenzy_min": 25000}
GOLDEN_HOUR_THRESHOLD = 25.0
DENSE_STATES = ("fluent", "intense", "frenzy")


def _density_state(density):
    if density < STATE_BANDS["leisure_kb_below"]:
        return "leisure"
    if density < STATE_BANDS["steady_kb_below"]:
        return "steady"
    if density < STATE_BANDS["fluent_kb_below"]:
        return "fluent"
    if density < STATE_BANDS["intense_kb_below"]:
        return "intense"
    return "frenzy"


def _day_rung(keyboard):
    if keyboard < DAY_RUNG_THRESHOLDS["normal_min"]:
        return "leisure"
    if keyboard < DAY_RUNG_THRESHOLDS["diligent_min"]:
        return "normal"
    if keyboard < DAY_RUNG_THRESHOLDS["elite_min"]:
        return "diligent"
    if keyboard < DAY_RUNG_THRESHOLDS["frenzy_min"]:
        return "elite"
    return "frenzy"


def profile(values, dates=()):
    values = list(values)
    dates = sorted(set(dates))
    median = statistics.median(values) if values else None
    p25, p75 = _percentile(values, .25), _percentile(values, .75)
    n_days = len(dates)
    return {
        "n": len(values), "n_days": n_days, "dates": dates,
        "stage": "reference" if n_days >= 14 else "initial" if n_days >= 7 else "facts_only",
        "median": median, "p25": p25, "p75": p75,
        "iqr": p75 - p25 if values else None,
        "mad": statistics.median(abs(v - median) for v in values) if values else None,
    }


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def period_bounds(kind, end_date):
    end = date.fromisoformat(end_date)
    if kind == "weekly":
        if end.weekday() != 6:
            raise ValueError("weekly end_date must be a Sunday")
        start = end - timedelta(days=6)
        previous = start - timedelta(days=7)
    elif kind == "monthly":
        after = end + timedelta(days=1)
        if after.day != 1:
            raise ValueError("monthly end_date must be the last calendar day")
        start = end.replace(day=1)
        previous = (start - timedelta(days=1)).replace(day=1)
    else:
        raise ValueError("unsupported period kind")
    return start, end + timedelta(days=1), previous


def _merged(intervals):
    result = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1] = result[-1][0], max(end, result[-1][1])
        else:
            result.append((start, end))
    return result


def _quarter_seconds(intervals):
    result = defaultdict(float)
    for start, end in _merged(intervals):
        while start < end:
            quarter = int(start // 900) * 900
            boundary = min(end, quarter + 900)
            result[quarter] += boundary - start
            start = boundary
    return result


def _dt(value, tz):
    value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError("canonical timestamps must include a UTC offset")
    return value.astimezone(tz)


def build_evidence(snapshot: Path, end_date: str, kind="weekly", zone="Asia/Shanghai", hidden_apps=()):
    """Read one consistent snapshot using an SQLite read-only connection."""
    tz = ZoneInfo(zone)
    start, end, previous = period_bounds(kind, end_date)
    ref_start = start - timedelta(days=PARAMETERS["reference_days"])
    query_start = min(ref_start, previous)
    lo = datetime.combine(query_start, time.min, tzinfo=tz)
    hi = datetime.combine(end, time.min, tzinfo=tz)
    if hi > datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0):
        raise ValueError("report period must be complete")
    with sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT started_at,process_name,keyboard_down_count,mouse_button_count,"
            "scroll_event_count,collector_version FROM interaction_buckets "
            "WHERE julianday(started_at)>=julianday(?) AND julianday(started_at)<julianday(?) "
            "AND health_state='running' ORDER BY started_at", (lo.isoformat(), hi.isoformat()),
        ).fetchall()
        segments = conn.execute(
            "SELECT state,started_at,ended_at FROM observation_segments WHERE component='collector' "
            "AND julianday(started_at)<julianday(?) AND julianday(ended_at)>julianday(?)",
            (hi.isoformat(), lo.isoformat()),
        ).fetchall()
        foreground = conn.execute(
            "SELECT process_name,started_at,ended_at FROM foreground_intervals "
            "WHERE health_state='running' AND julianday(started_at)<julianday(?) "
            "AND julianday(ended_at)>julianday(?) ORDER BY julianday(started_at),julianday(ended_at)",
            (hi.isoformat(), lo.isoformat()),
        ).fetchall()

    hidden = {str(v).strip().lower().removesuffix(".exe") for v in hidden_apps}
    names = {r[1] for r in rows} | {r[0] for r in foreground}
    aliases = {name: f"[隐藏应用 {i + 1}]" for i, name in enumerate(sorted(
        name for name in names if name.lower().removesuffix(".exe") in hidden))}
    safe = lambda name: aliases.get(name, name)
    versions = sorted({r[5] for r in rows})
    if len(versions) > 1:
        raise ValueError("compare one collector version at a time")

    ranges = defaultdict(list)
    for state, begin, finish in segments:
        begin, finish = max(_dt(begin, tz).timestamp(), lo.timestamp()), min(_dt(finish, tz).timestamp(), hi.timestamp())
        ranges[state].append((begin, finish))
    healthy = _quarter_seconds(ranges["running"])
    expected = _quarter_seconds([v for s in ("running", "degraded", "unknown") for v in ranges[s]])
    unknown = _quarter_seconds([v for s in ("degraded", "unknown") for v in ranges[s]])
    healthy_ranges = _merged(ranges["running"])
    healthy_starts = [s for s, e in healthy_ranges]

    days = {}
    for n in range((end - query_start).days):
        d = (query_start + timedelta(days=n)).isoformat()
        days[d] = {"date": d, "calendar_group": "MonFri" if date.fromisoformat(d).weekday() < 5 else "SatSun",
                   "counts": dict.fromkeys(CHANNELS, 0), "input_minutes": set(), "keyboard_minutes": set(),
                   "unverified_input_counts": dict.fromkeys(CHANNELS, 0), "input_minutes_without_health": set(),
                   "healthy_seconds": 0., "expected_seconds": 0., "unknown_seconds": 0.}
    hour_health = defaultdict(float)
    for quarter, seconds in expected.items():
        local = datetime.fromtimestamp(quarter, tz)
        d = days[local.date().isoformat()]
        good = min(healthy.get(quarter, 0), seconds)
        d["healthy_seconds"] += good
        d["expected_seconds"] += seconds
        d["unknown_seconds"] += unknown.get(quarter, 0)
        hour_health[(d["date"], local.hour)] += good

    quarters = {}
    tool_minutes = defaultdict(set)
    tool_totals = defaultdict(lambda: dict.fromkeys(CHANNELS, 0))
    hour_keyboard = defaultdict(set)
    for timestamp, raw_app, keyboard, click, scroll, version in rows:
        local = _dt(timestamp, tz)
        d = local.date().isoformat()
        minute = int(local.timestamp() // 60)
        quarter = int(local.timestamp() // 900) * 900
        app = safe(raw_app)
        counts = [int(keyboard), int(click), int(scroll)]
        if any(v < 0 for v in counts):
            raise ValueError("event counts must be non-negative")
        for channel, value in zip(CHANNELS, counts):
            days[d]["counts"][channel] += value
            tool_totals[(d, app)][channel] += value
        if not sum(counts):
            continue
        interval = bisect_right(healthy_starts, local.timestamp()) - 1
        verified = interval >= 0 and local.timestamp() < healthy_ranges[interval][1]
        if not verified:
            days[d]["input_minutes_without_health"].add(minute)
            for channel, value in zip(CHANNELS, counts):
                days[d]["unverified_input_counts"][channel] += value
        days[d]["input_minutes"].add(minute)
        tool_minutes[(d, app)].add(minute)
        if keyboard:
            days[d]["keyboard_minutes"].add(minute)
            hour_keyboard[(d, local.hour)].add(minute)
        q = quarters.setdefault((quarter, app), {"counts": dict.fromkeys(CHANNELS, 0), "minutes": set(), "date": d})
        for channel, value in zip(CHANNELS, counts):
            q["counts"][channel] += value
        q["minutes"].add(minute)

    daily = []
    for d in days.values():
        daily.append(dict(d, input_minutes=len(d["input_minutes"]), keyboard_minutes=len(d["keyboard_minutes"]),
                          input_minutes_without_health=len(d["input_minutes_without_health"]),
                          quality="observed" if d["expected_seconds"] else "unobserved"))

    def between(begin, finish):
        return [d for d in daily if begin.isoformat() <= d["date"] < finish.isoformat()]

    reference_days, current_days, previous_days = between(ref_start, start), between(start, end), between(previous, start)

    def totals(ds):
        return {"calendar_days": len(ds), "observed_days": sum(d["quality"] == "observed" for d in ds),
                "counts": {channel: sum(d["counts"][channel] for d in ds) for channel in CHANNELS},
                "input_minutes": sum(d["input_minutes"] for d in ds),
                "healthy_seconds": sum(d["healthy_seconds"] for d in ds)}

    counts = {"current": totals(current_days), "reference": totals(reference_days), "previous": totals(previous_days)}
    reference_profiles = {}
    for group in ("all", "MonFri", "SatSun"):
        samples = [d for d in reference_days if d["quality"] == "observed"
                   and ratio(d["healthy_seconds"], d["expected_seconds"]) >= PARAMETERS["minimum_day_healthy_ratio"]
                   and (group == "all" or d["calendar_group"] == group)]
        reference_profiles[group] = {channel: profile([d["counts"][channel] for d in samples], [d["date"] for d in samples]) for channel in CHANNELS}

    def tool_profile(begin, finish):
        samples = defaultdict(lambda: defaultdict(list))
        sample_dates = defaultdict(set)
        for (quarter, app), q in quarters.items():
            if not (begin.isoformat() <= q["date"] < finish.isoformat()):
                continue
            if len(q["minutes"]) < PARAMETERS["minimum_tool_input_minutes"]:
                continue
            if healthy.get(quarter, 0) < 900 * PARAMETERS["minimum_window_healthy_ratio"]:
                continue
            sample_dates[app].add(q["date"])
            for channel in CHANNELS:
                samples[app][channel].append(q["counts"][channel] / len(q["minutes"]))
        return {app: {ch: profile(values, sample_dates[app]) for ch, values in values_by_channel.items()}
                for app, values_by_channel in samples.items()}

    fingerprints = {"reference": tool_profile(ref_start, start), "current": tool_profile(start, end)}

    def qualified_state_windows(begin, finish):
        grouped = defaultdict(dict)
        for (quarter, app), q in quarters.items():
            if not (begin.isoformat() <= q["date"] < finish.isoformat()):
                continue
            if len(q["minutes"]) < PARAMETERS["minimum_tool_input_minutes"]:
                continue
            if healthy.get(quarter, 0) < 900 * PARAMETERS["minimum_window_healthy_ratio"]:
                continue
            grouped[quarter][app] = q
        agg = defaultdict(lambda: {"windows": 0, "input_minutes": 0, "counts": dict.fromkeys(CHANNELS, 0),
                                   "density_samples": [], "apps": defaultdict(lambda: {"input_minutes": 0, "keyboard": 0})})
        for quarter, apps in grouped.items():
            dominant_app, dominant = max(apps.items(), key=lambda kv: len(kv[1]["minutes"]))
            density = dominant["counts"]["keyboard"] / len(dominant["minutes"])
            entry = agg[_density_state(density)]
            entry["windows"] += 1
            entry["input_minutes"] += len(set().union(*(q["minutes"] for q in apps.values())))
            entry["density_samples"].append(density)
            for channel in CHANNELS:
                entry["counts"][channel] += sum(q["counts"][channel] for q in apps.values())
            for app, q in apps.items():
                entry["apps"][app]["input_minutes"] += len(q["minutes"])
                entry["apps"][app]["keyboard"] += q["counts"]["keyboard"]
        result = {}
        for state, e in agg.items():
            top = sorted(e["apps"].items(), key=lambda item: -item[1]["input_minutes"])[:3]
            result[state] = {"windows": e["windows"], "input_minutes": e["input_minutes"], "counts": e["counts"],
                             "median_density": statistics.median(e["density_samples"]),
                             "top_apps": [{"app": app, "input_minutes": v["input_minutes"], "keyboard": v["keyboard"]}
                                          for app, v in top]}
        return result

    def rung_days(ds):
        buckets = defaultdict(list)
        for d in ds:
            if d["quality"] == "observed":
                buckets[_day_rung(d["counts"]["keyboard"])].append(d["date"])
        return {rung: {"days": len(dates), "dates": sorted(dates)} for rung, dates in sorted(buckets.items())}

    state_time = {
        "bands": dict(STATE_BANDS), "day_rung_thresholds": dict(DAY_RUNG_THRESHOLDS),
        "day_rungs": {"current": rung_days(current_days), "reference": rung_days(reference_days)},
        "windows": {"current": qualified_state_windows(start, end), "reference": qualified_state_windows(ref_start, start)},
    }

    def day_rhythm(ds):
        observed = [d for d in ds if d["quality"] == "observed"]
        rows = []
        for d in observed:
            minutes = set()
            for hour in range(24):
                minutes |= hour_keyboard.get((d["date"], hour), set())
            if not minutes:
                continue
            first, last = min(minutes), max(minutes)
            first_local = datetime.fromtimestamp(first * 60, tz)
            last_local = datetime.fromtimestamp(last * 60, tz)
            rows.append({"date": d["date"], "input_minutes": d["input_minutes"], "keyboard_minutes": len(minutes),
                         "first_second": first_local.hour * 3600 + first_local.minute * 60 + first_local.second,
                         "last_second": last_local.hour * 3600 + last_local.minute * 60 + last_local.second,
                         "span_minutes": last - first})
        hours = []
        for hour in range(24):
            per_day = [len(hour_keyboard.get((d["date"], hour), ())) for d in observed]
            hours.append({"hour": hour, "keyboard_minutes": sum(per_day),
                          "days_present": sum(v > 0 for v in per_day)})
        longest = max(rows, key=lambda r: r["span_minutes"], default=None)
        return {
            "observed_days": len(observed), "with_keyboard_days": len(rows),
            "avg_input_minutes": statistics.fmean(r["input_minutes"] for r in rows) if rows else None,
            "avg_keyboard_minutes": statistics.fmean(r["keyboard_minutes"] for r in rows) if rows else None,
            "first_keyboard": profile((r["first_second"] for r in rows), (r["date"] for r in rows)),
            "last_keyboard": profile((r["last_second"] for r in rows), (r["date"] for r in rows)),
            "avg_keyboard_span_minutes": statistics.fmean(r["span_minutes"] for r in rows) if rows else None,
            "longest_span_day": {"date": longest["date"], "span_minutes": longest["span_minutes"]} if longest else None,
            "hours": hours,
        }

    daily_rhythm = {"current": day_rhythm(current_days), "reference": day_rhythm(reference_days)}

    period_days = (end - start).days
    decompositions = []
    for app in sorted({app for d, app in tool_totals}):
        out = {"tool": app}
        for name, begin, finish in (("reference", ref_start, start), ("current", start, end)):
            selected = [(d, a) for d, a in tool_totals if a == app and begin.isoformat() <= d < finish.isoformat()]
            minutes = sum(len(tool_minutes[key]) for key in selected)
            app_counts = {ch: sum(tool_totals[key][ch] for key in selected) for ch in CHANNELS}
            out[name] = {"counts": app_counts, "input_minutes": minutes,
                         "mean_density": {ch: ratio(app_counts[ch], minutes) for ch in CHANNELS}}
        scale = period_days / PARAMETERS["reference_days"]
        out["reference_period_scale"] = scale
        out["minutes_factor"] = ratio(out["current"]["input_minutes"], out["reference"]["input_minutes"] * scale)
        out["count_factors"] = {ch: ratio(out["current"]["counts"][ch], out["reference"]["counts"][ch] * scale) for ch in CHANNELS}
        out["density_factors"] = {ch: ratio(out["current"]["mean_density"][ch], out["reference"]["mean_density"][ch])
                                  if out["current"]["mean_density"][ch] is not None and out["reference"]["mean_density"][ch] is not None else None for ch in CHANNELS}
        decompositions.append(out)

    def hourly(ds):
        result = []
        for hour in range(24):
            valid = [d for d in ds if hour_health[(d["date"], hour)] >= PARAMETERS["minimum_hour_healthy_seconds"]]
            values = [len(hour_keyboard[(d["date"], hour)]) / (hour_health[(d["date"], hour)] / 3600) for d in valid]
            result.append({"hour": hour, "keyboard_minutes_per_healthy_hour": profile(values, [d["date"] for d in valid])})
        return result

    variation = []
    monday = ref_start + timedelta(days=(-ref_start.weekday()) % 7)
    while monday + timedelta(days=7) <= end:
        ds = between(monday, monday + timedelta(days=7))
        weekday = [d for d in ds if d["calendar_group"] == "MonFri"]
        out = {"start": monday.isoformat(), "counts": totals(ds)["counts"], "healthy_seconds": totals(ds)["healthy_seconds"]}
        for name, samples in (("all", ds), ("MonFri", weekday)):
            p = profile([d["counts"]["keyboard"] for d in samples], [d["date"] for d in samples])
            out[name] = {"profile": p, "relative_iqr": ratio(p["iqr"], p["median"]),
                         "top_two_share": ratio(sum(sorted(d["counts"]["keyboard"] for d in samples)[-2:]), sum(d["counts"]["keyboard"] for d in samples))}
        variation.append(out)
        monday += timedelta(days=7)

    sequence = []
    overall_reference = [d["counts"]["keyboard"] for d in reference_days if d["quality"] == "observed"]
    peak = _percentile(overall_reference, PARAMETERS["peak_quantile"])
    low = _percentile(overall_reference, PARAMETERS["low_quantile"])
    for i, d in enumerate(current_days):
        value = d["counts"]["keyboard"]
        following = current_days[i+1:i+1+PARAMETERS["followup_days"]]
        sequence.append({"date": d["date"], "keyboard": value,
                         "position": "unobserved" if d["quality"] == "unobserved" or peak is None else "high_candidate" if value > peak else "low_candidate" if value < low else "middle",
                         "followup_available_days": len(following),
                         "followup_observed_days": sum(v["quality"] == "observed" for v in following),
                         "following_keyboard": [v["counts"]["keyboard"] for v in following]})

    tool_keyboard = Counter()
    tool_mouse = Counter()
    for (d, app), v in tool_totals.items():
        if start.isoformat() <= d < end.isoformat():
            tool_keyboard[app] += v["keyboard"]
            tool_mouse[app] += v["click"] + v["scroll"]
    origins = [app for app, n in tool_keyboard.most_common(2) if n > 0]
    hubs = [app for app, n in tool_mouse.most_common() if app not in origins and n > 0]
    hub = hubs[0] if hubs else None

    def paths(begin, finish):
        left, right = datetime.combine(begin, time.min, tzinfo=tz).timestamp(), datetime.combine(finish, time.min, tzinfo=tz).timestamp()
        seq = []
        for app, started, ended in foreground:
            s, e = max(_dt(started, tz).timestamp(), left), min(_dt(ended, tz).timestamp(), right)
            if e <= s:
                continue
            app = safe(app)
            if seq and app == seq[-1][0] and 0 <= s - seq[-1][2] <= 1:
                seq[-1] = app, seq[-1][1], max(e, seq[-1][2])
            else:
                seq.append((app, s, e))
        transitions = sum(p[0] != q[0] and 0 <= q[1] - p[2] <= 1 for p, q in zip(seq, seq[1:]))
        loops, dates, dwell = Counter(), Counter(), defaultdict(list)
        for a, b, c in zip(seq, seq[1:], seq[2:]):
            if a[0] in origins and b[0] == hub and c[0] == a[0] and 0 <= b[1] - a[2] <= 1 and 0 <= c[1] - b[2] <= 1:
                loops[a[0]] += 1
                dates[datetime.fromtimestamp(a[1], tz).date().isoformat()] += 1
                dwell[a[0]].append(b[2] - b[1])
        return {"transitions": transitions, "loops": sum(loops.values()), "loops_by_origin": dict(loops),
                "loops_by_date": dict(dates), "middle_seconds": {app: profile(v) for app, v in dwell.items()}}

    workflow = {"origins": origins, "hub": hub, "current": paths(start, end), "previous": paths(previous, start)}
    clock_reference, clock_current = hourly(reference_days), hourly(current_days)

    def tool_shares(begin, finish):
        totals = defaultdict(int)
        for (d, app), v in tool_totals.items():
            if begin.isoformat() <= d < finish.isoformat():
                totals[app] += v["keyboard"]
        grand = sum(totals.values())
        return {app: value / grand for app, value in totals.items()} if grand else {}

    def dense_share(period):
        windows = state_time["windows"][period]
        total = sum(v["input_minutes"] for v in windows.values())
        dense = sum(windows[s]["input_minutes"] for s in DENSE_STATES if s in windows)
        return ratio(dense, total)

    reference_shares, current_shares = tool_shares(ref_start, start), tool_shares(start, end)
    mover = None
    for app in sorted(set(reference_shares) | set(current_shares)):
        delta = current_shares.get(app, 0.) - reference_shares.get(app, 0.)
        if delta >= 0.05 and current_shares.get(app, 0.) >= 0.05:
            if mover is None or delta > mover["delta"]:
                mover = {"app": app, "delta": delta,
                         "reference_share": reference_shares.get(app), "current_share": current_shares.get(app)}
    if mover is not None:
        mover.pop("delta")
    current_hours = daily_rhythm["current"]["hours"]
    top_hour = max(current_hours, key=lambda h: (h["keyboard_minutes"], -h["hour"]), default=None)
    golden_hours = sorted(
        ({"hour": row["hour"], "median_kb_minutes_per_healthy_hour": row["keyboard_minutes_per_healthy_hour"]["median"]}
         for row in clock_current
         if row["keyboard_minutes_per_healthy_hour"]["median"] is not None
         and row["keyboard_minutes_per_healthy_hour"]["median"] >= GOLDEN_HOUR_THRESHOLD),
        key=lambda item: -item["median_kb_minutes_per_healthy_hour"])
    highlights = {
        "golden_hour_threshold": GOLDEN_HOUR_THRESHOLD,
        "golden_hours": golden_hours,
        "top_hour": {"hour": top_hour["hour"], "keyboard_minutes": top_hour["keyboard_minutes"],
                     "days_present": top_hour["days_present"]} if top_hour and top_hour["keyboard_minutes"] else None,
        "day_rungs": {"elite": state_time["day_rungs"]["current"].get("elite"),
                      "frenzy": state_time["day_rungs"]["current"].get("frenzy")},
        "tool_share_top_mover": mover,
        "dense_state_share": {"current": dense_share("current"), "reference": dense_share("reference")},
        "workflow_loops": {"current": workflow["current"]["loops"], "previous": workflow["previous"]["loops"]},
    }
    signals = {"period_counts": counts, "daily_reference": reference_profiles, "tool_fingerprints": fingerprints,
               "volume_decomposition": decompositions,
               "clock_profile": {"reference": clock_reference, "current": clock_current},
               "weekly_variation": variation, "daily_sequence": {"high_threshold": peak, "low_threshold": low, "days": sequence},
               "workflow": workflow, "state_time": state_time, "daily_rhythm": daily_rhythm, "highlights": highlights}
    definitions = [
        ("F01", "period_counts", "event", "calendar_period", ["M01"], ["METHODS"]),
        ("F02", "tool_fingerprints", "event_per_input_minute", "per_tool_input_present_minute_in_qualified_15m_window", ["M03"], ["METHODS"]),
        ("F03", "volume_decomposition", "factor", "same_length_equivalent_of_reference_period", ["M02"], ["METHODS"]),
        ("F04", "weekly_variation", "relative_iqr", "daily_keyboard_median", ["M04"], ["METHODS"]),
        ("F05", "clock_profile", "keyboard_minutes_per_healthy_hour", "healthy_clock_hour", ["M06"], ["METHODS"]),
        ("F06", "daily_sequence", "event", "recorded_calendar_day", ["M04", "M05"], ["METHODS"]),
        ("F07", "workflow", "process_return", "continuous_foreground_sequence", ["M07"], ["METHODS"]),
        ("F08", "record_quality", "second", "collector_responsibility_interval", ["M08"], ["METHODS"]),
        ("F09", "state_time", "input_minute", "per_app_qualified_window_by_keyboard_density_state", ["M03", "M04"], ["METHODS"]),
        ("F10", "daily_rhythm", "minute", "observed_calendar_day", ["M06"], ["METHODS"]),
        ("F11", "highlights", "rank", "period_comparison_and_absolute_ranks", ["M01", "M06"], ["METHODS"]),
    ]
    facts = [{"id": fid, "signal": sig, "unit": unit, "denominator": den, "method_ids": methods, "research_ids": research}
             for fid, sig, unit, den, methods, research in definitions]
    packet = {"contract_version": CONTRACT, "metric_version": METRIC_VERSION, "timezone": zone,
              "generated_at": datetime.now(timezone.utc).isoformat(), "privacy_profile": "local_private", "delivery": "none",
              "period": {"kind": kind, "start": start.isoformat(), "end_exclusive": end.isoformat()},
              "reference": {"start": ref_start.isoformat(), "end_exclusive": start.isoformat(), "calendar_days": 28},
              "source": {"snapshot_sha256": sha256(snapshot.read_bytes()).hexdigest(), "collector_versions": versions},
              "parameters": dict(PARAMETERS), "daily": daily, "signals": signals, "facts": facts}
    return packet


def validate_evidence(packet):
    """Schema plus semantic validation; no remote schema retrieval."""
    from jsonschema import Draft202012Validator

    schema = json.loads((ROOT / "docs/reporting/report-evidence.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(packet)
    start = date.fromisoformat(packet["period"]["start"])
    end = date.fromisoformat(packet["period"]["end_exclusive"])
    reference = packet["reference"]
    if not (date.fromisoformat(reference["start"]) < date.fromisoformat(reference["end_exclusive"]) == start < end):
        raise ValueError("reference must precede the target period")
    if (start - date.fromisoformat(reference["start"])).days != reference["calendar_days"]:
        raise ValueError("reference day count mismatch")
    required_start, required_end, _ = period_bounds(packet["period"]["kind"], (end - timedelta(days=1)).isoformat())
    if (start, end) != (required_start, required_end):
        raise ValueError("invalid natural calendar period")
    ZoneInfo(packet["timezone"])
    if datetime.fromisoformat(packet["generated_at"]).tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    ids = [v["id"] for v in packet["facts"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate fact id")
    methods = {v["id"] for v in load_methods()["methods"]}
    research = json.loads((ROOT / "docs/reporting/research-index.json").read_text(encoding="utf-8"))
    research_ids = {v["id"] for v in research["entries"]}
    for fact in packet["facts"]:
        if not set(fact["method_ids"]) <= methods:
            raise ValueError("unknown writing method")
        if not set(fact["research_ids"]) <= research_ids:
            raise ValueError("unknown research source")
        if fact["signal"] not in packet["signals"] and fact["signal"] != "record_quality":
            raise ValueError("missing fact signal")
    selected = [v for v in packet["daily"] if start.isoformat() <= v["date"] < end.isoformat()]
    actual = packet["signals"]["period_counts"]["current"]["counts"]
    for channel in CHANNELS:
        if sum(v["counts"][channel] for v in selected) != actual[channel]:
            raise ValueError("period totals do not reconcile")
    for row in packet["daily"]:
        if not 0 <= row["healthy_seconds"] <= row["expected_seconds"] + 1:
            raise ValueError("invalid observation denominator")
        if row["keyboard_minutes"] > row["input_minutes"]:
            raise ValueError("keyboard minutes must be part of input minutes")
        for channel in CHANNELS:
            if row["unverified_input_counts"][channel] > row["counts"][channel]:
                raise ValueError("unverified input count exceeds recorded count")
    for row in packet["signals"]["volume_decomposition"]:
        for ch in CHANNELS:
            factors = row["minutes_factor"], row["density_factors"][ch], row["count_factors"][ch]
            if all(v is not None for v in factors) and not math.isclose(factors[0] * factors[1], factors[2], rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("volume decomposition does not reconcile")
    state_time = packet["signals"]["state_time"]
    for name, begin, finish in (("current", start, end),
                                ("reference", date.fromisoformat(reference["start"]), start)):
        window_keyboard = sum(v["counts"]["keyboard"] for v in state_time["windows"][name].values())
        if window_keyboard > packet["signals"]["period_counts"][name]["counts"]["keyboard"]:
            raise ValueError("state window keyboard exceeds period totals")
        rungs = state_time["day_rungs"][name]
        if sum(v["days"] for v in rungs.values()) > packet["signals"]["period_counts"][name]["observed_days"]:
            raise ValueError("day rungs exceed observed days")
        for entry in rungs.values():
            for d in entry["dates"]:
                if not begin.isoformat() <= d < finish.isoformat():
                    raise ValueError("day rung date outside its period")
    forbidden = {"password", "api_key", "access_token", "executable_path", "window_title", "title", "path", "raw_events"}
    def walk(value):
        if isinstance(value, dict):
            if forbidden & value.keys():
                raise ValueError("forbidden report field")
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("report numbers must be finite")
    walk(packet)


def load_methods():
    return json.loads((ROOT / "docs/reporting/method-library.json").read_text(encoding="utf-8"))


def validate_letter(packet, letter):
    facts = {v["id"]: v for v in packet["facts"]}
    methods = {v["id"] for v in load_methods()["methods"]}
    for paragraph in letter["paragraphs"]:
        if not paragraph["fact_ids"] or not set(paragraph["fact_ids"]) <= facts.keys():
            raise ValueError("paragraph must reference known facts")
        if not paragraph["method_ids"] or not set(paragraph["method_ids"]) <= methods:
            raise ValueError("paragraph must reference known methods")
        if paragraph["kind"] not in {"observation", "interpretation", "hypothesis"}:
            raise ValueError("unknown paragraph kind")
        supported = {m for f in paragraph["fact_ids"] for m in facts[f]["method_ids"]}
        if not set(paragraph["method_ids"]) <= supported:
            raise ValueError("writing method is unsupported by paragraph facts")
        if paragraph["kind"] == "hypothesis" and not any(v in paragraph["text"] for v in ("猜测", "推测")):
            raise ValueError("hypotheses must be explicitly marked")
        if "M05" in paragraph["method_ids"]:
            seq = packet["signals"]["daily_sequence"]
            eligible = any(v["position"] == "high_candidate" and v["followup_available_days"] >= packet["parameters"]["followup_days"]
                           and v["followup_observed_days"] >= packet["parameters"]["followup_days"] for v in seq["days"])
            if not eligible:
                raise ValueError("peak follow-up is right-censored or absent")
    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    if letter["evidence_sha256"] != sha256(encoded).hexdigest():
        raise ValueError("letter evidence binding mismatch")
