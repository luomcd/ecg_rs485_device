#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T80 诊断：① A+ 档 BP 节真实指纹（带 abs）② 固件真实链在 853 域 vs 500 域的 a3 口径差
③ 实测 txt 的 idxraw 抽取规律。
"""
import importlib.util
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location('_t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['_t51'] = m
_spec.loader.exec_module(m)
FS = m.FS

sos, n_notch = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, 'ECG_PROFILE_DUAL_A_PLUS')
kinds = [m.section_kind(s) for s in sos]

print('=' * 96)
print('[A] A+ 档 BP 段各节 |H| 指纹（|H| 用 abs，避免复数实部）')
print('=' * 96)
FR = (0.02, 0.05, 0.1, 0.2, 0.5, 0.67, 1.0, 5.0, 40.0, 100.0, 150.0, 200.0, 250.0, 300.0)
print('  节 类型   ' + ' '.join('%7.2f' % f for f in FR))
for i in range(n_notch, len(sos)):
    print('  %2d %-5s ' % (i, kinds[i]) + ' '.join('%7.4f' % abs(m.band_mag([sos[i]], f)) for f in FR))

# 反解每节等效 -3dB 点（相对其通带最大值）
print()
print('  各节等效拐点（|H| 首次降到通带 0.7071 的频率）：')
fgrid = np.logspace(np.log10(0.01), np.log10(400), 4000)
for i in range(n_notch, len(sos)):
    H = np.array([abs(m.band_mag([sos[i]], f)) for f in fgrid])
    k = kinds[i]
    if k == 'HP':
        Hp = H[-1]
        idx = np.argmax(H >= Hp * 0.7071)
        print('   节%2d [HP] 通带 %.4f ⇒ -3dB @ %.4f Hz' % (i, Hp, fgrid[idx]))
    elif k == 'LP':
        Hp = H[0]
        idx = np.argmax(H <= Hp * 0.7071)
        print('   节%2d [LP] 通带 %.4f ⇒ -3dB @ %.1f Hz' % (i, Hp, fgrid[idx]))

print()
print('  完整 BP 段（notch OFF 实际链）|H|：')
print('   ' + ' '.join('%7.2fHz=%6.4f' % (f, abs(m.band_mag(sos, f, first=n_notch))) for f in FR))

# ---------------------------------------------------------------- 853 vs 500 口径
print()
print('=' * 96)
print('[B] 固件真实链（BP 段 + dc1@0.05）：853 域 vs 500 域（抽取）测得的 a3 / a1')
print('=' * 96)
from scipy.signal import lfilter


def dc_block(x, fc=0.05):
    r = 1.0 - 2.0 * np.pi * fc / FS
    return lfilter([1.0, -1.0], [1.0, -r], x)


def report(y, fs, idx, width, tag):
    from _t51_yy0885_pulse import read_all, med
    rs = read_all(y, fs, idx, 3000.0, width)
    if not rs:
        print('   %-28s 读数不足' % tag)
        return
    print('   %-28s a3过冲 %6.2f%% ｜ 下冲 %6.2f%% ｜ a1@.05 中位 %6.1f ｜ droop %6.1f µV'
          % (tag, med(rs, ['ovs', 'near']), med(rs, ['dns', 'near']),
             med(rs, ['dispm', 0.05]), med(rs, ['plat_near']) - med(rs, ['plat_far'])))


period = 2.0
n = int((5.0 + max(4.0 * period, 6.0)) * FS)
x, idx = m.pulse_train(n, FS, 3000.0, 0.1, 5.0, period)
secs = [sos[i] for i in range(n_notch, len(sos))]
y853 = dc_block(m.run(secs, x, first=0))
report(y853, FS, idx, 0.1, '① 853 域（固件真实）')

# 抽取到 500 域：两种口径
# (a) 线性插值到 500 Hz 均匀网格
t853 = np.arange(len(y853)) / FS
t500 = np.arange(0, t853[-1], 1 / 500.0)
y500i = np.interp(t500, t853, y853)
idx500 = [int(round(i0 / FS * 500)) for i0 in idx]
report(y500i, 500.0, idx500, 0.1, '② 500 域（线性插值）')

# (b) 最近邻抽取，步长按 853/500 = 1.7065 交替（模拟 avg 步长 1.7065）
pos = np.arange(0, len(y853), 500.0 / FS)
pos = np.round(pos).astype(int)
pos = pos[pos < len(y853)]
y500n = y853[pos]
idx500n = [int(np.argmin(np.abs(pos - i0))) for i0 in idx]
report(y500n, 500.0, idx500n, 0.1, '③ 500 域（最近邻抽取）')

# 真实 txt 的 idxraw 步长规律
print()
print('  实测 txt 的 idxraw 步长分布：')
for f in ['3mv0.5hz-620k(off)-notchoff-2026-09-21 124440.txt']:
    p = os.path.join(HERE, f)
    raw = []
    for ln in open(p, encoding='utf-8', errors='replace'):
        q = ln.split()
        if len(q) < 6:
            continue
        try:
            raw.append(int(q[0]))
        except ValueError:
            pass
    d = np.diff(raw)
    u, c = np.unique(d, return_counts=True)
    print('   %-46s n=%d  步长 %s' % (os.path.basename(p), len(raw),
                                     ' '.join('%d:%.1f%%' % (a, 100.0 * b / len(d)) for a, b in zip(u, c))))
    print('   平均步长 %.4f ⇒ 等效 %.3f Hz' % (d.mean(), FS / d.mean()))
