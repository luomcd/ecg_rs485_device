"""T208d —— 连发 START（区分「半双工丢包」与「完全不通」）。

依据：RUN 态下行实测约 50% 丢（`MEMORY` §10），STOP 态本应 100% 通。
      若只是丢包，连发 8 次全丢的概率 = 0.5^8 ≈ 0.4%，可忽略。
      ⇒ 8 次全无 frames 增长 ⇒ **判「完全不通」**，不再是概率问题。

STM32 侧语义核对（`vcomcmd.c:674-682`）：`0x01` 不过状态门，`ads1293_start()`
首步就停流 ⇒ 重复 START 是安全重启。当前处于「未起采」状态，重发无副作用。

用法：python _t208d_burst_start.py [COM35] [次数]
"""
import re
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM35"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 8
BAUD = 115200

RE_STAT = re.compile(
    r"rx=(\d+)\s*B\s+tx=(\d+)\s*B.*?frames=(\d+)\s+crc_err=(\d+)\s+resync=(\d+)\s*B\s+bad_len=(\d+)",
    re.S,
)


def drain(port, dur=0.5):
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        n = port.in_waiting
        if n:
            buf += port.read(n)
        else:
            time.sleep(0.02)
    return buf.decode("utf-8", errors="replace")


def stat(port):
    port.write(b"485-stat\r\n")
    m = RE_STAT.search(drain(port, 0.6))
    if not m:
        return None
    return dict(zip(("rx", "tx", "frames", "crc_err", "resync", "bad_len"),
                    (int(x) for x in m.groups())))


def main():
    try:
        p = serial.Serial(PORT, BAUD, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 {PORT}：{e}")
        return 2

    try:
        time.sleep(0.3)
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.4)

        # 若 ESP32 期间被重启过，485 是未初始化态 ⇒ 先补上（否则 485-send 原地返回）
        p.write(b"485-stat\r\n")
        head = drain(p, 0.7)
        if "未初始化" in head:
            print("485 未初始化（ESP32 被重启过）→ 执行 485-start")
            p.write(b"485-start\r\n")
            print(drain(p, 1.8))

        base = stat(p)
        if base is None:
            print("[FAIL] 读不到 485-stat")
            return 1
        print(f"基线: rx={base['rx']}  tx={base['tx']}  frames={base['frames']}")
        print(f"连发 {N} 次 485-send start（每次间隔 0.35 s）...\n")

        last = base
        for i in range(1, N + 1):
            p.write(b"485-send start\r\n")
            drain(p, 0.35)
            cur = stat(p)
            if cur is None:
                print(f"  #{i} 读 stat 失败")
                continue
            d_rx = cur["rx"] - last["rx"]
            d_fr = cur["frames"] - last["frames"]
            d_tx = cur["tx"] - last["tx"]
            print(f"  #{i}  Δtx={d_tx:+d}B  Δrx={d_rx:+d}B  Δframes={d_fr:+d}  "
                  f"rx={cur['rx']}  frames={cur['frames']}")
            last = cur
            if cur["frames"] > base["frames"]:
                print(f"\n[判定] ✅ 第 {i} 次成功：STM32 已起采 ⇒ 下行通，之前是丢包")
                return 0

        print(f"\n[判定] ❌ {N} 次全部无 frames 增长（概率 0.5^{N} ≈ {0.5**N:.1%}）")
        print(f"       累计：Δtx={last['tx'] - base['tx']}B（ESP32 确实发了）  "
              f"Δrx={last['rx'] - base['rx']}B（STM32 一个字节都没回）")
        if last["rx"] == base["rx"]:
            print("       ⇒ STM32 侧**连 ASCII 都没吐**（0x01 失败本应回 'start FAIL:'）")
            print("       ⇒ 命令根本没被 STM32 收到，或到了被静默丢弃")
        return 1
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
