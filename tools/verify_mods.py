# -*- coding: utf-8 -*-
"""verify_mods.py — 验证简谱修饰键（八度/半音）解析与谱面输出"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import engine
import server

LBL = {"'": "高音(右键)", "_": "低音(左键)", "#": "半音(中键)"}


def main():
    sc = engine.load_score_file(os.path.join(ROOT, 'songs', '八度示例.txt'))
    print(f"歌词本音符 {sc['note_count']} 个 | BPM {sc['bpm']} | "
          f"{sc['length_beats']:.0f} 拍")

    chart = server.chart_json(sc, '八度示例')
    print(f"谱面音符 {chart['note_count']} 个：")
    for n in chart['notes'][:16]:
        mod = n.get('mod', '')
        desc = ' + '.join(LBL[c] for c in "_'#" if c in mod) or '中音'
        print(f"  lane={n['lane']:<2} mod={mod or '-':<3} t={n['t']:>5.2f}  → {desc}")

    # 断言：谱面里应同时出现 高音/低音/半音 三类修饰
    mods = {n.get('mod', '') for n in chart['notes']}
    has = lambda ch: any(ch in m for m in mods)
    ok = has("'") and has('_') and has('#')
    print('\n修饰键覆盖: 高音=%s 低音=%s 半音=%s' %
          (has("'"), has('_'), has('#')))
    print('判定: ' + ('✓ 简谱八度/半音修饰解析正确' if ok else '✗ 修饰解析缺失'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
