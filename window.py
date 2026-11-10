"""原生桌面窗口（pywebview + Edge WebView2）"""
import config
import ctypes
import ctypes.wintypes
import time
from urllib.parse import urlsplit
import webbrowser

user32 = ctypes.windll.user32
_SetWindowPos = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_uint,
)(("SetWindowPos", user32))
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetWindowRect.restype = ctypes.c_bool
user32.GetParent.argtypes = [ctypes.c_void_p]
user32.GetParent.restype = ctypes.c_void_p
user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetWindow.restype = ctypes.c_void_p
user32.ReleaseCapture.argtypes = []
user32.ReleaseCapture.restype = ctypes.c_bool
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.SetForegroundWindow.restype = ctypes.c_bool
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
user32.SystemParametersInfoW.restype = ctypes.c_bool
user32.SendMessageW.argtypes = [
    ctypes.c_void_p, ctypes.c_uint,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
]
user32.SendMessageW.restype = ctypes.wintypes.LPARAM
user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

# SetWindowPos flags
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
SWP_SHOWWINDOW = 0x0040
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_FRAMECHANGED = 0x0020
SW_HIDE = 0
SW_SHOW = 5
SW_MINIMIZE = 6
SW_RESTORE = 9
WM_NCLBUTTONDOWN = 0x00A1
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
HTCAPTION = 2
VK_LBUTTON = 0x01
GW_OWNER = 4
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_MINIMIZEBOX = 0x00020000
WS_SYSMENU = 0x00080000
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
SPI_GETWORKAREA = 0x0030
SM_CXSCREEN = 0
SM_CYSCREEN = 1
WINDOW_WIDTH = 520
WINDOW_HEIGHT = 860
TITLEBAR_HEIGHT = 40


def should_hide_for_close_reason(reason) -> bool:
    """Only user-initiated closes hide the window; system exits must proceed."""
    name = str(reason).rsplit(".", 1)[-1]
    return name not in {"WindowsShutDown", "TaskManagerClosing", "ApplicationExitCall"}

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _center_position(width: int, height: int) -> tuple[int, int]:
    rect = RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    else:
        left, top = 0, 0
        right, bottom = user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)
    return (
        left + max(0, (right - left - width) // 2),
        top + max(0, (bottom - top - height) // 2),
    )


def _is_offscreen_or_tiny(hwnd) -> bool:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return True
    width = max(0, rect.right - rect.left)
    height = max(0, rect.bottom - rect.top)
    if width < WINDOW_WIDTH // 2 or height < WINDOW_HEIGHT // 2:
        return True

    work = RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0):
        return (
            rect.right <= work.left
            or rect.left >= work.right
            or rect.bottom <= work.top
            or rect.top >= work.bottom
            or rect.left < work.left - 200
            or rect.top < work.top - 200
        )
    return rect.left < -200 or rect.top < -200


def _restore_main_window(hwnd, force_center: bool = False):
    if force_center or _is_offscreen_or_tiny(hwnd):
        x, y = _center_position(WINDOW_WIDTH, WINDOW_HEIGHT)
        _SetWindowPos(
            hwnd, 0, x, y, WINDOW_WIDTH, WINDOW_HEIGHT,
            SWP_NOZORDER | SWP_NOOWNERZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW
        )
    else:
        user32.ShowWindow(hwnd, SW_RESTORE)
        _SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOOWNERZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW
        )
    user32.SetForegroundWindow(hwnd)


def _find_hwnd(title):
    """通过窗口标题找到 HWND"""
    candidates = []

    def _cb(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value == title:
                rect = RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
                exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                score = area
                if exstyle & WS_EX_APPWINDOW:
                    score += 1_000_000_000
                if exstyle & WS_EX_TOOLWINDOW:
                    score -= 1_000_000_000
                if user32.GetParent(hwnd):
                    score -= 1_000_000
                if user32.GetWindow(hwnd, GW_OWNER):
                    score -= 1_000_000
                candidates.append((score, hwnd))
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _window_has_webview(hwnd, wait: float = 3.0) -> bool:
    """检查窗口是否已挂载可用的 WebView2 渲染，防止恢复空壳白屏窗口。"""
    marker_classes = {"Chrome_WidgetWin_0", "Chrome_RenderWidgetHostHWND"}
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    deadline = time.monotonic() + wait
    while True:
        found = False

        def _cb(h, _):
            nonlocal found
            buf = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(h, buf, 128)
            if buf.value in marker_classes:
                found = True
                return False
            return True

        user32.EnumChildWindows(hwnd, WNDENUMPROC(_cb), 0)
        if found:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


class WindowAPI:
    """暴露给 JS 调用的 Python API（最小化 + 隐藏到托盘）"""

    def __init__(self, window):
        self._window = window
        self._hwnd = None
        self._native_theme = "light"
        self._drag_enabled_at = time.monotonic() + 2.0

    def _get_hwnd(self):
        if not self._hwnd:
            native = getattr(self._window, "native", None)
            handle = getattr(native, "Handle", None)
            try:
                if handle:
                    self._hwnd = int(handle.ToInt64())
            except Exception:
                self._hwnd = None
        if not self._hwnd:
            self._hwnd = _find_hwnd(config.APP_NAME)
        return self._hwnd

    def close(self):
        return self.hide_to_tray()

    def hide_to_tray(self):
        hwnd = self._get_hwnd()
        if hwnd:
            user32.ShowWindow(hwnd, SW_HIDE)
            return True
        try:
            self._window.hide()
            return True
        except Exception:
            return False

    def minimize(self):
        hwnd = self._get_hwnd()
        if hwnd:
            user32.ShowWindow(hwnd, SW_MINIMIZE)
        else:
            self._window.minimize()

    def open_url(self, url):
        """Open an explicitly supplied external URL in the user's default browser."""
        value = str(url or "").strip()
        if not value:
            return False
        try:
            parts = urlsplit(value)
        except ValueError:
            return False
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        try:
            return bool(webbrowser.open(value, new=2))
        except Exception:
            return False

    def set_native_theme(self, theme):
        self._native_theme = "dark" if str(theme).lower() == "dark" else "light"
        apply_theme = getattr(self, "_apply_native_theme", None)
        if apply_theme:
            apply_theme(self._native_theme)
        return True


def create_window(port: int, icon_path: str = None, title_icon_path: str = None, start_hidden: bool = False):
    """创建原生桌面窗口，返回 (window, start_fn)"""
    import webview

    # Avoid pywebview's JS mousemove drag loop. A native WinForms titlebar is
    # installed above WebView and handles drag synchronously in Windows.
    webview.settings["DRAG_REGION_SELECTOR"] = ".__type_tracker_no_web_drag__"
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True

    api = WindowAPI(None)
    frame_query = "frameless=1" if config.BORDERLESS_WINDOW else "frameless=0"
    nativebar_query = "nativebar=1" if config.BORDERLESS_WINDOW else "nativebar=0"
    x, y = _center_position(WINDOW_WIDTH, WINDOW_HEIGHT)
    window = webview.create_window(
        title=config.APP_NAME,
        url=f"http://127.0.0.1:{port}?desktop=1&v={config.APP_VERSION}&{frame_query}&{nativebar_query}",
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        x=x,
        y=y,
        min_size=(WINDOW_WIDTH, WINDOW_HEIGHT),
        resizable=False,
        frameless=config.BORDERLESS_WINDOW,
        easy_drag=False,
        hidden=start_hidden,
        js_api=api,
    )
    api._window = window

    def _install_native_titlebar_once():
        native = getattr(window, "native", None)
        if not native or getattr(api, "_native_titlebar_installed", False):
            return
        try:
            import clr
            clr.AddReference("System.Windows.Forms")
            clr.AddReference("System.Drawing")
            import System.Windows.Forms as WinForms
            from System.Drawing import Color, ContentAlignment, Font, FontStyle, Icon, Image, Point, Size

            def _create():
                if getattr(api, "_native_titlebar_installed", False):
                    return None
                api._hwnd = int(native.Handle.ToInt64())
                try:
                    if icon_path:
                        form_icon = Icon(icon_path)
                        native.Icon = form_icon
                        api._native_form_icon = form_icon
                        hicon = int(form_icon.Handle.ToInt64())
                        user32.SendMessageW(api._hwnd, WM_SETICON, ICON_SMALL, hicon)
                        user32.SendMessageW(api._hwnd, WM_SETICON, ICON_BIG, hicon)
                except Exception:
                    pass
                scale = float(getattr(native, "_scale", 1.0) or 1.0)

                def px(value):
                    return max(1, int(round(value * scale)))

                bar_h = px(TITLEBAR_HEIGHT)

                webview_control = getattr(native, "webview", None)
                if webview_control:
                    webview_control.Dock = getattr(WinForms.DockStyle, "None")
                    webview_control.Location = Point(0, bar_h)
                    webview_control.Size = Size(
                        native.ClientSize.Width,
                        max(1, native.ClientSize.Height - bar_h),
                    )
                    webview_control.Anchor = (
                        WinForms.AnchorStyles.Top
                        | WinForms.AnchorStyles.Bottom
                        | WinForms.AnchorStyles.Left
                        | WinForms.AnchorStyles.Right
                    )

                panel = WinForms.Panel()
                panel.Name = "InteractionRhythmNativeTitlebar"
                panel.Height = bar_h
                panel.Dock = WinForms.DockStyle.Top

                logo = WinForms.PictureBox()
                logo.AutoSize = False
                logo.BackColor = Color.Transparent
                logo.Location = Point(px(12), px(8))
                logo.Size = Size(px(24), px(24))
                logo.SizeMode = WinForms.PictureBoxSizeMode.StretchImage
                try:
                    if title_icon_path:
                        logo.Image = Image.FromFile(title_icon_path)
                        api._native_logo_image = logo.Image
                except Exception:
                    logo.BackColor = Color.FromArgb(21, 128, 61)

                title = WinForms.Label()
                title.Text = config.APP_NAME
                title.AutoSize = False
                title.TextAlign = ContentAlignment.MiddleLeft
                title.Font = Font("LXGW WenKai", 10)
                title.BackColor = Color.Transparent
                title.Location = Point(px(44), px(7))
                title.Size = Size(px(180), px(26))

                def _drag(sender, args):
                    if time.monotonic() < getattr(api, "_drag_enabled_at", 0):
                        return
                    if not (user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000):
                        return
                    if args.Button == WinForms.MouseButtons.Left:
                        hwnd = api._get_hwnd()
                        if hwnd:
                            user32.SetForegroundWindow(hwnd)
                            user32.ReleaseCapture()
                            user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)

                panel.MouseDown += _drag
                logo.MouseDown += _drag
                title.MouseDown += _drag

                button_panel = WinForms.FlowLayoutPanel()
                button_panel.AutoSize = False
                button_panel.FlowDirection = WinForms.FlowDirection.RightToLeft
                button_panel.WrapContents = False
                button_panel.Dock = WinForms.DockStyle.Right
                button_panel.Width = px(164)
                button_panel.Height = bar_h
                button_panel.Padding = WinForms.Padding(0, px(6), px(8), 0)
                button_panel.BackColor = Color.Transparent

                def _button(text, color=None, bold=False, font_size=10, close=False, font_name="Segoe UI"):
                    btn = WinForms.Label()
                    btn.Text = text
                    btn.AutoSize = False
                    btn.TextAlign = ContentAlignment.MiddleCenter
                    btn.ForeColor = color or Color.FromArgb(82, 102, 91)
                    btn.Font = Font(font_name, font_size, FontStyle.Bold if bold else FontStyle.Regular)
                    btn.Size = Size(px(28), px(28))
                    btn.Margin = WinForms.Padding(px(2), 0, 0, 0)
                    btn.Cursor = WinForms.Cursors.Hand

                    def _enter(sender, args):
                        sender.BackColor = (
                            getattr(api, "_title_red_hover", sender.BackColor)
                            if close
                            else getattr(api, "_title_hover_bg", sender.BackColor)
                        )

                    def _leave(sender, args):
                        sender.BackColor = getattr(api, "_title_bar_bg", sender.BackColor)

                    def _down(sender, args):
                        sender.BackColor = getattr(api, "_title_down_bg", sender.BackColor)

                    def _up(sender, args):
                        sender.BackColor = (
                            getattr(api, "_title_red_hover", sender.BackColor)
                            if close
                            else getattr(api, "_title_hover_bg", sender.BackColor)
                        )

                    btn.MouseEnter += _enter
                    btn.MouseLeave += _leave
                    btn.MouseDown += _down
                    btn.MouseUp += _up
                    return btn

                close_btn = _button("\uE8BB", Color.FromArgb(220, 38, 38), font_size=8, close=True, font_name="Segoe MDL2 Assets")
                min_btn = _button("\uE921", font_size=8, font_name="Segoe MDL2 Assets")
                settings_btn = _button("\uE713", font_size=8, font_name="Segoe MDL2 Assets")
                support_btn = _button("♥", Color.FromArgb(21, 128, 61), font_size=10)
                ai_btn = _button("AI", bold=True, font_size=8)

                tips = WinForms.ToolTip()
                tips.SetToolTip(ai_btn, "MiMo 回放")
                tips.SetToolTip(settings_btn, "设置")
                tips.SetToolTip(support_btn, "支持扣舷")
                tips.SetToolTip(min_btn, "最小化")
                tips.SetToolTip(close_btn, "隐藏到托盘")

                def _run_js(script):
                    try:
                        browser = getattr(native, "browser", None)
                        webview = getattr(browser, "webview", None)
                        core = getattr(webview, "CoreWebView2", None)
                        if core:
                            core.ExecuteScriptAsync(script)
                    except Exception:
                        pass

                def _minimize(sender, args):
                    native.WindowState = WinForms.FormWindowState.Minimized

                def _close(sender, args):
                    api.hide_to_tray()

                def _settings(sender, args):
                    _run_js("openSettings()")

                def _insights(sender, args):
                    _run_js("openMimoInsights()")

                def _support(sender, args):
                    _run_js("openSupport()")

                min_btn.Click += _minimize
                close_btn.Click += _close
                settings_btn.Click += _settings
                support_btn.Click += _support
                ai_btn.Click += _insights

                def _form_closing(sender, args):
                    if should_hide_for_close_reason(args.CloseReason):
                        args.Cancel = True
                        api.hide_to_tray()

                native.FormClosing += _form_closing

                api._native_tip = tips

                def _apply_native_theme(theme):
                    def _apply():
                        dark = theme == "dark"
                        bar_bg = Color.FromArgb(13, 18, 22) if dark else Color.FromArgb(244, 246, 245)
                        hover_bg = Color.FromArgb(24, 36, 43) if dark else Color.FromArgb(232, 238, 234)
                        down_bg = Color.FromArgb(31, 41, 48) if dark else Color.FromArgb(220, 230, 224)
                        text = Color.FromArgb(232, 236, 233) if dark else Color.FromArgb(24, 35, 29)
                        muted = Color.FromArgb(151, 163, 156) if dark else Color.FromArgb(82, 102, 91)
                        faint = Color.FromArgb(105, 120, 111) if dark else Color.FromArgb(132, 149, 141)
                        green = Color.FromArgb(74, 222, 128) if dark else Color.FromArgb(21, 128, 61)
                        red = Color.FromArgb(248, 113, 113) if dark else Color.FromArgb(220, 38, 38)
                        red_hover = Color.FromArgb(69, 26, 32) if dark else Color.FromArgb(254, 226, 226)
                        api._title_bar_bg = bar_bg
                        api._title_hover_bg = hover_bg
                        api._title_down_bg = down_bg
                        api._title_red_hover = red_hover

                        panel.BackColor = bar_bg
                        if logo.Image:
                            logo.BackColor = Color.Transparent
                        title.ForeColor = text
                        for btn in (ai_btn, support_btn, settings_btn, min_btn, close_btn):
                            btn.BackColor = bar_bg
                        ai_btn.ForeColor = muted
                        settings_btn.ForeColor = muted
                        min_btn.ForeColor = muted
                        close_btn.ForeColor = red

                    try:
                        if native.InvokeRequired:
                            native.BeginInvoke(WinForms.MethodInvoker(_apply))
                        else:
                            _apply()
                    except Exception:
                        pass

                api._apply_native_theme = _apply_native_theme

                panel.Controls.Add(logo)
                panel.Controls.Add(title)
                button_panel.Controls.Add(close_btn)
                button_panel.Controls.Add(min_btn)
                button_panel.Controls.Add(settings_btn)
                button_panel.Controls.Add(support_btn)
                button_panel.Controls.Add(ai_btn)
                panel.Controls.Add(button_panel)
                native.Controls.Add(panel)
                panel.BringToFront()
                _apply_native_theme(api._native_theme)
                api._native_titlebar_installed = True
                api._drag_enabled_at = time.monotonic() + 1.2
                return None

            if native.InvokeRequired:
                native.Invoke(WinForms.MethodInvoker(_create))
            else:
                _create()
        except Exception as e:
            print(f"[{config.APP_NAME}] 原生标题栏安装失败: {e}")
            return

    startup_position_fixed = {"done": False}

    def _fix_window_style_once():
        hwnd = api._get_hwnd()
        if not hwnd:
            return
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_SYSMENU | WS_MINIMIZEBOX)
        exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        exstyle = (exstyle | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle)
        if not startup_position_fixed["done"]:
            if start_hidden:
                _SetWindowPos(
                    hwnd, 0, x, y, WINDOW_WIDTH, WINDOW_HEIGHT,
                    SWP_NOZORDER | SWP_NOOWNERZORDER | SWP_FRAMECHANGED | SWP_NOACTIVATE,
                )
            elif _is_offscreen_or_tiny(hwnd):
                _restore_main_window(hwnd, force_center=True)
            else:
                user32.ShowWindow(hwnd, SW_RESTORE)
                _SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOOWNERZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
                )
            startup_position_fixed["done"] = True
        else:
            _SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOOWNERZORDER | SWP_FRAMECHANGED
            )

    def _fix_window_style():
        import threading
        import time

        def _loop():
            for _ in range(3):
                _install_native_titlebar_once()
                _fix_window_style_once()
                if startup_position_fixed["done"] and getattr(api, "_native_titlebar_installed", False):
                    break
                time.sleep(0.2)

        threading.Thread(target=_loop, daemon=True).start()

    def start():
        webview.start(_fix_window_style, debug=False, gui="edgechromium", icon=icon_path)

    return window, start
