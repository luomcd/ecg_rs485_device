# -*- coding: utf-8 -*-
"""T81 · a1「脉冲后基线偏移」的**稳态口径**重算
--------------------------------------------------------------------
T79/T80 用的口径是「脉冲后窗内 max|u|」= 波形瞬态峰值，不是"基线"。
GB 9706.247 a) 原文是"脉冲后的基线与脉冲出现前基线之间的偏移" ⇒ **基线量**
⇒ 应在**恢复平稳之后**读，即 a2 斜率终点的基线。

本脚本把"平稳"变成可复现的操作定义，并给多口径读数：
  口径① 平台中位：从 tail 曲线斜率首次持续 < 阈值 起，到下一脉冲前的中位值
  口径② 定点：降沿后 +0.5/+1.0/+1.5/+1.8 s 的基线
  口径③ 极值（旧口径，供对照）
阈值取两档：300 µV/s（= a2 判限自身）与 50 µV/s（更严"真平"）。
同时对列②（链前 AFE 原始码）做同口径对照 ⇒ 归因（源/AFE 是否也带 tail）。
"""
import importlib.util
import sys

import numpy as np

spec = importlib.util.spec_from_file_location('t51', '_t51_yy0885_pulse.py')
m = importlib.util.module_from_spec(spec)
sys.modules['t51'] = m
spec.loader.exec_module(m)

FS = m.FS_TXT
FILES = [
    '3mv0.5hz-620k(off)-notchoff-2026-09-21 124440.txt',      # 09-21 A+（BP3/LP2）
    '3mv-rectanglepulse(notch off)-0.5hz-2026-09-20 143826.txt',  # 09-20（BP4/LP4）
]


def tail_curve(y, idx, width):
    """跨脉冲中位的 tail 曲线：相对各自脉冲前基线（脉冲前 0.5~0.05 s 中位）。"""
    w = int(round(width * FS))
    segs = []
    for i0 in idx:
        i1 = i0 + w
        if i0 - int(0.5 * FS) < 0 or i1 + int(1.85 * FS) >= len(y):
            continue
        base = float(np.median(y[i0 - int(0.5 * FS):i0 - int(0.05 * FS)]))
        segs.append(y[i1:i1 + int(1.85 * FS)] - base)
    if not segs:
        return None, 0
    L = min(len(s) for s in segs)
    return np.median(np.array([s[:L] for s in segs]), axis=0), len(segs)


def slope_win(c, win=0.20):
    """滑窗差分斜率 µV/s（窗 200 ms）。"""
    k = int(win * FS)
    return (c[k:] - c[:-k]) / win, k


def plateau_a1(c, thr):
    """从 tail 起点往后，找 |slope|<thr 首次**持续**到最后的位置 ⇒ 平台起点。"""
    sl, k = slope_win(c)
    ok = np.abs(sl) < thr
    # 从末尾往前，找到最后一个不满足的位置
    i = len(ok) - 1
    while i >= 0 and ok[i]:
        i -= 1
    start = i + 1  # 单位＝滑窗索引 ⇒ 曲线索引 +k
    return (start + k) if start < len(ok) else None


def report(name, y, idx, width, amp_nom=3000.0):
    print('  [%s]' % name)
    c, n = tail_curve(y, idx, width)
    if c is None:
        print('    无有效段')
        return
    L = len(c)
    tgrid = [0.02, 0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 1.00, 1.30, 1.60, 1.80]
    sl, _ = slope_win(c)
    print('     段数 %d ｜ tail 窗 %.2f s' % (n, L / FS))
    print('     t(s)  : ' + ' '.join('%7.2f' % t for t in tgrid))
    print('     u(µV) : ' + ' '.join('%7.1f' % c[int(t * FS)] for t in tgrid))
    print('     sl(µV/s):' + ' '.join(
        '%7.1f' % sl[min(int(t * FS), len(sl) - 1)] for t in tgrid))
    for thr in (300.0, 50.0):
        st = plateau_a1(c, thr)
        if st is None:
            print('     口径① |slope|<%5.0f µV/s : **窗内始终未平息**（1.85 s 内没进入平台）'
                  % thr)
        else:
            v = float(np.median(c[st:]))
            print('     口径① |slope|<%5.0f µV/s : 平台起于 +%.2f s，平台中位 **%+7.1f µV**'
                  '  (尾值 +1.80s = %+7.1f)  %s'
                  % (thr, st / FS, v, c[int(1.80 * FS)],
                     '✓' if abs(v) <= 100 else '✗'))
    # 定点口径
    d = {t: float(c[int(t * FS)]) for t in (0.5, 1.0, 1.5, 1.8)}
    print('     口径② 定点：+0.5s %+7.1f ｜ +1.0s %+7.1f ｜ +1.5s %+7.1f ｜ +1.8s %+7.1f µV'
          % (d[0.5], d[1.0], d[1.5], d[1.8]))
    print('     口径③ 极值 max|u|(0~1.85s) = %7.1f µV   ← T79/T80 旧口径'
          % float(np.max(np.abs(c))))


print('=' * 100)
print('a1 稳态口径重算（判限 ≤ 100 µV）')
print('=' * 100)
for f in FILES:
    uv5, uv4, uv2, fs = m.load_txt(f)
    idx, width = m.detect_pulses(uv5, fs)
    print()
    print('─' * 100)
    print('文件：%s' % f)
    print('  样本 %d ｜ 时长 %.2f s ｜ 脉冲 %d 段 ｜ 脉宽 %.1f ms ｜ 间隔 %.3f s'
          % (len(uv5), len(uv5) / fs, len(idx), width * 1e3,
             float(np.median(np.diff(idx))) / fs if len(idx) > 1 else float('nan')))
    print('  ● 列⑤ 链后（判读对象）')
    report('col5 链后', uv5, idx, width)
    print('  ● 列② 链前（AFE 原始码，归因对照）')
    report('col2 链前', uv2, idx, width)
