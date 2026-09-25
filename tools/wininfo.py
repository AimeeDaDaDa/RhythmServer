# -*- coding: utf-8 -*-
"""wininfo.py — 查顶层窗口状态（排查窗口不可见用）"""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
dwmapi = ctypes.WinDLL('dwmapi', use_last_error=True)

DWMWA_CLOAKED = 14
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080


def main():
    results = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value
        if '节奏' not in title and 'Rhythm' not in title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        cloaked = wintypes.DWORD(0)
        try:
            dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED,
                                         ctypes.byref(cloaked), 4)
        except Exception:
            pass
        results.append(
            f"hwnd={hwnd} pid={pid.value} title={title!r} "
            f"visible={bool(user32.IsWindowVisible(hwnd))} "
            f"iconic={bool(user32.IsIconic(hwnd))} "
            f"rect=({r.left},{r.top},{r.right},{r.bottom}) "
            f"size={r.right - r.left}x{r.bottom - r.top} "
            f"exstyle=0x{ex & 0xFFFFFFFF:X} "
            f"layered={bool(ex & WS_EX_LAYERED)} "
            f"transparent={bool(ex & WS_EX_TRANSPARENT)} "
            f"toolwindow={bool(ex & WS_EX_TOOLWINDOW)} "
            f"cloaked={cloaked.value}")
        return True

    user32.EnumWindows(cb, 0)
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
    print(f'屏幕: {screen_w}x{screen_h}')
    if not results:
        print('未找到「节奏/Rhythm」相关顶层窗口')
    for line in results:
        print(line)


if __name__ == '__main__':
    main()
