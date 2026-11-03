# -*- coding: utf-8 -*-
"""T61 出图：三判据并排 + 滑窗机制（噪声主导）"""
import io
import os
import sys

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t56_raw853_lowfreq as T56
import _t60_fc250_onboard as T60
import _t61_lowend1_predict as T61

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 130

FS = T56.FS
F1 = '3mv-0.1hz-raw853on-notchoff-fc250-2026-09-20 200724.txt'      # 新链 OFF
F2 = '3mv-0.1hz-raw853on-notchoff-2026-09-20 182243.txt'            # 旧链 OFF


def load(path):
    d = T56.load7(path)
    idx, raw, filt = d['idx'], d['raw'], d['filt']
    t = (idx - idx[0]) / FS
    pl, thr, bl = T56.find_pulses(raw)
    xin = raw * T56.UVPC
    s_new, nn, hp_n, _ = T60.chain_of(os.path.join(HERE, '..', 'Filter', 'ecg_filter.c'),
                                      os.path.join(HERE, '..', 'Filter', 'ecg_filter_coeffs.h'))
    s_old, _, hp_o, _ = T60.chain_of(os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter.c.bak'),
                                     os.path.join(HERE, '_bak_T58_20260920', 'ecg_filter_coeffs.h.bak'))
    ys = {'输入 raw': xin,
          '实测(列⑤)': filt * T56.UVPC,
          '旧链OFF': T60.run_chain(s_old, nn, hp_o, xin, False),
          '新链OFF': T60.run_chain(s_new, nn, hp_n, xin, False),
          '未来链OFF': T61.run_off_future(s_new, nn, hp_n, xin)}
    Rs = {k: T56.judge(v, t, pl, FS, 10.0) for k, v in ys.items()}
    # 逐脉冲过冲
    pp = {k: T60.per_pulse(v, t, pl, FS)[2] for k, v in ys.items()}
    return dict(t=t, pl=pl, ys=ys, Rs=Rs, pp=pp)


def sd_diff(v):
    return float(np.std(np.diff(v)) / np.sqrt(2.0))


A = load(F1)
B = load(F2)

# ============ 图 1：降沿后波形（看噪声）与判据并排 ============
fig = plt.figure(figsize=(13.5, 8.6))
gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.15], hspace=0.36, wspace=0.24)

ax = fig.add_subplot(gs[0, :])
te = A['t'][A['pl'][1][1]]
m = (A['t'] >= te - 0.10) & (A['t'] <= te + 1.20)
_mp = (A['t'] >= te + 1.5) & (A['t'] <= te + 9.5)      # 平台段（远离边沿与恢复尾）
for k, c, lw in (('输入 raw', '#888888', 0.9), ('实测(列⑤)', '#c0392b', 1.0),
                 ('旧链OFF', '#2980b9', 1.0), ('未来链OFF', '#27ae60', 1.0)):
    v = A['ys'][k]
    b0 = np.median(v[(A['t'] >= te - 5.0) & (A['t'] <= te - 0.05)])
    ax.plot(A['t'][m] - te, (v[m] - b0), lw=lw,
            label='%s (σ=%.2f µV)' % (k, sd_diff(v[_mp])))
ax.axvline(0, color='k', ls=':', lw=0.8)
ax.set_xlabel('降沿后时间 (s)')
ax.set_ylabel('µV（去降沿前 5 s 基线）')
ax.set_title('① 降沿后波形叠加（200724 · 新链 OFF 实测）—— 平台段噪声决定滑窗读数', fontsize=11)
ax.legend(fontsize=8.5, ncol=4, loc='lower right')
ax.grid(alpha=0.25)

ax = fig.add_subplot(gs[1, 0])
labs = ['实测(列⑤)', '旧链OFF', '新链OFF', '未来链OFF']
short = ['实测', '旧链OFF', '新链OFF', '未来链OFF']
x = np.arange(len(labs)); w = 0.55
vals = [abs(A['Rs'][k]['disp_long'][0.100]) for k in labs]
cols = ['#c0392b', '#2980b9', '#8e44ad', '#27ae60']
b = ax.bar(x, vals, w, color=cols, alpha=0.85)
ax.axhline(100, color='r', ls='--', lw=1.2, label='判限 100 µV')
for xi, v in zip(x, vals):
    ax.text(xi, v + 8, '%.0f' % v, ha='center', fontsize=9,
            color='g' if v <= 100 else 'r', fontweight='bold')
ax.set_xticks(x); ax.set_xticklabels(short, fontsize=9)
ax.set_ylabel('µV')
ax.set_title('② 位移 (降沿 +0.100 s)\n仅「低端降 1 阶」可解', fontsize=10)
ax.grid(alpha=0.25, axis='y')

ax = fig.add_subplot(gs[1, 1])
sw = [A['Rs'][k]['slope_win'][0.100] for k in labs]
b = ax.bar(x, sw, w, color=cols, alpha=0.85)
ax.axhline(300, color='r', ls='--', lw=1.2, label='判限 300 µV/s')
b[3].set_hatch('///'); b[3].set_edgecolor('k')
for xi, v in zip(x, sw):
    ax.text(xi, v + 12, '%.0f' % v, ha='center', fontsize=9,
            color='g' if v <= 300 else 'r', fontweight='bold')
ax.set_xticks(x); ax.set_xticklabels(short, fontsize=9)
ax.set_ylabel('µV/s')
ax.set_title('③ 滑窗 100 ms —— ★噪声主导（≈5σ/0.1s）\n降阶无效，须降噪或降带宽', fontsize=10)
ax.legend(fontsize=8)
ax.grid(alpha=0.25, axis='y')

plt.savefig(os.path.join(HERE, 'T61_a_三判据并排.png'), bbox_inches='tight')
plt.close()

# ============ 图 2：滑窗 vs σ 关系式 + 逐脉冲离散 ============
fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.3))

# 2a σ 对比（两份输入）
ax = axes[0]
keys = ['输入 raw', '实测(列⑤)', '旧链OFF', '新链OFF', '未来链OFF']
def sds(D, key):
    te_ = D['t'][D['pl'][1][1]]
    mp = (D['t'] >= te_ + 1.5) & (D['t'] <= te_ + 1.5 + 8.0)
    return sd_diff(D['ys'][key][mp])
xa = np.arange(len(keys)); w2 = 0.38
va = [sds(A, k) for k in keys]
vb = [sds(B, k) for k in keys]
ax.bar(xa - w2 / 2, va, w2, label='200724 输入', color='#c0392b', alpha=0.85)
ax.bar(xa + w2 / 2, vb, w2, label='182243 输入', color='#2980b9', alpha=0.85)
ax.axhline(6.0, color='r', ls='--', lw=1.2, label='达标上限 σ≤6.0')
ax.set_xticks(xa); ax.set_xticklabels(['输入', '实测', '旧链OF', '新链OF', '未来链'], fontsize=8.5)
ax.set_ylabel('输出噪声 σ (µV，差分法)')
ax.set_title('④ 输出噪声 σ（滑窗的驱动力）\nfc250: 6.95→8.83 (+27%)', fontsize=10)
ax.legend(fontsize=8)
ax.grid(alpha=0.25, axis='y')

# 2b σ vs 滑窗（关系式）
ax = axes[1]
pts = []
for D, mk, cc in ((A, 'o', '#c0392b'), (B, 's', '#2980b9')):
    for k in ('旧链OFF', '新链OFF', '未来链OFF'):
        pts.append((sds(D, k), D['Rs'][k]['slope_win'][0.100], mk, cc, k if D is A else ''))
xs = np.linspace(4, 11, 50)
ax.plot(xs, xs * 5 / 0.1, 'k--', lw=1.2, label='理论 5σ/0.1 s')
for s_, v, mk, cc, lb in pts:
    ax.scatter([s_], [v], marker=mk, s=52, color=cc, zorder=3)
ax.scatter([], [], marker='o', color='#c0392b', label='200724 输入')
ax.scatter([], [], marker='s', color='#2980b9', label='182243 输入')
ax.axhline(300, color='r', ls=':', lw=1.4, label='判限 300')
ax.axvline(6.0, color='g', ls=':', lw=1.4, label='σ 上限 6.0')
ax.set_xlabel('输出噪声 σ (µV)'); ax.set_ylabel('滑窗 100 ms (µV/s)')
ax.set_title('⑤ 滑窗 = 噪声极值（≈5σ/0.1s）\n旧链 6.954→347.7 vs 实测 347.1（0.1%）', fontsize=10)
ax.legend(fontsize=7.5)
ax.grid(alpha=0.25)

# 2c 逐脉冲离散
ax = axes[2]
np_ = len(A['pl'])
xa = np.arange(np_)
for k, cc, mk in (('实测(列⑤)', '#c0392b', 'o'), ('旧链OFF', '#2980b9', 's'), ('未来链OFF', '#27ae60', '^')):
    ax.plot(xa + 1, A['pp'][k], marker=mk, color=cc, lw=1.4, label=k.replace('(列⑤)',''))
ax.axhline(10, color='r', ls='--', lw=1.2, label='判限 10%')
ax.set_xticks(xa + 1); ax.set_xticklabels(['P%d' % (i + 1) for i in range(np_)], fontsize=9)
ax.set_ylabel('逐脉冲过冲 (%)')
ax.set_title('⑥ 逐脉冲过冲（T59 纪律：报最差）\n未来链 4.5~7.4% 全部达标', fontsize=10)
ax.legend(fontsize=8)
ax.grid(alpha=0.25, axis='y')

plt.tight_layout()
plt.savefig(os.path.join(HERE, 'T61_b_噪声机制.png'), bbox_inches='tight')
plt.close()
print('图已生成：T61_a_三判据并排.png / T61_b_噪声机制.png')

# 打印关键数字供报告引用
print('\n---- 关键数字 ----')
for tag, D in (('200724(新链OFF)', A), ('182243(旧链OFF)', B)):
    print('[%s]' % tag)
    for k in keys:
        te_ = D['t'][D['pl'][1][1]]
        mp = (D['t'] >= te_ + 1.5) & (D['t'] <= te_ + 1.5 + 8.0)
        print('   %-12s σ=%6.3f  µV ｜滑窗100 %8.1f ｜位移+0.1 %8.1f ｜过冲最差 %6.2f%%'
              % (k, sd_diff(D['ys'][k][mp]), D['Rs'][k]['slope_win'][0.100],
                 D['Rs'][k]['disp_long'][0.100], D['pp'][k].max()))
