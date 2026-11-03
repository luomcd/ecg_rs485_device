# -*- coding: utf-8 -*-
"""
ecg_analyze.py — ADS1293 ASCII 7 列记录「统一分析入口」
（收敛 2026-09-16 的 iit_usb_*.py / iit_gap_*.py / iit_notch_*.py 一次性脚本）

用法
----
  python ecg_analyze.py --file "3mv-input-unselect-2026-09-16 202524.txt" --mode all
  python ecg_analyze.py --file A.txt --file B.txt --mode metrics          # 多文件对比表
  python ecg_analyze.py --file A.txt --mode gap --gapwin 300,1200,3000    # M17 残差判决
  python ecg_analyze.py --file A.txt --mode metrics --json out.json

模式
----
  audit    掉块 / 坏行审计（Δ列①、Δ列③、坏行原文、累计时基偏差、缺口物理时长）
  metrics  指标（4Hz / 50Hz 宽带 rms / raw p-p / 谱纯度 / 链抑制 dB / filter50÷4Hz）
  gap      M17 残差法判时基（列⑤ 4Hz 拟合残差：行号时基 vs 列① 时基）
  all      = audit + metrics

口径铁律（改这里 = 改全盘）
---------------------------
  · **5 列**（09-16 17:59 前）= ①序 ②raw ③降频序 ④int16 ⑤滤波；
    **7 列**（17:59 后）= 上列 + ⑥0x19 + ⑦0x18。两格式**前 5 列位置与含义完全一致**，
    故列②/列⑤ 口径不变，可混用对比。**只信列②**(M6)
  · UV_PER_CODE = 0.1122 µV/code（列②/列⑤ 同域）；列④ 用 0.1526；零点 6,075,000
  · fs_raw = 853.2423 Hz；输出为 500 Hz 降频流
  · **时基统一 t = 列①/fs_raw**（M16）；缺口判真伪用残差法（M17），禁用单点相位/过零
    ★T170-A（2026-10-08）：**列① 恒 0 时自动退化列③/fs_out** —— 当前 HEAD 固件的
      ascii-1 第①列是**字面 0**（`ads1293.c:1403-1404`）⇒ 退化。**退化时 audit/metrics
      都会先打一行提示**，绝不静默换口径；旧数据（列① 有值）路径逐字不变。
  · 所有跨窗指标取「避开缺口 ±guard 的合格窗」中位（M7 抗离群）
  · 接入/旁路对比前，先证明两态真不同；跨记录比工频只用宽带 rms + 谱纯度（M12）
  · ★M18：链后残留（列⑤ 的 50Hz）是**"拍"**——工频实际频率与 notch 中心差 ≈0.04 Hz，
    实测包络可在 25 s 内从 0.6 µV 升到 299 µV（×500）。⇒ **跨记录/跨段比 flt50 必须
    同段位同段长**（脚本默认 `--seg fixed` 取"缺口+0.5 s 起 6 s"单段，即为兼容旧报告口径）；
    用 `--seg auto`（全记录中位）与 fixed 混比会把结论差出 2~6 倍。
"""
import argparse
import json
import math
import os
import sys

import numpy as np

# ── 口径常量 ────────────────────────────────────────────────────────────────
FS_RAW = 853.2423          # ADC 真实采样率（DRDY 周期 1172 µs）
FS_OUT = 500.0             # ASCII 输出降频后采样率
CENTER = 6075000           # 偏移二进制零点
UV = 0.1122                # µV / code（列② raw 与列⑤ 滤波 code 同域）
UV_I16 = 0.1526            # µV / code（列④，仅对照用）
BAND_LO, BAND_HI = 45.0, 55.0     # 工频宽带
CORE_LO, CORE_HI = 49.5, 50.5     # 工频核心（谱纯度分子）
F_SIG = 4.0                # 测试信号频率

try:                                            # Windows 控制台中文保险
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ── 解析（M8：列数 + 格式双校验）─────────────────────────────────────────────
def parse(fn):
    """解析。**支持 5 列（09-16 17:59 前）与 7 列（含诊断位）两种格式**：
        5 列 = ①序 ②raw ③降频序 ④int16 ⑤滤波
        7 列 = 上列 + ⑥0x19 + ⑦0x18
       前 5 列位置与含义完全一致，故列②/列⑤ 口径不变。坏行单独归档，不进统计。
    """
    rec = {k: [] for k in ("seq", "raw", "dec", "i16", "flt", "err19", "lod18")}
    rec["bad"], rec["rows"], rec["ncol"] = [], 0, None
    with open(fn, encoding="utf-8", errors="replace") as fh:
        for ln, L in enumerate(fh, 1):
            rec["rows"] += 1
            p = L.split()
            if len(p) not in (5, 7):
                rec["bad"].append((ln, len(p), L.rstrip()[:96]))
                continue
            ncol = len(p)
            if rec["ncol"] is None:
                rec["ncol"] = ncol
            try:
                seq, raw, dec = int(p[0]), int(p[1]), int(p[2])
                i16, flt = int(p[3]), float(p[4])
                e19, l18 = (int(p[5], 16), int(p[6], 16)) if ncol == 7 else (0, 0)
            except ValueError:
                rec["bad"].append((ln, -1, L.rstrip()[:96]))
                continue
            if ncol != rec["ncol"]:                      # 记录内格式不一致 = 串口伪影
                rec["bad"].append((ln, ncol, "列数不一致 " + L.rstrip()[:88]))
                continue
            if not (0 < dec < 10 ** 9):          # 列③ 被截尾（M11 三查）
                rec["bad"].append((ln, len(p), "列③异常 " + L.rstrip()[:88]))
                continue
            rec["seq"].append(seq); rec["raw"].append(raw); rec["dec"].append(dec)
            rec["i16"].append(i16); rec["flt"].append(flt)
            rec["err19"].append(e19); rec["lod18"].append(l18)
    for k in ("seq", "raw", "dec", "i16", "flt", "err19", "lod18"):
        rec[k] = np.asarray(rec[k], float)
    rec["file"] = os.path.basename(fn)
    rec["badn"] = len(rec["bad"])
    rec["goodn"] = len(rec["seq"])
    return rec


# ── 时基（M16）与重采样 ─────────────────────────────────────────────────────
# ★T170-A（2026-10-08）：时基列自适应。
#   背景：当前 HEAD 固件的 ascii-1 第①列是**字面 0**（`ads1293.c:1403-1404`，
#   `idxraw` 被注释掉）⇒ 一切以列① 为时基的东西全废：`Δ列①` 恒 0（掉块检测瞎）、
#   重采样得 0 点 ⇒ `--mode metrics` 抛
#   `ValueError: Invalid number of FFT data points (0)`。
#   ⇒ 对策：**列① 有效才用列①；列① 恒 0 ⇒ 退化用列③（500 域序号）**。
#   🛑 旧数据（列① 有值）路径**逐字不变** —— 这是本补丁的回归底线。
def _tbcol(rec):
    """选时基列。返回 (列名, fs, 提示串|None)。

    列①（853 域 `idxraw`）优先；列① 无有效递增 ⇒ 退化列③（500 域 `idxfilter`）。
    """
    seq, dec = rec["seq"], rec["dec"]
    if len(seq) > 1 and (seq.max() - seq.min()) > 0 and np.any(np.diff(seq) > 0):
        return "seq", FS_RAW, None
    if len(dec) > 1 and (dec.max() - dec.min()) > 0 and np.any(np.diff(dec) > 0):
        return "dec", FS_OUT, ("⚠ 列① 恒 0（当前固件 ascii-1 无 853 序号）"
                               "⇒ 时基与缺口判据**自动改用列③（500 域序号）**")
    return "seq", FS_RAW, "⚠ 列①/列③ 都无有效序号 ⇒ 时基不可用"


def timebase(rec):
    """真实时间基：返回相对秒。列① 优先；**列① 恒 0 时自动退化列③**（★T170-A）。"""
    col, fs, _ = _tbcol(rec)
    return (rec[col] - rec[col][0]) / fs


def find_gaps(rec):
    """返回缺口列表 [(行号, Δ列, 缺口秒, 跳过的输出样本数)]。

    判据列随时间基自适应（★T170-A）：列① 有效用列①，否则用列③。
    """
    col, fs, _ = _tbcol(rec)
    d = np.diff(rec[col])
    out = []
    for i in np.where(d > 3)[0]:
        out.append((int(i) + 1, int(d[i]), float(d[i]) / fs,
                    int(round(d[i] / fs * FS_OUT)) - 1))
    return out


def resample(rec, col="raw", fs=FS_OUT):
    """按列① 时间基把列 col 重采样到均匀 fs 网格；缺口段为线性内插（须剔窗）。

    ⚠ 口径：列② raw 是偏移二进制 ⇒ 先减 CENTER 再 ×UV；
            列⑤ 滤波 code 已是去偏置浮点 ⇒ **只 ×UV，不减 CENTER**（踩过）。
    """
    t = timebase(rec)
    y = ((rec[col] - CENTER) if col in ("raw", "i16") else rec[col]) * UV
    n = int((t[-1] - t[0]) * fs)
    tu = t[0] + np.arange(n) / fs
    return tu, np.interp(tu, t, y)


def clean_windows(rec, tu, tw=2.0, guard=0.5, fs=FS_OUT):
    """避开缺口 ±guard 的整周期窗索引（N×2）。缺口来自列① 跳号。"""
    gt = []
    col, _fs_tb, _ = _tbcol(rec)
    d = np.diff(rec[col])
    for i in np.where(d > 3)[0]:
        gt.append(float(timebase(rec)[i]))
    n = int(round(tw * fs))
    wins = []
    for s in range(0, len(tu) - n + 1, n):
        a, b = tu[s], tu[s + n - 1]
        if any(a - guard <= x <= b + guard for x in gt):
            continue
        wins.append((s, s + n))
    if not wins and len(tu) >= n:
        wins.append((0, n))
    return wins


# ── 基础测量 ───────────────────────────────────────────────────────────────
def dft_amp(t, x, f0=F_SIG):
    """单频 DFT 幅度（µV）。要求 t 为真时间基、窗内无缺口。"""
    xc = x - x.mean()
    a = 2 * np.mean(xc * np.cos(2 * np.pi * f0 * t))
    b = 2 * np.mean(xc * np.sin(2 * np.pi * f0 * t))
    return float(math.hypot(a, b))


def band_rms(x, lo=BAND_LO, hi=BAND_HI, fs=FS_OUT):
    """带内宽带 rms（µV），M5/M12：比工频一律用这个，不用单频相干幅度。

    ⚠ 矩形窗口径：50Hz 主瓣旁瓣泄漏会灌进邻近子带（直连态 48~49Hz：
       矩形窗 54µV vs 加窗 7.5µV，差 7 倍）。它是 M 系列口径铁律的基准，
       **跨记录回归值靠它**，故不改。判"真实带内能量/裙边"请用 band_rms_hann()。
    """
    xc = x - x.mean()
    X = np.fft.rfft(xc)
    f = np.fft.rfftfreq(len(xc), 1 / fs)
    m = (f >= lo) & (f <= hi)
    return float(np.sqrt(2 * np.sum(np.abs(X[m]) ** 2) / len(xc) ** 2))


def band_rms_hann(x, lo=BAND_LO, hi=BAND_HI, fs=FS_OUT):
    """加 hanning 窗的带内 rms（µV）—— 判"裙边/旁带"用这个（T14, 2026-09-17）。

    旁瓣 −31 dB 且衰减快，主瓣泄漏对邻近子带的影响比矩形窗低 1~2 个量级。
    代价：窗内能量集中，故本函数**不可**用于跨记录回归（那些值由 band_rms 给出）。
    """
    import numpy as _np
    xc = _np.asarray(x, dtype=float)
    xc = xc - xc.mean()
    n = len(xc)
    w = _np.hanning(n)
    X = _np.fft.rfft(xc * w)
    f = _np.fft.rfftfreq(n, 1 / fs)
    a = _np.abs(X) * 2 / _np.sum(w)          # 每个 bin 的等效正弦峰值
    m = (f >= lo) & (f <= hi)
    return float(_np.sqrt(_np.sum(a[m] ** 2) / 2))


def pick_segments(rec, tu, tw=2.0, seg="auto", seglen=6.0, guard=0.5, fs=FS_OUT):
    """段选择。
    auto  = 全部「避开缺口 ±guard」的整周期窗（默认，统计最稳）
    fixed = 首个缺口 +guard 起 seglen 秒的**单段**（复现 2026-09-16 旧报告口径）
    """
    if seg == "fixed":
        col, _fs_tb, _ = _tbcol(rec)
        d = np.diff(rec[col])
        kk = np.where(d > 3)[0]
        t0 = tu[0] if not len(kk) else float(timebase(rec)[int(kk[0])])
        a = int((t0 - tu[0] + guard) * fs)
        n = int(seglen * fs)
        a = max(0, min(a, len(tu) - 100))
        return [(a, min(len(tu), a + n))]
    return clean_windows(rec, tu, tw=tw, guard=guard, fs=fs)


def metrics(rec, tw=2.0, seg="auto", seglen=6.0):
    """合格段口径的核心指标。"""
    tu, xu = resample(rec, "raw")
    tf, xf = resample(rec, "flt")
    segs = pick_segments(rec, tu, tw=tw, seg=seg, seglen=seglen)
    a4, r50, pp, f4, f50 = [], [], [], [], []
    for s, e in segs:
        e = min(e, len(xu), len(xf))
        if e - s < 100:
            continue
        a4.append(dft_amp(tu[s:e], xu[s:e]))
        r50.append(band_rms(xu[s:e]))
        pp.append(float(np.ptp(xu[s:e])))
        f4.append(dft_amp(tf[s:e], xf[s:e]))
        f50.append(band_rms(xf[s:e]))
    med = lambda v: float(np.median(v)) if v else float("nan")
    m = dict(
        file=rec["file"], rows=rec["rows"], good=rec["goodn"], bad=rec["badn"],
        tb_note=_tbcol(rec)[2],
        gaps=[dict(row=g[0], dseq=g[1], gap_ms_phys=g[2] * 1e3, drop_500hz=g[3]) for g in find_gaps(rec)],
        nseg=len(segs), tw=tw, seg=seg, seglen=seglen,
        raw_4hz=med(a4), raw_50rms=med(r50), raw_pp=med(pp),
        filt_4hz=med(f4), filt_50rms=med(f50),
    )
    cat = np.concatenate([xu[s:min(e, len(xu))] for s, e in segs]) if segs else xu
    r50w, rcore = band_rms(cat), band_rms(cat, CORE_LO, CORE_HI)
    m["purity"] = (rcore / max(r50w, 1e-12)) ** 2 if r50w > 100.0 else None   # 无工频⇒N/A
    m["notch_db"] = 20 * math.log10(max(m["raw_50rms"], 1e-12) / max(m["filt_50rms"], 1e-12))
    # 无信号记录（4Hz 幅度 < 100 µV）时，比值类指标无意义 ⇒ 置 None，避免被误读
    ok = m["raw_4hz"] > 100.0
    m["f50_over_4hz"] = m["filt_50rms"] / m["filt_4hz"] if (ok and m["filt_4hz"] > 1e-9) else None
    m["chain_gain_4hz"] = m["filt_4hz"] / m["raw_4hz"] if (ok and m["raw_4hz"] > 1e-9) else None
    return m


def show_metrics(m):
    print("-" * 92)
    if m.get("tb_note"):
        print("  " + m["tb_note"])
    print("[metrics] %s   段=%s ｜ %d 段 × %.1fs（避开缺口 ±0.5s）"
          % (m["file"], m["seg"], m["nseg"], m["tw"]))
    print("  raw   : 4Hz %8.1f µV ｜ 50Hz(45~55rms) %9.1f µV ｜ p-p %9.1f µV"
          % (m["raw_4hz"], m["raw_50rms"], m["raw_pp"]))
    print("  filter: 4Hz %8.1f µV ｜ 50Hz(45~55rms) %9.1f µV ｜ notch 抑制 %5.1f dB ｜ 链@4Hz ×%s"
          % (m["filt_4hz"], m["filt_50rms"], m["notch_db"],
             "N/A" if m["chain_gain_4hz"] is None else "%.4f" % m["chain_gain_4hz"]))
    p = "N/A（无工频）" if m["purity"] is None else "%.4f" % m["purity"]
    r = "N/A" if m["f50_over_4hz"] is None else "%.4f" % m["f50_over_4hz"]
    print("  谱纯度(49.5~50.5 ÷ 45~55) = %s ｜ filter50/4Hz = %s" % (p, r))
    if m["chain_gain_4hz"] is None:
        print("  ⚠ 本记录无 4Hz 信号（raw 4Hz < 100 µV）⇒ 链增益/比值类指标不适用")


# ── 审计 ───────────────────────────────────────────────────────────────────
def audit(rec):
    print("=" * 92)
    print("[audit] %s" % rec["file"])
    print("  总行 %d ｜ 好行 %d ｜ 坏行 %d" % (rec["rows"], rec["goodn"], rec["badn"]))
    for ln, nc, s in rec["bad"][:6]:
        print("    坏行 L%-6d (%s列) %r" % (ln, nc if nc >= 0 else "?", s))
    if rec["badn"] > 6:
        print("    … 其余 %d 条略" % (rec["badn"] - 6))
    if rec["goodn"] < 10:
        print("  ⚠ 有效样本太少，跳过差分审计"); return
    for nm, key in (("Δ列①", "seq"), ("Δ列③", "dec")):
        d = np.diff(rec[key])
        pos = d[d > 0]
        mode = int(np.bincount(pos.astype(int)).argmax()) if len(pos) else -1
        print("  %s: 中位 %d 众数 %d 最大 %d" % (nm, int(np.median(d)), mode, int(d.max())))
    col, tbfs, tbn = _tbcol(rec)
    lbl = "列①" if col == "seq" else "列③"
    if tbn:
        print("  " + tbn)
    g = find_gaps(rec)
    if not g:
        print("  ⇒ 未检出%s 跳号（无掉块）" % lbl)
    for r, dd, sec, skip in g[:8]:
        print("  ⚠ 掉块 @行%-6d Δ%s=%-4d ⇒ 物理缺口 %5.1f ms；输出流少 %d 个 500Hz 样本（%.1f ms）"
              % (r, lbl, dd, sec * 1e3, skip, skip / FS_OUT * 1e3))
    print("     ⇒ 时间基取自%s（%.4f Hz）：物理时长 = Δ%s÷%.4f；'缺 N 个样本'指 500Hz 输出流"
          % (lbl, tbfs, lbl, tbfs))
    t = timebase(rec)
    t_nom = (rec["goodn"] - 1) / FS_OUT
    print("  累计时基偏差（末尾）：名义 %.3f s ｜ %s %.3f s ｜ 差 %+.1f ms"
          % (t_nom, lbl, t[-1], (t[-1] - t_nom) * 1e3))
    if rec["ncol"] == 7:
        e = rec["err19"].astype(int); l = rec["lod18"].astype(int)
        orv = int(np.bitwise_or.reduce(e.astype(np.int64))) if len(e) else 0
        print("  诊断位：0x19 非零 %d 行 (or=0x%02x) ｜ 0x18 非零 %d 行"
              % (int(np.sum(e != 0)), orv, int(np.sum(l != 0))))
        # 0x19=ERROR_STATUS（ads1293.pdf p53 Fig.56）逐位命中行数
        #   ⚠ bit1 RLDRAIL / bit0 CMOR 都是「电平型」告警（RLD 输出近轨 / 共模直流电平超范围），
        #     对 12 mV 量级的 AC 摆幅不敏感 ⇒ 全零【不能】用来否定 AC 非线性，只能否定"直流工作点被搬走"。
        bits = (("bit7 SYNCEDGEERR", 7), ("bit6 CH3ERR", 6), ("bit5 CH2ERR", 5),
                ("bit4 CH1ERR", 4), ("bit3 LEADOFF", 3), ("bit2 BATLOW", 2),
                ("bit1 RLDRAIL", 1), ("bit0 CMOR", 0))
        hit = ["%s=%d" % (n, int(np.sum((e >> b) & 1))) for n, b in bits if int(np.sum((e >> b) & 1))]
        print("       0x19 位命中：" + ("；".join(hit) if hit else "8 个位全部 0 行（直流工作点未搬、RLD 未顶轨）"))
        print("       ⚠ 电平型告警 ⇒ 全零 ≠ 环健康、更不能否定 AC 非线性")
    else:
        print("  诊断位：无（5 列格式，无 ⑥0x19 / ⑦0x18 列）")



# ── M17 残差法 ─────────────────────────────────────────────────────────────
def _rms_fit(t, y, f0=F_SIG):
    A = np.column_stack([np.cos(2 * np.pi * f0 * t), np.sin(2 * np.pi * f0 * t), np.ones(len(t))])
    co, *_ = np.linalg.lstsq(A, y, rcond=None)
    return float(np.sqrt(np.mean((y - A @ co) ** 2))), float(math.hypot(co[0], co[1]))


def gap_verdict(rec, wins=(300, 500, 800, 1200, 2000, 3000)):
    """判时基：列⑤ 4Hz 残差小者为真时间基。禁用单点相位/过零。"""
    print("=" * 92)
    print("[gap] %s —— M17 残差法（列⑤ 4Hz 拟合）" % rec["file"])
    d = np.diff(rec["seq"])
    kk = np.where(d > 3)[0]
    if not len(kk):
        print("  无缺口，本模式不适用"); return
    k = int(kk[0]) + 1
    IDX, FLT = rec["seq"], rec["flt"]
    print("  缺口 k=行%d ｜ 全记录 %d 行" % (k, len(rec["seq"])))
    print("  %-8s %-13s %-13s %-6s %-7s %s" % ("窗长", "残差N(行号)", "残差G(列①)", "判定", "比值", "幅度N/G"))
    for W in wins:
        lo, hi = max(0, k - W // 2), min(len(IDX), k + W // 2)
        y = FLT[lo:hi] - FLT[lo:hi].mean()
        tN = np.arange(hi - lo) / FS_OUT
        tG = (IDX[lo:hi] - IDX[lo]) / FS_RAW
        rN, aN = _rms_fit(tN, y); rG, aG = _rms_fit(tG, y)
        print("  %-8d %-13.1f %-13.1f %-6s %-7.3f %.0f/%.0f"
              % (W, rN, rG, "N" if rN < rG else "G", min(rN, rG) / max(rN, rG), aN, aG))
    c = min(len(IDX) - 10, k + 3400)
    print("  对照（远离缺口 c=行%d，两时基应等价）：" % c)
    for W in (300, 1200, 3000):
        lo, hi = max(0, c - W // 2), min(len(IDX), c + W // 2)
        y = FLT[lo:hi] - FLT[lo:hi].mean()
        tN = np.arange(hi - lo) / FS_OUT
        tG = (IDX[lo:hi] - IDX[lo]) / FS_RAW
        rN, _ = _rms_fit(tN, y); rG, _ = _rms_fit(tG, y)
        print("     W=%-6d 残差 N %.1f ｜ G %.1f ｜ 比值 %.4f" % (W, rN, rG, min(rN, rG) / max(rN, rG)))
    print("  ⇒ 比值 ≪1 且跨窗长一致者胜出；配合 gap 扫描极小值确认缺口物理时长")


# ── 主流程 ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="ADS1293 ASCII 7 列统一分析入口")
    ap.add_argument("--file", action="append", required=True, help="数据文件（可多次指定）")
    ap.add_argument("--mode", default="all", choices=["audit", "metrics", "gap", "all"])
    ap.add_argument("--tw", type=float, default=2.0, help="指标窗长（秒，默认 2.0＝4Hz 整周期）")
    ap.add_argument("--seg", default="auto", choices=["auto", "fixed"],
                    help="auto=全部避缺口整周期窗（默认）｜fixed=首个缺口+0.5s 起单段（复现旧报告）")
    ap.add_argument("--seglen", type=float, default=6.0, help="fixed 段长度（秒，默认 6.0）")
    ap.add_argument("--gapwin", default="300,500,800,1200,2000,3000", help="gap 模式窗长列表")
    ap.add_argument("--json", default=None, help="把 metrics 结果写为 JSON")
    a = ap.parse_args()

    for fn in a.file:
        if not os.path.isfile(fn):
            print("⚠ 找不到文件：%s" % fn); continue
        rec = parse(fn)
        if a.mode in ("audit", "all"):
            audit(rec)
        if a.mode in ("gap",):
            gap_verdict(rec, tuple(int(x) for x in a.gapwin.split(",")))
        if a.mode in ("metrics", "all"):
            m = metrics(rec, tw=a.tw, seg=a.seg, seglen=a.seglen)
            show_metrics(m)
            if a.json:
                with open(a.json, "w", encoding="utf-8") as fh:
                    json.dump(m, fh, ensure_ascii=False, indent=2, default=str)
                print("  [json] -> %s" % a.json)

    if a.mode == "metrics" and len(a.file) > 1:
        print("=" * 104)
        print("对比表（段=%s，中位口径）" % a.seg)
        print("%-32s %8s %10s %10s %10s %8s %8s %10s" %
              ("文件", "4Hz", "50Hz_raw", "50Hz_flt", "抑制dB", "纯度", "链@4Hz", "flt50/4Hz"))
        for fn in a.file:
            if not os.path.isfile(fn):
                continue
            m = metrics(parse(fn), tw=a.tw, seg=a.seg, seglen=a.seglen)
            fmt = lambda v, f="%.4f": "N/A" if v is None else f % v
            print("%-32s %8.1f %10.1f %10.1f %10.1f %10s %10s %10s"
                  % (m["file"][:32], m["raw_4hz"], m["raw_50rms"], m["filt_50rms"],
                     m["notch_db"], fmt(m["purity"]), fmt(m["chain_gain_4hz"]),
                     fmt(m["f50_over_4hz"])))


if __name__ == "__main__":
    main()
