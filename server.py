# -*- coding: utf-8 -*-
"""server.py — 歌谱同步服务端（纯存储 + 同步中转，不做任何转换）

职责：接收客户端上传的歌谱，供多客户端之间同步（上传 + 拉取）。
转换逻辑在客户端本地完成，转换后调用 POST /api/upload 上传。

接口：
  GET    /api/songs                歌曲库列表
  GET    /api/songs/{id}/chart     下落音符时间轴 JSON（客户端渲染）
  GET    /api/songs/{id}/midi      下载 MIDI 文件（有则返回）
  POST   /api/upload               上传歌谱（multipart 或 JSON 文本 + meta）

存储：songs/ 目录，每个歌曲一个 .txt 歌谱 + 可选 .mid 源文件 + .json 元数据。
启动：python server.py  （默认 127.0.0.1:8321）
"""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

import engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SONGS_DIR = os.path.join(BASE_DIR, 'songs')
PORT = 8321

LANE_KEYS = ['z', 'x', 'c', 'v', 'b', 'n', 'm', ',']   # 轨道 0..7

# 允许上传的 id 字符集（防路径穿越/注入）
_SAFE_ID = re.compile(r'^[A-Za-z0-9\u4e00-\u9fff _\-\.]{1,120}$')


def ensure_songs_dir():
    os.makedirs(SONGS_DIR, exist_ok=True)


def valid_id(sid):
    return bool(sid) and _SAFE_ID.match(sid) and '..' not in sid


def list_songs():
    """扫描 songs/ 目录 → [{id,title,bpm,notes,has_midi,updated}]"""
    ensure_songs_dir()
    out = []
    for name in sorted(os.listdir(SONGS_DIR)):
        stem, ext = os.path.splitext(name)
        if ext.lower() != '.txt' or name.startswith('_'):
            continue
        base = os.path.join(SONGS_DIR, stem)
        meta = {}
        mp = base + '.json'
        if os.path.exists(mp):
            try:
                with open(mp, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
            except Exception:
                meta = {}
        try:
            score = engine.load_score_file(base + '.txt')
            title = score.get('title') or stem
            bpm, notes = score['bpm'], score['note_count']
        except Exception:
            title, bpm, notes = stem, None, None
        has_midi = os.path.exists(base + '.mid') or os.path.exists(base + '.midi')
        out.append({
            'id': stem,
            'title': meta.get('title') or title,
            'bpm': bpm,
            'notes': notes,
            'has_midi': has_midi,
            'updated': int(meta.get('updated') or os.path.getmtime(base + '.txt')),
        })
    out.sort(key=lambda s: -s['updated'])
    return out


def find_song(sid):
    if not valid_id(sid):
        return None
    p = os.path.join(SONGS_DIR, sid + '.txt')
    return p if os.path.exists(p) else None


def pair_events(score):
    """把 down/up 事件配对成音符 → [(lane, start_sec, dur_sec, mod)]
    key 形如 'z' / "z'" 高音 / 'z_' 低音 / '#z' 半音（修饰键按 (lane, mod) 分别配对）"""
    bpm = score['bpm'] or 120.0
    spb = 60.0 / bpm
    pending, notes = {}, []
    for beat, kind, key in score['events']:
        if not key or key[0] not in LANE_KEYS:
            continue
        lane = LANE_KEYS.index(key[0])
        mod = key[1:]
        ident = (lane, mod)
        if kind == 1:
            pending.setdefault(ident, []).append(beat)
        else:
            q = pending.get(ident)
            if q:
                start = q.pop(0)
                notes.append((lane, start * spb,
                              max(0.05, (beat - start)) * spb, mod))
    notes.sort(key=lambda n: n[1])
    return notes


def chart_json(score, sid):
    notes = pair_events(score)
    length = notes[-1][1] + notes[-1][2] if notes else 0.0
    out = []
    for lane, t, dur, mod in notes:
        item = {'lane': lane, 't': round(t, 3), 'dur': round(dur, 3)}
        if mod:
            item['mod'] = mod          # ' 高音(右键) / _ 低音(左键) / # 半音(中键)
        out.append(item)
    return {'id': sid, 'title': score.get('title') or sid, 'bpm': score['bpm'],
            'keys': [k.upper() if k != ',' else ',' for k in LANE_KEYS],
            'length': round(length, 3), 'note_count': len(notes),
            'notes': out}


INDEX_HTML = '''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>歌谱同步库</title>
<style>
:root{--bg:#0f1220;--card:#1a1e30;--accent:#6c8cff;--text:#e8eaf2;--dim:#9aa0b5}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:"Microsoft YaHei UI",system-ui,sans-serif;padding:24px}
h1{font-size:20px;margin-bottom:4px}
.sub{color:var(--dim);font-size:13px;margin-bottom:20px}
#list{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
.card{background:var(--card);border:1px solid #2a2f45;border-radius:12px;padding:16px}
.card h3{font-size:16px;margin-bottom:6px}
.meta{color:var(--dim);font-size:12px;line-height:1.7}
.tag{display:inline-block;background:#232842;color:var(--accent);border-radius:5px;padding:1px 8px;font-size:11px;margin-right:6px}
.empty{color:var(--dim)}
a.btn{display:inline-block;margin-top:10px;background:var(--accent);color:#fff;border:none;
border-radius:8px;padding:6px 14px;font-size:12px;cursor:pointer;text-decoration:none}
</style></head><body>
<h1>🎵 歌谱同步库</h1>
<div class="sub">服务端仅做存储与同步，转换由客户端本地完成后自动上传</div>
<div id="list"><div class="empty">加载中…</div></div>
<script>
fetch('/api/songs').then(r=>r.json()).then(songs=>{
  const box=document.getElementById('list');
  if(!songs.length){box.innerHTML='<div class="empty">暂无歌谱，客户端转换后会自动上传到这里</div>';return;}
  box.innerHTML=songs.map(s=>`
    <div class="card"><h3>${s.title||s.id}</h3>
      <div class="meta"><span class="tag">${s.has_midi?'MIDI':'简谱'}</span>
        BPM ${s.bpm||'—'} · ${s.notes||0} 音符</div>
      <a class="btn" href="/api/songs/${encodeURIComponent(s.id)}/chart" target="_blank">谱面 JSON</a>
    </div>`).join('');
}).catch(e=>{box.innerHTML='<div class="empty">无法连接：'+e+'</div>';});
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _send(self, code, body: bytes, ctype='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'))

    @staticmethod
    def _decode_path(p):
        try:
            p = p.encode('latin-1').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return unquote(p)

    def log_message(self, fmt, *args):
        try:
            print(f'[http] {args[0] if args else fmt}')
        except Exception:
            pass

    def do_GET(self):
        path = self._decode_path(urlparse(self.path).path)
        try:
            if path == '/api/songs':
                self._json(200, list_songs())
                return
            m = re.fullmatch(r'/api/songs/([^/]+)/chart', path)
            if m:
                p = find_song(m.group(1))
                if not p:
                    self._json(404, {'error': '歌曲不存在'})
                    return
                try:
                    self._json(200, chart_json(engine.load_score_file(p), m.group(1)))
                except Exception as e:
                    self._json(500, {'error': f'谱面解析失败：{e}'})
                return
            m = re.fullmatch(r'/api/songs/([^/]+)/midi', path)
            if m:
                p = find_song(m.group(1))
                if not p:
                    self._json(404, {'error': '歌曲不存在'})
                    return
                mid = os.path.splitext(p)[0] + '.mid'
                if not os.path.exists(mid):
                    mid = os.path.splitext(p)[0] + '.midi'
                if not os.path.exists(mid):
                    self._json(404, {'error': '该歌曲无 MIDI 源'})
                    return
                with open(mid, 'rb') as f:
                    self._send(200, f.read(), 'audio/midi')
                return
            if path == '/api/health':
                self._json(200, {'ok': True, 'time': time.time()})
                return
            if path in ('/', '/index.html'):
                self._send(200, INDEX_HTML, 'text/html; charset=utf-8')
                return
            self._json(404, {'error': 'not found'})
        except Exception as e:
            self._json(500, {'error': str(e)})

    def do_POST(self):
        path = self._decode_path(urlparse(self.path).path)
        try:
            if path == '/api/upload':
                ctype = self.headers.get('Content-Type', '')
                if 'application/json' in ctype:
                    self._upload_json()
                elif 'multipart/form-data' in ctype:
                    self._upload_multipart()
                else:
                    self._json(415, {'error': '需 application/json 或 multipart/form-data'})
                return
            self._json(404, {'error': 'not found'})
        except Exception as e:
            self._json(500, {'error': str(e)})

    # ---- 上传：JSON（含歌谱文本 + 元数据 + 可选 MIDI base64） ----
    def _upload_json(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(n) or b'{}')
        sid = str(body.get('id') or '').strip()
        title = str(body.get('title') or sid).strip()
        score_text = str(body.get('score') or '').strip()
        if not valid_id(sid):
            self._json(400, {'error': 'id 不合法（仅中英文数字、空格、-_ .，≤120字符）'})
            return
        if not score_text:
            self._json(400, {'error': '缺少 score 歌谱文本'})
            return
        # 先解析校验，避免写入坏谱
        try:
            engine.parse_score(score_text)
        except Exception as e:
            self._json(400, {'error': f'歌谱解析失败：{e}'})
            return
        self._save_song(sid, title, score_text,
                        midi_b64=body.get('midi_b64') or None)
        self._json(200, {'ok': True, 'id': sid, 'title': title})

    def _upload_multipart(self):
        ctype = self.headers.get('Content-Type', '')
        m = re.search(r'boundary=([^;]+)', ctype)
        if not m:
            self._json(400, {'error': '缺少 boundary'})
            return
        boundary = m.group(1).strip().strip('"').encode('utf-8')
        n = int(self.headers.get('Content-Length') or 0)
        data = self.rfile.read(n)
        parts = data.split(b'--' + boundary)
        fields, files = {}, {}
        for part in parts:
            if b'\r\n\r\n' not in part:
                continue
            head, body = part.split(b'\r\n\r\n', 1)
            body = body[:-2] if body.endswith(b'\r\n') else body
            htxt = head.decode('utf-8', 'replace')
            dm = re.search(r'name="([^"]+)"', htxt)
            if not dm:
                continue
            name = dm.group(1)
            if 'filename=' in htxt:
                files[name] = body
            else:
                fields[name] = body.decode('utf-8')
        sid = fields.get('id', '').strip()
        title = fields.get('title', sid).strip()
        score_text = fields.get('score', '').strip()
        if not valid_id(sid) or not score_text:
            self._json(400, {'error': 'id 非法或缺少 score'})
            return
        try:
            engine.parse_score(score_text)
        except Exception as e:
            self._json(400, {'error': f'歌谱解析失败：{e}'})
            return
        midi_b64 = None
        if 'midi' in files:
            import base64
            midi_b64 = base64.b64encode(files['midi']).decode('ascii')
        self._save_song(sid, title, score_text, midi_b64=midi_b64)
        self._json(200, {'ok': True, 'id': sid, 'title': title})

    def _save_song(self, sid, title, score_text, midi_b64=None):
        ensure_songs_dir()
        base = os.path.join(SONGS_DIR, sid)
        with open(base + '.txt', 'w', encoding='utf-8') as f:
            f.write(score_text)
        if midi_b64:
            import base64
            try:
                raw = base64.b64decode(midi_b64)
                with open(base + '.mid', 'wb') as f:
                    f.write(raw)
            except Exception:
                pass
        with open(base + '.json', 'w', encoding='utf-8') as f:
            json.dump({'title': title, 'updated': int(time.time())}, f,
                      ensure_ascii=False)
        print(f'[upload] {sid} · {title}')


def main():
    ensure_songs_dir()
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f'歌谱同步服务端已启动: http://127.0.0.1:{PORT}  （纯存储同步，不做转换）')
    print(f'歌曲目录: {SONGS_DIR}')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')


if __name__ == '__main__':
    main()
