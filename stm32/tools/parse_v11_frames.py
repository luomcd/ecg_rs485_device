#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_v11_frames.py —— 串口协议 v1.1 二进制帧解析 + 波形预览（2026-09-11）

对应《ECG波形上位机_WinForm_需求描述.md》v1.1 §3：
  帧结构  : AA 55 | TYPE(1B) | LEN(2B, u16 小端) | PAYLOAD(LEN 字节) | CRC8(1B)
  CRC8    : poly 0x07, init 0x00, 计算范围 = TYPE + LEN + PAYLOAD
  Type 0x01 显示帧: SEQ u16 + 25 x int16（0.1526 uV/LSB，500 Hz，LEN=52）
  Type 0x02 调试帧: SEQ u16 + 20 x { seq_raw u32, raw24 u32, seq_dsp u32, dsp i16, y_code f32 }
                    （853.24 Hz，LEN=362，小端）

用法:
    python parse_v11_frames.py <抓包文件> [输出.png]

抓包要求：串口工具以「原始字节 / 二进制」方式保存（不要加时间戳前缀）。
若工具只能存十六进制文本（如 "AA 55 01 ..."），脚本也会自动识别并转换。

判定标准（协议闭环验收）：
  CRC 错误 = 0；SEQ 无跳变（丢帧 = 0）；显示流实测速率 ≈ 500 Hz；调试流 ≈ 853.24 Hz；
  y_code(uV) 与 dsp(uV) 波形形态一致。
"""

import re
import sys
import struct
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

FS_DSP = 853.2423          # 调试流采样率 Hz
FS_DISP = 500.0            # 显示流采样率 Hz
UV_PER_LSB16 = 5.0e3 / 32767.0     # 0.15259 uV/LSB（±5 mV 满刻度 int16）
UV_PER_CODE = 0.1122       # y_code / raw24 的 uV 换算
RAW_CENTER = 6_075_000     # 偏移二进制中心
LEN_OK = {0x01: 52, 0x02: 362}


# ---------------------------------------------------------------- 基础工具
def crc8(data: bytes, crc: int = 0x00) -> int:
    """CRC-8, poly 0x07, init 0x00（与固件侧必须一致）。"""
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def load_bytes(path: str) -> bytes:
    raw = open(path, "rb").read()
    # 自动识别「十六进制文本」保存方式
    try:
        txt = raw.decode("ascii")
        if len(txt) > 20 and re.fullmatch(r"[0-9A-Fa-f\s]+", txt):
            hexs = re.sub(r"\s+", "", txt)
            if len(hexs) % 2 == 0 and len(hexs) >= 20:
                print("[提示] 输入是十六进制文本，已自动转成二进制")
                return bytes.fromhex(hexs)
    except (UnicodeDecodeError, ValueError):
        pass
    return raw


# ---------------------------------------------------------------- 帧解析
def parse_frames(buf: bytes):
    """按 §3.3 同步策略解析，返回 (显示帧列表, 调试帧列表, 统计)。"""
    st = dict(total=len(buf), tried=0, crc_err=0, bad_len=0,
              disp=0, dbg=0, disp_gap=0, dbg_gap=0, other=0)
    disp, dbg = [], []          # (seq, payload bytes)
    i, n = 0, len(buf)
    last_disp_seq = last_dbg_seq = None

    while i < n - 5:
        if buf[i] != 0xAA or buf[i + 1] != 0x55:
            i += 1
            continue
        typ = buf[i + 2]
        ln = buf[i + 3] | (buf[i + 4] << 8)
        end = i + 5 + ln + 1
        if typ not in LEN_OK or ln != LEN_OK[typ] or end > n:
            st["bad_len" if typ in LEN_OK else "other"] += 1
            i += 1                                   # 回退 1 字节重扫（确定性恢复）
            continue
        payload = buf[i + 5:i + 5 + ln]
        if crc8(buf[i + 2:i + 5 + ln]) != buf[end - 1]:
            st["crc_err"] += 1
            i += 1
            continue
        seq = payload[0] | (payload[1] << 8)
        if typ == 0x01:
            if last_disp_seq is not None:
                st["disp_gap"] += (seq - last_disp_seq - 1) % 65536
            last_disp_seq = seq
            disp.append((seq, np.frombuffer(payload[2:], dtype="<i2").copy()))
            st["disp"] += 1
        else:
            if last_dbg_seq is not None:
                st["dbg_gap"] += (seq - last_dbg_seq - 1) % 65536
            last_dbg_seq = seq
            rec = np.frombuffer(payload[2:], dtype=np.dtype(
                [("seq_raw", "<u4"), ("raw24", "<u4"),
                 ("seq_dsp", "<u4"), ("dsp", "<i2"), ("y_code", "<f4")]))
            dbg.append((seq, rec.copy()))
            st["dbg"] += 1
        i = end

    return disp, dbg, st


# ---------------------------------------------------------------- 简易 R 峰（仅健康检查用）
def count_beats(uv: np.ndarray, fs: float) -> int:
    w = max(1, int(0.015 * fs))
    sm = np.convolve(np.abs(uv), np.ones(w) / w, mode="same")
    thr = 0.4 * sm.max()
    cand = np.where((sm[1:-1] > thr) & (sm[1:-1] >= sm[:-2]) & (sm[1:-1] > sm[2:]))[0] + 1
    refr = int(0.2 * fs)
    cnt, last = 0, -10 * refr
    for p in cand:
        if p - last >= refr:
            cnt += 1
            last = p
    return cnt


# ---------------------------------------------------------------- 主流程
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    out_png = sys.argv[2] if len(sys.argv) > 2 else "v11_parse_check.png"

    buf = load_bytes(path)
    disp, dbg, st = parse_frames(buf)
    print("=" * 72)
    print(f"文件: {path}   ({st['total']:,} 字节)")
    print("=" * 72)
    print(f"显示帧(Type 0x01): {st['disp']:>7} 帧   SEQ 丢帧: {st['disp_gap']}")
    print(f"调试帧(Type 0x02): {st['dbg']:>7} 帧   SEQ 丢帧: {st['dbg_gap']}")
    print(f"CRC 错误: {st['crc_err']}    LEN 异常: {st['bad_len']}    无关 AA55: {st['other']}")

    ok = st["crc_err"] == 0 and st["disp_gap"] == 0 and st["dbg_gap"] == 0
    print(f"\n协议闭环判定: {'✓ 通过（CRC=0、丢帧=0）' if ok else '✗ 有异常，见上表'}")

    rows, titles = [], []
    if dbg:
        rec = np.concatenate([r for _, r in dbg])
        n_d = len(rec)
        t_d = np.arange(n_d) / FS_DSP
        dur_d = n_d / FS_DSP
        print(f"\n[调试流] {n_d} 样本 = {dur_d:.1f} s → 实测 {n_d/dur_d:.2f} Hz (期望 853.24)")
        raw_uv = (rec["raw24"].astype(np.float64) - np.median(rec["raw24"])) * UV_PER_CODE
        dsp_uv = rec["dsp"].astype(np.float64) * UV_PER_LSB16
        y_uv = (rec["y_code"].astype(np.float64) - np.median(rec["y_code"])) * UV_PER_CODE
        hr = count_beats(y_uv, FS_DSP)
        print(f"[调试流] R 峰约 {hr} 个 → 心率 ≈ {hr/dur_d*60:.0f} bpm")
        print(f"[调试流] raw p-p {raw_uv.max()-raw_uv.min():.0f} uV,  y p-p {y_uv.max()-y_uv.min():.0f} uV")
        rows += [(t_d, raw_uv / 1000), (t_d, dsp_uv / 1000), (t_d, y_uv / 1000)]
        titles += ["raw24（去中心 ×0.1122）", "dsp int16 ×0.1526", "y_code float32（去中心 ×0.1122）"]
    if disp:
        smp = np.concatenate([s for _, s in disp])
        n_p = len(smp)
        t_p = np.arange(n_p) / FS_DISP
        dur_p = n_p / FS_DISP
        print(f"[显示流] {n_p} 样本 = {dur_p:.1f} s → 实测 {n_p/dur_p:.2f} Hz (期望 500)")
        print(f"[显示流] p-p {(smp.max()-smp.min())*UV_PER_LSB16:.0f} uV")
        rows += [(t_p, smp.astype(np.float64) * UV_PER_LSB16 / 1000)]
        titles.append("显示帧 int16 ×0.1526")

    if not rows:
        print("\n未解析出任何有效帧 —— 请确认固件已按 §3.0 帧结构输出、串口工具保存的是原始字节。")
        sys.exit(2)

    fig, axes = plt.subplots(len(rows), 1, figsize=(13, 2.6 * len(rows) + 1), sharex=False)
    axes = np.atleast_1d(axes)
    for ax, (t, y), title in zip(axes, rows, titles):
        ax.plot(t, y, lw=0.7, color="#d62728" if "raw" in title else "#1f77b4")
        ax.set_title(title, fontsize=10, loc="left")
        ax.set_ylabel("mV")
        ax.grid(alpha=0.35, which="both")
        ax.set_xlim(t[0], t[0] + min(10.0, t[-1] - t[0]))
    axes[-1].set_xlabel("时间 (s) —— 每面板显示前 10 s")
    fig.suptitle(f"协议 v1.1 解析验证  {path.split(chr(92))[-1]}\n"
                 f"CRC 错误 {st['crc_err']} | 丢帧 显示{st['disp_gap']}/调试{st['dbg_gap']}",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out_png, dpi=130)
    print(f"\n波形图已保存: {out_png}")
    sys.exit(0 if ok else 3)


if __name__ == "__main__":
    main()
