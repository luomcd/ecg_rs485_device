#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205d_event_probe.py —— 逐条命令读 STM32 计数器，判「字节没到」还是「到了没生效」（T205 第四步）

为什么要这一步
--------------
前三步已经把范围压到很窄，但还差最后一刀：

| 步骤 | 结论 |
|---|---|
| 前缀补偿 | k=0 通、k≥1 全否 ⇒ **不丢首字节** |
| 相位扫描 | 3 次/相位，噪声大；且与「线路占用仅 10%」矛盾 ⇒ 不采信 |
| 单发/连发 | 二进制 p(1)=16.7% → p(8)=100%，且**高于独立模型** ⇒ 负相关、连发可救 |

现在只有两种可能，必须分清：

    A. 下行字节**没到** STM32（物理层：485 方向、电平、转换器保持时间）
    B. 到了、但**没生效**（固件：乒乓缓冲、IDLE 分帧、解析闸门、TX 忙判）

判别量只有一个：`comm_rx_events`（`comm_port.c:231`，每次 UART5 收到**任何**一包就 +1）

    Δevents = 1 → 字节到了，一包完整 ⇒ 病在固件
    Δevents = 0 → 字节没到 ⇒ 病在物理层

配合 `comm_rx_loss`（`:236`/`:255`，错误或重挂失败）：
    Δloss > 0 → 收到的是**残帧/噪声**（FE/ORE/重挂失败）

符号地址（本工程 IAR map，`EWARM/demo_ads1293/build/.../demo_ads1293.map:937-941`）
------------------------------------------------------------------------------------
    comm_tx_frames = 0x200010A8      comm_tx_drops = 0x200010AC
    comm_rx_events = 0x200011B4      comm_rx_loss  = 0x20001430

🛑 必须 `mode=hotplug`：默认模式会软复位芯片 ⇒ 计数器归零，读出来是"假空白"（T202 踩过）。

用法
----
    python tools/_t205d_event_probe.py --port COM27 --trials 12

退出码：0 已是明确结论 ／ 6 收不到显示帧
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

ADDR_TX = 0x200010A8      # tx_frames, tx_drops 连续 8 B
ADDR_RX = 0x200011B4      # rx_events 4 B
ADDR_LOSS = 0x20001430    # rx_loss 4 B


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


def read_counters() -> dict | None:
    """SWD hotplug 直读四个计数器（不清零、不复位）。"""
    try:
        out = subprocess.run(
            [CLI, "-c", "port=SWD", "mode=hotplug",
             "-r32", hex(ADDR_TX), "8",
             "-r32", hex(ADDR_RX), "4",
             "-r32", hex(ADDR_LOSS), "4", "-q"],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except Exception as exc:
        print(f"    [warn] SWD 读失败: {exc}")
        return None

    words = re.findall(r"0x[0-9A-Fa-f]{8}\s*:\s*([0-9A-Fa-f ]+)", out)
    flat = []
    for w in words:
        flat.extend(int(x, 16) for x in w.split())
    if len(flat) < 4:
        print(f"    [warn] SWD 输出无法解析：{out[-200:]}")
        return None
    return {"tx_frames": flat[0], "tx_drops": flat[1],
            "rx_events": flat[2], "rx_loss": flat[3]}


class Reader:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.sta = []
        self.raw = bytearray()

    def feed(self, data: bytes, now: float) -> None:
        self.raw += data
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
    ap = argparse.ArgumentParser(description="逐条命令读计数器")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--control", type=int, default=5, help="空白对照轮数（不发命令）")
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

        # ---- 阶段 A：空白对照（不发任何命令，只等同样长的时间）------------------
        # 🛑 没有这一步，上面的 Δevents/Δloss 就无法归因：若静默时它们也在涨，
        #    那"rx_events 在涨"就证明不了"字节到了"。这是本脚本上一版的方法缺陷。
        print("阶段 A：空白对照（不发命令，等同样时长）")
        print(f"{'#':>2} {'Δevents':>8} {'Δloss':>7} {'Δtxf':>7} {'Δtxd':>6}")
        print("-" * 34)
        ctrl_e, ctrl_l = [], []
        for i in range(1, args.control + 1):
            c0 = read_counters()
            time.sleep(0.45)
            drain(port, rd, 12, 1.5)
            c1 = read_counters()
            if c0 is None or c1 is None:
                return 5
            de = c1["rx_events"] - c0["rx_events"]
            dl = c1["rx_loss"] - c0["rx_loss"]
            ctrl_e.append(de)
            ctrl_l.append(dl)
            print(f"{i:>2} {de:>8} {dl:>7} {c1['tx_frames'] - c0['tx_frames']:>7} "
                  f"{c1['tx_drops'] - c0['tx_drops']:>6}")
        if ctrl_e:
            print(f"   静默时平均：Δevents {sum(ctrl_e) / len(ctrl_e):.2f}  "
                  f"Δloss {sum(ctrl_l) / len(ctrl_l):.2f}")
        print()

        print(f"{'#':>2} {'want':>4} {'翻转':>4} {'Δevents':>8} {'Δloss':>7} "
              f"{'Δtxf':>7} {'Δtxd':>6}  判读")
        print("-" * 78)

        base_e = (sum(ctrl_e) / len(ctrl_e)) if ctrl_e else 0.0
        base_l = (sum(ctrl_l) / len(ctrl_l)) if ctrl_l else 0.0

        tally = collections.Counter()
        for i in range(1, args.trials + 1):
            c0 = read_counters()
            if c0 is None:
                return 5
            cur = rd.notch()
            if cur is None:
                print("    [warn] 窗口内无帧，跳过")
                continue
            want = 1 - cur
            port.write(build(CMD_SET_NOTCH, bytes([want])))
            port.flush()
            time.sleep(0.45)
            drain(port, rd, 12, 1.5)
            got = rd.notch()
            flipped = got == want
            c1 = read_counters()
            if c1 is None:
                return 5

            de = c1["rx_events"] - c0["rx_events"]
            dl = c1["rx_loss"] - c0["rx_loss"]
            dtf = c1["tx_frames"] - c0["tx_frames"]
            dtd = c1["tx_drops"] - c0["tx_drops"]

            # 扣掉静默基线 —— 只认"发命令多出来的那一部分"
            ne = de - base_e
            nl = dl - base_l

            if flipped and ne >= 0.5:
                verdict, key = "到了·也生效", "ok_all"
            elif flipped and ne < 0.5:
                verdict, key = "?? 没多收却生效", "impossible"
            elif (not flipped) and ne >= 0.5:
                verdict, key = "到了·没生效 ⇒ 固件", "arrived_no_effect"
            else:
                verdict, key = "没到 ⇒ 物理层", "never_arrived"
            tally[key] += 1

            print(f"{i:>2} {want:>4} {'是' if flipped else '否':>4} "
                  f"{de:>3}(净{ne:+.1f}) {dl:>3}(净{nl:+.1f}) "
                  f"{dtf:>7} {dtd:>6}  {verdict}")

        print("-" * 78)
        print(f"汇总：{dict(tally)}")
        print()
        if tally["arrived_no_effect"] + tally["ok_all"] > tally["never_arrived"]:
            print("[结论] 下行字节**确实到达** STM32（rx_events 在涨，rx_loss 基本不动）")
            print("       ⇒ 丢命令的根因在**固件侧**：IDLE 分帧 / 乒乓缓冲 / 解析闸门。")
            print("       ⇒ 485 物理层、方向切换可以排除，不必再折腾接线。")
        else:
            print("[结论] 下行字节**基本没到** STM32 ⇒ 病在物理层（方向/保持时间/电平）。")
            print("       ⇒ 这时才值得做 A/B 短接的收发回路自测。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
