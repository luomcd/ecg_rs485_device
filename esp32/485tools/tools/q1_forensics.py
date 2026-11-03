#!/usr/bin/env python3
"""Q1 离线取证：ecg.bin 末段 25.7 s 为何零 R 峰？

用途
----
`test_logs/README.md` §五 Q1 行要求补三项证据：
    ① 该段原始波形形态 + 峰峰值
    ② 是否 LOD / 导联脱落
    ③ 录制日志

本脚本只做 ①② —— 直接读 T101 v2.2 原始帧，逐秒统计波形特征与 STA 位，
判定末段零峰的机理属于下列哪一种：

    A. 信号中断 / 导联脱落   → 段内 pp 塌到噪声级（几十 µV），或恒定值
    B. 基线大幅漂移          → raw_pp 很大，但去趋势后（>0.5 Hz）pp 很小
    C. 幅度整体缩小          → raw_pp 与 ac_pp 同比例缩小，形态仍在
    D. 心率真的掉到 0        → 波形正常但无 QRS（不可能，除非心脏停搏）

口径（与 mitbih_prep.py 同域）
------------------------------
    µV = int16 × 0.1526      （显示流 LSB，见 MEMORY「两个域不能混用」）
    t[s] = sample_index / 500

帧格式
------
    AA 55 | TYPE(1) | LEN(2,LE) | PAYLOAD(LEN) | CRC8(1)      总长 = 6 + LEN
    LEN=53 : SEQ(2,LE) + STA(1) + int16×25（单通道）
    LEN=103: SEQ(2,LE) + STA(1) + int16×25(ch1) + int16×25(ch2)
    LEN=52 : SEQ(2,LE) + int16×25（v1.1 旧帧，无 STA）
    ⚠ 严禁按固定 58 字节切帧，必须按 LEN 自适应（LEN=53 时每帧 59 B）。

用法
----
    python tools/q1_forensics.py                       # 默认取证 /sdcard/ecg.bin 的源文件
    python tools/q1_forensics.py --path X.bin --bin-sec 1
    python tools/q1_forensics.py --csv seg.csv         # 另存逐秒分段表
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------- 常量

FS = 500.0          # 帧内 25 样本 × 20 帧/s
UV_PER_LSB = 0.1526  # 显示流标定（µV/LSB），勿与 raw code 域的 0.1122 混用

SYNC0, SYNC1 = 0xAA, 0x55
LEN_V11, LEN_V22, LEN_DUAL = 52, 53, 103
LEN_SANITY_MAX = 200

STA_BIT = {
    "MODE": 1 << 0,
    "NOTCH": 1 << 1,
    "CHANNEL": 1 << 2,
    "RAW853": 1 << 3,
    "LOD": 1 << 4,
}

DEFAULT_PATH = (
    r"C:/work2026/VsPro/WinApp_ADSECG/ADS1293_Ecg/bin/Debug/net10.0-windows"
    r"/recordings/ecg_20260929_093408.bin"
)

# 已知 R 峰区间（README §五 Q1：首峰 2.12 s、末峰 64.13 s、跨度 62.01 s、63 峰）
KNOWN_FIRST_PEAK_S = 2.12
KNOWN_LAST_PEAK_S = 64.13
KNOWN_PEAKS = 63


# ---------------------------------------------------------------- CRC8

def crc8(buf: bytes) -> int:
    """多项式 0x07、初值 0x00、不反射（与 ecg_frame.c 一致）。"""
    crc = 0
    for b in buf:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


# ---------------------------------------------------------------- 解析

def parse_frames(raw: bytes) -> dict:
    """顺序扫全文件，返回帧列表与统计。"""
    frames = []          # [(seq, sta_or_None, ch1_list, ch2_list)]
    stats = Counter()
    resync_total = 0
    i, n = 0, len(raw)

    while i + 5 <= n:
        if raw[i] != SYNC0 or raw[i + 1] != SYNC1:
            # 重同步：向后找下一个 AA 55
            j = i + 1
            while j + 1 < n and not (raw[j] == SYNC0 and raw[j + 1] == SYNC1):
                j += 1
            resync_total += j - i
            stats["resync"] += 1
            i = j
            continue

        typ = raw[i + 2]
        ln = raw[i + 3] | (raw[i + 4] << 8)

        if ln > LEN_SANITY_MAX or i + 6 + ln > n:
            stats["bad_len"] += 1
            i += 1
            continue

        payload = raw[i + 5:i + 5 + ln]
        crc_recv = raw[i + 5 + ln]
        crc_calc = crc8(raw[i + 2:i + 5 + ln])   # TYPE + LEN + PAYLOAD
        stats["frames"] += 1
        if crc_calc != crc_recv:
            stats["crc_err"] += 1
            stats["crc_err_val"] += 0
        stats[f"len_{ln}"] += 1

        # N = (LEN-3)/2（奇数，含 STA）或 (LEN-2)/2（偶数，无 STA）
        if ln % 2 == 1:
            n_samp = (ln - 3) // 2
            seq = payload[0] | (payload[1] << 8)
            sta = payload[2]
            off = 3
        else:
            n_samp = (ln - 2) // 2
            seq = payload[0] | (payload[1] << 8)
            sta = None
            off = 2

        def le16(base: int, cnt: int) -> list[int]:
            out = []
            for k in range(cnt):
                p = base + 2 * k
                v = payload[p] | (payload[p + 1] << 8)
                out.append(v - 65536 if v >= 32768 else v)
            return out

        ch1 = le16(off, n_samp)
        ch2 = le16(off + 2 * n_samp, n_samp) if ln == LEN_DUAL else []
        frames.append((seq, sta, ch1, ch2))
        i += 6 + ln

    stats["tail_bytes"] = n - i
    stats["resync_bytes"] = resync_total
    return {"frames": frames, "stats": stats, "file_size": n}


def seq_check(frames: list) -> dict:
    """SEQ 连续性 —— 丢帧指示。"""
    seqs = [f[0] for f in frames]
    if not seqs:
        return {"gaps": 0, "gap_list": [], "first": None, "last": None}
    gaps = 0
    gap_list = []
    for a, b in zip(seqs, seqs[1:]):
        d = (b - a) & 0xFFFF
        if d != 1:
            gaps += 1
            if len(gap_list) < 20:
                gap_list.append((a, b, d))
    return {"gaps": gaps, "gap_list": gap_list, "first": seqs[0], "last": seqs[-1]}


# ---------------------------------------------------------------- 去趋势

def highpass(x: np.ndarray, fc: float = 0.5) -> np.ndarray:
    """一阶高通，用于剥掉基线漂移后看「逐拍幅度」。"""
    a = 1.0 / (1.0 + 2.0 * np.pi * fc / FS)
    y = np.empty_like(x)
    prev_x = x[0]
    prev_y = 0.0
    for k in range(x.size):
        prev_y = a * (prev_y + x[k] - prev_x)
        prev_x = x[k]
        y[k] = prev_y
    return y


def local_maxima(x: np.ndarray, min_h: float, guard: int) -> list[tuple[int, float]]:
    """找幅值 > min_h 的局部极大，且相互间隔至少 guard 样本。

    只用于「确认末峰是不是真峰」，不参与任何评分 —— 不是检测器。
    """
    cand = []
    for k in range(1, x.size - 1):
        if x[k] > min_h and x[k] >= x[k - 1] and x[k] > x[k + 1]:
            cand.append((k, float(x[k])))
    out: list[tuple[int, float]] = []
    for k, v in cand:
        if out and k - out[-1][0] < guard:
            if v > out[-1][1]:
                out[-1] = (k, v)
        else:
            out.append((k, v))
    return out


# ---------------------------------------------------------------- 分段统计

def seg_table(x_uv: np.ndarray, bin_sec: float, sta_per_sample: list) -> list[dict]:
    n_per = int(round(bin_sec * FS))
    rows = []
    for s in range(0, x_uv.size, n_per):
        seg = x_uv[s:s + n_per]
        stas = sta_per_sample[s:s + n_per]
        if seg.size == 0:
            continue
        ac = highpass(seg)
        rows.append({
            "t0": s / FS,
            "t1": (s + seg.size) / FS,
            "n": seg.size,
            "mean": float(seg.mean()),
            "std": float(seg.std()),
            "min": float(seg.min()),
            "max": float(seg.max()),
            "pp": float(seg.max() - seg.min()),
            "ac_pp": float(ac.max() - ac.min()),
            "ac_rms": float(np.sqrt((ac ** 2).mean())),
            "sta_set": sorted({v for v in stas if v is not None}),
            "lod_frac": float(np.mean([1 if (v is not None and v & STA_BIT["LOD"]) else 0
                                       for v in stas])),
        })
    return rows


def locate_break(x: np.ndarray, win: int = 50, thr: float = 200.0) -> dict | None:
    """用短窗峰峰值扫描定位「波形消失」的断点。

    返回最后一个 pp > thr 的窗，以及其后各窗的最大 pp —— 后者是判定
    「信号塌到噪声级」的直接证据。win=50 ⇒ 100 ms @500 Hz。
    """
    n_win = x.size // win
    pp = np.empty(n_win)
    for k in range(n_win):
        seg = x[k * win:(k + 1) * win]
        pp[k] = seg.max() - seg.min()
    big = np.where(pp > thr)[0]
    if big.size == 0:
        return None

    k0 = int(big[-1])
    after = pp[k0 + 1:]
    around = []
    for k in range(max(0, k0 - 8), min(n_win, k0 + 12)):
        seg = x[k * win:(k + 1) * win]
        around.append({
            "t": k * win / FS,
            "pp": float(pp[k]),
            "mean": float(seg.mean()),
            "min": float(seg.min()),
            "max": float(seg.max()),
        })

    return {
        "t_last_big": k0 * win / FS,
        "t_after": (k0 + 1) * win / FS,
        "pp_last": float(pp[k0]),
        "pp_after_max": float(after.max()) if after.size else 0.0,
        "around": around,
    }


def fit_tau(seg: np.ndarray) -> dict | None:
    """对「断点后的直流偏移」拟合一阶指数回落：y = a0·exp(-t/τ) + c。

    这是区分「输入源断开（直流阶跃 + 高通放电）」与「生物电信号」的关键判据：
    前者是单指数、R² 接近 1；后者不会单调回落。
    """
    n_per = int(FS)
    m = seg.size // n_per
    if m < 5:
        return None
    tm = np.array([(k + 0.5) * n_per / FS for k in range(m)])
    ym = np.array([seg[k * n_per:(k + 1) * n_per].mean() for k in range(m)])

    c = float(ym[-3:].mean())          # 稳态偏置
    d = ym - c
    # 只取符号一致且幅值足够大的点做 log 线性回归
    sign = 1.0 if d[0] >= 0 else -1.0
    mask = (d * sign > 0.5)
    if mask.sum() < 4:
        return None
    tt, ll = tm[mask], np.log(np.abs(d[mask]))
    slope, intercept = np.polyfit(tt, ll, 1)
    if slope >= 0:
        return None
    a0 = float(np.exp(intercept)) * sign
    tau = float(-1.0 / slope)
    pred = a0 * np.exp(-tm / tau) + c
    ss_res = float(((ym - pred) ** 2).sum())
    ss_tot = float(((ym - ym.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"a0": a0, "tau": tau, "c": c, "r2": r2}


def sta_transitions(sta_per_sample: list) -> list[tuple]:
    """STA 变化点（时间 + 前后值）。"""
    out = []
    prev = None
    for k, v in enumerate(sta_per_sample):
        if v is None:
            continue
        if v != prev:
            out.append((k / FS, prev, v))
            prev = v
    return out


def sta_str(v) -> str:
    if v is None:
        return "None"
    on = [k for k, b in STA_BIT.items() if v & b]
    return f"0x{v:02X}(" + ("|".join(on) if on else "0") + ")"


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="Q1 末段零峰离线取证")
    ap.add_argument("--path", default=DEFAULT_PATH, help="T101 v2.2 原始录制文件")
    ap.add_argument("--bin-sec", type=float, default=1.0, help="分段秒数（默认 1 s）")
    ap.add_argument("--csv", default=None, help="另存逐段表 CSV")
    ap.add_argument("--tail-from", type=float, default=KNOWN_LAST_PEAK_S,
                    help="末段起点（默认 64.13 s）")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.is_file():
        print(f"[FAIL] 文件不存在：{p}", file=sys.stderr)
        return 2

    raw = p.read_bytes()
    print("=" * 78)
    print("Q1 取证 · ecg.bin 末段零峰")
    print("=" * 78)
    print(f"文件      : {p}")
    print(f"大小      : {len(raw):,} B")

    res = parse_frames(raw)
    frames, st = res["frames"], res["stats"]
    fr_total = st["frames"]
    per_frame = 25
    n_samp = fr_total * per_frame
    dur = n_samp / FS

    print(f"帧数      : {fr_total}  "
          f"(len53={st['len_53']} len52={st['len_52']} len103={st['len_103']})")
    print(f"CRC 错误  : {st['crc_err']}   重同步次数: {st['resync']} "
          f"(多前进 {st['resync_bytes']} B)   非法长度: {st['bad_len']}")
    print(f"尾部残余  : {st['tail_bytes']} B")
    print(f"样本数    : {n_samp:,}  @{FS:.0f} Hz ⇒ 时长 {dur:.2f} s")

    sq = seq_check(frames)
    print(f"SEQ       : {sq['first']} → {sq['last']}，非+1 次数 {sq['gaps']}"
          + (f"  样例 {sq['gap_list'][:6]}" if sq["gaps"] else ""))

    # ---- 拼成整条波形
    x = np.concatenate([np.asarray(f[2], dtype=np.float64) for f in frames]) * UV_PER_LSB
    sta_ps = []
    for f in frames:
        sta_ps.extend([f[1]] * len(f[2]))
    # 双通道文件补 ch2
    has_ch2 = any(f[3] for f in frames)
    if has_ch2:
        x2 = np.concatenate([np.asarray(f[3], dtype=np.float64) for f in frames]) * UV_PER_LSB
    else:
        x2 = None

    print()
    print("--- STA 字节分布 ---")
    cnt = Counter([f[1] for f in frames])
    for v, c in sorted(cnt.items(), key=lambda kv: (kv[0] is None, kv[0])):
        print(f"  {sta_str(v):<28} {c:>5} 帧  ({c / fr_total * 100:5.1f} %)")

    print()
    print("--- STA 变化点 ---")
    tr = sta_transitions(sta_ps)
    if len(tr) <= 1:
        print("  全程恒定，无翻转")
    for t, a, b in tr[:40]:
        print(f"  t={t:8.3f} s   {sta_str(a):<28} → {sta_str(b)}")
    if len(tr) > 40:
        print(f"  ... 共 {len(tr)} 个变化点")

    # ---- 逐段统计
    rows = seg_table(x, args.bin_sec, sta_ps)
    print()
    print(f"--- 逐 {args.bin_sec:g} s 分段（µV；ac_pp 为 0.5 Hz 高通后峰峰值）---")
    print(f"{'t0':>8} {'t1':>8} {'mean':>9} {'std':>8} {'min':>9} {'max':>9} "
          f"{'pp':>8} {'ac_pp':>8} {'ac_rms':>7} {'LOD%':>6}  STA")
    for r in rows:
        print(f"{r['t0']:8.1f} {r['t1']:8.1f} {r['mean']:9.1f} {r['std']:8.1f} "
              f"{r['min']:9.1f} {r['max']:9.1f} {r['pp']:8.1f} {r['ac_pp']:8.1f} "
              f"{r['ac_rms']:7.1f} {r['lod_frac'] * 100:6.1f}  "
              + ",".join(sta_str(v) for v in r["sta_set"]))

    # ---- 自动定位断点
    # 🛑 不能用「预设的 tail_from」直接切区间做聚合统计：末峰 64.13 s 那一点
    #    残留波形会把整段 ac_pp 拉高（实测 869 µV），从而把「信号中断」误判成
    #    「幅度正常」。必须先用短窗扫描找断点，再按断点切段。
    print()
    print("--- 断点定位（100 ms 窗峰峰值扫描，阈值 200 µV）---")
    brk = locate_break(x, win=50, thr=200.0)
    # 细窗（100 ms）定位断点形态；粗窗（1 s）定位「信号有效终点」——
    # 细窗会被最后一个心搏的 T 波与基线跳变拖住 0.3 s，不能直接用于分段统计。
    brk_c = locate_break(x, win=int(FS), thr=100.0)
    if brk is None:
        print("  全程波形连续，未发现断点")
        t_break = dur
    else:
        t_break_fine = brk["t_after"]
        t_break = brk_c["t_after"] if brk_c else brk["t_after"]
        print(f"  最后一个 pp>200 µV 的 100 ms 窗起点 t={brk['t_last_big']:.2f} s "
              f"（该窗 pp={brk['pp_last']:.0f} µV）")
        print(f"  细窗断点（该窗结束）  t={t_break_fine:.3f} s  ⇒ 样本 "
              f"{int(round(t_break_fine * FS)):,}")
        if brk_c:
            print(f"  粗窗信号有效终点      t={t_break:.3f} s  "
                  f"（最后 1 s 窗 ac_pp>100 µV；= 末峰 64.13 s + 该搏 T 波 + 基线跳变）")
        print(f"  其后所有窗最大 pp     {brk['pp_after_max']:.1f} µV "
              f"⇒ {'已塌到噪声级' if brk['pp_after_max'] < 100 else '仍有明显波动'}")
        print()
        print("  断点前后逐 100 ms：")
        for r in brk["around"]:
            print(f"    t={r['t']:7.2f}s  pp={r['pp']:7.1f}  mean={r['mean']:+8.1f}  "
                  f"min={r['min']:+7.1f}  max={r['max']:+7.1f}"
                  + ("  <== 断点窗" if r["t"] == brk["t_last_big"] else ""))

    # ---- 区间汇总（活跃段 vs 断点后段）
    print()
    print("--- 关键区间对照 ---")

    def zone(name: str, t0: float, t1: float, sig: np.ndarray):
        a, b = int(t0 * FS), min(int(t1 * FS), sig.size)
        if b <= a:
            print(f"  {name}: 无样本")
            return None
        seg = sig[a:b]
        ac = highpass(seg)
        d = {
            "zone": name, "t0": t0, "t1": t1, "n": seg.size,
            "mean": float(seg.mean()), "std": float(seg.std()),
            "pp": float(seg.max() - seg.min()),
            "ac_pp": float(ac.max() - ac.min()),
            "ac_rms": float(np.sqrt((ac ** 2).mean())),
            "p1": float(np.percentile(seg, 1)), "p99": float(np.percentile(seg, 99)),
        }
        print(f"  {name:<24} n={d['n']:>7,}  mean={d['mean']:+9.1f}  std={d['std']:8.1f}  "
              f"pp={d['pp']:8.1f}  ac_pp={d['ac_pp']:8.1f}  ac_rms={d['ac_rms']:7.1f}  "
              f"p1..p99=[{d['p1']:+.1f},{d['p99']:+.1f}]")
        return d

    z_active = zone(f"活跃段 0~{t_break:.2f} s", 0.0, t_break, x)
    z_tail = zone(f"断点后 {t_break:.2f}~{dur:.2f} s", t_break, dur, x)
    if z_active and z_tail:
        print(f"  → 断点后/活跃段 幅值比：raw pp "
              f"{z_tail['pp'] / max(z_active['pp'], 1e-9):.3f}×   "
              f"ac_pp {z_tail['ac_pp'] / max(z_active['ac_pp'], 1e-9):.3f}×   "
              f"ac_rms {z_tail['ac_rms'] / max(z_active['ac_rms'], 1e-9):.3f}×")

    if x2 is not None:
        print()
        print("--- 通道 2 同口径 ---")
        zone("ch2 活跃段", 0.0, t_break, x2)
        zone("ch2 断点后", t_break, dur, x2)

    # ---- 断点后直流偏移的指数回落（判定「输入源断开」的关键证据）
    print()
    print("--- 断点后直流偏移回落（一阶高通放电？）---")
    tail_after = x[int(t_break * FS):]
    if tail_after.size > 2 * int(FS):
        tau_info = fit_tau(tail_after)
        if tau_info:
            print(f"  阶跃幅度 ≈ {tau_info['a0']:+.1f} µV，时间常数 τ ≈ "
                  f"{tau_info['tau']:.2f} s（等价截止 {1 / (2 * np.pi * tau_info['tau']):.4f} Hz）")
            print(f"  拟合残差 R² = {tau_info['r2']:.4f}"
                  "   （R²>0.95 ⇒ 单指数放电，即「直流阶跃被高通吃掉」而非生物电信号）")
        else:
            print("  无法拟合指数（段内没有单调回落）")

    # ---- 末段细看：是否恒定 / 是否只有工频 / 是否存在慢漂
    print()
    print("--- 末段细粒度诊断（每 5 s）---")
    tail_x = x[int(args.tail_from * FS):]
    n5 = int(5 * FS)
    print(f"{'t0':>8} {'mean':>9} {'dc_drift':>9} {'ac_pp':>8} {'ac_rms':>7} {'uniq':>6}")
    for s in range(0, tail_x.size, n5):
        seg = tail_x[s:s + n5]
        if seg.size < 10:
            continue
        ac = highpass(seg)
        t0 = args.tail_from + s / FS
        # dc_drift = 段内均值的线性趋势（最小二乘斜率 × 时长，µV）
        tt = np.arange(seg.size) / FS
        slope = np.polyfit(tt, seg, 1)[0]
        print(f"{t0:8.1f} {seg.mean():9.1f} {slope * seg.size / FS:9.1f} "
              f"{ac.max() - ac.min():8.1f} {np.sqrt((ac ** 2).mean()):7.1f} "
              f"{np.unique(np.round(seg, 1)).size:6d}")

    # ---- 断点前最后几个大峰（确认「末峰 64.13 s」是真峰还是断线伪差）
    print()
    print("--- 断点前最后 4 个 >400 µV 的局部极大（确认末峰性质）---")
    peaks = local_maxima(x, min_h=400.0, guard=int(0.2 * FS))
    for idx, val in peaks[-4:]:
        print(f"  t={idx / FS:8.3f} s  n={idx:>6}  峰值 {val:+8.1f} µV")

    # ---- 判决
    print()
    print("--- 判决 ---")
    verdict = "无法判定"
    if z_active and z_tail:
        ratio_ac = z_tail["ac_pp"] / max(z_active["ac_pp"], 1e-9)
        lod_any = any((v is not None and v & STA_BIT["LOD"]) for v in sta_ps)
        raw853_any = any((v is not None and v & STA_BIT["RAW853"]) for v in sta_ps)

        # 断点后段是否塌到噪声级：ac_rms < 10 µV 是「几乎无信号」的硬线
        noise_floor = z_tail["ac_rms"] < 10.0 and z_tail["ac_pp"] < 100.0
        if noise_floor and ratio_ac < 0.15:
            verdict = ("A · 信号中断 / 输入源断开"
                       "（断点后 ac_rms 塌到本底噪声级）")
        elif ratio_ac < 0.15 and not noise_floor:
            verdict = "B · 基线大幅漂移（aс 幅值大而 ac 幅值小）"
        elif ratio_ac < 0.5:
            verdict = "C · 幅度整体缩小（形态仍在，幅度不足）"
        else:
            verdict = "D · 波形幅度正常 ⇒ 零峰不是幅度问题（需查检测器/采样断流）"

        print(f"  判定：{verdict}")
        print(f"    - 断点后 ac_rms = {z_tail['ac_rms']:.2f} µV、ac_pp = "
              f"{z_tail['ac_pp']:.1f} µV（活跃段 {z_active['ac_rms']:.1f} / "
              f"{z_active['ac_pp']:.1f}），幅值比 {ratio_ac:.3f}×")
        print(f"    - STA 全程 LOD 位={'出现过' if lod_any else '从未置位'}"
              f"，RAW853 位={'出现过' if raw853_any else '从未置位'}")
        if not lod_any:
            print("      ⚠ LOD 未置位 **不能**当作「未脱落」的证据 —— "
                  "本配置下 LOD 检测很可能未使能（STA 恒为 0x02，只有 NOTCH）")
        if peaks:
            idx, val = peaks[-1]
            print(f"    - 末峰 t={idx / FS:.3f} s 幅值 {val:+.1f} µV，"
                  f"与前 3 峰幅度差 "
                  f"{abs(val - np.mean([v for _, v in peaks[-4:-1]])) / max(abs(val), 1e-9) * 100:.1f} %")

        tl = x[int(t_break * FS):]
        if tl.size:
            vals, cnts = np.unique(np.round(tl, 1), return_counts=True)
            print(f"    - 断点后最常见取值 {vals[cnts.argmax()]:+.1f} µV 仅占 "
                  f"{cnts.max() / tl.size * 100:.1f} %"
                  "（占比低 ⇒ 不是恒定饱和平台，是随机噪声）")

    # ---- CSV
    if args.csv:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            # 纯 LF：csv.writer 默认是 \r\n，本工程统一 LF（见 user MEMORY 的换行纪律）
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n")
            w.writeheader()
            for r in rows:
                r2 = dict(r)
                r2["sta_set"] = "|".join(sta_str(v) for v in r["sta_set"])
                w.writerow(r2)
        print(f"\n分段表已写 {args.csv}")

    print()
    print(f"对照 README §五 Q1 已知：首峰 {KNOWN_FIRST_PEAK_S} s / 末峰 "
          f"{KNOWN_LAST_PEAK_S} s / {KNOWN_PEAKS} 峰 ⇒ 末段零峰 "
          f"{dur - KNOWN_LAST_PEAK_S:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
