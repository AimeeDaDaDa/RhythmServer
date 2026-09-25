# -*- coding: utf-8 -*-
"""screenshot.py — 屏幕截图小工具（纯标准库：ctypes 调 GDI + zlib 写 PNG）

用法：
  python screenshot.py 输出.png            # 全屏
  python screenshot.py 输出.png 左 上 宽 高  # 指定区域
"""
import ctypes
import struct
import sys
import zlib
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long),
        ('biHeight', ctypes.c_long), ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long),
        ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wintypes.DWORD * 3)]


def grab(x, y, w, h):
    """返回逐行 BGRA 字节（自上而下）"""
    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, SRCCOPY)
    data = _read_bits(mem, bmp, w, h)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, hdc)
    return data


PW_RENDERFULLCONTENT = 0x00000002


def find_windows(keywords):
    """按标题关键字（多个，任一命中）列出所有顶层窗口 → [(hwnd, title)]"""
    if isinstance(keywords, str):
        keywords = (keywords,)
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if any(k in buf.value for k in keywords):
            hits.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return hits


def find_window(keyword):
    """按标题关键字找顶层窗口 hwnd（返回第一个）"""
    hits = find_windows(keyword)
    return hits[0][0] if hits else None


def grab_hwnd(hwnd):
    """用 PrintWindow 抓指定 hwnd（支持分层/透明窗口）"""
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        raise SystemExit(f'窗口尺寸异常: {w}x{h}')

    src = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, w, h)
    old = gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    data = _read_bits(mem, bmp, w, h)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, src)
    print(f'PrintWindow={ok} 窗口 {w}x{h} @ ({r.left},{r.top})')
    return data, w, h


def grab_window(keyword):
    """按标题抓窗口"""
    hwnd = find_window(keyword)
    if not hwnd:
        raise SystemExit(f'未找到标题包含 {keyword!r} 的窗口')
    return grab_hwnd(hwnd)


def grab_window_by_hwnd(hwnd):
    """按 hwnd 抓窗口（供测试脚本使用）"""
    return grab_hwnd(hwnd)


def _read_bits(mem, bmp, w, h):
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h          # 负数 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
    return buf.raw


def write_png(path, w, h, bgra):
    """BGRA 原始像素 → PNG（8bit RGBA，filter 0）"""
    rows = []
    stride = w * 4
    for y in range(h):
        row = bgra[y * stride:(y + 1) * stride]
        rgba = bytearray(stride)
        rgba[0::4] = row[2::4]   # R
        rgba[1::4] = row[1::4]   # G
        rgba[2::4] = row[0::4]   # B
        rgba[3::4] = b'\xff' * w  # A
        rows.append(b'\x00' + bytes(rgba))
    raw = b''.join(rows)

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
           + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(png)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    out = sys.argv[1]
    if len(sys.argv) >= 6:
        x, y, w, h = (int(v) for v in sys.argv[2:6])
        write_png(out, w, h, grab(x, y, w, h))
        print(f'已保存 {out} ({w}x{h}) [屏幕区域]')
        return
    if len(sys.argv) == 3:
        data, w, h = grab_window(sys.argv[2])
        write_png(out, w, h, data)
        print(f'已保存 {out} ({w}x{h}) [窗口]')
        return
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    write_png(out, w, h, grab(0, 0, w, h))
    print(f'已保存 {out} ({w}x{h}) [全屏·不含分层窗口]')


if __name__ == '__main__':
    main()
