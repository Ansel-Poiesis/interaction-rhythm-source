from __future__ import annotations

from collections import Counter
import ctypes
import ctypes.wintypes as wintypes
from datetime import datetime
from pathlib import Path
import shutil
import tempfile
import threading
import time

from interaction_rhythm.benchmarking import compare_distributions
from interaction_rhythm.win32_input_protocol import (
    BENCHMARK_PROTOCOL_VERSION,
    benchmark_extra_info,
    benchmark_sequence,
)
from monitor import (
    MSLLHOOKSTRUCT,
    WH_MOUSE_LL,
    WM_LBUTTONDOWN,
    WM_MBUTTONDOWN,
    WM_MOUSEHWHEEL,
    WM_MOUSEWHEEL,
    WM_RBUTTONDOWN,
    WM_XBUTTONDOWN,
    _HAS_WIN32,
    _LowLevelHook,
    _get_foreground,
)
from scripts.benchmark_win32_keyboard_hook import (
    ForegroundEventProbe,
    INPUT,
    MOUSEINPUT,
    WM_CLOSE,
    _compile_window_host,
    _configure_user32,
    _force_foreground,
    _percentile,
    _start_window_host,
)


INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120
XBUTTON1 = 1
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

MOUSE_FIXTURES = (
    "left",
    "right",
    "middle",
    "x1",
    "wheel_up",
    "wheel_down",
    "hwheel_right",
    "hwheel_left",
)


def _signed_high_word(value: int) -> int:
    high = (int(value) >> 16) & 0xFFFF
    return high - 0x10000 if high & 0x8000 else high


def _observed_detail(message: int, mouse_data: int) -> int:
    if message == WM_XBUTTONDOWN:
        return (int(mouse_data) >> 16) & 0xFFFF
    if message in {WM_MOUSEWHEEL, WM_MOUSEHWHEEL}:
        return _signed_high_word(mouse_data)
    return 0


def _fixture_spec(kind: str) -> tuple[int, int, int | None, int]:
    specs = {
        "left": (WM_LBUTTONDOWN, 0, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_LEFTDOWN),
        "right": (WM_RBUTTONDOWN, 0, MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_RIGHTDOWN),
        "middle": (WM_MBUTTONDOWN, 0, MOUSEEVENTF_MIDDLEUP, MOUSEEVENTF_MIDDLEDOWN),
        "x1": (WM_XBUTTONDOWN, XBUTTON1, MOUSEEVENTF_XUP, MOUSEEVENTF_XDOWN),
        "wheel_up": (WM_MOUSEWHEEL, WHEEL_DELTA, None, MOUSEEVENTF_WHEEL),
        "wheel_down": (WM_MOUSEWHEEL, -WHEEL_DELTA, None, MOUSEEVENTF_WHEEL),
        "hwheel_right": (WM_MOUSEHWHEEL, WHEEL_DELTA, None, MOUSEEVENTF_HWHEEL),
        "hwheel_left": (WM_MOUSEHWHEEL, -WHEEL_DELTA, None, MOUSEEVENTF_HWHEEL),
    }
    return specs[kind]


def _send_mouse_fixture(sequence: int, kind: str) -> tuple[int, tuple[int, int]]:
    message, detail, up_flag, down_flag = _fixture_spec(kind)
    tag = benchmark_extra_info(sequence)
    count = 1 if up_flag is None else 2
    inputs = (INPUT * count)()
    mouse_data = ctypes.c_uint32(detail).value
    inputs[0].type = INPUT_MOUSE
    inputs[0].mi = MOUSEINPUT(0, 0, mouse_data, down_flag, 0, tag)
    if up_flag is not None:
        inputs[1].type = INPUT_MOUSE
        inputs[1].mi = MOUSEINPUT(0, 0, mouse_data, up_flag, 0, tag)
    user32, _kernel32 = _configure_user32()
    sent = int(user32.SendInput(count, inputs, ctypes.sizeof(INPUT)))
    if sent != count:
        raise RuntimeError(f"SendInput accepted {sent}/{count} mouse inputs")
    return sent, (message, detail)


def _position_fixture_under_pointer(hwnd: int) -> None:
    user32, _kernel32 = _configure_user32()
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise RuntimeError("GetCursorPos failed while positioning mouse fixture")
    positioned = user32.SetWindowPos(
        hwnd,
        HWND_TOPMOST,
        int(point.x) - 90,
        int(point.y) - 50,
        180,
        100,
        SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )
    if not positioned:
        raise RuntimeError("SetWindowPos failed while positioning mouse fixture")


class MouseHookProbe:
    def __init__(self):
        self.records: list[dict] = []
        self._lock = threading.Lock()
        self._hook = None
        _user32, self._kernel32 = _configure_user32()

    def _on_event(self, message: int, l_param: int) -> None:
        info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        sequence = benchmark_sequence(info.dwExtraInfo)
        if sequence is None:
            return
        app, _title, path = _get_foreground()
        latency_ms = (int(self._kernel32.GetTickCount()) - int(info.time)) & 0xFFFFFFFF
        with self._lock:
            self.records.append({
                "sequence": sequence,
                "message": message,
                "detail": _observed_detail(message, info.mouseData),
                "app": app,
                "path": path,
                "latency_ms": latency_ms,
            })

    def start(self) -> float:
        started_at = time.perf_counter()
        self._hook = _LowLevelHook(
            WH_MOUSE_LL,
            {
                WM_LBUTTONDOWN,
                WM_RBUTTONDOWN,
                WM_MBUTTONDOWN,
                WM_XBUTTONDOWN,
                WM_MOUSEWHEEL,
                WM_MOUSEHWHEEL,
            },
            self._on_event,
        )
        if not self._hook.start():
            raise RuntimeError("WH_MOUSE_LL installation failed")
        return (time.perf_counter() - started_at) * 1000

    def stop(self) -> None:
        if self._hook:
            self._hook.stop()
            self._hook = None


def analyze_mouse_records(
    expected: Counter,
    records: list[dict],
    expected_apps: dict[int, str],
) -> dict:
    actual = Counter(
        (row["sequence"], row["message"], row["detail"])
        for row in records
    )
    distribution = compare_distributions(expected, actual)
    attribution_errors = [
        row for row in records
        if row["app"].lower() != expected_apps.get(row["sequence"], "").lower()
    ]
    latencies = [float(row["latency_ms"]) for row in records]
    return {
        **distribution,
        "foreground_attribution_error_count": len(attribution_errors),
        "foreground_attribution_errors": attribution_errors[:10],
        "callback_latency_p50_ms": round(_percentile(latencies, 0.50), 3),
        "callback_latency_p95_ms": round(_percentile(latencies, 0.95), 3),
        "callback_latency_max_ms": round(max(latencies, default=0.0), 3),
    }


def run_live_mouse_benchmark(
    event_count: int = 120,
    switch_dwell_ms: float = 10.0,
) -> dict:
    if not _HAS_WIN32:
        raise RuntimeError("live Win32 mouse benchmark requires Windows")
    if event_count < len(MOUSE_FIXTURES):
        raise ValueError(f"event_count must be at least {len(MOUSE_FIXTURES)}")
    if switch_dwell_ms < 0:
        raise ValueError("switch_dwell_ms cannot be negative")

    expected = Counter()
    expected_apps: dict[int, str] = {}
    expected_by_kind = Counter()
    probe = MouseHookProbe()
    foreground_probe = ForegroundEventProbe()
    user32, _kernel32 = _configure_user32()
    original_foreground = int(user32.GetForegroundWindow() or 0)
    foreground_restored = False
    restart_ms = None
    measurement_started_at = None
    measurement_ended_at = None
    sent_input_count = 0
    focus_diagnostics = []

    with tempfile.TemporaryDirectory(prefix="interaction-rhythm-mouse-hook-") as temp_dir:
        temp_root = Path(temp_dir)
        template = _compile_window_host(temp_root)
        hosts = []
        try:
            for name in ("IRBenchA", "IRBenchB"):
                hosts.append(_start_window_host(template, temp_root, name))
            if original_foreground:
                if not _force_foreground(original_foreground):
                    raise RuntimeError("could not restore foreground before measurement")
                time.sleep(0.1)
            probe.start()
            foreground_probe.start()
            for host in hosts:
                if not _force_foreground(host["hwnd"]):
                    raise RuntimeError(f"could not warm foreground {host['name']}")
                time.sleep(0.05)
            if original_foreground:
                if not _force_foreground(original_foreground):
                    raise RuntimeError("could not restore foreground after warmup")
                time.sleep(0.1)
            foreground_probe.clear()
            measurement_started_at = datetime.now().astimezone()
            first_phase = event_count // 2
            for index in range(event_count):
                if index == first_phase:
                    probe.stop()
                    restart_ms = probe.start()
                host = hosts[index % len(hosts)]
                before_position_app = _get_foreground()[0]
                probe_record_count = len(foreground_probe.records)
                _position_fixture_under_pointer(host["hwnd"])
                if not _force_foreground(host["hwnd"]):
                    raise RuntimeError(f"could not foreground {host['name']}")
                app, _title, _path = _get_foreground()
                if app.lower() != host["name"].lower():
                    raise RuntimeError(
                        f"foreground attribution mismatch before injection: {app} != {host['name']}"
                    )
                sequence = index + 1
                kind = MOUSE_FIXTURES[index % len(MOUSE_FIXTURES)]
                deadline = time.monotonic() + 0.25
                observed_target_event = False
                while time.monotonic() < deadline:
                    observed_target_event = any(
                        row["app"].lower() == host["name"].lower()
                        for row in foreground_probe.records[probe_record_count:]
                    )
                    if observed_target_event:
                        break
                    time.sleep(0.005)
                focus_diagnostics.append({
                    "sequence": sequence,
                    "fixture_kind": kind,
                    "target": host["name"],
                    "target_before_position": (
                        before_position_app.lower() == host["name"].lower()
                    ),
                    "target_after_foreground": app.lower() == host["name"].lower(),
                    "target_winevent_observed": observed_target_event,
                })
                sent, event_key = _send_mouse_fixture(sequence, kind)
                sent_input_count += sent
                expected[(sequence, *event_key)] += 1
                expected_apps[sequence] = app
                expected_by_kind[kind] += 1
                if switch_dwell_ms:
                    time.sleep(switch_dwell_ms / 1000)
        finally:
            if original_foreground:
                foreground_restored = _force_foreground(original_foreground)
                time.sleep(0.1)
            measurement_ended_at = datetime.now().astimezone()
            probe.stop()
            foreground_probe.stop()
            for host in hosts:
                user32.PostMessageW(host["hwnd"], WM_CLOSE, 0, 0)
            for host in hosts:
                process = host["process"]
                try:
                    process.wait(timeout=2)
                except Exception:
                    process.terminate()
                    process.wait(timeout=2)

    correctness = analyze_mouse_records(expected, probe.records, expected_apps)
    fixture_apps = {host["name"] for host in hosts}
    foreground_events_by_app = Counter(
        row["app"] for row in foreground_probe.records
        if row["app"] in fixture_apps
    )
    foreground_event_result = {
        "raw_event_count": len(foreground_probe.records),
        "fixture_event_count": sum(foreground_events_by_app.values()),
        "fixture_events_by_app": dict(sorted(foreground_events_by_app.items())),
        "expected_fixture_event_count": event_count,
    }
    foreground_event_result["passed"] = (
        foreground_event_result["fixture_event_count"] == event_count
    )
    passed = (
        correctness["lost_event_count"] == 0
        and correctness["duplicate_event_count"] == 0
        and correctness["attribution_error_count"] == 0
        and correctness["foreground_attribution_error_count"] == 0
        and restart_ms is not None
        and (not original_foreground or foreground_restored)
        and foreground_event_result["passed"]
    )
    return {
        "passed": passed,
        "scope": "real WH_MOUSE_LL + marked SendInput button/wheel fixture",
        "evidence_boundary": (
            "Measures marked button-down and wheel delivery, direction/type separation, "
            "event-time process attribution, and hook restart. It does not measure physical "
            "mice, touchpads, high-resolution wheels, multiple devices, UAC secure desktop, "
            "Remote Desktop, lock/sleep, cursor movement, or Raw Input."
        ),
        "environment": {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "benchmark_protocol_version": BENCHMARK_PROTOCOL_VERSION,
        },
        "scenario": {
            "event_count": event_count,
            "expected_collector_ignored_event_count": event_count,
            "sent_input_count": sent_input_count,
            "fixture_counts": dict(sorted(expected_by_kind.items())),
            "host_count": 2,
            "switch_dwell_ms": switch_dwell_ms,
            "measurement_started_at": measurement_started_at.isoformat(
                timespec="microseconds"
            ),
            "measurement_ended_at": measurement_ended_at.isoformat(
                timespec="microseconds"
            ),
            "hook_restart_ms": round(float(restart_ms or 0.0), 3),
            "original_foreground_restored": foreground_restored,
            "cursor_coordinates_persisted": False,
            "focus_diagnostic_failures": [
                item for item in focus_diagnostics
                if not item["target_winevent_observed"]
            ],
        },
        "correctness": correctness,
        "foreground_event_probe": foreground_event_result,
    }
