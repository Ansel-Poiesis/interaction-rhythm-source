"""Measure real WH_KEYBOARD_LL delivery with marked, content-free Escape input."""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes
import ctypes.wintypes as wintypes
from datetime import datetime
from math import ceil
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from interaction_rhythm.benchmarking import compare_distributions
from interaction_rhythm.observations import KeyboardPressState
from interaction_rhythm.win32_input_protocol import (
    BENCHMARK_PROTOCOL_VERSION,
    benchmark_extra_info,
    benchmark_sequence,
)
from monitor import (
    HC_ACTION,
    KBDLLHOOKSTRUCT,
    WH_KEYBOARD_LL,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    _HAS_WIN32,
    _ForegroundEventHook,
    _LowLevelHook,
    _get_foreground,
    _get_window_process,
)


VK_ESCAPE = 0x1B
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
WM_CLOSE = 0x0010
SW_SHOW = 5


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", INPUTUNION)]


WINDOW_HOST_SOURCE = r"""
using System;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        var form = new Form
        {
            Text = args[0],
            ShowInTaskbar = false,
            KeyPreview = true,
            StartPosition = FormStartPosition.Manual,
            Location = new Point(-30000, -30000),
            Size = new Size(180, 100)
        };
        form.KeyDown += (sender, eventArgs) =>
        {
            eventArgs.Handled = true;
            eventArgs.SuppressKeyPress = true;
        };
        form.Shown += (sender, eventArgs) =>
            File.WriteAllText(args[1], form.Handle.ToInt64().ToString());
        Application.Run(form);
    }
}
"""


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _compiler_path() -> Path:
    candidates = (
        Path(os.environ.get("WINDIR", r"C:\Windows"))
        / "Microsoft.NET/Framework64/v4.0.30319/csc.exe",
        Path(os.environ.get("WINDIR", r"C:\Windows"))
        / "Microsoft.NET/Framework/v4.0.30319/csc.exe",
    )
    compiler = next((path for path in candidates if path.is_file()), None)
    if not compiler:
        raise RuntimeError(".NET Framework C# compiler is unavailable")
    return compiler


def _compile_window_host(root: Path) -> Path:
    source = root / "WindowHost.cs"
    output = root / "WindowHost.exe"
    source.write_text(WINDOW_HOST_SOURCE, encoding="utf-8")
    result = subprocess.run(
        [
            str(_compiler_path()),
            "/nologo",
            "/target:winexe",
            "/optimize+",
            f"/out:{output}",
            "/reference:System.Windows.Forms.dll",
            "/reference:System.Drawing.dll",
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError(f"window host compilation failed: {result.stdout}\n{result.stderr}")
    return output


def _wait_for_handle(path: Path, process: subprocess.Popen, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            value = path.read_text(encoding="ascii").strip()
            if value:
                return int(value)
        if process.poll() is not None:
            raise RuntimeError(f"window host exited with {process.returncode}")
        time.sleep(0.02)
    raise TimeoutError("window host did not publish its handle")


def _start_window_host(template: Path, root: Path, name: str) -> dict:
    executable = root / f"{name}.exe"
    ready = root / f"{name}.hwnd"
    shutil.copy2(template, executable)
    process = subprocess.Popen(
        [str(executable), name, str(ready)],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {
        "name": f"{name}.exe",
        "process": process,
        "hwnd": _wait_for_handle(ready, process),
    }


def _configure_user32():
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.GetTickCount.restype = wintypes.DWORD
    return user32, kernel32


def _window_thread(user32, hwnd: int) -> int:
    process_id = wintypes.DWORD()
    return int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id)))


def _force_foreground(hwnd: int, timeout: float = 1.0) -> bool:
    user32, kernel32 = _configure_user32()
    current_thread = int(kernel32.GetCurrentThreadId())
    previous = int(user32.GetForegroundWindow() or 0)
    previous_thread = _window_thread(user32, previous) if previous else 0
    target_thread = _window_thread(user32, hwnd)
    attached = []
    for thread_id in (previous_thread, target_thread):
        if thread_id and thread_id != current_thread:
            if user32.AttachThreadInput(current_thread, thread_id, True):
                attached.append(thread_id)
    try:
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        for thread_id in reversed(attached):
            user32.AttachThreadInput(current_thread, thread_id, False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if int(user32.GetForegroundWindow() or 0) == int(hwnd):
            return True
        time.sleep(0.005)
    return False


def _send_escape(sequence: int, down_count: int = 1) -> int:
    user32, _kernel32 = _configure_user32()
    tag = benchmark_extra_info(sequence)
    inputs = (INPUT * (down_count + 1))()
    for index in range(down_count):
        inputs[index].type = INPUT_KEYBOARD
        inputs[index].ki = KEYBDINPUT(VK_ESCAPE, 0, 0, 0, tag)
    inputs[down_count].type = INPUT_KEYBOARD
    inputs[down_count].ki = KEYBDINPUT(VK_ESCAPE, 0, KEYEVENTF_KEYUP, 0, tag)
    sent = int(user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT)))
    if sent != len(inputs):
        raise RuntimeError(f"SendInput accepted {sent}/{len(inputs)} events")
    return sent


class KeyboardHookProbe:
    def __init__(self):
        self.raw_records: list[dict] = []
        self.normalized_records: list[dict] = []
        self._press_state = KeyboardPressState()
        self._lock = threading.Lock()
        self._hook = None
        _user32, self._kernel32 = _configure_user32()

    def _on_event(self, message: int, l_param: int):
        info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        sequence = benchmark_sequence(info.dwExtraInfo)
        if sequence is None or int(info.vkCode) != VK_ESCAPE:
            return
        callback_tick = int(self._kernel32.GetTickCount())
        app, _title, path = _get_foreground()
        with self._lock:
            self.raw_records.append({
                "sequence": sequence,
                "message": int(message),
                "app": app,
                "path": path,
                "latency_ms": (callback_tick - int(info.time)) & 0xFFFFFFFF,
            })
            if message in {WM_KEYUP, WM_SYSKEYUP}:
                self._press_state.on_key_up(info.vkCode, info.scanCode)
            elif self._press_state.on_key_down(info.vkCode, info.scanCode):
                self.normalized_records.append({
                    "sequence": sequence,
                    "app": app,
                    "path": path,
                })

    def start(self) -> float:
        started_at = time.perf_counter()
        self._hook = _LowLevelHook(
            WH_KEYBOARD_LL,
            {WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP},
            self._on_event,
        )
        if not self._hook.start():
            raise RuntimeError("WH_KEYBOARD_LL installation failed")
        return (time.perf_counter() - started_at) * 1000

    def stop(self):
        if self._hook:
            self._hook.stop()
            self._hook = None
        self._press_state.reset()


class ForegroundEventProbe:
    def __init__(self):
        self.records: list[dict] = []
        self._lock = threading.Lock()
        self._hook = None

    def _on_change(self, hwnd):
        app, _title, path = _get_window_process(hwnd)
        with self._lock:
            self.records.append({
                "hwnd": int(hwnd or 0),
                "app": app,
                "path": path,
                "observed_at": time.perf_counter(),
            })

    def start(self):
        self._hook = _ForegroundEventHook(self._on_change)
        if not self._hook.start():
            raise RuntimeError("EVENT_SYSTEM_FOREGROUND hook installation failed")

    def stop(self):
        if self._hook:
            self._hook.stop()
            self._hook = None

    def clear(self):
        with self._lock:
            self.records.clear()


def _keyboard_total(database: Path) -> int:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=2)
    try:
        row = connection.execute("SELECT COALESCE(SUM(count),0) FROM keystrokes").fetchone()
        return int(row[0] or 0)
    finally:
        connection.close()


def _fixture_artifact_counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=2)
    try:
        return {
            table: int(connection.execute(
                f"SELECT COUNT(1) FROM {table} WHERE instr(path, ?) > 0",
                ("interaction-rhythm-hook-",),
            ).fetchone()[0])
            for table in ("app_usage", "app_paths", "app_interactions")
        }
    finally:
        connection.close()


def analyze_records(
    expected_raw: Counter,
    raw_records: list[dict],
    normalized_records: list[dict],
    expected_apps: dict[int, str],
) -> dict:
    actual_raw = Counter((row["sequence"], row["message"]) for row in raw_records)
    distribution = compare_distributions(expected_raw, actual_raw)
    attribution_errors = [
        row for row in normalized_records
        if row["app"].lower() != expected_apps.get(row["sequence"], "").lower()
    ]
    expected_normalized = len(expected_apps)
    latencies = [float(row["latency_ms"]) for row in raw_records]
    return {
        **distribution,
        "expected_normalized_press_count": expected_normalized,
        "captured_normalized_press_count": len(normalized_records),
        "foreground_attribution_error_count": len(attribution_errors),
        "foreground_attribution_errors": attribution_errors[:10],
        "callback_latency_p50_ms": round(_percentile(latencies, 0.50), 3),
        "callback_latency_p95_ms": round(_percentile(latencies, 0.95), 3),
        "callback_latency_max_ms": round(max(latencies, default=0.0), 3),
    }


def run_live_benchmark(
    press_count: int = 120,
    host_count: int = 1,
    repeat_downs: int = 5,
    recorder_database: Path | None = None,
    switch_dwell_ms: float = 0.0,
) -> dict:
    if not _HAS_WIN32 or os.name != "nt":
        raise RuntimeError("live Win32 hook benchmark requires Windows")
    if press_count < 4:
        raise ValueError("press_count must be at least 4")
    if host_count not in {1, 2}:
        raise ValueError("host_count must be 1 or 2")
    if switch_dwell_ms < 0:
        raise ValueError("switch_dwell_ms cannot be negative")
    if recorder_database and host_count > 1:
        raise RuntimeError("two-host switching is refused while a recorder database is active")

    recorder_database = Path(recorder_database) if recorder_database else None
    recorder_before = _keyboard_total(recorder_database) if recorder_database else None
    artifacts_before = (
        _fixture_artifact_counts(recorder_database) if recorder_database else None
    )
    expected_raw = Counter()
    expected_apps: dict[int, str] = {}
    probe = KeyboardHookProbe()
    foreground_probe = ForegroundEventProbe()
    original_foreground = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    restart_ms = None
    foreground_restored = False
    measurement_started_at = None
    measurement_ended_at = None

    with tempfile.TemporaryDirectory(prefix="interaction-rhythm-hook-") as temp_dir:
        temp_root = Path(temp_dir)
        template = _compile_window_host(temp_root)
        names = ["InteractionRhythm"] if host_count == 1 else ["IRBenchA", "IRBenchB"]
        hosts = []
        try:
            for name in names:
                hosts.append(_start_window_host(template, temp_root, name))
            if original_foreground:
                if not _force_foreground(original_foreground):
                    raise RuntimeError("could not restore foreground before measurement")
                time.sleep(0.1)
            foreground_probe.start()
            probe.start()
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
            sequence = 1
            first_phase = press_count // 2
            for index in range(press_count):
                if index == first_phase:
                    probe.stop()
                    restart_ms = probe.start()
                host = hosts[index % len(hosts)]
                if not _force_foreground(host["hwnd"]):
                    raise RuntimeError(f"could not foreground {host['name']}")
                app, _title, _path = _get_foreground()
                if app.lower() != host["name"].lower():
                    raise RuntimeError(
                        f"foreground attribution mismatch before injection: {app} != {host['name']}"
                    )
                expected_apps[sequence] = app
                expected_raw[(sequence, WM_KEYDOWN)] += 1
                expected_raw[(sequence, WM_KEYUP)] += 1
                _send_escape(sequence)
                if switch_dwell_ms:
                    time.sleep(switch_dwell_ms / 1000)
                sequence += 1

            host = hosts[-1]
            if not _force_foreground(host["hwnd"]):
                raise RuntimeError(f"could not foreground {host['name']} for repeat fixture")
            app, _title, _path = _get_foreground()
            expected_apps[sequence] = app
            expected_raw[(sequence, WM_KEYDOWN)] += repeat_downs
            expected_raw[(sequence, WM_KEYUP)] += 1
            _send_escape(sequence, down_count=repeat_downs)
        finally:
            if original_foreground:
                foreground_restored = _force_foreground(original_foreground)
                time.sleep(0.1)
            measurement_ended_at = datetime.now().astimezone()
            probe.stop()
            foreground_probe.stop()
            user32, _kernel32 = _configure_user32()
            for host in hosts:
                user32.PostMessageW(host["hwnd"], WM_CLOSE, 0, 0)
            for host in hosts:
                process = host["process"]
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=2)

    if recorder_database:
        time.sleep(1.25)
    recorder_after = _keyboard_total(recorder_database) if recorder_database else None
    artifacts_after = (
        _fixture_artifact_counts(recorder_database) if recorder_database else None
    )
    correctness = analyze_records(
        expected_raw,
        probe.raw_records,
        probe.normalized_records,
        expected_apps,
    )
    foreground_events_by_app = Counter(
        row["app"] for row in foreground_probe.records
        if row["app"] in {host["name"] for host in hosts}
    )
    foreground_event_result = {
        "raw_event_count": len(foreground_probe.records),
        "fixture_event_count": sum(foreground_events_by_app.values()),
        "fixture_events_by_app": dict(sorted(foreground_events_by_app.items())),
        "expected_fixture_event_count": press_count if host_count == 2 else 1,
    }
    foreground_event_result["passed"] = (
        foreground_event_result["fixture_event_count"]
        == foreground_event_result["expected_fixture_event_count"]
        if host_count == 2
        else foreground_event_result["fixture_event_count"] >= 1
    )
    recorder_delta = (
        recorder_after - recorder_before
        if recorder_before is not None and recorder_after is not None
        else None
    )
    artifact_delta = (
        {
            table: max(0, artifacts_after[table] - artifacts_before[table])
            for table in artifacts_before
        }
        if artifacts_before is not None and artifacts_after is not None
        else None
    )
    recorder_guard_passed = (
        recorder_delta in {None, 0}
        and (artifact_delta is None or sum(artifact_delta.values()) == 0)
    )
    passed = (
        correctness["lost_event_count"] == 0
        and correctness["duplicate_event_count"] == 0
        and correctness["foreground_attribution_error_count"] == 0
        and correctness["captured_normalized_press_count"]
        == correctness["expected_normalized_press_count"]
        and restart_ms is not None
        and (not original_foreground or foreground_restored)
        and foreground_event_result["passed"]
        and recorder_guard_passed
    )
    return {
        "passed": passed,
        "scope": "real WH_KEYBOARD_LL + SendInput Escape fixture",
        "evidence_boundary": (
            "Measures marked Escape delivery, event-time process attribution, auto-repeat "
            "deduplication, and hook restart. It does not measure physical keyboards, mouse "
            "buttons, scrolling, UAC secure desktop, Remote Desktop, lock/sleep, or Raw Input."
        ),
        "environment": {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "python": sys.version.split()[0],
            "benchmark_protocol_version": BENCHMARK_PROTOCOL_VERSION,
        },
        "scenario": {
            "press_count": press_count,
            "repeat_downs": repeat_downs,
            "host_count": host_count,
            "switch_dwell_ms": switch_dwell_ms,
            "measurement_started_at": measurement_started_at.isoformat(
                timespec="microseconds"
            ),
            "measurement_ended_at": measurement_ended_at.isoformat(
                timespec="microseconds"
            ),
            "expected_raw_event_count": sum(expected_raw.values()),
            "expected_collector_ignored_event_count": sum(expected_raw.values()),
            "hook_restart_ms": round(float(restart_ms or 0.0), 3),
            "original_foreground_restored": foreground_restored,
        },
        "correctness": correctness,
        "foreground_event_probe": foreground_event_result,
        "running_recorder_guard": {
            "database": str(recorder_database) if recorder_database else None,
            "keyboard_count_before": recorder_before,
            "keyboard_count_after": recorder_after,
            "keyboard_count_delta": recorder_delta,
            "fixture_artifacts_before": artifacts_before,
            "fixture_artifacts_after": artifacts_after,
            "fixture_artifact_delta": artifact_delta,
            "passed": recorder_guard_passed,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--presses", type=int, default=120)
    parser.add_argument("--repeat-downs", type=int, default=5)
    parser.add_argument("--switch-hosts", action="store_true")
    parser.add_argument("--switch-dwell-ms", type=float, default=0.0)
    parser.add_argument("--recorder-database", type=Path)
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()

    report = run_live_benchmark(
        press_count=args.presses,
        host_count=2 if args.switch_hosts else 1,
        repeat_downs=args.repeat_downs,
        recorder_database=args.recorder_database,
        switch_dwell_ms=args.switch_dwell_ms,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
