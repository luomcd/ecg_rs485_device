# -*- coding: utf-8 -*-
"""T31: 钉死「实测工频摊在 49.4~50.6 Hz（宽 1.2 Hz）」的口径 —— 表观谱宽 vs 真实漂移

问题（09-18 待确认 ①）：IMP620 报告 §3.2 与 ecg_filter_coeffs.h:40 / ecg_filter.c:139 都写
    「实测工频摊在 49.4~50.6Hz」（宽 1.2 Hz）
而 09-17 §14.3 与 09-18 §8 两次实测漂移都只有 0.11~0.16 Hz，差 10 倍。
本脚本用同一批实测数据回答：1.2 Hz 到底是"工频在漂"，还是"FFT 窗的主瓣在摊"。

输出两块：
  A) 表观宽度扫描：同一段 raw 数据，换不同窗长 N（Hann），量 50Hz 峰的表观宽度（-3dB/-6dB/零点）
     ⇒ 哪个 N 给出 1.2 Hz，就说明 1.2 Hz 是"那个窗长下的主瓣宽"，与漂移无关。
  B) 真实漂移（Hilbert 相位斜率法）：扫全部 7 列文件（跨 09-16/17/18 三天、多种供电与接线状态），
     逐 2 s 量瞬时 50Hz 频率，给段内跨度与跨文件极差；150Hz 按 3x 外推。
"""
import os, sys, glob
import numpy as np
from scipy.signal import butter, sosfiltfilt, hilbert, get_window

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ecg_analyze as E

DESK = r"C:\Users\luom\Desktop\ADS1293"
FS = 500.0

# ────────────────────────────── A) 表观谱宽 vs 窗长 ──────────────────────────────
def apparent_width(x, fs, f0=50.0, half=5.0, drops=(3.0, 6.0, 20.0)):
    """Hann 窗 FFT，量主峰相对峰值下降 drops(dB) 处的宽度（线性插值跨阈点）。"""
    x = np.asarray(x, float)
    N = len(x)
    x = x - x.mean()
    w = get_window("hann", N)
    X = np.abs(np.fft.rfft(x * w))
    f = np.fft.rfftfreq(N, 1.0 / fs)
    m = (f > f0 - half) & (f < f0 + half)
    idx = np.where(m)[0]
    i0 = idx[np.argmax(X[idx])]
    Xp = X[i0]
    out = {"f_peak": f[i0], "N": N, "df": fs / N}
    for d in drops:
        thr = Xp * 10 ** (-d / 20.0)

        def cross(i, j):
            """i,j 相邻且跨越 thr 时的插值频率"""
            if X[j] == X[i]:
                return f[i]
            return f[i] + (thr - X[i]) * (f[j] - f[i]) / (X[j] - X[i])

        # 左：从 i0 往左走，停在"最后一个 X>thr"处 ⇒ 跨阈点在 [L-1, L]
        L = i0
        while L - 1 >= idx[0] and X[L - 1] > thr:
            L -= 1
        fl = cross(L - 1, L) if L > idx[0] else f[idx[0]]
        # 右：停在"最后一个 X>thr"处 ⇒ 跨阈点在 [R, R+1]
        R = i0
        while R + 1 <= idx[-1] and X[R + 1] > thr:
            R += 1
        fr = cross(R, R + 1) if R < idx[-1] else f[idx[-1]]
        out["w%d" % int(d)] = fr - fl
    return out


def run_width_scan():
    fn = os.path.join(DESK, "3mV-4Hz-620k-in-2026-09-18 093952.txt")
    rec = E.parse(fn)
    tu, y = E.resample(rec, "raw", FS)
    gaps = E.find_gaps(rec)
    # 取最长无缺口段的起点后 20 s
    if gaps:
        segs, prev = [], 0
        for g in gaps:
            segs.append((prev, int(g[0]) - 1))
            prev = int(g[0]) + 1
        segs.append((prev, len(rec["seq"]) - 1))
        a, b = max(segs, key=lambda s: s[1] - s[0])
        t0 = tu[a] + 1.0
    else:
        t0 = tu[0] + 1.0
    print("=" * 92)
    print("A) 表观谱宽 vs 窗长  —— 文件 %s" % rec["file"])
    print("   数据段起点 t=%.1fs，raw 域（列②，0.1122 µV/code），用 Hann 窗，无零填充" % t0)
    print("=" * 92)
    print("%8s %10s %9s %9s %9s %9s %9s" % ("N", "时长(s)", "df(Hz)", "-3dB宽", "-6dB宽", "-20dB宽", "峰位"))
    print("-" * 92)
    for N in (512, 800, 833, 1024, 1667, 2048, 4096, 8192):
        i0 = int((t0 - tu[0]) * FS)
        if i0 + N > len(y):
            continue
        r = apparent_width(y[i0:i0 + N], FS)
        print("%8d %10.2f %9.3f %9.3f %9.3f %9.3f %9.4f"
              % (N, N / FS, r["df"], r["w3"], r["w6"], r["w20"], r["f_peak"]))
    print("-" * 92)
    print("Hann 窗理论：-3dB 宽 ≈ 1.44·df ｜ -6dB 宽 ≈ 2.0·df ｜ -20dB 宽 ≈ 3.1·df（df = 500/N）")
    print("⇒ 若「49.4~50.6」(宽 1.2Hz) 指 -6dB 宽 ⇒ df≈0.60 ⇒ N≈833 (1.67s)")
    print("⇒ 若指 -20dB 宽 ⇒ df≈0.39 ⇒ N≈1300 (2.6s)；若指零点宽(4·df) ⇒ N≈1667 (3.3s)")
    return


# ────────────────────────────── B) 真实漂移 ──────────────────────────────
def inst_freq(tu, y, f0=50.0, bw=1.0, seg=2.0):
    """逐 seg 秒窗的瞬时频率 + 该窗带内 rms（用于剔除低信噪比窗的伪读数）"""
    lo, hi = (f0 - bw / 2) / (FS / 2), (f0 + bw / 2) / (FS / 2)
    sos = butter(4, [lo, hi], btype="band", output="sos")
    z = hilbert(sosfiltfilt(sos, y))
    ph = np.unwrap(np.angle(z))
    n = int(seg * FS)
    fr, am = [], []
    for i in range(0, len(ph) - n, n):
        tt = tu[i:i + n] - tu[i]
        p = ph[i:i + n]
        k = np.polyfit(tt, p, 1)[0] / (2 * np.pi)
        fr.append(k)
        am.append(np.std(z[i:i + n]) * np.sqrt(2))
    return np.array(fr), np.array(am)


def amp50(y, f0=50.0, bw=1.0):
    lo, hi = (f0 - bw / 2) / (FS / 2), (f0 + bw / 2) / (FS / 2)
    sos = butter(4, [lo, hi], btype="band", output="sos")
    return float(np.std(sosfiltfilt(sos, y)) * np.sqrt(2))


def run_drift():
    files = sorted(glob.glob(os.path.join(DESK, "*.txt")))
    rows = []
    for fn in files:
        try:
            rec = E.parse(fn)
        except Exception:
            continue
        if rec["ncol"] != 7 or rec["goodn"] < 4000:
            continue
        tu, y = E.resample(rec, "raw", FS)
        a50 = amp50(y, 50.0)
        if a50 < 300:                     # 50Hz 幅度太低（直连/无输入）⇒ 漂移测不准，剔除
            continue
        fr, am = inst_freq(tu, y, 50.0, 1.0, 2.0)
        keep = am > 0.5 * np.median(am)   # 剔除低信噪比窗（相位斜率法在那种窗会跳）
        n_raw = len(fr)
        fr = fr[keep & (fr > 48) & (fr < 52)]
        if len(fr) < 2:
            continue
        rows.append(dict(f=rec["file"], n=len(fr), nraw=n_raw, a50=a50,
                         fmin=fr.min(), fmax=fr.max(), fmed=np.median(fr),
                         span=fr.max() - fr.min(), sd=fr.std()))
    print()
    print("=" * 92)
    print("B) 真实工频漂移（Hilbert 相位斜率，2 s 窗，raw 域列②，只取 50Hz 幅度 >300µV 的文件）")
    print("=" * 92)
    print("%-46s %6s %7s %9s %9s %9s %9s" % ("文件", "窗数", "剔除", "50Hz(rms)", "最低", "最高", "段内跨度"))
    print("-" * 92)
    for r in sorted(rows, key=lambda z: z["f"]):
        print("%-46s %6d %7d %9.0f %9.4f %9.4f %9.4f"
              % (r["f"][:46], r["n"], r["nraw"] - r["n"], r["a50"], r["fmin"], r["fmax"], r["span"]))
    print("-" * 92)
    if rows:
        gmin = min(r["fmin"] for r in rows)
        gmax = max(r["fmax"] for r in rows)
        print("★ 跨文件（= 跨天跨环境）极差：%.4f ~ %.4f Hz  ⇒ 宽 %.4f Hz" % (gmin, gmax, gmax - gmin))
        print("★ 3x 外推 150Hz 带：%.4f ~ %.4f  ⇒ 宽 %.4f Hz" % (3 * gmin, 3 * gmax, 3 * (gmax - gmin)))
        print("★ 最大段内跨度：%.4f Hz ⇒ 3x = %.4f Hz" % (max(r["span"] for r in rows),
                                                          3 * max(r["span"] for r in rows)))
    return


def run_peak_track():
    """含激励文件里 45-55Hz 带内的谱峰追踪：看 49.4 / 50.6 这类数字有没有可能是激励谐波。"""
    print()
    print("=" * 92)
    print("C) 45-55Hz 带内谱峰追踪（逐 2s 窗，raw 域列②，Hann N=1000，df=0.5Hz）")
    print("=" * 92)
    for name in ("5mV-10Hz-620k-in-2026-09-18 094441.txt",
                 "3mV-4Hz-620k-in-2026-09-18 093952.txt",
                 "3mv-input-unselect-2026-09-16 202524.txt"):
        fn = os.path.join(DESK, name)
        if not os.path.exists(fn):
            continue
        rec = E.parse(fn)
        tu, y = E.resample(rec, "raw", FS)
        N = 1000
        w = get_window("hann", N)
        print("\n-- %s" % name)
        print("%5s %10s %11s %11s" % ("窗", "峰位(Hz)", "峰值(µV)", "45-55rms"))
        for k in range(0, len(y) // N):
            seg = y[k * N:(k + 1) * N]
            seg = seg - seg.mean()
            X = np.abs(np.fft.rfft(seg * w)) * 2 / np.sum(w)
            f = np.fft.rfftfreq(N, 1.0 / FS)
            m = (f > 45) & (f < 55)
            idx = np.where(m)[0]
            j = idx[np.argmax(X[idx])]
            sos = butter(4, [45 / (FS / 2), 55 / (FS / 2)], btype="band", output="sos")
            rms = float(np.std(sosfiltfilt(sos, seg)) * np.sqrt(2))
            print("%5d %10.3f %11.0f %11.0f" % (k, f[j], X[j], rms))
    return


if __name__ == "__main__":
    run_width_scan()
    run_drift()
    run_peak_track()
