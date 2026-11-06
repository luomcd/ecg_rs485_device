#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205q_byte_audit.py —— 「发下去到底丢了什么字节」字节级审计（T205）

已经排除的（别再回头试）
------------------------
    丢首字节／协议字段／CRC／长度对核／状态门／命令差异／DTR·RTS／
    WinApp 收发模型（不 flush + 后台死读）／相位／PC 读不读／板子复位态／
    **SWD 读取扰动**（不碰 SWD 仍有 30%）／**发送间隔**（1.0 s→25%，3.0 s→12.5%）

剩下的唯一形态问题
------------------
芯片侧落地率 ≈25%~40%，且**与主机侧一切时序无关** ⇒ 只能是「芯片收到的字节不对」。
也可能是「芯片收到了完整帧但没执行」。这两者必须分开，只有 SWD 能把芯片 RX 缓冲
末态挖出来。

本脚本做的事（每轮）
--------------------
    1. SWD 读 `f64.notch_on` ⇒ 定 want（保证每轮都是**真翻转**，杜绝幂等空操作）
    2. SWD 把 `s_rx_pub[0]`/`s_rx_pub[1]`/`s_rx_work` 三块**清零** + 记 `comm_rx_events` 基线
    3. 发 8 字节 `AA 55 80 02 00 05 xx crc`
    4. sleep 0.45 s
    5. SWD 一次性回读三块缓冲 + `VCOM_RxData/RxLength/RxBuffer` + `rx_events` + `f64.notch_on`
    6. 分类：
         clean_frame  完整 8 B 帧、CRC 对、对齐在 0  ⇒ 字节没问题 ⇒ 病在分派之后
         bad_first    首字节不是 0xAA                ⇒ 帧头被吃掉/损坏
         fragmented   有 AA 但长度不齐                ⇒ 分帧（IDLE 提前触发）
         no_event     三块全 0                        ⇒ 字节根本没到 MCU
         garbage      有非零但与帧无关                ⇒ 换向杂散

地址（IAR map，本轮已复核：f64=0x20000538 + 0x388 = notch_on）
    f64.notch_on  0x200008C0    comm_rx_events 0x200011B4
    s_rx_pub_idx  0x200010B0    s_rx_pub[0]    0x200010B4 (128 B)
    s_rx_pub[1]   0x20001134    s_rx_work      0x20001370 (128 B)
    VCOM_RxData 0x200013F0 ／ RxLength 0x200013F4 ／ RxBuffer 0x200013F8

🛑 `mode=hotplug` 必须；默认模式会软复位 ⇒ 清零是"假清零"。
🛑 清零发生在**发命令之前**，所以本次命令的接收不受影响；回读在命令已处理完之后。

用法
----
    python tools/_t205q_byte_audit.py --port COM27 --trials 8
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

CLI = (r"C:\Program Files\STMicroelectronics\STM32Cube"
       r"\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe")

ADDR_IDX = 0x200010B0
ADDR_PUB0 = 0x200010B4
ADDR_PUB1 = 0x20001134
ADDR_WORK = 0x20001370
ADDR_RXEV = 0x200011B4
ADDR_FLAGS = 0x200008C0
SPAN = 32
WORK_TAIL = 140          # work(128) + RxData(1) + pad + RxLength(4) + RxBuffer(4)


# ------------------------------------------------------------------ 协议 ---
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


# -------------------------------------------------------------------- SWD ---
def _words(out: str) -> bytes:
    words = []
    for line in out.splitlines():
        m = re.match(r"\s*0x[0-9A-Fa-f]{8}\s*:\s*([0-9A-Fa-f ]+)", line)
        if m:
            words.extend(m.group(1).split())
    return b"".join(int(w, 16).to_bytes(4, "little") for w in words)


def cli(args: list):
    try:
        return subprocess.run(
            [CLI, "-c", "port=SWD", "mode=hotplug"] + args + ["-q"],
            capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return None


def chip_notch():
    out = cli(["-r32", hex(ADDR_FLAGS), "4"])
    if not out:
        return None
    d = _words(out)
    return (d[0] & 0xFF) if len(d) >= 1 else None


def clear_all():
    zeros = ["0x00000000"] * (SPAN // 4)
    out = cli(["-w32", hex(ADDR_PUB0)] + zeros
              + ["-w32", hex(ADDR_PUB1)] + zeros
              + ["-w32", hex(ADDR_WORK)] + zeros
              + ["-r32", hex(ADDR_RXEV), "4"])
    if not out or "download complete" not in out:
        return None
    d = _words(out.split("download complete")[-1])
    return int.from_bytes(d[:4], "little") if len(d) >= 4 else None


def dump_all():
    out = cli(["-r32", hex(ADDR_IDX), "4",
               "-r32", hex(ADDR_PUB0), str(SPAN),
               "-r32", hex(ADDR_PUB1), str(SPAN),
               "-r32", hex(ADDR_WORK), str(WORK_TAIL),
               "-r32", hex(ADDR_RXEV), "4",
               "-r32", hex(ADDR_FLAGS), "4"])
    if not out:
        return None
    data = _words(out)
    need = 4 + SPAN * 2 + WORK_TAIL + 4 + 4
    if len(data) < need:
        return None
    tail = data[4 + 2 * SPAN: 4 + 2 * SPAN + WORK_TAIL]
    return {"idx": data[0],
            "pub0": data[4: 4 + SPAN],
            "pub1": data[4 + SPAN: 4 + 2 * SPAN],
            "work": tail[:SPAN],
            "rx_data": tail[128],
            "rx_len": int.from_bytes(tail[132:136], "little"),
            "rx_buf": int.from_bytes(tail[136:140], "little"),
            "rx_events": int.from_bytes(
                data[4 + 2 * SPAN + WORK_TAIL: 8 + 2 * SPAN + WORK_TAIL], "little"),
            "notch": data[8 + 2 * SPAN + WORK_TAIL] & 0xFF}


def brief(b: bytes) -> str:
    nz = [i for i, v in enumerate(b) if v != 0]
    if not nz:
        return "(全 0)"
    hi = max(nz)
    return f"{hi + 1:>2}B: " + " ".join(f"{v:02X}" for v in b[:hi + 1])


# --------------------------------------------------------------- 串口 ---
class Reader:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.sta = []

    def feed(self, data: bytes) -> None:
        self.buf += data
        while True:
            i = self.buf.find(b"\xAA\x55")
            if i < 0:
                if len(self.buf) > 1:
                    del self.buf[: len(self.buf) - 1]
                return
            if i > 0:
                del self.buf[:i]
            if len(self.buf) < 5:
                return
            length = self.buf[3] | (self.buf[4] << 8)
            total = 6 + length
            if total > 260:
                del self.buf[0:1]
                continue
            if len(self.buf) < total:
                return
            fr = bytes(self.buf[:total])
            if crc8(fr[2: total - 1]) != fr[total - 1]:
                del self.buf[0:1]
                continue
            if self.buf[2] == 0x01 and length in (53, 103):
                self.sta.append(fr[7])
            del self.buf[:total]

    def mode(self, tail: int = 8):
        if not self.sta:
            return None
        return (collections.Counter(self.sta[-tail:]).most_common(1)[0][0] >> 1) & 1

    def hist(self, tail: int = 8) -> str:
        c = collections.Counter(self.sta[-tail:])
        return " ".join(f"{k:02X}×{v}" for k, v in sorted(c.items()))


def main() -> int:
    ap = argparse.ArgumentParser(description="字节级审计：芯片到底收到了什么")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=8)
    args = ap.parse_args()

    port = serial.Serial(args.port, args.baud, timeout=0.05)
    with port:
        port.reset_input_buffer()
        time.sleep(0.3)
        if port.in_waiting < 60:
            port.write(build(0x01))
            port.flush()
            time.sleep(0.5)
        print(f"[ok] 芯片 notch={chip_notch()}")
        print()

        tally = collections.Counter()
        for i in range(1, args.trials + 1):
            f0 = chip_notch()
            if f0 is None:
                print("[FAIL] SWD 读失败")
                return 5
            want = 1 - f0
            frame = build(0x05, bytes([want]))

            ev0 = clear_all()
            if ev0 is None:
                print("    [warn] 清零失败，跳过本轮")
                continue

            t0 = time.monotonic()
            port.write(frame)
            port.flush()
            time.sleep(0.45)

            mem = dump_all()
            if mem is None:
                print("[FAIL] 回读失败")
                return 5
            dt = (time.monotonic() - t0) * 1000
            d_ev = mem["rx_events"] - ev0
            applied = (mem["notch"] == want)

            # 分类
            if mem["work"].startswith(frame) or mem["pub0"].startswith(frame) \
                    or mem["pub1"].startswith(frame):
                key, note = "clean_frame", "完整合法帧对齐在 0"
            elif not any(mem["work"]) and not any(mem["pub0"]) and not any(mem["pub1"]):
                key, note = "no_event", "三块全 0 ⇒ 字节没到 MCU"
            elif mem["work"] and mem["work"][0] not in (0, 0xAA):
                key, note = "bad_first", f"work[0]=0x{mem['work'][0]:02X} ⇒ 帧头损坏"
            elif 0xAA in mem["work"]:
                key, note = "fragmented", "有 AA 但内容不齐 ⇒ 分帧/混入垃圾"
            else:
                key, note = "garbage", "与帧无关的非零内容"
            tally[key] += 1
            tally["生效" if applied else "未生效"] += 1
            tally[(key, applied)] += 1

            buf_tag = {ADDR_PUB0: "pub0", ADDR_PUB1: "pub1"}.get(mem["rx_buf"], "?")
            print(f"--- #{i}  want={want}  {dt:.0f}ms  生效={'是' if applied else '**否**'} ---")
            print(f"  发出 : {frame.hex(' ').upper()}")
            print(f"  work : {brief(mem['work'])}")
            print(f"  pub0 : {brief(mem['pub0'])}")
            print(f"  pub1 : {brief(mem['pub1'])}")
            print(f"  交接 : RxData={mem['rx_data']} RxLength={mem['rx_len']} "
                  f"RxBuffer={mem['rx_buf']:#010x}({buf_tag})  Δevents={d_ev}  "
                  f"chip={mem['notch']}")
            print(f"  ⇒ {key}：{note}")
            print()

        print("=" * 66)
        for k, v in tally.items():
            if isinstance(k, tuple):
                continue
            print(f"  {k:<12} {v}")
        print("-" * 66)
        pairs = {k[0]: {} for k in tally if isinstance(k, tuple)}
        for k, v in tally.items():
            if isinstance(k, tuple):
                pairs.setdefault(k[0], {})[k[1]] = v
        for shape, d in pairs.items():
            print(f"  {shape:<12} 生效 {d.get(True, 0)} ／ 未生效 {d.get(False, 0)}")
        print("=" * 66)
        return 0


if __name__ == "__main__":
    sys.exit(main())
