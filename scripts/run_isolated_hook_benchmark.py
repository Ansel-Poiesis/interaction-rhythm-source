"""Run the two-process hook benchmark against an isolated development runtime."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitor import _query_process_path
from scripts.benchmark_win32_keyboard_hook import (
    _fixture_artifact_counts,
    _keyboard_total,
    run_live_benchmark,
)
from scripts.benchmark_win32_mouse_hook import run_live_mouse_benchmark


WM_COMMAND = 0x0111
ID_REFRESH = 1002
ID_QUIT = 1003
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
CREATE_NO_WINDOW = 0x08000000
TRAY_CLASS_PREFIX = "InteractionRhythmTray"


def _configure_win32():
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return user32, kernel32, enum_proc


def _tray_windows() -> list[dict]:
    user32, _kernel32, enum_proc = _configure_win32()
    windows = []

    def visit(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(hwnd, name, len(name)) <= 0:
            return True
        class_name = name.value
        if not class_name.startswith(TRAY_CLASS_PREFIX):
            return True
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        windows.append({
            "hwnd": int(hwnd),
            "pid": int(process_id.value),
            "class_name": class_name,
            "executable": _query_process_path(process_id.value),
        })
        return True

    callback = enum_proc(visit)
    user32.EnumWindows(callback, 0)
    return windows


def _same_path(first: str | Path, second: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(str(first))) == os.path.normcase(
        os.path.abspath(str(second))
    )


def _find_tray(executable: Path | None = None, pid: int | None = None) -> dict | None:
    for item in _tray_windows():
        if pid is not None and item["pid"] != int(pid):
            continue
        if executable is not None and not _same_path(item["executable"], executable):
            continue
        return item
    return None


def _wait_for_tray(
    executable: Path | None = None,
    pid: int | None = None,
    timeout: float = 8.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = _find_tray(executable=executable, pid=pid)
        if item:
            return item
        time.sleep(0.05)
    raise TimeoutError("InteractionRhythm tray window was not found")


def _wait_for_process_exit(pid: int, timeout: float = 8.0) -> bool:
    _user32, kernel32, _enum_proc = _configure_win32()
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def _flush_and_exit(tray: dict, timeout: float = 8.0) -> None:
    user32, _kernel32, _enum_proc = _configure_win32()
    if not user32.PostMessageW(tray["hwnd"], WM_COMMAND, ID_REFRESH, 0):
        raise RuntimeError("failed to request runtime flush")
    time.sleep(0.75)
    if not user32.PostMessageW(tray["hwnd"], WM_COMMAND, ID_QUIT, 0):
        raise RuntimeError("failed to request runtime exit")
    if not _wait_for_process_exit(tray["pid"], timeout=timeout):
        raise TimeoutError(f"runtime PID {tray['pid']} did not exit")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/today",
                timeout=0.5,
            ) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def _wait_for_collector(database: Path, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    required = {
        "foreground_watcher",
        "keyboard_hook",
        "mouse_hook",
        "database_writer",
        "system",
        "system_events",
    }
    latest = {}
    while time.monotonic() < deadline:
        if database.is_file():
            connection = sqlite3.connect(database, timeout=1)
            try:
                rows = connection.execute(
                    "SELECT component,state FROM collector_states ORDER BY id"
                ).fetchall()
                latest = {component: state for component, state in rows}
            finally:
                connection.close()
            if all(latest.get(component) == "running" for component in required):
                return latest
        time.sleep(0.1)
    raise TimeoutError(f"isolated collector did not become healthy: {latest}")


def _formal_snapshot(database: Path) -> dict:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=2)
    try:
        mouse = connection.execute(
            "SELECT COALESCE(SUM(count),0) FROM mouse_events"
        ).fetchone()[0]
        return {
            "keyboard_count": _keyboard_total(database),
            "mouse_count": int(mouse or 0),
            "fixture_artifacts": _fixture_artifact_counts(database),
        }
    finally:
        connection.close()


def _isolated_database_result(
    database: Path,
    expected_intervals: int,
    measurement_started_at: str | None = None,
    measurement_ended_at: str | None = None,
    ignored_benchmark_event_count: int | None = None,
    expected_ignored_benchmark_event_count: int | None = None,
) -> dict:
    connection = sqlite3.connect(database, timeout=2)
    try:
        input_totals = connection.execute(
            "SELECT COALESCE(SUM(keyboard_down_count),0),"
            "COALESCE(SUM(mouse_button_count),0),"
            "COALESCE(SUM(scroll_event_count),0) FROM interaction_buckets"
        ).fetchone()
        input_by_app = connection.execute(
            "SELECT process_name,COALESCE(SUM(keyboard_down_count),0),"
            "COALESCE(SUM(mouse_button_count),0),"
            "COALESCE(SUM(scroll_event_count),0) FROM interaction_buckets "
            "GROUP BY process_name ORDER BY process_name"
        ).fetchall()
        fixture_where = (
            "WHERE process_name IN ('IRBenchA.exe','IRBenchB.exe')"
        )
        fixture_params = []
        if measurement_started_at and measurement_ended_at:
            fixture_where += (
                " AND julianday(started_at)>=julianday(?)"
                " AND julianday(ended_at)<=julianday(?)"
            )
            fixture_params.extend([measurement_started_at, measurement_ended_at])
        rows = connection.execute(
            "SELECT process_name,COUNT(*),"
            "SUM(CASE WHEN ended_at > started_at THEN 1 ELSE 0 END) "
            "FROM foreground_intervals "
            f"{fixture_where} GROUP BY process_name ORDER BY process_name",
            fixture_params,
        ).fetchall()
        total_fixture_intervals = connection.execute(
            "SELECT COUNT(*) FROM foreground_intervals "
            "WHERE process_name IN ('IRBenchA.exe','IRBenchB.exe')"
        ).fetchone()[0]
        all_rows = connection.execute(
            "SELECT process_name,COUNT(*) FROM foreground_intervals "
            "GROUP BY process_name ORDER BY process_name"
        ).fetchall()
        by_app = {
            app: {"interval_count": int(count), "positive_boundary_count": int(positive or 0)}
            for app, count, positive in rows
        }
        actual_intervals = sum(item["interval_count"] for item in by_app.values())
        outside = {"starts_before_window": 0, "ends_after_window": 0}
        if measurement_started_at and measurement_ended_at:
            outside_row = connection.execute(
                "SELECT "
                "SUM(CASE WHEN julianday(started_at)<julianday(?) THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN julianday(ended_at)>julianday(?) THEN 1 ELSE 0 END) "
                "FROM foreground_intervals "
                "WHERE process_name IN ('IRBenchA.exe','IRBenchB.exe')",
                (measurement_started_at, measurement_ended_at),
            ).fetchone()
            outside = {
                "starts_before_window": int(outside_row[0] or 0),
                "ends_after_window": int(outside_row[1] or 0),
            }
        keyboard_count, button_count, scroll_count = (
            int(value or 0) for value in input_totals
        )
        fixture_input_count = sum(
            int(keyboard or 0) + int(buttons or 0) + int(scroll or 0)
            for app, keyboard, buttons, scroll in input_by_app
            if app in {"IRBenchA.exe", "IRBenchB.exe"}
        )
        ignored_matches = (
            ignored_benchmark_event_count == expected_ignored_benchmark_event_count
            if expected_ignored_benchmark_event_count is not None
            else True
        )
        return {
            "collector_ignored_benchmark_event_count": ignored_benchmark_event_count,
            "expected_collector_ignored_benchmark_event_count": (
                expected_ignored_benchmark_event_count
            ),
            "collector_ignored_benchmark_events_match": ignored_matches,
            "stored_input_events": {
                "keyboard": keyboard_count,
                "mouse_button": button_count,
                "scroll": scroll_count,
                "total": keyboard_count + button_count + scroll_count,
                "by_app": {
                    app: {
                        "keyboard": int(keyboard or 0),
                        "mouse_button": int(buttons or 0),
                        "scroll": int(scroll or 0),
                    }
                    for app, keyboard, buttons, scroll in input_by_app
                },
            },
            "stored_fixture_input_event_count": fixture_input_count,
            "foreground_intervals_by_app": by_app,
            "all_foreground_intervals_by_app": {
                app: int(count) for app, count in all_rows
            },
            "expected_fixture_intervals": expected_intervals,
            "captured_fixture_intervals": actual_intervals,
            "out_of_window_fixture_intervals": max(
                0, int(total_fixture_intervals or 0) - actual_intervals
            ),
            "out_of_window_interval_boundaries": outside,
            "measurement_started_at": measurement_started_at,
            "measurement_ended_at": measurement_ended_at,
            "transition_capture_ratio": round(
                actual_intervals / expected_intervals if expected_intervals else 0,
                4,
            ),
            "passed": (
                ignored_matches
                and fixture_input_count == 0
                and set(by_app) == {"IRBenchA.exe", "IRBenchB.exe"}
                and actual_intervals == expected_intervals
                and all(
                    item["positive_boundary_count"] == item["interval_count"]
                    for item in by_app.values()
                )
            ),
        }
    finally:
        connection.close()


def _start_runtime(
    python_executable: Path,
    data_dir: Path,
    port: int,
    log_path: Path,
) -> tuple[subprocess.Popen, object, Path, Path]:
    ready_file = data_dir.parent / "collector-ready.json"
    stop_file = data_dir.parent / "collector-stop"
    result_file = data_dir.parent / "collector-result.json"
    env = dict(os.environ)
    env.update({
        "PYTHONUTF8": "1",
        "INTERACTION_RHYTHM_DATA_DIR": str(data_dir.resolve()),
        "INTERACTION_RHYTHM_PORT": str(port),
        "INTERACTION_RHYTHM_MUTEX_NAME": (
            rf"Local\InteractionRhythm.Benchmark.{uuid.uuid4().hex}"
        ),
        "INTERACTION_RHYTHM_DISABLE_LEGACY_IMPORT": "1",
        "INTERACTION_RHYTHM_UPDATE_URL": "http://127.0.0.1:1/disabled.json",
    })
    log_stream = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            str(python_executable),
            str(Path(__file__).resolve()),
            "--collector-worker",
            "--data-dir",
            str(data_dir),
            "--port",
            str(port),
            "--ready-file",
            str(ready_file),
            "--stop-file",
            str(stop_file),
            "--result-file",
            str(result_file),
        ],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_stream,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    return process, log_stream, stop_file, result_file


def run_collector_worker(
    data_dir: Path,
    port: int,
    ready_file: Path,
    stop_file: Path,
    result_file: Path,
) -> int:
    from db import DB
    from monitor import Win32CollectorAdapter
    from stats import StatsServer

    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["INTERACTION_RHYTHM_DATA_DIR"] = str(data_dir)
    database = DB(data_dir / "tracker.db")
    collector = Win32CollectorAdapter(database)
    server = StatsServer(database, port=port)
    try:
        server.start()
        collector.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if collector._collection_healthy():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("collector worker did not become healthy")
        ready_file.write_text(
            json.dumps({"pid": os.getpid(), "port": server.port}) + "\n",
            encoding="utf-8",
        )
        while not stop_file.exists():
            time.sleep(0.05)
        return 0
    finally:
        result_file.write_text(
            json.dumps({
                "foreground_event_hook": collector._win.event_stats,
                "input_protocol": {
                    "keyboard_ignored_benchmark_event_count": (
                        collector._keys.ignored_benchmark_count
                    ),
                    "mouse_ignored_benchmark_event_count": (
                        collector._mouse.ignored_benchmark_count
                    ),
                },
                "system_events": collector._system.event_stats,
            }) + "\n",
            encoding="utf-8",
        )
        collector.stop()
        server.stop()
        database.checkpoint()
        database.close()


def _restart_formal(executable: Path, port: int) -> dict:
    process = subprocess.Popen(
        [str(executable), "--background"],
        cwd=executable.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not _wait_for_http(port, timeout=12):
        raise RuntimeError("formal runtime did not restore its HTTP endpoint")
    tray = _wait_for_tray(executable=executable, timeout=12)
    return {"pid": tray["pid"], "http_ready": True, "spawn_pid": process.pid}


def run_isolated_benchmark(
    formal_executable: Path,
    formal_database: Path,
    runtime_python: Path,
    formal_port: int = 19023,
    press_count: int = 60,
    repeat_downs: int = 5,
    switch_dwell_ms: float = 10.0,
    input_kind: str = "keyboard",
) -> dict:
    formal_executable = Path(formal_executable).resolve()
    formal_database = Path(formal_database).resolve()
    runtime_python = Path(runtime_python).resolve()
    if not formal_executable.is_file():
        raise FileNotFoundError(formal_executable)
    if not formal_database.is_file():
        raise FileNotFoundError(formal_database)
    if not runtime_python.is_file():
        raise FileNotFoundError(runtime_python)

    formal_tray = _wait_for_tray(executable=formal_executable)
    formal_stopped = False
    formal_recovery = None
    isolated_process = None
    isolated_log_stream = None
    isolated_stop_file = None
    isolated_result_file = None
    report = None

    try:
        _flush_and_exit(formal_tray)
        formal_stopped = True
        formal_before = _formal_snapshot(formal_database)

        with tempfile.TemporaryDirectory(prefix="interaction-rhythm-runtime-") as temp_dir:
            temp_root = Path(temp_dir)
            data_dir = temp_root / "data"
            database = data_dir / "tracker.db"
            port = _free_port()
            (
                isolated_process,
                isolated_log_stream,
                isolated_stop_file,
                isolated_result_file,
            ) = _start_runtime(runtime_python, data_dir, port, temp_root / "runtime.log")
            if not _wait_for_http(port):
                raise RuntimeError("isolated runtime HTTP endpoint did not become ready")
            collector_health = _wait_for_collector(database)
            if input_kind == "mouse":
                benchmark = run_live_mouse_benchmark(
                    event_count=press_count,
                    switch_dwell_ms=switch_dwell_ms,
                )
            else:
                benchmark = run_live_benchmark(
                    press_count=press_count,
                    host_count=2,
                    repeat_downs=repeat_downs,
                    switch_dwell_ms=switch_dwell_ms,
                )
            time.sleep(1.0)
            isolated_stop_file.write_text("stop\n", encoding="ascii")
            isolated_process.wait(timeout=8)
            if isolated_process.returncode != 0:
                raise RuntimeError(
                    f"isolated collector worker exited with {isolated_process.returncode}"
                )
            collector_runtime = json.loads(
                isolated_result_file.read_text(encoding="utf-8")
            )
            ignored_key = f"{input_kind}_ignored_benchmark_event_count"
            isolated_result = _isolated_database_result(
                database,
                press_count,
                benchmark["scenario"]["measurement_started_at"],
                benchmark["scenario"]["measurement_ended_at"],
                collector_runtime["input_protocol"][ignored_key],
                benchmark["scenario"]["expected_collector_ignored_event_count"],
            )
            isolated_log_stream.close()
            isolated_log_stream = None
            isolated_process = None

            report = {
                "passed": bool(benchmark["passed"] and isolated_result["passed"]),
                "scope": (
                    f"isolated v0.10.1 runtime + {input_kind} hook + "
                    "two-process foreground switching"
                ),
                "environment": {
                    "isolated_port": port,
                    "runtime_python": str(runtime_python),
                    "input_kind": input_kind,
                    "temporary_data_deleted_after_run": True,
                },
                "collector_health": collector_health,
                "collector_runtime": collector_runtime,
                "hook_benchmark": benchmark,
                "isolated_database": isolated_result,
                "formal_runtime": {
                    "stopped_pid": formal_tray["pid"],
                    "before": formal_before,
                },
            }
    finally:
        if isolated_process is not None and isolated_process.poll() is None:
            try:
                if isolated_stop_file is not None:
                    isolated_stop_file.write_text("stop\n", encoding="ascii")
                    isolated_process.wait(timeout=8)
                else:
                    isolated_process.terminate()
                    isolated_process.wait(timeout=4)
            except Exception:
                isolated_process.kill()
                isolated_process.wait(timeout=4)
        if isolated_log_stream is not None:
            isolated_log_stream.close()
        if formal_stopped:
            formal_recovery = _restart_formal(formal_executable, formal_port)

    if report is None:
        raise RuntimeError("isolated benchmark did not produce a report")
    time.sleep(1.0)
    formal_after = _formal_snapshot(formal_database)
    before = report["formal_runtime"]["before"]
    formal_guard = {
        "keyboard_count_delta": formal_after["keyboard_count"] - before["keyboard_count"],
        "mouse_count_delta": formal_after["mouse_count"] - before["mouse_count"],
        "fixture_artifact_delta": {
            table: formal_after["fixture_artifacts"][table]
            - before["fixture_artifacts"][table]
            for table in before["fixture_artifacts"]
        },
        "recovery": formal_recovery,
    }
    formal_guard["passed"] = (
        formal_guard["keyboard_count_delta"] == 0
        and formal_guard["mouse_count_delta"] == 0
        and sum(formal_guard["fixture_artifact_delta"].values()) == 0
        and bool(formal_recovery and formal_recovery["http_ready"])
    )
    report["formal_runtime"].update({
        "after": formal_after,
        "guard": formal_guard,
    })
    report["passed"] = bool(report["passed"] and formal_guard["passed"])
    return report


def main() -> int:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--formal-executable",
        type=Path,
        default=local_app_data / "Programs/InteractionRhythm/InteractionRhythm.exe",
    )
    parser.add_argument(
        "--formal-database",
        type=Path,
        default=local_app_data / "Programs/InteractionRhythm/data/tracker.db",
    )
    parser.add_argument(
        "--runtime-python",
        type=Path,
        default=ROOT / ".venv/Scripts/python.exe",
    )
    parser.add_argument("--formal-port", type=int, default=19023)
    parser.add_argument("--presses", type=int, default=60)
    parser.add_argument("--events", type=int)
    parser.add_argument(
        "--input-kind",
        choices=("keyboard", "mouse"),
        default="keyboard",
    )
    parser.add_argument("--repeat-downs", type=int, default=5)
    parser.add_argument("--switch-dwell-ms", type=float, default=10.0)
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()

    report = run_isolated_benchmark(
        formal_executable=args.formal_executable,
        formal_database=args.formal_database,
        runtime_python=args.runtime_python,
        formal_port=args.formal_port,
        press_count=args.events if args.events is not None else args.presses,
        repeat_downs=args.repeat_downs,
        switch_dwell_ms=args.switch_dwell_ms,
        input_kind=args.input_kind,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


def collector_worker_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_collector_worker(
        data_dir=args.data_dir,
        port=args.port,
        ready_file=args.ready_file,
        stop_file=args.stop_file,
        result_file=args.result_file,
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--collector-worker":
        raise SystemExit(collector_worker_main(sys.argv[2:]))
    raise SystemExit(main())
