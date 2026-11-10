"""键盘响应、鼠标响应与前台窗口使用时间监控。"""
import ctypes
import ctypes.wintypes as wintypes
import ntpath
import threading
import time
from datetime import datetime

from config import POLL_INTERVAL, BATCH_INTERVAL
from interaction_rhythm.collector import CollectorAdapter
from interaction_rhythm.observations import (
    ForegroundSnapshot,
    KeyboardPressState,
    ObservationBuffer,
    local_now,
)
from interaction_rhythm.win32_input_protocol import is_benchmark_input
from interaction_rhythm.windows_system_events import (
    Win32SystemEventSource,
    WindowsSystemEvent,
)

try:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    _HAS_WIN32 = True
except Exception:
    _HAS_WIN32 = False


ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
LowLevelProc = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
WinEventProc = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.HWND,
    wintypes.LONG,
    wintypes.LONG,
    wintypes.DWORD,
    wintypes.DWORD,
)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_XBUTTONDOWN = 0x020B
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
DESKTOP_SWITCHDESKTOP = 0x0100
SLEEP_GAP_SECONDS = 15.0


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ULONG_PTR),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


if _HAS_WIN32:
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelProc, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HANDLE
    user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.SetWinEventHook.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HMODULE,
        WinEventProc,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    user32.SetWinEventHook.restype = wintypes.HANDLE
    user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
    user32.UnhookWinEvent.restype = wintypes.BOOL
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.SwitchDesktop.argtypes = [wintypes.HANDLE]
    user32.SwitchDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL

    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
    kernel32.Process32NextW.restype = wintypes.BOOL


def _query_process_path(pid: int) -> str:
    if not _HAS_WIN32 or not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32767)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    except Exception:
        return ""
    finally:
        kernel32.CloseHandle(handle)
    return ""


def collect_running_app_paths() -> dict[str, str]:
    """扫描进程路径，用原生 API 避免拉起沉重的进程库。"""
    if not _HAS_WIN32:
        return {}
    paths: dict[str, str] = {}
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return paths
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile
            path = _query_process_path(entry.th32ProcessID)
            if name and path:
                paths[name] = path
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return paths


class _LowLevelHook:
    def __init__(self, hook_id: int, messages: set[int], on_event):
        self._hook_id = hook_id
        self._messages = messages
        self._on_event = on_event
        self._callback = None
        self._hook = None
        self._thread = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._stop = threading.Event()

    def start(self) -> bool:
        if not _HAS_WIN32:
            return False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(1.2)
        return bool(self._hook)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()

        def _proc(n_code, w_param, l_param):
            try:
                if n_code == HC_ACTION and int(w_param) in self._messages:
                    self._on_event(int(w_param), l_param)
            except Exception:
                pass
            return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

        self._callback = LowLevelProc(_proc)
        hmod = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(self._hook_id, self._callback, hmod, 0)
        if not self._hook:
            self._hook = user32.SetWindowsHookExW(self._hook_id, self._callback, None, 0)
        self._ready.set()
        if not self._hook:
            return

        msg = wintypes.MSG()
        while not self._stop.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def stop(self):
        self._stop.set()
        if _HAS_WIN32 and self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.5)

    @property
    def healthy(self) -> bool:
        return bool(self._hook and self._thread and self._thread.is_alive())


class _ForegroundEventHook:
    def __init__(self, on_change):
        self._on_change = on_change
        self._callback = None
        self._hook = None
        self._thread = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._event_count = 0
        self._callback_error_count = 0
        self._last_callback_error = ""

    def start(self) -> bool:
        if not _HAS_WIN32:
            return False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(1.2)
        return bool(self._hook)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()

        def _proc(_hook, _event, hwnd, _object_id, _child_id, _thread, _event_time):
            self._event_count += 1
            try:
                self._on_change(hwnd)
            except Exception as exc:
                self._callback_error_count += 1
                self._last_callback_error = type(exc).__name__

        self._callback = WinEventProc(_proc)
        self._hook = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            None,
            self._callback,
            0,
            0,
            WINEVENT_OUTOFCONTEXT,
        )
        self._ready.set()
        if not self._hook:
            return

        msg = wintypes.MSG()
        while not self._stop.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook:
            user32.UnhookWinEvent(self._hook)
            self._hook = None

    def stop(self):
        self._stop.set()
        if _HAS_WIN32 and self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.5)

    @property
    def healthy(self) -> bool:
        return bool(self._hook and self._thread and self._thread.is_alive())

    @property
    def stats(self) -> dict:
        return {
            "event_count": self._event_count,
            "callback_error_count": self._callback_error_count,
            "last_callback_error": self._last_callback_error,
            "healthy": self.healthy,
        }


# ── 键盘计数器 ──────────────────────────────────────────────

class KeystrokeMonitor:
    """全局键盘监听；只把去重后的按下事实写入共享缓冲。"""

    def __init__(self, buffer: ObservationBuffer, current_app, state_recorder=None):
        self._buffer = buffer
        self._current_app = current_app
        self._state_recorder = state_recorder
        self._pressed = KeyboardPressState()
        self._stop = threading.Event()
        self._hook = None
        self._ignored_benchmark_count = 0

    def _on_key(self, message, l_param):
        info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if is_benchmark_input(info.dwExtraInfo):
            self._ignored_benchmark_count += 1
            return
        if message in {WM_KEYUP, WM_SYSKEYUP}:
            self._pressed.on_key_up(info.vkCode, info.scanCode)
            return
        if not self._pressed.on_key_down(info.vkCode, info.scanCode):
            return
        app, _title, path = self._current_app()
        self._buffer.record_keyboard(
            local_now(),
            ForegroundSnapshot(app, path),
            "running" if self.healthy else "degraded",
        )

    def reconcile(self):
        """Release pending keys after a health/focus boundary.

        A lost key-up would otherwise leave the key in the dedup set and
        silently drop every later physical press until the collector stops.
        """
        self._pressed.reset()

    def start(self):
        if self._stop.is_set():
            return
        self._hook = _LowLevelHook(
            WH_KEYBOARD_LL,
            {WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP},
            self._on_key,
        )
        started = self._hook.start()
        if self._state_recorder:
            self._state_recorder("keyboard_hook", "running" if started else "degraded")

    def stop(self):
        self._stop.set()
        if self._hook:
            self._hook.stop()
        self._pressed.reset()
        if self._state_recorder:
            self._state_recorder("keyboard_hook", "paused")

    def flush(self):
        return None

    @property
    def healthy(self) -> bool:
        return bool(self._hook and self._hook.healthy)

    @property
    def ignored_benchmark_count(self) -> int:
        return self._ignored_benchmark_count


# ── 鼠标响应计数器 ──────────────────────────────────────────

class MouseMonitor:
    """全局鼠标监听，分别记录按键与滚轮，不记录移动或坐标。"""

    def __init__(self, buffer: ObservationBuffer, current_app, state_recorder=None):
        self._buffer = buffer
        self._current_app = current_app
        self._state_recorder = state_recorder
        self._stop = threading.Event()
        self._hook = None
        self._ignored_benchmark_count = 0

    def _on_mouse(self, message, l_param):
        info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if is_benchmark_input(info.dwExtraInfo):
            self._ignored_benchmark_count += 1
            return
        app, _title, path = self._current_app()
        foreground = ForegroundSnapshot(app, path)
        state = "running" if self.healthy else "degraded"
        if message in {WM_MOUSEWHEEL, WM_MOUSEHWHEEL}:
            self._buffer.record_scroll(local_now(), foreground, state)
        else:
            self._buffer.record_mouse_button(local_now(), foreground, state)

    def start(self):
        if self._stop.is_set():
            return
        self._hook = _LowLevelHook(
            WH_MOUSE_LL,
            {WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN, WM_MOUSEWHEEL, WM_MOUSEHWHEEL},
            self._on_mouse,
        )
        started = self._hook.start()
        if self._state_recorder:
            self._state_recorder("mouse_hook", "running" if started else "degraded")

    def stop(self):
        self._stop.set()
        if self._hook:
            self._hook.stop()
        if self._state_recorder:
            self._state_recorder("mouse_hook", "paused")

    def flush(self):
        return None

    @property
    def healthy(self) -> bool:
        return bool(self._hook and self._hook.healthy)

    @property
    def ignored_benchmark_count(self) -> int:
        return self._ignored_benchmark_count


# ── 前台窗口监控 ────────────────────────────────────────────

def _get_window_process(hwnd) -> tuple[str, str, str]:
    """Resolve a WinEvent hwnd without reading title or content metadata."""
    if not _HAS_WIN32 or not hwnd:
        return ("unknown", "", "")
    try:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        path = _query_process_path(pid.value)
        name = ntpath.basename(path) if path else "unknown"
        return (name, "", path)
    except Exception:
        return ("unknown", "", "")


def _get_foreground() -> tuple[str, str, str]:
    """返回 (进程名, 空标题, 可执行文件路径)，不读取内容型窗口标题。"""
    if not _HAS_WIN32:
        return ("unknown", "", "")
    return _get_window_process(user32.GetForegroundWindow())


class WindowMonitor:
    """WinEvent 驱动的前台观察器，低频轮询只负责 watchdog。"""

    def __init__(self, db):
        self._db = db
        self._cur_app = None
        self._cur_title = ""
        self._cur_path = ""
        self._cur_start = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._event_hook = None
        self._watchdog_thread = None
        self._event_observation_counts: dict[str, int] = {}
        self._event_stats_lock = threading.Lock()

    def _transition(self, app: str, path: str, force_boundary: bool = False):
        app = (app or "unknown").strip() or "unknown"
        path = path or ""
        now = local_now().isoformat(timespec="microseconds")
        with self._lock:
            changed = app != self._cur_app or path != self._cur_path
            if not changed and not force_boundary:
                return
            prev_app = self._cur_app
            prev_path = self._cur_path
            prev_start = self._cur_start
            self._cur_app = app
            self._cur_title = ""
            self._cur_path = path
            self._cur_start = now
        if prev_app and prev_start and now > prev_start:
            self._db.add_session(
                prev_app,
                "",
                prev_start,
                now,
                prev_path,
                health_state="unknown" if prev_app == "unknown" else "running",
            )

    def _observe(self, force_boundary: bool = False, hwnd=None):
        app, _title, path = (
            _get_window_process(hwnd) if hwnd else _get_foreground()
        )
        if hwnd:
            with self._event_stats_lock:
                self._event_observation_counts[app] = (
                    self._event_observation_counts.get(app, 0) + 1
                )
        self._transition(app, path, force_boundary)

    def _loop(self):
        watchdog_interval = max(5.0, POLL_INTERVAL * 10)
        heartbeat_at = time.monotonic()
        while not self._stop.wait(watchdog_interval):
            self._observe()
            if time.monotonic() - heartbeat_at >= 30:
                self._db.record_collector_state(
                    "foreground_watcher",
                    "running" if self.healthy else "degraded",
                )
                heartbeat_at = time.monotonic()

    def start(self):
        app, _title, path = _get_foreground()
        with self._lock:
            self._cur_app = app
            self._cur_title = ""
            self._cur_path = path
            self._cur_start = local_now().isoformat(timespec="microseconds")
        self._event_hook = _ForegroundEventHook(
            lambda hwnd: self._observe(hwnd=hwnd)
        )
        started = self._event_hook.start()
        self._db.record_collector_state(
            "foreground_watcher",
            "running" if started else "degraded",
        )
        self._watchdog_thread = threading.Thread(target=self._loop, daemon=True)
        self._watchdog_thread.start()

    def current_app(self) -> tuple[str, str, str]:
        with self._lock:
            return (self._cur_app or "unknown", self._cur_title, self._cur_path)

    def stop(self):
        self._stop.set()
        if self._event_hook:
            self._event_hook.stop()
        if self._watchdog_thread and self._watchdog_thread is not threading.current_thread():
            self._watchdog_thread.join(timeout=1.5)
        with self._lock:
            app = self._cur_app
            path = self._cur_path
            start = self._cur_start
        if app and start:
            now = local_now().isoformat(timespec="microseconds")
            if now > start:
                self._db.add_session(
                    app,
                    "",
                    start,
                    now,
                    path,
                    health_state="unknown" if app == "unknown" else "running",
                )
        self._db.record_collector_state("foreground_watcher", "paused")

    def flush(self):
        self._observe(force_boundary=True)

    def pause_observation(self, at: datetime):
        """Close the current foreground interval without opening a new one."""
        ended_at = at.isoformat(timespec="microseconds")
        with self._lock:
            app = self._cur_app
            path = self._cur_path
            started_at = self._cur_start
            self._cur_start = None
        if app and started_at and ended_at > started_at:
            self._db.add_session(
                app,
                "",
                started_at,
                ended_at,
                path,
                health_state="unknown" if app == "unknown" else "running",
            )

    def resume_observation(self, at: datetime):
        """Start a fresh foreground interval after a non-observable boundary."""
        app, _title, path = _get_foreground()
        with self._lock:
            self._cur_app = (app or "unknown").strip() or "unknown"
            self._cur_title = ""
            self._cur_path = path or ""
            self._cur_start = at.isoformat(timespec="microseconds")

    @property
    def healthy(self) -> bool:
        return bool(self._event_hook and self._event_hook.healthy)

    @property
    def event_stats(self) -> dict:
        if not self._event_hook:
            return {
                "event_count": 0,
                "callback_error_count": 0,
                "last_callback_error": "",
                "healthy": False,
            }
        stats = self._event_hook.stats
        with self._event_stats_lock:
            stats["observed_process_counts"] = dict(
                sorted(self._event_observation_counts.items())
            )
        return stats


class ObservationWriter:
    def __init__(self, db, buffer: ObservationBuffer):
        self._db = db
        self._buffer = buffer
        self._stop = threading.Event()
        self._healthy = True
        self._thread = None

    def start(self):
        self._db.record_collector_state("database_writer", "running")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(BATCH_INTERVAL):
            self.flush()

    def flush(self, force: bool = False):
        buckets = self._buffer.drain(local_now(), force=force)
        if not buckets:
            return
        try:
            self._db.add_interaction_buckets(buckets)
            self._healthy = True
        except Exception as exc:
            self._healthy = False
            for bucket in buckets:
                self._buffer.requeue(bucket)
            try:
                self._db.record_collector_state("database_writer", "degraded", detail=str(exc)[:240])
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.5)
        self.flush(force=True)
        self._db.record_collector_state("database_writer", "paused")

    @property
    def healthy(self) -> bool:
        return self._healthy


def _desktop_state() -> str:
    if not _HAS_WIN32:
        return "unknown"
    desktop = None
    try:
        desktop = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not desktop:
            return "locked"
        return "running" if user32.SwitchDesktop(desktop) else "locked"
    except Exception:
        return "unknown"
    finally:
        if desktop:
            try:
                user32.CloseDesktop(desktop)
            except Exception:
                pass


class SystemStateMonitor:
    """记录责任区间，并用 monotonic 间隙识别休眠恢复。"""

    def __init__(
        self,
        db,
        health_provider,
        event_source_factory=Win32SystemEventSource,
        pause_observation=None,
        resume_observation=None,
        on_state_change=None,
    ):
        self._db = db
        self._health_provider = health_provider
        self._on_state_change = on_state_change
        self._stop = threading.Event()
        self._state = None
        self._segment_start = None
        self._last_monotonic = None
        self._last_wall = None
        self._thread = None
        self._lock = threading.RLock()
        self._explicit_suspend = False
        self._event_counts: dict[str, int] = {}
        self._deduplicated_event_counts: dict[str, int] = {}
        self._boundary_error_count = 0
        self._pause_observation = pause_observation or (lambda _at: None)
        self._resume_observation = resume_observation or (lambda _at: None)
        self._event_source = event_source_factory(self._on_windows_event)

    def _notify_boundary(self, callback, at: datetime) -> None:
        try:
            callback(at)
        except Exception as exc:
            self._boundary_error_count += 1
            try:
                self._db.record_collector_state(
                    "system_events",
                    "degraded",
                    at,
                    f"boundary_flush_failed:{type(exc).__name__}",
                )
            except Exception:
                pass

    def _resolved_state(self) -> str:
        desktop = _desktop_state()
        if desktop != "running":
            return desktop
        return "running" if self._health_provider() else "degraded"

    def _initialize(
        self,
        now: datetime,
        monotonic_now: float,
        resolved_state: str | None = None,
    ) -> None:
        with self._lock:
            self._state = resolved_state or self._resolved_state()
            self._segment_start = now
            self._last_wall = now
            self._last_monotonic = monotonic_now
            self._explicit_suspend = False
            self._db.record_collector_state("system", self._state, now)

    def start(self):
        self._initialize(local_now(), time.monotonic())
        events_started = self._event_source.start()
        self._db.record_collector_state(
            "system_events",
            "running" if events_started else "degraded",
            detail="" if events_started else self._event_source.stats.get("last_error", ""),
        )
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _close_segment(self, end: datetime):
        if self._state and self._segment_start and end > self._segment_start:
            self._db.add_observation_segment(
                "collector", self._state, self._segment_start, end
            )

    def _transition(self, state: str, now: datetime, detail: str = ""):
        if state == self._state:
            return
        self._close_segment(now)
        self._state = state
        self._segment_start = now
        self._db.record_collector_state("system", state, now, detail)
        if self._on_state_change:
            try:
                self._on_state_change(state, detail)
            except Exception:
                pass

    def _count_event(self, kind: str, deduplicated: bool = False) -> None:
        target = (
            self._deduplicated_event_counts
            if deduplicated
            else self._event_counts
        )
        target[kind] = target.get(kind, 0) + 1

    def _on_windows_event(self, event: WindowsSystemEvent) -> None:
        self._handle_windows_event(
            event.kind,
            local_now(),
            time.monotonic(),
        )

    def _handle_windows_event(
        self,
        kind: str,
        now: datetime,
        monotonic_now: float,
        resolved_state: str | None = None,
    ) -> None:
        with self._lock:
            self._count_event(kind)
            if kind == "session_lock":
                if self._state == "locked":
                    self._count_event(kind, deduplicated=True)
                else:
                    self._notify_boundary(self._pause_observation, now)
                    self._transition("locked", now, "wts_session_lock")
            elif kind == "session_unlock":
                next_state = resolved_state or self._resolved_state()
                if self._state != "locked":
                    self._count_event(kind, deduplicated=True)
                else:
                    self._transition(next_state, now, "wts_session_unlock")
                    self._notify_boundary(self._resume_observation, now)
            elif kind == "suspend":
                if self._state == "sleeping" or self._explicit_suspend:
                    self._count_event(kind, deduplicated=True)
                else:
                    self._notify_boundary(self._pause_observation, now)
                    self._transition("sleeping", now, "power_suspend")
                    self._explicit_suspend = True
            elif kind in {"resume_automatic", "resume_user"}:
                next_state = resolved_state or self._resolved_state()
                if self._state != "sleeping" and not self._explicit_suspend:
                    self._count_event(kind, deduplicated=True)
                else:
                    self._transition(next_state, now, f"power_{kind}")
                    self._explicit_suspend = False
                    self._notify_boundary(self._resume_observation, now)
            elif kind == "session_end":
                self._notify_boundary(self._pause_observation, now)
                self._transition("paused", now, "windows_session_end")
            elif kind == "session_disconnect":
                if self._state in {"locked", "sleeping", "paused", "unknown"}:
                    self._count_event(kind, deduplicated=True)
                else:
                    self._notify_boundary(self._pause_observation, now)
                    self._transition("unknown", now, "wts_session_disconnect")
            elif kind == "session_connect":
                next_state = resolved_state or self._resolved_state()
                if self._state != "unknown":
                    self._count_event(kind, deduplicated=True)
                else:
                    self._transition(next_state, now, "wts_session_connect")
                    self._notify_boundary(self._resume_observation, now)
            elif kind == "session_logoff":
                self._notify_boundary(self._pause_observation, now)
                self._transition("paused", now, "wts_session_logoff")
            else:
                raise ValueError(f"unsupported Windows system event: {kind}")
            self._last_wall = now
            self._last_monotonic = monotonic_now

    def _tick(
        self,
        now: datetime,
        monotonic_now: float,
        resolved_state: str | None = None,
    ) -> None:
        with self._lock:
            next_state = resolved_state or self._resolved_state()
            gap = monotonic_now - (
                self._last_monotonic
                if self._last_monotonic is not None
                else monotonic_now
            )
            if self._explicit_suspend and gap <= SLEEP_GAP_SECONDS:
                return
            if gap > SLEEP_GAP_SECONDS and self._last_wall:
                if self._state == "sleeping":
                    self._close_segment(now)
                else:
                    self._notify_boundary(self._pause_observation, self._last_wall)
                    self._close_segment(self._last_wall)
                    self._db.add_observation_segment(
                        "collector",
                        "unknown",
                        self._last_wall,
                        now,
                        "unobserved_monotonic_gap",
                    )
                self._state = next_state
                self._segment_start = now
                self._db.record_collector_state(
                    "system", self._state, now, "resume_after_gap"
                )
                self._explicit_suspend = False
                if self._on_state_change:
                    try:
                        self._on_state_change(self._state, "resume_after_gap")
                    except Exception:
                        pass
                self._notify_boundary(self._resume_observation, now)
            else:
                previous_state = self._state
                if next_state != previous_state and next_state in {"locked", "sleeping", "paused", "unknown"}:
                    self._notify_boundary(self._pause_observation, now)
                self._transition(next_state, now)
                if (
                    next_state != previous_state
                    and previous_state in {"locked", "sleeping", "paused", "unknown"}
                    and next_state not in {"locked", "sleeping", "paused", "unknown"}
                ):
                    self._notify_boundary(self._resume_observation, now)
                if (
                    next_state == self._state
                    and self._segment_start
                    and (now - self._segment_start).total_seconds() >= 30
                ):
                    self._close_segment(now)
                    self._segment_start = now
                    self._db.record_collector_state(
                        "system", self._state, now, "heartbeat"
                    )
            self._last_monotonic = monotonic_now
            self._last_wall = now

    def _finish(self, now: datetime) -> None:
        with self._lock:
            self._close_segment(now)
            self._segment_start = None

    def _loop(self):
        while not self._stop.wait(1.0):
            self._tick(local_now(), time.monotonic())

    def stop(self):
        self._stop.set()
        events_stopped = self._event_source.stop()
        self._db.record_collector_state(
            "system_events",
            "paused" if events_stopped else "degraded",
            detail="" if events_stopped else "event_thread_stop_timeout",
        )
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.5)
        now = local_now()
        self._finish(now)
        self._db.record_collector_state("system", "paused", now)

    @property
    def event_stats(self) -> dict:
        with self._lock:
            return {
                "handled_event_counts": dict(sorted(self._event_counts.items())),
                "deduplicated_event_counts": dict(
                    sorted(self._deduplicated_event_counts.items())
                ),
                "explicit_suspend": self._explicit_suspend,
                "boundary_error_count": self._boundary_error_count,
                "transport": self._event_source.stats,
            }


# ── 总监控器 ────────────────────────────────────────────────

class Win32CollectorAdapter(CollectorAdapter):
    """In-process Windows adapter; emits only normalized aggregate facts."""

    def __init__(self, db):
        self._db = db
        self._buffer = ObservationBuffer()
        self._win = WindowMonitor(db)
        self._writer = ObservationWriter(db, self._buffer)
        self._keys = KeystrokeMonitor(
            self._buffer, self._win.current_app, db.record_collector_state
        )
        self._mouse = MouseMonitor(
            self._buffer, self._win.current_app, db.record_collector_state
        )
        self._system = SystemStateMonitor(
            db,
            self._collection_healthy,
            pause_observation=self._pause_observation,
            resume_observation=self._resume_observation,
            on_state_change=self._on_collector_state_change,
        )
        self._system_started = False
        self._stop = threading.Event()
        self._startup_threads = []

    def start(self):
        self._win.start()
        self._writer.start()
        self._startup_threads = [
            threading.Thread(target=self._start_input_monitors, daemon=True),
            threading.Thread(target=self._remember_paths, daemon=True),
        ]
        for thread in self._startup_threads:
            thread.start()

    def _start_input_monitors(self):
        if self._stop.wait(0.25):
            return
        self._keys.start()
        if self._stop.wait(0.75):
            return
        self._mouse.start()
        if not self._stop.is_set():
            self._system.start()
            self._system_started = True

    def _collection_healthy(self) -> bool:
        return (
            self._win.healthy
            and self._keys.healthy
            and self._mouse.healthy
            and self._writer.healthy
        )

    def _pause_observation(self, at: datetime) -> None:
        self._keys.reconcile()
        self._writer.flush(force=True)
        self._win.pause_observation(at)
        self._db.checkpoint()

    def _resume_observation(self, at: datetime) -> None:
        self._win.resume_observation(at)

    def _on_collector_state_change(self, state: str, _detail: str = "") -> None:
        if state in {"degraded", "unknown", "locked", "sleeping", "paused"}:
            self._keys.reconcile()

    def _remember_paths(self):
        if self._stop.wait(20):
            return
        try:
            self._db.remember_app_paths(collect_running_app_paths())
        except Exception:
            pass

    def stop(self):
        self._stop.set()
        for thread in self._startup_threads:
            if thread is not threading.current_thread():
                thread.join(timeout=1.5)
        if self._system_started:
            self._system.stop()
        self._keys.stop()
        self._mouse.stop()
        self._writer.stop()
        self._win.stop()

    def flush(self):
        self._writer.flush(force=True)
        self._win.flush()

    def checkpoint(self):
        self._db.checkpoint()

    @property
    def stats(self):
        return self._db.today_stats()


# Keep the historical import stable while callers migrate to the explicit adapter name.
Tracker = Win32CollectorAdapter
