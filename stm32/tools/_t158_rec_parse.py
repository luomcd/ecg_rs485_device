#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_t158_rec_parse.py —— 上位机「● 记录」落盘文件解析与体检（T158, 2026-09-29）

**为什么有这个脚本**：上位机 `Recorder` 只把**串口原始字节**写进
`recordings/ecg_*.bin`（`Form1.cs:738`，在解析/显示之前），**没有下游读回来**。
所以「● 记录」按下去是好的，但拿到的是一坨字节 —— 本脚本就是缺的那一环。

帧结构（来源 `Core/Protocol.cs`，实测复验过）
    AA 55 | TYPE(1) | LEN(u16 LE) | PAYLOAD(LEN) | CRC8(1)       总长 = 6 + LEN
    CRC8: poly 0x07, init 0x00, 计算范围 = TYPE + LEN + PAYLOAD
    TYPE 0x01 显示帧: PAYLOAD = SEQ(u16 LE) | [STA(1)] | N×int16(LE)
        LEN =  52 ⇒ v1.1 旧帧   ，无 STA，N = 25
        LEN =  53 ⇒ v2.2 单通道 ，有 STA，N = 25
        LEN = 103 ⇒ v2.2 双通道 ，有 STA，N = 50  ← 块布局！[0:25]=CH1、[25:50]=CH2
    STA 位域: 0x01 Mode(0=diag/1=mon) 0x02 Notch 0x04 Channel(0=I/1=I&II)
              0x08 Raw853 0x10 Lod        （v1.1 旧帧无此字节）
换算: µV = int16 × 0.1526（`AppConfig.MicroVoltsPerLsb`，±5 mV 满刻度）
    标称 500 Hz（`AppConfig.DisplayFrameRate`，⚠ `test raw853 on` 时实际 853.2423）

**时间轴由 SEQ 给**（丢帧也不会错位），**不依赖任何墙钟** —— 文件里没有时间戳。

用法:
    python tools/_t158_rec_parse.py <记录文件.bin> [--csv out.csv] [--png out.png]
                                    [--t0 秒] [--t1 秒] [--bl-win 秒] [--quiet]
"""
import argparse
import os
import struct
import sys
from collections import Counter

import numpy as np

MAGIC = b"\xAA\x55"
CRC_POLY = 0x07
FRAME_RATE = 500.0      # 标称显示流采样率 Hz（`raw853 off`）
FS853 = 853.2423208191125   # ★T183：`raw853 on` 时**帧流实际按 853.2423 Hz 出**
#   ── 依据（2026-10-10 T183）：STA bit3=1 的 `.bin` 记录，
#      「文件墙钟时长 33.279 s」vs 「1136 帧 × 25 点 ÷ 等速率」：
#      fs=500 ⇒ 56.800 s（差 +70.7%）｜ fs=853.2423 ⇒ 33.285 s（差 **+0.016%**）。
#      ⇒ 帧率 = 853.2423/25 = 34.14 fps；`_t158` 原先把 fs 硬写 500 ⇒ 时长虚高 1.7065×。
#   ⚠ 注意：`duration_s = span × 25 / fs` 是**恒等式** ⇒ 老版本报的
#      「实测速率 500.000 Hz」**不是独立测量**，是假设的回读（无证据力）。
UV_PER_LSB = 0.1526     # µV = int16 × 0.1526
PER_CH = 25             # 每通道每帧样本数
STA_RAW853 = 0x08       # STA bit3：0 = off（500 Hz 流）／1 = on（853.2423 Hz 流）
FS_OVERRIDE = None      # ★T183：命令行 `--fs <Hz>` 覆盖（None = 按 STA 自动判档）


def rate_from_sta(sta):
    """按 STA bit3 给出帧流速率；无 STA 字节（0xFF）时按 off 处理。"""
    return FS853 if (sta != 0xFF and (sta & STA_RAW853)) else FRAME_RATE

LEN_TABLE = {
    52: ("v1.1 旧帧·无STA·单通道", False, 25),
    53: ("v2.2 单通道", True, 25),
    103: ("v2.2 双通道", True, 50),
}
STA_BITS = (
    ("模式", 0x01, {0: "diag(诊断)", 1: "mon(监护)"}),
    ("陷波", 0x02, {0: "off", 1: "on"}),
    ("通道", 0x04, {0: "I", 1: "I&II"}),
    ("raw853", 0x08, {0: "off", 1: "on"}),
    ("脱落", 0x10, {0: "正常", 1: "LOD 脱落"}),
)


def _mk_crc_table():
    tab = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ CRC_POLY) & 0xFF if (c & 0x80) else ((c << 1) & 0xFF)
        tab.append(c)
    return tab


_CRC_TAB = _mk_crc_table()


def crc8(buf):
    c = 0
    for x in buf:
        c = _CRC_TAB[c ^ x]
    return c


def autodetect_frame_len(buf):
    """按 AA55 位置间距投票；返回 (帧长, 间距直方图)。"""
    pos = []
    k = buf.find(MAGIC)
    while k >= 0:
        pos.append(k)
        k = buf.find(MAGIC, k + 1)
    if len(pos) < 2:
        return 0, Counter()
    hist = Counter(pos[i + 1] - pos[i] for i in range(min(len(pos) - 1, 5000)))
    return hist.most_common(1)[0][0], hist


def parse(buf, verbose=True):
    """解析整个文件；返回 dict。"""
    flen, hist = autodetect_frame_len(buf)
    rep = {
        "bytes": len(buf),
        "frame_len": flen,
        "magic_hits": sum(hist.values()),
        "gap_hist": hist,
        "frames": [],
        "crc_bad": 0,
        "crc_ok": 0,
        "resync": 0,
        "tail_bytes": 0,
    }
    if flen < 7:
        rep["error"] = "帧长无法自动识别（AA55 少于 2 次或间距异常）"
        return rep

    i = 0
    frames = []
    while i + flen <= len(buf):
        if buf[i:i + 2] != MAGIC:
            # 失步：向后找下一个帧头
            nxt = buf.find(MAGIC, i + 1)
            if nxt < 0 or nxt + flen > len(buf):
                break
            rep["resync"] += 1
            i = nxt
            continue
        fr = buf[i:i + flen]
        if crc8(fr[2:flen - 1]) == fr[flen - 1]:
            rep["crc_ok"] += 1
            typ = fr[2]
            length = struct.unpack("<H", fr[3:5])[0]
            payload = fr[5:5 + length]
            seq = struct.unpack("<H", payload[0:2])[0]
            has_sta = length != 52
            sta = payload[2] if has_sta else 0xFF
            off = 3 if has_sta else 2
            raw = np.frombuffer(payload[off:off + 2 * (len(payload) - off) // 2], dtype="<i2")
            frames.append((seq, sta, length, raw.astype(np.float64) * UV_PER_LSB, typ))
        else:
            rep["crc_bad"] += 1
        i += flen
    rep["tail_bytes"] = len(buf) - i
    rep["frames"] = frames

    if not frames:
        rep["error"] = "没有一帧通过 CRC 校验"
        return rep

    seqs = np.array([f[0] for f in frames], dtype=np.int64)
    d = np.diff(seqs)
    rep["seq_first"] = int(seqs[0])
    rep["seq_last"] = int(seqs[-1])
    rep["n_frames"] = len(frames)
    rep["n_pts"] = len(frames) * PER_CH
    # SEQ 是 16 bit ⇒ 回绕按 +65536 处理
    wrap = d < 0
    d_ok = d.copy()
    d_ok[wrap] += 65536
    rep["dropped"] = int(np.sum(d_ok[d_ok > 1] - 1))
    rep["gaps"] = [(int(seqs[i]), int(d_ok[i])) for i in range(len(d_ok)) if d_ok[i] != 1]
    rep["gap_hist_seq"] = Counter(int(x) for x in d_ok if x != 1).most_common(8)
    # 时长由 SEQ 给（末帧首点 − 首帧首点 + 一帧）
    span = int(seqs[-1] - seqs[0]) + (1 if not wrap.any() else int(np.sum(d_ok != 1)))
    stas_all = Counter(f[1] for f in frames)
    sta0 = stas_all.most_common(1)[0][0]
    # ★T183：速率按 STA bit3 取（`raw853 on` ⇒ 853.2423），可用 --fs 覆盖
    fs_used = rate_from_sta(sta0)
    if FS_OVERRIDE:
        fs_used = FS_OVERRIDE
    rep["fs"] = fs_used
    rep["fs_auto"] = rate_from_sta(sta0)
    rep["fs_src"] = ("--fs 覆盖" if FS_OVERRIDE else
                     ("STA bit3 = 1 ⇒ 853.2423" if fs_used == FS853 else "STA bit3 = 0 ⇒ 500"))
    rep["duration_s"] = span * PER_CH / fs_used
    rep["duration_s_at500"] = span * PER_CH / FRAME_RATE
    rep["eff_fps"] = len(frames) / rep["duration_s"] if rep["duration_s"] > 0 else float("nan")
    rep["rate_hz"] = span * PER_CH / rep["duration_s"] if rep["duration_s"] > 0 else float("nan")
    stas = stas_all
    rep["sta_hist"] = stas
    rep["sta"] = sta0
    rep["lens"] = Counter(f[2] for f in frames)
    rep["types"] = Counter(f[4] for f in frames)
    return rep


def sta_text(sta):
    if sta == 0xFF:
        return "（v1.1 旧帧：无 STA 字节）"
    return " ".join("%s=%s" % (name, tbl[1 if (sta & bit) else 0])
                    for name, bit, tbl in STA_BITS)


def describe(rep, quiet=False):
    out = []
    a = out.append
    a("=" * 78)
    a("【文件】%d 字节  帧长自动识别 = %d 字节（AA55 命中 %d 次）"
      % (rep["bytes"], rep["frame_len"], rep["magic_hits"]))
    if "error" in rep:
        a("🛑 " + rep["error"])
        return "\n".join(out)
    a("        间距直方图前 4：%s" % repr(rep["gap_hist"].most_common(4)))
    a("【帧型】%s" % (LEN_TABLE.get(rep["frame_len"] - 6, ("未知", None, None))[0]))
    a("        LEN 分布 %s ｜ TYPE 分布 %s"
      % (dict(rep["lens"]), dict(rep["types"])))
    a("【审计】总帧 %d ｜ CRC OK %d / BAD %d ｜ 失步重同步 %d 次 ｜ 尾部残字节 %d"
      % (rep["n_frames"] + rep["crc_bad"], rep["crc_ok"], rep["crc_bad"],
         rep["resync"], rep["tail_bytes"]))
    a("        SEQ %d → %d ｜ 丢帧 %d ｜ 非连续 %d 处 %s"
      % (rep["seq_first"], rep["seq_last"], rep["dropped"], len(rep["gaps"]),
         rep["gap_hist_seq"] if rep["gap_hist_seq"] else "无"))
    a("        时长 %.3f s（由 SEQ 推）｜ 采样点 %d ｜ 出帧速率 %.3f Hz（%s）"
      % (rep["duration_s"], rep["n_pts"], rep["fs"], rep["fs_src"]))
    if abs(rep.get("duration_s_at500", rep["duration_s"]) - rep["duration_s"]) > 1e-6:
        a("        ⚠ 若仍按旧版 500 Hz 折算会得 %.3f s（差 %+.1f%%）—— 旧版硬编码 500 的系统偏差"
          % (rep["duration_s_at500"],
             (rep["duration_s_at500"] / rep["duration_s"] - 1.0) * 100.0))
    a("【档位】STA = 0x%02X ⇒ %s" % (rep["sta"], sta_text(rep["sta"])))
    if len(rep["sta_hist"]) > 1:
        a("        ⚠ 段内 STA 变过 %s" % {("%02X" % k): v for k, v in rep["sta_hist"].items()})
    if not quiet:
        ch = 2 if rep["frame_len"] - 6 == 103 else 1
        a("【通道】%d 条（块布局：前 %d 点=CH1%s）" % (ch, PER_CH, "，后 25 点=CH2" if ch == 2 else ""))
        for c in range(ch):
            seg = np.concatenate([f[3][c * PER_CH:(c + 1) * PER_CH] for f in rep["frames"]])
            a("        CH%d  均值 %+9.1f µV ｜ min %+9.1f ｜ max %+9.1f ｜ 标准差 %8.1f"
              % (c + 1, seg.mean(), seg.min(), seg.max(), seg.std()))
            n1 = int(rep["fs"])  # 前 1 s
            if len(seg) > 2 * n1:
                a("             前 1 s 均值 %+8.1f µV ⇒ 末 1 s 均值 %+8.1f µV ⇒ 漂移 %+8.1f µV"
                  % (seg[:n1].mean(), seg[-n1:].mean(), seg[-n1:].mean() - seg[:n1].mean()))
    a("=" * 78)
    return "\n".join(out)


def build_series(rep, t0=None, t1=None):
    """拼成等间隔序列；返回 (t, ch1, ch2) 或 (t, ch1, None)。"""
    ch = 2 if rep["frame_len"] - 6 == 103 else 1
    segs1, segs2 = [], []
    t0_seq, t1_seq = None, None
    for seq, sta, length, vals, typ in rep["frames"]:
        if not segs1:
            t0_seq = seq
        segs1.append(vals[:PER_CH])
        if ch == 2:
            segs2.append(vals[PER_CH:2 * PER_CH])
        t1_seq = seq + 1
    span = t1_seq - t0_seq
    fs = rep.get("fs", FRAME_RATE)          # ★T183：随时间轴同步用真实出帧速率
    t = (np.arange(span * PER_CH) / fs)
    y1 = np.concatenate(segs1) if segs1 else np.array([])
    y2 = np.concatenate(segs2) if segs2 else None
    if t0 is not None or t1 is not None:
        lo = int((t0 or 0) * fs)
        hi = int((t1 if t1 is not None else t[-1] + 1) * fs)
        lo = max(0, lo)
        hi = min(len(y1), hi)
        t = t[lo:hi]
        y1 = y1[lo:hi]
        y2 = y2[lo:hi] if y2 is not None else None
    return t, y1, y2


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("bin_path")
    ap.add_argument("--csv")
    ap.add_argument("--png")
    ap.add_argument("--t0", type=float)
    ap.add_argument("--t1", type=float)
    ap.add_argument("--fs", type=float, default=None,
                    help="强制出帧速率 Hz（默认按 STA bit3 自动判档）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    global FS_OVERRIDE
    FS_OVERRIDE = a.fs

    if not os.path.exists(a.bin_path):
        print("🛑 文件不存在: %s" % a.bin_path)
        return 2
    if os.path.getsize(a.bin_path) == 0:
        print("🛑 文件是 0 字节 —— 按了「● 记录」但**串口没有收到任何数据**。")
        print("   成因：记录只搬运串口字节流 ⇒ 若当时未 Start（固件没在发帧）或串口是虚的，就落空。")
        return 1

    buf = open(a.bin_path, "rb").read()
    rep = parse(buf)
    print(describe(rep, quiet=a.quiet))
    if "error" in rep:
        return 1
    if a.csv or a.png:
        t, y1, y2 = build_series(rep, a.t0, a.t1)
        if a.csv:
            cols = [t] + ([y1] if True else []) + ([y2] if y2 is not None else [])
            hdr = ["t_s"] + ["ch1_uv"] + (["ch2_uv"] if y2 is not None else [])
            arr = np.column_stack(cols)
            np.savetxt(a.csv, arr, delimiter=",", header=",".join(hdr),
                       comments="", fmt="%.4f")
            print("→ CSV 已导出 %s（%d 行）" % (a.csv, len(arr)))
        if a.png:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            fig, ax = plt.subplots(figsize=(12, 5))
            ax.plot(t, y1, lw=0.8, color="#c0392b", label="CH1")
            if y2 is not None:
                ax.plot(t, y2, lw=0.8, color="#2c3e50", label="CH2", alpha=0.8)
            ax.set_xlabel("t (s)  ← 由 SEQ 推，非墙钟")
            ax.set_ylabel("µV")
            ax.set_title("%s ｜ 帧长 %d ｜ %.2f s ｜ %s"
                         % (os.path.basename(a.bin_path), rep["frame_len"],
                            rep["duration_s"], sta_text(rep["sta"])))
            ax.grid(alpha=0.3)
            ax.legend(loc="upper right")
            fig.tight_layout()
            fig.savefig(a.png, dpi=120)
            print("→ PNG 已导出 %s" % a.png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
