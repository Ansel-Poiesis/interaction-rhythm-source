"""Windows user-scoped DPAPI storage. No plaintext fallback."""
from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


def _crypt(data: bytes, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise ValueError("邮箱凭据保护需要 Windows 当前用户环境")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer)
    target = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError("无法读取或保护邮箱配置，请在原 Windows 用户下重新配置")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def protect(value: str) -> str:
    return base64.b64encode(_crypt(value.encode("utf-8"), False)).decode("ascii")


def unprotect(value: str) -> str:
    return _crypt(base64.b64decode(value, validate=True), True).decode("utf-8")
