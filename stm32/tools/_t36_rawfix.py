# -*- coding: utf-8 -*-
"""_t36_rawfix.py —— 修正 _t35_verify.py [4] 段的 fs 错误（853->500），重算 raw 域三线。
依据：全谱扫描证实列② raw 的等效采样率 = 500 Hz（主峰 fs=500 -> 49.93Hz，fs=853 -> 85.21Hz）。"""
import io, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
D = "C:/Users/luom/Desktop/ADS1293/"
UV4, UV2, ZERO = 0.1526, 0.1122, 6075000.0
FS = 500.0                      # ★ 修正：列②③④⑤ 全部是 500 Hz 栅格（列① seq 才是 853）
FILES = {
    ("A", "3mV/4Hz", "in"):   "3mV-4Hz-620k-in-2026-09-18 093952.txt",
    ("A", "3mV/4Hz", "noin"): "3mV-4Hz-620k-noin-2026-09-18 094253.txt",
    ("A", "5mV/10Hz", "in"):  "5mV-10Hz-620k-in-2026-09-18 094441.txt",
    ("A", "5mV/10Hz", "noin"):"5mV-10Hz-620k-noin-2026-09-18 094358.txt",
    ("B", "3mV/4Hz", "in"):   "3mv-4hz-620k-in-2026-09-18 111023.txt",
    ("B", "3mV/4Hz", "noin"): "3mv-4hz-620k-noin-2026-09-18 111133.txt",
    ("B", "5mV/10Hz", "in"):  "5mv-10hz-620k-in-2026-09-18 111248.txt",
    ("B", "5mV/10Hz", "noin"):"5mv-10hz-620k-noin-2026-09-18 111214.txt",
}


def load(fn):
    seq, raw, i16 = [], [], []
    for ln in io.open(D + fn, encoding="utf-8", errors="ignore"):
        p = ln.split()
        if len(p) != 7:
            continue
        try:
            seq.append(int(p[0])); raw.append(int(p[1])); i16.append(int(p[3]))
        except ValueError:
            pass
    return np.array(seq, np.int64), np.array(raw, np.int64), np.array(i16, np.int64)


def peak(x, f0, half=1.0):
    """Hann 窗单谱线峰值幅度（µV）与其频点"""
    x = np.asarray(x, float)
    x = x - x.mean()
    N = len(x)
    w = np.hanning(N)
    X = np.abs(np.fft.rfft(x * w)) / (w.sum() / 2.0)
    f = np.fft.rfftfreq(N, 1.0 / FS)
    m = (f >= f0 - half) & (f <= f0 + half)
    idx = np.where(m)[0]
    k = idx[np.argmax(X[idx])]
    return X[k], f[k]


data = {k: load(v) for k, v in FILES.items()}
print("=" * 112)
print("raw 域（列②，未滤波，0.1122 µV/code，**fs=500 Hz 修正后**）—— A 档 IMP620(9节) vs B 档 N2(13节)")
print("=" * 112)
print("  %-3s %-9s %-5s %14s %14s %14s %14s" % ("组", "信号", "接线", "50Hz", "100Hz", "150Hz", "200Hz"))
print("  " + "-" * 96)
for g in ("A", "B"):
    for sig in ("3mV/4Hz", "5mV/10Hz"):
        for wire in ("noin", "in"):
            x = (data[(g, sig, wire)][1].astype(float) - ZERO) * UV2
            v = ["%9.1f@%5.1f" % peak(x, f0) for f0 in (50., 100., 150., 200.)]
            print("  %-3s %-9s %-5s %14s %14s %14s %14s" % (g, sig, wire, *v))
    print("  " + "-" * 96)

print()
print("【放大倍数】in / noin（证明「620k 放大仍在」）")
print("  %-3s %-9s %12s %12s %12s %12s" % ("组", "信号", "50Hz", "100Hz", "150Hz", "200Hz"))
print("  " + "-" * 68)
for g in ("A", "B"):
    for sig in ("3mV/4Hz", "5mV/10Hz"):
        a = [(data[(g, sig, "in")][1].astype(float) - ZERO) * UV2,
             (data[(g, sig, "noin")][1].astype(float) - ZERO) * UV2]
        r = []
        for f0 in (50., 100., 150., 200.):
            p1, _ = peak(a[0], f0); p0, _ = peak(a[1], f0)
            r.append("%7.1fx" % (p1 / p0) if p0 > 0.5 else "   n/a")
        print("  %-3s %-9s %12s %12s %12s %12s" % (g, sig, *r))

print()
print("【屏幕域压制量】raw -> 屏幕（列④，fs=500，3mV 组）")
print("  %-3s %12s %12s %10s" % ("频点", "raw(µV)", "屏幕(µV)", "压制dB"))
print("  " + "-" * 52)
for g in ("A", "B"):
    xr = (data[(g, "3mV/4Hz", "in")][1].astype(float) - ZERO) * UV2
    xs = data[(g, "3mV/4Hz", "in")][2].astype(float) * UV4
    for f0 in (50., 100., 150.):
        pr, fr = peak(xr, f0); ps, fs_ = peak(xs, f0)
        print("  %s@ %6.1f %12.1f %12.2f %10.1f" % (g, f0, pr, ps, 20 * np.log10(ps / pr)))
