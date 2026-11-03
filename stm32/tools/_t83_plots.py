#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T83 候选 A/B 裁决图。四面板：
  (A) 离线单脉冲尾（0~10 s）：现状 / A / B / D vs ±100 µV 判限（对数横轴）
  (B) 三口径 × 8 候选柱状（sm100 / env1s / p99.9）—— 口径决定结论
  (C) 低端频响 |H(f)| 相对现状（0.01~1 Hz）—— 差异只出现在 ≤0.1 Hz，≥0.67 Hz 全重合
  (D) 判据影响矩阵 —— 行＝候选，列＝六项判据/开销
数据来自 _t83_ab_scan.py 导出的 _t83_data.npz
"""
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
D = np.load(os.path.join(HERE, '_t83_data.npz'), allow_pickle=True)
names = [str(x) for x in D['names']]
tails, c5tail = D['tails'], D['c5tail']
fs, fs_t = float(D['fs']), float(D['fs_t'])
sm100, env1s, p999 = D['sm100'], D['env1s'], D['p999']
stab_sm, stab_env = D['stab_sm'], D['stab_env']
fg, Hdb = D['fgrid'], D['Hdb']
OUT = os.path.join(HERE, 'T83_候选AB裁决_2026-09-21.png')

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DengXian']
for f in ('Microsoft YaHei',):
    try:
        matplotlib.font_manager.findfont(f, fallback_to_default=False)
        plt.rcParams['font.sans-serif'] = [f]
        break
    except Exception:
        pass
plt.rcParams['axes.unicode_minus'] = False

SHORT = {n: n.split()[0] + ('/' + n.split()[1] if len(n.split()) > 1 else '') for n in names}
SHORT = {n: n.replace('  ', ' ').strip()[:26] for n in names}
fig, ax = plt.subplots(2, 2, figsize=(17.5, 10.2))

# ---------------- (A) tail 对比
a0 = ax[0, 0]
t = np.arange(tails.shape[1]) / fs
COL = {0: '#c00', 1: '#e80', 2: '#888', 3: '#08c', 4: '#060', 5: '#00a', 6: '#609', 7: '#096'}
for i, n in enumerate(names):
    a0.plot(t, tails[i], lw=1.1, color=COL[i], label=SHORT[n])
a0.axhline(100, color='k', ls='--', lw=1.2)
a0.axhline(-100, color='k', ls='--', lw=1.2)
a0.text(0.02, 104, '判限 ±100 µV', fontsize=9)
tc5 = np.arange(len(c5tail)) / fs_t
a0.plot(tc5, c5tail, lw=1.4, color='k', alpha=.55, ls=':', label='实测 col⑤（现状真输出）')
a0.set_xscale('log')
a0.set_xlim(1e-3, 10)
a0.set_ylim(-120, 420)
a0.set_xlabel('脉冲终点后时间 (s，对数轴)')
a0.set_ylabel('基线偏移 (µV)')
a0.set_title('(A) 单脉冲尾：现状 319 µV 不过 ／ A 91 µV 过 ／ B 44 µV 过（实测 col⑤ 282 µV 佐证）', fontsize=11)
a0.legend(fontsize=8, ncol=2, loc='upper right')
a0.grid(alpha=.3, which='both')

# ---------------- (B) 三口径柱状
a1 = ax[0, 1]
x = np.arange(len(names))
w = 0.26
a1.bar(x - w, sm100, w, label='sm100（100 ms 平滑）', color='#08c')
a1.bar(x, env1s, w, label='env1s（1 s RMS 包络）', color='#060')
a1.bar(x + w, p999, w, label='p99.9（分位口径）', color='#e80')
a1.plot(x, stab_sm, 'kv', ms=7, label='9 组稳健最差 sm100')
a1.axhline(100, color='k', ls='--', lw=1.2)
a1.text(0.05, 104, '限 100 µV', fontsize=9)
for xi, v in zip(x, stab_env):
    a1.annotate('%.0f' % v, (xi, v), textcoords='offset points', xytext=(0, 4),
                ha='center', fontsize=7.5, color='#060')
a1.set_xticks(x)
a1.set_xticklabels([SHORT[n].replace(' ', '\n') for n in names], fontsize=8)
a1.set_ylabel('µV')
a1.set_ylim(0, 430)
a1.set_title('(B) 三口径 × 8 候选：阈值全在 100 µV —— A/B 只在「删 HP4」处分叉', fontsize=11)
a1.legend(fontsize=8.5)
a1.grid(alpha=.3, axis='y')

# ---------------- (C) 低端频响
a2 = ax[1, 0]
for i, n in enumerate(names):
    a2.plot(fg, Hdb[i], lw=1.2, color=COL[i], label=SHORT[n])
for fv, lab in ((0.05, '0.05 Hz'), (0.67, '0.67 Hz（判据 b 下限）'), (0.1, '0.1 Hz')):
    a2.axvline(fv, color='k', ls=':', lw=1)
    a2.text(fv * 1.03, 5.6, lab, fontsize=8, rotation=90, va='top')
a2.axhline(0, color='k', lw=.8)
a2.set_xscale('log')
a2.set_xlim(0.01, 1)
a2.set_ylim(-3.5, 6.5)
a2.set_xlabel('频率 (Hz)')
a2.set_ylabel('相对现状的增益 (dB)')
a2.set_title('(C) 低端频响：差异全在 ≤0.1 Hz > 判据 b（≥0.67 Hz）零影响', fontsize=11)
a2.legend(fontsize=8, ncol=2, loc='upper left')
a2.grid(alpha=.3, which='both')

# ---------------- (D) 影响矩阵
a3 = ax[1, 1]
a3.axis('off')
COLS = ['a1\n(基线偏移)', 'a2/a3', 'b 正弦\n0.67~40Hz', '输入阻抗\n620k', '频响\n10~250Hz',
        'CPU\n(dtc)', 'gdtover\n阈值', '低频漂移\n抑制']
CELLS = {
    'S0': ['NG 319', 'OK', 'OK', 'OK', 'OK', '13.75%', 'NG 超', '基准'],
    'A': ['OK 93', 'OK', 'OK 0.004dB', 'OK 0.004dB', '逐位不变', '12.39%', 'OK 回到', '略弱'],
    'B': ['OK 47', 'OK', 'OK 0.005dB', 'OK 0.005dB', '逐位不变', '12.39%', 'OK 回到', '更弱'],
}
ROWK = [('现状 18 节', 'S0'), ('候选 A 16 节', 'A'), ('候选 B 16 节', 'B')]
tb = a3.table(cellText=[CELLS[k] for _, k in ROWK], rowLabels=[r for r, _ in ROWK],
              colLabels=COLS, loc='center', cellLoc='center')
tb.auto_set_font_size(False)
tb.set_fontsize(9.5)
tb.scale(1, 2.4)
for j in range(len(COLS)):
    tb[0, j].set_facecolor('#e8eef7')
for i in range(1, 4):
    for j in range(len(COLS)):
        v = CELLS[ROWK[i - 1][1]][j]
        tb[i, j].set_facecolor('#e6f5e6' if v.startswith('OK') else
                               ('#fbe6e6' if v.startswith('NG') else '#f5f5f5'))
a3.set_title('(D) 判据影响矩阵：A/B 对已判三项全无副作用；差异只在「低频漂移抑制」', fontsize=11, y=0.98)

plt.tight_layout()
plt.savefig(OUT, dpi=155, bbox_inches='tight', facecolor='white')
print('[写出]', os.path.basename(OUT))
