# -*- coding: utf-8 -*-
"""T81 · 离线单脉冲长窗（v2，修正激励构造）
--------------------------------------------------------------------
v1 的错：① σ 取样区间误含其他脉冲（660 µV）② 直接拼 col② 原始码 ⇒ DC 基准不齐
        ⇒ u 爆到 −68 万 µV。
v2 的激励构造（干净、可复现）：
  前导 8 s 静默噪声（σ 由**脉冲前 0.5~0.05 s 实测安静段**估）+ 实测脉冲本体
  （起点前 0.15 s ~ 终点后 0.20 s，含 AFE 平滑边沿；用最前 0.1 s 中位归零）+ 尾 60 s 静默噪声
  再过离线固件链（T80 已验证：与实测 col⑤ 相关 0.9991）⇒ 单脉冲固有响应。
"""
import importlib.util
import os
import sys

import numpy as np
from scipy.signal import lfilter

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['t51'] = m
_spec.loader.exec_module(m)

FS = m.FS
FS_T = 500.0
UV = 0.1122
LEAD_S, TRAIL_S = 8.0, 60.0
PRE_PAD, POST_PAD = 0.15, 0.20


def dc_block(x, fc, fs=FS):
    r = 1.0 - 2.0 * np.pi * fc / fs
    return lfilter([1.0, -1.0], [1.0, -r], x)


def load(path):
    r = []
    for ln in open(path, encoding='utf-8', errors='replace'):
        q = ln.split()
        if len(q) < 5:
            continue
        try:
            r.append([float(x) for x in q[:5]])
        except ValueError:
            pass
    return np.array(r)


def pulses(v, fs, frac=0.5, minw=0.02):
    hi, lo = np.percentile(v, 98), np.median(v)
    thr = lo + frac * (hi - lo)
    up = v > thr
    st, en = [], []
    for k in range(1, len(v)):
        if up[k] and not up[k - 1]:
            st.append(k)
        elif up[k - 1] and not up[k]:
            en.append(k)
    o = []
    for s in st:
        e = next((x for x in en if x > s), None)
        if e and (e - s) / fs >= minw:
            o.append((s, e))
    return o


FILES = [
    ('3mv0.5hz-620k(off)-notchoff-2026-09-21 124440.txt', 'ECG_PROFILE_DUAL_A_PLUS', 'A+ 现状(18节)'),
    ('3mv-rectanglepulse(notch off)-0.5hz-2026-09-20 143826.txt', 'ECG_PROFILE_DIAG150_D3', '09-20 D3(7节)'),
]

TGRID = (0.02, 0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 1.00, 1.30, 1.60,
         2.00, 3.00, 4.00, 5.00, 6.00, 8.00, 10.0, 15.0, 20.0, 30.0, 45.0, 59.0)

for fname, macro, tag in FILES:
    p = os.path.join(HERE, fname)
    if not os.path.exists(p):
        print('缺 %s' % fname)
        continue
    a = load(p)
    c2 = a[:, 1] * UV
    c5 = a[:, 4] * UV
    pr = pulses(c5, FS_T)
    kk = len(pr) // 2
    s, e = pr[kk]
    width = (e - s) / FS_T

    # σ：脉冲前 0.5~0.05 s 的实测安静段（避开 tail 与脉冲）
    q = np.concatenate([c5[s_i - int(0.5 * FS_T):s_i - int(0.05 * FS_T)]
                        for s_i, _ in pr])
    sg = float(np.std(q))

    a0 = s - int(PRE_PAD * FS_T)
    a1 = e + int(POST_PAD * FS_T)
    w = c2[a0:a1].copy()
    w -= float(np.median(w[:int(0.10 * FS_T)]))       # 用最前 0.1 s 归零

    rs = np.random.RandomState(7)
    lead = rs.randn(int(LEAD_S * FS_T)) * sg
    tailn = rs.randn(int(TRAIL_S * FS_T)) * sg
    x500 = np.concatenate([lead, w, tailn])
    i0 = len(lead) + (s - a0)                          # 脉冲起点（500 域）

    t500 = np.arange(len(x500)) / FS_T
    t853 = np.arange(0.0, t500[-1], 1.0 / FS)
    x853 = np.interp(t853, t500, x500)
    t0 = i0 / FS_T
    k0, k1 = int(round(t0 * FS)), int(round((t0 + width) * FS))

    secs, nn = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, macro)
    bp = [secs[i] for i in range(nn, len(secs))]

    print('=' * 112)
    print('%s  [%s]' % (fname, tag))
    print('  噪声 σ = %.2f µV ｜ 用第 %d/%d 段脉冲（脉宽 %.1f ms）｜ 激励段 %.2f~%.2f s ｜ 总长 %.1f s ｜ 链 %d 节(BP %d)'
          % (sg, kk + 1, len(pr), width * 1e3, t0, t0 + width, len(x500) / FS_T, len(secs), len(bp)))
    print('=' * 112)

    def report(name, y):
        bb = float(np.median(y[k0 - int(0.5 * FS):k0 - int(0.05 * FS)]))
        u = y - bb
        k = int(0.2 * FS)
        sl = np.concatenate([np.zeros(k), (u[k:] - u[:-k]) / 0.2])
        row = [(t, float(u[k1 + int(t * FS)]), float(sl[k1 + int(t * FS)]))
               for t in TGRID if k1 + int(t * FS) < len(u)]
        print('  [%s]' % name)
        print('    t(s)    : ' + ''.join('%8.2f' % r[0] for r in row))
        print('    u(µV)   : ' + ''.join('%8.1f' % r[1] for r in row))
        print('    |sl|    : ' + ''.join('%8.1f' % abs(r[2]) for r in row))
        for thr in (100.0, 50.0, 20.0):
            seg = np.abs(u[k1:])
            bad = np.where(seg > thr)[0]
            tt = 0.0 if len(bad) == 0 else (bad[-1] + 1) / FS
            jj = k1 + int(tt * FS)
            v = float(np.median(u[jj:min(jj + int(2 * FS), len(u))])) if jj < len(u) else float('nan')
            ok = '✓' if abs(v) <= 100.0 else '✗'
            print('    → |u| 最后一次 > %5.0f µV 在 +%5.2f s  ⇒ 之后平台 %+7.1f µV  %s'
                  % (thr, tt, v, ok))
        tail_med = float(np.median(u[-int(3 * FS):]))
        print('    → **单脉冲稳态 a1（尾窗 +57~60 s 中位）= %+7.1f µV**  %s   ｜ 极值 max|u| = %7.1f µV'
              % (tail_med, '✓ ≤100' if abs(tail_med) <= 100 else '✗', float(np.max(np.abs(u[k1:])))))

    report('① 激励本身（实测 col② 脉冲，不进链）', x853)
    report('② 离线 现状链 = HP4@0.05 + dc@0.05', dc_block(m.run(bp, x853, first=0), 0.05))
    report('③ 候选A 去 HP4 = 只 dc@0.05', dc_block(x853, 0.05))
    report('④ 候选B 去 HP4 + dc@0.02', dc_block(x853, 0.02))
    print()
