# -*- coding: utf-8 -*-
"""_t37_ab.py —— 只用 2026-09-18 当天 8 个文件做正面 A/B 对比。
A = IMP620 (9节, 09:39~09:44)   B = IMP620_N2 (13节, 11:10~11:15)
不含任何跨天数据、不含历史外推。"""
import io, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

D = "C:/Users/luom/Desktop/ADS1293/"
UV4, UV2, ZERO = 0.1526, 0.1122, 6075000.0
FS = 500.0

FILES = {
    ("A", "3mV/4Hz",  "noin"): "3mV-4Hz-620k-noin-2026-09-18 094253.txt",
    ("A", "3mV/4Hz",  "in"):   "3mV-4Hz-620k-in-2026-09-18 093952.txt",
    ("A", "5mV/10Hz", "noin"): "5mV-10Hz-620k-noin-2026-09-18 094358.txt",
    ("A", "5mV/10Hz", "in"):   "5mV-10Hz-620k-in-2026-09-18 094441.txt",
    ("B", "3mV/4Hz",  "noin"): "3mv-4hz-620k-noin-2026-09-18 111133.txt",
    ("B", "3mV/4Hz",  "in"):   "3mv-4hz-620k-in-2026-09-18 111023.txt",
    ("B", "5mV/10Hz", "noin"): "5mv-10hz-620k-noin-2026-09-18 111214.txt",
    ("B", "5mV/10Hz", "in"):   "5mv-10hz-620k-in-2026-09-18 111248.txt",
}

def load(fn):
    a = {k: [] for k in ("ir", "raw", "if", "i16", "f5")}
    for ln in io.open(D + fn, encoding="utf-8", errors="ignore"):
        p = ln.split()
        if len(p) != 7:
            continue
        try:
            a["ir"].append(int(p[0])); a["raw"].append(int(p[1])); a["if"].append(int(p[2]))
            a["i16"].append(int(p[3])); a["f5"].append(float(p[4]))
        except ValueError:
            pass
    return {k: np.array(v) for k, v in a.items()}

def pp_med(x, win=1.0, step=0.25):
    n, s = int(win * FS), int(step * FS)
    if len(x) <= n:
        return float(np.ptp(x))
    return float(np.median([np.ptp(x[i:i + n]) for i in range(0, len(x) - n + 1, s)]))

def peak(x, f0, half=1.0):
    y = np.asarray(x, float); y = y - y.mean()
    N = len(y); w = np.hanning(N)
    X = np.abs(np.fft.rfft(y * w)) / (w.sum() / 2.0)
    f = np.fft.rfftfreq(N, 1.0 / FS)
    m = (f >= f0 - half) & (f <= f0 + half); i = np.where(m)[0]
    k = i[np.argmax(X[i])]
    return X[k], f[k]

S = {k: load(v) for k, v in FILES.items()}

print("=" * 116)
print("[1] 主判据（列④ 显示口径，0.1526 µV/LSB，p-p 取 1s 滑窗中位数，步长 0.25s）")
print("=" * 116)
print("  %-6s %-9s %10s %10s %10s %10s %10s" % ("组", "信号", "noin", "in", "比值", "Δ(µV)", "时长s"))
print("  " + "-" * 78)
res = {}
for g, lab in (("A", "A IMP620"), ("B", "B N2")):
    for sig in ("3mV/4Hz", "5mV/10Hz"):
        di = S[(g, sig, "in")]["i16"].astype(float) * UV4
        dn = S[(g, sig, "noin")]["i16"].astype(float) * UV4
        pi, pn = pp_med(di), pp_med(dn)
        r = pi / pn * 100
        res[(g, sig)] = (pn, pi, r)
        print("  %-6s %-9s %10.1f %10.1f %9.2f%% %10.1f %6.1f/%.1f"
              % (lab, sig, pn, pi, r, pi - pn, len(di) / FS, len(dn) / FS))
    print("  " + "-" * 78)

print()
print("=" * 116)
print("[2] 列⑤ 口径交叉验证（filterraw 原始 double，×0.1122 µV/code，**无 int16 量化**）")
print("=" * 116)
print("  %-10s %-9s %10s %10s %10s %10s %10s" % ("组", "信号", "noin⑤", "in⑤", "比值⑤", "列⑤/列④", "相关系数"))
print("  " + "-" * 84)
for g in ("A", "B"):
    for sig in ("3mV/4Hz", "5mV/10Hz"):
        f5i = S[(g, sig, "in")]["f5"] * UV2
        f5n = S[(g, sig, "noin")]["f5"] * UV2
        i16i = S[(g, sig, "in")]["i16"].astype(float) * UV4
        pi, pn = pp_med(f5i), pp_med(f5n)
        n = min(len(f5i), len(i16i))
        c = np.corrcoef(f5i[:n], i16i[:n])[0, 1]
        print("  %-10s %-9s %10.1f %10.1f %9.2f%% %10.3f %10.5f"
              % ("A IMP620" if g == "A" else "B N2", sig, pn, pi, pi / pn * 100,
                 pi / res[(g, sig)][1], c))
    print("  " + "-" * 84)
print("  说明：列⑤ 比值与列④ 比值应一致；列⑤/列④ ≈ 0.1526/0.1122 = %.3f（定标互证）" % (0.1526 / 0.1122))

print()
print("=" * 116)
print("[3] 原始域（列②，未滤波，0.1122 µV/code）—— 证明「620k 放大与环境注入没变」")
print("=" * 116)
print("  %-12s %-8s %14s %14s %14s %14s" % ("组/接线", "信号", "50Hz", "100Hz", "150Hz", "200Hz"))
print("  " + "-" * 82)
rawamp = {}
for g in ("A", "B"):
    for wire in ("noin", "in"):
        for sig in ("3mV/4Hz", "5mV/10Hz"):
            x = (S[(g, sig, wire)]["raw"].astype(float) - ZERO) * UV2
            v = [peak(x, f0) for f0 in (50., 100., 150., 200.)]
            rawamp[(g, sig, wire)] = [z[0] for z in v]
            print("  %-12s %-8s %14.1f %14.1f %14.1f %14.1f"
                  % ("%s %s" % (g, "620k" if wire == "in" else "开路"), sig, *[z[0] for z in v]))
    print("  " + "-" * 82)
print("  【in / noin 放大倍数】")
print("  %-12s %-8s %14s %14s %14s %14s" % ("组", "信号", "50Hz", "100Hz", "150Hz", "200Hz"))
print("  " + "-" * 82)
for g in ("A", "B"):
    for sig in ("3mV/4Hz", "5mV/10Hz"):
        a = rawamp[(g, sig, "in")]; b = rawamp[(g, sig, "noin")]
        print("  %-12s %-8s %14s" % (g, sig, " ".join(("%13.1fx" % (a[i] / b[i])) if b[i] > 0.5 else "          n/a" for i in range(4))))
    print("  " + "-" * 82)

print()
print("=" * 116)
print("[4] 屏幕域（列⑤，620k 接入组）—— 证明「新档把 100/150Hz 压掉了」")
print("=" * 116)
print("  %-8s %12s %12s %8s %12s %12s %8s" % ("频点", "A 3mV", "B 3mV", "压制", "A 5mV", "B 5mV", "压制"))
print("  " + "-" * 76)
scr = {}
for f0 in (50., 100., 150.):
    row = []
    for g, sig in (("A", "3mV/4Hz"), ("B", "3mV/4Hz"), ("A", "5mV/10Hz"), ("B", "5mV/10Hz")):
        x = S[(g, sig, "in")]["f5"] * UV2
        a = peak(x, f0)[0]
        scr[(g, sig, f0)] = a
        row.append(a)
    print("  %-8.0f %12.2f %12.2f %7.1fx %12.2f %12.2f %7.1fx"
          % (f0, row[0], row[1], row[0] / max(row[1], 1e-9), row[2], row[3], row[2] / max(row[3], 1e-9)))
print("  " + "-" * 76)
print("  【raw -> 屏幕 净衰减 dB】")
print("  %-8s %12s %12s %12s %12s" % ("频点", "A 3mV", "B 3mV", "A 5mV", "B 5mV"))
print("  " + "-" * 60)
for f0 in (50., 100., 150.):
    v = []
    for g, sig in (("A", "3mV/4Hz"), ("B", "3mV/4Hz"), ("A", "5mV/10Hz"), ("B", "5mV/10Hz")):
        pr = rawamp[(g, sig, "in")][(50., 100., 150., 200.).index(f0)]
        v.append(20 * np.log10(scr[(g, sig, f0)] / pr))
    print("  %-8.0f %12.1f %12.1f %12.1f %12.1f" % (f0, *v))
