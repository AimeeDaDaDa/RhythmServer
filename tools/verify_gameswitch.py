# -*- coding: utf-8 -*-
r"""verify_gameswitch.py — 验证「游戏」下拉手动切换（不抢前台）

做法：客户端窗口用 PostMessage 投递鼠标点击（不激活窗口），
点开「游戏」下拉并选择第二项（永劫无间），然后检查 settings.json 的 GameId 是否变更。
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screenshot as ss

user32 = ctypes.WinDLL('user32', use_last_error=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(ROOT), 'client', 'bin', 'Release',
                   'net10.0-windows', 'RhythmClient.exe')
APPDATA = os.path.join(os.environ['APPDATA'], 'RhythmOverlay')

WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
MK_LBUTTON = 0x0001

# 「游戏」下拉框在客户端内的坐标（按 880x700 窗口布局量得）
GAMEBOX = (112, 198)
ITEM2 = (60, 34)      # 弹层内第二项（永劫无间）相对弹层的坐标


def click(hwnd, x, y):
    lp = (y << 16) | (x & 0xFFFF)
    user32.PostMessageW(hwnd, 0x0200, 0, lp)                    # WM_MOUSEMOVE
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)


def proc_windows(pid):
    """该进程的所有顶层窗口"""
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid:
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(h, cls, 256)
            r = wintypes.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            out.append((h, cls.value, r))
        return True

    user32.EnumWindows(cb, 0)
    return out


def main():
    subprocess.run(['taskkill', '/F', '/IM', 'RhythmClient.exe'], capture_output=True)
    time.sleep(1)
    os.makedirs(APPDATA, exist_ok=True)
    with open(os.path.join(APPDATA, 'settings.json'), 'w', encoding='utf-8') as f:
        json.dump({'WindowKeyword': '三角洲', 'GameOnly': True, 'GameId': 'delta'}, f)

    subprocess.Popen([APP])
    time.sleep(6)

    hwnd = ss.find_window('节奏覆盖层')
    if not hwnd:
        print('未找到客户端窗口')
        return
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    print(f'客户端 hwnd={hwnd} pid={pid.value}')

    rgba = ss.grab_window_by_hwnd(hwnd)
    print(f'切换前 settings.GameId = {json.load(open(os.path.join(APPDATA,"settings.json"), encoding="utf-8"))["GameId"]}')

    # 点击「游戏」下拉 → 展开
    click(hwnd, *GAMEBOX)
    time.sleep(0.8)
    wins = proc_windows(pid.value)
    print(f'进程窗口数={len(wins)}：' + ', '.join(f'{c}@{r.right-r.left}x{r.bottom-r.top}' for _, c, r in wins))

    # 找到弹层（WPF Popup 通常类名 Popup / 尺寸较小且非主窗）
    popup = None
    for h, cls, r in wins:
        if h != hwnd and (r.right - r.left) > 40 and (r.bottom - r.top) > 20:
            popup = (h, cls, r)
            break
    if popup:
        ph, cls, r = popup
        print(f'弹层 hwnd={ph} class={cls} size={r.right-r.left}x{r.bottom-r.top} → 点击第 2 项')
        click(ph, *ITEM2)
        time.sleep(0.8)
    else:
        print('未找到弹层，改在主窗内按第二项位置点击')
        click(hwnd, GAMEBOX[0], GAMEBOX[1] + 34)

    time.sleep(1)
    after = json.load(open(os.path.join(APPDATA, 'settings.json'), encoding='utf-8'))['GameId']
    print(f'切换后 settings.GameId = {after}')
    print('判定: ' + ('✓ 手动切换生效（delta → naraka）' if after == 'naraka'
                    else f'✗ 未切换（仍为 {after}，WPF 可能忽略投递的鼠标消息）'))

    # 截图留证
    d, w, h = ss.grab_window_by_hwnd(hwnd)
    ss.write_png(os.path.join(ROOT, 'gameswitch.png'), w, h, d)
    print('已保存 gameswitch.png')


if __name__ == '__main__':
    main()
