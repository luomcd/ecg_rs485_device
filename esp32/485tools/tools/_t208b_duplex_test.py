"""T208b —— ESP32 <-> STM32 485 双通道往返测试（纯上位机，不改固件）。

判据设计（一次测完上下行，不靠猜）：
  下行（ESP32 -> STM32）成立的必要充分证据 = **STM32 从 STOP 变成发帧**
      ⇒ ESP32 侧 `485-stat` 的 frames / rx 从 0 开始涨。
      （STOP 态 STM32 零上行，所以 frames 涨只可能是它收下了 0x01）
  上行（STM32 -> ESP32）成立 = 同上，且 crc_err=0、bad_len=0。

  再发 0x02 STOP，观察 frames **停止增长** ⇒ 反向证明下行不是"碰巧"。

⚠ `485-start` 会先 stop 再 init，统计会归零 —— 所以基线必须在 `485-start` 之后取。
⚠ RUN 态下行实测约 50% 丢（半双工抢总线），STOP 允许重发 2 次并如实记录。

用法：
    python _t208b_duplex_test.py            # COM35，默认
    python _t208b_duplex_test.py COM35
"""
import re
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM35"
BAUD = 115200

RE_STAT = re.compile(
    r"rx=(\d+)\s*B\s+tx=(\d+)\s*B.*?frames=(\d+)\s+crc_err=(\d+)\s+resync=(\d+)\s*B\s+bad_len=(\d+)",
    re.S,
)


def drain(port, dur=0.6):
    """收满 dur 秒的原始字节，返回解码文本。"""
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        n = port.in_waiting
        if n:
            buf += port.read(n)
        else:
            time.sleep(0.02)
    return buf.decode("utf-8", errors="replace")


def stat(port, label):
    port.write(b"485-stat\r\n")
    txt = drain(port, 0.7)
    m = RE_STAT.search(txt)
    if not m:
        print(f"  [{label}] 解析失败，原文：\n{txt}")
        return None
    d = dict(zip(("rx", "tx", "frames", "crc_err", "resync", "bad_len"),
                 (int(x) for x in m.groups())))
    print(f"  [{label}] rx={d['rx']} B  tx={d['tx']} B  frames={d['frames']}  "
          f"crc_err={d['crc_err']}  resync={d['resync']}  bad_len={d['bad_len']}")
    return d


def main():
    try:
        p = serial.Serial(PORT, BAUD, timeout=0.2)
    except Exception as e:
        print(f"[FAIL] 打不开 {PORT}：{e}\n       → 终端/串口助手是否还占着？")
        return 2

    try:
        time.sleep(0.3)
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.5)

        print("=== 1. 确认 485 已初始化 ===")
        p.write(b"485-stat\r\n")
        head = drain(p, 0.7)
        if "未初始化" in head:
            print("  未初始化 → 执行 485-start")
            p.write(b"485-start\r\n")
            print(drain(p, 1.6))
        else:
            print("  已初始化，直接进入基线")

        print("=== 2. 基线（STOP 态，应当 rx/tx/frames 全 0）===")
        base = stat(p, "baseline")
        if base is None:
            return 1

        print("=== 3. 下行 0x01 START（AA 55 80 01 00 01 5D）===")
        p.write(b"485-send start\r\n")
        print(drain(p, 0.8))
        print("  ...等 3.0 s 让 STM32 起来并回流...")
        time.sleep(3.0)
        # 期间不敲任何命令，避免与上行抢总线
        noise = drain(p, 0.4)
        if noise.strip():
            print(f"  [期间纯监听] 收到 {len(noise)} 字符非命令输出（多半是日志）")

        after_start = stat(p, "after START")
        if after_start is None:
            return 1

        d_rx = after_start["rx"] - base["rx"]
        d_fr = after_start["frames"] - base["frames"]
        d_crc = after_start["crc_err"] - base["crc_err"]
        d_bad = after_start["bad_len"] - base["bad_len"]
        print(f"\n  Δrx={d_rx} B  Δframes={d_fr}  Δcrc_err={d_crc}  Δbad_len={d_bad}")
        print(f"  推算行率：{d_fr / 3.0:.1f} fps（按 STM32 25 样本/帧 ≈ 853.24 Hz / 25 = 34.1 fps 上限；"
              f"实测历史值 21.7 fps）\n")

        down_ok = d_fr > 0
        up_ok = d_fr > 0 and d_crc == 0
        print("  [判定] 下行 ESP32→STM32: " + ("✅ 通（STM32 已从 STOP 起采）" if down_ok
                                              else "❌ 不通（frames 未增长，命令没进去）"))
        print("  [判定] 上行 STM32→ESP32: " + ("✅ 通（帧完整，无 CRC 错）" if up_ok
                                              else ("⚠ frames 有增但 crc_err/bad_len 非 0" if down_ok
                                                    else "—（未起采，无从判）")))

        print("\n=== 4. 下行 0x02 STOP，验证 frames 停住 ===")
        sent = 0
        for attempt in (1, 2):
            sent = attempt
            p.write(b"485-send stop\r\n")
            print(drain(p, 0.6))
            time.sleep(1.5)
            cur = stat(p, f"after STOP #{attempt}")
            if cur is None:
                return 1
            if cur["frames"] - after_start["frames"] <= 2:   # 允许在途 1~2 帧
                print(f"  → 第 {attempt} 次 STOP 生效，frames 停住")
                break
            print(f"  → frames 仍在涨（+{cur['frames'] - after_start['frames']}），"
                  f"符合『RUN 态下行 ~50% 丢』，重发")

        return 0 if (down_ok and up_ok) else 1
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
