"""扣舷-数字节律 - 主入口（原生桌面客户端）。"""
import json
import secrets
import sys
from datetime import datetime
import os
import signal
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

from config import PORT, APP_NAME, APP_MUTEX_NAME, DB_RETENTION_DAYS, HOURLY_RETENTION_DAYS
from diagnostics import log_event, log_exception
from db import DB
from startup_guard import (
    existing_instance_is_stale as _guard_stale,
    query_status as _query_status,
    takeover_in_cooldown as _guard_takeover_in_cooldown,
)
from interaction_rhythm.runtime_paths import resolve_data_dir
from stats import StatsServer

_JOB_HANDLE = None
_INSTANCE_MUTEX_HANDLE = None


def resource_path(relative: str) -> Path:
    """获取资源路径，兼容 PyInstaller 打包模式"""
    if getattr(sys, 'frozen', False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent
    return base / relative


def data_path() -> Path:
    """获取数据目录（DB 等可写文件），打包模式下放在 exe 同级"""
    root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    return resolve_data_dir(root)


DB_PATH = data_path() / "tracker.db"

# 本地内部关机令牌：仅用于前台实例接管坏实例时请求旧实例优雅退出。
_SHUTDOWN_TOKEN = secrets.token_hex(16)


def legacy_database_candidates() -> list[Path]:
    """Find old tracker.db files from installed and portable-era layouts."""
    candidates = []
    app_root = data_path().parent
    candidates.extend([
        app_root / "data" / "tracker.db",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "InteractionRhythm" / "data" / "tracker.db",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "TypeTracker" / "data" / "tracker.db",
        Path(os.environ.get("APPDATA", "")) / "InteractionRhythm" / "tracker.db",
        Path(os.environ.get("APPDATA", "")) / "TypeTracker" / "tracker.db",
    ])

    source_root = Path(__file__).parent
    for base in (
        source_root / "data",
        source_root / "dist" / "current" / "InteractionRhythm" / "data",
        source_root / "dist" / "build" / "InteractionRhythm" / "data",
    ):
        candidates.append(base / "tracker.db")
    for parent in (
        source_root / "dist" / "releases",
        source_root / "dist" / "archive",
    ):
        if parent.exists():
            candidates.extend(parent.glob("**/data/tracker.db"))

    seen = set()
    result = []
    current = str(DB_PATH.resolve())
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = str(resolved)
        if key == current or key in seen:
            continue
        seen.add(key)
        if resolved.exists():
            result.append(resolved)
    return result


def local_server_healthy(timeout: float = 0.12) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/api/today", timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def _local_db_last_activity():
    """本数据目录最近一条采集活动时间（旧版实例无 status 端点时的回退信号）。"""
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute(
                "SELECT MAX(t) FROM ("
                "  SELECT started_at AS t FROM foreground_intervals "
                "  UNION ALL SELECT ts FROM keystrokes "
                "  UNION ALL SELECT ts FROM mouse_events "
                "  UNION ALL SELECT started_at FROM interaction_buckets"
                ")"
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
    except Exception:
        pass
    return None


def _existing_instance_is_stale() -> bool:
    """已有实例是否已停止采集（paused/locked/卡死/长时间无新数据）。"""
    reachable, status = _query_status(PORT)
    return _guard_stale(reachable, status, _local_db_last_activity())


def _read_takeover_marker() -> str | None:
    """读取最近一次接管尝试的时间戳（冷却期依据）。"""
    try:
        marker = data_path() / "takeover.cooldown"
        if marker.exists():
            return marker.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


def _takeover_in_cooldown() -> bool:
    """最近一次接管尝试是否仍在冷却期（15 分钟内）。"""
    return _guard_takeover_in_cooldown(_read_takeover_marker())


def _mark_takeover_attempt():
    """记录一次接管尝试的时间戳（用于冷却期判定）。"""
    try:
        (data_path() / "takeover.cooldown").write_text(
            datetime.now().astimezone().isoformat(), encoding="utf-8"
        )
    except Exception:
        pass


def set_windows_app_id():
    """让 Windows 任务栏把进程归到扣舷-数字节律，而不是 python.exe。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kouxian.DigitalRhythm")
    except Exception:
        pass


def acquire_single_instance_lock() -> bool:
    """Use a named Windows mutex so newer builds cannot run side by side."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.GetLastError.restype = wintypes.DWORD
        handle = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
        if not handle:
            return True

        global _INSTANCE_MUTEX_HANDLE
        already_exists = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        if already_exists:
            # 关键：失败时立即关闭句柄，否则重试循环里每次失败都泄漏一个
            # 已存在互斥体的句柄，导致即使旧实例退出，互斥体也永远"已存在"，
            # 新实例永远拿不到锁（接管坏实例时表现为 25s 超时后静默退出）。
            kernel32.CloseHandle(handle)
            _INSTANCE_MUTEX_HANDLE = None
            return False
        _INSTANCE_MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


def _release_instance_lock():
    global _INSTANCE_MUTEX_HANDLE
    if not _INSTANCE_MUTEX_HANDLE or os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(_INSTANCE_MUTEX_HANDLE)
    except Exception:
        pass
    _INSTANCE_MUTEX_HANDLE = None


def _retry_acquire_lock(timeout: float = 8.0) -> bool:
    """释放旧互斥句柄后重试独占启动锁（用于接管坏实例后）。"""
    _release_instance_lock()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if acquire_single_instance_lock():
            return True
        time.sleep(0.25)
    return False


def _write_shutdown_token():
    """把内部关机令牌写入数据目录，供同机前台实例接管时读取。"""
    try:
        (data_path() / "shutdown.token").write_text(_SHUTDOWN_TOKEN, encoding="utf-8")
    except Exception:
        pass


def request_existing_instance_exit(timeout: float = 2.0) -> bool:
    """请求已有实例优雅退出（本地 shutdown 端点 + 落盘令牌）。"""
    token = None
    try:
        token_file = data_path() / "shutdown.token"
        if token_file.exists():
            token = token_file.read_text(encoding="utf-8").strip()
    except Exception:
        token = None
    if not token:
        return False
    try:
        payload = json.dumps({"token": token}).encode("utf-8")
        req = Request(
            f"http://127.0.0.1:{PORT}/api/internal/shutdown",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def _force_terminate_other_instance() -> bool:
    """兜底：优雅退出失败时，强制终止同名 exe 的其他实例（非本进程）。

    仅打包版（frozen）启用，避免源码模式下误杀同机其他 python 进程。
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_uint32),
                ("cntUsage", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_uint32),
                ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32),
                ("pcPriClassBase", ctypes.c_int32),
                ("dwFlags", ctypes.c_uint32),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x00000002
        PROCESS_TERMINATE = 0x0001
        exe_name = (Path(sys.executable).name if getattr(sys, "frozen", False) else "python.exe").lower()
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap in (0, -1):
            return False
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            terminated = False
            ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                if entry.szExeFile.lower() == exe_name and entry.th32ProcessID != os.getpid():
                    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, entry.th32ProcessID)
                    if handle:
                        kernel32.TerminateProcess(handle, 0)
                        kernel32.CloseHandle(handle)
                        terminated = True
                ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
            return terminated
        finally:
            kernel32.CloseHandle(snap)
    except Exception:
        return False


def set_kill_children_on_exit():
    """让 WebView2 子进程随主进程一起退出，避免后台残留一瞬间。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_void_p),
                ("MaximumWorkingSetSize", ctypes.c_void_p),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_void_p),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_void_p),
                ("JobMemoryLimit", ctypes.c_void_p),
                ("PeakProcessMemoryUsed", ctypes.c_void_p),
                ("PeakJobMemoryUsed", ctypes.c_void_p),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return
        kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())

        global _JOB_HANDLE
        _JOB_HANDLE = job
    except Exception:
        pass


def restore_existing_window(retries: int = 0, delay: float = 0.2, require_server: bool = True) -> bool:
    if os.name != "nt":
        return False
    import time
    try:
        from window import _find_hwnd, _restore_main_window, _window_has_webview
        for attempt in range(max(0, retries) + 1):
            hwnd = _find_hwnd(APP_NAME)
            server_ok = local_server_healthy() if hwnd and require_server else True
            if hwnd and server_ok:
                # 只恢复真正挂载了 WebView2 渲染的窗口，避免把空壳窗口恢复成白屏。
                if _window_has_webview(hwnd, wait=3.0):
                    _restore_main_window(hwnd)
                    return True
                log_event("restore_empty_window_no_webview", {"hwnd": int(hwnd) if hwnd else None})
                return False
            if attempt < retries:
                time.sleep(delay)
    except Exception:
        pass
    return False


def _show_port_conflict_dialog(error_text: str = ""):
    """端口被占用时给出明确提示，而不是让应用静默闪退。"""
    try:
        import ctypes
        detail = f"\n\n{error_text}" if error_text else ""
        msg = (
            f"{APP_NAME} 无法启动：本机端口 {PORT} 已被其他程序占用。"
            f"\n请关闭占用该端口的程序（或重启后重试），再重新打开应用。{detail}"
        )
        ctypes.windll.user32.MessageBoxW(None, msg, APP_NAME, 0x00000010)  # MB_ICONERROR
    except Exception:
        pass


def main():
    set_windows_app_id()
    background_start = any(arg in ("--background", "--hidden", "--tray") for arg in sys.argv[1:])
    log_event("startup_begin", {"background": background_start, "argv": sys.argv[1:]})
    if not acquire_single_instance_lock():
        log_event("startup_existing_instance", {"background": background_start})
        if not background_start:
            if restore_existing_window(retries=10, delay=0.1, require_server=False):
                return
            # 已有实例但窗口不可用（WebView 未加载/无窗口）。先判断采集是否真的
            # 停止：窗口空壳但采集正常时不应接管（杀实例会中断采集、丢时段数据），
            # 只提示并退出；只有采集已停摆（stale）且不在冷却期才接管。
            if not _existing_instance_is_stale():
                log_event("startup_existing_window_empty_but_collecting")
                return
            if _takeover_in_cooldown():
                log_event("startup_takeover_cooldown", {"foreground": True})
                return
            log_event("startup_takeover_stale_instance")
            _mark_takeover_attempt()
            evicted = request_existing_instance_exit() or _force_terminate_other_instance()
            # 旧实例优雅退出（tracker/server stop）可能耗时 10s+，等待时间要给足。
            if evicted and _retry_acquire_lock(timeout=25.0):
                pass
            else:
                return
        else:
            # 后台自启：若已有实例已停止采集（paused/locked/卡死/长时间无新
            # 数据），接管而不是静默退出，避免采集长期停摆（如 8/3 数据缺失）。
            if not _existing_instance_is_stale():
                return
            if _takeover_in_cooldown():
                log_event("startup_takeover_cooldown", {"background": True})
                return
            log_event("startup_takeover_stale_instance")
            _mark_takeover_attempt()
            evicted = request_existing_instance_exit() or _force_terminate_other_instance()
            if evicted and _retry_acquire_lock(timeout=25.0):
                pass
            else:
                return
    if not background_start and restore_existing_window(retries=0, delay=0.05):
        log_event("startup_existing_window_restored")
        return
    # 本实例已成为唯一实例，才写入内部关机令牌，避免覆盖旧实例的令牌。
    _write_shutdown_token()
    print(f"[{APP_NAME}] 启动中...")

    db = DB(DB_PATH)
    log_event("startup_db_opened", {"db": str(DB_PATH)})

    tracker = None
    tracker_lock = threading.Lock()
    server = None

    class TrackerFacade:
        """托盘使用的轻量代理，避免托盘启动时顺手拉起全局监听器。"""

        @property
        def stats(self):
            active = tracker
            if active:
                return active.stats
            try:
                return db.today_stats()
            except Exception:
                return {"keystrokes": 0, "mouse_events": 0, "total_seconds": 0}

        def flush(self):
            active = tracker
            if active:
                active.flush()

    def ensure_tracker_started():
        nonlocal tracker
        if tracker:
            return tracker
        with tracker_lock:
            if tracker:
                return tracker
            from monitor import Win32CollectorAdapter
            next_tracker = Win32CollectorAdapter(db)
            next_tracker.start()
            tracker = next_tracker
            return tracker

    def shutdown(*_):
        print(f"\n[{APP_NAME}] 关闭中...")
        log_event("shutdown_begin")
        if tracker:
            tracker.stop()
        if server:
            server.stop()
        try:
            db.checkpoint()
            db.close()
        except Exception as e:
            log_exception("shutdown_db_close_failed", e)
        print(f"[{APP_NAME}] 已退出")
        log_event("shutdown_finished")
        sys.exit(0)

    def exit_for_update():
        print(f"\n[{APP_NAME}] 准备安装更新...")
        log_event("update_shutdown_begin")
        # 快速退出路径：直接终止进程。SQLite WAL 保证已提交事务持久（下次打开
        # 自动重放），避免 tracker flush / DB checkpoint 因锁等待阻塞接管或更新。
        log_event("update_shutdown_exit")
        os._exit(0)

    log_event("startup_server_binding", {"port": PORT})
    try:
        server = StatsServer(db, PORT, shutdown_callback=exit_for_update, shutdown_token=_SHUTDOWN_TOKEN)
    except OSError as e:
        # 已有实例（锁被占）→ 恢复其窗口；否则视为端口被其他程序占用。
        if background_start or restore_existing_window(retries=10, delay=0.2):
            return
        log_event("startup_port_conflict", {"port": PORT, "error": str(e)})
        _show_port_conflict_dialog(str(e))
        return

    server.start()
    server.service.email_report_manager().start()

    def start_tracker_async():
        import time
        # 全局键鼠监听和前台窗口探测会让 WebView 启动期抖一下。
        # 可见启动时等窗口、标题栏、托盘都稳定后再拉起记录器。
        time.sleep(0.8 if background_start else 6.0)
        try:
            ensure_tracker_started()
        except Exception as e:
            print(f"[{APP_NAME}] 记录器启动失败: {e}")
            log_exception("tracker_start_failed", e)

    threading.Thread(target=start_tracker_async, daemon=True).start()

    def import_legacy_async():
        import time
        time.sleep(1.2 if background_start else 2.5)
        if os.getenv("INTERACTION_RHYTHM_DISABLE_LEGACY_IMPORT") == "1":
            log_event("legacy_import_disabled")
            return
        try:
            candidates = legacy_database_candidates()
            results = db.import_legacy_databases(candidates)
            imported = [
                item for item in results
                if not item.get("skipped") and not item.get("error")
                and sum(int(item.get(key) or 0) for key in (
                    "keystrokes", "mouse_events", "app_usage", "app_paths", "app_interactions", "insight_history"
                )) > 0
            ]
            if imported:
                log_event("legacy_data_imported", {"sources": len(imported), "results": imported})
                db.checkpoint()
        except Exception as e:
            print(f"[{APP_NAME}] 旧数据继承跳过: {e}")
            log_exception("legacy_import_skipped", e)

    threading.Thread(target=import_legacy_async, daemon=True).start()

    def cleanup_async():
        import time
        time.sleep(12)
        try:
            deleted = db.cleanup(DB_RETENTION_DAYS, HOURLY_RETENTION_DAYS)
            db.checkpoint()
            print(f"[{APP_NAME}] 历史数据清理完成，删除 {deleted} 行")
        except Exception as e:
            print(f"[{APP_NAME}] 历史数据清理跳过: {e}")
            log_exception("cleanup_skipped", e)

    threading.Thread(target=cleanup_async, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[{APP_NAME}] 已启动  http://127.0.0.1:{PORT}")
    log_event("server_started", {"port": PORT})

    # 启动系统托盘（后台加载，避免拖慢窗口显示）
    def start_tray_async():
        try:
            import time
            time.sleep(0.4 if background_start else 3.0)
            tray_error = data_path() / "tray-error.log"
            if tray_error.exists():
                tray_error.unlink()
            from tray import create_tray
            def tray_shutdown():
                try:
                    if server:
                        server.stop()
                except Exception:
                    pass

            _, tray_start = create_tray(TrackerFacade(), PORT, shutdown_callback=tray_shutdown)
            tray_start()
        except Exception as e:
            print(f"[{APP_NAME}] 托盘启动失败: {e}")
            log_exception("tray_start_failed", e)
            try:
                import traceback
                (data_path() / "tray-error.log").write_text(traceback.format_exc(), encoding="utf-8")
            except Exception:
                pass

    tray_thread = threading.Thread(target=start_tray_async, daemon=True)
    tray_thread.start()
    print(f"[{APP_NAME}] 系统托盘加载中")

    # 生成应用图标（用于任务栏、窗口和原生标题栏）
    frozen = getattr(sys, "frozen", False)
    title_icon_path = data_path() / "interaction-rhythm-title.png"
    if frozen:
        icon_candidates = [
            Path(sys.executable).with_suffix(".ico"),
            Path(sys.executable).parent / "InteractionRhythm.ico",
            data_path() / f"{APP_NAME}.ico",
        ]
        icon_path = next((path for path in icon_candidates if path.exists() and path.stat().st_size >= 1024), None)
        if not title_icon_path.exists():
            title_icon_path = None
    else:
        bundled_icon = resource_path("app.ico")
        icon_path = bundled_icon if bundled_icon.exists() else data_path() / f"{APP_NAME}.ico"
        if not icon_path.exists() or icon_path.stat().st_size < 1024:
            try:
                from tray import make_ico
                make_ico(str(icon_path))
            except Exception as e:
                print(f"[{APP_NAME}] 图标生成失败: {e}")
                log_exception("icon_generate_failed", e)
                icon_path = None
        if not title_icon_path.exists():
            try:
                from tray import make_icon
                make_icon(96).save(title_icon_path, "PNG")
            except Exception as e:
                print(f"[{APP_NAME}] 标题栏图标生成失败: {e}")
                log_exception("title_icon_generate_failed", e)
                title_icon_path = None

    # 主线程：原生桌面窗口（阻塞直到窗口关闭）
    window_started = False
    try:
        from window import create_window
        _, window_start = create_window(
            PORT,
            icon_path=str(icon_path) if icon_path else None,
            title_icon_path=str(title_icon_path) if title_icon_path else None,
            start_hidden=background_start,
        )
        log_event("window_start_begin", {"background": background_start})
        window_start()  # 阻塞主线程
        window_started = True
    except Exception as e:
        print(f"[{APP_NAME}] 窗口启动失败: {e}")
        log_exception("window_start_failed", e)

    if window_started or not background_start:
        # 窗口正常关闭后清理；前台实例窗口失败也退出（用户需要看到错误反馈）。
        shutdown()
    else:
        # 后台/托盘实例窗口失败（如 WebView2 初始化失败）时，采集与本地服务不能停：
        # 窗口只是展示层，tracker/server/托盘应继续运行，避免时段数据再次丢失。
        log_event("background_window_failed_keep_running")
        _keep_running_after_window_failure(server, tracker, db)


def _keep_running_after_window_failure(server, tracker, db):
    """后台实例窗口失败时保活主线程：采集/本地服务/托盘继续运行。

    窗口只是展示层；即使 WebView2 渲染无法创建，tracker 的键鼠与前台采集、
    本地 HTTP 服务与系统托盘仍应保持可用，直到进程被信号/接管/托盘退出。
    """
    log_event("background_keep_running_start")
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            if tracker:
                tracker.stop()
        except Exception:
            pass
        try:
            if server:
                server.stop()
        except Exception:
            pass
        try:
            db.checkpoint()
            db.close()
        except Exception:
            pass
        log_event("background_keep_running_stopped")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        if getattr(sys, "frozen", False):
            log_path = Path(sys.executable).parent / "startup-error.log"
        else:
            log_path = Path(__file__).parent / "startup-error.log"
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        log_exception("startup_crashed", sys.exc_info()[1])
        raise
