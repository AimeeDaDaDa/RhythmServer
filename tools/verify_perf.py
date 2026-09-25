# -*- coding: utf-8 -*-
r"""verify_perf.py — 实测客户端的 GPU 占用（停止态 vs 播放态）

用 typeperf 采集 GPU Engine 计数器（Utilization Percentage），按客户端 PID 过滤后求和。
"""
import ctypes
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(ROOT), 'client',
                   'bin', 'Release', 'net10.0-windows', 'RhythmClient.exe')

VK_F1, VK_F8 = 0x70, 0x77


def key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.04)
    user32.keybd_event(vk, 0, 2, 0)
    time.sleep(0.15)


def client_pid():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq RhythmClient.exe', '/FO', 'CSV', '/NH'],
                         capture_output=True).stdout
    m = re.search(rb'","(\d+)"', out)
    return int(m.group(1)) if m else None


def sample_gpu(pid, seconds=4):
    """采集 GPU 引擎占用（%），返回 (均值, 峰值)"""
    try:
        out = subprocess.run(['typeperf', r'\GPU Engine(*)\Utilization Percentage',
                              '-sc', str(seconds), '-si', '1'],
                             capture_output=True, timeout=seconds + 15).stdout.decode('gbk', 'replace')
    except Exception as e:
        return None, f'采样失败: {e}'
    lines = [l for l in out.splitlines() if l.strip().startswith('"')]
    if len(lines) < 2:
        return None, 'typeperf 无数据'
    header = [h.strip('"') for h in lines[0].split('","')]
    idx = [i for i, h in enumerate(header) if f'pid_{pid}_' in h]
    if not idx:
        return 0.0, 0.0
    vals = []
    for row in lines[1:]:
        parts = [p.strip('"') for p in row.split('","')]
        if len(parts) < len(header):
            continue
        try:
            vals.append(sum(float(parts[i]) for i in idx))
        except (ValueError, IndexError):
            pass
    if not vals:
        return None, '解析失败'
    return sum(vals) / len(vals), max(vals)


def main():
    subprocess.run(['taskkill', '/F', '/IM', 'RhythmClient.exe'], capture_output=True)
    time.sleep(1)
    subprocess.Popen([APP])
    time.sleep(6)

    pid = client_pid()
    if not pid:
        print('未找到客户端进程')
        return
    print(f'客户端 PID={pid}')

    # 1) 停止态
    key(VK_F1)
    time.sleep(2)
    avg, peak = sample_gpu(pid)
    print(f'停止态  GPU 占用: 均值 {avg:.1f}%  峰值 {peak if isinstance(peak, str) else f"{peak:.1f}%"}')

    # 2) 播放态
    key(VK_F8)
    time.sleep(2)
    avg2, peak2 = sample_gpu(pid)
    print(f'播放态  GPU 占用: 均值 {avg2:.1f}%  峰值 {peak2 if isinstance(peak2, str) else f"{peak2:.1f}%"}')

    # 3) 再停止
    key(VK_F1)
    time.sleep(2)
    avg3, peak3 = sample_gpu(pid, seconds=3)
    print(f'回到停止 GPU 占用: 均值 {avg3:.1f}%  峰值 {peak3 if isinstance(peak3, str) else f"{peak3:.1f}%"}')


if __name__ == '__main__':
    main()
