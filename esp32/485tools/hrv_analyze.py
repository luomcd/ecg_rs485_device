#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hrv_analyze.py —— QRS 峰定位精度分析（HRV 前置验收）

输入：ESP32 的 `result-dump rpeak_hrv.txt` 原样输出（带 `# ---- BEGIN/END`
      与 `cardiolite>` 提示也不影响解析），或只有 `P <序号> ...` 行的纯文本。

输出：定位误差的**分解**与判定，不是一个笼统的"准不准"。

================================ 原理 ================================

信号源是 AECG100 固定 180 bpm ⇒ **真值已知**：RR_true = 60/180 = 0.3333 s。
按 500 Hz 解释 ⇒ RR_true = 166.667 样本。

观测到的第 k 个峰位置可以写成：

    p_k = k·T + e_k          T = 真 RR，e_k = 第 k 个峰的**定位误差**

三个量必须分开，混在一起就会得出错的结论：

  ① **时基**（T 是否等于 166.667）
     → 用线性拟合的斜率估，与真值的偏差换算成等效采样率。
       斜率偏小 0.2% ⇒ 实际采样率偏高 0.2%，所有 HRV 时间量整体偏。

  ② **单峰定位抖动** σ(e)   ← HRV 最怕的就是这个
     🛑 **不能用"去线性趋势后的残差 std"来估**（第一版就栽在这）：
        p_k 是 RR 的累加，RR 若带白噪声，位置残差就是**随机游走**，
        std 随 √k 增长、可达几十样本，看起来像"抖动巨大"，其实反映的是
        极慢的漂移，不是单峰精度。实测反证：残差 σ=30.9 样本，
        而相邻差 RR 的 σ 只有 3.86 样本 —— 差 8 倍，模型错了。

     正确估计（两种，互为交叉校验）：
       (a) 相邻差法： RR_k = T + (e_{k+1} − e_k)
                     若 e 白噪声 ⇒ σ(RR) = √2·σ(e) ⇒ **σ(e) = σ(RR)/√2**
       (b) 二阶差分法：d2_k = p_{k+1} − 2p_k + p_{k−1} = e_{k+1} − 2e_k + e_{k−1}
                     ⇒ σ(d2) = √6·σ(e) ⇒ **σ(e) = σ(d2)/√6**
           二阶差分天然消掉线性趋势与慢漂，比 (a) 更抗心率慢变。

     两者应当在 30% 内一致；差得多说明 e 不是白噪声（有漏检/多检补偿、
     或检测点在 R 峰/T 波之间跳）。

  ③ **漏检 / 多检**
     · 漏检一个峰 ⇒ RR 出现一个 ≈2T 的间期
     · 多检一个峰 ⇒ RR 出现一个 ≈0.5T 的间期（实测最小 RR=116=0.70T 即此类）
     判据用"相对真值 T 的偏离"，不用相对中位数 —— 固定 180 bpm 下
     任何 ±15% 以上的 RR 都该解释清楚。

================================ 判定依据 ================================

HRV 的核心时域指标 RMSSD = sqrt(mean(ΔRR²))，生理典型值 20~50 ms。
工程要求测量噪声对 RMSSD 的污染 < 5%：

    允许 σ(e) < 0.05 × RMSSD_typical / √2
             ≈ 0.05 × 20 ms / 1.414 ≈ 0.71 ms ≈ 0.35 样本 @500 Hz

另有一条 HRV 文献经验判据：R 峰定位误差应 < 1 ms ⇒ < 0.5 样本。

    判定   条件（σ(e)，@500 Hz）        可用范围
    PASS   ≤ 0.5 样本（≤1.0 ms）        时域 + 频域 HRV 都可用
    WARN   0.5 ~ 1.0 样本（1~2 ms）     只可做均值/趋势类，RMSSD 需谨慎
    FAIL   > 1.0 样本（>2 ms）          不可用于 HRV，须改定位算法

用法
----
    py hrv_analyze.py rpeak_hrv.txt
    py hrv_analyze.py rpeak_hrv.txt --true-bpm 180 --fs 500
    py hrv_analyze.py --port COM35 --dump rpeak_hrv.txt     # 直接从串口取
    py hrv_analyze.py --selftest                            # 自检脚本本身

⚠ 换信号源（非 180 bpm）时必须改 `--true-bpm`，否则所有判定失去锚点。
"""
import argparse
import math
import statistics as st
import sys

# ── 判定阈值（单位：样本数 @ fs）────────────────────────────────────────
TH_PASS = 0.5      # σ(e) ≤ 0.5 样本 ⇒ ≤1 ms @500 Hz
TH_WARN = 1.0      # 0.5 ~ 1.0 样本

# ── 漏检/多检判据：相对真值 T 的偏离 ───────────────────────────────────
RR_LOW_RATIO  = 0.75   # RR < 0.75·T ⇒ 疑似多检（插入假峰）
RR_HIGH_RATIO = 1.40   # RR > 1.40·T ⇒ 疑似漏检（丢一个 QRS）


def parse_peaks(text):
    """从文本里抽峰序号。兼容两种格式与 result-dump 的提示符混排。

    紧凑：`P 12345 12346 ...`（16 峰/行）
    宽格式：`W 12345 -45 -30 -20 500 480 300 100`（一峰一行，第 1 个数是峰号）
    """
    peaks = []
    for line in text.splitlines():
        s = line.strip()
        for tag in ("W ", "P "):
            i = s.find(tag)
            if i < 0:
                continue
            # 允许行首是 `cardiolite> ` 之类的提示符
            prefix = s[:i].strip()
            if prefix and not prefix.endswith(">"):
                continue
            toks = s[i + 2:].split()
            if not toks:
                break
            if tag == "W ":
                # 宽格式：只取第 1 个字段（峰号），邻域值含负号不参与
                if toks[0].isdigit():
                    peaks.append(int(toks[0]))
            else:
                for tok in toks:
                    if tok.isdigit():
                        peaks.append(int(tok))
                    else:
                        break
            break
    return peaks


def linfit(xs, ys):
    """最小二乘 y = a·x + b，纯 Python，不依赖 numpy。"""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    a = sxy / sxx if sxx else 0.0
    return a, my - a * mx


def analyze(peaks, fs=500.0, true_bpm=180.0):
    out = {"n": len(peaks)}
    if len(peaks) < 8:
        out["error"] = "峰数不足（<8），无法做二阶差分与统计"
        return out

    T_true = fs * 60.0 / true_bpm           # 理论 RR（样本）
    out["T_true"] = T_true
    out["span_s"] = (peaks[-1] - peaks[0]) / fs

    # ---- ① 时基：两种估计 ----
    ks = list(range(out["n"]))
    a, b = linfit(ks, [float(p) for p in peaks])
    out["slope"]  = a
    out["fs_eff"] = fs * T_true / a if a else float("nan")

    # ---- ② 定位抖动：两种独立估计 ----
    rr = [y - x for x, y in zip(peaks, peaks[1:])]
    out["rr"] = rr
    out["rr_mean"] = st.mean(rr)
    out["rr_med"]  = st.median(rr)
    out["rr_sigma"] = st.pstdev(rr)
    out["rmssd"] = math.sqrt(sum((y - x) ** 2 for x, y in zip(rr, rr[1:])) / max(1, len(rr) - 1))

    # ★ 时基的**鲁棒**估计：用 RR 中位数，不用线性拟合斜率。
    #   原因：多检（RR≈0.65T）与漏检（RR≈1.65T）会系统性拉偏斜率，
    #   而中位数对少数异常值不敏感。实测 210 bpm：斜率给出 -1.0% 的"时基偏差"，
    #   但同期有 18 处多检 —— 那 1% 其实是检测异常造成的假象。
    out["T_med"]    = out["rr_med"]
    out["fs_eff_med"] = fs * T_true / out["rr_med"] if out["rr_med"] else float("nan")

    d2 = [peaks[i + 1] - 2 * peaks[i] + peaks[i - 1] for i in range(1, out["n"] - 1)]
    out["sigma_e_rr"] = out["rr_sigma"] / math.sqrt(2.0)
    out["sigma_e_d2"] = st.pstdev(d2) / math.sqrt(6.0)
    # 主判据取二阶差分（抗心率慢变）；相邻差法作交叉校验
    out["sigma_e"] = out["sigma_e_d2"]

    # ---- ③ 漏检/多检：相对真值 T 的偏离 ----
    low  = [(i, v) for i, v in enumerate(rr) if v < RR_LOW_RATIO * T_true]
    high = [(i, v) for i, v in enumerate(rr) if v > RR_HIGH_RATIO * T_true]
    out["extra"]   = low
    out["missed"]  = high
    out["anom_rate"] = (len(low) + len(high)) / max(1, len(rr))
    expect = out["span_s"] * true_bpm / 60.0 + 1.0
    out["expect"] = expect
    out["count_delta"] = out["n"] - expect
    # ---- ④ ★评审 T223 Q1：以「峰」为统计单位的稳健指标 ----
    #   🛑 一个错误峰会造成两个异常 RR ⇒ 异常峰数 ≈ 异常间期数 / 2。
    #     用 RR 间期数当分子会系统性高估一倍。
    #   🛑 阈值必须**相对**：90 bpm 的漂移是 9 样本，T=333 ⇒ 2.7%；
    #     若用绝对阈值 10 就会漏掉它（这正是之前判 90 bpm"异常率 0%"的原因）。
    thr = max(3.0, 0.02 * out["T_med"])
    err = [abs(v - out["T_med"]) for v in rr]
    n_anom = sum(1 for v in err if v > thr)
    se = sorted(err)
    out["err_thr"]        = thr
    out["anom_rr"]        = n_anom
    out["anom_peak"]      = n_anom / 2.0
    out["anom_peak_pct"]  = 100.0 * (n_anom / 2.0) / out["n"]
    out["p95_err"]        = se[int(0.95 * (len(se) - 1))]
    out["p99_err"]        = se[int(0.99 * (len(se) - 1))]
    out["max_err"]        = se[-1]
    # 综合判定：σ 达标 **且** 一个异常间期都没有
    out["verdict"] = ("PASS" if (out.get("sigma_e_d2", 99) <= 0.5 and n_anom == 0)
                      else "FAIL")

    return out


def report(r, fs, true_bpm):
    if "error" in r:
        print("✗", r["error"])
        return 1

    T = r["T_true"]
    print("=" * 68)
    print("QRS 峰定位精度分析")
    print("=" * 68)
    print(f"峰数              : {r['n']}")
    print(f"跨度              : {r['span_s']:.2f} s")
    print(f"真值（{true_bpm:.0f} bpm）  : RR = {T:.3f} 样本 = {T/fs*1000:.2f} ms")
    print()

    # ① 时基
    print("① 时基")
    dev_med = (r["fs_eff_med"] - fs) / fs * 100
    print(f"   RR 中位数      : {r['T_med']:.0f} 样本 ⇒ 等效 {r['fs_eff_med']:.2f} Hz"
          f"（{dev_med:+.3f}%）   ★鲁棒估计，以此为准")
    dev = (r["fs_eff"] - fs) / fs * 100
    print(f"   拟合斜率       : {r['slope']:.3f} 样本/拍，等效 {r['fs_eff']:.2f} Hz"
          f"（{dev:+.3f}%）")
    print("                    ⚠ 斜率会被漏检/多检拉偏，只在③无异常时才可信")
    if abs(dev_med) > 0.1:
        print(f"   ⚠ 时基偏差超过 0.1% ⇒ 所有 HRV 时间量整体偏 {dev_med:+.2f}%")
    print()

    # ② 抖动
    print("② 定位抖动 σ(e)")
    print(f"   二阶差分法     : {r['sigma_e_d2']:.3f} 样本 = {r['sigma_e_d2']/fs*1000:.2f} ms")
    print(f"   相邻差法       : {r['sigma_e_rr']:.3f} 样本 = {r['sigma_e_rr']/fs*1000:.2f} ms")
    print(f"   RR σ / RMSSD   : {r['rr_sigma']:.3f} / {r['rmssd']:.3f} 样本"
          f"（{r['rr_sigma']/fs*1000:.2f} / {r['rmssd']/fs*1000:.2f} ms）")
    ratio = (r["sigma_e_d2"] / r["sigma_e_rr"]) if r["sigma_e_rr"] else 0.0
    if 0.7 <= ratio <= 1.4:
        print(f"   交叉校验       : ✓ 两法一致（比值 {ratio:.2f}）⇒ e 近似白噪声")
    else:
        print(f"   交叉校验       : ⚠ 比值 {ratio:.2f} 偏离 1 ⇒ e 非白噪声"
              f"（有漏检补偿或检测点在 R/T 之间跳）")
    print()

    # ③ 漏检/多检
    print("③ 漏检 / 多检")
    print(f"   计数           : 应有 {r['expect']:.1f} 个，实测 {r['n']} 个"
          f"，差 {r['count_delta']:+.1f}")
    if r["extra"]:
        print(f"   ⚠ 疑似多检 {len(r['extra'])} 处（RR < {RR_LOW_RATIO:.2f}·T）：")
        for i, v in r["extra"][:6]:
            print(f"      第 {i+1} 个间期 = {v} 样本（{v/T:.2f}·T）")
    if r["missed"]:
        print(f"   ⚠ 疑似漏检 {len(r['missed'])} 处（RR > {RR_HIGH_RATIO:.2f}·T）：")
        for i, v in r["missed"][:6]:
            print(f"      第 {i+1} 个间期 = {v} 样本（{v/T:.2f}·T）")
    if not r["extra"] and not r["missed"]:
        print("   ✓ 无异常间期")
    else:
        print(f"   异常率         : {r['anom_rate']*100:.2f}%"
              f"（{len(r['extra'])+len(r['missed'])}/{len(r['rr'])}）")
        if r["anom_rate"] > 0.03:
            print("   ⚠ 异常率 >3% ⇒ 下面的 σ(e) 与斜率都被污染，"
                  "先修检测器再谈 HRV")
    print()

    # 判定
    s = r["sigma_e"]
    print("-" * 68)
    if s <= TH_PASS:
        v, why = "PASS", f"σ(e)={s:.2f} ≤ {TH_PASS} 样本（≤1.0 ms）"
    elif s <= TH_WARN:
        v, why = "WARN", f"σ(e)={s:.2f} 样本（{s/fs*1000:.2f} ms），落在 1~2 ms"
    else:
        v, why = "FAIL", f"σ(e)={s:.2f} 样本（{s/fs*1000:.2f} ms），>2 ms"
    print(f"判定：{v}   {why}")
    if v != "PASS":
        print("  依据：RMSSD 典型 20~50 ms，要求测量噪声污染 <5%")
        print("        ⇒ 允许 σ(e) < 0.05×20ms/√2 ≈ 0.71 ms ≈ 0.35 样本")
        print("  含义：均值/趋势类指标可用；RMSSD/SDNN/频域不可直接用")
        print(f"  差距：需要把 σ(e) 从 {s:.2f} 压到 ≤{TH_PASS} 样本"
              f"（改善 {s/TH_PASS:.1f} 倍）")
    print("-" * 68)
    return 0 if v == "PASS" else (2 if v == "WARN" else 3)


def dump_from_port(port, name, baud=115200, timeout=30.0):
    """直接从串口取一份 result-dump（免存盘）。"""
    try:
        import serial
    except ImportError:
        sys.exit("缺 pyserial —— 用 485tools\\py.cmd 跑本脚本")
    import time
    p = serial.Serial()
    p.port = port
    p.baudrate = baud
    p.timeout = 0.05
    p.dtr = False          # ★ 不能拉 DTR/RTS，否则 USB-CDC 直接复位板子
    p.rts = False
    p.set_buffer_size(rx_size=262144)
    p.open()
    time.sleep(0.3)
    p.reset_input_buffer()
    p.write(("result-dump %s\r\n" % name).encode())
    p.flush()
    buf = b""
    t_end = time.time() + timeout
    while time.time() < t_end:
        c = p.read(4096)
        if c:
            buf += c
        if b"# ---- END" in buf:
            time.sleep(0.4)
            buf += p.read(8192)
            break
        time.sleep(0.05)
    p.close()
    return buf.decode("utf-8", "replace")


def selftest():
    """合成数据自检：注入已知 σ 的高斯抖动，看两种估计能否还原。"""
    import random
    random.seed(7)
    fs, bpm = 500.0, 180.0
    T = fs * 60.0 / bpm
    print("注入 σ → 还原（样本）")
    for sig in (0.3, 1.0, 3.0):
        peaks, t = [], 0.0
        for _ in range(300):
            peaks.append(int(round(t + random.gauss(0, sig))))
            t += T
        r = analyze(peaks, fs, bpm)
        print(f"  {sig:4.1f} → d2 法 {r['sigma_e_d2']:.2f} / rr 法 {r['sigma_e_rr']:.2f}"
              f"   斜率 {r['slope']:.3f}（真值 {T:.3f}）")
    print("（int 取整会引入 ~0.29 的底噪：sqrt(σ²+0.29²)）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="QRS 峰定位精度分析（HRV 前置验收）")
    ap.add_argument("file", nargs="?", help="result-dump 输出文件")
    ap.add_argument("--port", default="COM35", help="配合 --dump 直接从串口取")
    ap.add_argument("--dump", metavar="NAME", help="先 result-dump NAME 再分析")
    ap.add_argument("--fs", type=float, default=500.0, help="采样率，默认 500")
    ap.add_argument("--true-bpm", type=float, default=180.0,
                    help="信号源设定心率（AECG100），默认 180")
    ap.add_argument("--selftest", action="store_true", help="自检脚本本身")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.dump:
        text = dump_from_port(a.port, a.dump)
    elif a.file:
        with open(a.file, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        ap.print_help()
        return 1

    peaks = parse_peaks(text)
    if not peaks:
        print("✗ 没解析到任何 `P ...` 行。检查输入是否为 result-dump 输出。")
        return 1
    return report(analyze(peaks, a.fs, a.true_bpm), a.fs, a.true_bpm)


if __name__ == "__main__":
    sys.exit(main())
