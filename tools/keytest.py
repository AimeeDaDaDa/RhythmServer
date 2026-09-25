# -*- coding: utf-8 -*-
"""keytest.py — 确定性验证：自动演奏的按键是否真的送达前台窗口

做法：自建一个带多行输入框(EDIT)的测试窗口 → 置前并对焦 → 让客户端开始演奏
      → 抽消息循环处理收到的按键 → 读回输入框文本并打印
用法：python tools/keytest.py [等待秒数]
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
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # server/
APP = os.path.join(os.path.dirname(ROOT), 'client',
                   'bin', 'Release', 'net10.0-windows', 'RhythmClient.exe')
APPDATA = os.path.join(os.environ['APPDATA'], 'RhythmOverlay')

VK_F8, VK_F9, VK_ALT = 0x77, 0x78, 0xA4
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD, WS_VISIBLE, WS_BORDER = 0x40000000, 0x10000000, 0x00800000
ES_MULTILINE, ES_AUTOVSCROLL = 0x0004, 0x0040
PM_REMOVE = 1

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.restype = wintypes.HWND

# 收到的消息计数（验证鼠标修饰键 + 键盘）
STATS = {'L': 0, 'R': 0, 'M': 0, 'char': 0, 'text': []}


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# ---- 全局鼠标钩子：统计注入的鼠标按键（验证修饰键） ----
WH_MOUSE_LL = 14
WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN = 0x0201, 0x0204, 0x0207
LLMHF_INJECTED = 0x00000001


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


LRESULT = ctypes.c_ssize_t
MOUSEHOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

MOUSE_STATS = {'L': 0, 'R': 0, 'M': 0, 'inh_L': 0, 'inh_R': 0, 'inh_M': 0}


def _mouse_proc(n_code, w_param, l_param):
    if n_code >= 0:
        info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        injected = bool(info.flags & LLMHF_INJECTED)
        msg = w_param & 0xFFFFFFFF
        name = {WM_LBUTTONDOWN: 'L', WM_RBUTTONDOWN: 'R', WM_MBUTTONDOWN: 'M'}.get(msg)
        if name:
            MOUSE_STATS[name] += 1
            if injected:
                MOUSE_STATS['inh_' + name] += 1
    return user32.CallNextHookEx(None, n_code, w_param, l_param)


_MOUSE_PROC = MOUSEHOOKPROC(_mouse_proc)


def install_mouse_hook():
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, MOUSEHOOKPROC,
                                         wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    return user32.SetWindowsHookExW(WH_MOUSE_LL, _MOUSE_PROC,
                                    kernel32.GetModuleHandleW(None), 0)


# ---- 子类化输入框：直接统计落到输入框上的鼠标按键（最可靠） ----
GWL_WNDPROC = -4
EDIT_STATS = {'L': 0, 'R': 0, 'M': 0}
_old_edit_proc = None

WNDPROC2 = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)


def _edit_proc(hwnd, msg, wp, lp):
    if msg in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
        EDIT_STATS[{WM_LBUTTONDOWN: 'L', WM_RBUTTONDOWN: 'R',
                    WM_MBUTTONDOWN: 'M'}[msg]] += 1
    return user32.CallWindowProcW(_old_edit_proc, hwnd, msg, wp, lp)


_EDIT_PROC = WNDPROC2(_edit_proc)


def subclass_edit(edit):
    global _old_edit_proc
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                       wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallWindowProcW.restype = LRESULT
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    _old_edit_proc = user32.SetWindowLongPtrW(
        edit, GWL_WNDPROC, ctypes.cast(_EDIT_PROC, ctypes.c_void_p))


def _wndproc(hwnd, msg, wp, lp):
    if msg == 0x0201:      # WM_LBUTTONDOWN → 低音修饰
        STATS['L'] += 1
        return 0
    if msg == 0x0204:      # WM_RBUTTONDOWN → 高音修饰
        STATS['R'] += 1
        return 0
    if msg == 0x0207:      # WM_MBUTTONDOWN → 半音修饰
        STATS['M'] += 1
        return 0
    if msg == 0x0102:      # WM_CHAR
        ch = chr(wp)
        if ch.isprintable():
            STATS['char'] += 1
            STATS['text'].append(ch)
        return 0
    if msg == 0x0010:      # WM_CLOSE
        user32.DestroyWindow(hwnd)
        return 0
    if msg == 0x0002:      # WM_DESTROY
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


_WNDPROC = WNDPROC(_wndproc)


def create_target_window():
    """创建带输入框的测试窗口，返回 (主窗口, 输入框)"""
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = _WNDPROC
    wc.hInstance = hinst
    wc.hbrBackground = 6
    wc.lpszClassName = 'KeyTestTarget'
    user32.RegisterClassW(ctypes.byref(wc))

    hwnd = user32.CreateWindowExW(
        0x00000008, 'KeyTestTarget', '按键接收测试窗口', WS_OVERLAPPEDWINDOW,
        120, 120, 700, 420, None, None, hinst, None)
    edit = user32.CreateWindowExW(
        0, 'EDIT', '',
        WS_CHILD | WS_VISIBLE | WS_BORDER | ES_MULTILINE | ES_AUTOVSCROLL | 0x100000,
        10, 10, 660, 360, hwnd, 1, hinst, None)
    user32.ShowWindow(hwnd, 5)
    return hwnd, edit


def pump(seconds, watch_hwnd=None):
    """抽消息循环，让测试窗口接收按键；同时采样前台窗口归属"""
    msg = wintypes.MSG()
    end = time.time() + seconds
    seen = {}
    last_sample = 0.0
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        now = time.time()
        if watch_hwnd and now - last_sample >= 0.4:
            last_sample = now
            fg = user32.GetForegroundWindow()
            if fg != watch_hwnd:                 # 被抢走就抢回来
                force_foreground(watch_hwnd)
            b = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(fg, b, 256)
            key = (fg, b.value)
            seen[key] = seen.get(key, 0) + 1
        time.sleep(0.01)
    return seen


def self_inject_test(edit, vk=0x5A):
    """对照实验：测试脚本自己注入一次按键，验证测试窗口能收到"""
    user32.SetWindowTextW(edit, '')
    user32.SetFocus(edit)
    time.sleep(0.2)
    scan = 0x2C  # Z 的扫描码
    user32.keybd_event(vk, scan, 0x0008, 0)
    time.sleep(0.05)
    user32.keybd_event(vk, scan, 0x0008 | 0x0002, 0)
    time.sleep(0.3)
    return read_edit(edit)


def key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(vk, 0, 2, 0)
    time.sleep(0.05)


def force_foreground(hwnd):
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    user32.keybd_event(VK_ALT, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_ALT, 0, 2, 0)


def read_edit(edit):
    buf = ctypes.create_unicode_buffer(8192)
    user32.GetWindowTextW(edit, buf, 8192)
    return buf.value


def main():
    wait = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0

    subprocess.run(['taskkill', '/F', '/IM', 'RhythmClient.exe'], capture_output=True)
    time.sleep(1)

    # 任意窗口模式，让按键能进测试窗口
    os.makedirs(APPDATA, exist_ok=True)
    with open(os.path.join(APPDATA, 'settings.json'), 'w', encoding='utf-8') as f:
        json.dump({'WindowKeyword': '三角洲', 'GameOnly': False}, f)

    subprocess.Popen([APP])
    time.sleep(4)

    hwnd, edit = create_target_window()
    hook = install_mouse_hook()
    subclass_edit(edit)
    # 反复争夺前台，直到确实拿到（Windows 前台锁很顽固）
    ok = False
    for _ in range(15):
        force_foreground(hwnd)
        user32.SetFocus(edit)
        time.sleep(0.4)
        if user32.GetForegroundWindow() == hwnd:
            ok = True
            break
    print(f'测试窗口 hwnd={hwnd} 已取得前台={ok}')
    if not ok:
        print('无法取得前台，测试不可靠，放弃')
        return
    # 鼠标注入是发给「光标下的窗口」，把光标移到测试窗口内
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    user32.SetCursorPos((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    time.sleep(0.6)

    key(VK_F9)      # 停止
    time.sleep(0.3)
    key(VK_F8)      # 从头演奏
    print(f'已触发演奏，接收 {wait:.0f} 秒…')
    seen = pump(wait, watch_hwnd=hwnd)

    text = read_edit(edit)
    print(f'输入框收到 {len(text)} 个字符')
    print(f'内容预览: {text[:120]!r}')
    from collections import Counter
    c = Counter(ch.upper() for ch in text if ch.upper() in 'ZXCVBNM,')
    print(f'按键分布: {dict(c)}')
    print(f"鼠标修饰键按下次数(输入框): 左键(低音)={EDIT_STATS['L']} "
          f"右键(高音)={EDIT_STATS['R']} 中键(半音)={EDIT_STATS['M']}")
    print(f"全局鼠标钩子(注入来源): 左键={MOUSE_STATS['L']}(注入{MOUSE_STATS['inh_L']}) "
          f"右键={MOUSE_STATS['R']}(注入{MOUSE_STATS['inh_R']}) "
          f"中键={MOUSE_STATS['M']}(注入{MOUSE_STATS['inh_M']})")
    print(f"WM_CHAR 总数={STATS['char']}")

    if seen:
        print('前台窗口采样（每0.4秒）:')
        for (h, t), n in sorted(seen.items(), key=lambda kv: -kv[1]):
            print(f'    x{n}  hwnd={h} title={t!r}')
    else:
        print('无前台采样')

    # 对照实验：脚本自己注入按键，验证测试窗口本身能收键
    selftext = self_inject_test(edit)
    print(f'对照实验(脚本注入Z): 收到 {selftext!r}')

    print('判定: ' + ('✓ 按键事件确实送达前台窗口' if len(text) > 0 else '✗ 未收到客户端的按键'))

    data, w, h = ss.grab_window_by_hwnd(hwnd)
    out = os.path.join(ROOT, 'keytest_shot.png')
    ss.write_png(out, w, h, data)
    print(f'已保存 {out}')

    # 抓客户端窗口（读状态栏的「已发送 N」计数）
    cw = ss.find_window('节奏覆盖层')
    if cw:
        cdata, cwid, chei = ss.grab_window_by_hwnd(cw)
        cout = os.path.join(ROOT, 'keytest_client.png')
        ss.write_png(cout, cwid, chei, cdata)
        print(f'已保存 {cout} ({cwid}x{chei})')
    else:
        print('未找到客户端窗口（可能已退出）')

    user32.PostMessageW(hwnd, 0x0010, 0, 0)


if __name__ == '__main__':
    main()
