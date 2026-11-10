from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from interaction_rhythm.observations import KeyboardPressState
from interaction_rhythm.win32_input_protocol import is_benchmark_input
from interaction_rhythm.windows_raw_input import Win32RawInputSource, raw_input_device_inventory
from monitor import (
    KBDLLHOOKSTRUCT,
    MSLLHOOKSTRUCT,
    WH_KEYBOARD_LL,
    WH_MOUSE_LL,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    WM_LBUTTONDOWN,
    WM_RBUTTONDOWN,
    WM_MBUTTONDOWN,
    WM_XBUTTONDOWN,
    WM_MOUSEWHEEL,
    WM_MOUSEHWHEEL,
    _HAS_WIN32,
    _LowLevelHook,
)
from scripts.benchmark_win32_keyboard_hook import (
    WM_CLOSE,
    _compile_window_host,
    _configure_user32,
    _force_foreground,
    _send_escape,
    _start_window_host,
)
from scripts.benchmark_win32_mouse_hook import (
    MOUSE_FIXTURES,
    _position_fixture_under_pointer,
    _send_mouse_fixture,
)


MOUSE_MESSAGES = {
    WM_LBUTTONDOWN,
    WM_RBUTTONDOWN,
    WM_MBUTTONDOWN,
    WM_XBUTTONDOWN,
    WM_MOUSEWHEEL,
    WM_MOUSEHWHEEL,
}
RAW_INPUT_FIXTURE_TOKEN = "interaction-rhythm-raw-input-"


def local_recorder_active(port: int = 19023) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.25)
        return client.connect_ex(("127.0.0.1", port)) == 0


class CountOnlyHookProbe:
    """Ephemeral comparison probe; it never retains keys, coordinates, or payloads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pressed = KeyboardPressState()
        self._counts = {
            "keyboard_presses": 0,
            "keyboard_repeat_messages": 0,
            "keyboard_releases": 0,
            "mouse_button_presses": 0,
            "scroll_events": 0,
            "benchmark_messages_ignored": 0,
        }
        self._keyboard_hook = None
        self._mouse_hook = None

    def _on_keyboard(self, message: int, l_param: int) -> None:
        info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        with self._lock:
            if is_benchmark_input(info.dwExtraInfo):
                self._counts["benchmark_messages_ignored"] += 1
                return
            if message in {WM_KEYUP, WM_SYSKEYUP}:
                self._pressed.on_key_up(info.vkCode, info.scanCode)
                self._counts["keyboard_releases"] += 1
            elif self._pressed.on_key_down(info.vkCode, info.scanCode):
                self._counts["keyboard_presses"] += 1
            else:
                self._counts["keyboard_repeat_messages"] += 1

    def _on_mouse(self, message: int, l_param: int) -> None:
        info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        with self._lock:
            if is_benchmark_input(info.dwExtraInfo):
                self._counts["benchmark_messages_ignored"] += 1
                return
            if message in {WM_MOUSEWHEEL, WM_MOUSEHWHEEL}:
                self._counts["scroll_events"] += 1
            else:
                self._counts["mouse_button_presses"] += 1

    def start(self) -> bool:
        self._keyboard_hook = _LowLevelHook(
            WH_KEYBOARD_LL,
            {WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP},
            self._on_keyboard,
        )
        self._mouse_hook = _LowLevelHook(WH_MOUSE_LL, MOUSE_MESSAGES, self._on_mouse)
        return bool(self._keyboard_hook.start() and self._mouse_hook.start())

    def stop(self) -> None:
        if self._keyboard_hook:
            self._keyboard_hook.stop()
        if self._mouse_hook:
            self._mouse_hook.stop()

    @property
    def healthy(self) -> bool:
        return bool(
            self._keyboard_hook and self._keyboard_hook.healthy
            and self._mouse_hook and self._mouse_hook.healthy
        )

    @property
    def snapshot(self) -> dict:
        with self._lock:
            return {
                **self._counts,
                "key_values_retained": False,
                "coordinates_accessed": False,
            }


def compare_aggregate_counts(raw: dict, hook: dict) -> dict:
    raw_scroll = int(raw.get("vertical_scroll_events", 0)) + int(raw.get("horizontal_scroll_events", 0))
    deltas = {
        "keyboard_presses": int(raw.get("keyboard_presses", 0)) - int(hook.get("keyboard_presses", 0)),
        "mouse_button_presses": int(raw.get("mouse_button_presses", 0)) - int(hook.get("mouse_button_presses", 0)),
        "scroll_events": raw_scroll - int(hook.get("scroll_events", 0)),
    }
    observed = bool(any(
        int(raw.get(key, 0)) or int(hook.get(key, 0))
        for key in ("keyboard_presses", "mouse_button_presses")
    ) or raw_scroll or int(hook.get("scroll_events", 0)))
    return {
        "status": "observed" if observed else "insufficient_physical_input",
        "deltas": deltas,
        "exact_match": observed and all(value == 0 for value in deltas.values()),
    }


def run_raw_input_benchmark(duration: float = 0.0, injection_count: int = 0) -> dict:
    if not _HAS_WIN32:
        raise RuntimeError("Raw Input benchmark requires Windows")
    if duration < 0:
        raise ValueError("duration cannot be negative")
    if injection_count < 0:
        raise ValueError("injection_count cannot be negative")
    if duration > 0 and injection_count > 0:
        raise ValueError("physical comparison and injection visibility must run separately")
    if injection_count > 0 and local_recorder_active():
        raise RuntimeError(
            "Raw Input injection fixture is refused while the canonical recorder is active"
        )
    inventory = raw_input_device_inventory()
    raw_source = Win32RawInputSource()
    hook_probe = CountOnlyHookProbe() if duration > 0 or injection_count > 0 else None
    raw_started = raw_source.start()
    hook_started = hook_probe.start() if hook_probe else False
    registration_stats = raw_source.stats
    try:
        injection_probe = None
        if injection_count > 0 and raw_started and hook_started:
            raw_before = raw_source.counter.snapshot
            hook_before = hook_probe.snapshot
            sent_keyboard_messages = 0
            sent_mouse_messages = 0
            user32, _kernel32 = _configure_user32()
            original_foreground = int(user32.GetForegroundWindow() or 0)
            with tempfile.TemporaryDirectory(prefix="interaction-rhythm-raw-input-") as temp_dir:
                temp_root = Path(temp_dir)
                template = _compile_window_host(temp_root)
                host = _start_window_host(template, temp_root, "IRRawInputBench")
                try:
                    _position_fixture_under_pointer(host["hwnd"])
                    if not _force_foreground(host["hwnd"]):
                        raise RuntimeError("could not foreground Raw Input injection fixture")
                    for index in range(injection_count):
                        sent_keyboard_messages += _send_escape(9000 + index)
                        sent, _event_key = _send_mouse_fixture(
                            10000 + index,
                            MOUSE_FIXTURES[index % len(MOUSE_FIXTURES)],
                        )
                        sent_mouse_messages += sent
                    time.sleep(0.2)
                finally:
                    if original_foreground:
                        _force_foreground(original_foreground)
                    user32.PostMessageW(host["hwnd"], WM_CLOSE, 0, 0)
                    try:
                        host["process"].wait(timeout=2)
                    except Exception:
                        host["process"].terminate()
                        host["process"].wait(timeout=2)
            raw_after = raw_source.counter.snapshot
            hook_after = hook_probe.snapshot
            injection_probe = {
                "requested_presses": injection_count,
                "sent_keyboard_messages": sent_keyboard_messages,
                "sent_mouse_messages": sent_mouse_messages,
                "hook_marked_message_delta": (
                    hook_after["benchmark_messages_ignored"]
                    - hook_before["benchmark_messages_ignored"]
                ),
                "raw_keyboard_packet_delta": (
                    raw_after["keyboard_packets"] - raw_before["keyboard_packets"]
                ),
                "raw_keyboard_press_delta": (
                    raw_after["keyboard_presses"] - raw_before["keyboard_presses"]
                ),
                "raw_mouse_packet_delta": (
                    raw_after["mouse_packets"] - raw_before["mouse_packets"]
                ),
                "raw_mouse_button_delta": (
                    raw_after["mouse_button_presses"] - raw_before["mouse_button_presses"]
                ),
                "raw_marked_ignored_delta": (
                    raw_after["benchmark_packets_ignored"]
                    - raw_before["benchmark_packets_ignored"]
                ),
            }
            expected_raw_packets = sent_keyboard_messages + sent_mouse_messages
            injection_probe["status"] = (
                "raw_input_observed_and_filtered_sendinput"
                if injection_probe["raw_keyboard_packet_delta"] == sent_keyboard_messages
                and injection_probe["raw_mouse_packet_delta"] == sent_mouse_messages
                and injection_probe["raw_marked_ignored_delta"] == expected_raw_packets
                and injection_probe["raw_keyboard_press_delta"] == 0
                and injection_probe["raw_mouse_button_delta"] == 0
                else "raw_input_sendinput_filter_incomplete"
            )
        elif duration > 0 and raw_started and hook_started:
            time.sleep(duration)
        elif raw_started:
            time.sleep(0.1)
        raw_counts = raw_source.counter.snapshot
        hook_counts = hook_probe.snapshot if hook_probe else None
        comparison = compare_aggregate_counts(raw_counts, hook_counts) if duration > 0 else {
            "status": "injection_probe_only" if injection_probe else "transport_only",
            "deltas": None,
            "exact_match": False,
        }
    finally:
        if hook_probe:
            hook_probe.stop()
        raw_stopped = raw_source.stop()
    final_stats = raw_source.stats
    transport_passed = bool(
        raw_started
        and registration_stats["window_kind"] == "message_only"
        and registration_stats["devices_registered"]
        and registration_stats["callback_error_count"] == 0
        and raw_stopped
        and not final_stats["healthy"]
        and not final_stats["window_active"]
        and not final_stats["class_registered"]
        and not final_stats["devices_registered"]
        and not final_stats["thread_alive"]
    )
    injection_filter_passed = (
        None
        if injection_probe is None
        else injection_probe["status"] == "raw_input_observed_and_filtered_sendinput"
    )
    passed = bool(
        transport_passed
        and (duration <= 0 or hook_started)
        and injection_filter_passed is not False
    )
    return {
        "passed": passed,
        "replacement_gate_passed": bool(
            passed and comparison["status"] == "observed" and comparison["exact_match"]
        ),
        "scope": "Raw Input message-only transport and optional passive Hook comparison",
        "evidence_boundary": (
            "Registers real keyboard and mouse Raw Input with RIDEV_INPUTSINK and can compare "
            "aggregate physical input counts during a passive window. It retains no key values, "
            "device identifiers, coordinates, or payloads and never writes SQLite. A transport-only "
            "or insufficient-input run cannot justify replacing low-level hooks."
        ),
        "duration_seconds": duration,
        "injection_probe": injection_probe,
        "injection_filter_passed": injection_filter_passed,
        "hook_probe_started": hook_started,
        "device_inventory": inventory,
        "raw_counts": raw_counts,
        "hook_counts": hook_counts,
        "comparison": comparison,
        "registration_stats": registration_stats,
        "final_stats": final_stats,
        "resource_released": raw_stopped,
        "database_write_path": None,
        "foreground_fixture_path_token": RAW_INPUT_FIXTURE_TOKEN if injection_count else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--probe-injection", type=int, default=0, metavar="PRESSES")
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()
    report = run_raw_input_benchmark(args.duration, args.probe_injection)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
