#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""esp_run.py —— 板端一次完整「实时 485 记录」跑测（P4-a 用）

把 T226 §9 的 P4-a 固化成一条命令：**重启 → 开 485 源 → 下发 START → 记录 N 秒 → 回传峰序列**。

为什么要重启
------------
🛑 `play` 之后板子的数据源会**反复被文件回放抢回 FILE**：`485-start` 明明成功
（`485-stat` 显示 `源: RS485 / UART: ready`），3 s 后再敲命令就变回
`源: FILE（SD 回放）/ UART: 未初始化`，连 `485-send` 都报「485 未初始化」。
症状极具误导性（像是 UART 被关掉了），实际是 FILE 生产者还在推样本。
⇒ 唯一干净的起点是 `restart`。

为什么要校验 frames
------------------
🛑 `485-start` ≠ 让 STM32 出数据。它只开 ESP32 侧 UART1，**必须再发 `0x01`**
（`485-send start`）才进 RUN。只发前者时 `485-stat` 全是 0，会被误判成"链路坏"。
本脚本因此在起记录**之前**强制校验 `frames > 0`，不达标直接失败退出。

用法
----
    py esp_run.py --secs 180 --out _board_fid1_90bpm.txt
    py esp_run.py --secs 180 --out x.txt --no-restart    # 源已经是 RS485 时
"""
import argparse
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from s9a_link_check import EspPort       # noqa: E402


def stat_frames(txt):
    m = re.search(r"frames=(\d+)", txt)
    return int(m.group(1)) if m else -1


def stat_field(txt, key):
    m = re.search(key + r"=(\d+)", txt)
    return int(m.group(1)) if m else -1


def dump_file(e, stem, timeout=180.0):
    """把 SD 上的结果文件回传并返回文本。

    🛑 **不能用 `EspPort.wait_prompt()`**：它把累积的字节读进局部变量后**直接丢弃**
    （只返回 True/False），随后 `take()` 拿到的是空的 —— 表现就是"回传 0 字符"，
    很容易被误读成"SD 上没有结果文件"。这里自己攒，攒到 prompt 为止。
    """
    e.take()
    e.ser.write(("result-dump %s\r\n" % stem).encode())
    e.ser.flush()
    acc = bytearray()
    end = time.time() + timeout
    while time.time() < end:
        acc.extend(e.take())
        if b"cardiolite> " in acc:
            # prompt 可能出现在中间（多行回显）—— 再多收 0.8 s 兜住尾巴
            time.sleep(0.8)
            acc.extend(e.take())
            break
        time.sleep(0.05)
    return acc.decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser(description="板端实时 485 记录一次（P4-a）")
    ap.add_argument("--port", default="COM35")
    ap.add_argument("--secs", type=int, default=180)
    ap.add_argument("--out", required=True, help="峰序列回传保存路径")
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("--dump-only", action="store_true", help="只把 SD 上的 rpeak_hrv.txt 拉回来")
    ap.add_argument("--fmt", choices=["compact", "wide"], default="compact",
                    help="峰序列格式；compact 只有峰号（落盘行数少、不易丢行），wide 带 ±3 邻域")
    a = ap.parse_args()

    if a.dump_only:
        e = EspPort(a.port)
        time.sleep(0.5)
        data = dump_file(e, "rpeak_hrv.txt")
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
        print("%s（%d 字符）" % (a.out, len(data)))
        e.close()
        return 0

    if not a.no_restart:
        print("[1/6] 重启板子（清掉可能残留的 FILE 回放）…")
        e = EspPort(a.port)
        time.sleep(0.5)
        e.ser.write(b"restart\r\n")
        time.sleep(1.0)
        e.close()
        time.sleep(7.0)

    print("[2/6] 开 485 源 …")
    e = EspPort(a.port)
    time.sleep(0.5)
    print("  " + e.cmd("485-start", 3.0).strip().splitlines()[0])

    print("[3/6] 校验 UART 已收帧（否则下发 START）…")
    txt = e.cmd("485-stat", 3.0)
    if stat_frames(txt) <= 0:
        print("  frames=0 ⇒ 下发 485-send start")
        e.cmd("485-send start", 3.0)
        time.sleep(5.0)
        txt = e.cmd("485-stat", 3.0)
    nf = stat_frames(txt)
    print("  frames=%d  rx=%d B" % (nf, stat_field(txt, "rx")))
    if nf <= 0:
        print("  ✗ 仍收不到帧 —— STM32 没在出数据或总线没接，终止（先查硬件，不要归因固件）")
        e.close()
        return 1

    # 记录基线（记录开始时刻的累计值，用来算增量）
    base = {k: stat_field(txt, k) for k in ("frames", "crc_err", "seq_gaps", "bad_len", "resync")}

    # 🛑 关掉屏上打点绘制：`rpeak off` **只停绘制、不停检测**，
    #    但能显著降低 LVGL 负载 —— 消费者一慢，环缓就满，`push_sample` 会**跳过检测器**
    #    （实测一次 180 s 跑出 fed=77700 / pushed=101075 ⇒ 23% 样本没进检测器，
    #     这种数据算出来的 RR 全是假的）。
    e.cmd("rpeak off", 2.0)

    print("[4/6] %s + rec %d s …" % (a.fmt, a.secs))
    fmt_cmd = "rpeak wide on" if a.fmt == "wide" else "rpeak wide off"
    print("  " + e.cmd(fmt_cmd, 2.0).strip().splitlines()[0])
    r = e.cmd("rpeak rec %d" % a.secs, 3.0)
    if "记录开始" not in r:
        print("  ✗ 记录没起来：\n%s" % r)
        e.close()
        return 1
    print("  " + [l for l in r.splitlines() if "记录开始" in l][0].strip())

    t0 = time.time()
    while time.time() - t0 < a.secs + 8:
        time.sleep(10.0)
        print("    … %ds / %ds" % (int(time.time() - t0), a.secs), flush=True)

    print("[5/6] 回传峰序列 …")
    data = dump_file(e, "rpeak_hrv.txt")
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)
    print("  %s（%d 字符）" % (a.out, len(data)))

    print("[6/6] 链路增量对账 …")
    txt = e.cmd("485-stat", 3.0)
    print(txt)
    e.cmd("rpeak on", 2.0)          # 恢复屏上打点
    e.close()

    # 🛑 完整性闸门：rec 开始时检测器会被 reset（fed 归零），
    #    所以 rec 结束后 fed ≈ secs×500 才说明「没有样本被跳过」。
    fed = stat_field(txt, "fed")
    need = a.secs * 500
    if fed < need * 0.97:
        print("⚠ fed=%d < %d（%.0f%%）⇒ 环缓曾满、检测器跳过了样本，本份数据不能用于判 σ"
              % (fed, need, 100.0 * fed / need))

    print("=" * 60)
    print("本次增量（应全为 0，否则本份数据不能用来说算法）：")
    for k, v0 in base.items():
        v1 = stat_field(txt, k)
        print("  %-9s %+d" % (k, v1 - v0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
