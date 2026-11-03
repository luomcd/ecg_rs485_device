#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
plot_rate5_view.py —— 5 列格式数据快速体检 + 出图 (reRate / Filter001 同款格式)

数据格式 (与板上串口输出一致):
  第1列: 853Hz 原始 ECG 样本序号 (seq_in)
  第2列: 原始数据 (raw, 24bit codes, ~6.07M 量级)
  第3列: 降采样输出序号 (seq_out, 500Hz)
  第4列: int16 转换输出 (LSB, ±5mV ↔ ±32767)
  第5列: 原始数据滤波后 (853Hz codes 域)

用法:
  python plot_rate5_view.py "1mV-ECG-2026-09-09 085920.txt"            # 默认出图+统计
  python plot_rate5_view.py "xxx.txt" --win 5:15                        # 指定放大窗口(s)
  python plot_rate5_view.py "xxx.txt" --out myview.png                  # 指定输出文件名
  python plot_rate5_view.py "xxx.txt" --no-qrs                          # 跳过 QRS 检测(纯噪声数据可加)

输出:
  1) 同名 _view.png 体检图(4 面板)
  2) 终端打印统计摘要: 时长/输出率/各列σ/pp/HR 粗估

依赖: numpy scipy matplotlib(托管 venv 已装)
"""
import sys, argparse
from pathlib import Path
import numpy as np

# ---------------- 常量(与固件/方案文档一致) ----------------
FS_IN  = 1e6 / 1172.0     # 853.24 Hz
DT_US  = 1172.0
FS_OUT = 500.0            # 降采样输出率(宏可选 250/500,按数据实际率自适应)
LSB_UV = 0.1526           # int16 输出 LSB (µV/LSB, ±5mV ↔ 32767)
UV_PER_CODE = 0.1122      # 853Hz codes→µV (AECG100 1mV 实测校准 2026-09-09, R=0.88mV 口径)

# ---------------- 解析 ----------------
def load_rate5(path):
    """解析 5 列格式, 自动跳过非数字行(如 'afe test stop')。"""
    a, raw, o, lsb, y = [], [], [], [], []
    for line in open(path, encoding="utf-8", errors="replace"):
        f = line.split()
        if len(f) < 5:
            continue
        try:
            a.append(int(f[0])); raw.append(int(f[1]))
            o.append(int(f[2])); lsb.append(int(f[3]))
            y.append(float(f[4]))
        except ValueError:
            pass
    return (np.array(a), np.array(raw), np.array(o),
            np.array(lsb), np.array(y))

def dedup_out(seq_out, val):
    """第3/4列: 每输出样本第一次出现的行 → 等间隔 500Hz 序列。"""
    first = {}
    for i, s in enumerate(seq_out):
        if s not in first:
            first[s] = i
    seqs = sorted(first)
    return np.array([val[first[s]] for s in seqs])

def qrs_events(y, t, thr_scale=1200.0, merge_n=30):
    """幅度阈值事件检测(返回时间/幅值数组)。非零相位链的粗检,仅用于 HR/幅度粗估。"""
    cand = np.where(np.abs(y) > thr_scale)[0]
    peaks = []
    i = 0
    while i < len(cand):
        j = i
        while j + 1 < len(cand) and cand[j+1] - cand[j] < merge_n:
            j += 1
        seg = cand[i:j+1]
        k = seg[int(np.argmax(np.abs(y[seg])))]
        peaks.append((t[k], y[k]))
        i = j + 1
    return (np.array([p[0] for p in peaks]),
            np.array([p[1] for p in peaks]))

# ---------------- 统计摘要 ----------------
def summarize(a, raw, o, lsb, y, win=None):
    fs = FS_IN
    N = len(a)
    dur = N / fs
    m = a > int(5 * fs)                     # 默认去前 5s 建立期
    if win:
        t0, t1 = win
        m = m & ((a / fs) >= t0) & ((a / fs) <= t1)
    t = (a - a[0]) / fs
    d_in = np.diff(a)
    gaps = int((d_in > 1).sum())
    print("=" * 64)
    print(f"文件: {N} 行 ≈ {dur:.1f}s @ {fs:.1f}Hz | seq缺口 {gaps} 处")
    print("-" * 64)
    print(f"[col2 raw   ] 中位={np.median(raw):.0f}  σ={raw.std():.0f}  "
          f"范围[{raw.min()},{raw.max()}]")
    dc_off = np.median(raw) - 6075000
    print(f"              与0V理论码差 {dc_off:+.0f} codes ({dc_off*UV_PER_CODE/1000:+.3f} mV)")
    # 第5列 y
    ym = y[m]
    print(f"[col5 y     ] 稳σ={ym.std():.1f} codes = {ym.std()*UV_PER_CODE:.2f} µV"
          f"   pp={ym.max()-ym.min():.0f} codes")
    # 输出率与第4列
    L = dedup_out(o, lsb)
    fout = len(L) / dur
    ml = np.arange(len(L)) / FS_OUT
    mL = ml >= 5.0
    if win:
        mL = mL & (ml >= win[0]) & (ml <= win[1])
    Lm = L[mL]
    print(f"[col4 int16 ] 输出率={fout:.0f}Hz  稳σ={Lm.std():.1f} LSB = {Lm.std()*LSB_UV:.2f} µV"
          f"   pp={Lm.max()-Lm.min()} LSB = {(Lm.max()-Lm.min())*LSB_UV/1000:.3f} mV")
    # QRS 事件粗检(col5)
    tm = (a[m] - a[0]) / fs
    pkt, pkv = qrs_events(y[m], tm)
    print("-" * 64)
    if len(pkt) >= 3:
        rr = np.diff(pkt)
        hr = 60.0 / np.median(rr)
        print(f"事件检测: {len(pkt)} 次 / {(tm[-1]-tm[0]):.0f}s "
              f"(合并窗内可能含 P/T, 若 HR 异常请看图)"
              f"  粗HR≈{hr:.0f} bpm")
        print(f"事件幅度: 正峰max={pkv.max():.0f} codes 负谷min={pkv.min():.0f} codes")
        pp_c = pkv.max() - pkv.min()
        pp_L = Lm.max() - Lm.min()
        print(f"  → 滤波y pp≈{pp_c:.0f} codes | int16 pp={pp_L} LSB")
        print(f"  ※ LSB 定标反推: 若 AECG 档位为 K mV 峰峰(信号实际摆幅)")
        print(f"     则 LSB_OUT_UV = {1000*1.0:.0f}·K/{pp_L:.0f} = {1000/pp_L:.4f}·K µV/LSB")
        print(f"     UV_PER_CODE = 1000·K/{pp_c:.0f} = {1000/pp_c:.4f}·K µV/code")
        print(f"     (代入 K 即得实测值; 与实测 0.1122 / 输出 0.1526 对照)")
    else:
        print("事件检测: 无显著周期事件(纯噪声/基线数据)")
    print("=" * 64)
    return dict(t=t, y=y, L=L, ml=ml)

# ---------------- 绘图 ----------------
def plot_view(a, raw, o, lsb, y, win, out, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for f in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"]:
        try:
            font_manager.fontManager.addfont(f)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=f).get_name()
            break
        except Exception:
            pass
    plt.rcParams["axes.unicode_minus"] = False

    fs = FS_IN
    t = (a - a[0]) / fs
    # int16 输出等间隔序列
    L = dedup_out(o, lsb)
    tl = np.arange(len(L)) / FS_OUT
    # y(853) → µV 用于对照面板
    yuv = y * UV_PER_CODE / 1000.0      # codes → mV (推算口径)

    fig, axs = plt.subplots(4, 1, figsize=(14, 13))
    fig.suptitle(title or Path(out).stem, fontsize=13, y=0.995)
    c_qrs, c_ref = "#1f77b4", "#B03A2E"

    # --- A. int16 全段, 心电图纸式 ±5mV ---
    ax = axs[0]
    ax.plot(tl, L, lw=0.5, color=c_qrs)
    ax.axhline(0, color="r", lw=0.7)
    ax.set_ylim(-32768, 32767)
    ax.set_ylabel("LSB (±5mV)")
    sig = L[tl >= 5.0].std()
    ax.set_title(f"A. int16 全段(设备输出视角)  稳σ={sig:.0f} LSB "
                 f"6σ占屏={6*sig/65536*100:.2f}%", fontsize=10, loc="left")
    ax.grid(alpha=0.25)

    # --- B. int16 放大窗 (win or 自动找 QRS 段) ---
    ax = axs[1]
    if win:
        t0, t1 = win
    else:
        # 自动: 从 t=5s 起取 4s
        t0, t1 = 5.0, min(9.0, tl[-1])
    w = (tl >= t0) & (tl <= t1)
    ax.plot(tl[w], L[w], lw=0.8, color=c_qrs)
    ax.axhline(0, color="r", lw=0.7)
    # 心电图网格: 0.1mV/小格 @ 500Hz
    mv2px = 32767.0 / 5.0
    for g in np.arange(-5, 5.1, 0.1):
        ax.axhline(g * mv2px, color="#DDDDDD", lw=0.4)
    for g in np.arange(-5, 5.1, 0.5):
        ax.axhline(g * mv2px, color="#AAAAAA", lw=0.7)
    ax.set_ylim(-5 * mv2px, 5 * mv2px)
    ax.set_xlim(t0, t1)
    ax.set_ylabel("LSB")
    ax.set_title(f"B. int16 放大 {t0:.1f}–{t1:.1f}s (0.1mV 小格/0.5mV 粗格)", fontsize=10, loc="left")

    # --- C. y(853Hz codes)同窗对照 ---
    ax = axs[2]
    w = (t >= t0) & (t <= t1)
    ax.plot(t[w], y[w], lw=0.7, color=c_ref)
    ax.axhline(0, color="r", lw=0.7)
    ax.set_ylabel("codes")
    ax.set_title("C. 第5列滤波 y(853Hz)同窗对照 (codes 域)", fontsize=10, loc="left")
    ax.grid(alpha=0.25)

    # --- D. raw 全段 (看 DC/LOD 行为) ---
    ax = axs[3]
    ax.plot(t, (raw - np.median(raw)) / 1000.0, lw=0.4, color="#888888")
    ax.set_ylabel("raw−中位 (kcode)")
    ax.set_title("D. raw 相对中位 (kcode) —— 看直流/毛刺/异常", fontsize=10, loc="left")
    ax.grid(alpha=0.25)
    ax.set_xlabel("时间 (s)")

    plt.tight_layout(rect=[0, 0, 1, 0.985])
    plt.savefig(out, dpi=110)
    print(f"图已保存: {out}")

# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser(description="5列格式 ECG 数据快速体检出图")
    ap.add_argument("file", help="数据文件路径")
    ap.add_argument("--win", default=None, help="放大窗口 '5:9'(s)")
    ap.add_argument("--out", default=None, help="输出 png 路径(默认 <名>_view.png)")
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    a, raw, o, lsb, y = load_rate5(args.file)
    if len(a) == 0:
        print("未解析到有效数据行!"); sys.exit(1)
    win = tuple(map(float, args.win.split(":"))) if args.win else None
    out = args.out or (str(Path(args.file).with_suffix("")) + "_view.png")
    summarize(a, raw, o, lsb, y, win=win)
    plot_view(a, raw, o, lsb, y, win, out, args.title)

if __name__ == "__main__":
    main()
