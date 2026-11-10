"""Privacy-preserving Raw Input transport prototype for Windows benchmarks."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from collections import Counter
import os
import threading

from interaction_rhythm.win32_input_protocol import is_benchmark_input


WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2
RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_MOUSE = 0x02
HID_USAGE_GENERIC_KEYBOARD = 0x06
RI_KEY_BREAK = 0x0001
RI_KEY_E0 = 0x0002
RI_KEY_E1 = 0x0004

RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_WHEEL = 0x0400
RI_MOUSE_HWHEEL = 0x0800

_MOUSE_BUTTON_DOWN_FLAGS = (
    RI_MOUSE_LEFT_BUTTON_DOWN,
    RI_MOUSE_RIGHT_BUTTON_DOWN,
    RI_MOUSE_MIDDLE_BUTTON_DOWN,
    RI_MOUSE_BUTTON_4_DOWN,
    RI_MOUSE_BUTTON_5_DOWN,
)


class RawInputCounter:
    """Aggregate packets without retaining keys, device IDs, coordinates, or payloads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pressed = set()
        self._counts = Counter()
        self._devices = set()

    def record_keyboard(
        self,
        message: int,
        make_code: int,
        flags: int,
        device_handle=0,
        extra_information: int = 0,
    ) -> None:
        identity = (int(make_code), int(flags) & (RI_KEY_E0 | RI_KEY_E1))
        with self._lock:
            self._counts["keyboard_packets"] += 1
            if is_benchmark_input(extra_information):
                self._counts["benchmark_packets_ignored"] += 1
                return
            if device_handle:
                self._devices.add(int(device_handle))
            if message in {WM_KEYUP, WM_SYSKEYUP} or flags & RI_KEY_BREAK:
                self._counts["keyboard_releases"] += 1
                self._pressed.discard(identity)
                return
            if message not in {WM_KEYDOWN, WM_SYSKEYDOWN}:
                self._counts["keyboard_other_messages"] += 1
                return
            self._counts["keyboard_raw_down_messages"] += 1
            if identity in self._pressed:
                self._counts["keyboard_repeat_messages"] += 1
                return
            self._pressed.add(identity)
            self._counts["keyboard_presses"] += 1

    def record_mouse(
        self,
        button_flags: int,
        device_handle=0,
        extra_information: int = 0,
    ) -> None:
        flags = int(button_flags)
        with self._lock:
            self._counts["mouse_packets"] += 1
            if is_benchmark_input(extra_information):
                self._counts["benchmark_packets_ignored"] += 1
                return
            if device_handle:
                self._devices.add(int(device_handle))
            button_count = sum(1 for flag in _MOUSE_BUTTON_DOWN_FLAGS if flags & flag)
            self._counts["mouse_button_presses"] += button_count
            if flags & RI_MOUSE_WHEEL:
                self._counts["vertical_scroll_events"] += 1
            if flags & RI_MOUSE_HWHEEL:
                self._counts["horizontal_scroll_events"] += 1
            if not button_count and not flags & (RI_MOUSE_WHEEL | RI_MOUSE_HWHEEL):
                self._counts["movement_only_packets"] += 1

    def record_hid(self, device_handle=0) -> None:
        with self._lock:
            self._counts["hid_packets_ignored"] += 1
            if device_handle:
                self._devices.add(int(device_handle))

    @property
    def snapshot(self) -> dict:
        with self._lock:
            counts = dict(self._counts)
            return {
                "keyboard_packets": counts.get("keyboard_packets", 0),
                "keyboard_raw_down_messages": counts.get("keyboard_raw_down_messages", 0),
                "keyboard_presses": counts.get("keyboard_presses", 0),
                "keyboard_repeat_messages": counts.get("keyboard_repeat_messages", 0),
                "keyboard_releases": counts.get("keyboard_releases", 0),
                "keyboard_other_messages": counts.get("keyboard_other_messages", 0),
                "mouse_packets": counts.get("mouse_packets", 0),
                "mouse_button_presses": counts.get("mouse_button_presses", 0),
                "vertical_scroll_events": counts.get("vertical_scroll_events", 0),
                "horizontal_scroll_events": counts.get("horizontal_scroll_events", 0),
                "movement_only_packets": counts.get("movement_only_packets", 0),
                "hid_packets_ignored": counts.get("hid_packets_ignored", 0),
                "benchmark_packets_ignored": counts.get("benchmark_packets_ignored", 0),
                "distinct_device_count": len(self._devices),
                "pressed_state_size": len(self._pressed),
                "key_values_retained": False,
                "device_identifiers_retained": False,
                "coordinates_accessed": False,
            }


if os.name == "nt":
    # Dedicated DLL wrappers keep ctypes argtypes isolated from other Win32 adapters.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [
            ("usUsagePage", wintypes.USHORT),
            ("usUsage", wintypes.USHORT),
            ("dwFlags", wintypes.DWORD),
            ("hwndTarget", wintypes.HWND),
        ]

    class RAWINPUTDEVICELIST(ctypes.Structure):
        _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]

    class RAWINPUTHEADER(ctypes.Structure):
        _fields_ = [
            ("dwType", wintypes.DWORD),
            ("dwSize", wintypes.DWORD),
            ("hDevice", wintypes.HANDLE),
            ("wParam", wintypes.WPARAM),
        ]

    class _RAWMOUSE_BUTTON_FIELDS(ctypes.Structure):
        _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]

    class _RAWMOUSE_BUTTONS(ctypes.Union):
        _anonymous_ = ("fields",)
        _fields_ = [("ulButtons", wintypes.ULONG), ("fields", _RAWMOUSE_BUTTON_FIELDS)]

    class RAWMOUSE(ctypes.Structure):
        _anonymous_ = ("buttons",)
        _fields_ = [
            ("usFlags", wintypes.USHORT),
            ("buttons", _RAWMOUSE_BUTTONS),
            ("ulRawButtons", wintypes.ULONG),
            ("lLastX", wintypes.LONG),
            ("lLastY", wintypes.LONG),
            ("ulExtraInformation", wintypes.ULONG),
        ]

    class RAWKEYBOARD(ctypes.Structure):
        _fields_ = [
            ("MakeCode", wintypes.USHORT),
            ("Flags", wintypes.USHORT),
            ("Reserved", wintypes.USHORT),
            ("VKey", wintypes.USHORT),
            ("Message", wintypes.UINT),
            ("ExtraInformation", wintypes.ULONG),
        ]

    class RAWHID(ctypes.Structure):
        _fields_ = [("dwSizeHid", wintypes.DWORD), ("dwCount", wintypes.DWORD), ("bRawData", ctypes.c_byte * 1)]

    class _RAWINPUT_DATA(ctypes.Union):
        _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD), ("hid", RAWHID)]

    class RAWINPUT(ctypes.Structure):
        _anonymous_ = ("data",)
        _fields_ = [("header", RAWINPUTHEADER), ("data", _RAWINPUT_DATA)]

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.RegisterRawInputDevices.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT,
    ]
    user32.RegisterRawInputDevices.restype = wintypes.BOOL
    user32.GetRawInputData.argtypes = [
        wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
        ctypes.POINTER(wintypes.UINT), wintypes.UINT,
    ]
    user32.GetRawInputData.restype = wintypes.UINT
    user32.GetRawInputDeviceList.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICELIST), ctypes.POINTER(wintypes.UINT), wintypes.UINT,
    ]
    user32.GetRawInputDeviceList.restype = wintypes.UINT
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
else:
    WNDPROC = None
    WNDCLASSW = None


def raw_input_device_inventory() -> dict:
    """Return type counts only; device names and handles never leave this function."""
    if os.name != "nt":
        return {"keyboard": 0, "mouse": 0, "hid": 0, "other": 0, "total": 0}
    count = wintypes.UINT(0)
    result = user32.GetRawInputDeviceList(None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if result == 0xFFFFFFFF:
        raise OSError("GetRawInputDeviceList size query failed")
    devices = (RAWINPUTDEVICELIST * count.value)()
    if count.value:
        result = user32.GetRawInputDeviceList(devices, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
        if result == 0xFFFFFFFF:
            raise OSError("GetRawInputDeviceList failed")
    types = Counter(int(devices[index].dwType) for index in range(count.value))
    known = types.get(RIM_TYPEKEYBOARD, 0) + types.get(RIM_TYPEMOUSE, 0) + types.get(RIM_TYPEHID, 0)
    return {
        "keyboard": types.get(RIM_TYPEKEYBOARD, 0),
        "mouse": types.get(RIM_TYPEMOUSE, 0),
        "hid": types.get(RIM_TYPEHID, 0),
        "other": max(0, count.value - known),
        "total": count.value,
        "device_names_retained": False,
        "device_handles_retained": False,
    }


class Win32RawInputSource:
    """Receive keyboard and mouse Raw Input on a message-only window."""

    def __init__(self):
        self.counter = RawInputCounter()
        self._thread = None
        self._ready = threading.Event()
        self._hwnd = None
        self._hinstance = None
        self._class_name = ""
        self._wndproc = None
        self._class_registered = False
        self._window_created = False
        self._devices_registered = False
        self._callback_error_count = 0
        self._last_error = ""

    def _read_packet(self, handle) -> None:
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        if user32.GetRawInputData(handle, RID_INPUT, None, ctypes.byref(size), header_size) == 0xFFFFFFFF:
            raise OSError("GetRawInputData size query failed")
        if not size.value:
            return
        buffer = (ctypes.c_byte * size.value)()
        copied = user32.GetRawInputData(handle, RID_INPUT, buffer, ctypes.byref(size), header_size)
        if copied == 0xFFFFFFFF or copied != size.value:
            raise OSError("GetRawInputData payload read failed")
        packet = ctypes.cast(buffer, ctypes.POINTER(RAWINPUT)).contents
        device = packet.header.hDevice or 0
        if packet.header.dwType == RIM_TYPEKEYBOARD:
            self.counter.record_keyboard(
                packet.keyboard.Message,
                packet.keyboard.MakeCode,
                packet.keyboard.Flags,
                device,
                packet.keyboard.ExtraInformation,
            )
        elif packet.header.dwType == RIM_TYPEMOUSE:
            self.counter.record_mouse(
                packet.mouse.usButtonFlags,
                device,
                packet.mouse.ulExtraInformation,
            )
        else:
            self.counter.record_hid(device)

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == WM_INPUT:
            try:
                self._read_packet(lparam)
            except Exception as exc:
                self._callback_error_count += 1
                self._last_error = type(exc).__name__
            return 0
        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _register_devices(self, flags: int, target) -> bool:
        devices = (RAWINPUTDEVICE * 2)(
            RAWINPUTDEVICE(HID_USAGE_PAGE_GENERIC, HID_USAGE_GENERIC_KEYBOARD, flags, target),
            RAWINPUTDEVICE(HID_USAGE_PAGE_GENERIC, HID_USAGE_GENERIC_MOUSE, flags, target),
        )
        return bool(user32.RegisterRawInputDevices(devices, len(devices), ctypes.sizeof(RAWINPUTDEVICE)))

    def _run(self) -> None:
        if os.name != "nt":
            self._last_error = "WindowsRequired"
            self._ready.set()
            return
        self._class_name = f"InteractionRhythmRawInput{os.getpid()}_{id(self):x}"
        self._hinstance = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._window_proc)
        try:
            window_class = WNDCLASSW()
            window_class.lpfnWndProc = self._wndproc
            window_class.hInstance = self._hinstance
            window_class.lpszClassName = self._class_name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                self._last_error = "RegisterClassW"
                return
            self._class_registered = True
            hwnd_message = wintypes.HWND(-3)
            self._hwnd = user32.CreateWindowExW(
                0, self._class_name, self._class_name, 0,
                0, 0, 0, 0, hwnd_message, None, self._hinstance, None,
            )
            if not self._hwnd:
                self._last_error = "CreateWindowExW"
                return
            self._window_created = True
            self._devices_registered = self._register_devices(RIDEV_INPUTSINK, self._hwnd)
            if not self._devices_registered:
                self._last_error = "RegisterRawInputDevices"
                return
            self._ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._ready.set()
            if self._devices_registered:
                self._register_devices(RIDEV_REMOVE, None)
            self._devices_registered = False
            if self._hwnd and user32.IsWindow(self._hwnd):
                user32.DestroyWindow(self._hwnd)
            self._hwnd = None
            if self._class_registered:
                user32.UnregisterClassW(self._class_name, self._hinstance)
            self._class_registered = False

    def start(self, timeout: float = 2.0) -> bool:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.healthy

    def stop(self, timeout: float = 2.0) -> bool:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)
        return not bool(self._thread and self._thread.is_alive())

    @property
    def healthy(self) -> bool:
        return bool(
            self._window_created
            and self._devices_registered
            and self._hwnd
            and self._thread
            and self._thread.is_alive()
        )

    @property
    def stats(self) -> dict:
        return {
            "window_kind": "message_only",
            "window_created": self._window_created,
            "window_active": bool(self._hwnd),
            "class_registered": self._class_registered,
            "devices_registered": self._devices_registered,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "callback_error_count": self._callback_error_count,
            "last_error": self._last_error,
            "healthy": self.healthy,
            "counts": self.counter.snapshot,
        }
