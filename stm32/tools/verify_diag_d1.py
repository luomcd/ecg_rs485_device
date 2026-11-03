#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D1 诊断档离线验证（2026-09-10）

目的
----
在**不动板上固件**的前提下，用 AECG100 60bpm 1mV 实测数据离线重放两条链，
定量比较 D1 诊断档相对 v3 监护/ST 档的形态收益（重点是 QRS/R-S 幅度）。

    v3 = dc_block(0.05,double) -> notch 50/100/150(Q30) -> butter(4,[0.05, 40])
    D1 = dc_block(0.05,double) -> notch 50(Q30)         -> butter(4,[0.05,150])

口径
----
* µV 换算：UV_PER_CODE = 0.1122（2026-09-09 AECG100 标称回代定标）
* 因果滤波（scipy sosfilt 逐样本等价），与板上 MCU 实现一致，不用 filtfilt
* 模板平均：以 **raw** 定位 R 峰（不用滤波输出，避免振铃伪峰），R 前 0.35s ~ R 后 0.55s
* 基线：raw 减全段中值；滤波链输出本身已去 DC，直接乘 0.1122 即为 µV
* 自检：v3 重放应与数据第 5 列（板上 y）逐样本一致（残差 σ≈0）

运行
----
  C:\\Users\\luom\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe verify_diag_d1.py
  可选: python verify_diag_d1.py "路径\\xxx.txt"
"""

import os
import sys

import numpy as np
from scipy import signal
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FS = 1e6 / 1172.0
UV_PER_CODE = 0.1122
F_LOW = 0.05

# 中文字体（Windows）
for _f in ("Microsoft YaHei", "SimHei", "DejaVu Sans"):
    try:
        matplotlib.rcParams["font.sans-serif"] = [_f]
        break
    except Exception:
        pass
matplotlib.rcParams["axes.unicode_minus"] = False

DEFAULT_FILE = r"C:\CQQianya\TI\Demo\tools\1mV-ECG-2026-09-09 190245.txt"


# ---------------------------------------------------------------- 滤波链构建
def notch_sos(hz, q=30.0):
    b, a = signal.iirnotch(hz, q, fs=FS)
    return signal.tf2sos(b, a)


def chain_v3():
    secs = [notch_sos(h) for h in (50.0, 100.0, 150.0)]
    secs.append(signal.butter(4, [F_LOW, 40.0], btype="bandpass", fs=FS, output="sos"))
    return np.vstack(secs)


def chain_d1():
    secs = [notch_sos(50.0)]
    secs.append(signal.butter(4, [F_LOW, 150.0], btype="bandpass", fs=FS, output="sos"))
    return np.vstack(secs)


def chain_d2():
    """D2 = D1 + 100Hz notch（6 节）。"""
    secs = [notch_sos(50.0), notch_sos(100.0)]
    secs.append(signal.butter(4, [F_LOW, 150.0], btype="bandpass", fs=FS, output="sos"))
    return np.vstack(secs)


def dc_block(x, fc=0.05):
    """一阶高通 y[n]=(x[n]-x[n-1])+r*y[n-1]，内部 double，与板上 ecg_dc_block 等价。

    关键：板上"首样本预热"——以 x[0] 为直流起点、y[0] 强制输出 0，
    因此纯直流输入(6M 常数)时输出恒为 0，**不产生 15~18s 衰减瞬态**。
    用 lfilter 时必须设 zi=[-x[0]] 复刻这一行为，否则 y[0]=6M 会引入虚假瞬态。
    """
    r = 1.0 - 2.0 * np.pi * fc / FS
    y, _ = signal.lfilter([1.0, -1.0], [1.0, -r], x, zi=[-float(x[0])])
    return y


def run_chain(raw, sos):
    return signal.sosfilt(sos, dc_block(np.asarray(raw, dtype=np.float64)))


# ---------------------------------------------------------------- 数据解析
def load(path):
    d = np.loadtxt(path, dtype=np.float64)
    if d.ndim == 1:
        d = d.reshape(1, -1)
    return d


# ---------------------------------------------------------------- R 峰定位
def find_r_peaks(raw_uv, min_dist_s=0.5):
    """在 raw 上定位 R 峰：滑动 20ms 平滑后找超过 0.5*max 的局部极大。"""
    w = max(1, int(0.02 * FS))
    sm = np.convolve(raw_uv, np.ones(w) / w, mode="same")
    thr = 0.5 * np.max(sm)
    cand = np.where((sm[1:-1] > thr) &
                    (sm[1:-1] >= sm[:-2]) &
                    (sm[1:-1] > sm[2:]))[0] + 1
    md = int(min_dist_s * FS)
    peaks, last = [], -10 * md
    for p in cand:
        if p - last >= md:
            peaks.append(p)
            last = p
    return np.array(peaks, dtype=int)


def template(y, peaks, pre_s=0.35, post_s=0.55):
    a, b = int(pre_s * FS), int(post_s * FS)
    segs = [y[p - a:p + b] for p in peaks if p - a >= 0 and p + b <= len(y)]
    if not segs:
        return None, None
    n = min(len(s) for s in segs)
    return np.mean([s[:n] for s in segs], axis=0), n


def metrics(tpl, n):
    """tpl: 模板（µV），n: 模板长度；R 位于索引 a=int(0.35*FS)。

    基线口径：**PR(PQ) 段 = R 峰前 100~40 ms 均值**（临床 ST 测量标准等电位参考），
    所有指标均相对该基线，raw 与滤波链用同一口径，可直接比较。
    """
    a = int(0.35 * FS)
    t_ms = (np.arange(n) - a) / FS * 1000.0

    base = tpl[(t_ms >= -100) & (t_ms <= -40)].mean()
    tpl = tpl - base

    def win(lo, hi):
        m = (t_ms >= lo) & (t_ms <= hi)
        return tpl[m], t_ms[m]

    R = tpl[a]
    s_seg, _ = win(0, 100)
    S = s_seg.min()
    p_seg, _ = win(-250, -110)
    P = p_seg.max()
    t_seg, _ = win(150, 450)
    T = t_seg.max()
    st_seg, _ = win(60, 105)
    ST = st_seg.mean()
    tp_seg, _ = win(450, 700)
    return dict(base=base, R=R, RS=R - S, P=P, T=T, ST=ST,
                TP=tp_seg.mean(), TPstd=tp_seg.std())


# ---------------------------------------------------------------- 主流程
def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FILE
    if not os.path.exists(path):
        print("找不到数据文件:", path)
        sys.exit(1)
    print("数据:", os.path.basename(path))

    d = load(path)
    raw = d[:, 1]                       # 第 2 列：24-bit ADC 原始码
    board_codes = d[:, 4]               # 第 5 列：板上滤波输出（**code 域**，非 µV）
    raw_uv = (raw - np.median(raw)) * UV_PER_CODE

    y_v3_codes = run_chain(raw, chain_v3())
    y_d1_codes = run_chain(raw, chain_d1())
    y_d2_codes = run_chain(raw, chain_d2())
    y_v3 = y_v3_codes * UV_PER_CODE
    y_d1 = y_d1_codes * UV_PER_CODE
    y_d2 = y_d2_codes * UV_PER_CODE

    # --- 自检：自动判定该数据板上跑的是哪一档（v3 / D1 / D2），逐样本比对 ---
    cands = {"v3": y_v3_codes, "D1": y_d1_codes, "D2": y_d2_codes}
    print("\n[自检] 板上第5列 vs 三档复刻（code 域残差 σ）:")
    best, best_sig = None, None
    for k, yc in cands.items():
        m = min(len(yc), len(board_codes))
        s = (yc[:m] - board_codes[:m]).std()
        print(f"    {k:<3}: σ={s:8.3f} codes")
        if best_sig is None or s < best_sig:
            best, best_sig = k, s
    print(f"  → 板上实际档位判定 = **{best}**（残差 σ={best_sig:.3f} codes）")
    if best_sig > 5:
        print("  ⚠ 残差偏大 —— 该数据可能不是本表任一档的完整落地版本")

    # --- R 峰 ---
    peaks = find_r_peaks(raw_uv)
    print(f"检出 R 峰 {len(peaks)} 个，平均 RR = "
          f"{np.mean(np.diff(peaks))/FS*1000:.1f} ms ({60*FS/np.mean(np.diff(peaks)):.1f} bpm)")

    # --- 模板 ---
    tpl_raw, n = template(raw_uv, peaks)
    tpl_v3, _ = template(y_v3, peaks)
    tpl_d1, _ = template(y_d1, peaks)
    tpl_d2, _ = template(y_d2, peaks)
    a = int(0.35 * FS)
    t_ms = (np.arange(n) - a) / FS * 1000.0

    mets = {"raw": metrics(tpl_raw, n), "v3": metrics(tpl_v3, n),
            "D1": metrics(tpl_d1, n), "D2": metrics(tpl_d2, n)}

    # --- 指标表（均相对 PR 段基线） ---
    print(f"\n{'指标':<10}{'raw':>8}{'v3(0.05-40)':>13}{'D1(0.05-150)':>14}"
          f"{'D2(+100n)':>12}{'D2 vs D1':>10}{'D2 vs raw':>11}")
    print("-" * 80)
    rows = (("R", "R 峰", False), ("RS", "R-S", False), ("P", "P 峰", False),
            ("T", "T 峰", False), ("ST", "ST 段", True),
            ("TP", "TP 段均", True), ("TPstd", "TP σ", True))
    for k, label, is_abs in rows:
        r, v, x, z = mets["raw"][k], mets["v3"][k], mets["D1"][k], mets["D2"][k]
        dz = f"{z - x:+.0f}" if is_abs else f"{(z - x) / x * 100:+.1f}%"
        dr = f"{z - r:+.0f}" if is_abs else f"{(z - r) / r * 100:+.1f}%"
        print(f"{label:<10}{r:>8.0f}{v:>13.0f}{x:>14.0f}{z:>12.0f}{dz:>10}{dr:>11}")
    print(f"{'(PR 基线绝对偏移)':<10}{mets['raw']['base']:>8.0f}"
          f"{mets['v3']['base']:>13.0f}{mets['D1']['base']:>14.0f}"
          f"{mets['D2']['base']:>12.0f}")

    # --- 画图 ---
    out_png = "diag_d2_verify.png"
    fig, ax = plt.subplots(2, 1, figsize=(11, 8))

    seg = slice(int(2 * FS), int(6 * FS))
    ax[0].plot(np.arange(seg.stop - seg.start) / FS + 2, raw_uv[seg],
               lw=0.7, color="#999999", label="raw (AECG100 1mV)")
    ax[0].plot(np.arange(seg.stop - seg.start) / FS + 2, y_v3[seg],
               lw=1.0, color="#1f77b4", label="v3  0.05-40Hz")
    ax[0].plot(np.arange(seg.stop - seg.start) / FS + 2, y_d1[seg],
               lw=1.0, color="#d62728", label="D1  0.05-150Hz")
    ax[0].plot(np.arange(seg.stop - seg.start) / FS + 2, y_d2[seg],
               lw=1.0, color="#2ca02c", label="D2  0.05-150Hz+100n", alpha=0.9)
    ax[0].set_xlabel("时间 (s)")
    ax[0].set_ylabel("幅度 (µV)")
    ax[0].set_title(f"D2 / D1 / v3 时域对比 — {os.path.basename(path)}")
    ax[0].legend(loc="upper right", fontsize=9)
    ax[0].grid(alpha=0.3)

    tpl_raw_b = tpl_raw - mets["raw"]["base"]
    tpl_v3_b = tpl_v3 - mets["v3"]["base"]
    tpl_d1_b = tpl_d1 - mets["D1"]["base"]
    tpl_d2_b = tpl_d2 - mets["D2"]["base"]

    ax[1].plot(t_ms, tpl_raw_b, lw=1.2, color="#999999", label="raw")
    ax[1].plot(t_ms, tpl_v3_b, lw=1.6, color="#1f77b4", label="v3 0.05-40Hz")
    ax[1].plot(t_ms, tpl_d1_b, lw=1.6, color="#d62728", label="D1 0.05-150Hz")
    ax[1].plot(t_ms, tpl_d2_b, lw=1.6, color="#2ca02c", label="D2 +100Hz notch")
    ax[1].axhline(0, color="k", lw=0.5, alpha=0.5)
    ax[1].axvspan(60, 105, color="orange", alpha=0.12, label="ST 段")
    ax[1].axvspan(150, 450, color="green", alpha=0.08, label="T 波窗")
    ax[1].set_xlabel("相对 R 峰时间 (ms)")
    ax[1].set_ylabel("幅度 (µV)")
    ax[1].set_title(f"模板平均（{len(peaks)} 拍，R 峰对齐）")
    ax[1].legend(loc="upper right", fontsize=9)
    ax[1].grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    print(f"\n图已保存: {out_png}")


if __name__ == "__main__":
    main()
