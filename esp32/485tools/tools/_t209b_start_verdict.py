#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t209b_start_verdict.py —— 下行终判：复位到 STOP 态后，ESP32 发 START(0x01)

为什么这条判据比 0x10 快照更硬
------------------------------
    0x10 快照有两个「合法静默」漏洞：RUN 态静默、AFE 没供电 SPI 失败静默。
    0x01 START 只有一个静默条件：**命令没到**。
    而且它的回执是**持续帧流**（≈21.7 fps × 59 B ≈ 1285 B/s），
    帧流一旦起来，物理上无法伪造 —— 比一帧 80 B 好判得多。

流程（顺序不能变）
------------------
    ① `485-start`   必须最先（它会 rs485_reset_stat + ecg_stream_init，清零统计）
    ② 记基线
    ③ ST-Link 复位 STM32 ⇒ 回到 STOP 态（消除「复位前已经在 RUN」的干扰）
       ⚠ SWD attach 会 halt 内核，只在复位这一下用，复位完立刻 detach
    ④ 读统计：rx 增量 ≈98 B（ASCII 横幅）= 上行健康探针
    ⑤ `485-send start`（0x01）⇒ 等 6 s 分两窗读 rx/frames 增量
    ⑥ 判定

判读
----
    frames 涨 / rx 暴涨   ⇒ ✅ 下行通（0x01 被受理，STM32 起采）
    rx 停在横幅值不动      ⇒ ❌ 下行不通（字节没到 STM32）
    bad_len/crc_err 涨    ⇒ 到了但被打乱（极性/终端/波特率）

用法
----
    python _t209b_start_verdict.py --esp COM35
"""
import argparse
import re
import subprocess
import sys
import time

import serial

CLI = r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"

RE_STAT = re.compile(
    r"rx=(\d+)\s*B\s+tx=(\d+)\s*B.*?frames=(\d+)\s+crc_err=(\d+)\s+resync=(\d+)\s*B\s+bad_len=(\d+)",
    re.S,
)
RE_SNAPSHOTS = re.compile(r"snapshots=(\d+)")
KEYS = ("rx", "tx", "frames", "crc", "resync", "bad_len")


def drain(p, dur):
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        n = p.in_waiting
        if n:
            buf += p.read(n)
        else:
            time.sleep(0.02)
    return bytes(buf)


def cmd(p, line, wait=1.0):
    p.write(line.encode() + b"\r\n")
    p.flush()
    return drain(p, wait).decode("utf-8", "replace")


def stat(p):
    txt = cmd(p, "485-stat", 0.9)
    m = RE_STAT.search(txt)
    if not m:
        return None
    d = dict(zip(KEYS, (int(x) for x in m.groups())))
    mm = RE_SNAPSHOTS.search(txt)
    d["snapshots"] = int(mm.group(1)) if mm else -1
    return d


def stm32_reset():
    try:
        r = subprocess.run([CLI, "-c", "port=SWD", "mode=normal", "-rst"],
                           capture_output=True, text=True, timeout=60)
    except Exception as e:
        return False, f"调用失败: {e}"
    out = r.stdout or ""
    ok = ("MCU Reset" in out) or ("Software reset is performed" in out)
    m = re.search(r"Device name\s*:\s*(.+)", out)
    return ok, (m.group(1).strip() if m else out.strip().splitlines()[-1][:60])


def show(d, tag):
    print(f"   {tag}: rx={d['rx']} tx={d['tx']} frames={d['frames']} "
          f"snapshots={d['snapshots']} bad_len={d['bad_len']} crc_err={d['crc']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esp", default="COM35")
    ap.add_argument("--settle", type=float, default=1.6)
    ap.add_argument("--hold", type=float, default=6.0, help="START 后观察总时长")
    a = ap.parse_args()

    p = serial.Serial(a.esp, 115200, timeout=0.2)
    try:
        time.sleep(0.4)
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.4)

        print("① 485-start（最先执行，之后不再调用）")
        cmd(p, "485-start", 2.5)
        s0 = stat(p)
        if s0 is None:
            print("[FAIL] 读不到 485-stat")
            return 2
        show(s0, "基线")

        print("\n② ST-Link 复位 STM32（⇒ 必然 STOP 态）")
        ok, info = stm32_reset()
        print(f"   复位{'成功' if ok else '失败'}：{info}")
        if not ok:
            return 2
        time.sleep(a.settle)

        s1 = stat(p)
        d_rx = s1["rx"] - s0["rx"]
        print(f"\n③ 复位后: rx Δ{d_rx:+d} B（≈98 B 横幅 ⇒ 上行健康）")
        up = d_rx >= 40

        print("\n④ 485-send start（0x01 —— 判据就在这一步）")
        out = cmd(p, "485-send start", 1.2)
        for line in out.splitlines():
            if line.strip() and "485" not in line.lower():
                print("   " + line.strip())

        print(f"\n⑤ 等 {a.hold:.0f} s，分两窗看帧流是否起来")
        half = a.hold / 2.0
        time.sleep(half)
        s2 = stat(p)
        print(f"   窗A(0~{half:.0f}s): rx Δ{s2['rx']-s1['rx']:+d} B  frames Δ{s2['frames']-s1['frames']:+d}")
        time.sleep(half)
        s3 = stat(p)
        print(f"   窗B({half:.0f}~{a.hold:.0f}s): rx Δ{s3['rx']-s2['rx']:+d} B  frames Δ{s3['frames']-s2['frames']:+d}")

        d_flow = s3["rx"] - s1["rx"]
        d_fr = s3["frames"] - s1["frames"]
        d_bl = s3["bad_len"] - s1["bad_len"]
        d_crc = s3["crc"] - s1["crc"]

        print("\n" + "=" * 68)
        print(f"  上行（复位横幅）   : {'✅ 通，rx +%d B' % d_rx if up else '❌ 未收到'}")
        print(f"  下行（0x01 START） : {'✅ 通 —— 起采，rx +%d B / frames +%d' % (d_flow, d_fr) if d_fr > 0 else '❌ 不通（帧流没起来）'}")
        print(f"  线上错帧           : bad_len Δ{d_bl:+d}   crc_err Δ{d_crc:+d}")
        print()
        if up and d_fr > 0:
            print("[结论] ✅ **下行通了** —— 0x01 被 STM32 受理并起采")
            print(f"       帧率 ≈ {d_fr / a.hold:.1f} fps，码率 ≈ {d_flow / a.hold:.0f} B/s")
            return 0
        if up and d_fr == 0:
            print("[结论] ❌ 上行通、下行不通（STOP 态已由复位保证，状态门排除）")
            if d_bl > 0 or d_crc > 0:
                print("       线上有字节但被打乱 ⇒ 查 A/B 极性 / 终端电阻 / 波特率")
            else:
                print("       0x01 根本没被解析 ⇒ 字节没进 PD2")
                print("       下一步测量（按信息量排序）：")
                print("         ① UT-2211 DB9 pin2(SOUT) 对 pin5(GND)：应为 −5~−10 V")
                print("         ② UT-2211 pin4 是否真接了 +5 V（无源电荷泵的供电前提）")
                print("         ③ MAX3232 R1OUT(→STM32 PD2) 静态高电平应为 ≥1.26 V")
            return 1
        print("[结论] ❌ 双通道都不通 ⇒ 断点在 STM32 一侧")
        return 2
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
