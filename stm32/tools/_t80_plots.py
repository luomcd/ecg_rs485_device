#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T80 判据 a 裁决图（激励＝实测 col②）。四面板：
  (A) 单脉冲全景：链前 col② / 链后 col⑤ / 离线现状（证明模型忠实）
  (B) 降沿后 tail 包络 0~1.2 s：实测 / 现状 / 候选A(去HP4) / 候选B(去HP4+dc@0.02) ＋ ±100 µV 判限
  (C) a1 三口径柱状对比 ＋ 100 µV 判限
  (D) 判据 b：0.67~40 Hz 归一增益（现状 vs 候选）＋ 70~140% 限带
"""
import importlib.util
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import lfilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location('_t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['_t51'] = m
_spec.loader.exec_module(m)

FS, FS_T, UV = m.FS, 500.0, 0.1122
MEAS = os.path.join(HERE, '3mv0.5hz-620k(off)-notchoff-2026-09-21 124440.txt')


def dcblk(x, fc, fs=FS):
    r = 1.0 - 2.0 * np.pi * fc / fs
    return lfilter([1.0, -1.0], [1.0, -r], x)


def dc_mag(f, fc):
    z = np.exp(1j * 2 * np.pi * f / FS)
    r = 1.0 - 2.0 * np.pi * fc / FS
    return abs((1 - 1 / z) / (1 - r / z))


def load(p):
    r = []
    for ln in open(p, encoding='utf-8', errors='replace'):
        q = ln.split()
        if len(q) < 6:
            continue
        try:
            r.append([float(x) for x in q[:6]])
        except ValueError:
            pass
    return np.array(r)


def pulse_idx(y, fs, frac=0.5, minw=0.02):
    hi, lo = np.percentile(y, 98), np.median(y)
    thr = lo + frac * (hi - lo)
    up = y > thr
    st, en = [], []
    for k in range(1, len(y)):
        if up[k] and not up[k - 1]:
            st.append(k)
        elif up[k - 1] and not up[k]:
            en.append(k)
    return [(s, next(e for e in en if e > s)) for s in st if next((e for e in en if e > s), None)
            and (next(e for e in en if e > s) - s) / fs >= minw]


def readings(y, fs, pr, width=0.1):
    w = int(round(width * fs))
    k = int(0.1 * fs)
    sm = np.convolve(y, np.ones(k) / k, mode='same')
    out = []
    for s, e in pr:
        i0, i1 = s, s + w
        if i1 + int(1.6 * fs) >= len(y) or i0 < int(0.3 * fs):
            continue
        base = np.median(y[i0 - int(0.3 * fs):i0])
        bs = np.median(sm[i0 - int(0.3 * fs):i0])
        u, us = y - base, sm - bs
        j = i1 + int(0.05 * fs)
        jend = min(j + int(1.0 * fs), len(y) - 1)
        pk = float(np.max(u[i0:i0 + int(0.03 * fs)]))
        pn = float(np.median(u[i0 + int(0.015 * fs):i0 + int(0.03 * fs)]))
        out.append(dict(a1max=float(np.max(np.abs(u[j:jend]))),
                        a1med=float(np.median(np.abs(u[j:jend]))),
                        a1smo=float(np.max(np.abs(us[j:jend]))),
                        a3=(pk - pn) / pn * 100.0,
                        env=u[j - int(0.05 * fs):j - int(0.05 * fs) + int(1.2 * fs)].copy()))
    return out


def med_of(rs, k):
    v = [r[k] for r in rs if np.isfinite(r[k])]
    return float(np.median(v)) if v else float('nan')


def main():
    for f in ('Microsoft YaHei', 'SimHei', 'DengXian', 'SimSun'):
        try:
            matplotlib.font_manager.findfont(f, fallback_to_default=False)
            plt.rcParams['font.sans-serif'] = [f]
            break
        except Exception:
            continue
    plt.rcParams['axes.unicode_minus'] = False

    a = load(MEAS)
    c2 = (a[:, 1] - a[:, 1].mean()) * UV
    c5 = (a[:, 4] - a[:, 4].mean()) * UV
    pr = pulse_idx(c5, FS_T)
    t500 = np.arange(len(a)) / FS_T
    t853 = np.arange(0, t500[-1], 1 / FS)
    x853 = np.interp(t853, t500, c2)

    sos, n_notch = m.parse_coeffs_f64(m.COEFFS_C, m.COEFFS_H, 'ECG_PROFILE_DUAL_A_PLUS')
    kinds = [m.section_kind(s) for s in sos]
    bp = [sos[i] for i in range(n_notch, len(sos))]          # [LP, HP, HP]
    lp_only = [s for s, k in zip(bp, kinds[n_notch:]) if k == 'LP']

    def to500(y853):
        return np.interp(t500, t853, y853)

    S0 = to500(dcblk(m.run(bp, x853), 0.05))
    S1 = to500(dcblk(m.run(lp_only, x853), 0.05))
    S4 = to500(dcblk(m.run(lp_only, x853), 0.02))

    r5 = readings(c5, FS_T, pr)
    r0 = readings(S0, FS_T, pr)
    r1 = readings(S1, FS_T, pr)
    r4 = readings(S4, FS_T, pr)

    fig, ax = plt.subplots(2, 2, figsize=(15.5, 9.2))

    # ---------------- (A) 单脉冲全景 ----------------
    i0 = pr[3][0]
    i1 = i0 + int(0.1 * FS_T)
    lo, hi = i0 - int(0.3 * FS_T), i0 + int(1.6 * FS_T)
    tt = (np.arange(lo, hi) - i0) / FS_T
    b2 = np.median(c2[i0 - int(0.3 * FS_T):i0])
    b5 = np.median(c5[i0 - int(0.3 * FS_T):i0])
    b0 = np.median(S0[i0 - int(0.3 * FS_T):i0])
    ax[0, 0].plot(tt, c2[lo:hi] - b2, lw=1.3, color='#2a7', label='链前 col②（AFE 原始码）')
    ax[0, 0].plot(tt, c5[lo:hi] - b5, lw=1.5, color='#c33', label='链后 col⑤（实测）')
    ax[0, 0].plot(tt, S0[lo:hi] - b0, lw=1.0, ls='--', color='#333', label='离线现状（激励＝col②）')
    ax[0, 0].axhline(0, color='k', lw=.6)
    ax[0, 0].axhline(-100, color='#c60', lw=.7, ls=':')
    ax[0, 0].axvline(0, color='r', lw=.6, ls='--')
    ax[0, 0].axvline(0.1, color='r', lw=.6, ls='--')
    ax[0, 0].set_ylim(-620, 3520)
    ax[0, 0].set_title('(A) 3 mV / 100 ms 方波脉冲：链前 vs 链后（离线现状与实测逐点吻合，相关 0.999）')
    ax[0, 0].set_xlabel('时间 (s)'); ax[0, 0].set_ylabel('µV')
    ax[0, 0].legend(fontsize=8, loc='upper right'); ax[0, 0].grid(alpha=.3)

    # ---------------- (B) tail 包络 ----------------
    def env_of(rs):
        e = np.median(np.array([r['env'] for r in rs]), axis=0)
        kk = max(3, int(0.05 * FS_T))
        e = np.convolve(e, np.ones(kk) / kk, mode='same')
        return np.arange(len(e)) / FS_T + 0.05, e

    for nm, rs, col in (('实测 col⑤', r5, '#c33'), ('现状 S0', r0, '#333'),
                        ('候选A 去HP4', r1, '#1170aa'), ('候选B 去HP4+dc@0.02', r4, '#2a7')):
        t, e = env_of(rs)
        ax[0, 1].plot(t, e, lw=1.8, color=col, label=nm)
    ax[0, 1].axhspan(-100, 100, color='#c60', alpha=.10)
    ax[0, 1].axhline(-100, color='#c60', lw=1.0, ls='--', label='判限 ±100 µV（a1）')
    ax[0, 1].axhline(100, color='#c60', lw=1.0, ls='--')
    ax[0, 1].axhline(0, color='k', lw=.6)
    ax[0, 1].axvline(0.25, color='gray', lw=.8, ls=':')
    ax[0, 1].annotate('脉冲终点后 200 ms 起算（主口径）', xy=(0.27, 92), fontsize=8, color='#555')
    ax[0, 1].set_xlim(0.05, 1.25); ax[0, 1].set_ylim(-320, 120)
    ax[0, 1].set_title('(B) 降沿后基线位移包络（跨脉冲中位，50 ms 平滑）—— 现状全程越限；候选A/B 全时点在限内')
    ax[0, 1].set_xlabel('降沿后时间 (s)'); ax[0, 1].set_ylabel('µV')
    ax[0, 1].legend(fontsize=8, loc='lower right'); ax[0, 1].grid(alpha=.3)

    # ---------------- (C) a1 三口径柱状 ----------------
    labs = ['极值 max|u|', '中位 |u|', '平滑 100ms max']
    vals = [[med_of(r0, 'a1max'), med_of(r0, 'a1med'), med_of(r0, 'a1smo')],
            [med_of(r5, 'a1max'), med_of(r5, 'a1med'), med_of(r5, 'a1smo')],
            [med_of(r1, 'a1max'), med_of(r1, 'a1med'), med_of(r1, 'a1smo')],
            [med_of(r4, 'a1max'), med_of(r4, 'a1med'), med_of(r4, 'a1smo')]]
    names = ['现状 S0', '实测 col⑤', '候选A', '候选B']
    cols = ['#333', '#c33', '#1170aa', '#2a7']
    xx = np.arange(len(labs))
    wd = 0.2
    for k, (v, nm, cc) in enumerate(zip(vals, names, cols)):
        ax[1, 0].bar(xx + (k - 1.5) * wd, v, wd, color=cc, label=nm)
        for xi, vi in zip(xx + (k - 1.5) * wd, v):
            ax[1, 0].text(xi, vi + 4, '%.0f' % vi, ha='center', fontsize=7.5,
                          color='#c00' if vi > 100 else '#080')
    ax[1, 0].axhline(100, color='#c00', lw=1.6, ls='--')
    ax[1, 0].text(len(labs) - 0.5, 112, 'a1 判限 100 µV', color='#c00', fontsize=9, ha='right')
    ax[1, 0].set_xticks(xx); ax[1, 0].set_xticklabels(labs)
    ax[1, 0].set_ylabel('位移 (µV)'); ax[1, 0].set_ylim(0, 360)
    ax[1, 0].set_title('(C) a1 三口径对比 —— 现状三口径全超；候选A 中位/平滑达标；候选B 三口径全达标')
    ax[1, 0].legend(fontsize=8); ax[1, 0].grid(alpha=.3, axis='y')

    # ---------------- (D) 判据 b ----------------
    fr = np.logspace(np.log10(0.5), np.log10(50), 400)
    H5 = abs(m.band_mag(sos, 5.0, first=n_notch)) * dc_mag(5.0, 0.05)
    g0 = [abs(m.band_mag(sos, f, first=n_notch)) * dc_mag(f, 0.05) / H5 * 100 for f in fr]
    H5b = abs(m.band_mag(lp_only, 5.0)) * dc_mag(5.0, 0.05)
    g1 = [abs(m.band_mag(lp_only, f)) * dc_mag(f, 0.05) / H5b * 100 for f in fr]
    ax[1, 1].semilogx(fr, g0, lw=1.8, color='#333', label='现状 S0')
    ax[1, 1].semilogx(fr, g1, lw=1.4, ls='--', color='#1170aa', label='候选A 去HP4（曲线重合）')
    ax[1, 1].axhspan(70, 140, color='#2a7', alpha=.10)
    ax[1, 1].axhline(140, color='#2a7', lw=1.0, ls='--')
    ax[1, 1].axhline(70, color='#2a7', lw=1.0, ls='--', label='判限 140% / 70%（参考 5 Hz）')
    ax[1, 1].axhline(100, color='k', lw=.6)
    for fv in (0.67, 1, 2, 5, 10, 20, 40):
        ax[1, 1].axvline(fv, color='gray', lw=.5, alpha=.5)
    ax[1, 1].set_xlim(0.5, 50); ax[1, 1].set_ylim(40, 160)
    ax[1, 1].set_title('(D) 判据 b：0.67~40 Hz 相对 5 Hz 归一增益 —— 低端降阶不影响（0.67 Hz 99.7%）')
    ax[1, 1].set_xlabel('频率 (Hz)'); ax[1, 1].set_ylabel('相对 5 Hz (%)')
    ax[1, 1].legend(fontsize=8, loc='lower left'); ax[1, 1].grid(alpha=.3, which='both')

    fig.suptitle('T80  判据 a 裁决与修正方案（GB 9706.247）—— 激励＝实测 col②  2026-09-21', fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = os.path.join(HERE, 'T80_判据a裁决与修正_2026-09-21.png')
    fig.savefig(out, dpi=130)
    print('saved:', out)
    print('(C) 数值:')
    for nm, v in zip(names, vals):
        print('   %-10s max %6.1f ｜ med %6.1f ｜ 平滑 %6.1f' % (nm, v[0], v[1], v[2]))


if __name__ == '__main__':
    main()
