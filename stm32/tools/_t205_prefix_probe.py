#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205_prefix_probe.py —— 判「RUN 态下行帧开头被吞掉几个字节」（T205）

为什么要这个脚本
----------------
T204 证过下行通（`SET_MODE`/`SET_NOTCH` 的 STA 位跟随）。但 2026-11-04 在**当前**这条
通路上复测，结果两极：

    STOP 态（上行停流）            ASCII / 二进制  = 4/4、100%
    RUN  态（显示帧 20 fps 在流）  ASCII / 二进制  = 0/10、0/5

而 `comm_tx_drops` 一格没涨 ⇒ `printf_msg` **根本没被调用** ⇒ 命令没进到解析之后。
但 `comm_rx_events` +27（发 15 条）⇒ 字节**确实到了** STM32 的 UART5。

⇒ 结论指向「字节到了，但整帧被判非法」。而源码里有两道**位置敏感**的闸：

    `vcom_recv_analysis():847`  `VCOM_RxLength >= 7 && buf[0]==0xAA && buf[1]==0x55`
    `vcom_bin_dispatch():627`   `src[0]==0xAA && src[1]==0x55`
    `vcom_bin_dispatch():639`   `6 + LEN == len`          ← 长度必须**精确**

**只要帧首丢 1 个字节，这三道闸全部命中失败，且全程静默**（无回显、无计数器）。

判据设计（为什么不能只看"成功/失败"）
--------------------------------------
给帧前面加 **k 个填充字节**，则：

    「恰好被吞掉 k 个字节」⟺ 收到的缓冲里剩下的正好是合法帧 ⟺ 命令生效

于是跑 k = 0/1/2/3 四组，**哪一组通过，就说明吞掉了几个字节**；
四组全不通过 ⇒ 不是"吞开头"，是别的（帧被切断、CRC 坏、或根本没解析）。

🛑 判据必须是 **STA 位翻转**，不是"没报错"。且每次发**与当前相反**的值（T204 教训：
   拿固定值当判据时，"命令没到"与"命令到了但值本来就对"分不开 ⇒ 假阳性）。

用法
----
    python tools/_t205_prefix_probe.py --port COM27            # RUN 态（显示帧在流）
    python tools/_t205_prefix_probe.py --port COM27 --trials 10

退出码：0 命中某一组（打印是第几组）／5 四组全不通过／6 收不到显示帧
"""
import argparse
import collections
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial：pip install pyserial")


# ============================ 协议层 ======================================

def crc8(data: bytes) -> int:
    """poly=0x07 / init=0x00 / 不反射 —— 与固件 `ecg_crc8.c` 同式。"""
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build(cmd: int, arg: bytes = b"") -> bytes:
    """下行帧：AA 55 | 0x80 | LEN(2B LE) | CMD | ARG | CRC8，总长 = 6 + LEN。"""
    length = 1 + len(arg)
    body = bytes([0x80, length & 0xFF, (length >> 8) & 0xFF, cmd]) + arg
    return bytes([0xAA, 0x55]) + body + bytes([crc8(body)])


CMD_SET_NOTCH = 0x05


class FrameReader:
    """滚动解析上行显示帧（TYPE=0x01，LEN=53/103），取 STA = frame[7]。"""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.sta_seq = []
        self.bad_crc = 0

    def feed(self, data: bytes, now: float) -> None:
        self.buf += data
        while True:
            idx = self.buf.find(b"\xAA\x55")
            if idx < 0:
                if len(self.buf) > 1:
                    del self.buf[: len(self.buf) - 1]
                return
            if idx > 0:
                del self.buf[:idx]
            if len(self.buf) < 5:
                return
            ftype = self.buf[2]
            length = self.buf[3] | (self.buf[4] << 8)
            total = 6 + length
            if total > 200:
                del self.buf[0:1]
                continue
            if len(self.buf) < total:
                return
            frame = bytes(self.buf[:total])
            if crc8(frame[2 : total - 1]) != frame[total - 1]:
                del self.buf[0:1]
                self.bad_crc += 1
                continue
            if ftype == 0x01 and length in (53, 103):
                self.sta_seq.append((now, frame[7]))
            del self.buf[:total]

    @property
    def frames(self) -> int:
        return len(self.sta_seq)


def collect(port, min_frames: int = 12, timeout: float = 4.0) -> FrameReader:
    """按**帧数**收（T204 教训：CH340 上 `read(4096)` 会阻塞 3.49 s，墙钟不可信）。"""
    reader = FrameReader()
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end and reader.frames < min_frames:
        chunk = port.read(64)
        if chunk:
            reader.feed(chunk, time.monotonic())
    return reader


def notch_of(sta: int) -> int:
    return (sta >> 1) & 0x01


def steady(reader: FrameReader, tail: int = 8) -> int | None:
    if not reader.sta_seq:
        return None
    tail_values = [s for _, s in reader.sta_seq[-tail:]]
    return collections.Counter(tail_values).most_common(1)[0][0]


# ============================ 主流程 ======================================

# (标签, 填充前缀)  —— 加 k 字节后，只有「恰好吞掉 k 字节」才会重新构成合法帧
VARIANTS = [
    ("k=0 无前缀", b""),
    ("k=1",        b"\xAA"),
    ("k=2",        b"\xAA\x55"),
    ("k=3",        b"\xAA\x55\xAA"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="T205 下行帧被吞字节数判定")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=8, help="每组试几次（值交替，判翻转）")
    ap.add_argument("--verbose", action="store_true", help="逐次打印窗口内的 STA 轨迹")
    args = ap.parse_args()

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.05)
    except Exception as exc:
        print(f"[FAIL] 打不开 {args.port}: {exc}")
        return 6

    with port:
        port.reset_input_buffer()
        base = collect(port, 25, 6.0)
        if base.frames == 0:
            print(f"[FAIL] 收不到显示帧（坏CRC={base.bad_crc}）⇒ 不在 RUN 态／串口选错")
            return 6
        sta0 = steady(base)
        print(f"[ok] 上行 {base.frames} 帧，STA=0x{sta0:02X}（当前陷波 "
              f"{'开' if notch_of(sta0) else '关'}）")

        # 目标值交替：每次发**与当前相反**的值 ⇒ 判据是"翻转"，能区分"没变"和"变对了"
        want = 1 - notch_of(sta0)
        results = {}

        for label, prefix in VARIANTS:
            hits = 0
            detail = []
            for _ in range(args.trials):
                payload = prefix + build(CMD_SET_NOTCH, bytes([want]))
                port.write(payload)
                port.flush()
                win = collect(port, 12, 4.0)
                got_sta = steady(win)
                ok = got_sta is not None and notch_of(got_sta) == want
                hits += 1 if ok else 0
                detail.append("1" if ok else ".")
                if args.verbose:
                    seen = [f"{s:02X}" for _, s in win.sta_seq]
                    print(f"      want={want}  seen={'-'.join(seen)}")
                if ok:
                    want ^= 1          # 只有真的变了才翻转期望值
                time.sleep(0.05)
            results[label] = (hits, len(detail))
            print(f"  {label:<12} {hits}/{len(detail)}  {' '.join(detail)}")

        print()
        print("=" * 56)
        winners = [k for k, (h, n) in results.items() if h >= max(2, n // 2)]
        for label, (h, n) in results.items():
            print(f"{label:<14}{h}/{n}")
        print("=" * 56)
        if not winners:
            print("[?] 四组全不通过 ⇒ 不是「吞开头」。可能性收窄为：")
            print("    · 帧被中途切断（IDLE 在帧内提前触发）⇒ 长度对不上，同样静默")
            print("    · CRC 被破坏")
            print("    · 命令压根没进 UART5（需读 comm_rx_events 复核）")
            return 5
        print(f"[命中] {winners} 通过 ⇒ 下行帧**开头被吞 {winners} 个字节**。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
