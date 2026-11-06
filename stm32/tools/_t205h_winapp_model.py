#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205h_winapp_model.py —— 照抄 WinApp 的收发模型，看成功率是否变好（T205 第六步）

为什么
------
猛哥 11-04 两次反驳都指向同一件事：**他的 WinApp 点一下就灵，我的脚本只有 20%**。
反驳二尤其硬：「点击 START 后一直有数据上行，再点 STOP 应该不会成功才对」——
STOP 也是单发 6 字节帧，若下行真只有 20%，STOP 会频繁失效。他观察到立即生效。

所以先怀疑**我的收发模型**，不是链路。WinApp 的做法（`Core/SerialSource.cs`）：

    · 后台线程 `port.Read(bytes, 0, 4096)`，ReadTimeout = 250
    · 主线程 `port.Write(帧, 0, 8)`，**不 flush**
    · `DtrEnable = false, RtsEnable = false`

本脚本逐条对齐，只保留一个必要偏离：

    🛑 **`read(4096)` 不可用** —— pyserial 在 CH340 上 `read(4096)` 恒返回 0 字节
       （实测：1/64/256/512/1024 都正常，唯独 4096 为 0）⇒ 这里用 1024，其余照抄。

跑法
----
    python tools/_t205h_winapp_model.py --port COM27 --rounds 12

判读
----
* 若成功率跳到 ~100% ⇒ **前几轮的 20% 是我同步读循环造成的假象**，链路本身是好的。
* 若仍在 20% 上下 ⇒ 模型无关，问题在链路或固件，继续按 T205 §4 的三条病理查。
"""
import argparse
import collections
import sys
import threading
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial：pip install pyserial")


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


class WinAppLike:
    """对齐 `Core/SerialSource.cs`：后台线程大块读 + 主线程写 + DTR/RTS 拉低 + 不 flush。"""

    def __init__(self, port_name: str, baud: int) -> None:
        self.p = serial.Serial(port_name, baud, timeout=0.25)
        self.p.dtr = False
        self.p.rts = False
        self.buf = bytearray()
        self.sta = []
        self.raw = 0
        self.err = None
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while not self._stop:
            try:
                chunk = self.p.read(1024)      # 🛑 4096 在 CH340 上读不到，见文件头
            except Exception as exc:           # pragma: no cover
                self.err = repr(exc)
                return
            if chunk:
                self.raw += len(chunk)
                self._feed(chunk)

    def _feed(self, data: bytes) -> None:
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
                self.sta.append(frame[7])
            del self.buf[:total]

    def bit(self, n: int) -> int | None:
        if not self.sta:
            return None
        return (collections.Counter(self.sta[-8:]).most_common(1)[0][0] >> n) & 0x01

    def send(self, frame: bytes) -> None:
        self.p.write(frame)                    # 与 WinApp 一致：不 flush

    def close(self) -> None:
        self._stop = True
        time.sleep(0.4)
        self.p.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="照抄 WinApp 收发模型测成功率")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--gap", type=float, default=0.65)
    args = ap.parse_args()

    src = WinAppLike(args.port, args.baud)
    time.sleep(1.2)
    print(f"上行：{src.raw} B / {len(src.sta)} 帧   err={src.err}")
    if len(src.sta) < 8:
        print("       ⇒ 没有上行流，先发 START")
        for _ in range(3):
            src.send(build(0x01))
            time.sleep(0.4)
        time.sleep(1.0)
        print(f"START 后：{src.raw} B / {len(src.sta)} 帧")
        if len(src.sta) < 8:
            print("[FAIL] 仍无上行帧 ⇒ 不在 RUN 态／串口错")
            src.close()
            return 6

    print(f"起点 STA=0x{src.sta[-1]:02X}，判据＝STA.bit1 翻转")
    print()

    hits = 0
    marks = []
    for _ in range(args.rounds):
        cur = src.bit(1)
        if cur is None:
            break
        want = 1 - cur
        src.send(build(0x05, bytes([want])))
        time.sleep(args.gap)
        ok = src.bit(1) == want
        hits += 1 if ok else 0
        marks.append("1" if ok else ".")

    print(f"WinApp 同款收发：{hits}/{args.rounds} = "
          f"{hits / max(1, args.rounds) * 100:.1f}%   {' '.join(marks)}")
    print()
    if hits >= args.rounds * 0.8:
        print("[结论] 模型对齐后成功率恢复到 80%+ ⇒ 前几轮的 20% 是**同步读循环造成的假象**，")
        print("       链路本身没问题。T205 的『三种病理』需要重新审视（多半是 dump 快照的误读）。")
    else:
        print("[结论] 模型对齐后仍低 ⇒ 收发模型不是原因，问题在链路或固件，")
        print("       按 docs/T205 §4 的三条病理继续查（分帧／开头丢失／换向杂散）。")
    src.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
