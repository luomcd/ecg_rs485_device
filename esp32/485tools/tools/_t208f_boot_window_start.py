"""T208f —— 在 STM32 上电窗口内猛发 START（验证 UT-2211 电荷泵供电机理）。

机理假设（UT-2211 官方手册：无源 RS-232 电荷泵取电，485 侧第 4 脚才有 VCC 备用输入）：
    STM32 空闲（STOP 态）⇒ TXD 恒 mark（负电平）⇒ 电荷泵泵不出正电源
    ⇒ UT-2211 的 RS-232 输出驱不动 ⇒ **下行发不回 STM32**
    STM32 一有数据（TXD 正负跳变）⇒ 有电 ⇒ 上行通

⇒ 推论：**在 STM32 正在发上电横幅的那几百毫秒内**打 START，有机会进去。
   一旦 START 进去 ⇒ 起采 ⇒ TXD 持续活动 ⇒ 电荷泵持续有电 ⇒ 系统**自举**稳定。

判据：frames 从 0 变正 ⇒ 机理坐实，且链路被"点燃"。
用法：python -u _t208f_boot_window_start.py COM35 45
"""
import re
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM35"
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
BAUD = 115200

RE_STAT = re.compile(
    r"rx=(\d+)\s*B\s+tx=(\d+)\s*B.*?frames=(\d+)\s+crc_err=(\d+)\s+resync=(\d+)\s*B\s+bad_len=(\d+)",
    re.S,
)


def drain(port, dur):
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        n = port.in_waiting
        if n:
            buf += port.read(n)
        else:
            time.sleep(0.01)
    return buf.decode("utf-8", errors="replace")


def stat(port):
    port.write(b"485-stat\r\n")
    m = RE_STAT.search(drain(port, 0.35))
    if not m:
        return None
    return dict(zip(("rx", "tx", "frames", "crc_err", "resync", "bad_len"),
                    (int(x) for x in m.groups())))


def main():
    try:
        p = serial.Serial(PORT, BAUD, timeout=0.1)
    except Exception as e:
        print(f"[FAIL] 打不开 {PORT}：{e}", flush=True)
        return 2

    try:
        time.sleep(0.3)
        p.reset_input_buffer()
        p.write(b"\r\n")
        drain(p, 0.3)
        p.write(b"485-stat\r\n")
        if "未初始化" in drain(p, 0.6):
            print("485 未初始化 → 485-start", flush=True)
            p.write(b"485-start\r\n")
            drain(p, 1.6)

        base = stat(p)
        print(f"基线: rx={base['rx']} tx={base['tx']} frames={base['frames']}", flush=True)
        print(f"=== {DUR:.0f}s 窗口：每 8 ms 一批（3 帧），总线占用约 14% ===", flush=True)
        print(">>> 请现在给 STM32 重新上电，并在窗口内多上电几次（每次横幅只有 ~14 ms）<<<", flush=True)

        BATCH = 3
        one = b"485-send start\r\n"
        burst = one * BATCH
        t0 = time.time()
        n_sent = 0
        n_round = 0
        last_report = 0.0
        while time.time() - t0 < DUR:
            p.write(burst)          # 一次写入 3 条，CLI 逐条执行 ⇒ 3 帧背靠背
            n_sent += BATCH
            n_round += 1
            time.sleep(0.008)
            drain(p, 0.0)           # 只清空回显，不等待

            ts = time.time() - t0
            if ts - last_report >= 1.0:
                last_report = ts
                cur = stat(p)
                if cur is None:
                    continue
                print(f"[{ts:5.1f}s] 已发 {n_sent:5d} 帧  rx={cur['rx']}  "
                      f"tx={cur['tx']}  frames={cur['frames']}  resync={cur['resync']}",
                      flush=True)
                if cur["frames"] > 0:
                    print("\n" + "=" * 60, flush=True)
                    print(f"*** frames={cur['frames']} —— START 进去了！***", flush=True)
                    print("*** 机理坐实：STM32 TX 活动时 UT-2211 才有电 ***", flush=True)
                    print("*** 且已起采，TXD 持续活动 ⇒ 自举稳定 ***", flush=True)
                    print("=" * 60, flush=True)
                    time.sleep(2.0)
                    fin = stat(p)
                    print(f"2 s 后复核: rx={fin['rx']} frames={fin['frames']} "
                          f"crc={fin['crc_err']}", flush=True)
                    return 0
        print(f"\n[判定] {DUR:.0f}s / {n_sent} 帧全部无效，frames 仍 0", flush=True)
        return 1
    finally:
        try:
            p.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
