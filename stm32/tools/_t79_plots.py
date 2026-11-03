#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
T79 出图：判据 a（3 mV / 100 ms 方波脉冲）实测判读 + 归因 + 候选对比

四面板：
  (A) 脉冲全景        链前(列②) vs 链后(列⑤) —— 一眼看出"平顶方波进、下倾+tail 出"
  (B) 上升沿放大      过冲来源
  (C) 下降沿后 2 s    实测 vs 离线(现状) vs 离线(低端1阶候选)，±100 µV 判限
  (D) tail 包络       同一横轴上的三条曲线的 |位移| 包络（中位口径）

跑法：<venv-python> _t79_plots.py
"""
import importlib.util
import os
import sys

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_t51():
    spec = importlib.util.spec_from_file_location('t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['t51'] = mod
    spec.loader.exec_module(mod)
    return mod


def dcblk(x, fs, fc=0.05):
    """固件 ecg_dc_block_process 同式：y[n] = (x[n]-x[n-1]) + r*y[n-1]，r = 1-2*pi*fc/fs"""
    r = 1.0 - 2.0 * np.pi * fc / fs
    y = np.empty_like(x, dtype=float)
    xp = x[0]
    yv = 0.0
    for k in range(len(x)):
        yv = (x[k] - xp) + r * yv
        xp = x[k]
        y[k] = yv
    return y


def main():
    for f in ('Microsoft YaHei', 'SimHei', 'DengXian', 'SimSun'):
        try:
            matplotlib.font_manager.findfont(f, fallback_to_default=False)
            plt.rcParams['font.sans-serif'] = [f]
            break
        except Exception:
            continue
    plt.rcParams['axes.unicode_minus'] = False

    m = _load_t51()
    FS = m.FS
    MEAS = os.path.join(HERE, '3mv0.5hz-620k(off)-notchoff-2026-09-21 124440.txt')

    uv, uv_lsb, uv_pre, fs = m.load_txt(MEAS)
    idx, width = m.detect_pulses(uv, fs)
    rs = m.read_all(uv, fs, idx, 3000.0, width)
    i0 = rs[0]['i0']
    w = int(round(width * fs))
    i1 = i0 + w
    base5 = float(np.median(uv[i0 - int(0.5 * fs):i0]))
    base2 = float(np.median(uv_pre[i0 - int(0.5 * fs):i0]))
    u5 = uv - base5
    u2 = uv_pre - base2

    # ---------------- 离线：A+ 现状（链首陷波跳过）与 低端1阶候选 ----------------
    sos, n_notch = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, 'ECG_PROFILE_DUAL_A_PLUS')
    n = int((5.0 + max(4.0 * 2.0, 6.0)) * FS)
    x, xidx = m.pulse_train(n, FS, 3000.0, 0.1, 5.0, 2.0)
    y_now = dcblk(m.run(sos, x, first=n_notch), FS)          # 低端 5 阶 = dc(1) + HP4
    y_cand = dcblk(m.run(sos[:16], x, first=n_notch), FS)    # 低端 1 阶 = dc only
    k0 = xidx[1]
    k1 = k0 + int(round(0.1 * FS))
    b_n = float(np.median(y_now[k0 - int(0.5 * FS):k0]))
    b_c = float(np.median(y_cand[k0 - int(0.5 * FS):k0]))
    z_now = y_now - b_n
    z_cand = y_cand - b_c

    fig, ax = plt.subplots(2, 2, figsize=(15.5, 9))

    # ---- (A) 全景 ----
    lo, hi = i0 - int(0.30 * fs), i0 + int(2.05 * fs)
    tt = (np.arange(lo, hi) - i0) / fs
    ax[0, 0].plot(tt, u2[lo:hi], lw=1.2, color='#2a7', label='列② 链前（AFE 原始码）')
    ax[0, 0].plot(tt, u5[lo:hi], lw=1.4, color='#c33', label='列⑤ 链后（滤波链输出）')
    ax[0, 0].axhline(3000, color='gray', lw=.8, ls=':', label='标称 3 mV')
    ax[0, 0].axhline(0, color='k', lw=.6)
    ax[0, 0].axvline(0, color='r', lw=.6, ls='--')
    ax[0, 0].axvline(width, color='r', lw=.6, ls='--')
    _pn = float(np.median(u5[i0 + int(.03 * fs):i0 + int(.05 * fs)]))
    _pf = float(np.median(u5[i1 - int(.02 * fs):i1]))
    _droop = _pn - _pf
    _pk = float(np.max(u5[i1:i1 + int(0.05 * fs)]))
    ax[0, 0].annotate('链前（列②）：平顶方波，droop ≈ 0 µV',
                      xy=(0.45, 3230), fontsize=9, color='#2a7')
    ax[0, 0].annotate('链后（列⑤）：平台下倾 −%.0f µV，落沿下冲 %.0f µV，慢尾 ~2 s' % (_droop, _pk),
                      xy=(0.45, -430), fontsize=9, color='#c33')
    ax[0, 0].set_ylim(-620 - abs(_pk) * 0.15, 3520)
    ax[0, 0].set_title('(A) 3 mV/100 ms 方波脉冲：链前 vs 链后')
    ax[0, 0].set_xlabel('时间 (s)'); ax[0, 0].set_ylabel('µV')
    ax[0, 0].legend(fontsize=8, loc='upper right'); ax[0, 0].grid(alpha=.3)

    # ---- (B) 上升沿放大 ----
    lo, hi = i0 - int(0.01 * fs), i0 + int(0.07 * fs)
    tm = (np.arange(lo, hi) - i0) / fs * 1000
    ax[0, 1].plot(tm, u2[lo:hi], lw=1.3, color='#2a7', label='链前')
    ax[0, 1].plot(tm, u5[lo:hi], lw=1.6, color='#c33', label='链后')
    ax[0, 1].axhline(rs[0]['plat_near'], color='g', lw=.9, ls='--',
                     label='近端平台 %.0f µV' % rs[0]['plat_near'])
    ax[0, 1].axhline(3000, color='gray', lw=.9, ls=':', label='理想 3000 µV')
    _ov = float(np.median([r['ovs']['near'] for r in rs]))
    _pv = float(np.median([r['peak'] for r in rs]))
    _nov = sum(1 for r in rs if r['ovs']['near'] >= 10)
    ax[0, 1].plot(0, _pv, 'rv')
    ax[0, 1].annotate('逐脉冲中位峰 %.0f µV\n过冲 %.2f%%（判限 <10%%）' % (_pv, _ov),
                      (0, _pv), textcoords='offset points', xytext=(10, -22), fontsize=9)
    ax[0, 1].set_title('(B) 上升沿放大：逐脉冲中位过冲 %.2f%% ｜ 超限 %d/%d 段'
                       % (_ov, _nov, len(rs)))
    ax[0, 1].set_xlabel('ms'); ax[0, 1].set_ylabel('µV')
    ax[0, 1].legend(fontsize=8); ax[0, 1].grid(alpha=.3)

    # ---- (C) 下降沿后 2 s：实测 vs 离线现状 vs 候选 ----
    lo, hi = i1 - int(0.01 * fs), i1 + int(2.0 * fs)
    tm = (np.arange(lo, hi) - i1) / fs
    ax[1, 0].plot(tm, u5[lo:hi], lw=1.3, color='#c33', label='实测 列⑤（A+ 档，BP 3 节）')
    kk = np.arange(k1 - int(0.01 * FS), min(k1 + int(2.0 * FS), len(z_now)))
    ax[1, 0].plot((kk - k1) / FS, z_now[kk], lw=1.2, color='#38f', alpha=.85,
                  label='离线 A+ 现状（低端 5 阶）')
    kk2 = np.arange(k1 - int(0.01 * FS), min(k1 + int(2.0 * FS), len(z_cand)))
    ax[1, 0].plot((kk2 - k1) / FS, z_cand[kk2], lw=1.6, color='#e91', alpha=.9,
                  label='离线 候选：低端 1 阶（去 HP4）')
    ax[1, 0].axhline(0, color='k', lw=.6)
    ax[1, 0].axhline(-100, color='r', lw=.8, ls='--', label='a1 判限 ±100 µV')
    ax[1, 0].set_ylim(-620, 380)
    ax[1, 0].set_title('(C) 下降沿后：tail（a1 位移判据）')
    ax[1, 0].set_xlabel('s（0 = 脉冲终点）'); ax[1, 0].set_ylabel('µV')
    ax[1, 0].legend(fontsize=8); ax[1, 0].grid(alpha=.3)

    # ---- (D) tail 包络（中位口径）----
    xs = np.array([0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 1.0, 1.5])
    env_m, env_n, env_c = [], [], []
    nxt = next((j for j in idx if j > i1), len(uv))
    guard = max(0, nxt - int(0.3 * fs))
    for xv in xs:
        j = i1 + int(xv * fs)
        je = min(j + int(0.15 * fs), guard)
        env_m.append(float(np.median(np.abs(u5[j:je]))) if je > j + 5 else np.nan)
        jk = k1 + int(xv * FS)
        env_n.append(float(np.median(np.abs(z_now[jk:jk + int(0.15 * FS)]))))
        env_c.append(float(np.median(np.abs(z_cand[jk:jk + int(0.15 * FS)]))))
    ax[1, 1].plot(xs, env_m, 'o-', color='#c33', lw=1.6, label='实测（列⑤，σ≈20 µV）')
    ax[1, 1].plot(xs, env_n, 's-', color='#38f', lw=1.4, label='离线 A+ 现状（低端 5 阶）')
    ax[1, 1].plot(xs, env_c, '^-', color='#e91', lw=1.6, label='离线 候选 低端 1 阶')
    ax[1, 1].axhline(100, color='r', lw=1.0, ls='--', label='a1 判限 100 µV')
    ax[1, 1].set_yscale('log')
    ax[1, 1].set_title('(D) 位移包络（中位口径）｜现状超限 1.6~2.0×，候选全时点通过')
    ax[1, 1].set_xlabel('降沿后时间 (s)'); ax[1, 1].set_ylabel('|位移| 中位 (µV)')
    ax[1, 1].legend(fontsize=8); ax[1, 1].grid(alpha=.3, which='both')

    fig.suptitle('T79  判据 a（3 mV/100 ms 方波脉冲）实测判读 —— YY0885 / GB 9706.225-2021   2026-09-21', fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, .96])
    out = os.path.join(HERE, 'T79_判据a实测判读_2026-09-21.png')
    fig.savefig(out, dpi=130)
    print('[fig] ' + out)


if __name__ == '__main__':
    main()
