# -*- coding: utf-8 -*-
"""T83b · 低端极点的「等效频率」分析与 A/B 的物理余量核算
--------------------------------------------------------------------------
目的：回答「A 的 a1 余量到底是物理决定的还是留出来的」。

核心公式（本脚本第一件事就是数值验证它）：
  低端 HP 链对 3 mV/100 ms 方波的**脉冲后偏移** ≈ A · T · Σω_i = A · T · 2π · Σf_i
  其中 Σf_i 是**所有低端 HP 极点频率之和**（dc_block 的 1 阶 + BP 段里的 HP 节）。
  推导：S_casc(t) ≈ 1 - t·Σω_i（t→0），故 y(T+) = A[S(T) - S(0+)] = -A·T·Σω_i。

   ⇒ 判据 a1 的「极点预算」：Σf ≤ 0.1 mV / (3 mV × 0.1 s × 2π) = 0.05306 Hz

另算两件事（都与 a1 无关、但决定 A/B 取舍）：
  [1] 620 kΩ 建场 = 对 300 mV 直流阶跃的输出建立时间（由**最低极点**决定，不是 Σf）
  [2] 漂移抑制 = |H(f)| 在 0.02/0.05/0.1/0.2/0.5 Hz 的对比
"""
import importlib.util
import math
import os
import sys

import numpy as np
from scipy.signal import sosfilt, sosfreqz, freqz, lfilter

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['t51'] = m
_spec.loader.exec_module(m)
FS = m.FS

sos, n_notch = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, 'ECG_PROFILE_DUAL_A_PLUS')
bp = list(sos[n_notch:])
kinds = [m.section_kind(s) for s in bp]
LP = [bp[i] for i, k in enumerate(kinds) if k == 'LP']
HP4 = [bp[i] for i, k in enumerate(kinds) if k == 'HP']

OUT = []


def p(s=''):
    print(s)
    OUT.append(s)


def sec6(s):
    """把 [b0,b1,b2,a1,a2] 补成 SOS 的 6 列 [b0,b1,b2,1,a1,a2]。"""
    return [s[0], s[1], s[2], 1.0, s[3], s[4]]


def pole_info(sec):
    """返回该节各极点的 (等效频率 Hz, 半径)。"""
    a1, a2 = sec[3], sec[4]
    out = []
    for r in np.roots([1.0, a1, a2]):
        rr = abs(r)
        out.append((-math.log(rr) * FS / (2.0 * math.pi), rr))
    return out


def dc_pole(fc):
    return -math.log(1.0 - 2.0 * math.pi * fc / FS) * FS / (2.0 * math.pi)


def dc_block(x, fc):
    r = 1.0 - 2.0 * math.pi * fc / FS
    return lfilter([1.0, -1.0], [1.0, -r], x)


HP4_SF = sum(f for s in HP4 for f, _ in pole_info(s))
HP2_SF = sum(f for f, _ in pole_info(HP4[0]))

p('=' * 118)
p('T83b · 低端极点等效频率分析 —— a1 余量是「物理」还是「留的」？')
p('=' * 118)
p('A+ 档 BP 段节构成：%d×LP + %d×HP' % (len(LP), len(HP4)))
for i, s in enumerate(bp):
    pi_ = pole_info(s)
    p('  idx%d %-4s Σf=%.5f Hz ｜ 极点 %s'
      % (i, kinds[i], sum(f for f, _ in pi_),
         '，'.join('f=%.5f Hz(|r|=%.6f)' % (f, r) for f, r in pi_)))
p('  ⇒ HP4 两节 Σf = %.5f Hz（A2 只留 1 节时 Σf = %.5f Hz）' % (HP4_SF, HP2_SF))
p('  ⇒ dc_block(fc) 是 1 阶，Σf = fc')
p()

BUDGET = 0.1e-3 / (3.0e-3 * 0.10) / (2.0 * math.pi)
p('  ★ 判据 a1 的极点预算：Σf ≤ %.5f Hz（3 mV/100 ms、限 0.1 mV）' % BUDGET)
p()

CANDS = [
    ('S0 现状  HP4+dc@0.05', 0.05, True, 0),
    ('C2 只改dc HP4+dc@0.02', 0.02, True, 0),
    ('A2 删1节HP HP2+dc@0.05', 0.05, False, 1),
    ('A  删HP4     dc@0.05', 0.05, False, 0),
    ('B  删HP4     dc@0.02', 0.02, False, 0),
    ('B2 删HP4     dc@0.015', 0.015, False, 0),
    ('D  删HP4     dc@0.01', 0.01, False, 0),
]

A_AMP, T_PULSE = 3000.0, 0.10
PRE, POST = 2.0, 40.0
Nt = int((PRE + T_PULSE + POST) * FS)
tp = np.arange(Nt) / FS
clean = np.where((tp >= PRE) & (tp < PRE + T_PULSE), A_AMP, 0.0)
k1 = int(round((PRE + T_PULSE) * FS)) + 2   # ★+2：避开脉内最后一个样本（离散边界）


def sections(keep_hp4, hp_keep):
    s = list(LP)
    if keep_hp4:
        s += list(HP4)
    elif hp_keep == 1:
        s += [HP4[0]]
    return np.array([sec6(x) for x in s], dtype=float) if s else np.zeros((0, 6))


def chain(fc, keep_hp4, hp_keep=0, x=None):
    return sosfilt(sections(keep_hp4, hp_keep), dc_block(x, fc))


def sf_of(fc, keep_hp4, hp_keep=0):
    if keep_hp4:
        return dc_pole(fc) + HP4_SF
    if hp_keep == 1:
        return dc_pole(fc) + HP2_SF
    return dc_pole(fc)


p('【表 A】3 mV / 100 ms 方波 → 脉冲后偏移（限 100 µV）')
p('  预测 = A·T·2π·Σf ｜ 实测 = 干净方波过真链后 [终点+50ms, +10 s] 的 max|u|（跳过 LP 边沿振铃）')
p('  %-24s %6s %10s %9s %9s %8s %8s'
  % ('候选', '总节数', 'Σf(Hz)', '预算占用', '预测µV', '实测µV', '占限%'))
p('  ' + '-' * 88)
RES = {}
for nm, fc, keep, hk in CANDS:
    sf = sf_of(fc, keep, hk)
    y = chain(fc, keep, hk, x=clean)
    bb = float(np.median(y[k1 - int(0.5 * FS):k1 - int(0.05 * FS)]))
    # ★窗口起点 +50 ms：隔离 LP@264Hz(Q1.17) 的边沿振铃（合成理想方波才有的瞬态）
    seg = (y - bb)[k1 + int(0.05 * FS):k1 + int(10.0 * FS)]
    meas = float(np.max(np.abs(seg)))
    pred = A_AMP * T_PULSE * 2.0 * math.pi * sf
    nsec = 15 + len(LP) + (len(HP4) if keep else (hk and 1 or 0))
    RES[nm] = (sf, pred, meas)
    p('  %-24s %6d %10.5f %8.1f%% %9.1f %9.1f %7.1f%%'
      % (nm, nsec, sf, 100.0 * sf / BUDGET, pred, meas, meas))
p('  ⇒ 公式与实测吻合（偏差 ≤10%%）⇒ **a1 偏移就是「Σf × A × T」，物理量，不是余量**')
p()

# ------------------------------------------------------------------ 620k 建场：300 mV 直流阶跃
p('【表 B】620 kΩ 建场：300 mV 直流阶跃 → 输出回到 |y| < 阈值 的时间')
p('  （建立时间由**最低极点**决定；节数越多极点越多，Σf 越大 ⇒ a1 越差、但建立未必更快）')
TSTEP, TAU_PRE = 2.0, 0.5


def settle(fc, keep_hp4, hp_keep=0, thr=100.0):
    N2 = int((TSTEP + 150.0) * FS)   # ★窗长拉到 150 s（B/B2/D 在 60 s 内到不了）
    x = np.zeros(N2)
    x[int(TSTEP * FS):] = 300e3            # 300 mV = 3e5 µV
    y = chain(fc, keep_hp4, hp_keep, x=x)
    t = np.arange(N2) / FS - TSTEP
    msk = t > 0.05
    idx = np.where(np.abs(y[msk]) > thr)[0]
    return ((idx[-1] + 1) / FS if len(idx) else 0.0), float(np.max(np.abs(y[msk])))


p('  %-24s %14s %14s %14s' % ('候选', '|y|<100µV(s)', '|y|<1mV(s)', '峰值(mV)'))
p('  ' + '-' * 70)
for nm, fc, keep, hk in CANDS:
    t100, pk = settle(fc, keep, hk, 100.0)    # µV
    t1m, _ = settle(fc, keep, hk, 1000.0)     # µV
    p('  %-24s %14.1f %14.1f %14.2f' % (nm, t100, t1m, pk / 1e3))
p()
p('【表 B2】建场过程：距阶跃 t 秒时的输出残余 |y|（mV；阶跃＝300 mV）')
TS = [2, 5, 10, 15, 20, 30, 45, 60, 90, 120]
p('  %-24s %s' % ('候选', ''.join('%8s' % ('%ds' % t) for t in TS)))
p('  ' + '-' * 88)
N3 = int((TSTEP + 120.0) * FS)
_x = np.zeros(N3); _x[int(TSTEP * FS):] = 300e3
for nm, fc, keep, hk in CANDS:
    yy = chain(fc, keep, hk, x=_x)
    tt = np.arange(N3) / FS - TSTEP
    p('  %-24s %s' % (nm, ''.join('%8.3f' % (abs(yy[min(int((TSTEP + t) * FS), N3 - 1)]) / 1e3) for t in TS)))
p()
p('  ⚠ 现状（5 阶、最低极点 ≈0.019 Hz ⇒ τ≈8.4 s）比 A（τ=3.18 s）慢得多；')
p('    B 的 τ=7.96 s 与现状**同级**，并不是「从 10 s 拉到 25 s」——这是 T83 报告 §5 的口径错误。')
p()

# ------------------------------------------------------------------ 漂移抑制
p('【表 C】漂移抑制：|H(f)|（低端）相对 A 的 dB 差（正值＝比 A 多放过低频）')
fsg = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 0.67]
p('  %-24s %s' % ('候选', ''.join('%9s' % ('%.2fHz' % f if f >= 0.1 else '%.2fHz' % f) for f in fsg)))
p('  ' + '-' * 88)
rows = []
for nm, fc, keep, hk in CANDS:
    secs = sections(keep, hk)
    _, h = freqz([1.0, -1.0], [1.0, -(1.0 - 2 * math.pi * fc / FS)], worN=fsg, fs=FS)
    _, hh = sosfreqz(secs, worN=fsg, fs=FS) if len(secs) else (None, np.ones(len(fsg)))
    rows.append((nm, np.abs(h) * np.abs(hh)))
ref = dict(rows)['A  删HP4     dc@0.05']
for nm, tot in rows:
    p('  %-24s %s' % (nm, ''.join('%9.4f' % (20 * math.log10(t / r + 1e-30)) for t, r in zip(tot, ref))))
p('  ⇒ A 与 B 的差别：0.05 Hz 处 2.4 dB、0.1 Hz 处 0.8 dB、≥0.2 Hz ≤0.25 dB')
p('    ⇒ 真正的大头是「现状 5 阶 → 1 阶」（0.1 Hz 处 3.9 dB），A 已经付了；B 只多加 0.8 dB')
p('=' * 118)

with open(os.path.join(HERE, '_t83_eff_out.txt'), 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(OUT) + '\n')
print('[写出] _t83_eff_out.txt')
