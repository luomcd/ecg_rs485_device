# -*- coding: utf-8 -*-
"""
T73 · 输入阻抗测试数据判读（620 kΩ 串入 / raw853 OFF / 500 Hz 域）

判据（口径先钉死）：
  ① 标称口径（T24 §1 猛哥定义）：列④ 1 s 窗 p-p ÷ **标称源 p-p** ≤ 1.10
  ② 相对口径（T50 §⑤）：列④ 1 s 窗 p-p 中位(620k) ÷ 中位(直连) ≤ 1.10
  ③ 真信号口径：基波等效 p-p = 2√2 × AC rms ÷ 标称 —— 唯一不受带外噪声污染的读数

用法：
  python _t73_input_z.py --file "xxx.txt" [--ref "yyy.txt"] [--fig]
  python _t73_input_z.py --compare            # 跨文件谱线对照
"""
import argparse
import io
import os
import re
import sys

import numpy as np
from scipy.signal import welch
from scipy.integrate import trapezoid as trapz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _t69_fr_forecast import parse_profile, parse_counts, hz_sos, FS as FS853   # noqa

UV4 = 0.1526
UV5 = 0.1122
FS = 500.0
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COEFF_C = os.path.join(ROOT, 'Filter', 'ecg_filter.c')
COEFF_H = os.path.join(ROOT, 'Filter', 'ecg_filter_coeffs.h')

BANDS = [(0.5, 5), (5, 15), (15, 45), (45, 55), (55, 95), (95, 105),
         (105, 145), (145, 155), (155, 245), (245, 340), (340, 426)]


def _f7(t):
    """第⑦列 gdtover：**固件以 %x 输出（十六进制）**；合成/历史数据可能给十进制或浮点。
    ⇒ 先试 hex，失败退回 float（两种都收，绝不因该列丢行）。
    教训：整行 float() 会在 gdtover 长到含 a~f 时静默丢行（A+ 档 dtcycles>12000 ⇒ 每样本 ++，
    实测 11334 行只认出 3288 行 = 丢 71%，且是系统性抽稀，直接污染 p-p 统计）。"""
    try:
        return float(int(t, 16))
    except ValueError:
        return float(t)


def load(path):
    rows, bad, w = [], [], {}
    for ln, line in enumerate(io.open(path, encoding='utf-8', errors='replace'), 1):
        p = line.split()
        w[len(p)] = w.get(len(p), 0) + 1
        if len(p) != 7:
            bad.append((ln, len(p)))
            continue
        try:
            rows.append([float(p[0]), float(p[1]), float(p[2]), float(p[3]),
                         float(p[4]), float(p[5]), _f7(p[6])])
        except ValueError:
            bad.append((ln, -1))
    return np.array(rows), bad, w


def noised(v, fs=FS, nper=512):
    fr, P = welch(v, fs=fs, nperseg=nper, noverlap=nper // 2, detrend='constant')
    return fr, P


def band_rms(fr, P, a, b):
    m = (fr >= a) & (fr < b)
    return float(np.sqrt(trapz(P[m], fr[m]))) if m.any() else 0.0


def pp_windows(v, win=1.0, trim=1.0):
    n = len(v)
    W = int(win * FS)
    a0, a1 = int(trim * FS), n - int(trim * FS)
    return np.array([v[a:a + W].max() - v[a:a + W].min() for a in range(a0, a1 - W, W)])


def narrow(v, fs, f0, bw='±1.5'):
    n = len(v)
    X = np.fft.rfft(v - v.mean())
    fr = np.fft.rfftfreq(n, 1 / fs)
    X[(fr < f0 - 1.5) | (fr > f0 + 1.5)] = 0
    return np.fft.irfft(X, n)


def peaks(v, fs, thr=25.0, fmin=120.0, fmax=430.0):
    n = len(v)
    N = 1 << 16
    V = np.abs(np.fft.rfft((v - v.mean()) * np.hanning(n), N)) / (n / 4)
    fr = np.fft.rfftfreq(N, 1 / fs)
    out = []
    for i in range(2, len(fr) - 2):
        if not (fmin < fr[i] < fmax):
            continue
        if V[i] > thr and V[i] == max(V[i - 2:i + 3]):
            out.append((float(fr[i]), float(V[i])))
    # 合并 1 Hz 内的重复
    merged = []
    for f, a in sorted(out, key=lambda x: -x[1]):
        if all(abs(f - g) > 1.0 for g, _ in merged):
            merged.append((f, a))
    return sorted(merged)


def predict_target(c4, lines, sos_cur, sos_tgt, nom, fsx=None):
    """把列④读数的带外干扰线按**目标链 |H| 比值**重缩放，返回 (中位%, p95%, 明细)。

    做法（T73 §5 / T75 §3 共用）：
      对每根线 f，取窄带分量 narrow(c4, f) 乘上 g = |H_tgt(f)| / |H_cur(f)| 再拼回，
      然后按原判据重算 1 s 窗 p-p。⇒ 得到"同一批数据换到目标档会读到多少"的预测。
    ⚠ fsx 必须用**设计域 853.242 Hz**（系数是按它设计的）；用 500 域算会得到荒谬值。
    """
    fsx = fsx or FS853
    rebuild = c4.copy()
    detail = []
    for f, _amp in lines:
        hc = abs(hz_sos(sos_cur, np.atleast_1d(f), fsx)[0])
        hn = abs(hz_sos(sos_tgt, np.atleast_1d(f), fsx)[0])
        g = (hn / hc) if hc > 1e-12 else 0.0
        detail.append((f, 20 * np.log10(max(hc, 1e-30)),
                       20 * np.log10(max(hn, 1e-30)), 20 * np.log10(max(hn / hc, 1e-30))))
        rebuild = rebuild - narrow(c4, FS, f) * (1 - g)
    pps = pp_windows(rebuild)
    return (np.median(pps) / nom * 100,
            float(np.percentile(pps, 95)) / nom * 100,
            detail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default=None)
    ap.add_argument('--ref', default=None)
    ap.add_argument('--nom', type=float, default=3000.0, help='标称源 p-p (µV)')
    ap.add_argument('--prof', default='DIAG150_D3', help='数据所在档')
    ap.add_argument('--target', default='IMP620_N2', help='预测目标档')
    ap.add_argument('--compare', action='store_true')
    ap.add_argument('--fig', action='store_true')
    a = ap.parse_args()

    if a.compare:
        files = [
            ('3mv-10hz-sine-620kin-2026-09-21 104047.txt', 'D3   in 620k 3mV10Hz (今日)'),
            ('5mv-10hz-620k-noin-2026-09-18 111214.txt', 'N2?  noin 5mV10Hz 09-18 11:12'),
            ('5mV-10Hz-620k-in-2026-09-18 111248.txt', 'N2?  in   5mV10Hz 09-18 11:12'),
            ('3mv-4hz-620k-noin-2026-09-18 111133.txt', 'N2?  noin 3mV4Hz  09-18 11:11'),
            ('3mV-4Hz-620k-in-2026-09-18 111023.txt', 'N2?  in   3mV4Hz  09-18 11:10'),
            ('3mV-4Hz-620k-in-2026-09-18 093952.txt', '未陷波 in 3mV4Hz 09-18 09:39'),
            ('3mV-4Hz-620k-noin-2026-09-18 094253.txt', '未陷波 noin 3mV4Hz 09-18 09:42'),
            ('5mV-10Hz-620k-in-2026-09-18 094441.txt', '未陷波 in 5mV10Hz 09-18 09:44'),
            ('5mV-10Hz-620k-noin-2026-09-18 094358.txt', '未陷波 noin 5mV10Hz 09-18 09:43'),
            ('620kin-raw835off(on-off-on)-2026-09-20 215252.txt', 'D?   in 620k 09-20 21:52'),
            ('620knotin-raw835off(on-off-on)-2026-09-20 215252.txt', 'D?   noin 620k 09-20 21:52'),
        ]
        print('%-32s %9s %9s %9s %9s %9s %9s %9s' %
              ('文件', 'ACrms', '1s中位pp', '标称%', '150Hz线', '200Hz线', '250Hz线', '>145rms'))
        for f, lab in files:
            try:
                d, _, _ = load(f)
            except Exception as e:
                print('  %-32s 读取失败 %s' % (lab, e))
                continue
            v = (d[:, 3] * UV4)[int(.5 * FS):-int(.5 * FS)]
            pps = pp_windows(v)
            fr, P = noised(v)
            def line(f0):
                m = (fr >= f0 - 1.5) & (fr <= f0 + 1.5)
                n = len(v)
                V = np.abs(np.fft.rfft((v - v.mean()) * np.hanning(n), 1 << 16)) / (n / 4)
                fa = np.fft.rfftfreq(1 << 16, 1 / FS)
                mm = (fa >= f0 - 1.5) & (fa <= f0 + 1.5)
                return float(V[mm].max())
            nz = np.sqrt(band_rms(fr, P, 145, 155) ** 2 + band_rms(fr, P, 155, 245) ** 2
                         + band_rms(fr, P, 245, 340) ** 2)
            print('%-32s %9.1f %9.1f %8.1f%% %9.1f %9.1f %9.1f %9.1f' %
                  (lab, v.std(), np.median(pps), np.median(pps) / 3000 * 100,
                   line(149.9), line(199.8), line(249.8), nz))
        return

    path = a.file
    d, bad, w = load(path)
    s = int(.5 * FS)
    c4 = (d[:, 3] * UV4)[s:-s]
    c5 = (d[:, 4] * UV5)[s:-s]
    cyc = d[s:-s, 5]
    n = len(c4)
    print('=' * 86)
    print('文件 %s' % path)
    print('  有效行 %d  坏行 %d %s  列宽 %s' % (len(d), len(bad), bad[:3], w))
    print('  长度 %.2f s @ fs=%.1f Hz   AC rms(列④) %.1f µV' % (n / FS, FS, c4.std()))
    if cyc.any():
        print('  dtcycles 中位 %.0f [%.0f,%.0f]' % (np.median(cyc), cyc.min(), cyc.max()))
    di = np.diff(d[:, 2])
    k = np.where(di != 1)[0]
    if len(k):
        print('  ⚠ 丢样 %d 处，最大缺口 %d 样本' % (len(k), int(di[k].max() - 1)))

    # ---- 真信号 ----
    sig_pp = c4.std() * 2 * np.sqrt(2)
    print('\n[1] 真信号（10 Hz 正弦，等效 p-p = 2√2·ACrms）')
    print('    列④ %.1f µVpp = **%.2f%%**  ｜ 列⑤ %.1f µVpp = %.2f%%'
          % (sig_pp, sig_pp / a.nom * 100, c5.std() * 2 * np.sqrt(2),
             c5.std() * 2 * np.sqrt(2) / a.nom * 100))

    # ---- p-p 口径 ----
    pps = pp_windows(c4)
    print('\n[2] p-p 口径（标称 %.0f µV ⇒ 限 %.0f µV）' % (a.nom, a.nom * 1.1))
    print('    1 s 窗 中位 %8.1f µV = **%.2f%%** %s'
          % (np.median(pps), np.median(pps) / a.nom * 100,
             '✓' if np.median(pps) <= a.nom * 1.1 else '✗'))
    print('           p95  %8.1f µV = %.2f%% %s'
          % (np.percentile(pps, 95), np.percentile(pps, 95) / a.nom * 100,
             '✓' if np.percentile(pps, 95) <= a.nom * 1.1 else '✗'))
    print('           全段 %8.1f µV = %.2f%% %s'
          % (c4.max() - c4.min(), (c4.max() - c4.min()) / a.nom * 100,
             '✓' if (c4.max() - c4.min()) <= a.nom * 1.1 else '✗'))
    # 去外干扰后的"本该读数"
    clean = c4.copy()
    pk = peaks(c4, FS, thr=25.0)
    for f, _ in pk:
        clean = clean - narrow(c4, FS, f)
    pps_c = pp_windows(clean)
    print('    剔除干扰线后 1 s 窗中位 %8.1f µV = **%.2f%%** %s'
          % (np.median(pps_c), np.median(pps_c) / a.nom * 100,
             '✓' if np.median(pps_c) <= a.nom * 1.1 else '✗'))

    # ---- 频带 ----
    fr, P = noised(c4)
    print('\n[3] 频带 rms（列④，µV）')
    tot = 0.0
    for b1, b2 in BANDS:
        r = band_rms(fr, P, b1, b2)
        if (b1, b2) != (5, 15):
            tot += r * r
        print('    %6.1f~%6.1f Hz : %8.2f' % (b1, b2, r))
    print('    除信号带外合计 rms = %.1f µV' % np.sqrt(tot))

    # ---- 干扰线 ----
    print('\n[4] 带外离散谱线（列④，>25 µV）')
    for f, amp in pk:
        print('    %8.2f Hz : %7.1f µV' % (f, amp))

    # ---- 档位对照预测 ----
    soc = parse_profile(COEFF_C, a.prof)
    son = parse_profile(COEFF_C, a.target)
    cn = parse_counts(COEFF_H, a.prof)
    cN = parse_counts(COEFF_H, a.target)
    print('\n[5] 档位对照预测：把当前读数换成 %s（%d 节）' % (a.target, cN[0] if cN else -1))
    print('    %-10s %10s %10s %10s' % ('freq', a.prof[:10], a.target[:10], '改善 dB'))
    print('    （|H| 按设计域 fs=%.3f Hz 评估）' % FS853)
    med, p95, detail = predict_target(c4, pk, soc, son, a.nom)
    for f, d1, d2, g in detail:
        print('    %8.2f Hz  %9.3f %10.3f %10.2f' % (f, d1, d2, g))
    print('    ⇒ 预测 %s：1 s 窗 p-p **中位 %.2f%%** %s ｜ **p95 %.2f%%** %s'
          % (a.target, med, '✓' if med <= 110 else '✗', p95, '✓' if p95 <= 110 else '✗'))

    if a.ref:
        dr, _, _ = load(a.ref)
        r4 = (dr[:, 3] * UV4)[s:-s]
        ppr = pp_windows(r4)
        print('\n[6] 直连参照 %s' % os.path.basename(a.ref))
        print('    ACrms %.1f → 等效 p-p %.1f   1 s 窗 p-p 中位 %.1f'
              % (r4.std(), r4.std() * 2 * np.sqrt(2), np.median(ppr)))
        print('    相对口径：等效 p-p %.2f%%   1 s 窗中位 %.2f%%'
              % (sig_pp / (r4.std() * 2 * np.sqrt(2)) * 100,
                 np.median(pps) / np.median(ppr) * 100))

    if a.fig:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(3, 1, figsize=(11, 9.5))
        t = np.arange(1000) / FS
        ax[0].plot(t, c4[:1000], lw=.9, label=u'列④ 原始（含带外干扰）')
        lp = narrow(c4, FS, 10.0)
        ax[0].plot(t, lp[:1000], lw=1.6, label=u'仅留 ~10 Hz 基波')
        ax[0].axhline(a.nom / 2, color='r', ls=':', lw=1)
        ax[0].axhline(-a.nom / 2, color='r', ls=':', lw=1)
        ax[0].legend(fontsize=8)
        ax[0].set_title(u'列④ 前 2 s（620 kΩ in，D3）—— 红点线＝3 mV 标称半幅')
        ax[0].grid(alpha=.3); ax[0].set_ylabel(u'µV')
        ax[1].hist(pps, bins=12)
        ax[1].axvline(a.nom * 1.1, color='r', ls='--', lw=2, label=u'判限 110%% = %.0f µV' % (a.nom * 1.1))
        ax[1].axvline(np.median(pps), color='g', ls='-', lw=2, label=u'中位 %.0f (%.1f%%)'
                      % (np.median(pps), np.median(pps) / a.nom * 100))
        ax[1].legend(fontsize=8)
        ax[1].set_title(u'1 s 窗 p-p 分布'); ax[1].grid(alpha=.3); ax[1].set_xlabel(u'µV')
        n2 = len(c4)
        V = np.abs(np.fft.rfft((c4 - c4.mean()) * np.hanning(n2))) / (n2 / 4)
        frr = np.fft.rfftfreq(n2, 1 / FS)
        ax[2].semilogy(frr, V / np.sqrt(2) + 1e-3, lw=.8)
        for f, amp in pk:
            ax[2].annotate(u'%.1f Hz\n%.0f µV' % (f, amp), (f, amp / np.sqrt(2) * 1.3),
                           fontsize=7, ha='center', color='r')
        ax[2].set_xlim(0, 430); ax[2].set_ylim(1e-2, 1e4); ax[2].grid(alpha=.3)
        ax[2].set_title(u'列④ 幅度谱（µV rms）—— 149.9 / 199.8 / 249.7 三根市电谐波线')
        ax[2].set_xlabel(u'Hz')
        plt.tight_layout()
        out = os.path.join(ROOT, 'tools', 'T73_输入阻抗_干扰定位_2026-09-21.png')
        plt.savefig(out, dpi=110)
        print('\n图: %s' % out)


if __name__ == '__main__':
    main()
