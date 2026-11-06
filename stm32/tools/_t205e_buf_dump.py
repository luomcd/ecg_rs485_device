#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205e_buf_dump.py —— 直接 dump 固件收到的原始字节（T205 最终判据）

为什么必须走到这一步
--------------------
前面四步全部被自己的数据否掉过：

| 步骤 | 结论 | 后来被什么推翻 |
|---|---|---|
| ASCII 复测 | RUN 态 0/10，"完全不通" | 样本太小：p≈0.2 时 0/10 有 11% 概率 |
| 前缀补偿 | 不丢首字节（k=0 通、k≥1 全否） | 仍成立 |
| 相位扫描 | 方向抢占成立 | 线路占用仅 10%，矛盾；3 次/相位是噪声 |
| 计数器 | "rx_events 在涨 ⇒ 字节到了" | **空白对照**：静默时它也涨 1.0/4s ⇒ 证明不了 |

所以不再从外部推，直接看**固件缓冲里到底躺着什么**。

读什么（IAR map：`EWARM/demo_ads1293/build/.../demo_ads1293.map`）
------------------------------------------------------------------
    s_rx_pub_idx 0x200010B0  (1 B)   最近一次发布到哪一片
    s_rx_pub     0x200010B4  (256 B) **两片乒乓发布区**，各 128 B
    s_rx_work    0x20001370  (128 B) 外设收落点（回调里的拷贝源）

判读
----
* 缓冲里出现 `AA 55 80 02 00 05 xx crc`（8 B 整齐）⇒ 帧**完整到达且长度正确**
  ⇒ 病在 `vcom_bin_dispatch` 之后（状态/内核），要另找。
* 出现 `… AA 55 80 02 00 05 xx crc` 但前面还有别的字节 ⇒ **垃圾并进了同一包**
  ⇒ `buf[0]!=0xAA` ⇒ 静默丢。这是"帧到了却没生效"的最可能形态。
* 只有半截（如 `AA 55 80 02`）⇒ **IDLE 在帧内提前触发** ⇒ 长度对核失败。
* 全是 0 或全是旧噪声 ⇒ 该次命令**根本没产生事件**。

🛑 必须 `mode=hotplug`（默认模式软复位 ⇒ 缓冲清零，读出来是假空白）。

用法
----
    python tools/_t205e_buf_dump.py --port COM27 --trials 6
"""
import argparse
import collections
import re
import subprocess
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial：pip install pyserial")

CLI = r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"

ADDR_IDX = 0x200010B0     # s_rx_pub_idx  (1 B)
ADDR_PUB = 0x200010B4     # s_rx_pub      (256 B = 两片 × 128)
ADDR_WORK = 0x20001370    # s_rx_work     (128 B)
PUB_LEN = 128


def crc8(data: bytes) -> int:
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build(cmd: int, arg: bytes = b"") -> bytes:
    length = 1 + len(arg)
    body = bytes([0x80, length & 0xFF, (length >> 8) & 0xFF, cmd]) + arg
    return bytes([0xAA, 0x55]) + body + bytes([crc8(body)])


CMD_SET_NOTCH = 0x05


def read_mem() -> dict | None:
    try:
        out = subprocess.run(
            [CLI, "-c", "port=SWD", "mode=hotplug",
             "-r32", hex(ADDR_IDX), "4",
             "-r32", hex(ADDR_PUB), str(PUB_LEN * 2),
             "-r32", hex(ADDR_WORK), str(PUB_LEN), "-q"],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except Exception as exc:
        print(f"    [warn] SWD 读失败: {exc}")
        return None

    words = []
    for line in out.splitlines():
        m = re.match(r"\s*0x[0-9A-Fa-f]{8}\s*:\s*([0-9A-Fa-f ]+)", line)
        if m:
            words.extend(m.group(1).split())
    data = b"".join(int(w, 16).to_bytes(4, "little") for w in words)
    if len(data) < 4 + PUB_LEN * 2 + PUB_LEN:
        print(f"    [warn] SWD 返回不足：{len(data)} B")
        return None
    idx = data[0]
    pub = data[4: 4 + PUB_LEN * 2]
    work = data[4 + PUB_LEN * 2: 4 + PUB_LEN * 2 + PUB_LEN]
    return {"idx": idx, "pub0": pub[:PUB_LEN], "pub1": pub[PUB_LEN:], "work": work}


def hexline(b: bytes) -> str:
    """只打印有内容的区间；全 0 直接标出来。"""
    nz = [i for i, v in enumerate(b) if v != 0]
    if not nz:
        return "(全 0)"
    lo, hi = min(nz), max(nz)
    return f"[{lo:>3}..{hi:>3}] " + " ".join(f"{v:02X}" for v in b[lo: hi + 1])


class Reader:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.sta = []

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
                continue
            if ftype == 0x01 and length in (53, 103):
                self.sta.append((now, frame[7]))
            del self.buf[:total]

    def notch(self) -> int | None:
        if not self.sta:
            return None
        tail = [s for _, s in self.sta[-8:]]
        return (collections.Counter(tail).most_common(1)[0][0] >> 1) & 0x01


def drain(port, rd: Reader, frames: int = 12, timeout: float = 1.5) -> None:
    n0 = len(rd.sta)
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end and len(rd.sta) - n0 < frames:
        chunk = port.read(64)
        if chunk:
            rd.feed(chunk, time.monotonic())


def main() -> int:
    ap = argparse.ArgumentParser(description="dump 固件 RX 缓冲")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=6)
    args = ap.parse_args()

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.05)
    except Exception as exc:
        print(f"[FAIL] 打不开 {args.port}: {exc}")
        return 6

    with port:
        port.reset_input_buffer()
        rd = Reader()
        drain(port, rd, 25, 3.0)
        if len(rd.sta) < 10:
            print(f"[FAIL] 只收到 {len(rd.sta)} 帧 ⇒ 不在 RUN 态／串口错")
            return 6
        print(f"[ok] 上行 {len(rd.sta)} 帧，STA=0x{rd.sta[-1][1]:02X}")
        print()

        for i in range(1, args.trials + 1):
            cur = rd.notch()
            if cur is None:
                continue
            want = 1 - cur
            frame = build(CMD_SET_NOTCH, bytes([want]))

            port.write(frame)
            port.flush()
            time.sleep(0.40)
            drain(port, rd, 12, 1.5)
            got = rd.notch()
            flipped = got == want

            mem = read_mem()
            if mem is None:
                return 5

            print(f"--- #{i}  want={want}  翻转={'是' if flipped else '否'}  "
                  f"pub_idx={mem['idx']} ---")
            print(f"  发出去的帧 : {frame.hex(' ').upper()}")
            print(f"  pub0       : {hexline(mem['pub0'])}")
            print(f"  pub1       : {hexline(mem['pub1'])}")
            print(f"  work       : {hexline(mem['work'])}")

            # 判读：在缓冲里找完整命令帧
            hit = None
            for tag, buf in (("pub0", mem["pub0"]), ("pub1", mem["pub1"])):
                p = buf.find(frame)
                if p >= 0:
                    hit = (tag, p)
                    break
            if hit:
                tag, p = hit
                where = "包首（合法）" if p == 0 else f"偏移 {p}（**前面有垃圾 ⇒ 会被静默丢**）"
                print(f"  ⇒ 完整帧出现在 {tag} {where}；生效={'是' if flipped else '否'}")
            else:
                print("  ⇒ 缓冲里**找不到**这条完整帧")
            print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
