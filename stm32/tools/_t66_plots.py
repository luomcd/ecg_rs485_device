# -*- coding: utf-8 -*-
"""T66 出图：fc250 在 620 kΩ 输入阻抗测试上的实测代价 ＋ 按档分离

图 a  T66_a_fc250代价.png
  ① 两链频响差（100~450 Hz）：≤150 Hz 逐点相同，差异全在 155~426
  ② 带内 rms 分解：fc250 的代价集中在 245~340 Hz
  ③ 关键指标 × 三链（离线 fc150 / 离线 fc250 / 实测列⑤）：方波幅度不变，p-p 涨

图 b  T66_b_输入阻抗多口径.png
  ① 多口径比值（fc250 vs fc150）× 110% 判据线
  ② 逐 1 s 窗 p-p 分布（直连 vs 接入）：无任何窗低于直连 ⇒「低于设定值」不成立
  ③ 落码汇总账（档位／节数／上角／输入阻抗读数）

★ 数值全部来自 `_t66_out.txt`（同一批 215252 数据、同一套函数），此处仅重绘。
"""
import os

import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import rcParams

HERE = os.path.dirname(os.path.abspath(__file__))

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

C_150 = '#2E6FD9'      # fc150（回退链）
C_250 = '#D9542E'      # fc250（当前链）
C_MEAS = '#1F9D6B'     # 实测
C_GRID = '#E3E7EC'
C_LIM = '#B3261E'

# ---------------------------------------------------------------- 数据
FQ = [1, 5, 10, 40, 100, 150, 155, 160, 180, 200, 250, 300, 350, 400]
D150 = [0.00, 0.00, 0.00, -0.45, -325.80, -331.17, -9.73, -6.45, -9.06,
        -13.70, -26.36, -40.70, -59.86, -97.41]
D250 = [0.00, 0.00, 0.00, -0.45, -325.73, -328.36, -6.19, -2.08, -0.74,
        -0.98, -3.03, -7.94, -16.85, -35.54]

BANDS = ['0.3~2', '2~45', '45~55', '55~145', '145~155', '155~245', '245~340', '340~426']
R_MEAS = [62.1, 29.6, 3.8, 4.9, 0.2, 4.1, 14.1, 0.1]     # 实测列⑤
R_250 = [62.1, 29.6, 3.8, 4.9, 0.2, 4.1, 14.1, 0.1]      # 离线 fc250
R_150 = [62.1, 29.6, 3.8, 4.9, 0.2, 1.0, 1.0, 0.0]       # 离线 fc150

# 表：名称 -> [p-p(µV), 1s窗中位, 方波幅度, rms, 平段rms]
METR = [
    ('离线 fc150（回退）', 2634.1, 1675.3, 973.4, 502.1, 55.11, C_150),
    ('离线 fc250（当前）', 2873.1, 1949.6, 980.3, 513.6, 56.44, C_250),
    ('实测 列⑤（fc250）', 2824.0, 1950.1, 979.5, 513.2, 56.39, C_MEAS),
]

# 多口径：口径 -> (fc250 比值%, fc150 比值%)
RATIO = [
    ('方波幅度', 100.05, 99.43),
    ('1s窗 p-p 中位', 105.51, 99.36),
    ('1s窗 p-p p95', 111.15, 102.53),
    ('全段 p-p', 132.19, 123.81),
    ('1s窗最大', 140.74, 132.08),
]

# 逐 1 s 窗 p-p（µV）
WIN_NO = 1728, 1730, 1741, 1747, 1754, 1765, 1770      # min/p5/p25/中位/p75/p95/max
WIN_IN = 1800, 1849, 1904, 1946, 1996, 2060, 2556
AMP_NO, AMP_IN = 977.0, 980.1


def style(ax, title, xl, yl, grid_axis='y'):
    ax.set_title(title, fontsize=10.5, pad=7)
    ax.set_xlabel(xl, fontsize=9)
    ax.set_ylabel(yl, fontsize=9)
    ax.tick_params(labelsize=8.5)
    ax.grid(True, axis=grid_axis, color=C_GRID, lw=0.8)
    ax.set_axisbelow(True)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)


# ================================================================ 图 a
def fig_a():
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 3.9))

    # ① 频响差
    ax = axes[0]
    x = np.arange(len(FQ))
    ax.plot(x, D150, '-o', color=C_150, lw=1.8, ms=4, label='fc150（LP4@150，回退）')
    ax.plot(x, D250, '-s', color=C_250, lw=1.8, ms=4, label='fc250（LP2@250，当前）')
    ax.axvspan(-0.3, 5.5, color='#EAF2FF', zorder=0)
    ax.text(2.4, -170, '≤150 Hz 逐点相同\n（差 = 0.00 dB）', ha='center', fontsize=8.4,
            color='#2E6FD9')
    ax.annotate('', xy=(9.6, -6), xytext=(6.2, -150),
                arrowprops=dict(arrowstyle='->', color=C_LIM, lw=1.4))
    ax.text(7.1, -215, '差异全在\n155~426 Hz\n（放行 3.5~1241×）', fontsize=8.2,
            color=C_LIM, ha='left', va='bottom')
    ax.set_ylim(-345, 35)
    style(ax, '① 两链频响（含 9 节陷波）', '频率（Hz）', '增益（dB）', 'both')
    ax.set_xticks(x[::2])
    ax.set_xticklabels([str(FQ[i]) for i in range(0, len(FQ), 2)], fontsize=8)
    ax.legend(fontsize=8, loc='lower left', framealpha=0.9)

    # ② 带内 rms
    ax = axes[1]
    x = np.arange(len(BANDS))
    w = 0.27
    ax.bar(x - w, R_150, w, color=C_150, label='离线 fc150（回退）')
    ax.bar(x, R_250, w, color=C_250, label='离线 fc250（当前）')
    ax.bar(x + w, R_MEAS, w, color=C_MEAS, alpha=0.85, label='实测列⑤')
    ax.set_yscale('log')
    ax.set_ylim(0.05, 200)
    for i in (6,):
        ax.annotate('245~340 Hz\n14.1 → 1.0 µV\n（14×）', xy=(i - 0.27, 14.1),
                    xytext=(i - 2.0, 55), fontsize=8.2, color=C_LIM,
                    arrowprops=dict(arrowstyle='->', color=C_LIM, lw=1.2))
    style(ax, '② 输出带内 rms 分解（620 kΩ 接入）', '频带（Hz）', 'rms（µV，对数）')
    ax.set_xticks(x)
    ax.set_xticklabels(BANDS, fontsize=7.6, rotation=20)
    ax.legend(fontsize=7.8, loc='lower left', framealpha=0.9)

    # ③ 关键指标
    ax = axes[2]
    names = [m[0] for m in METR]
    keys = [('p-p', 1), ('1s窗p-p中位', 2), ('方波幅度', 3), ('rms', 4)]
    x = np.arange(len(keys))
    w = 0.26
    for k, m in enumerate(METR):
        vals = [m[i] for _, i in keys]
        ax.bar(x + (k - 1) * w, vals, w, color=m[6],
               label='%s（%.0f µV 方波）' % (m[0], m[3]))
    ax.axhline(973.4, color=C_150, ls='--', lw=1.1)
    ax.text(3.42, 5100, '方波幅度三链几乎重合（973 / 980 / 979 µV，≤0.7%）', fontsize=7.4,
            color='#2E6FD9', ha='right', va='bottom')
    style(ax, '③ 关键指标 × 三链（同输入）', '', 'µV')
    ax.set_xticks(x)
    ax.set_xticklabels([k for k, _ in keys], fontsize=8.4)
    ax.set_ylim(0, 5600)
    ax.legend(fontsize=7.4, loc='upper left', framealpha=0.9, bbox_to_anchor=(0.0, 0.84))

    fig.suptitle('T66 · fc250 在 620 kΩ 输入阻抗测试上的实测代价（真 853 域，无折叠）',
                 fontsize=11.5, y=1.02)
    fig.tight_layout()
    p = os.path.join(HERE, 'T66_a_fc250代价_2026-09-20.png')
    fig.savefig(p, dpi=150, bbox_inches='tight')
    print('[out]', p)


# ================================================================ 图 b
def fig_b():
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 3.9))

    # ① 多口径比值
    ax = axes[0]
    labs = [r[0] for r in RATIO]
    v150 = [r[2] for r in RATIO]
    v250 = [r[1] for r in RATIO]
    y = np.arange(len(labs))[::-1]
    h = 0.36
    ax.barh(y + h / 2, v250, h, color=C_250, label='fc250（当前）')
    ax.barh(y - h / 2, v150, h, color=C_150, label='fc150（回退）')
    ax.axvline(110, color=C_LIM, ls='--', lw=1.6)
    ax.text(110.8, 4.42, '判据 110%', fontsize=8.2, color=C_LIM)
    for i, (a, b) in enumerate(zip(v250, v150)):
        ax.text(a + 1.5, y[i] + h / 2, '%.1f%%' % a, va='center', fontsize=8, color=C_250)
        ax.text(b + 1.5, y[i] - h / 2, '%.1f%%' % b, va='center', fontsize=8, color=C_150)
    ax.set_xlim(90, 155)
    style(ax, '① 输入阻抗比值（接入 / 直连）', '比值（%）', '', 'x')
    ax.set_yticks(y)
    ax.set_yticklabels(labs, fontsize=8.6)
    ax.legend(fontsize=8, loc='lower right', framealpha=0.9)

    # ② 逐窗 p-p 分布
    ax = axes[1]
    qs = ['min', 'p5', 'p25', '中位', 'p75', 'p95', 'max']
    x = np.arange(len(qs))
    ax.fill_between([-0.4, 6.4], 0, AMP_NO, color='#FFE9E3', alpha=0.55, zorder=0)
    ax.plot(x, WIN_NO, '-o', color='#7A8699', lw=1.8, ms=4.5, label='直连（24 窗）')
    ax.plot(x, WIN_IN, '-s', color=C_250, lw=1.8, ms=4.5, label='620 kΩ 接入（46 窗）')
    ax.axhline(AMP_NO, color=C_150, ls='--', lw=1.3)
    ax.text(6.35, AMP_NO + 90, '直连方波幅度 977 µV', fontsize=8, color=C_150, ha='right')
    ax.text(3.0, 470, '「低于设定值」区\n（接入侧 46 窗无一落入）', fontsize=8.2,
            color=C_LIM, ha='center', va='center')
    ax.text(3.0, 2270, '接入后 p-p 全面高于直连\n→「超标」成立、「偏低」不成立',
            fontsize=8.4, color=C_250, ha='center', va='center')
    ax.set_ylim(0, 2900)
    style(ax, '② 逐 1 s 窗 p-p 分布（列⑤，853 域）', '分位点', 'p-p（µV）')
    ax.set_xticks(x)
    ax.set_xticklabels(qs, fontsize=8.4)
    ax.legend(fontsize=8, loc='lower right', framealpha=0.95)

    # ③ 落码汇总
    ax = axes[2]
    ax.axis('off')
    rows = [
        ('档位', '节数', 'BP 上角', '输入阻抗读数'),
        ('D1/D2/D3 诊断', '不变', '250 Hz', '—'),
        ('IMP620', '8 → 9', '250 → 150', '—'),
        ('IMP620_N2', '12 → 13', '250 → 150', '105.5% → 99.4%'),
        ('ST40', '7（不变）', '40 Hz', '#error 顺带修复'),
    ]
    xs = [0.01, 0.42, 0.60, 0.80]
    ty, dy = 0.95, 0.152
    for r, row in enumerate(rows):
        yy = ty - r * dy
        for c, cell in enumerate(row):
            col = '#5A6472' if r == 0 else '#111418'
            if r and '→' in cell and c in (1, 2, 3):
                col = C_250
            if r and ('修复' in cell):
                col = '#1F9D6B'
            ax.text(xs[c], yy, cell, fontsize=8.1 if r else 8.0, color=col,
                    fontweight='bold' if r == 0 else 'normal',
                    ha='left', va='center', transform=ax.transAxes)
        if r == 0:
            ax.plot([0.0, 1.0], [yy - 0.06, yy - 0.06], color=C_GRID, lw=1.2,
                    transform=ax.transAxes)
    ax.text(0.0, 0.02,
            '依据：IMP620/N2 自注「专用于 620 kΩ 输入阻抗测试，勿用于监护」\n'
            '→ 不需要 250 Hz 带宽；D 档要过表 201.107 的 150 Hz ≥70% → 留 fc250。',
            fontsize=7.2, color='#5A6472', transform=ax.transAxes, va='bottom',
            linespacing=1.5)
    ax.set_title('③ 按档分离落码汇总', fontsize=10.5, pad=7)

    fig.suptitle('T66 · 输入阻抗判据的多口径对照与「p-p 低于设定值」的判定',
                 fontsize=11.5, y=1.02)
    fig.tight_layout()
    p = os.path.join(HERE, 'T66_b_输入阻抗多口径_2026-09-20.png')
    fig.savefig(p, dpi=150, bbox_inches='tight')
    print('[out]', p)


if __name__ == '__main__':
    fig_a()
    fig_b()
