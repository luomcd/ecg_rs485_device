# -*- coding: utf-8 -*-
"""T82 · +300 mV 偏移下的输入阻抗判读（620k in / 10 Hz / notch on / A+ 18 节）

问题：真信号口径 100.31% ✓、1 s 窗 p-p 中位 104.95% ✓，但 p95 114.50% ✗。
要回答：p95 偏高是「输入阻抗问题」还是「带外干扰」，并给出链前/链后归因。
"""
import io
import numpy as np
from scipy.signal import welch
from scipy.integrate import trapezoid as trapz

UV4 = 0.1526
FS = 500.0
BAND_OUT = [(0.5, 5), (15, 45), (45, 55), (55, 95), (95, 105),
            (105, 145), (145, 155), (155, 245), (245, 340)]
SIG = (5, 15)


def load(path):
    rows = []
    for line in io.open(path, encoding='utf-8', errors='replace'):
        p = line.split()
        if len(p) != 7:
            continue
        try:
            rows.append([float(p[0]), float(p[1]), float(p[2]), float(p[3]),
                         float(p[4]), float(p[5]), float(int(p[6], 16))])
        except ValueError:
            pass
    return np.array(rows)


def band(fr, P, a, b):
    m = (fr >= a) & (fr < b)
    return float(np.sqrt(trapz(P[m], fr[m]))) if m.any() else 0.0


def notch_out(v, fs, lo=44.0, hi=56.0):
    """在频域挖掉 44~56 Hz 带（含 50 Hz 及其边带），其余原样。"""
    n = len(v)
    X = np.fft.rfft(v - v.mean())
    fr = np.fft.rfftfreq(n, 1 / fs)
    X[(fr >= lo) & (fr <= hi)] = 0
    return np.fft.irfft(X, n) + v.mean()


def lowpass(v, fs, fc):
    n = len(v)
    X = np.fft.rfft(v - v.mean())
    fr = np.fft.rfftfreq(n, 1 / fs)
    X[fr > fc] = 0
    return np.fft.irfft(X, n) + v.mean()


def wins(v, w=1.0):
    W = int(w * FS)
    return np.array([np.ptp(v[i * W:(i + 1) * W]) for i in range(len(v) // W)])


def report(path, lab, nominal=3000.0):
    d = load(path)
    c2, c4 = d[:, 1], d[:, 3] * UV4
    n = len(c4)
    pps = wins(c4)
    print('=' * 96)
    print('%s' % lab)
    print('  %s' % path)
    print('  行 %d  时长 %.1f s  dtcycles 中位 %.0f（模型节数 %.2f）'
          % (n, n / FS, np.median(d[:, 5]), (np.median(d[:, 5]) - 1387.8) / 639.07))
    sig = c4.std() * 2 * np.sqrt(2)
    print('  [信号口径] col4 等效 p-p %7.1f µV = %6.2f%%' % (sig, sig / nominal * 100))
    for nm, v in (('原样', c4), ('挖 44~56Hz', notch_out(c4, FS)),
                  ('低通 40Hz', lowpass(c4, FS, 40.0))):
        p = wins(v)
        print('  [1s窗p-p %-10s] 中位 %7.1f = %6.2f%% ｜ p95 %7.1f = %6.2f%% ｜ max %7.1f = %6.2f%% ｜ 超3300 %d/%d'
              % (nm, np.median(p), np.median(p) / nominal * 100,
                 np.percentile(p, 95), np.percentile(p, 95) / nominal * 100,
                 p.max(), p.max() / nominal * 100, (p > 3300).sum(), len(p)))
    # 长窗（10 s）：看干扰是否只是"窗内极值"效应
    for w in (2.0, 5.0, 10.0):
        if n / FS > w * 2:
            p = wins(c4, w)
            print('  [%4.0fs 窗 p-p]        中位 %7.1f = %6.2f%% ｜ max %7.1f = %6.2f%%'
                  % (w, np.median(p), np.median(p) / nominal * 100,
                     p.max(), p.max() / nominal * 100))
    fr, P = welch(c4, fs=FS, nperseg=512, noverlap=256, detrend='constant')
    fr2, P2 = welch(c2, fs=FS, nperseg=512, noverlap=256, detrend='constant')
    sc = sig / (np.median([np.ptp(c2[i * int(FS):(i + 1) * int(FS)])
                           for i in range(len(c2) // int(FS))]))
    print('  [col2→col4 换算] col2 中位 1s p-p = %.0f 码，col4 信号 p-p = %.1f µV ⇒ 1 码 = %.5f µV'
          % (1 / sc * sig, sig, sc))
    print('  带外 rms(µV)  ' + ' '.join('%s:%.1f' % ('%g-%g' % b, band(fr, P, *b)) for b in BAND_OUT))
    print('  带外合计 %.1f µV（占信号 p-p 的 %.2f%%）'
          % (np.sqrt(sum(band(fr, P, *b) ** 2 for b in BAND_OUT)),
             100 * np.sqrt(sum(band(fr, P, *b) ** 2 for b in BAND_OUT)) / sig))
    print('  带外 rms(码，col2) ' + ' '.join('%s:%.0f' % ('%g-%g' % b, band(fr2, P2, *b)) for b in BAND_OUT))
    print('  col2 带外合计 %.0f 码 = %.1f µV（占 col2 信号 p-p 的 %.2f%%）'
          % (np.sqrt(sum(band(fr2, P2, *b) ** 2 for b in BAND_OUT)),
             np.sqrt(sum(band(fr2, P2, *b) ** 2 for b in BAND_OUT)) * sc,
             100 * np.sqrt(sum(band(fr2, P2, *b) ** 2 for b in BAND_OUT)) / (1 / sc * sig)))
    # 超限窗在链前是否同现
    bad = np.where(pps > 3300)[0]
    W = int(FS)
    if len(bad):
        p2 = np.array([np.ptp(c2[i * W:(i + 1) * W]) for i in range(len(c2) // W)])
        allb = np.median(p2[bad]) / np.median(np.delete(p2, bad))
        print('  [链前同现] 超限 %d 窗的 col2 p-p 中位 %.0f，其余窗 %.0f ⇒ 比值 %.3f'
              % (len(bad), np.median(p2[bad]), np.median(np.delete(p2, bad)), allb))
        all4 = np.median(pps[bad]) / np.median(np.delete(pps, bad))
        print('             同一批窗 col4 比值 %.3f' % all4)
    return c4


if __name__ == '__main__':
    report('3mv10hz-620k(in)-notchon-2026-09-21 124735.txt',
           'T78-B · 620k in 10Hz 无偏移（notch on, A+）')
    report('620kin(300mv偏移)-2026-09-21 141011.txt',
           'T82 · 620k in 10Hz **+300 mV 偏移**（notch on, A+）')
