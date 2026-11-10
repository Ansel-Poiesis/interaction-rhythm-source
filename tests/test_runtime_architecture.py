import os
import ctypes
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from db import DB
from interaction_rhythm.ai_config import load_ai_config, public_base_url
from interaction_rhythm.collector import CollectorAdapter
from interaction_rhythm.metrics import (
    METRIC_REGISTRY,
    METRIC_VERSION,
    personal_reference_summary,
    registry_payload,
)
from interaction_rhythm.observations import (
    ForegroundSnapshot,
    InteractionBucket,
    KeyboardPressState,
    ObservationBuffer,
)
from interaction_rhythm.service import ReplayJobManager, SessionGuard
from interaction_rhythm.win32_input_protocol import (
    benchmark_extra_info,
    benchmark_sequence,
)
from stats import StatsServer
import mimo_client
import settings
from monitor import (
    KBDLLHOOKSTRUCT,
    MSLLHOOKSTRUCT,
    KeystrokeMonitor,
    MouseMonitor,
    SystemStateMonitor,
    Tracker,
    WM_KEYDOWN,
    WM_LBUTTONDOWN,
    WindowMonitor,
    Win32CollectorAdapter,
)


class CollectorBoundaryTests(unittest.TestCase):
    def test_win32_runtime_implements_the_platform_neutral_contract(self):
        self.assertTrue(issubclass(Win32CollectorAdapter, CollectorAdapter))
        self.assertIs(Tracker, Win32CollectorAdapter)

    def test_validation_runtime_can_override_the_loopback_port(self):
        env = dict(os.environ)
        env["INTERACTION_RHYTHM_PORT"] = "19001"
        result = subprocess.run(
            [sys.executable, "-c", "import config; print(config.PORT)"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.stdout.strip(), "19001")


class ObservationBufferTests(unittest.TestCase):
    def test_events_keep_the_foreground_seen_when_the_event_happened(self):
        buffer = ObservationBuffer()
        first = datetime(2026, 7, 20, 10, 0, 0, 100_000)
        editor = ForegroundSnapshot("Code.exe", r"C:\\Code.exe")
        browser = ForegroundSnapshot("msedge.exe", r"C:\\Edge.exe")

        buffer.record_keyboard(first, editor)
        buffer.record_mouse_button(first + timedelta(milliseconds=200), browser)
        buffer.record_scroll(first + timedelta(milliseconds=300), editor)

        buckets = buffer.drain(first + timedelta(seconds=2))
        by_app = {bucket.app: bucket for bucket in buckets}
        self.assertEqual(by_app["Code.exe"].keyboard_down_count, 1)
        self.assertEqual(by_app["Code.exe"].scroll_event_count, 1)
        self.assertEqual(by_app["Code.exe"].mouse_button_count, 0)
        self.assertEqual(by_app["msedge.exe"].mouse_button_count, 1)

    def test_keyboard_counts_escape_once_and_ignores_auto_repeat(self):
        state = KeyboardPressState()

        self.assertTrue(state.on_key_down(0x1B, 1))
        self.assertFalse(state.on_key_down(0x1B, 1))
        state.on_key_up(0x1B, 1)
        self.assertTrue(state.on_key_down(0x1B, 1))

    def test_keyboard_monitor_recovers_after_lost_keyup(self):
        buffer = ObservationBuffer()
        foreground = lambda: ("Code.exe", "", r"C:\Code.exe")
        keyboard = KeystrokeMonitor(buffer, foreground)
        info = KBDLLHOOKSTRUCT(
            vkCode=0x41,
            scanCode=30,
            flags=0,
            time=0,
            dwExtraInfo=0,
        )

        keyboard._on_key(WM_KEYDOWN, ctypes.addressof(info))
        # Simulate a lost WM_KEYUP, then a health/focus boundary reconciliation.
        keyboard.reconcile()
        keyboard._on_key(WM_KEYDOWN, ctypes.addressof(info))

        buckets = buffer.drain(force=True)
        self.assertEqual(sum(bucket.keyboard_down_count for bucket in buckets), 2)

    def test_system_state_monitor_reconciles_pressed_keys_on_degraded(self):
        class FakeDB:
            def __init__(self):
                self.states = []
                self.segments = []

            def record_collector_state(self, component, state, *_args, **_kwargs):
                self.states.append((component, state))

            def add_observation_segment(self, *_args, **_kwargs):
                self.segments.append(_args)

        seen = []
        database = FakeDB()
        monitor = SystemStateMonitor(
            database,
            lambda: False,
            on_state_change=lambda state, _detail: seen.append(state),
        )
        start = datetime(2026, 7, 20, 10, 0, 0)
        monitor._initialize(start, 100.0, "running")

        monitor._tick(start + timedelta(seconds=1), 101.0, "degraded")

        self.assertIn("degraded", seen)
        self.assertEqual(database.states[-1], ("system", "degraded"))

    def test_benchmark_marker_round_trips_without_matching_ordinary_input(self):
        marker = benchmark_extra_info(42)

        self.assertEqual(benchmark_sequence(marker), 42)
        self.assertLessEqual(marker, 0xFFFFFFFF)
        self.assertIsNone(benchmark_sequence(0))
        self.assertIsNone(benchmark_sequence(42))

    def test_runtime_ignores_marked_keyboard_and_mouse_events(self):
        buffer = ObservationBuffer()
        foreground = lambda: ("Code.exe", "", r"C:\Code.exe")
        keyboard = KeystrokeMonitor(buffer, foreground)
        mouse = MouseMonitor(buffer, foreground)
        key_info = KBDLLHOOKSTRUCT(
            vkCode=0x7B,
            scanCode=0,
            flags=0,
            time=0,
            dwExtraInfo=benchmark_extra_info(1),
        )
        mouse_info = MSLLHOOKSTRUCT(
            mouseData=0,
            flags=0,
            time=0,
            dwExtraInfo=benchmark_extra_info(2),
        )

        keyboard._on_key(WM_KEYDOWN, ctypes.addressof(key_info))
        mouse._on_mouse(WM_LBUTTONDOWN, ctypes.addressof(mouse_info))

        self.assertEqual(buffer.drain(force=True), [])
        self.assertEqual(keyboard.ignored_benchmark_count, 1)
        self.assertEqual(mouse.ignored_benchmark_count, 1)


class ForegroundMonitorTests(unittest.TestCase):
    def test_subsecond_foreground_transitions_keep_distinct_boundaries(self):
        class FakeDB:
            def __init__(self):
                self.sessions = []

            def add_session(self, *args, **kwargs):
                self.sessions.append((args, kwargs))

        database = FakeDB()
        monitor = WindowMonitor(database)
        start = datetime(2026, 7, 21, 10, 0, 0, 100_000)
        end = start + timedelta(microseconds=500)

        with patch("monitor.local_now", side_effect=[start, end]):
            monitor._transition("IRBenchA.exe", r"C:\Fixture\A.exe")
            monitor._transition("IRBenchB.exe", r"C:\Fixture\B.exe")

        self.assertEqual(len(database.sessions), 1)
        args, _kwargs = database.sessions[0]
        self.assertEqual(args[2], start.isoformat(timespec="microseconds"))
        self.assertEqual(args[3], end.isoformat(timespec="microseconds"))

    def test_winevent_observation_uses_the_event_hwnd(self):
        class FakeDB:
            def add_session(self, *_args, **_kwargs):
                return None

        monitor = WindowMonitor(FakeDB())

        with patch(
            "monitor._get_window_process",
            return_value=("IRBenchA.exe", "", r"C:\Fixture\A.exe"),
        ) as event_window, patch(
            "monitor._get_foreground",
            return_value=("IRBenchB.exe", "", r"C:\Fixture\B.exe"),
        ) as current_window:
            monitor._observe(hwnd=1234)

        self.assertEqual(monitor.current_app()[0], "IRBenchA.exe")
        event_window.assert_called_once_with(1234)
        current_window.assert_not_called()


class MetricCoreTests(unittest.TestCase):
    def test_personal_reference_has_robust_statistics_and_cold_start(self):
        cold = personal_reference_summary([1, 2, 3, 4, 5, 6], current_value=8)
        ready = personal_reference_summary(list(range(1, 15)), current_value=20)

        self.assertEqual(cold["status"], "insufficient_data")
        self.assertEqual(cold["stage"], "facts_only")
        self.assertEqual(ready["stage"], "typical_range")
        self.assertEqual(ready["median"], 7.5)
        self.assertEqual(ready["p25"], 4.25)
        self.assertEqual(ready["p75"], 10.75)
        self.assertEqual(ready["position"], "above_p75")
        self.assertIsNotNone(ready["robust_deviation_score"])

    def test_registry_covers_all_contract_metric_ids(self):
        payload = registry_payload()
        ids = set(payload["metrics"])
        expected = {
            *(f"IR-EVT-{index:03d}" for index in range(1, 5)),
            "IR-FG-001",
            *(f"IR-HLT-{index:03d}" for index in range(1, 5)),
            *(f"IR-DER-{index:03d}" for index in range(1, 5)),
            *(f"IR-SES-{index:03d}" for index in range(1, 4)),
            *(f"IR-BAS-{index:03d}" for index in range(1, 4)),
        }

        self.assertEqual(ids, expected)
        self.assertEqual(len(METRIC_REGISTRY), 19)
        self.assertEqual(payload["metrics"]["IR-DER-004"]["status"], "verified")
        self.assertEqual(payload["metrics"]["IR-SES-002"]["status"], "unsupported")

    def test_registry_resolves_every_metric_id_emitted_by_daily_metrics(self):
        payload = registry_payload()
        registered = set(payload["metrics"])

        with tempfile.TemporaryDirectory() as temp_dir:
            db = DB(Path(temp_dir) / "tracker.db")
            try:
                metrics = db.day_metrics("2026-07-20")
                emitted = set()
                emitted.update(metrics["events"]["metric_ids"])
                emitted.update(metrics["coverage"]["metric_ids"])
                emitted.add(metrics["foreground_metric_id"])
            finally:
                db.close()

        self.assertTrue(emitted.issubset(registered))


class SQLiteV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "tracker.db"
        self.db = DB(self.path)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_schema_v2_is_versioned_and_dual_writes_legacy_projection(self):
        started = datetime(2026, 7, 20, 11, 0, 0)
        bucket = InteractionBucket(
            started_at=started,
            ended_at=started + timedelta(seconds=1),
            app="Code.exe",
            path=r"C:\\Code.exe",
            keyboard_down_count=2,
            mouse_button_count=3,
            scroll_event_count=4,
        )

        self.db.add_interaction_bucket(bucket)

        conn = sqlite3.connect(self.path)
        try:
            schema_version = conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            fact = conn.execute(
                "SELECT keyboard_down_count, mouse_button_count, scroll_event_count "
                "FROM interaction_buckets"
            ).fetchone()
            legacy_keyboard = conn.execute("SELECT SUM(count) FROM keystrokes").fetchone()[0]
            legacy_mouse = conn.execute("SELECT SUM(count) FROM mouse_events").fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(schema_version, "2")
        self.assertEqual(fact, (2, 3, 4))
        self.assertEqual(legacy_keyboard, 2)
        self.assertEqual(legacy_mouse, 7)

    def test_new_foreground_intervals_do_not_store_window_titles(self):
        start = datetime(2026, 7, 20, 12, 0, 0)
        self.db.add_session(
            "Code.exe",
            "secret document title",
            start.isoformat(),
            (start + timedelta(seconds=30)).isoformat(),
            r"C:\\Code.exe",
        )

        conn = sqlite3.connect(self.path)
        try:
            legacy_title = conn.execute("SELECT title FROM app_usage").fetchone()[0]
            entity_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(foreground_entities)")
            }
        finally:
            conn.close()

        self.assertEqual(legacy_title, "")
        self.assertNotIn("title", entity_columns)

    def test_historical_window_titles_can_be_deleted_locally(self):
        self.db._conn.execute(
            "INSERT INTO app_usage(app,title,path,start,end) VALUES(?,?,?,?,?)",
            (
                "legacy.exe",
                "old private title",
                "",
                "2026-07-20T12:00:00",
                "2026-07-20T12:01:00",
            ),
        )
        self.db._conn.commit()

        deleted = self.db.delete_historical_window_titles()
        title = self.db._conn.execute(
            "SELECT title FROM app_usage WHERE app='legacy.exe'"
        ).fetchone()[0]

        self.assertEqual(deleted, 1)
        self.assertEqual(title, "")

    def test_day_metrics_distinguish_input_foreground_and_coverage(self):
        start = datetime(2026, 7, 20, 13, 0, 0)
        self.db.add_interaction_bucket(InteractionBucket(
            started_at=start,
            ended_at=start + timedelta(seconds=1),
            app="Code.exe",
            path="",
            keyboard_down_count=2,
            mouse_button_count=3,
            scroll_event_count=4,
        ))
        self.db.add_foreground_interval(
            "Code.exe", "", start, start + timedelta(seconds=60)
        )
        self.db.add_observation_segment(
            "collector", "running", start, start + timedelta(seconds=60)
        )

        metrics = self.db.day_metrics("2026-07-20")
        day = self.db.day_stats("2026-07-20")

        self.assertEqual(metrics["metric_version"], METRIC_VERSION)
        self.assertEqual(metrics["events"]["keyboard_down_count"], 2)
        self.assertEqual(metrics["events"]["mouse_button_count"], 3)
        self.assertEqual(metrics["events"]["scroll_event_count"], 4)
        self.assertEqual(metrics["foreground_seconds"], 60)
        self.assertEqual(metrics["coverage"]["expected_seconds"], 60)
        self.assertEqual(metrics["coverage"]["healthy_seconds"], 60)
        self.assertEqual(metrics["coverage"]["ratio"], 1.0)
        self.assertEqual(day["foreground_seconds"], 60)
        self.assertEqual(day["total_seconds"], 60)
        self.assertEqual(day["foreground_source"], "v2_foreground_intervals")

    def test_locked_time_is_excluded_and_unknown_time_is_not_zero(self):
        start = datetime(2026, 7, 20, 14, 0, 0)
        self.db.add_observation_segment(
            "collector", "running", start, start + timedelta(seconds=30)
        )
        self.db.add_observation_segment(
            "collector", "unknown", start + timedelta(seconds=30), start + timedelta(seconds=60)
        )
        self.db.add_observation_segment(
            "collector", "locked", start + timedelta(seconds=60), start + timedelta(seconds=90)
        )

        coverage = self.db.day_metrics("2026-07-20")["coverage"]

        self.assertEqual(coverage["expected_seconds"], 60)
        self.assertEqual(coverage["healthy_seconds"], 30)
        self.assertEqual(coverage["unknown_seconds"], 30)
        self.assertEqual(coverage["ratio"], 0.5)

    def test_foreground_interval_is_clipped_at_local_midnight(self):
        start = datetime(2026, 7, 20, 23, 59, 30)
        self.db.add_foreground_interval(
            "Code.exe", "", start, start + timedelta(seconds=60)
        )

        first = self.db.day_metrics("2026-07-20")
        second = self.db.day_metrics("2026-07-21")

        self.assertEqual(first["foreground_seconds"], 30)
        self.assertEqual(second["foreground_seconds"], 30)

    def test_subsecond_foreground_interval_preserves_boundaries(self):
        start = datetime(2026, 7, 20, 12, 0, 0, 100_000)
        end = start + timedelta(microseconds=500)

        self.db.add_foreground_interval("IRBenchA.exe", "", start, end)
        row = self.db._conn.execute(
            "SELECT started_at,ended_at FROM foreground_intervals"
        ).fetchone()

        self.assertIsNotNone(row)
        stored_start = datetime.fromisoformat(row[0])
        stored_end = datetime.fromisoformat(row[1])
        self.assertEqual(stored_start.microsecond, 100_000)
        self.assertEqual(stored_end.microsecond, 100_500)
        self.assertLess(stored_start, stored_end)

    def test_foreground_interval_ending_at_midnight_does_not_leak_availability(self):
        midnight = datetime(2026, 7, 21)
        # Keep this above SQLite julianday()'s platform-dependent millisecond precision.
        self.db.add_foreground_interval(
            "Code.exe", "", midnight - timedelta(milliseconds=10), midnight
        )

        previous = self.db.day_metrics("2026-07-20")
        current = self.db.day_metrics("2026-07-21")

        self.assertTrue(previous["foreground_available"])
        self.assertFalse(current["foreground_available"])

    def test_app_analysis_handles_mixed_timezone_timestamps(self):
        # db._dt 对带时区字符串取墙钟、对 naive 字符串原样使用。测试数据必须
        # 保证两种写法的墙钟都落在本地当前时间之前，否则在非 UTC+8 的机器
        # （如 UTC 的 CI runner）上行会被 _range 的 now 窗口裁掉（2026-09-29
        # CI 实测 1 != 2）。这里用本机偏移构造 aware 行，保证任何时区确定。
        local_offset = datetime.now().astimezone().utcoffset() or timedelta(0)
        self.db._conn.execute(
            "INSERT INTO app_usage(app,title,path,start,end) VALUES(?,?,?,?,?)",
            (
                "MixedTz.exe",
                "",
                "",
                (datetime.now() - timedelta(hours=2)).isoformat(),
                (datetime.now() - timedelta(hours=2) + timedelta(seconds=60)).isoformat(),
            ),
        )
        self.db._conn.execute(
            "INSERT INTO app_usage(app,title,path,start,end) VALUES(?,?,?,?,?)",
            (
                "MixedTz.exe",
                "",
                "",
                (
                    datetime.now(timezone(local_offset)) - timedelta(hours=1)
                ).isoformat(),
                (
                    datetime.now(timezone(local_offset))
                    - timedelta(hours=1)
                    + timedelta(seconds=120)
                ).isoformat(),
            ),
        )
        self.db._conn.commit()

        result = self.db.app_analysis("MixedTz.exe", 7)

        self.assertEqual(result["session_count"], 2)
        self.assertEqual(result["total_seconds"], 180)
        # 00:00-02:00 之间运行时两行可能分属两个日期桶，只断言窗口内总量
        self.assertEqual(sum(d["seconds"] for d in result["daily"]), 180)

    def test_close_releases_connections_created_by_worker_threads(self):
        worker = threading.Thread(target=self.db.today_stats)
        worker.start()
        worker.join(timeout=2)

        self.db.close()
        self.path.unlink()

        self.assertFalse(self.path.exists())


class ServiceBoundaryTests(unittest.TestCase):
    def test_session_guard_rejects_cross_origin_and_missing_cookie(self):
        guard = SessionGuard(host="127.0.0.1", port=19023, token="fixed-token")

        self.assertFalse(guard.authorize("127.0.0.1:19023", None, ""))
        self.assertFalse(guard.authorize(
            "127.0.0.1:19023",
            "https://example.com",
            "interaction_rhythm_session=fixed-token",
        ))
        self.assertTrue(guard.authorize(
            "127.0.0.1:19023",
            "http://127.0.0.1:19023",
            "interaction_rhythm_session=fixed-token",
        ))
        self.assertIn("SameSite=Strict", guard.cookie_header())

    def test_loopback_server_uses_session_cookie_without_wildcard_cors(self):
        class FakeDB:
            def today_stats(self):
                return {"ok": True}

        server = StatsServer(FakeDB(), port=0)
        server.start()
        try:
            root = urllib.request.urlopen(
                f"http://127.0.0.1:{server.port}/", timeout=2
            )
            cookie = root.headers.get("Set-Cookie", "")
            root.close()
            today = urllib.request.urlopen(
                f"http://127.0.0.1:{server.port}/api/today", timeout=2
            )
            try:
                self.assertIsNone(today.headers.get("Access-Control-Allow-Origin"))
            finally:
                today.close()

            self.assertIn("SameSite=Strict", cookie)
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/api/replay/jobs",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(denied.exception.code, 403)

            session_cookie = cookie.split(";", 1)[0]
            authorized = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/api/replay/jobs",
                data=b'{"date":"2026-07-20"}',
                headers={
                    "Content-Type": "application/json",
                    "Cookie": session_cookie,
                    "Origin": f"http://127.0.0.1:{server.port}",
                },
                method="POST",
            )
            accepted = urllib.request.urlopen(authorized, timeout=2)
            try:
                self.assertEqual(accepted.status, 202)
            finally:
                accepted.close()
            self.assertTrue(server.threaded)
        finally:
            server.stop()

    def test_replay_jobs_do_not_block_submission(self):
        manager = ReplayJobManager(lambda date: {"date": date, "ok": True})

        submitted = manager.submit("2026-07-20")
        result = manager.wait(submitted["job_id"], timeout=1)

        self.assertEqual(submitted["status"], "queued")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["date"], "2026-07-20")

    def test_request_threads_release_their_sqlite_connections(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DB(Path(temp_dir) / "server.db")
            server = StatsServer(db, port=0)
            server.start()
            try:
                for _ in range(8):
                    response = urllib.request.urlopen(
                        f"http://127.0.0.1:{server.port}/api/today", timeout=2
                    )
                    response.close()
            finally:
                server.stop()

            self.assertEqual(len(db._connections), 1)
            db.close()


class AIConfigurationTests(unittest.TestCase):
    def test_default_provider_is_cloud_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            config = load_ai_config()

        self.assertEqual(config.provider, "deepseek")
        self.assertFalse(config.cloud_enabled)
        self.assertEqual(config.api_key, "")

    def test_provider_and_key_enable_cloud(self):
        env = {
            "INTERACTION_RHYTHM_AI_PROVIDER": "deepseek",
            "INTERACTION_RHYTHM_AI_API_KEY": "secret",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_ai_config()

        self.assertEqual(config.provider, "deepseek")
        self.assertTrue(config.cloud_enabled)
        self.assertEqual(config.api_key, "secret")

    def test_legacy_local_selection_normalizes_to_default_cloud(self):
        with patch.dict(
            os.environ,
            {"INTERACTION_RHYTHM_AI_PROVIDER": "ollama"},
            clear=True,
        ):
            config = load_ai_config()

        self.assertEqual(config.provider, "deepseek")
        self.assertFalse(config.cloud_enabled)

    def test_missing_key_never_calls_cloud(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            mimo_client, "_call_openai_compatible"
        ) as cloud:
            with self.assertRaisesRegex(RuntimeError, "API Key"):
                mimo_client._call_ai([{"role": "user", "content": "fixture"}])

        cloud.assert_not_called()

    def test_timeline_density_uses_healthy_seconds_as_denominator(self):
        class FakeDB:
            def hour_detail(self, _date, hour):
                return {
                    "responses": 60 if hour == 0 else 0,
                    "coverage": {"healthy_seconds": 60 if hour == 0 else 0},
                    "apps": [],
                }

        first = mimo_client._build_timeline_segments(FakeDB(), "2026-07-20")[0]

        self.assertEqual(first["density"], "密集")


class AIOutputContractTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            "date": "2026-07-20",
            "metric_version": "0.2.0",
            "keyboard": 12,
            "mouse": 7,
            "mouse_buttons": 5,
            "scroll_events": 2,
            "mouse_split_available": True,
            "foreground_minutes": 18,
            "top_hours": [{"range": "10:00-11:00", "responses": 12}],
            "coverage": {"ratio": 0.9, "healthy_seconds": 540, "expected_seconds": 600},
            "facts": [
                {"fact_id": "2026-07-20:keyboard", "metric_id": "IR-EVT-001", "metric_version": "0.2.0", "value": 12},
                {"fact_id": "2026-07-20:mouse-buttons", "metric_id": "IR-EVT-002", "metric_version": "0.2.0", "value": 5},
                {"fact_id": "2026-07-20:scroll", "metric_id": "IR-EVT-003", "metric_version": "0.2.0", "value": 2},
                {"fact_id": "2026-07-20:foreground", "metric_id": "IR-FG-001", "metric_version": "0.2.0", "value": 1080},
                {"fact_id": "2026-07-20:hourly-distribution", "metric_id": "IR-DER-004", "metric_version": "0.2.0", "value": []},
                {"fact_id": "2026-07-20:coverage", "metric_id": "IR-HLT-003", "metric_version": "0.2.0", "value": {"ratio": 0.9}},
            ],
        }

    def test_structured_sentences_require_real_fact_ids_and_metrics(self):
        raw = json.dumps({"sentences": [{
            "text": "今天记录到 12 次键盘事件。",
            "fact_ids": ["2026-07-20:keyboard"],
            "metric_ids": ["IR-EVT-001"],
            "claim_id": "A01",
            "metric_version": "0.2.0",
            "boundary": "只描述被捕获的键盘事件。",
        }]}, ensure_ascii=False)

        sentences, error = mimo_client._validate_structured_sentences(raw, self.context)

        self.assertEqual(error, "")
        self.assertEqual(sentences[0]["fact_ids"], ["2026-07-20:keyboard"])

    def test_medical_diagnosis_causes_factual_fallback(self):
        raw = json.dumps({"sentences": [{
            "text": "这些数据说明你已经确诊焦虑症。",
            "fact_ids": ["2026-07-20:keyboard"],
            "metric_ids": ["IR-EVT-001"],
            "claim_id": "A01",
            "metric_version": "0.2.0",
            "boundary": "只描述被捕获的键盘事件。",
        }]}, ensure_ascii=False)

        sentences, error = mimo_client._validate_structured_sentences(raw, self.context)

        self.assertIsNone(sentences)
        self.assertEqual(error, "no_usable_model_content")

    def test_hard_safety_boundaries_are_rejected(self):
        fixtures = [
            "今天打了多少字。",
            "今天超过了其他用户。",
            "你超过了 80% 的用户。",
            "系统比用户更了解你的意图。",
            "今天全天数据完整。",
            "数据完全私密，绝不上传。",
            "这些数据可以确诊抑郁症。",
        ]
        for text in fixtures:
            raw = json.dumps({"sentences": [{
                "text": text,
                "fact_ids": ["2026-07-20:keyboard"],
                "metric_ids": ["IR-EVT-001"],
                "claim_id": "A01",
                "metric_version": "0.2.0",
                "boundary": "fixture",
            }]}, ensure_ascii=False)
            sentences, error = mimo_client._validate_structured_sentences(raw, self.context)
            self.assertIsNone(sentences, text)
            self.assertEqual(error, "no_usable_model_content", text)

    def test_cautious_state_interpretations_are_allowed(self):
        raw = json.dumps({"findings": [{
            "text": "连续输入较多，可能是一个专注时段，也值得回想当时是否疲劳。",
            "fact_ids": ["2026-07-20:keyboard"],
            "confidence": "low",
        }]}, ensure_ascii=False)

        sentences, error = mimo_client._validate_structured_sentences(raw, self.context)

        self.assertEqual(error, "")
        self.assertEqual(sentences[0]["confidence"], "low")

    def test_one_invalid_item_does_not_discard_valid_findings(self):
        raw = json.dumps({"findings": [
            {"text": "引用了不存在的事实。", "fact_ids": ["missing"]},
            {"text": "输入集中在上午，可能形成了较连续的节律。", "fact_ids": ["2026-07-20:hourly-distribution"]},
        ]}, ensure_ascii=False)

        sentences, error = mimo_client._validate_structured_sentences(raw, self.context)

        self.assertEqual(error, "")
        self.assertEqual(len(sentences), 2)
        self.assertEqual(sentences[0]["fact_ids"], [])
        self.assertIn("上午", sentences[1]["text"])
        self.assertEqual(sentences[1]["fact_ids"], ["2026-07-20:hourly-distribution"])

    def test_benign_chao_guo_sentences_are_not_rejected(self):
        fixtures = (
            "覆盖超过 80% 的时段已计入。",
            "这个数字超过 7 天没有更新。",
            "超过一周后才进入观察窗口。",
        )
        for text in fixtures:
            raw = json.dumps({"sentences": [{
                "text": text,
                "fact_ids": ["2026-07-20:keyboard"],
                "metric_ids": ["IR-EVT-001"],
                "claim_id": "A01",
                "metric_version": "0.2.0",
                "boundary": "fixture",
            }]}, ensure_ascii=False)
            sentences, error = mimo_client._validate_structured_sentences(raw, self.context)
            self.assertEqual(error, "", text)
            self.assertEqual(sentences[0]["text"], text)

    def test_allowed_claim_ids_match_the_claim_matrix(self):
        expected = {
            *(f"A{index:02d}" for index in range(1, 10)),
            *(f"Q{index:02d}" for index in range(1, 9)),
            *(f"U{index:02d}" for index in range(1, 4)),
        }

        self.assertEqual(mimo_client.ALLOWED_CLAIM_IDS, expected)
        self.assertEqual(mimo_client.CLAIM_METRIC_REQUIREMENTS["A09"], {"IR-HLT-003"})
        self.assertEqual(
            mimo_client.CLAIM_METRIC_REQUIREMENTS["Q08"],
            {"IR-HLT-003", "IR-BAS-001"},
        )

    def test_ai_preview_exposes_fields_without_the_api_key(self):
        config = SimpleNamespace(
            provider="deepseek",
            base_url="https://user:secret@example.test/v1?token=secret#fragment",
            model="test-model",
            cloud_enabled=True,
            api_key="secret-key",
        )
        with patch.object(mimo_client, "_compact_day_context", return_value=self.context), patch.object(
            mimo_client, "load_ai_config", return_value=config
        ):
            preview = mimo_client.ai_context_preview(None, "2026-07-20")

        serialized = json.dumps(preview, ensure_ascii=False)
        self.assertNotIn("secret-key", serialized)
        self.assertNotIn("token=secret", serialized)
        self.assertEqual(preview["base_url"], "https://example.test/v1")
        self.assertIn("window_titles", preview["excluded_fields"])
        self.assertIn("facts", preview["fields"])

    def test_ai_preview_payload_matches_the_shared_context_contract(self):
        config = SimpleNamespace(
            provider="deepseek",
            base_url="https://example.test/v1",
            model="test-model",
            cloud_enabled=True,
            api_key="secret-key",
        )
        with patch.object(mimo_client, "_compact_day_context", return_value=self.context), patch.object(
            mimo_client, "load_ai_config", return_value=config
        ):
            preview = mimo_client.ai_context_preview(None, "2026-07-20")

        self.assertEqual(preview["payload"], mimo_client._ai_context_payload(self.context))
        self.assertIn("responses", preview["fields"])
        self.assertIn("keyboard_ratio_percent", preview["fields"])

    def test_production_messages_embed_only_the_shared_payload(self):
        messages = mimo_client._build_ai_messages(self.context)
        serialized = json.dumps(messages, ensure_ascii=False)

        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertIn("2026-07-20:keyboard", serialized)
        self.assertIn("JSON", messages[0]["content"])
        self.assertIn("reflection_questions", messages[0]["content"])
        self.assertIn("research_context", messages[1]["content"])
        self.assertNotIn("window_titles", serialized)
        self.assertNotIn("input_text", serialized)

    def test_cloud_request_contains_only_aggregate_payload_and_auth_header(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured["path"] = self.path
                captured["headers"] = dict(self.headers)
                captured["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"choices":[{"message":{"content":"ok"}}]}')

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(mimo_client, "load_ai_config", return_value=SimpleNamespace(timeout=5)):
                result = mimo_client._call_openai_compatible(
                    [{"role": "user", "content": json.dumps(mimo_client._ai_context_payload(self.context), ensure_ascii=False)}],
                    "openai",
                    "secret-key",
                    f"http://127.0.0.1:{server.server_port}/v1",
                    "fixture-model",
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(result, "ok")
        body = captured["body"].decode("utf-8")
        self.assertIn("fixture-model", body)
        self.assertIn("2026-07-20:keyboard", body)
        self.assertNotIn("secret-key", body)
        self.assertNotIn("window_titles", body)
        self.assertNotIn("input_text", body)
        self.assertNotIn("mouse_coordinates", body)
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer secret-key")
        self.assertNotIn("api-key", {key.lower() for key in captured["headers"]})

    def test_detailed_cloud_response_exposes_numeric_usage_without_changing_text_api(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3, "ignored": "value"},
                }).encode("utf-8"))

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(mimo_client, "load_ai_config", return_value=SimpleNamespace(timeout=5)):
                result = mimo_client._call_openai_compatible_detailed(
                    [{"role": "user", "content": "fixture"}],
                    "deepseek",
                    "secret-key",
                    f"http://127.0.0.1:{server.server_port}/v1",
                    "fixture-model",
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(result["text"], "ok")
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["usage"], {"prompt_tokens": 12, "completion_tokens": 3})

    def test_public_base_url_redacts_ipv6_credentials(self):
        self.assertEqual(
            public_base_url("https://user:secret@[::1]:8443/v1/?token=hidden"),
            "https://[::1]:8443/v1",
        )

    def test_public_base_url_redacts_malformed_or_schemeless_credentials(self):
        self.assertEqual(
            public_base_url("user:secret@example.test/v1?token=hidden"),
            "example.test/v1",
        )
        self.assertNotIn("secret", public_base_url("https://user:secret@example.test:not-a-port/v1"))

    def test_ai_settings_persist_the_api_key_for_ui_saved_workflow(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            settings, "_settings_path", return_value=Path(temp_dir) / "settings.json"
        ), patch.object(settings, "_read_run_value", return_value=""), patch.object(
            settings, "_delete_run_value"
        ):
            saved = settings.save_settings({
                "ai_provider": "deepseek",
                "ai_base_url": "https://example.test/v1",
                "ai_model": "test-model",
                "ai_api_key": "persisted-secret",
            })
            serialized = (Path(temp_dir) / "settings.json").read_text(encoding="utf-8")

        self.assertEqual(saved["ai_provider"], "deepseek")
        self.assertEqual(saved["ai_api_key"], "persisted-secret")
        self.assertIn('"ai_api_key": "persisted-secret"', serialized)
        self.assertIn("ai_base_url", serialized)

    def test_deterministic_fallback_has_sentence_level_provenance(self):
        result = mimo_client._factual_replay(self.context, "fixture")

        self.assertTrue(result["fallback"])
        self.assertGreaterEqual(len(result["sentences"]), 3)
        self.assertNotIn("模型不可用", result["model_note"])
        for sentence in result["sentences"]:
            self.assertTrue(sentence["fact_ids"])
            self.assertTrue(sentence["metric_ids"])
            self.assertIn(sentence["claim_id"][0], "AQU")
            self.assertEqual(sentence["metric_version"], "0.2.0")


if __name__ == "__main__":
    unittest.main()
