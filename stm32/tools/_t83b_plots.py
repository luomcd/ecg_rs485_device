# -*- coding: utf-8 -*-
"""T83b 出图：a1 的极点预算 / 刺激源容差 / 620k 建场曲线（三面板）"""
import importlib.util
import math
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import sosfilt, lfilter

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(spec)
sys.modules['t51'] = m
spec.loader.exec_module(m)
FS = m.FS

for f in ('Microsoft YaHei', 'SimHei', 'DengXian', 'Arial Unicode MS'):
    try:
        plt.rcParams['font.sans-serif'] = [f]
        break
    except Exception:
        pass
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 110

sos, n_notch = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, 'ECG_PROFILE_DUAL_A_PLUS')
bp = list(sos[n_notch:])
kinds = [m.section_kind(s) for s in bp]
LP = [bp[i] for i, k in enumerate(kinds) if k == 'LP']
HP4 = [bp[i] for i, k in enumerate(kinds) if k == 'HP']


def s6(s):
    return [s[0], s[1], s[2], 1.0, s[3], s[4]]


def dc_pole(fc):
    return -math.log(1.0 - 2.0 * math.pi * fc / FS) * FS / (2.0 * math.pi)


def dc_block(x, fc):
    r = 1.0 - 2.0 * math.pi * fc / FS
    return lfilter([1.0, -1.0], [1.0, -r], x)


def mk(fc, keep, hk=0):
    s = list(LP)
    if keep:
        s += list(HP4)
    elif hk == 1:
        s += [HP4[0]]
    return np.array([s6(v) for v in s], dtype=float)


def sf(fc, keep, hk=0):
    if keep:
        return dc_pole(fc) + 0.13068
    if hk == 1:
        return dc_pole(fc) + 0.09243
    return dc_pole(fc)


BUDGET = 0.1 / (3.0 * 0.10) / (2.0 * math.pi)      # 0.05306 Hz
A_AMP, T_PULSE = 3000.0, 0.10

CANDS = [('S0 现状\n18 节', 0.05, True, 0), ('A 删HP4\ndc@0.05', 0.05, False, 0),
         ('B 删HP4\ndc@0.02', 0.02, False, 0), ('B2 删HP4\ndc@0.015', 0.015, False, 0),
         ('D 删HP4\ndc@0.01', 0.01, False, 0)]

# 脉冲偏移（干净方波，跳过低通边沿振铃）
Nt = int((2.0 + T_PULSE + 20.0) * FS)
tp = np.arange(Nt) / FS
cl = np.where((tp >= 2.0) & (tp < 2.0 + T_PULSE), A_AMP, 0.0)
k1 = int(round((2.0 + T_PULSE) * FS)) + 2
TAIL = []
for nm, fc, keep, hk in CANDS:
    y = sosfilt(mk(fc, keep, hk), dc_block(cl, fc))
    bb = float(np.median(y[k1 - int(0.5 * FS):k1 - int(0.05 * FS)]))
    TAIL.append(float(np.max(np.abs((y - bb)[k1 + int(0.05 * FS):k1 + int(10.0 * FS)]))))
SFS = [sf(fc, keep, hk) for _, fc, keep, hk in CANDS]

fig, ax = plt.subplots(1, 3, figsize=(16.2, 5.8))

# ---- 面板 1：a1 占限 与 Σf 预算
x = np.arange(len(CANDS))
col = ['#c0392b', '#c0392b', '#1e8449', '#1e8449', '#1e8449']
ax[0].bar(x, TAIL, color=col, width=0.6, zorder=3)
ax[0].axhline(100.0, color='#c0392b', ls='--', lw=2, zorder=4)
ax[0].text(-0.42, 106, 'a1 限 100 µV', color='#c0392b', ha='left', fontsize=10.5, weight='bold')
for xi, (t, sfv) in enumerate(zip(TAIL, SFS)):
    yo = t + 8 if xi % 2 == 0 else t + 165          # 交错摆放，避免相邻标签互压
    ax[0].text(xi, yo, '%.0f µV\n占限 %.0f%%\nΣf %.4f Hz\n预算 %.0f%%'
               % (t, t, sfv, 100 * sfv / BUDGET), ha='center', va='bottom', fontsize=7.8, weight='bold')
    if xi % 2 == 1:
        ax[0].annotate('', xy=(xi, yo - 3), xytext=(xi, t + 4),
                       arrowprops=dict(arrowstyle='-', color='0.45', lw=0.8, ls=':'))
ax[0].set_xticks(x); ax[0].set_xticklabels([c[0] for c in CANDS], fontsize=9.5)
ax[0].set_ylim(0, 560); ax[0].set_ylabel('脉冲后偏移 a1 (µV)', fontsize=11)
ax[0].set_title('(1) a1 = A·T·2π·Σf ｜ 极点预算 Σf ≤ %.4f Hz' % BUDGET, fontsize=11.5, weight='bold')
ax[0].grid(axis='y', ls=':', alpha=0.5, zorder=0)

# ---- 面板 2：刺激源容差
sc = np.linspace(0.9, 3.0, 200)
tA, tB = TAIL[1] * sc, TAIL[2] * sc
ax[1].plot(sc * 100 - 100, tA, color='#c0392b', lw=2.5, label='A（Σf 0.0500 Hz）')
ax[1].plot(sc * 100 - 100, tB, color='#1e8449', lw=2.5, label='B（Σf 0.0200 Hz）')
ax[1].axhline(100.0, color='k', ls='--', lw=1.8)
ax[1].text(95, 104, 'a1 限 100 µV', ha='right', fontsize=10.5, weight='bold')
cA = (100.0 / TAIL[1] - 1) * 100
cB = (100.0 / TAIL[2] - 1) * 100
ax[1].axvline(cA, color='#c0392b', ls=':', lw=1.6)
ax[1].axvline(cB, color='#1e8449', ls=':', lw=1.6)
ax[1].annotate('A 极限：刺激源 +%.1f%%' % cA, xy=(cA, 100), xytext=(cA + 8, 155),
               color='#c0392b', fontsize=10, weight='bold',
               arrowprops=dict(arrowstyle='->', color='#c0392b'))
ax[1].annotate('B 极限：+%.0f%%' % cB, xy=(cB, 100), xytext=(cB - 6, 60),
               color='#1e8449', fontsize=10, weight='bold',
               arrowprops=dict(arrowstyle='->', color='#1e8449'))
ax[1].set_xlabel('审评方方波幅度/宽度 相对标称 3 mV/100 ms 的偏差 (%)', fontsize=10.5)
ax[1].set_ylabel('a1 (µV)', fontsize=11)
ax[1].set_title('(2) 刺激源容差：A 只容忍 ±%.0f%%，B 容忍 ±%.0f%%' % (cA, cB), fontsize=11.5, weight='bold')
ax[1].legend(fontsize=10); ax[1].grid(ls=':', alpha=0.5); ax[1].set_xlim(-10, 200)

# ---- 面板 3：620k 建场曲线（1 s RMS 包络，避免零交叉假尖峰）
N3 = int(122.0 * FS)
_xx = np.zeros(N3); _xx[int(2.0 * FS):] = 300e3
w = int(1.0 * FS)
for nm, c, fc, keep, hk in (('现状 18 节', '#c0392b', 0.05, True, 0), ('A 16 节', '#2e86c1', 0.05, False, 0),
                            ('B 16 节', '#1e8449', 0.02, False, 0)):
    yy = sosfilt(mk(fc, keep, hk), dc_block(_xx, fc))[int(2.0 * FS):] / 1e3      # mV
    nenv = len(yy) // w
    env = np.sqrt(np.array([np.mean(yy[i * w:(i + 1) * w] ** 2) for i in range(nenv)]))
    tt = (np.arange(nenv) + 0.5) * w / FS
    ax[2].semilogy(tt, np.maximum(env, 1e-4), color=c, lw=2.2, label=nm)
ax[2].axhline(0.1, color='k', ls='--', lw=1.5)
ax[2].text(118, 0.115, '100 µV', ha='right', fontsize=10)
ax[2].set_xlim(0, 120); ax[2].set_ylim(5e-4, 400)
ax[2].set_xlabel('距 300 mV 阶跃接入的时间 (s)', fontsize=10.5)
ax[2].set_ylabel('输出 1 s RMS 包络 (mV)', fontsize=11)
ax[2].set_title('(3) 620 kΩ 建场（RMS 包络）：A 25 s ／ B 64 s ／ 现状 65 s', fontsize=11.5, weight='bold')
ax[2].legend(fontsize=10); ax[2].grid(ls=':', alpha=0.5, which='both')

plt.tight_layout()
out = os.path.join(HERE, 'T83b_a1极点预算_2026-09-21.png')
plt.savefig(out, bbox_inches='tight')
print('[写出]', out)
print('  候选 a1(µV) / Σf / 预算占用:')
for nm, t, s in zip([c[0].replace('\n', ' ') for c in CANDS], TAIL, SFS):
    print('    %-22s %7.1f µV  Σf=%.5f Hz  预算 %5.1f%%' % (nm, t, s, 100 * s / BUDGET))
print('  A 刺激源容差 = +%.1f%% ｜ B = +%.1f%%' % (cA, cB))
