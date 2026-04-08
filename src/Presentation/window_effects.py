from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtWidgets import QWidget


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint32),
        ("AnimationId", ctypes.c_int),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
_WCA_ACCENT_POLICY = 19


def _rgba_to_abgr(color: tuple[int, int, int, int]) -> int:
    r, g, b, a = color
    return ((a & 0xFF) << 24) | ((b & 0xFF) << 16) | ((g & 0xFF) << 8) | (r & 0xFF)


def apply_win10_acrylic(widget: QWidget, *, dark: bool = True) -> bool:
    if not hasattr(ctypes, "windll"):
        return False
    hwnd = int(widget.winId())
    if hwnd <= 0:
        return False
    try:
        user32 = ctypes.windll.user32
        set_attr = user32.SetWindowCompositionAttribute
    except Exception:
        return False

    tint = (24, 28, 36, 215) if dark else (245, 247, 250, 205)
    accent = ACCENT_POLICY()
    accent.AccentState = _ACCENT_ENABLE_ACRYLICBLURBEHIND
    accent.AccentFlags = 2
    accent.GradientColor = _rgba_to_abgr(tint)
    accent.AnimationId = 0

    data = WINDOWCOMPOSITIONATTRIBDATA()
    data.Attribute = _WCA_ACCENT_POLICY
    data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
    data.SizeOfData = ctypes.sizeof(accent)
    try:
        return bool(set_attr(wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:
        return False
