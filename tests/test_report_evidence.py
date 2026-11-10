from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import importlib.util
import json
import sqlite3

import pytest
from jsonschema import ValidationError

from interaction_rhythm.report_evidence import (
    ROOT, build_evidence, load_methods, period_bounds, validate_evidence, validate_letter,
)

TZ = timezone(timedelta(hours=8))


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as c:
        c.executescript("""
        CREATE TABLE interaction_buckets(started_at TEXT,process_name TEXT,keyboard_down_count INTEGER,mouse_button_count INTEGER,scroll_event_count INTEGER,collector_version TEXT,health_state TEXT,executable_path TEXT);
        CREATE TABLE observation_segments(component TEXT,state TEXT,started_at TEXT,ended_at TEXT);
        CREATE TABLE foreground_intervals(process_name TEXT,started_at TEXT,ended_at TEXT,health_state TEXT);
        """)
        for i in range(35):
            d = date(2023, 12, 4) + timedelta(days=i)
            at = datetime.combine(d, datetime.min.time(), TZ).replace(hour=9)
            c.execute("INSERT INTO observation_segments VALUES('collector','running',?,?)", (at.isoformat(), (at+timedelta(minutes=15)).isoformat()))
            current = i >= 28
            for minute in range(1, 7 if current else 4):
                c.execute("INSERT INTO interaction_buckets VALUES(?,?,?,?,?,?,?,?)", ((at+timedelta(minutes=minute)).isoformat(), "Editor.exe", 20 if current else 10, 1, 2, "fixture-1", "running", "private-do-not-export"))
            c.execute("INSERT INTO foreground_intervals VALUES(?,?,?,'running')", ("Editor.exe", at.isoformat(), (at+timedelta(minutes=15)).isoformat()))
    return path


@pytest.fixture
def packet(database):
    return build_evidence(database, "2024-01-07")


def bind_letter(packet, method="M01", fact="F01", kind="observation", text="本期记录可按个人参照回看。"):
    return {"subject": "测试信", "salutation": "你好：", "closing": "", "scope_note": "合成数据。",
            "evidence_sha256": sha256(json.dumps(packet, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest(),
            "paragraphs": [{"kind": kind, "text": text, "fact_ids": [fact], "method_ids": [method]}]}


def test_frozen_reference_and_read_only(database):
    before = sha256(database.read_bytes()).hexdigest()
    p = build_evidence(database, "2024-01-07")
    validate_evidence(p)
    assert p["reference"] == {"start": "2023-12-04", "end_exclusive": "2024-01-01", "calendar_days": 28}
    assert p["signals"]["period_counts"]["reference"]["counts"]["keyboard"] == 28 * 30
    assert p["signals"]["period_counts"]["current"]["counts"]["keyboard"] == 7 * 120
    assert sha256(database.read_bytes()).hexdigest() == before
    assert "private-do-not-export" not in json.dumps(p)


def test_duration_and_density_explain_volume(packet):
    row = packet["signals"]["volume_decomposition"][0]
    assert row["minutes_factor"] == 2
    assert row["density_factors"]["keyboard"] == 2
    assert row["count_factors"]["keyboard"] == 4
    altered = deepcopy(packet)
    altered["signals"]["volume_decomposition"][0]["count_factors"]["keyboard"] = 5
    with pytest.raises(ValueError, match="decomposition"):
        validate_evidence(altered)


def test_tool_profile_uses_stated_minute_denominator(packet):
    ref = packet["signals"]["tool_fingerprints"]["reference"]["Editor.exe"]["keyboard"]
    cur = packet["signals"]["tool_fingerprints"]["current"]["Editor.exe"]["keyboard"]
    assert (ref["median"], cur["median"]) == (10, 20)
    assert (ref["n_days"], cur["n_days"]) == (28, 7)


def test_unverified_events_preserved_but_not_in_healthy_window_profile(database):
    with sqlite3.connect(database) as c:
        c.execute("INSERT INTO interaction_buckets VALUES(?,?,?,?,?,?,?,?)", ("2024-01-02T10:01:00+08:00", "Editor.exe", 500, 0, 0, "fixture-1", "running", ""))
    p = build_evidence(database, "2024-01-07")
    row = next(v for v in p["daily"] if v["date"] == "2024-01-02")
    assert row["unverified_input_counts"]["keyboard"] == 500
    assert row["input_minutes_without_health"] == 1
    assert row["counts"]["keyboard"] == 620
    assert p["signals"]["tool_fingerprints"]["current"]["Editor.exe"]["keyboard"]["median"] == 20
    validate_evidence(p)


def test_empty_observation_is_not_a_zero_activity_day(database):
    with sqlite3.connect(database) as c:
        c.execute("DELETE FROM observation_segments WHERE substr(started_at,1,10)='2024-01-03'")
    p = build_evidence(database, "2024-01-07")
    row = next(v for v in p["daily"] if v["date"] == "2024-01-03")
    assert row["quality"] == "unobserved" and row["counts"]["keyboard"] == 120
    assert row["input_minutes_without_health"] == 6


def test_hidden_tool_names_do_not_export(database):
    p = build_evidence(database, "2024-01-07", hidden_apps=["Editor"])
    assert "Editor.exe" not in json.dumps(p)
    assert "隐藏应用" in json.dumps(p, ensure_ascii=False)
    assert p["signals"]["period_counts"]["current"]["counts"]["keyboard"] == 840


def test_utc_timestamp_is_cut_by_requested_local_date(database):
    with sqlite3.connect(database) as c:
        c.execute("INSERT INTO interaction_buckets VALUES(?,?,?,?,?,?,?,?)", ("2024-01-07T16:00:00+00:00", "Editor.exe", 500, 0, 0, "fixture-1", "running", ""))
    assert build_evidence(database, "2024-01-07")["signals"]["period_counts"]["current"]["counts"]["keyboard"] == 840


def test_naive_timestamp_rejected(database):
    with sqlite3.connect(database) as c:
        c.execute("UPDATE interaction_buckets SET started_at='2024-01-02T09:01:00' WHERE started_at='2024-01-02T09:01:00+08:00'")
    with pytest.raises(ValueError, match="UTC offset"):
        build_evidence(database, "2024-01-07")


def test_complete_calendar_periods_and_leap_month():
    assert period_bounds("monthly", "2024-02-29") == (date(2024, 2, 1), date(2024, 3, 1), date(2024, 1, 1))
    with pytest.raises(ValueError): period_bounds("weekly", "2024-01-06")
    with pytest.raises(ValueError): period_bounds("monthly", "2024-02-28")


@pytest.mark.parametrize("mutation", ["unit", "reference", "totals", "research", "private", "nonfinite"])
def test_semantic_and_schema_reject_bad_evidence(packet, mutation):
    p = deepcopy(packet)
    if mutation == "unit": p["facts"][1]["unit"] = "second"
    if mutation == "reference": p["reference"]["end_exclusive"] = "2024-01-02"
    if mutation == "totals": p["signals"]["period_counts"]["current"]["counts"]["keyboard"] += 1
    if mutation == "research": p["facts"][0]["research_ids"] = ["invented-paper"]
    if mutation == "private": p["signals"]["workflow"]["path"] = "secret"
    if mutation == "nonfinite": p["signals"]["workflow"]["bad"] = float("nan")
    with pytest.raises((ValueError, ValidationError)):
        validate_evidence(p)


def test_letter_binding_and_hypothesis_marker(packet):
    letter = bind_letter(packet)
    validate_letter(packet, letter)
    letter["paragraphs"][0]["kind"] = "hypothesis"
    with pytest.raises(ValueError, match="explicitly marked"):
        validate_letter(packet, letter)
    letter["paragraphs"][0]["text"] = "我的猜测是，操作形态发生了变化。"
    validate_letter(packet, letter)
    letter["evidence_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="binding"):
        validate_letter(packet, letter)


def test_right_censored_peak_cannot_be_written_as_completed_followup(packet):
    p = deepcopy(packet)
    for row in p["signals"]["daily_sequence"]["days"]:
        row["position"] = "middle"
    p["signals"]["daily_sequence"]["days"][-1]["position"] = "high_candidate"
    letter = bind_letter(p, method="M05", fact="F06")
    with pytest.raises(ValueError, match="right-censored"):
        validate_letter(p, letter)


def test_missing_observation_cannot_count_as_complete_peak_followup(packet):
    p = deepcopy(packet)
    for row in p["signals"]["daily_sequence"]["days"]:
        row["followup_observed_days"] = 0
    with pytest.raises(ValueError, match="right-censored"):
        validate_letter(p, bind_letter(p, method="M05", fact="F06"))


def test_method_library_sources_resolve():
    sources = json.loads((ROOT / "docs/reporting/research-index.json").read_text(encoding="utf-8"))
    ids = {v["id"] for v in sources["entries"]}
    methods = load_methods()["methods"]
    assert len({v["id"] for v in methods}) == 8
    assert all(set(v["research_ids"]) <= ids for v in methods)


def test_rendering_preserves_text_and_escapes_html(packet):
    spec = importlib.util.spec_from_file_location("letter_renderer", ROOT / "scripts/render_report_letter.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    letter = bind_letter(packet, text="观察 <script>alert(1)</script> & 数据")
    md, page = module.render(packet, letter)
    assert letter["paragraphs"][0]["text"] in md
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_state_time_windows_by_density_state(packet):
    windows = packet["signals"]["state_time"]["windows"]
    steady = windows["current"]["steady"]
    assert (steady["windows"], steady["input_minutes"]) == (7, 42)
    assert steady["counts"]["keyboard"] == 840
    assert steady["top_apps"][0] == {"app": "Editor.exe", "input_minutes": 42, "keyboard": 840}
    assert windows["reference"]["leisure"]["median_density"] == 10
    assert "fluent" not in windows["current"]


def test_state_time_counts_day_rungs_by_daily_volume(database):
    with sqlite3.connect(database) as c:
        for minute in range(7, 10):
            c.execute("INSERT INTO interaction_buckets VALUES(?,?,?,?,?,?,?,?)",
                      (f"2024-01-04T09:0{minute}:00+08:00", "Editor.exe", 3300, 1, 2, "fixture-1", "running", ""))
    p = build_evidence(database, "2024-01-07")
    rungs = p["signals"]["state_time"]["day_rungs"]["current"]
    assert rungs["diligent"]["dates"] == ["2024-01-04"]  # 120 + 9900 = 10020
    assert rungs["leisure"]["days"] == 6
    frenzy = p["signals"]["state_time"]["windows"]["current"]["frenzy"]
    assert (frenzy["windows"], frenzy["input_minutes"]) == (1, 9)
    assert frenzy["median_density"] == pytest.approx(10020 / 9)
    validate_evidence(p)


def test_state_time_needs_qualified_window(database):
    with sqlite3.connect(database) as c:
        for minute in range(1, 3):  # only 2 input minutes: below qualification floor
            c.execute("INSERT INTO interaction_buckets VALUES(?,?,?,?,?,?,?,?)",
                      (f"2024-01-06T11:0{minute}:00+08:00", "Editor.exe", 140, 0, 0, "fixture-1", "running", ""))
    p = build_evidence(database, "2024-01-07")
    assert "frenzy" not in p["signals"]["state_time"]["windows"]["current"]
    assert "intense" not in p["signals"]["state_time"]["windows"]["current"]


def test_letter_can_reference_state_time_fact(packet):
    letter = bind_letter(packet, method="M03", fact="F09", text="状态时间按密度分档归集。")
    validate_letter(packet, letter)


def test_state_time_exceeding_totals_rejected(packet):
    altered = deepcopy(packet)
    altered["signals"]["state_time"]["windows"]["current"]["steady"]["counts"]["keyboard"] += 1
    altered["signals"]["state_time"]["windows"]["current"]["steady"]["input_minutes"] += 0
    with pytest.raises(ValueError, match="exceeds period totals"):
        validate_evidence(altered)


def test_daily_rhythm_reports_window_and_hours(packet):
    rhythm = packet["signals"]["daily_rhythm"]["current"]
    assert (rhythm["observed_days"], rhythm["with_keyboard_days"]) == (7, 7)
    assert rhythm["avg_input_minutes"] == 6
    assert rhythm["avg_keyboard_minutes"] == 6
    assert rhythm["first_keyboard"]["median"] == 9 * 3600 + 60
    assert rhythm["last_keyboard"]["median"] == 9 * 3600 + 6 * 60
    assert rhythm["avg_keyboard_span_minutes"] == 5
    assert rhythm["longest_span_day"] == {"date": "2024-01-01", "span_minutes": 5}
    assert rhythm["hours"][9] == {"hour": 9, "keyboard_minutes": 42, "days_present": 7}
    assert sum(h["keyboard_minutes"] for h in rhythm["hours"]) == 42


def test_highlights_rank_top_hour_and_movers(packet):
    highlights = packet["signals"]["highlights"]
    assert highlights["top_hour"] == {"hour": 9, "keyboard_minutes": 42, "days_present": 7}
    assert highlights["golden_hours"] == []  # fixture clock hours never reach the 2880s health gate
    assert highlights["tool_share_top_mover"] is None  # single tool keeps a constant share
    assert highlights["dense_state_share"] == {"current": 0.0, "reference": 0.0}
    assert highlights["day_rungs"]["elite"] is None and highlights["day_rungs"]["frenzy"] is None
    assert highlights["workflow_loops"] == {"current": 0, "previous": 0}


def test_highlights_report_frenzy_days_and_share_mover(database):
    with sqlite3.connect(database) as c:
        for minute in range(7, 10):  # 120 + 3*8400 = 25320 keys: frenzy rung day
            c.execute("INSERT INTO interaction_buckets VALUES(?,?,?,?,?,?,?,?)",
                      (f"2024-01-04T09:0{minute}:00+08:00", "Editor.exe", 8400, 1, 2, "fixture-1", "running", ""))
        for minute in range(1, 4):  # reference period gains a second tool, moving Editor's share
            c.execute("INSERT INTO interaction_buckets VALUES(?,?,?,?,?,?,?,?)",
                      (f"2023-12-08T09:0{minute}:00+08:00", "Notes.exe", 100, 0, 0, "fixture-1", "running", ""))
    p = build_evidence(database, "2024-01-07")
    highlights = p["signals"]["highlights"]
    assert highlights["day_rungs"]["frenzy"]["dates"] == ["2024-01-04"]
    assert highlights["top_hour"] == {"hour": 9, "keyboard_minutes": 45, "days_present": 7}
    mover = highlights["tool_share_top_mover"]
    assert mover["app"] == "Editor.exe" and mover["current_share"] == 1.0
    assert mover["reference_share"] == pytest.approx(840 / 1140)
    assert highlights["dense_state_share"]["current"] > 0
    validate_evidence(p)


def test_letter_can_reference_rhythm_and_highlights(packet):
    validate_letter(packet, bind_letter(packet, method="M06", fact="F10", text="平均时段按观测日归集。"))
    validate_letter(packet, bind_letter(packet, method="M01", fact="F11", text="拔尖参数按名次与对比归集。"))
