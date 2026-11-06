#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205f_clean_dump.py —— 先把 RX 缓冲清零，再发一条命令，看留下的到底是什么（T205 定案）

为什么还要这一步
----------------
`_t205e_buf_dump.py` 已经证明了 `s_rx_pub` 里存的是「本次事件 Size 字节 + 之后全是旧残留」
（残留里看得到 `63 68 0D 0A` = `test notch\\r\\n` 的尾巴）。**残留会盖住真实尺寸**，
肉眼分不清"事件里就这么多字节"还是"后面没被覆盖"。

解决办法很简单：**SWD 先把三块缓冲清零**，再发命令，再 dump。
之后出现的任何非零字节，都只可能来自这一条命令。

读/写地址（IAR map）
--------------------
    s_rx_pub_idx 0x200010B0  1 B
    s_rx_pub[0]  0x200010B4  128 B
    s_rx_pub[1]  0x20001134  128 B
    s_rx_work    0x20001370  128 B

判读表
------
    work 里出现 `AA 55 80 02 00 05 xx crc`（8 B 对齐）⇒ 帧完整到达、长度正确
        ⇒ 若仍未生效，病在 `vcom_bin_dispatch` 之后（状态/内核），与链路无关
    work 里首位不是 0xAA（例如 0x80 / 0x00 / 0x55）⇒ **首字节损坏**
        ⇒ `vcom_recv_analysis:847` 的首字节闸挡住 ⇒ 静默丢
    work 里长度 < 8 ⇒ **IDLE 在帧内提前触发**（分帧）
    work 全 0 ⇒ 这次命令**根本没产生事件**（字节没到）

🛑 `mode=hotplug` 必须；默认模式会软复位 ⇒ 缓冲清零是"假清零"。

用法
----
    python tools/_t205f_clean_dump.py --port COM27 --trials 8
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

ADDR_IDX = 0x200010B0
ADDR_PUB0 = 0x200010B4
ADDR_PUB1 = 0x20001134
ADDR_WORK = 0x20001370
ADDR_RXEV = 0x200011B4     # comm_rx_events
SPAN = 32                 # 清零/回读的字节数（够装 8 B 帧 + 余量）
# 🛑 关键：`VCOM_RxData/Length/Buffer` 紧贴在 `s_rx_work`（0x20001370+128=0x200013F0）之后
#    0x200013F0 RxData(1) ／ 0x200013F4 RxLength(4) ／ 0x200013F8 RxBuffer(4)
#    一次读 140 B 就同时拿到 work + 这三个交接量 ⇒ 能判"主循环到底取走没有"
WORK_TAIL = 140


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


def _parse_words(out: str) -> bytes:
    words = []
    for line in out.splitlines():
        m = re.match(r"\s*0x[0-9A-Fa-f]{8}\s*:\s*([0-9A-Fa-f ]+)", line)
        if m:
            words.extend(m.group(1).split())
    return b"".join(int(w, 16).to_bytes(4, "little") for w in words)


def run_cli(args: list) -> str | None:
    try:
        return subprocess.run([CLI, "-c", "port=SWD", "mode=hotplug"] + args + ["-q"],
                              capture_output=True, text=True, timeout=60).stdout
    except Exception as exc:
        print(f"    [warn] SWD 失败: {exc}")
        return None


def clear_buffers() -> dict | None:
    """清零三块缓冲，并返回清零瞬间的 rx_events（作为本轮基线）。"""
    zeros = ["0x00000000"] * (SPAN // 4)
    out = run_cli(["-w32", hex(ADDR_PUB0)] + zeros
                  + ["-w32", hex(ADDR_PUB1)] + zeros
                  + ["-w32", hex(ADDR_WORK)] + zeros
                  + ["-r32", hex(ADDR_RXEV), "4"])
    if out is None or "download complete" not in out:
        return None
    d = _parse_words(out.split("download complete")[-1])
    if len(d) < 4:
        return None
    return {"rx_events": int.from_bytes(d[:4], "little")}


def dump_buffers() -> dict | None:
    out = run_cli(["-r32", hex(ADDR_IDX), "4",
                   "-r32", hex(ADDR_PUB0), str(SPAN),
                   "-r32", hex(ADDR_PUB1), str(SPAN),
                   "-r32", hex(ADDR_WORK), str(WORK_TAIL),
                   "-r32", hex(ADDR_RXEV), "4"])
    if out is None:
        return None
    data = _parse_words(out)
    need = 4 + SPAN * 2 + WORK_TAIL + 4
    if len(data) < need:
        print(f"    [warn] 回读不足 {len(data)}/{need} B")
        return None
    work_tail = data[4 + 2 * SPAN: 4 + 2 * SPAN + WORK_TAIL]
    return {"idx": data[0],
            "pub0": data[4: 4 + SPAN],
            "pub1": data[4 + SPAN: 4 + 2 * SPAN],
            "work": work_tail[:SPAN],
            "rx_data": work_tail[128],
            "rx_len": int.from_bytes(work_tail[132:136], "little"),
            "rx_buf": int.from_bytes(work_tail[136:140], "little"),
            "rx_events": int.from_bytes(data[4 + 2 * SPAN + WORK_TAIL:], "little")}


def brief(b: bytes) -> str:
    nz = [i for i, v in enumerate(b) if v != 0]
    if not nz:
        return "(全 0)"
    hi = max(nz)
    body = " ".join(f"{v:02X}" for v in b[: hi + 1])
    return f"{hi + 1:>2}B: {body}"


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
    ap = argparse.ArgumentParser(description="清零后 dump RX 缓冲")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=8)
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

        tally = collections.Counter()
        for i in range(1, args.trials + 1):
            cur = rd.notch()
            if cur is None:
                continue
            want = 1 - cur
            frame = build(CMD_SET_NOTCH, bytes([want]))

            c0 = clear_buffers()
            if c0 is None:
                print("    [warn] 清零失败，跳过本轮")
                continue

            port.write(frame)
            port.flush()
            time.sleep(0.40)
            drain(port, rd, 12, 1.5)
            got = rd.notch()
            flipped = got == want

            mem = dump_buffers()
            if mem is None:
                return 5
            d_ev = mem["rx_events"] - c0["rx_events"]

            print(f"--- #{i}  want={want}  翻转={'是' if flipped else '否'}  "
                  f"pub_idx={mem['idx']}  Δevents={d_ev} ---")
            print(f"  发出   : {frame.hex(' ').upper()}")
            print(f"  work   : {brief(mem['work'])}")
            print(f"  pub0   : {brief(mem['pub0'])}")
            print(f"  pub1   : {brief(mem['pub1'])}")
            buf_tag = {ADDR_PUB0: "pub0", ADDR_PUB1: "pub1"}.get(mem["rx_buf"], "?")
            print(f"  交接    : RxData={mem['rx_data']}  RxLength={mem['rx_len']}  "
                  f"RxBuffer={mem['rx_buf']:#010x}({buf_tag})")

            if mem["work"].find(frame) == 0 or mem["pub0"].find(frame) == 0 \
                    or mem["pub1"].find(frame) == 0:
                key = "clean_frame"
                note = "完整合法帧（8B 对齐在 0）"
            elif mem["work"].count(0) == len(mem["work"]) and \
                    mem["pub0"].count(0) == len(mem["pub0"]) and \
                    mem["pub1"].count(0) == len(mem["pub1"]):
                key = "no_event"
                note = "缓冲全 0 ⇒ 这条命令**没产生任何事件**"
            else:
                nz_w = [v for v in mem["work"] if v]
                if not nz_w:
                    key = "other"
                    note = "work 空，但 pub 有内容"
                elif nz_w[0] != 0xAA:
                    key = "bad_first"
                    note = f"work 首字节 = 0x{nz_w[0]:02X}（不是 0xAA）⇒ 首字节损坏"
                else:
                    key = "fragmented"
                    note = "有 AA 但长度/内容不齐 ⇒ 分帧或混入垃圾"
            tally[key] += 1

            # 另一片（本次没被 pub_idx 指到的那片）里若有 < 4 B 的短事件，
            # 说明命令之外**还有一包杂散**进来 —— 正常命令最短 7 B（二进制）／12 B（ASCII）。
            other = mem["pub1"] if mem["idx"] == 0 else mem["pub0"]
            o_nz = [v for v in other if v]
            short_ev = 0 < len(o_nz) < 4
            tally[("翻转" if flipped else "未翻转") + ("+杂散" if short_ev else "−杂散")] += 1

            print(f"  ⇒ {note}   生效={'是' if flipped else '否'}"
                  f"   另一片={'短事件 ' + str(len(o_nz)) + 'B' if short_ev else ('空' if not o_nz else str(len(o_nz)) + 'B')}")
            print()

        print("=" * 60)
        print(f"汇总：{dict(tally)}")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
