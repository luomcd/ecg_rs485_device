# -*- coding: utf-8 -*-
"""
T62 出图：滑窗判据的机制归因 ＋「低端降 1 阶」的落点

图 a  T62_a_滑窗机制归因.png
  ① 四态对照：滑窗 100 ms（对数轴）＋ σ差分 ⇒ 读数由 notch 开关决定，不由链的新旧决定
  ② 窗内原始采样：OFF 是噪声抖动，ON 是陷波器边沿振铃
  ③ 四链三判据（归一化到各自限值）：把「低端」逐级拆开
  ④ σ差分 vs 滑窗 散点：两者解耦

图 b  T62_b_低端降1阶落点.png
  ① 位移 ② 滑窗 ③ 过冲（两份输入 × 5 链，带限值线）
  ④ 合成对照：真输入 vs「理想方波＋白噪声」⇒ 读数里噪声占多少
"""
import os
import sys

import numpy as np
import scipy.signal as ss

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import rcParams

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _t51_yy0885_pulse as T51                  # noqa: E402
import _t56_raw853_lowfreq as T56                # noqa: E402
import _t60_fc250_onboard as T60                 # noqa: E402
import _t62_sw_mechanism as T62                  # noqa: E402

FS = T51.FS
UVPC = T56.UVPC

rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
rcParams['axes.unicode_minus'] = False
rcParams['figure.facecolor'] = '#FFFFFF'
rcParams['axes.facecolor'] = '#FFFFFF'
rcParams['axes.edgecolor'] = '#B0B7C0'
rcParams['axes.labelcolor'] = '#1F2328'
rcParams['xtick.color'] = '#3A4149'
rcParams['ytick.color'] = '#3A4149'
rcParams['text.color'] = '#1F2328'
rcParams['axes.titlecolor'] = '#111418'

C_OK = '#2E7D32'
C_BAD = '#C62828'
C_MEAS = '#1565C0'
C_L1 = '#6A1B9A'
C_L2 = '#EF6C00'
C_L3 = '#00838F'
C_L4 = '#2E7D32'
C_ON = '#C62828'
C_OFF = '#1565C0'
GRID = '#E5E7EB'
LIM = '#8A94A0'

F_OFF = '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 200724.txt'
F_ON = '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 195208.txt'
F_OLDOFF = '3mv-0.1hz-raw853on-notchoff-2026-09-20 182243.txt'
F_OLDON = '3mv-0.1hz-raw853on-2026-09-20 180458.txt'


def style(ax, title='', xl='', yl=''):
    ax.set_title(title, fontsize=10.5, pad=7)
    ax.set_xlabel(xl, fontsize=9)
    ax.set_ylabel(yl, fontsize=9)
    ax.grid(True, color=GRID, lw=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    ax.tick_params(labelsize=8.5)


def load(fn):
    d = T56.load7(os.path.join(HERE, fn))
    t = (d['idx'] - d['idx'][0]) / FS
    pulses, _thr, bl = T56.find_pulses(d['raw'])
    rep = float(np.median([t[pulses[k][0]] - t[pulses[k - 1][0]] for k in range(1, len(pulses))]))
    return d, t, pulses, bl, rep


def plat_mask(t, pulses):
    return (t >= t[pulses[1][1]] + 1.5) & (t <= t[pulses[2][0]] - 0.5)


def T62_plot_seg_sw(y, t, pulses, anch='fall', a=0.02, b=2.0, w=0.100):
    """在「降沿后 [a,b]」或「升沿前 [b,a]」段内逐脉冲取 100 ms 滑窗最大值，再取中位。"""
    vs = []
    for (s0, e0) in pulses:
        base = t[e0] if anch == 'fall' else t[s0]
        x0 = base + a if anch == 'fall' else base - a
        x1 = base + b if anch == 'fall' else base - b
        lo, hi = min(x0, x1), max(x0, x1)
        if lo < t[0] or hi + w > t[-1] or hi - w <= lo:
            continue
        xs = np.arange(lo, hi - w, 0.0002)
        if len(xs) < 10:
            continue
        z = np.abs(np.interp(xs + w, t, y) - np.interp(xs, t, y)) / w
        vs.append(float(z.max()))
    return float(np.median(vs)) if vs else float('nan')


def win_trace(y, t, pulses, ip=1, w=0.100):
    """返回第 ip 个脉冲降沿后的 (argmax 窗口起点, 读数, 位置数组, 斜率数组)。"""
    te = t[pulses[ip][1]]
    xs = np.arange(te + 0.020, min(te + 2.0, t[-1] - w), 0.0002)
    ya = np.interp(xs, t, y)
    yb = np.interp(xs + w, t, y)
    z = np.abs(yb - ya) / w
    k = int(np.argmax(z))
    return xs[k], float(z[k]), xs - te, z


# ================================================================== 数据

print('loading ...')
cases = []
for lab, fn, st in (('旧链 ON', F_OLDON, 'ON'), ('新链 ON\n(名为 OFF)', F_ON, 'ON'),
                    ('旧链 OFF', F_OLDOFF, 'OFF'), ('新链 OFF', F_OFF, 'OFF')):
    d, t, pulses, bl, rep = load(fn)
    y = d['filt'] * UVPC
    mp = plat_mask(t, pulses)
    R = T56.judge(y, t, pulses, FS, rep)
    sm, v, _l, _d = T62.sw_med(y, t, pulses, 0.100)
    _pk, _tp, op = T60.per_pulse(y, t, pulses, FS)
    cases.append(dict(lab=lab, st=st, dtc=float(np.median(d['dtc'])), y=y, t=t, pulses=pulses,
                      sd=T62.sd_diff(y[mp]), sw=sm, swp=v, disp=R['disp_long'][0.100],
                      op=op.max(), win=win_trace(y, t, pulses)))
    print('  %-16s dtc=%.0f  sw=%.1f  sd=%.3f' % (lab.replace('\n', ' '), np.median(d['dtc']), sm, T62.sd_diff(y[mp])))

s_n, nn, hp_n, k_n = T60.chain_of(T51.COEFFS_C, T51.COEFFS_H)
s_o, nn_o, hp_o, k_o = T60.chain_of(os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter.c.bak'),
                                    os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter_coeffs.h.bak'))
F, _a, _b, _c = T62.chains({'new': (s_n, nn, hp_n, k_n)}, 'plot')
CH = [('dc1', C_L1, F['L1 dc1']), ('dc1+HP4', C_L2, F['L2 dc1+HP4']),
      ('dc1+HP4+LP250', C_L3, F['L3 dc1+HP4+LP250']), ('dc1+LP250', C_L4, F['L4 dc1+LP250'])]

print('chains ...')
d0, t0, p0, bl0, rep0 = load(F_OFF)
xin0 = np.asarray(d0['raw'], float) * UVPC
yme0 = np.asarray(d0['filt'], float) * UVPC
mp0 = plat_mask(t0, p0)
chain_off = {}
for lab, col, fn in CH:
    yy = fn(xin0)
    R = T56.judge(yy, t0, p0, FS, rep0)
    sm, v, _l, _d = T62.sw_med(yy, t0, p0, 0.100)
    _pk, _tp, op = T60.per_pulse(yy, t0, p0, FS)
    chain_off[lab] = dict(y=yy, sw=sm, swp=v, disp=R['disp_long'][0.100], op=op.max(),
                          sd=T62.sd_diff(yy[mp0]))
    print('  %-16s sw=%.1f  disp=%.1f  op=%.2f%%' % (lab, sm, R['disp_long'][0.100], op.max()))

mlab = np.asarray([c['lab'] for c in cases])
msw = np.asarray([c['sw'] for c in cases])
msd = np.asarray([c['sd'] for c in cases])

# ================================================================== 图 a

fig, axs = plt.subplots(2, 2, figsize=(13.2, 9.0))
fig.suptitle('T62 · 滑窗判据「终点后斜率 ≤300 µV/s」的机制归因（实测列⑤，与链模型无关）',
             fontsize=12.5, y=0.985)

# ① 四态对照
ax = axs[0][0]
xs = np.arange(len(cases))
cols = [C_ON if c['st'] == 'ON' else C_OFF for c in cases]
bars = ax.bar(xs, msw, color=cols, width=0.55, zorder=3)
for x, v_, s_ in zip(xs, msw, msd):
    ax.text(x, v_ * 1.15, '%.0f' % v_, ha='center', fontsize=8.5, color='#1F2328')
ax.axhline(300, color=LIM, ls='--', lw=1.4, zorder=2)
ax.text(len(cases) - 0.45, 330, '判限 300', fontsize=8, color=LIM, ha='right')
ax.set_yscale('log')
ax.set_ylim(100, 30000)
ax.set_xticks(xs)
ax.set_xticklabels(mlab, fontsize=8.5)
ax2 = ax.twinx()
ax2.plot(xs, msd, 'o-', color='#455A64', lw=1.6, ms=6, zorder=4, label='σ差分')
for x, s_ in zip(xs, msd):
    ax2.annotate('σ=%.2f' % s_, (x, s_), textcoords='offset points', xytext=(0, -14),
                 ha='center', fontsize=7.5, color='#37474F', zorder=6,
                 bbox=dict(fc='white', ec='none', alpha=0.85, pad=0.8))
ax2.set_ylabel('σ差分 (µV)', fontsize=9, color='#455A64')
ax2.tick_params(labelsize=8, colors='#455A64')
ax2.set_ylim(0, 13)
style(ax, '① 四态对照：读数跟着 notch 开关走，不跟链的新旧走', '', '滑窗 100 ms (µV/s，对数轴)')
ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=C_ON),
                   plt.Rectangle((0, 0), 1, 1, color=C_OFF),
                   plt.Line2D([], [], color='#455A64', marker='o', ls='-')],
          labels=['notch ON', 'notch OFF', 'σ差分 (右轴)'], fontsize=8, loc='upper right')

# ② 窗内原始采样
ax = axs[0][1]
for c, col in zip(cases, [C_ON, C_ON, C_OFF, C_OFF]):
    pass
c_off = [c for c in cases if c['lab'] == '新链 OFF'][0]
c_on = [c for c in cases if c['lab'].startswith('新链 ON')][0]
for cc, col, nm in ((c_off, C_OFF, '新链 OFF (200724)'), (c_on, C_ON, '新链 ON (195208)')):
    x0, mx, _xs, _z = cc['win']
    g = np.arange(0.0, 0.1001, 0.0005)
    v = np.interp(x0 + g, cc['t'], cc['y'])
    v = v - v[0]
    ax.plot(g * 1000, v, color=col, lw=1.4, label='%s：%.0f µV/s' % (nm, mx))
ax.axhline(0, color=GRID, lw=0.8)
style(ax, '② 窗内原始采样（0.5 ms 栅格）：OFF 噪声抖动 vs ON 陷波振铃', '距降沿 (ms)', '相对窗首 (µV)')
ax.legend(fontsize=8, loc='lower right')
ax.text(0.03, 0.06, 'OFF：无规律抖动\nON：单向下冲 822 µV 后回弹',
        transform=ax.transAxes, fontsize=8.5, color='#3A4149',
        bbox=dict(fc='#F5F7FA', ec='#D8DEE6', boxstyle='round,pad=0.4'))

# ③ 四链三判据（归一化）
ax = axs[1][0]
labs = [c[0] for c in CH]
disp = [chain_off[c[0]]['disp'] / 100.0 for c in CH]
swv = [chain_off[c[0]]['sw'] / 300.0 for c in CH]
opv = [chain_off[c[0]]['op'] / 10.0 for c in CH]
xs = np.arange(len(labs))
w = 0.26
b1 = ax.bar(xs - w, np.abs(disp), w, color=C_MEAS, zorder=3, label='|位移+0.1s| / 100')
b2 = ax.bar(xs, swv, w, color=C_L3, zorder=3, label='滑窗100 / 300')
b3 = ax.bar(xs + w, opv, w, color=C_L2, zorder=3, label='过冲 / 10')
for bb, vs in ((b1, np.abs(disp)), (b2, swv), (b3, opv)):
    for r, v_ in zip(bb, vs):
        ax.text(r.get_x() + r.get_width() / 2, v_ + 0.03, '%.2f' % v_, ha='center', fontsize=7.5)
ax.axhline(1.0, color=LIM, ls='--', lw=1.4, zorder=2)
ax.text(0.015, 1.03, '判限 =1.00', fontsize=8, color=LIM, ha='left', zorder=7,
        bbox=dict(fc='white', ec='none', alpha=0.85, pad=0.8))
ax.set_xticks(xs)
ax.set_xticklabels(['L1\ndc1', 'L2\ndc1+HP4', 'L3\ndc1+HP4+LP250\n(现场)', 'L4\ndc1+LP250\n(低端降1阶)'],
                   fontsize=8.5)
style(ax, '③ 把「低端」逐级拆开（200724 新链 OFF 输入）', '', '读数 ÷ 各自判限')
ax.legend(fontsize=8.5, ncol=3, loc='upper left')

# ④ 三段对照：判据区间并不特殊
ax = axs[1][1]
SEGS = [('[0.02, 2.0]s\n判据区间', 1, 0.02, 2.0),
        ('[2.5, 9.0]s\n纯低电平段', 1, 2.5, 9.0),
        ('升沿前 1~4.5s\n高电平段', -1, 4.5, 1.0)]
YALL = [('实测\n列⑤', yme0)] + [(l.replace('+LP250', '+LP'), chain_off[l]['y']) for l in labs]
xs3 = np.arange(len(YALL))
w3 = 0.26
for j, (lab, sgn, aa, bb) in enumerate(SEGS):
    vals = [T62_plot_seg_sw(y, t0, p0, 'fall' if sgn > 0 else 'rise', aa, bb) for _nm, y in YALL]
    bb3 = ax.bar(xs3 + (j - 1) * w3, vals, w3, color=['#1565C0', '#00838F', '#EF6C00'][j],
                 zorder=3, label=lab.replace('\n', ' '))
    for r, v_ in zip(bb3, vals):
        ax.text(r.get_x() + r.get_width() / 2, v_ + 9, '%.0f' % v_, ha='center', fontsize=6.8)
ax.axhline(300, color=LIM, ls='--', lw=1.4, zorder=2)
ax.text(len(xs3) - 0.5, 312, '判限 300', fontsize=8, color=LIM, ha='right', zorder=7,
        bbox=dict(fc='white', ec='none', alpha=0.85, pad=0.8))
ax.set_xticks(xs3)
ax.set_xticklabels([n for n, _ in YALL], fontsize=8)
ax.set_ylim(0, 700)
style(ax, '④ 判据区间并不特殊：换到纯低电平段/高电平段，读数几乎一样', '', '滑窗 100 ms (µV/s)')
ax.legend(fontsize=8, ncol=3, loc='lower center')

fig.tight_layout(rect=(0, 0, 1, 0.965))
p1 = os.path.join(HERE, 'T62_a_滑窗机制归因_2026-09-20.png')
fig.savefig(p1, dpi=150)
plt.close(fig)
print('saved', p1)

# ================================================================== 图 b

print('case 182243 ...')
d1, t1, p1_, bl1, rep1 = load(F_OLDOFF)
xin1 = np.asarray(d1['raw'], float) * UVPC
yme1 = np.asarray(d1['filt'], float) * UVPC
mp1 = plat_mask(t1, p1_)
chain_alt = {}
for lab, col, fn in CH:
    yy = fn(xin1)
    R = T56.judge(yy, t1, p1_, FS, rep1)
    sm, v, _l, _d = T62.sw_med(yy, t1, p1_, 0.100)
    _pk, _tp, op = T60.per_pulse(yy, t1, p1_, FS)
    chain_alt[lab] = dict(sw=sm, swp=v, disp=R['disp_long'][0.100], op=op.max())
    print('  %-16s sw=%.1f disp=%.1f' % (lab, sm, R['disp_long'][0.100]))

R1 = T56.judge(yme1, t1, p1_, FS, rep1)
sm1 = T62.sw_med(yme1, t1, p1_, 0.100)[0]
op1 = T60.per_pulse(yme1, t1, p1_, FS)[2].max()
c_off0 = [c for c in cases if c['lab'] == '新链 OFF'][0]

fig, axs = plt.subplots(2, 2, figsize=(13.2, 9.0))
fig.suptitle('T62 · 「低端降 1 阶」（删 HP4）的落点：三判据里能解决 2 个，滑窗另需办法',
             fontsize=12.5, y=0.985)

GROUP = ['实测列⑤', 'dc1', 'dc1+HP4', 'dc1+HP4+LP250', 'dc1+LP250']
SHORT = ['实测', 'L1', 'L2', 'L3\n(现场)', 'L4\n(降1阶)']


def grouped(ax, key, limit, title, yl, fmt='%.1f', measure=None,
            meas_off=None, meas_alt=None):
    off = [measure(chain_off[l][key]) for l in GROUP[1:]]
    alt = [measure(chain_alt[l][key]) for l in GROUP[1:]]
    if meas_off is not None:
        off = [meas_off] + off
        alt = [meas_alt] + alt
    xs = np.arange(len(off))
    w = 0.36
    b1 = ax.bar(xs - w / 2, off, w, color=C_OFF, zorder=3, label='200724 新链OFF')
    b2 = ax.bar(xs + w / 2, alt, w, color='#7E57C2', zorder=3, label='182243 旧链OFF')
    for bb, vs in ((b1, off), (b2, alt)):
        for r, v_ in zip(bb, vs):
            ax.text(r.get_x() + r.get_width() / 2, v_ + limit * 0.02, fmt % v_,
                    ha='center', fontsize=7.2)
    ax.axhline(limit, color=LIM, ls='--', lw=1.4, zorder=2)
    ax.text(len(xs) - 0.5, limit * 1.05, '判限 %g' % limit, fontsize=8, color=LIM, ha='right',
            zorder=7, bbox=dict(fc='white', ec='none', alpha=0.85, pad=0.8))
    ax.set_xticks(xs)
    ax.set_xticklabels(SHORT[:len(xs)], fontsize=8.5)
    style(ax, title, '', yl)
    ax.legend(fontsize=8, loc='upper right')


grouped(axs[0][0], 'disp', 100.0, '① 位移 +0.1 s：L3 是 3.3 倍限，L4 降到 0.83 倍（达标）',
        'µV（限 100）', fmt='%.0f', measure=abs,
        meas_off=abs(c_off0['disp']), meas_alt=abs(R1['disp_long'][0.100]))
grouped(axs[0][1], 'op', 10.0, '② 过冲（逐脉冲最差）：L3 是 1.20 倍限，L4 降到 0.74 倍（达标）',
        '%（限 10）', fmt='%.2f', measure=lambda v: v,
        meas_off=c_off0['op'], meas_alt=op1)
grouped(axs[1][0], 'sw', 300.0, '③ 滑窗 100 ms：L3 降 13% / 30%，200724 上 L4 仍 1.55 倍限（不达标）',
        'µV/s（限 300）', fmt='%.0f', measure=lambda v: v,
        meas_off=c_off0['sw'], meas_alt=sm1)

# ④ 合成对照
ax = axs[1][1]
sd_plat = T62.sd_diff(xin0[mp0])
A = abs(float(np.mean([np.median(xin0[u[0] + int(0.05 * FS):u[1] - int(0.05 * FS)]) for u in p0]))
        - bl0 * UVPC)
rng = np.random.default_rng(20260920)
sq = np.zeros_like(xin0)
for (s0, e0) in p0:
    sq[s0:e0] = A
labels, vals, cols = [], [], []
for nm, yy, col in (('真输入\n实测列⑤', yme0, C_MEAS),
                    ('真输入 L3', chain_off['dc1+HP4+LP250']['y'], C_L3),
                    ('真输入 L4', chain_off['dc1+LP250']['y'], C_L4)):
    labels.append(nm)
    vals.append(T62.sw_med(yy, t0, p0, 0.100)[0])
    cols.append(col)
for nm, fn, col in (('合成方波\n+白噪声 L3', F['L3 dc1+HP4+LP250'], '#B0BEC5'),
                    ('合成方波\n+白噪声 L4', F['L4 dc1+LP250'], '#90A4AE')):
    yy = fn(sq + rng.normal(0.0, sd_plat, len(sq)))
    labels.append(nm)
    vals.append(T62.sw_med(yy, t0, p0, 0.100)[0])
    cols.append(col)
xs = np.arange(len(labels))
ax.bar(xs, vals, 0.6, color=cols, zorder=3)
for x, v_ in zip(xs, vals):
    ax.text(x, v_ + 12, '%.0f' % v_, ha='center', fontsize=8.5)
ax.axhline(300, color=LIM, ls='--', lw=1.4, zorder=2)
ax.text(len(labels) - 0.5, 315, '判限 300', fontsize=8, color=LIM, ha='right')
ax.set_xticks(xs)
ax.set_xticklabels(labels, fontsize=8)
style(ax, '④ 合成对照：换掉输入只留同量级白噪声，读数几乎不变', '',
      '滑窗 100 ms (µV/s)')
ax.text(0.03, 0.72, 'σ(白噪声)=%.1f µV\n读数主体是噪声，不是输入细节' % sd_plat,
        transform=ax.transAxes, fontsize=8.5, color='#3A4149',
        bbox=dict(fc='#F5F7FA', ec='#D8DEE6', boxstyle='round,pad=0.4'))

fig.tight_layout(rect=(0, 0, 1, 0.965))
p2 = os.path.join(HERE, 'T62_b_低端降1阶落点_2026-09-20.png')
fig.savefig(p2, dpi=150)
plt.close(fig)
print('saved', p2)
