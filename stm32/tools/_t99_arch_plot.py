#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T99 —— 监护/诊断双模式滤波架构 配图（离线，不动固件）。

产出 `tools/_t99_arch.png`（两面板）：
  面板 A：两种模式下**整链**幅频响应（notch OFF ＝判据口径）⇒ 看"低端重合、40 Hz 之上分道"
  面板 B：陷波簇局部（40~270 Hz，notch ON vs OFF）⇒ 看"为什么 5 个工频簇各要 3 节"

口径全部复用 `_t92_mon_measure.py`（同一 AFE 模型、同一 dc_block、同一 16 节表）。
"""
import importlib.util
import io
import os
import sys

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PNG = os.path.join(HERE, '_t99_arch.png')

_spec = importlib.util.spec_from_file_location(
    't92mon', os.path.join(HERE, '_t92_mon_measure.py'))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

C_DIAG = '#185FA5'
C_MON = '#D85A30'
C_NOTCH = '#888780'
C_BAND = '#EAF3DE'


def db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-12))


def main():
    ab = M.read_ab_sos()
    dc_fc = M.read_ab_dc_fc()
    txt = []
    p = lambda s='': (print(s), txt.append(s))

    p('A+B 档 %d 节；dc_block fc = %.4g Hz' % (len(ab), dc_fc))
    p('第 15 节 diag = LP2@250；mon = LP2@45（其余 15 节逐位相同）')

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(13.0, 9.6), dpi=150,
        gridspec_kw={'height_ratios': [1.0, 0.82], 'hspace': 0.30})

    # ============================================================ 面板 A
    f = np.logspace(np.log10(0.05), np.log10(300.0), 3000)
    # ★ 判据口径 = notch OFF；整机 = 固件链 × AFE
    h_diag = M.h_chain_notch_off(ab, 'diag', f, dc_fc) * M.afe_gain(f)
    h_mon = M.h_chain_notch_off(ab, 'mon', f, dc_fc) * M.afe_gain(f)

    ax1.set_facecolor('#FCFCFB')
    ax1.semilogx(f, db(h_diag), color=C_DIAG, lw=2.2, label='diag 档（第15节 LP2@250）')
    ax1.semilogx(f, db(h_mon), color=C_MON, lw=2.2, ls='--', label='mon 档（第15节 LP2@45）')

    # 判据点 + 参考点
    for fv, lab in ((0.67, '0.67'), (5.0, '5\n(参考点)'), (40.0, '40')):
        ax1.axvline(fv, color='#5F5E5A', lw=0.9, ls=':', alpha=0.85)
        ax1.text(fv, 3.0, lab, ha='center', va='bottom', fontsize=9.5, color='#444441')

    # 两条上角
    ax1.axvline(45.0, color=C_MON, lw=1.0, ls='-.', alpha=0.75)
    ax1.text(45.0, -34, ' mon 上角 45 Hz', fontsize=9.5, color=C_MON, va='top')
    ax1.axvline(250.0, color=C_DIAG, lw=1.0, ls='-.', alpha=0.75)
    ax1.text(250.0, -46, 'diag 上角 250 Hz ', fontsize=9.5, color=C_DIAG,
             va='top', ha='right')

    # -3 dB 参照
    ax1.axhline(-3.0, color='#888780', lw=0.9, ls='--', alpha=0.7)
    ax1.text(0.056, -2.4, '−3 dB', fontsize=9.5, color='#5F5E5A', va='bottom')

    ax1.set_xlim(0.05, 300.0)
    ax1.set_ylim(-62.0, 6.0)
    ax1.set_ylabel('幅频响应 (dB)', fontsize=11.5)
    ax1.set_title('面板 A｜两种模式的整链幅频（notch OFF ＝判据口径；含 ADS1293 AFE）',
                  fontsize=12.5, pad=9)
    ax1.grid(True, which='both', color='#D3D1C7', lw=0.5, alpha=0.55)
    ax1.legend(loc='lower left', fontsize=10.5, framealpha=0.95)

    # 分道区标注
    ax1.axvspan(40.0, 300.0, color='#FAEEDA', alpha=0.55, zorder=0)
    ax1.text(120.0, -56.0, '40 Hz 之上两档分道（mon 被 45 Hz 上角压下）',
             fontsize=10, color='#854F0B', ha='center')

    # ============================================================ 面板 B
    fb = np.logspace(np.log10(40.0), np.log10(270.0), 3000)
    on = db(M.h_chain(ab, 'diag', fb, dc_fc) * M.afe_gain(fb))
    off = db(M.h_chain_notch_off(ab, 'diag', fb, dc_fc) * M.afe_gain(fb))

    ax2.set_facecolor('#FCFCFB')
    ax2.semilogx(fb, off, color='#888780', lw=1.7, ls=':', label='notch OFF（无陷波，仅上角）')
    ax2.semilogx(fb, on, color='#534AB7', lw=2.0, label='notch ON（diag 15 节陷波全开）')

    for c in (50, 100, 150, 200, 250):
        ax2.axvline(c, color='#993C1D', lw=0.9, ls='-', alpha=0.45)
        ax2.text(c, -2.0, '%d' % c, ha='center', va='top', fontsize=10,
                 color='#993C1D', fontweight='medium')
    for c, sub in ((50, (49.80, 50.00, 50.20)), (100, (99.80, 100.00, 100.20)),
                   (150, (149.80, 150.00, 150.20)), (200, (199.80, 200.00, 200.20)),
                   (250, (249.80, 250.00, 250.20))):
        for s in sub:
            ax2.plot([s], [-88.0], marker='v', ms=4.2, color='#993C1D', alpha=0.8)

    ax2.set_xlim(40.0, 270.0)
    ax2.set_ylim(-95.0, 6.0)
    ax2.set_xlabel('频率 (Hz，对数轴)', fontsize=11.5)
    ax2.set_ylabel('幅频响应 (dB)', fontsize=11.5)
    ax2.set_title('面板 B｜陷波簇在做什么（diag 档；▼ ＝各簇的 3 个子中心，间距 0.2 Hz）',
                  fontsize=12.5, pad=9)
    ax2.grid(True, which='both', color='#D3D1C7', lw=0.5, alpha=0.55)
    ax2.legend(loc='lower right', fontsize=10.5, framealpha=0.95)

    fig.suptitle('ADS1293 ECG 滤波链：监护 / 诊断双模式架构（A+B 档，16 节 biquad ＋ 前置 dc_block）',
                 fontsize=13.5, y=0.985)
    fig.tight_layout(rect=[0, 0, 1, 0.965])
    fig.savefig(OUT_PNG)
    p('→ %s' % OUT_PNG)

    # 附：几个关键数字（供文档引用）
    def at(mode, fv, notch_off=True):
        h = (M.h_chain_notch_off if notch_off else M.h_chain)(ab, mode, np.array([fv]), dc_fc)
        return float(20.0 * np.log10(abs(h[0]) * abs(M.afe_gain(fv)[0])))
    p('')
    p('关键点（整机 dB，notch OFF）：')
    for fv in (0.67, 5.0, 40.0, 45.0, 50.0, 150.0, 250.0):
        p('  %-6g  diag %+7.2f dB ｜ mon %+7.2f dB ｜ Δ %+6.2f dB'
          % (fv, at('diag', fv), at('mon', fv), at('mon', fv) - at('diag', fv)))

    io.open(os.path.join(HERE, '_t99_plot_out.txt'), 'w',
            encoding='utf-8', newline='\n').write('\n'.join(txt) + '\n')


if __name__ == '__main__':
    main()
