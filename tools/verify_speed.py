# -*- coding: utf-8 -*-
"""verify_speed.py — 验证变速热键：F10 加速 / F9 减速 / F11 原速"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screenshot as ss

user32 = ctypes.WinDLL('user32', use_last_error=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # server/
APP = os.path.join(os.path.dirname(ROOT), 'client',
                   'bin', 'Release', 'net10.0-windows', 'RhythmClient.exe')

VK_F9, VK_F10, VK_F11, VK_ALT = 0x78, 0x79, 0x7A, 0xA4


def key(vk, n=1):
    for _ in range(n):
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.04)
        user32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.12)


def shot(tag):
    hwnd = ss.find_window('节奏覆盖层')
    if not hwnd:
        print(f'[{tag}] 未找到客户端窗口')
        return
    data, w, h = ss.grab_window_by_hwnd(hwnd)
    out = os.path.join(ROOT, f'speed_{tag}.png')
    ss.write_png(out, w, h, data)
    print(f'[{tag}] 已保存 {out}')


def main():
    subprocess.run(['taskkill', '/F', '/IM', 'RhythmClient.exe'], capture_output=True)
    time.sleep(1)
    subprocess.Popen([APP])
    time.sleep(5)
    shot('0_初始')

    key(VK_F10, 4)      # +5% x4 → 120%
    time.sleep(1)
    shot('1_加速4次')

    key(VK_F9, 2)       # -5% x2 → 110%
    time.sleep(1)
    shot('2_减速2次')

    key(VK_F11)         # 原速 100%
    time.sleep(1)
    shot('3_原速')

    print('完成：请查看 100 / 120 / 110 / 原速 四张状态栏截图')


if __name__ == '__main__':
    main()
