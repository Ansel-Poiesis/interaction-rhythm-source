"""Hidden Win32 window transport for session and power notifications."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from dataclasses import asdict, dataclass
import os
import threading


WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1

PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012

WTS_SESSION_LOCK = 0x0007
WTS_SESSION_UNLOCK = 0x0008
WTS_SESSION_DISCONNECT = 0x0001
WTS_SESSION_CONNECT = 0x0002
WTS_SESSION_REMOTE_CONNECT = 0x0003
WTS_SESSION_REMOTE_DISCONNECT = 0x0004
WTS_SESSION_LOGOFF = 0x0005
WTS_SESSION_LOGON = 0x0006
NOTIFY_FOR_THIS_SESSION = 0


@dataclass(frozen=True)
class WindowsSystemEvent:
    source: str
    kind: str
    code: int

    def as_dict(self) -> dict:
        return asdict(self)


def normalize_system_event(message: int, code: int) -> WindowsSystemEvent | None:
    if message == WM_ENDSESSION and code:
        return WindowsSystemEvent("session", "session_end", int(code))
    if message == WM_WTSSESSION_CHANGE:
        if code == WTS_SESSION_LOCK:
            return WindowsSystemEvent("wts", "session_lock", int(code))
        if code == WTS_SESSION_UNLOCK:
            return WindowsSystemEvent("wts", "session_unlock", int(code))
        if code in {WTS_SESSION_DISCONNECT, WTS_SESSION_REMOTE_DISCONNECT}:
            return WindowsSystemEvent("wts", "session_disconnect", int(code))
        if code in {WTS_SESSION_CONNECT, WTS_SESSION_REMOTE_CONNECT}:
            return WindowsSystemEvent("wts", "session_connect", int(code))
        if code == WTS_SESSION_LOGOFF:
            return WindowsSystemEvent("session", "session_logoff", int(code))
    if message == WM_POWERBROADCAST:
        if code == PBT_APMSUSPEND:
            return WindowsSystemEvent("power", "suspend", int(code))
        if code == PBT_APMRESUMEAUTOMATIC:
            return WindowsSystemEvent("power", "resume_automatic", int(code))
        if code == PBT_APMRESUMESUSPEND:
            return WindowsSystemEvent("power", "resume_user", int(code))
    return None


if os.name == "nt":
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    wtsapi32 = ctypes.windll.wtsapi32
    LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HANDLE),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = LRESULT
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.SendMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.SendMessageW.restype = LRESULT
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
    wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
    wtsapi32.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
    wtsapi32.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
else:
    WNDPROC = None
    WNDCLASSW = None


class Win32SystemEventSource:
    """Receive WTS and power messages on a hidden user-session top-level window."""

    _TEST_EVENT_CODES = {
        "session_lock": (WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK),
        "session_unlock": (WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK),
        "session_disconnect": (WM_WTSSESSION_CHANGE, WTS_SESSION_DISCONNECT),
        "session_connect": (WM_WTSSESSION_CHANGE, WTS_SESSION_CONNECT),
        "session_logoff": (WM_WTSSESSION_CHANGE, WTS_SESSION_LOGOFF),
        "suspend": (WM_POWERBROADCAST, PBT_APMSUSPEND),
        "resume_automatic": (WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC),
        "resume_user": (WM_POWERBROADCAST, PBT_APMRESUMESUSPEND),
        "session_end": (WM_ENDSESSION, 1),
    }

    def __init__(self, on_event):
        self._on_event = on_event
        self._thread = None
        self._ready = threading.Event()
        self._hwnd = None
        self._class_name = ""
        self._hinstance = None
        self._wndproc = None
        self._wts_registered = False
        self._class_registered = False
        self._window_created = False
        self._event_count = 0
        self._callback_error_count = 0
        self._last_error = ""

    def _dispatch(self, message: int, code: int) -> None:
        event = normalize_system_event(message, code)
        if event is None:
            return
        self._event_count += 1
        try:
            self._on_event(event)
        except Exception as exc:
            self._callback_error_count += 1
            self._last_error = type(exc).__name__

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == WM_QUERYENDSESSION:
            # Reply immediately. Any flushing belongs to WM_ENDSESSION so this
            # observer can never veto or delay a Windows shutdown query.
            return 1
        if message == WM_ENDSESSION:
            self._dispatch(int(message), int(wparam))
            return 0
        if message in {WM_WTSSESSION_CHANGE, WM_POWERBROADCAST}:
            self._dispatch(int(message), int(wparam))
            return 1 if message == WM_POWERBROADCAST else 0
        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _run(self) -> None:
        if os.name != "nt":
            self._last_error = "WindowsRequired"
            self._ready.set()
            return
        self._class_name = f"InteractionRhythmSystemEvents{os.getpid()}_{id(self):x}"
        self._hinstance = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._window_proc)
        registered_class = False
        try:
            window_class = WNDCLASSW()
            window_class.lpfnWndProc = self._wndproc
            window_class.hInstance = self._hinstance
            window_class.lpszClassName = self._class_name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                self._last_error = "RegisterClassW"
                return
            registered_class = True
            self._class_registered = True
            self._hwnd = user32.CreateWindowExW(
                0,
                self._class_name,
                self._class_name,
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                self._hinstance,
                None,
            )
            if not self._hwnd:
                self._last_error = "CreateWindowExW"
                return
            self._window_created = True
            self._wts_registered = bool(
                wtsapi32.WTSRegisterSessionNotification(
                    self._hwnd,
                    NOTIFY_FOR_THIS_SESSION,
                )
            )
            if not self._wts_registered:
                self._last_error = "WTSRegisterSessionNotification"
            self._ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self._last_error = type(exc).__name__
        finally:
            self._ready.set()
            hwnd = self._hwnd
            if hwnd and self._wts_registered:
                wtsapi32.WTSUnRegisterSessionNotification(hwnd)
            self._wts_registered = False
            if hwnd and user32.IsWindow(hwnd):
                user32.DestroyWindow(hwnd)
            self._hwnd = None
            if registered_class:
                user32.UnregisterClassW(self._class_name, self._hinstance)
            self._class_registered = False

    def start(self, timeout: float = 2.0) -> bool:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.healthy

    def stop(self, timeout: float = 2.0) -> bool:
        hwnd = self._hwnd
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)
        return not bool(self._thread and self._thread.is_alive())

    def post_test_event(self, kind: str) -> bool:
        """Post a fixture message to this window; never changes OS session or power state."""
        if kind not in self._TEST_EVENT_CODES:
            raise ValueError(f"unsupported system event fixture: {kind}")
        if not self._hwnd:
            return False
        message, code = self._TEST_EVENT_CODES[kind]
        return bool(user32.PostMessageW(self._hwnd, message, code, 0))

    def query_end_session_for_test(self) -> int:
        """Synchronously exercise the non-veto response on the fixture window."""
        if not self._hwnd:
            return 0
        return int(user32.SendMessageW(self._hwnd, WM_QUERYENDSESSION, 0, 0))

    @property
    def healthy(self) -> bool:
        return bool(
            self._window_created
            and self._wts_registered
            and self._hwnd
            and self._thread
            and self._thread.is_alive()
        )

    @property
    def stats(self) -> dict:
        return {
            "window_created": self._window_created,
            "window_active": bool(self._hwnd),
            "class_registered": self._class_registered,
            "wts_registered": self._wts_registered,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "event_count": self._event_count,
            "callback_error_count": self._callback_error_count,
            "last_error": self._last_error,
            "healthy": self.healthy,
        }
