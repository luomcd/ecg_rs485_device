#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T80 裁决：在【忠实激励】(实测 col②) 下，给出 现状 vs 修正候选 的判据 a 三子项读数。

为什么必须用实测 col② 当激励：
  T79 用"理想方波"当激励 ⇒ 过冲虚高一倍（16% vs 真实 4.8~6.7%）。
  实测链路里 AFE(ADS1293 SINC) 已把边沿平滑，col② 自身过冲仅 0.76~1.0%。
  ⇒ 任何 what-if 都必须以 col② 为输入，否则激励边沿过锐，读数不可比。

口径（三档并列）：
  max    ：[i1+0.05s, i1+1.05s] 窗内 max|u|        —— 极值，会被噪声上抬 ~3.4σ（保守）
  med    ：同窗内 中位|u|                          —— 抗极值
  smooth ：输出先过 100 ms 滑动平均再取同窗 max|u| —— 模拟"读基线"，抗噪
"""
import importlib.util
import os
import sys

import numpy as np
from scipy.signal import lfilter, butter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location('_t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['_t51'] = m
_spec.loader.exec_module(m)

FS, FS_T, UV = m.FS, 500.0, 0.1122
FILE = '3mv0.5hz-620k(off)-notchoff-2026-09-21 124440.txt'


def dc_block(x, fc, fs=FS):
    r = 1.0 - 2.0 * np.pi * fc / fs
    return lfilter([1.0, -1.0], [1.0, -r], x)


def load(p):
    r = []
    for ln in open(p, encoding='utf-8', errors='replace'):
        q = ln.split()
        if len(q) < 6:
            continue
        try:
            r.append([float(q[0]), float(q[1]), float(q[2]), float(q[3]), float(q[4]), float(q[5])])
        except ValueError:
            pass
    return np.array(r)


def pulse_idx(y, fs, frac=0.5, minw=0.02):
    hi, lo = np.percentile(y, 98), np.median(y)
    thr = lo + frac * (hi - lo)
    up = y > thr
    st, en = [], []
    for k in range(1, len(y)):
        if up[k] and not up[k - 1]:
            st.append(k)
        elif up[k - 1] and not up[k]:
            en.append(k)
    return [(s, next(e for e in en if e > s)) for s in st if next((e for e in en if e > s), None)
            and (next(e for e in en if e > s) - s) / fs >= minw]


def readout(y, fs, pr, width=0.1, amp=3000.0):
    w = int(round(width * fs))
    sm = np.convolve(y, np.ones(int(0.1 * fs)) / int(0.1 * fs), mode='same')
    out = []
    for s, e in pr:
        i0, i1 = s, s + w
        if i1 + int(1.6 * fs) >= len(y) or i0 < int(0.3 * fs):
            continue
        base = np.median(y[i0 - int(0.3 * fs):i0])
        bs = np.median(sm[i0 - int(0.3 * fs):i0])
        u, us = y - base, sm - bs
        pk = float(np.max(u[i0:i0 + int(0.03 * fs)]))
        pn = float(np.median(u[i0 + int(0.015 * fs):i0 + int(0.03 * fs)]))
        # a1 三口径（起算 +0.05 s，窗 1 s）
        j = i1 + int(0.05 * fs)
        jend = min(j + int(1.0 * fs), len(y) - 1)
        d = dict(a1max=float(np.max(np.abs(u[j:jend]))),
                 a1med=float(np.median(np.abs(u[j:jend]))),
                 a1smo=float(np.max(np.abs(us[j:jend]))))
        # a1 起算 +0.2 s
        j2 = i1 + int(0.2 * fs)
        d['a1smo2'] = float(np.max(np.abs(us[j2:jend])))
        # a2 拟合 0.2~1.2 s
        jb, je = i1 + int(0.2 * fs), min(i1 + int(1.2 * fs), len(y) - 1)
        d['a2fit'] = float(abs(np.polyfit(np.arange(je - jb) / fs, u[jb:je], 1)[0])) if je - jb > 10 else np.nan
        # a3 过冲（对近端平台）
        d['a3'] = (pk - pn) / pn * 100.0
        # 位移包络（用于画图）
        d['env'] = u[j:j + int(1.0 * fs)].copy()
        d['i1'] = i1
        out.append(d)
    return out


def agg(rs, k):
    v = [r[k] for r in rs if np.isfinite(r[k])]
    return float(np.median(v)) if v else float('nan')


a = load(os.path.join(HERE, FILE))
c2 = a[:, 1] * UV
c5 = a[:, 4] * UV
pr = pulse_idx(c5 - np.median(c5), FS_T)
t500 = np.arange(len(a)) / FS_T
t853 = np.arange(0, t500[-1], 1 / FS)
x853 = np.interp(t853, t500, c2 - c2.mean())

sos, n_notch = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, 'ECG_PROFILE_DUAL_A_PLUS')
kinds = [m.section_kind(s) for s in sos]
bp_real = [sos[i] for i in range(n_notch, len(sos))]
lp_secs = [sos[i] for i in range(n_notch, len(sos)) if kinds[i] == 'LP']
hp_secs = [sos[i] for i in range(n_notch, len(sos)) if kinds[i] == 'HP']

print('=' * 104)
print('T80 裁决 —— 判据 a（GB 9706.247）｜激励＝实测 col②（A+ 档，notch OFF，%s）' % FILE)
print('=' * 104)
print('  脉冲 %d 段 ｜ 间隔 %.2f s ｜ 脉宽 %.0f ms ｜ 实测链前平台 %.1f µV（标称 3000）'
      % (len(pr), np.median(np.diff([s for s, _ in pr])) / FS_T, 100.0,
         np.median([np.median(c2[s + int(0.02 * FS_T):s + int(0.04 * FS_T)]) for s, _ in pr])))


def show(tag, y500, note=''):
    rs = readout(y500, FS_T, pr)
    print('  %-26s a1 max %6.1f%s ｜ med %6.1f%s ｜ 平滑 %6.1f%s （起算+0.2s 平滑 %6.1f）｜ a2拟合 %6.1f%s ｜ a3 %5.2f%s   %s'
          % (tag,
             agg(rs, 'a1max'), '✓' if agg(rs, 'a1max') <= 100 else '✗',
             agg(rs, 'a1med'), '✓' if agg(rs, 'a1med') <= 100 else '✗',
             agg(rs, 'a1smo'), '✓' if agg(rs, 'a1smo') <= 100 else '✗',
             agg(rs, 'a1smo2'),
             agg(rs, 'a2fit'), '✓' if agg(rs, 'a2fit') <= 300 else '✗',
             agg(rs, 'a3'), '✓' if agg(rs, 'a3') < 10 else '✗', note))
    return rs


print()
print('  【实测】col⑤（设备实际输出，仅作基准）')
show('实测 col⑤', c5 - np.median(c5), '（含真实噪声）')

print()
print('  【离线·notch OFF】固件真实链（BP 3 节）＋ dc1@0.05')
rs0 = show('S0 现状（HP4+dc1）', np.interp(t500, t853, dc_block(m.run(bp_real, x853), 0.05)))
show('S1 dc1@0.05 + 去HP4', np.interp(t500, t853, dc_block(x853, 0.05)))
_hp2 = [tuple(v / r[3] for v in (r[0], r[1], r[2], r[4], r[5]))
        for r in butter(2, 0.05, btype='high', fs=FS, output='sos')]
show('S2 dc1@0.05 + HP2（2 阶）', np.interp(t500, t853, dc_block(m.run(_hp2, x853), 0.05)))
show('S4 dc1@0.02 + 去HP4', np.interp(t500, t853, dc_block(x853, 0.02)))
print('     （LP 节（250 Hz）在 S1/S4 中保留 ⇒ 10~250 Hz 频响与 T77 期望不变）')

print()
print('  【离线·notch ON】链首 15 节陷波参与（判据 a 不要求 ON 态，附此供参考）')
show('S0-on 现状', np.interp(t500, t853, dc_block(m.run(sos, x853), 0.05)))
show('S1-on 去HP4', np.interp(t500, t853, dc_block(m.run(sos[:n_notch], x853), 0.05)))

# ---------------------------------------------------------------- 位移包络（画图用）
print()
print('=' * 104)
print('位移包络（降沿后 0~1 s，跨脉冲中位）—— 单位 µV')
print('=' * 104)
TAGS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0)


def env_of(y):
    rr = readout(y, FS_T, pr)
    if not rr:
        return None
    e = np.median(np.array([r['env'] for r in rr]), axis=0)
    tt = np.arange(len(e)) / FS_T
    return [(t, float(e[min(int(t * FS_T), len(e) - 1)])) for t in TAGS]


for name, y in (('实测 col⑤', c5 - np.median(c5)),
                ('S0 现状', np.interp(t500, t853, dc_block(m.run(bp_real, x853), 0.05))),
                ('S1 去HP4', np.interp(t500, t853, dc_block(x853, 0.05))),
                ('S4 去HP4@0.02', np.interp(t500, t853, dc_block(x853, 0.02)))):
    e = env_of(y)
    print('  %-14s ' % name + ' '.join('%+.2fs=%6.1f' % (t, v) for t, v in e)
          + '   ' + ('✓ 全时点 |u|<=100' if all(abs(v) <= 100 for _, v in e) else '✗ 超限'))
