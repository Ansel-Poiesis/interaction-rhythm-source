"""系统托盘图标与应用图标生成。"""
from pathlib import Path
from PIL import Image

import config


def make_icon(size: int = 256) -> Image.Image:
    """从项目母版生成指定尺寸的应用图标。"""
    from icon_assets import make_icon_image

    return make_icon_image(size)


def make_ico(ico_path: str):
    """生成 .ico 文件（多分辨率）。"""
    from icon_assets import write_ico

    write_ico(ico_path)


def create_tray(tracker, port: int, shutdown_callback=None):
    """返回 (icon, start_fn)。Windows 原生托盘，避免 pystray 菜单延迟。"""
    import ctypes
    import ctypes.wintypes as wintypes
    import os
    import tempfile
    import threading
    import time
    import webbrowser

    if os.name != "nt":
        def _start_fallback():
            return None
        return None, _start_fallback

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32

    LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    HICON = getattr(wintypes, "HICON", wintypes.HANDLE)
    HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
    HBRUSH = getattr(wintypes, "HBRUSH", wintypes.HANDLE)
    UINT_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    WM_DESTROY = 0x0002
    WM_COMMAND = 0x0111
    WM_NULL = 0x0000
    WM_USER = 0x0400
    WM_TRAY = WM_USER + 23
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONUP = 0x0205
    WM_CONTEXTMENU = 0x007B

    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004

    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x00000010
    LR_DEFAULTSIZE = 0x00000040

    MF_STRING = 0x00000000
    TPM_LEFTALIGN = 0x0000
    TPM_BOTTOMALIGN = 0x0020
    TPM_RIGHTBUTTON = 0x0002
    TPM_NONOTIFY = 0x0080
    TPM_RETURNCMD = 0x0100

    ID_OPEN = 1001
    ID_REFRESH = 1002
    ID_QUIT = 1003

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", HICON),
        ]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", HICON),
            ("hCursor", HCURSOR),
            ("hbrBackground", HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.DestroyIcon.argtypes = [HICON]
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, UINT_PTR, wintypes.LPCWSTR]
    user32.TrackPopupMenu.argtypes = [
        wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p
    ]
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL

    def _fmt(sec):
        h, m = divmod(int(sec) // 60, 60)
        return f"{h}h {m:02d}m" if h else f"{m}m"

    def _tooltip():
        try:
            s = tracker.stats
            total = int(s.get("keystrokes", 0)) + int(s.get("mouse_events", 0))
            return f"{config.APP_NAME} · 响应 {total:,} · 活跃 {_fmt(s.get('total_seconds', 0))}"
        except Exception:
            return f"{config.APP_NAME} · 后台运行"

    def _restore_window():
        try:
            from window import _find_hwnd, _restore_main_window, _window_has_webview
            hwnd = _find_hwnd(config.APP_NAME)
            if hwnd and _window_has_webview(hwnd, wait=2.5):
                _restore_main_window(hwnd)
                return True
        except Exception:
            pass
        return False

    def _open_dashboard():
        if _restore_window():
            return
        # 窗口存在但 WebView 未挂载：先尝试重新加载一次，仍失败则退回浏览器。
        try:
            import webview
            windows = getattr(webview, "windows", None)
            if windows:
                windows[0].load_url(f"http://127.0.0.1:{port}?desktop=1&frameless=1&nativebar=1")
                time.sleep(0.8)
                if _restore_window():
                    return
        except Exception:
            pass
        webbrowser.open(f"http://127.0.0.1:{port}")

    def _refresh_now(icon):
        try:
            tracker.flush()
        except Exception:
            pass
        icon.update_tooltip(_tooltip())

    def _hard_exit(_icon=None):
        try:
            if kernel32.TerminateProcess(kernel32.GetCurrentProcess(), 0):
                return
        except Exception:
            pass
        os._exit(0)

    class NativeTrayIcon:
        def __init__(self):
            self.hwnd = None
            self.hicon = None
            self._icon_file = None
            self._class_name = f"InteractionRhythmTray{os.getpid()}"
            self._hinstance = kernel32.GetModuleHandleW(None)
            self._wndproc = WNDPROC(self._window_proc)
            self._deleted = False

        def _icon_path(self):
            temp_dir = Path(tempfile.gettempdir())
            for stale in temp_dir.glob("kouxian-tray*.ico"):
                try:
                    stale.unlink()
                except OSError:
                    pass
            path = temp_dir / f"kouxian-tray-{os.getpid()}.ico"
            make_ico(str(path))
            self._icon_file = path
            return str(path)

        def _make_nid(self, flags=0, tooltip=None):
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = flags
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = self.hicon
            if tooltip is not None:
                nid.szTip = str(tooltip)[:127]
            return nid

        def _register_window(self):
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = self._hinstance
            wc.lpszClassName = self._class_name
            user32.RegisterClassW(ctypes.byref(wc))
            self.hwnd = user32.CreateWindowExW(
                0, self._class_name, self._class_name, 0,
                0, 0, 0, 0, None, None, self._hinstance, None,
            )
            if not self.hwnd:
                raise RuntimeError("Native tray window creation failed")

        def _load_icon(self):
            self.hicon = user32.LoadImageW(
                None, self._icon_path(), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
            )
            if not self.hicon:
                raise RuntimeError("Native tray icon loading failed")

        def add(self):
            self._register_window()
            self._load_icon()
            nid = self._make_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP, _tooltip())
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
                raise RuntimeError("Native tray icon add failed")

        def update_tooltip(self, tooltip):
            if self._deleted or not self.hwnd:
                return
            nid = self._make_nid(NIF_TIP, tooltip)
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

        def delete(self):
            if self._deleted or not self.hwnd:
                return
            self._deleted = True
            nid = self._make_nid(0)
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

        def _show_menu(self):
            menu = user32.CreatePopupMenu()
            if not menu:
                return
            try:
                user32.AppendMenuW(menu, MF_STRING, ID_OPEN, config.APP_NAME)
                user32.AppendMenuW(menu, MF_STRING, ID_REFRESH, "刷新数据")
                user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "退出后台")
                point = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(point))
                user32.SetForegroundWindow(self.hwnd)
                command = user32.TrackPopupMenu(
                    menu,
                    TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
                    point.x,
                    point.y,
                    0,
                    self.hwnd,
                    None,
                )
                if command == ID_OPEN:
                    _open_dashboard()
                elif command == ID_REFRESH:
                    _refresh_now(self)
                elif command == ID_QUIT:
                    _hard_exit()
                user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            finally:
                user32.DestroyMenu(menu)

        def _window_proc(self, hwnd, msg, wparam, lparam):
            if msg == WM_TRAY:
                if lparam == WM_LBUTTONUP:
                    _open_dashboard()
                    return 0
                if lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                    return 0
            elif msg == WM_COMMAND:
                command = int(wparam) & 0xFFFF
                if command == ID_OPEN:
                    _open_dashboard()
                    return 0
                if command == ID_REFRESH:
                    _refresh_now(self)
                    return 0
                if command == ID_QUIT:
                    _hard_exit(self)
                    return 0
            elif msg == WM_DESTROY:
                self.delete()
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        def run(self):
            self.add()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            self.delete()
            if self.hicon:
                try:
                    user32.DestroyIcon(self.hicon)
                except Exception:
                    pass
            if self._icon_file:
                try:
                    self._icon_file.unlink()
                except OSError:
                    pass

    icon = NativeTrayIcon()

    def _refresh():
        while True:
            time.sleep(30)
            try:
                icon.update_tooltip(_tooltip())
            except Exception:
                pass

    def start():
        threading.Thread(target=_refresh, daemon=True).start()
        icon.run()

    return icon, start
