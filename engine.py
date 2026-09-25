# -*- coding: utf-8 -*-
"""engine.py — 游戏自动演奏核心（零第三方依赖，Windows）

乐谱格式（txt 简谱，首调记法）：
  1-7     → 键位映射（默认 Z X C V B N M，中音）
  1'      → 高音（游戏里按住鼠标右键）
  1_      → 低音（按住鼠标左键）
  #1      → 升半音（按住鼠标中键），可与八度组合：#1'
  8 / i   → 高音1（逗号键）
  0       → 休止符
  5*2     → 该音延 2 拍（5*0.5 = 半拍）
  [1 3 5] → 和弦（同时按下），[1 5]*2 → 和弦延 2 拍
  // 注释   | 小节线（仅视觉分隔）
  @bpm 120 @title 歌名
"""
import os
import re
import time
import atexit
import random
import threading
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)

try:
    winmm.timeBeginPeriod(1)  # 系统计时精度提升到 1ms
    atexit.register(lambda: winmm.timeEndPeriod(1))
except Exception:
    pass

# ---------------- 键位表（Set-1 扫描码） ----------------
SCAN = {
    '1': 0x02, '2': 0x03, '3': 0x04, '4': 0x05, '5': 0x06, '6': 0x07,
    '7': 0x08, '8': 0x09, '9': 0x0A, '0': 0x0B,
    'q': 0x10, 'w': 0x11, 'e': 0x12, 'r': 0x13, 't': 0x14, 'y': 0x15,
    'u': 0x16, 'i': 0x17, 'o': 0x18, 'p': 0x19,
    'a': 0x1E, 's': 0x1F, 'd': 0x20, 'f': 0x21, 'g': 0x22, 'h': 0x23,
    'j': 0x24, 'k': 0x25, 'l': 0x26,
    'z': 0x2C, 'x': 0x2D, 'c': 0x2E, 'v': 0x2F, 'b': 0x30, 'n': 0x31, 'm': 0x32,
    ';': 0x27, "'": 0x28, ',': 0x33, '.': 0x34, '/': 0x35,
    '[': 0x1A, ']': 0x1B, '\\': 0x2B, '-': 0x0C, '=': 0x0D, '`': 0x29, ' ': 0x39,
}
_OEM_VK = {';': 0xBA, "'": 0xDE, ',': 0xBC, '.': 0xBE, '/': 0xBF, '[': 0xDB,
           ']': 0xDD, '\\': 0xDC, '-': 0xBD, '=': 0xBB, '`': 0xC0, ' ': 0x20}
VK = {ch: (ord(ch.upper()) if ch.isalnum() else _OEM_VK[ch]) for ch in SCAN}

# 三角洲行动默认键位：Z X C V B N M + 逗号(高音1)
DEFAULT_KEYMAP = {'1': 'z', '2': 'x', '3': 'c', '4': 'v',
                  '5': 'b', '6': 'n', '7': 'm', '8': ','}


# ---------------- 按键引擎 ----------------
_PUL = ctypes.POINTER(wintypes.ULONG)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _PUL)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _PUL)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _IU(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("iu", _IU)]


KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002


class Keyer:
    """按键器：默认发送扫描码（绝大多数游戏可识别），dry 模式只记录不发送。
    支持鼠标修饰键：_ 左键(低音)  ' 右键(高音)  # 中键(半音)"""

    MOUSE_DOWN = {'_': 0x0002, "'": 0x0008, '#': 0x0020}   # 左/右/中 按下
    MOUSE_UP = {'_': 0x0004, "'": 0x0010, '#': 0x0040}     # 左/右/中 抬起

    def __init__(self, dry=False, use_vk=False, jitter_ms=0.0):
        self.dry = dry
        self.use_vk = use_vk
        self.jitter_ms = max(0.0, float(jitter_ms))
        self.held = set()

    def _send(self, key, down):
        if self.dry:
            return 1
        inp = _INPUT()
        inp.type = 1  # INPUT_KEYBOARD
        ki = inp.iu.ki
        ki.dwExtraInfo = None
        if self.use_vk:
            ki.wVk = VK[key]
            ki.dwFlags = 0 if down else KEYEVENTF_KEYUP
        else:
            ki.wScan = SCAN[key]
            ki.dwFlags = (KEYEVENTF_SCANCODE if down
                          else KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
        return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def press(self, key, down):
        """key 形如 'z' / "z'" 高音 / 'z_' 低音 / '#z' 半音；修饰=按住鼠标键"""
        if down:
            self.held.add(key)
        else:
            self.held.discard(key)
        base, mods = key[0], key[1:]
        order = [c for c in "_'#" if c in mods]
        if down:
            for c in order:                       # 先按住修饰键
                self._send_mouse(self.MOUSE_DOWN[c])
            return self._send(base, True)         # 再敲键盘
        r = self._send(base, False)               # 先松开键盘
        for c in reversed(order):                 # 再松开修饰键
            self._send_mouse(self.MOUSE_UP[c])
        return r

    def _send_mouse(self, flag):
        if self.dry:
            return 1
        inp = _INPUT()
        inp.type = 0  # INPUT_MOUSE
        inp.iu.mi.dwFlags = flag
        return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def release_all(self):
        for k in list(self.held):
            self.press(k, False)


# ---------------- 乐谱解析 ----------------
_TOKEN = re.compile(
    r'@\w+\s+[^\s]+'
    r'|\[[^\]]*\](?:\*[0-9]+(?:\.[0-9]+)?)?'   # 和弦（含 *n 后缀整体匹配）
    r'|[^\s\[\]|]+')                          # 普通记号（允许 # 作升号）
_DUR = re.compile(r'([^*]+?)(?:\*([0-9]+(?:\.[0-9]+)?))?$')
GAP_BEATS = 0.06  # 同键两次触发的最小间隔（拍）


def _note_of(base, keymap, lineno):
    """解析简谱记号（含修饰）→ 伪键名
    1' 高音(右键)  1_ 低音(左键)  #1 半音(中键)，可组合：#1'
    """
    t, sharp, high, low = base, False, False, False
    if t.startswith('#'):
        sharp, t = True, t[1:]
    if t.endswith("'"):
        high, t = True, t[:-1]
    elif t.endswith('_'):
        low, t = True, t[:-1]
    if not t:
        raise ValueError(f"第{lineno}行：记号不完整「{base}」")
    key = _key_of(t, keymap, lineno)
    mods = ("'" if high else "") + ("_" if low else "")
    return key + ("#" + mods if sharp else mods)


def _key_of(tok, keymap, lineno):
    t = tok.lower()
    if t == 'i':
        t = '8'
    if t in keymap:
        t = keymap[t]
    if t in SCAN:
        return t
    raise ValueError(f"第{lineno}行：未知记号「{tok}」")


def parse_score(text, keymap=None):
    """解析乐谱文本 → dict(title,bpm,events,length_beats,note_count)

    events: [(beat, kind, key)]，kind: 1=按下 0=抬起，按节拍排序。
    """
    km = dict(DEFAULT_KEYMAP) if keymap is None else dict(keymap)
    bpm, title = 120.0, ''
    notes, beat, errors = [], 0.0, []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split('//', 1)[0]
        for m in _TOKEN.finditer(line):
            tok = m.group(0)
            if tok.startswith('@'):
                parts = tok[1:].split(None, 1)
                if len(parts) == 2:
                    name, val = parts[0].lower(), parts[1].strip()
                    if name == 'bpm':
                        try:
                            bpm = float(val)
                        except ValueError:
                            errors.append(f"第{lineno}行：@bpm 无效")
                    elif name == 'title':
                        title = val
                continue
            dm = _DUR.match(tok)
            if dm is None:
                errors.append(f"第{lineno}行：记号无效「{tok}」")
                continue
            base, mult = dm.group(1), dm.group(2)
            dur = float(mult) if mult else 1.0
            if dur <= 0:
                errors.append(f"第{lineno}行：时值无效「{tok}」")
                continue
            if base == '0':
                beat += dur
                continue
            try:
                if base.startswith('['):
                    inner = base[1:-1].split()
                    if not inner:
                        continue
                    keys = [_note_of(t, km, lineno) for t in inner]
                else:
                    keys = [_note_of(base, km, lineno)]
            except ValueError as e:
                errors.append(str(e))
                continue
            notes.append((beat, keys, dur))
            beat += dur
    if errors:
        raise ValueError('；'.join(errors[:5]))
    if not notes:
        raise ValueError('乐谱为空：没有找到任何音符')

    # 生成 down/up 事件；同键相邻音重叠时裁剪前一个的延音
    per_key = {}
    for start, keys, dur in notes:
        for k in keys:
            lst = per_key.setdefault(k, [])
            if lst:
                ps, pd = lst[-1]
                if ps + pd > start - GAP_BEATS:
                    new_dur = (start - GAP_BEATS) - ps
                    lst[-1] = (ps, new_dur if new_dur > 1e-6 else 1e-6)
            lst.append((start, dur))
    events = []
    for k, lst in per_key.items():
        for s, d in lst:
            events.append((s, 1, k))
            events.append((s + d, 0, k))
    events.sort(key=lambda e: (e[0], e[1]))
    total = max([s + d for lst in per_key.values() for s, d in lst] + [beat])
    return {'title': title, 'bpm': bpm, 'events': events,
            'length_beats': total,
            'note_count': sum(len(ks) for _, ks, _ in notes)}


def load_score_file(path, keymap=None):
    with open(path, 'rb') as f:
        data = f.read()
    for enc in ('utf-8-sig', 'gbk'):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode('utf-8', 'replace')
    score = parse_score(text, keymap)
    if not score['title']:
        score['title'] = os.path.splitext(os.path.basename(path))[0]
    score['path'] = path
    return score


# ---------------- 播放调度 ----------------
class Player:
    """以「拍」为唯一时间基准；暂停/变速时冻结换算，随时可变。"""

    def __init__(self, keyer=None, on_event=None):
        self.keyer = keyer or Keyer()
        self.on_event = on_event
        self.loop = False
        self.delay = 1.0        # 起手延迟（秒）
        self.speed = 100.0      # 10 ~ 400
        self.state = 'idle'     # idle / playing / paused
        self.msg = '就绪'
        self._score = None
        self._idx = 0
        self._beats = 0.0       # anchor 之前已播的拍数
        self._anchor = 0.0      # perf_counter 基准点
        self._lock = threading.RLock()
        self._thread = None

    # ---- 内部 ----
    def _spb(self):
        return 60.0 / (self._score['bpm'] * self.speed / 100.0)

    def _reset_pos(self):
        self._idx, self._beats = 0, 0.0

    def _freeze(self):
        """把 anchor 以来的时间折算进 _beats，再重设 anchor（暂停/变速时用）"""
        elapsed = max(0.0, time.perf_counter() - self._anchor)
        self._beats += elapsed / self._spb()
        self._anchor = time.perf_counter()

    # ---- 控制 ----
    def load(self, score):
        with self._lock:
            self._score = score
            self._reset_pos()
            if self.state != 'playing':
                self.state = 'idle'

    def play(self):
        with self._lock:
            if self._score is None:
                self.msg = '未加载乐谱'
                return False
            if self.state == 'playing':
                return True
            if self.state != 'paused':          # 从头开始（含起手延迟）
                self._reset_pos()
                self._anchor = time.perf_counter() + max(0.0, self.delay)
            else:                                # 暂停恢复
                self._anchor = time.perf_counter()
            self.state = 'playing'
            self.msg = '播放中'
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
            return True

    def pause(self):
        with self._lock:
            if self.state == 'playing':
                self._freeze()
                self.state = 'paused'
                self.msg = '已暂停'
                self.keyer.release_all()

    def toggle(self):
        if self.state == 'playing':
            self.pause()
        else:
            self.play()

    def stop(self):
        with self._lock:
            if self.state == 'playing':
                self._freeze()
            self.state = 'idle'
            self.msg = '已停止'
            self._reset_pos()
        self.keyer.release_all()

    def set_speed(self, v):
        with self._lock:
            v = min(400.0, max(10.0, float(v)))
            if self.state == 'playing':
                self._freeze()
            self.speed = v
            self.msg = f'速度 {v:.0f}%'

    def seek(self, event_idx):
        with self._lock:
            if self._score is None:
                return
            evs = self._score['events']
            i = min(max(0, int(event_idx)), len(evs))
            self._idx = i
            self._beats = evs[i][0] if i < len(evs) else self._score['length_beats']
            if self.state != 'playing':
                self._anchor = time.perf_counter()

    # ---- 工作线程 ----
    def _wait_until(self, ev_beat):
        """等到指定拍；暂停/停止可随时打断。返回 False 表示停止。"""
        while True:
            paused = False
            with self._lock:
                if self.state == 'idle':
                    return False
                if self.state == 'paused':
                    paused = True
                else:
                    target = self._anchor + (ev_beat - self._beats) * self._spb()
            if paused:
                self.keyer.release_all()
                time.sleep(0.03)
                continue
            rem = target - time.perf_counter()
            if rem <= 0:
                return True
            if rem > 0.03:
                time.sleep(min(rem - 0.02, 0.06))
            # 最后 30ms 自旋等待，保证节拍精度

    def _run(self):
        try:
            while True:
                with self._lock:
                    if self.state != 'playing' or self._score is None:
                        break
                    evs = self._score['events']
                    if self._idx >= len(evs):
                        if self.loop:
                            self._reset_pos()
                            self._anchor = time.perf_counter()
                            continue
                        self.state = 'idle'
                        self.msg = '播放结束'
                        break
                    ev = evs[self._idx]
                    idx = self._idx
                if not self._wait_until(ev[0]):
                    break
                with self._lock:
                    if self.state != 'playing':
                        break
                    self._idx = idx + 1
                    self._dispatch(ev)
        finally:
            self.keyer.release_all()

    def _dispatch(self, ev):
        _, kind, key = ev
        if self.keyer.jitter_ms > 0 and kind == 1:
            time.sleep(random.uniform(0, self.keyer.jitter_ms) / 1000.0)
        self.keyer.press(key, kind == 1)
        if self.on_event:
            try:
                self.on_event(key, kind == 1)
            except Exception:
                pass

    def status(self):
        with self._lock:
            s = self._score
            st = {'state': self.state, 'speed': self.speed, 'loop': self.loop,
                  'msg': self.msg, 'title': '', 'bpm': 0.0, 'idx': self._idx,
                  'total': 0, 'cur': 0.0, 'cur_sec': 0.0, 'total_sec': 0.0,
                  'notes': 0}
            if s is None:
                return st
            spb = self._spb()
            st.update(title=s.get('title', ''), bpm=s['bpm'],
                      total=len(s['events']), notes=s['note_count'],
                      total_sec=s['length_beats'] * spb)
            if self.state == 'playing':
                cur = self._beats + (time.perf_counter() - self._anchor) / spb
            else:
                cur = self._beats
            st['cur'] = max(0.0, min(cur, s['length_beats']))
            st['cur_sec'] = st['cur'] * spb
            return st


# ---------------- 全局热键 ----------------
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_FKEYS = [(1, 0x77, 'F8'), (2, 0x78, 'F9'), (3, 0x79, 'F10'), (4, 0x7A, 'F11')]


def start_hotkeys(player, on_msg=None):
    """F8 播放/暂停  F9 停止  F10 减速-5%  F11 加速+5%（全局，游戏内可用）"""
    def _loop():
        registered = []
        for i, vk, name in _FKEYS:
            if user32.RegisterHotKey(None, i, _MOD_NOREPEAT, vk):
                registered.append(i)
            elif on_msg:
                on_msg(f'{name} 热键注册失败（可能被其他程序占用）')
        msg = wintypes.MSG()
        while registered and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == _WM_HOTKEY:
                if msg.wParam == 1:
                    player.toggle()
                elif msg.wParam == 2:
                    player.stop()
                elif msg.wParam == 3:
                    player.set_speed(player.speed - 5)
                elif msg.wParam == 4:
                    player.set_speed(player.speed + 5)
        for i in registered:
            user32.UnregisterHotKey(None, i)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


# ---------------- 窗口前置 ----------------
def focus_window(title_part):
    """把标题包含 title_part 的窗口调到前台（忽略大小写），成功返回 True"""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if title_part.lower() in buf.value.lower():
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(_cb, 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.keybd_event(0xA4, 0, 0, 0)      # ALT 按下，绕过前台窗口锁
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0xA4, 0, 2, 0)      # ALT 抬起
    return True
