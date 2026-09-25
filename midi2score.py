# -*- coding: utf-8 -*-
"""MIDI → 按键谱转换器（纯标准库）

用法：
  python midi2score.py song.mid                    # 生成 song.txt
  python midi2score.py song.mid --key D --transpose 2 --track 1
  python midi2score.py --demo                      # 生成示例 MIDI 并转换

说明：
  游戏琴键只有一个八度（1~7 + 高音1），超出音域的音自动折返；
  黑键（半音）就近取整到邻近的全音；整首歌需要升/降半音时在游戏里按 半音 键。
"""
import argparse
import math
import os
import struct
import sys

SCALES = {'major': (0, 2, 4, 5, 7, 9, 11), 'minor': (0, 2, 3, 5, 7, 8, 10)}
NOTE_PC = {'C': 0, 'C#': 1, 'DB': 1, 'D': 2, 'D#': 3, 'EB': 3, 'E': 4,
           'F': 5, 'F#': 6, 'GB': 6, 'G': 7, 'G#': 8, 'AB': 8, 'A': 9,
           'A#': 10, 'BB': 10, 'B': 11}


class Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self):
        v = struct.unpack_from('>H', self.d, self.p)[0]
        self.p += 2
        return v

    def u32(self):
        v = struct.unpack_from('>I', self.d, self.p)[0]
        self.p += 4
        return v

    def raw(self, n):
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def vlq(self):
        v = 0
        for _ in range(4):
            b = self.u8()
            v = (v << 7) | (b & 0x7F)
            if not b & 0x80:
                return v
        return v


def parse_midi(data):
    """→ (tpq, tracks[[事件...]], tempos[(tick, us_per_quarter)])"""
    r = Reader(data)
    if r.raw(4) != b'MThd':
        raise ValueError('不是有效的 MIDI 文件')
    r.u32()
    r.u16()  # format
    r.u16()  # ntrks
    div = r.u16()
    if div & 0x8000:
        raise ValueError('不支持 SMPTE 时基的 MIDI 文件')
    tempos, tracks = [], []
    while r.p < len(r.d):
        cid = r.raw(4)
        clen = r.u32()
        body = r.raw(clen)
        if cid != b'MTrk':
            continue
        tr = Reader(body)
        tick, running, evs = 0, None, []
        while tr.p < len(tr.d):
            tick += tr.vlq()
            st = tr.u8()
            if st < 0x80:
                if running is None:
                    raise ValueError('MIDI 数据损坏')
                tr.p -= 1
                st = running
            elif st < 0xF0:
                running = st
            hi = st & 0xF0
            if st == 0xFF:
                mt, ml = tr.u8(), tr.vlq()
                md = tr.raw(ml)
                if mt == 0x51 and ml >= 3:
                    tempos.append((tick, (md[0] << 16) | (md[1] << 8) | md[2]))
            elif st in (0xF0, 0xF7):
                tr.p += tr.vlq()
            elif hi in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                a, b = tr.u8() & 0x7F, tr.u8() & 0x7F
                if hi == 0x90 and b > 0:
                    evs.append((tick, 'on', (st & 0x0F, a, b)))
                elif hi == 0x80 or (hi == 0x90 and b == 0):
                    evs.append((tick, 'off', (st & 0x0F, a)))
            elif hi in (0xC0, 0xD0):
                tr.u8()
        tracks.append(evs)
    if not tempos:
        tempos = [(0, 500000)]
    tempos.sort()
    return div, tracks, tempos


def _tick2sec_fn(tempos, tpq):
    segs, acc = [], 0.0
    pts = list(tempos) + [(None, tempos[-1][1])]
    for i in range(len(pts) - 1):
        tick, us = pts[i]
        nt = pts[i + 1][0]
        segs.append((tick, nt, us, acc))
        if nt is not None:
            acc += (nt - tick) * us / 1e6 / tpq

    def f(tick):
        for a, b, us, base in segs:
            if b is None or tick <= b:
                return base + (max(tick, a) - a) * us / 1e6 / tpq
        return base
    return f


def _convert(data, key='C', transpose=0, quant=4, scale='major',
             track='all', drums='skip'):
    tpq, tracks, tempos = parse_midi(data)
    sec = _tick2sec_fn(tempos, tpq)
    tones = SCALES[scale]
    tonic = NOTE_PC[key.upper()] + transpose
    grid_ticks = tpq / float(quant)

    sel = None
    if str(track).lower() != 'all':
        sel = {int(x) for x in str(track).split(',') if x.strip()}

    raw_notes = []
    for ti, evs in enumerate(tracks):
        if sel is not None and ti not in sel:
            continue
        active = {}
        for tick, kind, payload in evs:
            ch = payload[0]
            if drums == 'skip' and ch == 9:      # GM 打击乐通道
                continue
            if kind == 'on':
                active.setdefault((ch, payload[1]), []).append((tick, payload[2]))
            elif kind == 'off':
                q = active.get((ch, payload[1]))
                if q:
                    on_tick, vel = q.pop(0)
                    raw_notes.append((on_tick, tick, payload[1], vel))
    if not raw_notes:
        raise ValueError('没有可用音符（试试 --track all 或 --drums keep）')

    bpm_out = 60e6 / tempos[0][1] * quant       # 1 格 = 1 拍

    # ---- 中音八度定位：游戏只有 8 键 + 两个八度修饰键（共 3 个八度） ----
    pitches = [n for _, _, n, _ in raw_notes]
    lo, hi = min(pitches), max(pitches)
    oct_lo = math.ceil((hi - 23 - tonic) / 12)     # 上界：最高音不超过 高音区
    oct_hi = math.floor((lo + 12 - tonic) / 12)    # 下界：最低音不低于 低音区
    squeezed = oct_lo > oct_hi
    if squeezed:                                   # 超过 3 个八度，只能就近压缩
        base_oct = oct_hi
    else:
        mid = (lo + hi) / 2
        base_oct = min(oct_hi, max(oct_lo, round((mid - tonic) / 12)))
    ref = tonic + 12 * base_oct                    # 中音 1 的实际音高

    def token_of(note):
        """MIDI 音高 → 简谱记号：1 / 1' 高音 / 1_ 低音 / #1 半音"""
        diff = note - ref
        oct_diff = int(math.floor(diff / 12.0 + 0.5))
        rel = diff - oct_diff * 12
        rel = ((rel % 12) + 12) % 12
        best = min(tones, key=lambda x: (abs(rel - x), x))
        degree = tones.index(best) + 1
        sharp = (rel != best)
        oct_diff = max(-1, min(1, oct_diff))       # 超出 ±1 八度时压缩（音域过宽）
        suffix = "'" if oct_diff > 0 else ("_" if oct_diff < 0 else "")
        return f"{'#' if sharp else ''}{degree}{suffix}"

    groups = {}
    end_tick = 0
    for on_tick, off_tick, note, vel in raw_notes:
        end_tick = max(end_tick, off_tick)
        g0 = int(round(on_tick / grid_ticks))
        g1 = max(g0 + 1, int(round(off_tick / grid_ticks)))
        groups.setdefault(g0, []).append((token_of(note), vel, g1 - g0))

    cells = []
    for g in sorted(groups):
        dedup = {}
        for d, vel, dur in groups[g]:           # 同记号去重，保留力度大的
            if d not in dedup or vel > dedup[d][0]:
                dedup[d] = (vel, dur)
        toks = sorted(d for d, _ in dedup.items())[:7]
        cells.append((g, toks, max(dur for _, dur in dedup.values())))
    # 重叠截短：后一个音起点早于前一个结尾时，截短前一个的延音（保节奏优先）
    for i in range(len(cells) - 1):
        g, degs, dur = cells[i]
        ng = cells[i + 1][0]
        if g + dur > ng:
            cells[i] = (g, degs, max(1, ng - g))

    tokens, cur = [], 0
    for g, degs, dur in cells:
        if g > cur:
            gap = g - cur
            tokens.append('0' if gap == 1 else f'0*{gap}')
        body = ('[' + ' '.join(str(d) for d in degs) + ']') if len(degs) > 1 else str(degs[0])
        tokens.append(body if dur == 1 else f'{body}*{dur}')
        cur = g + dur

    info = {'notes': len(raw_notes),
            'chords': sum(1 for _, d, _ in cells if len(d) > 1),
            'bpm': int(round(bpm_out)), 'quant': quant,
            'sec': sec(end_tick)}
    return tokens, info


def convert_path_data(data, title='score', **kw):
    tokens, info = _convert(data, **kw)
    lines, line = [], []
    for t in tokens:
        line.append(t)
        if len(line) == 16:
            lines.append(' '.join(line))
            line = []
    if line:
        lines.append(' '.join(line))
    header = (f'@title {title}\n@bpm {info["bpm"]}\n'
              f'// 由 MIDI 转换：1拍=1/{info["quant"]} 四分音符；'
              f'1\'=高音(按住右键) 1_=低音(按住左键) #1=升半音(按住中键)\n')
    return header + '\n'.join(lines) + '\n', info


# ---------------- 示例 MIDI（小星星片段） ----------------
def _vlq(n):
    b = [n & 0x7F]
    n >>= 7
    while n:
        b.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(b))


def demo_midi():
    seq = [(60, 480), (60, 480), (67, 480), (67, 480),
           (69, 480), (69, 480), (67, 960)]
    tr = bytearray()
    tr += _vlq(0) + b'\xFF\x51\x03' + (500000).to_bytes(3, 'big')
    first = True
    for note, dur in seq:
        tr += _vlq(0 if first else 10) + b'\x90' + bytes([note, 100])
        tr += _vlq(dur - 10) + b'\x80' + bytes([note, 0])
        first = False
    tr += _vlq(0) + b'\xFF\x2F\x00'
    return (b'MThd' + struct.pack('>IHHH', 6, 0, 1, 480)
            + b'MTrk' + struct.pack('>I', len(tr)) + bytes(tr))


def main():
    ap = argparse.ArgumentParser(description='MIDI → 按键谱转换器')
    ap.add_argument('midi', nargs='?', help='MIDI 文件路径')
    ap.add_argument('-o', '--out', default=None, help='输出 txt 路径')
    ap.add_argument('--key', default='C', help='调性，如 C / D / bB(即 A#)')
    ap.add_argument('--transpose', type=int, default=0, help='整体移调（半音数）')
    ap.add_argument('--quant', type=int, default=4,
                    choices=[1, 2, 3, 4, 6, 8, 12],
                    help='量化精度：每四分音符几格（4=十六分音符）')
    ap.add_argument('--scale', default='major', choices=['major', 'minor'])
    ap.add_argument('--track', default='all', help="选轨道：all 或编号如 0,1")
    ap.add_argument('--drums', default='skip', choices=['skip', 'keep'])
    ap.add_argument('--demo', action='store_true', help='生成示例 MIDI 并转换')
    a = ap.parse_args()

    if a.demo:
        path = 'demo.mid'
        with open(path, 'wb') as f:
            f.write(demo_midi())
        print(f'已生成示例 {path}')
    else:
        if not a.midi:
            ap.error('需要 MIDI 路径或 --demo')
        path = a.midi
    with open(path, 'rb') as f:
        data = f.read()
    title = os.path.splitext(os.path.basename(path))[0]
    try:
        text, info = convert_path_data(data, title, key=a.key,
                                       transpose=a.transpose, quant=a.quant,
                                       scale=a.scale, track=a.track,
                                       drums=a.drums)
    except ValueError as e:
        sys.exit(f'转换失败：{e}')
    out = a.out or (os.path.splitext(path)[0] + '.txt')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f'已生成 {out}')
    print(f"音符 {info['notes']} 个 / 和弦 {info['chords']} 个 / "
          f"时长 {info['sec']:.1f}s / 谱面 BPM {info['bpm']}"
          f"（游戏内速度 100% 即原速）")


if __name__ == '__main__':
    main()
