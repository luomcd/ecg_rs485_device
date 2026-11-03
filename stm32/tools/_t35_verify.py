# -*- coding: utf-8 -*-
"""
_t35_verify.py v2 —— IMP620_N2（13 节）上板实测验收

口径（严格对齐 09-18 §1 定案，勿改）：
  判据 = 屏幕总 p-p(620k) / p-p(开路) <= 110%
  数据源 = txt 第④列（int16，500Hz，0.1526 uV/LSB）
  **p-p 取 1s 滑动窗中位数**（避开偏置漂移污染全段 p-p）—— 上午 104.21/102.83 就是这个口径
  列② = 原始 ADC code（853.2423Hz，零点 6075000，0.1122 uV/code）
"""
import io
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

D = "C:/Users/luom/Desktop/ADS1293/"
UV4, UV2, ZERO = 0.1526, 0.1122, 6075000.0
FS_MCU, FS_OUT = 1e6 / 1172.0, 500.0

FILES = {
    ("A", "3mV/4Hz", "in"):   "3mV-4Hz-620k-in-2026-09-18 093952.txt",
    ("A", "3mV/4Hz", "noin"): "3mV-4Hz-620k-noin-2026-09-18 094253.txt",
    ("A", "5mV/10Hz", "noin"): "5mV-10Hz-620k-noin-2026-09-18 094358.txt",
    ("A", "5mV/10Hz", "in"):  "5mV-10Hz-620k-in-2026-09-18 094441.txt",
    ("B", "3mV/4Hz", "in"):   "3mv-4hz-620k-in-2026-09-18 111023.txt",
    ("B", "3mV/4Hz", "noin"): "3mv-4hz-620k-noin-2026-09-18 111133.txt",
    ("B", "5mV/10Hz", "noin"): "5mv-10hz-620k-noin-2026-09-18 111214.txt",
    ("B", "5mV/10Hz", "in"):  "5mv-10hz-620k-in-2026-09-18 111248.txt",
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
            continue
    return np.array(seq, np.int64), np.array(raw, np.int64), np.array(i16, np.int64)


def pp_win(x, fs=FS_OUT, win=1.0, step=0.25):
    """1s 滑窗 p-p -> (中位数, 90分位, 最大)"""
    n, s = int(win * fs), max(1, int(step * fs))
    if len(x) <= n:
        v = np.ptp(x)
        return float(v), float(v), float(v)
    vals = np.array([np.ptp(x[i:i + n]) for i in range(0, len(x) - n + 1, s)])
    return (float(np.median(vals)), float(np.percentile(vals, 90)), float(vals.max()))


def spec_amp(x, fs, f0, half=3.0):
    x = np.asarray(x, float) - np.mean(x)
    N = len(x)
    w = np.hanning(N)
    X = np.abs(np.fft.rfft(x * w)) / (w.sum() / 2.0)
    f = np.fft.rfftfreq(N, 1.0 / fs)
    m = (f >= f0 - half) & (f <= f0 + half)
    idx = np.where(m)[0]
    k = idx[np.argmax(X[idx])]
    return X[k], f[k]


data = {}
for key, fn in FILES.items():
    data[key] = load(fn)

print("=" * 110)
print("IMP620_N2（13 节）上板实测验收    A = IMP620(9节, 09:4x)   vs   B = IMP620_N2(13节, 11:1x)")
print("=" * 110)
print("口径：屏幕列④，0.1526 µV/LSB，500Hz；p-p = 1s 滑动窗中位数（对齐 09-18 §1 定案）")

print("\n[1] ★ 主判据：屏幕 p-p（1s 滑窗中位数，µV）")
print("  %-3s %-9s %10s %10s %10s %10s %9s %9s"
      % ("组", "信号", "noin(分母)", "in(分子)", "比值", "判决", "Δ(µV)", "时长in/no"))
print("  " + "-" * 96)
for g in ("A", "B"):
    for sig in ("3mV/4Hz", "5mV/10Hz"):
        a = data[(g, sig, "in")][2].astype(float) * UV4
        b = data[(g, sig, "noin")][2].astype(float) * UV4
        ma, _, _ = pp_win(a)
        mb, _, _ = pp_win(b)
        r = ma / mb * 100
        print("  %-3s %-9s %10.1f %10.1f %9.2f%% %10s %+9.1f %5.1f/%4.1fs"
              % (g, sig, mb, ma, r, "达标" if r <= 110 else "超标", ma - mb,
                 len(a) / FS_OUT, len(b) / FS_OUT))

print("\n[2] 滑窗离散度（中位/90分位/最大，µV）—— 判「运气值 vs 稳健值」")
print("  %-3s %-9s %-5s %26s %26s" % ("组", "信号", "接线", "in 中位/90%/max", "noin 中位/90%/max"))
print("  " + "-" * 92)
for g in ("A", "B"):
    for sig in ("3mV/4Hz", "5mV/10Hz"):
        row = []
        for wire in ("in", "noin"):
            x = data[(g, sig, wire)][2].astype(float) * UV4
            row.append(pp_win(x))
        print("  %-3s %-9s %-5s %26s %26s"
              % (g, sig, "in/noin",
                 "%7.1f %7.1f %7.1f" % row[0], "%7.1f %7.1f %7.1f" % row[1]))

print("\n[3] ★★ 三线残留对照  A 档 vs B 档（屏幕域 列④，fs=500，uV 峰值）")
print("  %-3s %-9s %-5s %11s %11s %11s" % ("组", "信号", "接线", "50Hz", "100Hz", "150Hz"))
print("  " + "-" * 62)
for g in ("A", "B"):
    for sig in ("3mV/4Hz", "5mV/10Hz"):
        for wire in ("noin", "in"):
            x = data[(g, sig, wire)][2].astype(float) * UV4
            vals = []
            for f0 in (50.0, 100.0, 150.0):
                amp, fpk = spec_amp(x, FS_OUT, f0)
                vals.append("%7.2f@%5.1f" % (amp, fpk))
            print("  %-3s %-9s %-5s %11s %11s %11s" % (g, sig, wire, *vals))
print("  " + "-" * 62)
print("  ← 判决看 in 行：150Hz 若从 ~24uV 掉到 <1uV，即陷波真生效；50Hz 是唯一「未动」的对照项")

print("\n[4] ★★ raw 域三线（列②，未滤波，0.1122uV/code）—— 证明「放大还在，是滤波器压掉的」")
print("    ⚠ fs 修正 2026-09-18 11:3x：列② 的等效采样率是 **500 Hz**，不是 FS_MCU=853.2423！")
print("      依据：全谱主峰 fs=500 -> 49.93Hz；fs=853 -> 85.21Hz（853/500=1.7065）。")
print("      ⇒ 原 [4] 段频点整体偏 1.7065x，读到的是 47~53Hz 窗口里的噪声底，raw 值全部作废。")
print("  %-3s %-9s %-5s %12s %12s %12s" % ("组", "信号", "接线", "50Hz", "100Hz", "150Hz"))
print("  " + "-" * 66)
for g in ("A", "B"):
    for sig in ("3mV/4Hz", "5mV/10Hz"):
        for wire in ("noin", "in"):
            x = (data[(g, sig, wire)][1].astype(float) - ZERO) * UV2
            vals = []
            for f0 in (50.0, 100.0, 150.0):
                amp, fpk = spec_amp(x, FS, f0)
                vals.append("%9.1f@%5.1f" % (amp, fpk))
            print("  %-3s %-9s %-5s %12s %12s %12s" % (g, sig, wire, *vals))

print("\n[5] ★ 1s 窗内「信号 vs 干扰」拆分（列④，µV 峰值，中位窗）")
print("  %-3s %-9s %-5s %11s %11s %11s %11s" % ("组", "信号", "接线", "信号f0", "50Hz", "100Hz", "150Hz"))
print("  " + "-" * 78)
for g in ("A", "B"):
    for sig, f0 in (("3mV/4Hz", 4.0), ("5mV/10Hz", 10.0)):
        for wire in ("in", "noin"):
            x = data[(g, sig, wire)][2].astype(float) * UV4
            n = int(FS_OUT)
            mid = len(x) // 2
            seg = x[mid:mid + n if mid + n <= len(x) else len(x)]
            vals = [spec_amp(seg, FS_OUT, f0)[0]] + [spec_amp(seg, FS_OUT, f)[0] for f in (50., 100., 150.)]
            print("  %-3s %-9s %-5s %11.1f %11.2f %11.2f %11.2f" % (g, sig, wire, *vals))

print("\n[6] B 组 in 长记录分段诊断（全长 p-p 为什么虚高；每 5s 一段）")
for sig in ("3mV/4Hz", "5mV/10Hz"):
    x = data[("B", sig, "in")][2].astype(float) * UV4
    seg = int(5 * FS_OUT)
    print("  %s  (总长 %.1fs)" % (sig, len(x) / FS_OUT))
    out = []
    for i in range(0, len(x) - seg + 1, seg):
        out.append("  %2d-%2ds:%7.1f" % (i / FS_OUT, (i + seg) / FS_OUT, np.ptp(x[i:i + seg])))
    for k in range(0, len(out), 3):
        print("    " + "".join(out[k:k + 3]))
