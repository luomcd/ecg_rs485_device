#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""replay_rpeak.py —— 在 PC 上逐位重放 ESP32 的 R 峰检测器

用途
----
评审（docs/T223）指出：现有宽格式只记 ±3 个样本，无法证明「滤波域选错了哪个极值」，
也无法离线比较候选修法。本脚本把 `ecg_rpeak.c` 的算法完整搬到 PC，
喂 485 直抓的**连续样本流**，输出每个峰的完整决策过程：

    trigger_idx   积分峰回退 30 样本得到的粗触发坐标
    loc_idx       locate_rpeak() 最终选中的位置
    loc_val       该位置的滤波值
    peak_val      窗口内 |滤波值| 的最大值
    at_top        落点是否 == 窗口最大点位置
    thr / spacing / is_twave / src(0=主路径 1=回溯)

一致性要求
----------
必须与固件逐位一致，否则结论无效。校验办法见 `--selfcheck`：
同一批数据下，本脚本算出的峰应与固件记录的峰号高度重合。

数据来源
--------
`cap485_quality.py --raw` 抓的 485 原始帧流（含 AA55 帧头），或纯 int16 样本文件。

用法
----
    py replay_rpeak.py --bin _raw_60bpm.bin --bpm 60
    py replay_rpeak.py --bin _raw_60bpm.bin --bpm 60 --dump-csv peaks.csv
    py replay_rpeak.py --bin _raw_60bpm.bin --bpm 60 --trace 120-140   # 只打印该峰号段的决策
"""
import argparse
import csv
import struct
import sys

import numpy as np

F32 = np.float32

# ---- 与 ecg_rpeak.h 完全一致的常量 ----
REFRACTORY = 80
MIN_RR = 80
INTEG_WIN = 60
LOWPASS_A = F32(0.25)
HIGHPASS_A = F32(0.05)
BASELINE_A = F32(0.002)
FILT_HIST = int(__import__("os").environ.get("RP_FILT_HIST", "140"))  # 固件=140；可环境变量覆盖做离线实验
# 🛑 回溯路径的落点策略（评审 Q2「trigger / fiducial 双坐标」的两种候选做法）：
#    0 = 固件现状：回溯时**事后**调 locate_rpeak(trigger)。trigger 常落后于当前样本
#        1.66×RR，±40 窗口早已滑出 filt_hist ⇒ found=false ⇒ 直接返回 trigger（不精修）
#    1 = 候选池里**当场**存下精修后的落点 fid，回溯只取 fid。候选入池那一刻的历史覆盖
#        与主路径完全一致（都是 trigger-40 ~ trigger+31）⇒ 主路径行为零改动。
FID_MODE = int(__import__("os").environ.get("RP_FID_MODE", "0"))
CAND_MAX = 64
PEAK_MAX = 32
AVG_RR_TAKE = 5
LOCATE_HALF = 40
# 🛑 落点搜索窗在**精修那一刻**最多能扫到多少样本：左半 40 + [trigger, s] 32 个 = 72。
#    窗尾 trigger+40 落在未来（s = trigger+31）⇒ 81 永远扫不满，别拿 81 当基准。
REF_WIN = LOCATE_HALF + 1 + (INTEG_WIN // 2 + 1)
TINY = F32(1e-12)
NO_PEAK = -1


def load_frames(path):
    """解析 485 帧流 → (样本数组, 连续段列表[(起始样本序号, 长度)])

    🛑 只取 SEQ 连续段：丢帧处样本会断，重放靠连续段才能让积分窗/候选池正确收敛。
    """
    d = open(path, "rb").read()
    frames = []
    i = 0
    bad = 0
    while i + 8 <= len(d):
        if d[i:i + 2] != b"\xaa\x55":
            i += 1
            continue
        L = d[i + 3] | (d[i + 4] << 8)
        if L not in (52, 53, 103):
            i += 1
            continue
        n = (L - 3) // 2
        total = 8 + 2 * n + 1
        if i + total > len(d):
            i += 1
            continue
        body = d[i + 2:i + 2 + 6 + 2 * n]
        crc = d[i + 2 + 6 + 2 * n]
        c = 0
        for x in body:
            c ^= x
            for _ in range(8):
                c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        if c != crc:
            bad += 1
            i += total
            continue
        seq = d[i + 5] | (d[i + 6] << 8)
        smp = struct.unpack_from("<%dh" % n, d, i + 8)
        frames.append((seq, smp[:25]))      # 只取 CH1 前 25 个
        i += total

    # 切连续段（SEQ +1）
    segs = []
    cur = []
    for k, (seq, smp) in enumerate(frames):
        if cur and ((seq - frames[k - 1][0]) & 0xFFFF) != 1:
            segs.append(cur)
            cur = []
        cur.append(smp)
    if cur:
        segs.append(cur)

    out = []
    idx = []
    for sg in segs:
        if len(sg) < 8:                     # 太短的段不够初始收敛
            continue
        out.append(np.concatenate([np.array(s, dtype=np.float32) for s in sg]))
        idx.append(len(sg))
    if not out:
        return np.zeros(0, np.float32), []
    samples = np.concatenate(out)
    return samples, [len(s) for s in out], bad


class RPeak:
    """与 ecg_rpeak.c 的状态机逐字段对应"""

    def __init__(self):
        self.baseline = F32(0)
        self.low = F32(0)
        self.high = F32(0)
        self.v_p1 = F32(0)
        self.v_p2 = F32(0)
        self.v_p3 = F32(0)
        self.deriv_p1 = F32(0)
        self.integ_p1 = F32(0)
        self.integ_p2 = F32(0)
        self.integ_win = np.zeros(INTEG_WIN, np.float32)
        self.integ_pos = 0
        self.integ_n = 0
        self.integ_sum = F32(0)
        self.filt_hist_v = np.zeros(FILT_HIST, np.float32)
        self.filt_hist_i = np.zeros(FILT_HIST, np.int64)
        self.filt_pos = 0
        self.filt_n = 0
        self.noise_peak = F32(0)
        self.signal_peak = F32(0)
        self.last_peak = NO_PEAK
        self.last_slope = F32(0)
        self.sample_index = 0
        self.cand = []            # [(index, value, slope)]
        self.peaks = []           # 最近 PEAK_MAX 个已接受峰
        self.peak_total = 0
        self.max_lag = 0          # 回溯触发时 trigger 距当前样本的最大滞后（样本数）

    def _locate(self, integrated_peak):
        """locate_rpeak：±40 窗口内取 |滤波值| 最大（与固件同为 fabsf 比较）

        返回 (best_i, best_v, best_abs, nwin)。nwin = 窗口内**真正扫到**的样本数。
        🛑 全窗应有 2*LOCATE_HALF+1 = 81 个。nwin < 81 就是**部分覆盖**：
           此时 `found` 仍为 true、`loc != trigger`（看起来"精修过了"），
           但右半边（或左半边）根本没数据 ⇒ 只能在不完整窗里挑极值 ⇒ **静默选错**。
           这比 found=false 更隐蔽，只有 nwin 能抓到。
        """
        start = integrated_peak - LOCATE_HALF
        end = integrated_peak + LOCATE_HALF
        first = (self.filt_pos - self.filt_n + FILT_HIST * 2) % FILT_HIST
        best_v = None
        best_i = integrated_peak
        best_abs = F32(-1.0)
        nwin = 0
        for k in range(self.filt_n):
            j = (first + k) % FILT_HIST
            ii = self.filt_hist_i[j]
            if ii < start or ii > end:
                continue
            nwin += 1
            a = F32(abs(self.filt_hist_v[j]))
            if best_v is None or a > best_abs:
                best_v = self.filt_hist_v[j]
                best_i = ii
                best_abs = a
        return best_i, (best_v if best_v is not None else F32(0)), best_abs, nwin

    def _accept(self, peak, value, slope, diag):
        self.signal_peak = F32(0.125) * value + F32(0.875) * self.signal_peak
        self.last_peak = peak
        self.last_slope = slope
        self.peak_total += 1
        self.peaks.append(peak)
        if len(self.peaks) > PEAK_MAX:
            self.peaks.pop(0)
        diag["accepted"] = True

    def process(self, x):
        x = F32(x)
        self.baseline += (x - self.baseline) * BASELINE_A
        centered = x - self.baseline
        self.low += (centered - self.low) * LOWPASS_A
        self.high += (self.low - self.high) * HIGHPASS_A
        value = self.low - self.high

        derivative = (F32(2.0) * value + self.v_p1 - self.v_p2 - F32(2.0) * self.v_p3) * F32(0.125)
        squared = derivative * derivative

        if self.integ_n < INTEG_WIN:
            self.integ_win[self.integ_pos] = squared
            self.integ_pos = (self.integ_pos + 1) % INTEG_WIN
            self.integ_n += 1
            self.integ_sum += squared
        else:
            self.integ_sum -= self.integ_win[self.integ_pos]
            self.integ_win[self.integ_pos] = squared
            self.integ_pos = (self.integ_pos + 1) % INTEG_WIN
            self.integ_sum += squared
        integrated = self.integ_sum / F32(self.integ_n)

        self.filt_hist_i[self.filt_pos] = self.sample_index
        self.filt_hist_v[self.filt_pos] = value
        self.filt_pos = (self.filt_pos + 1) % FILT_HIST
        if self.filt_n < FILT_HIST:
            self.filt_n += 1

        out = []
        prev_integ = self.integ_p1
        prevprev_integ = self.integ_p2
        thr = max(TINY, self.noise_peak + F32(0.25) * (self.signal_peak - self.noise_peak))

        if prev_integ >= prevprev_integ and prev_integ > integrated:
            peak = self.sample_index - 1 - INTEG_WIN // 2
            slope = F32(abs(self.deriv_p1))
            sec_thr = self.noise_peak + F32(0.5) * (self.signal_peak - self.noise_peak)

            if prev_integ > max(TINY, sec_thr):
                # 候选入池：FID_MODE=1 时**在此刻**就把落点精修好（此刻窗口覆盖最全）
                fid_i, fid_v, fid_w = peak, F32(0), 0
                if FID_MODE:
                    fid_i, fid_v, _, fid_w = self._locate(peak)
                self.cand.append((peak, prev_integ, slope, fid_i, fid_v, fid_w))
                if len(self.cand) > CAND_MAX:
                    self.cand.pop(0)

            if prev_integ > thr:
                spacing = (10 ** 18) if self.last_peak == NO_PEAK else (peak - self.last_peak)
                is_tw = (spacing < 360) and (self.last_slope > 0) and \
                        (slope < self.last_slope * F32(0.5))
                diag = dict(trigger=peak, thr=float(thr), spacing=spacing,
                            is_twave=bool(is_tw), src=0, accepted=False,
                            prev_integ=float(prev_integ))
                if (not is_tw) and spacing >= REFRACTORY:
                    li, lv, labs, nwin = self._locate(peak)
                    diag.update(loc=li, loc_val=float(lv), loc_abs=float(labs), win=nwin)
                    self._accept(li, prev_integ, slope, diag)
                    out.append((li, diag))
                else:
                    self.noise_peak = F32(0.125) * prev_integ + F32(0.875) * self.noise_peak
                    diag["reject"] = "twave" if is_tw else "refractory"
                    out.append((None, diag))
            else:
                self.noise_peak = F32(0.125) * prev_integ + F32(0.875) * self.noise_peak

        # ---- 回溯 ----
        if self.last_peak != NO_PEAK and len(self.peaks) >= 2:
            ssum = F32(0)
            m = 0
            k = len(self.peaks) - 1
            while k >= 1 and m < AVG_RR_TAKE:
                ssum += F32(self.peaks[k] - self.peaks[k - 1])
                m += 1
                k -= 1
            avg_rr = ssum / F32(m) if m else F32(0)
            if F32(self.sample_index - self.last_peak) > avg_rr * F32(1.66) and self.cand:
                lo = self.last_peak + REFRACTORY
                hi = self.sample_index - REFRACTORY
                best = None
                for c in self.cand:
                    if c[0] <= lo or c[0] >= hi:
                        continue
                    if best is None or c[1] > best[1]:
                        best = c
                if best is not None:
                    need = self.noise_peak + F32(0.5) * (self.signal_peak - self.noise_peak)
                    if best[1] > need:
                        # 记录回溯触发时的「窗口滞后量」——决定 filt_hist 到底要多大
                        lag = self.sample_index - int(best[0])
                        if lag > self.max_lag:
                            self.max_lag = lag
                        if FID_MODE:
                            li, lv = int(best[3]), float(best[4])
                            labs, nwin = abs(lv), int(best[5])
                        else:
                            li, lv, labs, nwin = self._locate(int(best[0]))
                        diag = dict(trigger=int(best[0]), thr=float(thr), spacing=0,
                                    is_twave=False, src=1, accepted=False,
                                    prev_integ=float(best[1]), loc=li,
                                    loc_val=float(lv), loc_abs=float(labs), lag=lag, win=nwin)
                        self._accept(li, best[1], best[2], diag)
                        out.append((li, diag))

                        # 🛑 与固件一致：回溯接受后丢弃 index <= 该候选 的全部候选
                        #    （`ecg_rpeak.c:282-290`）。T225 §4.4 指出本夹具此前漏了这一步。
                        #    候选池是按 trigger 递增的，所以等价于「丢掉最旧的一段」。
                        keep_from = int(best[0])
                        self.cand = [c for c in self.cand if c[0] > keep_from]

        self.deriv_p1 = derivative
        self.v_p3 = self.v_p2
        self.v_p2 = self.v_p1
        self.v_p1 = value
        self.integ_p2 = self.integ_p1
        self.integ_p1 = integrated
        self.sample_index += 1
        return out


def stats(peaks, bpm, fs=502.5, skip=0):
    """🛑 fs 用**实测**值（本项目 COM27 连续段 = 90250 样本 / 180 s = 501.4 Hz，
    取 502.5 与 RR 中位 333 自洽）。skip 用于剔除开机瞬态：滤波器/阈值从零起收敛，
    头几拍的落点必然偏，把它计入 σ 会把真正要看的抖动淹没。"""
    peaks = peaks[skip:]
    T = fs * 60.0 / bpm
    rr = [int(b) - int(a) for a, b in zip(peaks, peaks[1:])]
    if len(rr) < 5:
        return None
    med = sorted(rr)[len(rr) // 2]
    err = [abs(v - med) for v in rr]
    thr = max(3.0, 0.02 * med)
    anom = sum(1 for v in err if v > thr)
    se = sorted(err)
    d2 = [int(peaks[i + 1]) - 2 * int(peaks[i]) + int(peaks[i - 1]) for i in range(1, len(peaks) - 1)]
    import statistics as st
    sigma = st.pstdev(d2) / (6 ** 0.5) if len(d2) > 2 else float("nan")
    return dict(n=len(peaks), T=T, med=med, rr_sigma=st.pstdev(rr), sigma_e=sigma,
                p95=se[int(0.95 * (len(se) - 1))], p99=se[int(0.99 * (len(se) - 1))],
                maxerr=se[-1], anom=anom, anom_pct=100.0 * (anom / 2) / len(peaks))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--bpm", type=float, required=True)
    ap.add_argument("--dump-csv")
    ap.add_argument("--trace", help="只打印该峰号区间，如 120-140")
    ap.add_argument("--skip", type=int, default=0, help="统计时剔除前 N 个峰（去开机瞬态）")
    ap.add_argument("--max-samples", type=int)
    a = ap.parse_args()

    samples, seglens, bad = load_frames(a.bin)
    if a.max_samples:
        samples = samples[:a.max_samples]
    if len(samples) < 1000:
        sys.exit("样本太少：%d（bin 里没有可用的连续段？）" % len(samples))
    print("样本 %d   连续段 %d 个 %s   CRC坏帧 %d" %
          (len(samples), len(seglens), seglens[:6], bad))
    print("参数：filt_hist=%d   fid_mode=%d（%s）"
          % (FILT_HIST, FID_MODE, "候选入池即精修" if FID_MODE else "回溯事后精修（固件现状）"))

    rp = RPeak()
    peaks = []
    diags = []
    for x in samples:
        for idx, dg in rp.process(x):
            if idx is not None:
                peaks.append(idx)
                diags.append(dg)

    s = stats(peaks, a.bpm, skip=a.skip)
    # 幅度画像：🛑 「同一算法换一份数据结果突变」时，第一个要查的就是它（11-10 血泪）
    import statistics as _st
    hs = [float(samples[int(p)]) for p in peaks[a.skip:] if 0 <= int(p) < len(samples)]
    print("\n幅度：max %.0f  min %.0f  p2p %.0f  |  检出峰处样本值中位 %.0f"
          % (float(samples.max()), float(samples.min()),
             float(samples.max()) - float(samples.min()),
             _st.median(hs) if hs else 0))
    print("\n重放结果：峰 %d%s" % (len(peaks), ("（统计剔除前 %d 个）" % a.skip) if a.skip else ""))
    if s:
        print("  RR 中位 %d（理论 %.1f）  RR σ %.2f" % (s["med"], s["T"], s["rr_sigma"]))
        print("  σ(e) = %.2f 样本" % s["sigma_e"])
        print("  P95 %.1f   P99 %.1f   最大 %.1f" % (s["p95"], s["p99"], s["maxerr"]))
        print("  异常间期 %d  异常峰占比 %.2f%%" % (s["anom"], s["anom_pct"]))
        print("  判定：%s" % ("PASS" if (s["sigma_e"] <= 0.5 and s["anom"] == 0) else "FAIL"))

        # 异常间期归因：落在主路径峰还是回溯路径峰上（评审 Q2 需要的证据）
        p2 = peaks[a.skip:]
        thr = max(3.0, 0.02 * s["med"])
        by = {0: 0, 1: 0}
        for k in range(1, len(p2)):
            err = abs((int(p2[k]) - int(p2[k - 1])) - s["med"])
            if err > thr:
                src = diags[a.skip + k].get("src", 0)     # 用后一个峰的来源归因
                by[src] = by.get(src, 0) + 1
        print("  异常间期归因：主路径 %d / 回溯路径 %d" % (by.get(0, 0), by.get(1, 0)))
        # 落点未精修（loc==trigger）的峰数 —— 回溯路径覆盖不足的直接指纹
        nfix = sum(1 for dg in diags[a.skip:] if "loc" in dg and dg["loc"] == dg["trigger"])
        nbt = sum(1 for dg in diags[a.skip:] if dg.get("src") == 1)
        print("  落点未精修(loc==trigger) %d 个 / 共 %d 个；其中回溯路径峰 %d 个"
              % (nfix, len(diags) - a.skip, nbt))
        wins = [dg.get("win", REF_WIN) for dg in diags[a.skip:]]
        nshort = sum(1 for w in wins if w < REF_WIN)
        print("  搜索窗不满(%d/%d) %d 个（最窄 %d）⇒ 部分覆盖会静默选错极值，loc≠trigger 也不报警"
              % (REF_WIN, REF_WIN, nshort, min(wins) if wins else 0))
        print("  回溯触发时最大滞后 %d 样本 ⇒ 事后精修至少要 filt_hist ≥ %d"
              % (rp.max_lag, rp.max_lag + LOCATE_HALF + 1))

    if a.dump_csv:
        with open(a.dump_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["k", "peak", "rr", "trigger", "loc", "loc_val", "thr",
                        "spacing", "is_twave", "src", "loc_on_top"])
            for k, (p, dg) in enumerate(zip(peaks, diags)):
                rr = p - peaks[k - 1] if k else ""
                on_top = "" if "loc" not in dg else (dg["loc"] == dg["trigger"])
                w.writerow([k, p, rr, dg.get("trigger"), dg.get("loc"),
                            dg.get("loc_val"), dg.get("thr"), dg.get("spacing"),
                            dg.get("is_twave"), dg.get("src"), on_top])
        print("  逐峰 CSV -> %s" % a.dump_csv)

    if a.trace:
        lo, hi = [int(v) for v in a.trace.split("-")]
        print("\n峰 %d~%d 的决策：" % (lo, hi))
        for k in range(lo, min(hi + 1, len(peaks))):
            dg = diags[k]
            print("  #%d peak=%d trig=%s loc=%s loc_val=%.1f src=%d tw=%s" %
                  (k, peaks[k], dg.get("trigger"), dg.get("loc"),
                   dg.get("loc_val") or 0, dg.get("src", 0), dg.get("is_twave")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
