"""Read-only long-run monitor for the canonical Interaction Rhythm runtime."""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import sqlite3
import statistics
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
STILL_ACTIVE = 259
EXPECTED_COMPONENTS = {
    "database_writer",
    "foreground_watcher",
    "keyboard_hook",
    "mouse_hook",
    "system",
    "system_events",
}


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


if os.name == "nt":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]
    kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
        wintypes.DWORD,
    ]


def _filetime_seconds(value: FILETIME) -> float:
    ticks = (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
    return ticks / 10_000_000.0


def find_process(process_name: str) -> dict[str, Any] | None:
    if os.name != "nt":
        raise RuntimeError("runtime soak monitoring requires Windows")
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.casefold() == process_name.casefold():
                return {"pid": int(entry.th32ProcessID), "threads": int(entry.cntThreads)}
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return None


def sample_process(pid: int, thread_count: int | None = None) -> dict[str, Any]:
    access = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess failed for PID {pid}")
    try:
        memory = PROCESS_MEMORY_COUNTERS_EX()
        memory.cb = ctypes.sizeof(memory)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        created = FILETIME()
        exited = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        if not kernel32.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
        ):
            raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
        handles = wintypes.DWORD()
        if not kernel32.GetProcessHandleCount(handle, ctypes.byref(handles)):
            raise OSError(ctypes.get_last_error(), "GetProcessHandleCount failed")
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        size = wintypes.DWORD(32768)
        path_buffer = ctypes.create_unicode_buffer(size.value)
        path = ""
        if kernel32.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(size)):
            path = path_buffer.value
        return {
            "pid": pid,
            "path": path,
            "alive": int(exit_code.value) == STILL_ACTIVE,
            "working_set_bytes": int(memory.WorkingSetSize),
            "private_bytes": int(memory.PrivateUsage),
            "threads": int(thread_count or 0),
            "handles": int(handles.value),
            "process_cpu_seconds": _filetime_seconds(kernel) + _filetime_seconds(user),
        }
    finally:
        kernel32.CloseHandle(handle)


def _table_total(connection: sqlite3.Connection, table: str, fields: tuple[str, ...]) -> dict[str, int]:
    projections = ["COUNT(*)"] + [f"COALESCE(SUM({field}),0)" for field in fields]
    values = connection.execute(f"SELECT {','.join(projections)} FROM {table}").fetchone()
    result = {"rows": int(values[0] or 0)}
    result.update({field: int(values[index + 1] or 0) for index, field in enumerate(fields)})
    return result


def read_database_snapshot(database: Path) -> dict[str, Any]:
    database = database.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        latest_states = {
            row["component"]: {
                "state": row["state"],
                "observed_at": row["observed_at"],
                "detail": row["detail"],
            }
            for row in connection.execute(
                "SELECT component,state,observed_at,detail FROM collector_states "
                "WHERE id IN (SELECT MAX(id) FROM collector_states GROUP BY component)"
            )
        }
        last_bucket = connection.execute(
            "SELECT MAX(ended_at) FROM interaction_buckets"
        ).fetchone()[0]
        last_segment = connection.execute(
            "SELECT MAX(ended_at) FROM observation_segments WHERE component='collector'"
        ).fetchone()[0]
        return {
            "quick_check": quick_check,
            "interaction": _table_total(
                connection,
                "interaction_buckets",
                ("keyboard_down_count", "mouse_button_count", "scroll_event_count"),
            ),
            "foreground": _table_total(connection, "foreground_intervals", ()),
            "observation_segments": _table_total(connection, "observation_segments", ()),
            "collector_states": _table_total(connection, "collector_states", ()),
            "latest_component_states": latest_states,
            "last_bucket_ended_at": last_bucket,
            "last_segment_ended_at": last_segment,
            "database_bytes": sum(
                path.stat().st_size
                for path in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm"))
                if path.exists()
            ),
        }
    finally:
        connection.close()


def read_segment_window(database: Path, started_at: str, ended_at: str) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        rows = connection.execute(
            "SELECT state,started_at,ended_at FROM observation_segments "
            "WHERE component='collector' AND started_at<? AND ended_at>? ORDER BY started_at",
            (ended_at, started_at),
        ).fetchall()
    finally:
        connection.close()
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(ended_at)
    state_seconds: dict[str, float] = {}
    intervals: list[tuple[datetime, datetime]] = []
    for state, raw_start, raw_end in rows:
        row_start = max(start, datetime.fromisoformat(raw_start))
        row_end = min(end, datetime.fromisoformat(raw_end))
        if row_end <= row_start:
            continue
        state_seconds[state] = state_seconds.get(state, 0.0) + (row_end - row_start).total_seconds()
        intervals.append((row_start, row_end))
    intervals.sort()
    gaps = []
    cursor = start
    for row_start, row_end in intervals:
        if row_start > cursor:
            gaps.append((row_start - cursor).total_seconds())
        cursor = max(cursor, row_end)
    if end > cursor:
        gaps.append((end - cursor).total_seconds())
    return {
        "segments": len(intervals),
        "state_seconds": {key: round(value, 3) for key, value in sorted(state_seconds.items())},
        "max_unobserved_gap_seconds": round(max(gaps, default=0.0), 3),
        "total_unobserved_gap_seconds": round(sum(gaps), 3),
    }


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return float(ordered[rank])


def delta(after: dict[str, int], before: dict[str, int], key: str) -> int:
    return int(after.get(key, 0)) - int(before.get(key, 0))


def analyze_report(
    samples: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
    segment_window: dict[str, Any],
    duration_seconds: float,
    minimum_duration_seconds: float,
) -> dict[str, Any]:
    cpu_values = [float(item.get("cpu_percent_normalized", 0.0)) for item in samples[1:]]
    working = [float(item["working_set_bytes"]) for item in samples]
    private = [float(item["private_bytes"]) for item in samples]
    required_states = after.get("latest_component_states", {})
    missing_components = sorted(EXPECTED_COMPONENTS - set(required_states))
    unhealthy_components = sorted(
        component
        for component in EXPECTED_COMPONENTS & set(required_states)
        if required_states[component].get("state") != "running"
    )
    event_delta = {
        key: delta(after["interaction"], before["interaction"], key)
        for key in ("rows", "keyboard_down_count", "mouse_button_count", "scroll_event_count")
    }
    checks = {
        "minimum_duration_reached": duration_seconds >= minimum_duration_seconds,
        "process_alive": bool(samples and samples[-1].get("alive")),
        "pid_stable": len({item.get("pid") for item in samples}) == 1,
        "database_quick_check": after.get("quick_check") == "ok",
        "all_components_running": not missing_components and not unhealthy_components,
        "collector_gap_within_35_seconds": segment_window.get("max_unobserved_gap_seconds", 9999) <= 35,
        "working_set_p95_within_150_mib": percentile(working, 0.95) <= 150 * 1024 * 1024,
        "private_memory_p95_within_150_mib": percentile(private, 0.95) <= 150 * 1024 * 1024,
        "normalized_cpu_p95_within_1_5_percent": percentile(cpu_values, 0.95) <= 1.5,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "missing_components": missing_components,
        "unhealthy_components": unhealthy_components,
        "event_delta": event_delta,
        "database_growth_bytes": int(after["database_bytes"]) - int(before["database_bytes"]),
        "cpu_percent_normalized": {
            "mean": round(statistics.fmean(cpu_values), 4) if cpu_values else 0.0,
            "p95": round(percentile(cpu_values, 0.95), 4),
            "max": round(max(cpu_values, default=0.0), 4),
        },
        "working_set_mib": {
            "p95": round(percentile(working, 0.95) / 1024 / 1024, 3),
            "max": round(max(working, default=0.0) / 1024 / 1024, 3),
        },
        "private_memory_mib": {
            "p95": round(percentile(private, 0.95) / 1024 / 1024, 3),
            "max": round(max(private, default=0.0) / 1024 / 1024, 3),
        },
        "thread_count_max": max((int(item.get("threads", 0)) for item in samples), default=0),
        "handle_count_max": max((int(item.get("handles", 0)) for item in samples), default=0),
        "segment_window": segment_window,
    }


def render_markdown(report: dict[str, Any]) -> str:
    result = report["result"]
    run_status = report.get("status", "completed")
    status = "pass" if result["passed"] else run_status if run_status != "completed" else "blocked"
    checks = "\n".join(
        f"| `{name}` | {'pass' if passed else 'blocked'} |"
        for name, passed in result["checks"].items()
    )
    return f"""---
title: 扣舷-数字节律运行时长时监控
status: {status}
date: {report['ended_at'][:10]}
---

# 运行时长时监控

监控 PID `{report['pid']}`，持续 `{report['duration_seconds']:.1f}` 秒，共 `{len(report['samples'])}` 个只读样本。程序路径：`{report['process_path']}`。

- 运行状态：`{run_status}`。
- 中断原因：`{report.get('interruption', {}).get('reason', '') or 'none'}`。

| 检查 | 结果 |
| --- | --- |
{checks}

- CPU p95：`{result['cpu_percent_normalized']['p95']}%`（按逻辑处理器归一）。
- 工作集 p95：`{result['working_set_mib']['p95']} MiB`。
- 私有内存 p95：`{result['private_memory_mib']['p95']} MiB`。
- 数据库增长：`{result['database_growth_bytes']} bytes`。
- 最大未观察间隙：`{result['segment_window']['max_unobserved_gap_seconds']} 秒`。
- 输入增量：`{json.dumps(result['event_delta'], ensure_ascii=False)}`。
- 组件异常：`{json.dumps(result['unhealthy_components'], ensure_ascii=False)}`。
"""


def write_report_checkpoint(
    report: dict[str, Any],
    json_report: Path | None,
    markdown_report: Path | None,
) -> None:
    outputs = []
    if json_report:
        outputs.append((json_report, json.dumps(report, ensure_ascii=False, indent=2) + "\n"))
    if markdown_report:
        outputs.append((markdown_report, render_markdown(report)))
    for path, content in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def build_report(
    *,
    database: Path,
    pid: int,
    started: datetime,
    ended: datetime,
    before: dict[str, Any],
    after: dict[str, Any],
    samples: list[dict[str, Any]],
    minimum_duration_seconds: float,
    status: str,
    interruption: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        segment_window = read_segment_window(database, started.isoformat(), ended.isoformat())
    except Exception as exc:
        segment_window = {
            "segments": 0,
            "state_seconds": {},
            "max_unobserved_gap_seconds": max(0.0, (ended - started).total_seconds()),
            "total_unobserved_gap_seconds": max(0.0, (ended - started).total_seconds()),
            "analysis_error": f"{type(exc).__name__}: {str(exc)[:240]}",
        }
    duration_seconds = max(0.0, (ended - started).total_seconds())
    result = analyze_report(
        samples,
        before,
        after,
        segment_window,
        duration_seconds,
        minimum_duration_seconds,
    )
    if status != "completed":
        result["passed"] = False
    return {
        "schema_version": 2,
        "scope": "read-only canonical runtime, SQLite/WAL, process resources and collector health",
        "status": status,
        "interruption": interruption or {},
        "started_at": started.isoformat(timespec="seconds"),
        "ended_at": ended.isoformat(timespec="seconds"),
        "duration_seconds": duration_seconds,
        "minimum_duration_seconds": minimum_duration_seconds,
        "pid": pid,
        "process_path": samples[0].get("path", "") if samples else "",
        "database": str(database.resolve()),
        "before": before,
        "after": after,
        "samples": samples,
        "result": result,
    }


def run_monitor(
    database: Path,
    process_name: str,
    duration_seconds: float,
    interval_seconds: float,
    minimum_duration_seconds: float,
    checkpoint_callback=None,
) -> dict[str, Any]:
    process = find_process(process_name)
    if not process:
        raise RuntimeError(f"{process_name} is not running")
    pid = process["pid"]
    started = datetime.now().astimezone()
    before = read_database_snapshot(database)
    last_snapshot = before
    samples: list[dict[str, Any]] = []
    deadline = time.monotonic() + max(0.0, duration_seconds)
    previous_cpu = None
    previous_clock = None
    status = "completed"
    interruption: dict[str, Any] = {}
    try:
        while True:
            current = find_process(process_name)
            if not current:
                status = "interrupted"
                interruption = {"reason": "process_exited", "expected_pid": pid}
                break
            if current["pid"] != pid:
                status = "interrupted"
                interruption = {
                    "reason": "pid_changed",
                    "expected_pid": pid,
                    "observed_pid": current["pid"],
                }
                break
            sample = sample_process(pid, current["threads"])
            clock = time.monotonic()
            if previous_cpu is None:
                sample["cpu_percent_normalized"] = 0.0
            else:
                cpu_delta = max(0.0, sample["process_cpu_seconds"] - previous_cpu)
                wall_delta = max(0.001, clock - previous_clock)
                sample["cpu_percent_normalized"] = cpu_delta / wall_delta / max(1, os.cpu_count() or 1) * 100
            sample["sampled_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            last_snapshot = read_database_snapshot(database)
            sample["database_bytes"] = last_snapshot["database_bytes"]
            samples.append(sample)
            previous_cpu = sample["process_cpu_seconds"]
            previous_clock = clock
            if checkpoint_callback:
                checkpoint_now = datetime.now().astimezone()
                checkpoint_callback(build_report(
                    database=database,
                    pid=pid,
                    started=started,
                    ended=checkpoint_now,
                    before=before,
                    after=last_snapshot,
                    samples=samples,
                    minimum_duration_seconds=minimum_duration_seconds,
                    status="in_progress",
                ))
            if clock >= deadline:
                break
            time.sleep(min(max(0.2, interval_seconds), max(0.0, deadline - clock)))
    except KeyboardInterrupt:
        status = "interrupted"
        interruption = {"reason": "keyboard_interrupt", "expected_pid": pid}
    except Exception as exc:
        status = "interrupted"
        interruption = {
            "reason": "monitor_error",
            "expected_pid": pid,
            "error_type": type(exc).__name__,
            "message": str(exc)[:240],
        }
    ended = datetime.now().astimezone()
    try:
        after = read_database_snapshot(database)
    except Exception as exc:
        after = last_snapshot
        interruption = dict(interruption)
        interruption.setdefault("reason", "database_snapshot_failed")
        interruption["final_snapshot_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        if status == "completed":
            status = "interrupted"
    report = build_report(
        database=database,
        pid=pid,
        started=started,
        ended=ended,
        before=before,
        after=after,
        samples=samples,
        minimum_duration_seconds=minimum_duration_seconds,
        status=status,
        interruption=interruption,
    )
    if checkpoint_callback:
        checkpoint_callback(report)
    return report


def main() -> int:
    default_database = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "InteractionRhythm"
        / "data"
        / "tracker.db"
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=default_database)
    parser.add_argument("--process-name", default="InteractionRhythm.exe")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--minimum-duration", type=float, default=8 * 60 * 60)
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--markdown-report", type=Path)
    args = parser.parse_args()
    checkpoint_callback = lambda report: write_report_checkpoint(
        report, args.json_report, args.markdown_report
    )
    report = run_monitor(
        args.database,
        args.process_name,
        max(0.0, args.duration),
        max(0.2, args.interval),
        max(0.0, args.minimum_duration),
        checkpoint_callback=checkpoint_callback,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    return 0 if report["result"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
