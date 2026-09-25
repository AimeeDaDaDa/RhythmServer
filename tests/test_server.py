# -*- coding: utf-8 -*-
"""服务端同步链路自测：tests/test_server.py
用法：先 python server.py，再跑本脚本（或本脚本自动拉起服务端）。
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:8321'
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = r'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe'


def req(method, path, data=None, ctype='application/json'):
    # 对路径做 URL 编码（中文 id 安全）
    from urllib.parse import quote
    path = quote(path, safe='/?=&')
    r = urllib.request.Request(BASE + path, method=method)
    if data is not None:
        r.add_header('Content-Type', ctype)
        r.data = data if isinstance(data, bytes) else json.dumps(data).encode()
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def check(cond, what):
    print(('OK  ' if cond else 'FAIL') + ' ' + what)
    if not cond:
        sys.exit(1)


def main():
    # 拉起服务端（后台）
    srv = subprocess.Popen([PY, os.path.join(ROOT, 'server.py')],
                           cwd=ROOT, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        # 健康检查
        code, _ = req('GET', '/api/health')
        check(code == 200, '健康检查')

        # 上传一首测试歌谱（JSON 形式）
        song = {'id': '同步测试歌', 'title': '同步测试曲',
                'score': '@title 同步测试曲\n@bpm 100\n1 2 3 4 5 6 7 8'}
        code, body = req('POST', '/api/upload', data=song)
        check(code == 200, f'上传 JSON 歌谱 {json.loads(body)}')

        # 列表应包含它
        code, body = req('GET', '/api/songs')
        songs = json.loads(body)
        ids = [s['id'] for s in songs]
        check('同步测试歌' in ids, '列表包含新上传歌曲')

        # 谱面 JSON
        code, body = req('GET', '/api/songs/同步测试歌/chart')
        chart = json.loads(body)
        check(code == 200 and chart['note_count'] == 8,
              f'谱面 JSON（{chart["note_count"]}音符）')
        check(chart['notes'][0]['lane'] == 0 and chart['notes'][7]['lane'] == 7,
              '轨道映射 1→lane0 ... 8→lane7')

        # 带 MIDI 的上传（base64）
        midi_b64 = base64.b64encode(b'MThd' + b'\x00' * 8).decode()
        song2 = {'id': '带midi测试', 'title': '带midi', 'score': '@bpm 120\n1 2',
                 'midi_b64': midi_b64}
        code, _ = req('POST', '/api/upload', data=song2)
        check(code == 200, '上传带 MIDI 歌谱')
        code, body = req('GET', '/api/songs/带midi测试/midi')
        check(code == 200 and body.startswith(b'MThd'), '下载 MIDI 文件')

        # 坏谱拒收
        code, _ = req('POST', '/api/upload',
                      data={'id': '坏谱测试', 'score': '@bpm 120\nq9'})
        check(code == 400, '坏谱拒收(400)')

        # 非法 id 拒收（路径穿越）
        code, _ = req('POST', '/api/upload',
                      data={'id': '../evil', 'score': '@bpm 120\n1'})
        check(code == 400, '非法 id 拒收(400)')

        # 不存在的歌曲 404
        code, _ = req('GET', '/api/songs/不存在的歌/chart')
        check(code == 404, '不存在歌曲 404')

        # 清理测试文件
        for sid in ('同步测试歌', '带midi测试', '坏谱测试', '../evil'):
            for ext in ('.txt', '.json', '.mid', '.midi'):
                p = os.path.join(ROOT, 'songs', sid + ext)
                if os.path.exists(p):
                    os.remove(p)
        # 清理上一轮遗留的 __test 前缀文件
        for name in list(os.listdir(os.path.join(ROOT, 'songs'))):
            if name.startswith('__test'):
                os.remove(os.path.join(ROOT, 'songs', name))
        print('\n服务端同步链路全部通过 ✓')
    finally:
        srv.terminate()
        srv.wait()


if __name__ == '__main__':
    main()
