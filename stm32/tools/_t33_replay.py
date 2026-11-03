# -*- coding: utf-8 -*-
"""
_t33_replay.py —— IMP620_N2（13 节）落码后的回放验证（v3，作用于实测波形）

v1 为什么废弃
-------------
v1 想"从 raw[0] 起跑复刻整条链"，结果 p-p = 10000 µV（int16 满量程饱和）。
原因：文件**从 620k 接入瞬间开始**（raw 第一行就在 152k code 的大跳变上），
而 0.05Hz 四阶高通的建立时间 15~18s（ecg_filter.h 明载）。固件在抓包时状态
早已建立，复刻却从零起步 ⇒ 两者瞬态完全不同（相关系数 0.002），不可比。
⇒ **结论：没有"抓包前 N 秒"的 raw 就无法做零起点时域复刻。** 记录在案。

v2 的口径 bug（本版修正）
-------------------------
列⑤ 曾被当成 853Hz 序列 ⇒ FFT 频率轴整体偏 1.7066 倍。
实测：列② seq 跨度 28408 = 33.29s，列③ 降频序跨度 16647 = 500Hz × 33.29s。
⇒ **列⑤ 是"每次 500Hz 有输出时才打印的那个 853 样本"，实际等效 500Hz。**

v3 做什么（全部基于实测，不重跑滤波器）
---------------------------------------
第 1 关  同源复核：列⑤×0.1122  vs  列④×0.1526
         （列④ = 列⑤ 重采样+定标；实测斜率 0.9993、相关 0.9995 ⇒ 同源）
第 2 关  抠线回放（判决预测）：对**实测列④**做 FFT，把 100Hz 带（97~103）与
         150Hz 带（146~154）的谱按 **逐频点 N2a/IMP620 传递比**缩放，其余频点不动，
         反变换后按 §1 口径（1s 滑动窗 p-p 中位数）算比值。
         分母 = 同组 noin 的实测列④ p-p。
第 3 关  直连段保真：noin 段 4/10Hz 信号在抠线前后的幅度差（应 ≈0）。

⚠ 本轮唯一的外推：100/150 两条窄带换成新档后的传递比。该模型在 IMP620 上标定过：
   149.88Hz 模型算 41.26 µV，实测 41.15 µV（差 0.3%）。
"""
import io
import sys
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FS_RAW = 1e6 / 1172.0
FS_OUT = 500.0
UV, UV_I16 = 0.1122, 0.1526
DESK = "C:/Users/luom/Desktop/ADS1293/"

from scipy import signal as sps


def notch(f0, Q, fs=FS_RAW):
    b, a = sps.iirnotch(f0, Q, fs=fs)
    r = sps.tf2sos(b, a)[0]
    return (r[0], r[1], r[2], r[4], r[5])


def bp(fs=FS_RAW):
    return [(r[0], r[1], r[2], r[4], r[5]) for r in
            sps.butter(4, [0.05, 150.0], btype="bandpass", fs=fs, output="sos")]


IMP620 = [notch(49.80, 12.0), notch(50.00, 12.0), notch(50.20, 12.0),
          notch(100.0, 15.0), notch(150.0, 30.0)] + bp()
N2A = [notch(49.80, 12.0), notch(50.00, 12.0), notch(50.20, 12.0),
       notch(99.80, 20.0), notch(100.00, 20.0), notch(100.20, 20.0),
       notch(149.80, 20.0), notch(150.00, 20.0), notch(150.20, 20.0)] + bp()


def Hlin(secs, f):
    z = np.exp(-1j * 2 * np.pi * np.asarray(f, float) / FS_RAW)
    H = np.ones(len(np.atleast_1d(f)), dtype=complex)
    for (b0, b1, b2, a1, a2) in secs:
        H *= (b0 + b1 * z + b2 * z ** 2) / (1 + a1 * z + a2 * z ** 2)
    return np.abs(H)


def Hdb(secs, f):
    return 20 * np.log10(Hlin(secs, f))


def parse(fn):
    raw, i16, f5 = [], [], []
    bad = 0
    for ln in io.open(fn, "r", encoding="utf-8", errors="ignore"):
        p = ln.split()
        if len(p) != 7:
            bad += 1
            continue
        try:
            raw.append(int(p[1]))
            i16.append(int(p[3]))
            f5.append(float(p[4]))
        except ValueError:
            bad += 1
    return (np.array(raw, dtype=np.float64), np.array(i16, dtype=np.int32),
            np.array(f5, dtype=np.float64), bad)


def pp_med(x_uv, fs=FS_OUT, win_s=1.0, step_s=0.2):
    n = int(win_s * fs)
    if len(x_uv) < n:
        return float("nan")
    st = max(1, int(step_s * fs))
    return float(np.median([np.ptp(x_uv[i:i + n]) for i in range(0, len(x_uv) - n + 1, st)]))


def amp_at(x, f0, fs=FS_OUT, half=1.5):
    N = len(x)
    w = np.hanning(N)
    X = np.abs(np.fft.rfft((x - x.mean()) * w)) / (w.sum() / 2.0)
    f = np.fft.rfftfreq(N, 1.0 / fs)
    m = (f >= f0 - half) & (f <= f0 + half)
    return float(X[m].max()) if m.any() else float("nan")


def carve(x, band, new_old_secs, fs=FS_OUT):
    """把 [f_lo,f_hi] 内每个频点按 H_new/H_old 缩放，其余频点完全不动的"""
    N = len(x)
    X = np.fft.rfft(x - x.mean())
    f = np.fft.rfftfreq(N, 1.0 / fs)
    m = (f >= band[0]) & (f <= band[1])
    ratio = Hlin(new_old_secs[0], f[m]) / Hlin(new_old_secs[1], f[m])
    X[m] *= ratio
    return np.fft.irfft(X, n=N) + x.mean()


CASES = [
    ("3mV / 4Hz", "3mV-4Hz-620k-in-2026-09-18 093952.txt",
     "3mV-4Hz-620k-noin-2026-09-18 094253.txt", 4.0),
    ("5mV / 10Hz", "5mV-10Hz-620k-in-2026-09-18 094441.txt",
     "5mV-10Hz-620k-noin-2026-09-18 094358.txt", 10.0),
]
B100 = (97.0, 103.0)
B150 = (146.0, 154.0)

print("=" * 112)
print("IMP620_N2 回放验证 v3 —— 作用于实测波形（只换 100/150 两条窄带，其余频点一个不动）")
print("=" * 112)
print("  列⑤ = 滤波输出 code 值（×0.1122 µV），列④ = 显示 int16（×0.1526 µV）；列⑤ 等效 500Hz")
print("  口径：p-p = 500Hz 域 1s 滑动窗中位数（与 memory/2026-09-18.md §1 一致）")
print()

for name, fin, fnoin, fsig in CASES:
    raw_i, i16_i, f5_i, bad_i = parse(DESK + fin)
    raw_n, i16_n, f5_n, bad_n = parse(DESK + fnoin)
    rec = i16_i.astype(np.float64) * UV_I16
    rec_n = i16_n.astype(np.float64) * UV_I16

    # ---------------- 第 1 关 ----------------
    a, b = f5_i * UV, rec
    n = min(len(a), len(b))
    cc = np.corrcoef(a[:n], b[:n])[0, 1]
    sl = np.polyfit(a[:n], b[:n], 1)[0]
    print("#" * 112)
    print("%s   raw=%d（%.1f s）列④=%d（%.1f s）坏行 %d/%d"
          % (name, len(raw_i), len(raw_i) / FS_RAW, len(i16_i), len(i16_i) / FS_OUT, bad_i, bad_n))
    print("  第 1 关：列⑤×0.1122 vs 列④×0.1526（同源复核）")
    print("    相关 %.6f  斜率 %.4f  rms差 %.2f µV  p-p %.1f / %.1f µV"
          % (cc, sl, np.sqrt(np.mean((a[:n] - b[:n]) ** 2)), np.ptp(a), np.ptp(b)))
    print("    ⇒ %s" % ("列④ 就是列⑤ 的重采样显示版（同源确认）" if cc > 0.999 else "口径需再查"))

    # ---------------- 第 2 关 ----------------
    pp_den = pp_med(rec_n)
    cur = pp_med(rec)
    y100 = carve(rec, B100, (N2A, IMP620))
    y150 = carve(rec, B150, (N2A, IMP620))
    yboth = carve(carve(rec, B100, (N2A, IMP620)), B150, (N2A, IMP620))
    print()
    print("  第 2 关：抠线回放（实测列④ 上做）")
    print("    分子 p-p：IMP620 实测 %.1f µV     分母 p-p：noin 实测 %.1f µV" % (cur, pp_den))
    print()
    print("    %-36s %12s %10s %8s %10s" % ("方案", "分子 p-p(µV)", "比值", "判决", "Δ(pp)"))
    for lab, v in (("IMP620（实测，现状）", cur),
                   ("N2a：只抠 100Hz 带", pp_med(y100)),
                   ("N2a：只抠 150Hz 带", pp_med(y150)),
                   ("N2a：100+150 都抠（★本档）", pp_med(yboth))):
        r = 100.0 * v / pp_den
        print("    %-36s %12.1f %9.2f%% %8s %+10.2f" % (lab, v, r, "达标" if r <= 110 else "FAIL", v - cur))

    # 三线残留（列④ 口径）
    print()
    print("    三线实测残留（列④，单谱线峰值）与换档后：")
    print("      %-8s %14s %14s %12s" % ("f(Hz)", "IMP620(实测)", "N2a(抠线)", "改善"))
    for f0, band in ((50.0, (48.0, 52.0)), (100.0, B100), (150.0, B150)):
        av = amp_at(rec, f0)
        bv = av if f0 == 50.0 else amp_at(carve(rec, band, (N2A, IMP620)), f0)
        print("      %-8.0f %12.2f µV %12.2f µV %10.1f×" % (f0, av, bv, av / max(bv, 1e-9)))

    # ---------------- 第 3 关 ----------------
    y3 = carve(carve(rec_n, B100, (N2A, IMP620)), B150, (N2A, IMP620))
    sa, sb = amp_at(rec_n, fsig), amp_at(y3, fsig)
    print()
    print("  第 3 关：noin 段 %g Hz 信号保真" % fsig)
    print("    IMP620 %.3f µV → N2a %.3f µV   Δ %+.5f dB   ⇒ %s"
          % (sa, sb, 20 * np.log10(sb / sa),
             "带内零触碰 OK" if abs(20 * np.log10(sb / sa)) < 0.01 else "有变化"))
    print()

# ---------------- 汇总：带内传递比 ----------------
print("#" * 112)
print("  附：带内传递比（N2a − IMP620，dB，逐频点）")
for lab, band in (("100Hz 带 97~103", B100), ("150Hz 带 146~154", B150)):
    f = np.arange(band[0], band[1] + 1e-9, 0.05)
    d = Hdb(N2A, f) - Hdb(IMP620, f)
    imax = int(np.argmax(d))
    print("    %-18s 最差 %+7.2f dB @ %.2f Hz ｜ 中心处 %+8.2f dB ｜ 带边缘 %+6.2f dB"
          % (lab, d.min(), f[int(np.argmin(d))], d[imax], max(d[0], d[-1])))
print()
print("  注：50Hz 带**一个频点都没动**（IMP620 三节簇已压到 −73.9dB）——这就是 N2a 选「50 簇不动」的实测依据。")
