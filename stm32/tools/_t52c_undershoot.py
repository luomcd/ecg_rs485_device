# -*- coding: utf-8 -*-
"""
T52c · 「脉冲边缘的过冲」释义取证
=================================
目的：把实测的降沿下冲 -525.8 µV 拆成两个正交成分
      ①低端 HP 下垂的镜像（=判据 a1「位移」同一个病）
      ②高端 LP 边沿振铃（=判据 c「过冲」的对称现象）
并在「过冲=上升沿」与「过冲=降沿下冲」两种解读下，分别给出
      现状达标性 与 修法(低端→1阶 + 高端→LP2)后的达标性。

用法：python _t52c_undershoot.py
"""
import os
import sys

import numpy as np
from scipy.signal import butter, sosfilt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t52_yy0885_notchoff as T52          # noqa: E402
import _t51_yy0885_pulse as T51             # noqa: E402

FS = 500.0
A_IN = 3000.0
PW = 0.100
L_AFE = 3                                   # T52 标定：AFE 边沿圆化等效 3 样本移动平均
FC_LP = 150.0
LIM_DISP = 100.0                            # µV
LIM_SLOPE = 300.0                           # µV/s
LIM_OVER = 10.0                             # %

_SOS, _NN, _HPIDX, _KINDS = T52.fw_chain('IMP620_N2')
_LPIDX = [i for i in range(_NN, len(_SOS)) if _KINDS[i] == 'LP']


def hdr(t):
    print()
    print('=' * 92)
    print(t)
    print('=' * 92)


def presmooth(z, L):
    return z if L <= 1 else np.convolve(z, np.ones(L) / L, mode='same')


def pulse(interval=20.0, n=3, lead=6.0, tail=4.0, L=L_AFE):
    N = int((lead + interval * (n - 1) + tail) * FS)
    t = np.arange(N) / FS
    x = np.zeros(N)
    last = None
    for j in range(n):
        i0 = int(round((lead + j * interval) * FS))
        i1 = i0 + int(round(PW * FS))
        x[i0:i1] = A_IN
        last = (i0, i1)
    return t, presmooth(x, L), last


def apply_sections(x, idx):
    y = x
    for i in idx:
        y = T51._sosfilt_one(_SOS[i], y)
    return y


def chan(x, low='dc1_hp4', lp=None, notch=False):
    """low: dc1_hp4(现状) | dc1(修法) | hp4(只留HP4) | none
       lp : None | 2 | 3 | 4          notch: 是否串 9 节陷波"""
    y = x.copy()
    if low == 'dc1_hp4':
        y = T52.dc_block_apply(y, T52.DC_FC, FS)
        y = apply_sections(y, _HPIDX)
    elif low == 'dc1':
        y = T52.dc_block_apply(y, T52.DC_FC, FS)
    elif low == 'hp4':
        y = apply_sections(y, _HPIDX)
    if notch:
        y = apply_sections(y, list(range(_NN)))
    if lp:
        y = sosfilt(butter(lp, FC_LP / (FS / 2), output='sos'), y)
    return y


def metrics(t, y, i0, i1):
    """以脉冲前 280~30 ms 中位为 0 基准，给出顶部/边沿读数。"""
    b = float(np.median(y[i0 - int(0.28 * FS):i0 - int(0.03 * FS)]))
    u = y - b
    out = {}
    out['p_near'] = float(np.median(u[i0 + int(0.030 * FS):i0 + int(0.050 * FS)]))
    out['p_far'] = float(np.median(u[i1 - int(0.020 * FS):i1]))
    # 峰值 / 谷值（用插值消栅格漏峰）
    tp = np.arange(t[i0] - 0.005, t[i0] + 0.060, 1.0 / (FS * 20))
    up_p = np.interp(tp, t, u)
    out['peak'] = float(np.max(up_p))
    out['peak_t'] = float(tp[int(np.argmax(up_p))] - t[i0])
    tf_ = np.arange(t[i1] - 0.005, t[i1] + 0.060, 1.0 / (FS * 20))
    up_v = np.interp(tf_, t, u)
    out['valley'] = float(np.min(up_v))
    out['valley_t'] = float(tf_[int(np.argmin(up_v))] - t[i1])
    out['at20'] = float(np.interp(t[i1] + 0.020, t, u))
    out['at100'] = float(np.interp(t[i1] + 0.100, t, u))
    out['droop'] = out['p_near'] - out['p_far']
    out['base'] = b
    return out


CFG = [
    ('现状低端 仅(dc1∘HP4)',      dict(low='dc1_hp4'),           '低端孤见'),
    ('现状高端 仅(LP4)',          dict(low='none', lp=4),        '高端孤见'),
    ('现状整链 dc1+HP4+LP4',      dict(low='dc1_hp4', lp=4),     '叠加'),
    ('现状整链 + 9节陷波',        dict(low='dc1_hp4', lp=4, notch=True), '陷波贡献'),
    ('修法 dc1+LP2',              dict(low='dc1', lp=2),         '修法'),
    ('修法 dc1+LP3',              dict(low='dc1', lp=3),         '修法'),
    ('只降低端 dc1+HP4→LP4',      dict(low='dc1', lp=4),         '单降低端'),
    ('只降高端 dc1+HP4+LP2',      dict(low='dc1_hp4', lp=2),     '单降高端'),
]


def main():
    hdr('[0] 实测数据：降沿下冲的时程（对齐中位模板，14 段）')
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), T52.F_MAIN)
    d = T52.load_raw(path)
    y = d['uv5']
    tr, tf, step = T52.find_edges(y, FS)
    g, m = T52.template(y, tr)
    vy = float(np.min(m[(g >= 0.095) & (g <= 0.200)]))
    vt = float(g[int(np.argmin(np.where((g >= 0.095) & (g <= 0.200), m, 1e9)))] - 0.100)
    print('  段数 %d  平台(前) %.1f  平台(末) %.1f  顶部下垂 %.1f µV (%.2f%%)'
          % (len(tr),
             np.median([np.median(m[(g >= 0.030) & (g <= 0.050)])]),
             np.median([np.median(m[(g >= 0.080) & (g <= 0.099)])]),
             np.nan, np.nan))
    print('  降沿谷值 %.1f µV  出现在降沿后 %+.1f ms   占标称 %.2f%%'
          % (vy, vt * 1000.0, abs(vy) / A_IN * 100.0))
    for dt_ in (0.005, 0.010, 0.020, 0.050, 0.100, 0.200, 0.400, 0.700, 0.930):
        v = float(np.interp(0.100 + dt_, g, m))
        print('    降沿后 +%.3f s : %+9.1f µV   (%5.2f%% 标称)' % (dt_, v, abs(v) / A_IN * 100.0))

    hdr('[1] 模型：降沿下冲的成分分解（20 s 隔离，L=%d 边沿圆化，3 mV/100 ms）' % L_AFE)
    print('  %-24s %9s %9s %9s %9s %9s %9s' %
          ('配置', '顶下垂', '峰+', '谷-', '谷@+20ms', '谷/标称', '峰/标称'))
    t, x, (i0, i1) = pulse(20.0, 3)
    for lab, kw, _ in CFG:
        yy = chan(x, **kw)
        mt = metrics(t, yy, i0, i1)
        print('  %-24s %9.1f %9.1f %9.1f %9.1f %8.2f%% %8.2f%%'
              % (lab, mt['droop'], mt['peak'] - mt['p_near'], mt['valley'],
                 mt['at20'], abs(mt['valley']) / A_IN * 100.0, mt['peak'] / A_IN * 100.0))
    print('  说明：峰+/谷- 均以「前基线」为 0；顶下垂 = 近平台 − 末端平台。')

    hdr('[2] 两种解读下的判词（限：位移 100 µV / 斜率 300 µV/s / 过冲 10% = 300 µV）')
    t, x, (i0, i1) = pulse(20.0, 3)
    print('  %-24s %11s %11s %13s %13s' %
          ('配置', '位移+0.02s', '谷/标称', '过冲(升沿)', '过冲(降沿)'))
    for lab, kw, _ in CFG:
        yy = chan(x, **kw)
        mt = metrics(t, yy, i0, i1)
        over_up = (mt['peak'] - mt['p_near']) / mt['p_near'] * 100.0
        over_dn = abs(mt['valley']) / A_IN * 100.0
        v = lambda x_: ('%.2f%% %s' % (x_, '✓' if x_ < LIM_OVER else '✗'))
        print('  %-24s %10.1f %10.1f %16s %16s'
              % (lab, abs(mt['at20']), abs(mt['valley']),
                 '%.2f%% %s' % (over_up, '✓' if over_up < LIM_OVER else '✗'),
                 '%.2f%% %s' % (over_dn, '✓' if over_dn < LIM_OVER else '✗')))
    print('  ⚠ 过冲(升沿) 的分母用「近平台」；过冲(降沿) 的分母用标称幅值 3 mV。')

    hdr('[3] 脉冲间隔对「谷/位移」读数的影响（2 s vs 10 s vs 20 s）')
    print('  %-24s %7s %11s %11s %11s' % ('配置', '间隔s', '位移+0.02s', '谷/标称', '谷@+0.4s'))
    for lab, kw, _ in CFG:
        if kw.get('notch'):
            continue
        for iv in (2.0, 10.0, 20.0):
            n = 12 if iv <= 3 else (4 if iv <= 12 else 3)
            t2, x2, (j0, j1) = pulse(iv, n)
            yy = chan(x2, **kw)
            mt = metrics(t2, yy, j0, j1)
            print('  %-24s %7.0f %10.1f %10.2f%% %11.1f'
                  % (lab, iv, abs(mt['at20']), abs(mt['valley']) / A_IN * 100.0, mt['at100']))

    hdr('[4] 峰值/谷值对称性（验证「边沿过冲」是同一现象的上/下两个方向）')
    print('  %-24s %11s %11s %11s' % ('配置', '峰−平台', '平台−谷', '比值'))
    t, x, (i0, i1) = pulse(20.0, 3)
    for lab, kw, _ in CFG:
        if kw.get('low') != 'none' or not kw.get('lp'):
            continue
        yy = chan(x, **kw)
        mt = metrics(t, yy, i0, i1)
        a = mt['peak'] - mt['p_near']
        b = 0.0 - mt['valley']
        print('  %-24s %11.1f %11.1f %11.2f' % (lab, a, b, b / a if a else np.nan))


if __name__ == '__main__':
    main()
