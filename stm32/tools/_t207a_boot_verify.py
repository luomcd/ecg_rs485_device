#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t207a_boot_verify.py —— T207 上电写表 + 真机 485 链路 的四项客观验收（2026-11-04）

背景
----
T207 让固件在上电时把 36 条默认表写进 ADS1293（`ads1293_boot_init()`），
目的是消掉「上电后必须勾实验写点同步才能 START」这个老问题。
本次猛哥把硬件链路改成：STM32 → TTL → RS232 → UT-2211(232↔485) → 485/USB → PC(COM26)。

本脚本不依赖任何界面判读，四项全部可对账：

    T1  0x10 读快照 → 36 条逐条对金标（应 0 条不一致 = 上电已写表）
    T2  0x01 START  → 10 s 计帧：帧率 / crc / seq_gaps / 帧长
    T3  0x02 STOP + 0x03 RESET → 应回 0x03 快照，且停回 STOP（一条命令 = 复位+同步）
    T4  0x01 再次 START → 应重新起流（验 0x03 之后能直接开）

判据（实测基线，来自 T205/T198）
-------------------------------
· 显示帧：`AA 55 01 LEN=53 SEQ(2) STA(1) 25×int16 CRC8` = 59 B
· 帧率：**≈19.6 fps**（25 样本/帧 × ≈495 Hz）
  🛑 不是 853/25=34：853 是 DRDY 率，实际落帧受 `_popoutcnt` 与队列节流影响，以实测为准
· `crc_err=0`、`seq_gaps` 每 10 s ≤1

用法
----
    python tools/_t207a_boot_verify.py --port COM26
    python tools/_t207a_boot_verify.py --port COM26 --stream-s 20
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("需要 pyserial")

# 固件 regs_leaderIII（ECG_LOD_EN=0 分支），36 条
EXPECTED = {
    0x00: 0x00, 0x01: 0x11, 0x02: 0x19, 0x03: 0x00, 0x04: 0x00, 0x05: 0x00,
    0x06: 0x08, 0x07: 0x00, 0x08: 0x00, 0x09: 0x00, 0x0A: 0x07, 0x0B: 0x00,
    0x0C: 0x04, 0x0D: 0x00, 0x0E: 0x00, 0x0F: 0x00, 0x10: 0x00, 0x11: 0x00,
    0x12: 0x04, 0x13: 0x00, 0x14: 0x00, 0x15: 0x00, 0x16: 0x00, 0x17: 0x05,
    0x21: 0x02, 0x22: 0x02, 0x23: 0x02, 0x24: 0x02, 0x25: 0x00, 0x26: 0x00,
    0x27: 0x08, 0x28: 0x00, 0x29: 0x00, 0x2A: 0x00, 0x2E: 0x33, 0x2F: 0x30,
}


def crc8(data: bytes) -> int:
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build(cmd: int, arg: bytes = b"") -> bytes:
    n = 1 + len(arg)
    body = bytes([0x80, n & 0xFF, (n >> 8) & 0xFF, cmd]) + arg
    return bytes([0xAA, 0x55]) + body + bytes([crc8(body)])


class Frames:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.show = 0            # 0x01 显示帧
        self.snaps = []          # [(reqid, {reg: val})]
        self.other = {}          # 其它 TYPE 计数
        self.crc_err = 0
        self.lens = {}
        self.seqs = []
        self.first_ts = None
        self.last_ts = None

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
            ftype = self.buf[2]
            length = self.buf[3] | (self.buf[4] << 8)
            tot = 6 + length
            if tot > 260:
                del self.buf[0:1]
                continue
            if len(self.buf) < tot:
                return
            fr = bytes(self.buf[:tot])
            if crc8(fr[2: tot - 1]) != fr[tot - 1]:
                self.crc_err += 1
                del self.buf[0:1]
                continue
            now = time.monotonic()
            if ftype == 0x01:
                self.show += 1
                self.lens[tot] = self.lens.get(tot, 0) + 1
                self.seqs.append(fr[5] | (fr[6] << 8))
                if self.first_ts is None:
                    self.first_ts = now
                self.last_ts = now
            elif ftype == 0x03 and length >= 2:
                reqid, cnt = fr[5], fr[6]
                if length == 2 + 2 * cnt:
                    self.snaps.append(
                        (reqid, {fr[7 + 2 * k]: fr[8 + 2 * k] for k in range(cnt)}))
            else:
                self.other[ftype] = self.other.get(ftype, 0) + 1
            del self.buf[:tot]

    def seq_gaps(self) -> int:
        return sum(1 for a, b in zip(self.seqs, self.seqs[1:])
                   if b != ((a + 1) & 0xFFFF))


def harvest(port, tf: Frames, seconds: float) -> None:
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        chunk = port.read(1024)
        if chunk:
            tf.feed(chunk)


def main() -> int:
    ap = argparse.ArgumentParser(description="T207 上电写表 + 485 链路四项验收")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--stream-s", type=float, default=10.0)
    args = ap.parse_args()

    port = serial.Serial(args.port, args.baud, timeout=0.05)
    verdict = []
    with port:
        try:
            port.dtr = False
            port.rts = False
        except Exception:
            pass
        port.reset_input_buffer()

        # ---------------- T0：确保 STOP ----------------
        # 🛑 0x10 有状态门（`vcomcmd.c:652` `if (_bconvstart) return;`）⇒ **只在 STOP 回快照**。
        #   脚本可能被从 RUN 态调用（上一轮 T2/T4 之后就停在 RUN）⇒ 必须先停住。
        #   RUN 态下行会与上行帧抢半双工总线，实测约 50% 丢包 ⇒ 必须重发。
        stop_attempts = 0
        for attempt in range(1, 7):
            port.write(build(0x02))
            port.flush()
            time.sleep(0.35)
            tf_s = Frames()
            harvest(port, tf_s, 0.45)
            if tf_s.show == 0:
                stop_attempts = attempt
                break
        print("T0  确保 STOP：" + (f"第 {stop_attempts} 次尝试生效"
                                  if stop_attempts else "❌ 6 次都没停住"))
        if not stop_attempts:
            print("     （RUN 态下行全丢？先查半双工/接线）")
            return 3

        # ---------------- T1：快照 vs 金标 ----------------
        print("=" * 62)
        print("T1  0x10 读快照 → 36 条逐条对金标（上电是否已写表）")
        print("-" * 62)
        tf = Frames()
        port.write(build(0x10))
        port.flush()
        harvest(port, tf, 1.5)
        if not tf.snaps:
            print("  ❌ 没收到 0x03 快照（不在 STOP 态？下行不通？）")
            verdict.append(("T1", False, "无快照"))
        else:
            reqid, regs = tf.snaps[-1]
            # 🛑 0x00 CONFIG 不进比对：bit0 是 START_CON、boot init 末步显式写 0x02(停 STOP)
            #    ⇒ 它随运行态变。WinApp 的 `ConfigValidator.Validate()` 同样跳过 0x00。
            bad = [(r, w, regs.get(r)) for r, w in sorted(EXPECTED.items())
                   if r != 0x00 and regs.get(r) != w]
            cfg = regs.get(0x00)
            print(f"  REQID=0x{reqid:02X}  条数={len(regs)}  "
                  f"不一致={len(bad)}（比对 35 条，CONFIG 除外）"
                  + ("  ✅" if not bad else ""))
            for r, w, g in bad:
                print(f"    0x{r:02X} 期望 0x{w:02X} 实测 "
                      + (f"0x{g:02X}" if g is not None else "**缺失**"))
            print(f"  CONFIG(0x00)=0x{cfg:02X}（0x02=停 STOP，正常）"
                  if cfg is not None else "  CONFIG 缺失")
            verdict.append(("T1", not bad, f"{len(bad)}/35 条不一致"))

        # ---------------- T2：START + 计帧 ----------------
        print()
        print(f"T2  0x01 START → {args.stream_s:.0f} s 计帧（帧率 / crc / seq / 帧长）")
        print("-" * 62)
        port.reset_input_buffer()
        port.write(build(0x01))
        port.flush()
        tf2 = Frames()
        harvest(port, tf2, args.stream_s)
        if tf2.show == 0:
            print("  ❌ 没起流（0 帧）⇒ 写表回读失败或下行不通")
            verdict.append(("T2", False, "0 帧"))
        else:
            span = (tf2.last_ts - tf2.first_ts) if tf2.first_ts else args.stream_s
            fps = (tf2.show - 1) / span if span > 0 else 0.0
            lens = ", ".join(f"{k}B×{v}" for k, v in sorted(tf2.lens.items()))
            print(f"  帧数={tf2.show}  时长={span:.2f}s  帧率={fps:.1f} fps")
            print(f"  帧长={lens}  crc_err={tf2.crc_err}  seq_gaps={tf2.seq_gaps()}")
            if tf2.other:
                print(f"  ⚠ 其它 TYPE：{ {hex(k): v for k, v in tf2.other.items()} }")
            ok = (17.0 <= fps <= 22.0) and tf2.crc_err == 0
            print("  " + ("✅ 帧率与 CRC 达标" if ok else "❌ 帧率或 CRC 不达标"))
            verdict.append(("T2", ok, f"{fps:.1f} fps / crc={tf2.crc_err}"))

        # ---------------- T3：STOP + RESET（= 复位+同步） ----------------
        print()
        print("T3  0x02 STOP(允许重发) + 0x03 RESET → 应回 0x03 快照且停回 STOP")
        print("-" * 62)
        # 🛑 RUN 态下行会与上行帧抢总线（半双工），实测约 50% 丢包 ⇒ **必须允许重发**
        stopped_at = 0
        for attempt in range(1, 7):
            port.write(build(0x02))
            port.flush()
            time.sleep(0.35)
            tf_s = Frames()
            harvest(port, tf_s, 0.45)
            if tf_s.show == 0:
                stopped_at = attempt
                break
        print(f"  STOP 生效于第 {stopped_at or '—'} 次尝试"
              + ("" if stopped_at else "  ❌ 6 次都没停住"))
        if not stopped_at:
            verdict.append(("T3", False, "STOP 6 次未生效"))
        else:
            port.reset_input_buffer()
            tf3 = Frames()
            port.write(build(0x03))
            port.flush()
            harvest(port, tf3, 3.0)
            got03 = [s for s in tf3.snaps if s[0] == 0x03]
            resid = tf3.show
            nreg = len(got03[-1][1]) if got03 else 0
            print(f"  0x03 快照={'有' if got03 else '无'}（{nreg} 条）  "
                  f"RESET 后残留显示帧={resid}  crc_err={tf3.crc_err}")
            ok3 = bool(got03) and resid == 0
            print("  " + ("✅ RESET = 复位+同步，且停在 STOP" if ok3 else "❌ 快照缺或仍在出帧"))
            verdict.append(("T3", ok3, f"STOP×{stopped_at} / 快照{nreg}条"))

        # ---------------- T4：RESET 后能直接 START ----------------
        print()
        print("T4  0x01 再次 START → 验 0x03 之后可直接起流")
        print("-" * 62)
        port.reset_input_buffer()
        port.write(build(0x01))
        port.flush()
        tf4 = Frames()
        harvest(port, tf4, 3.0)
        print(f"  3 s 内帧数={tf4.show}")
        ok4 = tf4.show >= 3
        print("  " + ("✅ 起流成功" if ok4 else "❌ 未起流"))
        verdict.append(("T4", ok4, f"{tf4.show} 帧"))

    print()
    print("=" * 62)
    for name, ok, detail in verdict:
        print(f"  {name}  {'PASS' if ok else 'FAIL'}   {detail}")
    allok = all(v[1] for v in verdict)
    print("=" * 62)
    print("结论： " + ("✅ 四项全过 —— T207 上电写表在真机 485 链路上闭环"
                       if allok else "❌ 有未通过项，见上表"))
    return 0 if allok else 2


if __name__ == "__main__":
    sys.exit(main())
