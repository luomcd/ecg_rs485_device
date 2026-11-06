#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t206a_pwr_snapshot.py —— 上电后寄存器快照 vs 固件表，并验证 START 是否起流（T206）

猛哥 2026-11-04 提出的现象
--------------------------
「每次上电，ADS1293 的寄存器数据都有异常值，勾选实验写、点同步，才能正常 START。」

源码里对应的机制（已核实到行）
------------------------------
· `ads1293_start()`（`ads1293.c:804`）第③步 `ads1293_write_table(regs_leaderIII, 36)`
  **写全表 36 条并逐条回读比对**；任一条不一致 ⇒ 返回 `i+1` ⇒ **不起流、停回 STOP**，
  且这条路径**不回显**（上位机只能靠 1.5 s 超时看出来）。
· `ads1293_write_table()`（`:596`）失败时把**第一个坏项**留在
  `_wt_fail_reg / _wt_fail_want / _wt_fail_got`（T170-A 留痕，`0x20000980/81/82`）。
· `0x11 SET_REGS` 的白名单是**整批预扫**：`_reg_writable()` 任一项被拒 ⇒ **一条都不写**
  （`ads1293.c:703` 前的预扫段）。硬拒 7 条 = `0x11/0x12/0x13/0x21/0x22/0x23/0x24`
  （参考/时钟/抽率链）。所以「差异集里只要混进 1 条硬拒项，普通同步就整批作废」；
  `0x12 SET_REGS_FORCE`（实验写）`allow_protected=1` 才整批放行。

本脚本做三件事（全部实测，不猜）
--------------------------------
1. **硬复位** → 等 4 s（复位后停在 STOP，`0x10` 才受理）。
2. 发 `0x10 REQ_SNAPSHOT` → 解 `TYPE=0x03` 快照（`REQID COUNT (REG,VAL)×COUNT`），
   与固件 `regs_leaderIII` **逐条比对**，把不一致项标出来。
3. 发 `0x01 START` → 等 2 s，数显示帧：**有帧＝起流成功；无帧＝写表校验失败**。
   失败时用 SWD 读 `_wt_fail_reg/want/got`，直接告出**是第几条、期望值、实测值**。
   （此时肯定没有帧流，SWD 不会干扰任何正在进行的接收，读数可信。）

⚠ 2026-11-04 晚补记：**上位机还有一道硬闸**（`ConfigValidator.CanStart`）
   `Form1.cs:1050` ⇒ `lastValidation is not { CanStart: true }` 就直接弹「禁止 Start」、
   **一条命令都不发**。`CanStart` ＝ **H 组 11 条全一致**（`expected_regs.json` 的
   `grade=H`：0x01/0x02/0x27/0x2F + 0x11/0x12/0x13/0x21~0x24）。
   而上电后 ADS1293 停在 POR 默认值 ⇒ H 组必有差异 ⇒ **WinApp 侧「点 START 没反应」
   完全可以是这道闸，而不是芯片写不进去**。本脚本绕开 WinApp、直接发 0x01，
   正好能把两件事分开。

用法
----
    python tools/_t206a_pwr_snapshot.py --port COM27            # 先硬复位（只复位 STM32）
    python tools/_t206a_pwr_snapshot.py --port COM27 --no-reset # ★整板断电重上电后用这个
    python tools/_t206a_pwr_snapshot.py --port COM27 --no-reset --sync-retry
        └ START 失败时自动走「0x12 分批下发差异集 → 复核 → 再 START」，一次上电拿到全答案
"""
import argparse
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
ADDR_WT_FAIL = 0x20000980          # _wt_fail_reg / _wt_fail_want / _wt_fail_got

# 固件 regs_leaderIII（ECG_LOD_EN=0 分支），按 REG 升序，共 36 条
EXPECTED = {
    0x00: 0x00, 0x01: 0x11, 0x02: 0x19, 0x03: 0x00, 0x04: 0x00, 0x05: 0x00,
    0x06: 0x08, 0x07: 0x00, 0x08: 0x00, 0x09: 0x00, 0x0A: 0x07, 0x0B: 0x00,
    0x0C: 0x04, 0x0D: 0x00, 0x0E: 0x00, 0x0F: 0x00, 0x10: 0x00, 0x11: 0x00,
    0x12: 0x04, 0x13: 0x00, 0x14: 0x00, 0x15: 0x00, 0x16: 0x00, 0x17: 0x05,
    0x21: 0x02, 0x22: 0x02, 0x23: 0x02, 0x24: 0x02, 0x25: 0x00, 0x26: 0x00,
    0x27: 0x08, 0x28: 0x00, 0x29: 0x00, 0x2A: 0x00, 0x2E: 0x33, 0x2F: 0x30,
}
PROTECTED = {0x11, 0x12, 0x13, 0x21, 0x22, 0x23, 0x24}


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


def swd_bytes(addr: int, n: int = 4):
    try:
        out = subprocess.run(
            [CLI, "-c", "port=SWD", "mode=hotplug", "-r32", hex(addr), str(n), "-q"],
            capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return None
    words = re.findall(r"0x[0-9A-Fa-f]{8}\s*:\s*([0-9A-Fa-f]{8})", out)
    if not words:
        return None
    raw = b"".join(int(w, 16).to_bytes(4, "little") for w in words)
    return raw[:n]


def hw_reset() -> bool:
    try:
        r = subprocess.run([CLI, "-c", "port=SWD", "mode=hotplug", "-hardRst", "-q"],
                           capture_output=True, text=True, timeout=60)
        return "Hard reset is performed" in ((r.stdout or "") + (r.stderr or ""))
    except Exception:
        return False


class Frames:
    """同时收 0x01 显示帧与 0x03 快照帧。"""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.snaps = []
        self.show = 0

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
                del self.buf[0:1]
                continue
            if ftype == 0x01 and length in (53, 103):
                self.show += 1
            elif ftype == 0x03 and length >= 2:
                reqid = fr[5]
                cnt = fr[6]
                if length == 2 + 2 * cnt:
                    items = {}
                    for k in range(cnt):
                        items[fr[7 + 2 * k]] = fr[8 + 2 * k]
                    self.snaps.append((reqid, items))
            del self.buf[:tot]


def harvest(port, tf: Frames, seconds: float) -> None:
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        chunk = port.read(512)
        if chunk:
            tf.feed(chunk)


def send_collect(port, tf: Frames, cmd: int, arg: bytes = b"", seconds: float = 2.0) -> None:
    """发一条下行命令并只收它自己的回执（先清干净上一次的残留）。"""
    tf.buf.clear()
    tf.snaps.clear()
    tf.show = 0
    port.write(build(cmd, arg))
    port.flush()
    harvest(port, tf, seconds)


def sync_force(port, tf: Frames, items) -> list:
    """把差异集按 `0x12 SET_REGS_FORCE` 分批下发。

    🛑 每批 ≤ 28 项：`VCOM_BIN_MAX_LEN = 58` ⇒ LEN = 1+2n ≤ 58 ⇒ n ≤ 28
       (`vcomcmd.c` 分派层的门)；超了整帧被丢，且**不会**回快照。
    返回每批的回执快照（None ＝ 超时/被拒）。
    """
    payload = b"".join(bytes([r, v]) for r, v in items)
    acks = []
    for off in range(0, len(payload), 56):          # 56 B = 28 项
        chunk = payload[off: off + 56]
        send_collect(port, tf, 0x12, chunk, 1.5)
        acks.append(tf.snaps[-1] if tf.snaps else None)
        time.sleep(0.10)
    return acks


def main() -> int:
    ap = argparse.ArgumentParser(description="上电快照核对 + START 是否起流")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--sync-retry", action="store_true",
                    help="START 失败时用 0x12 分批强写差异集后重试")
    args = ap.parse_args()

    if not args.no_reset:
        print(f"[reset] 硬复位：{'OK' if hw_reset() else '未确认'}")
        time.sleep(4.0)          # 🛑 复位后必须 ≥4 s，否则会误报「无上行帧」

    port = serial.Serial(args.port, args.baud, timeout=0.05)
    with port:
        port.reset_input_buffer()

        # ---------- 1. 读快照 ----------
        port.write(build(0x10))
        port.flush()
        tf = Frames()
        harvest(port, tf, 2.0)
        if not tf.snaps:
            print("[FAIL] 没收到 0x03 快照 ⇒ 要么不在 STOP 态，要么下行没到")
            print("       （STOP 态才回快照，RUN 态静默 —— 见 vcomcmd.c:652）")
            return 5
        reqid, regs = tf.snaps[-1]
        print(f"[ok] 快照 REQID=0x{reqid:02X}  条数={len(regs)}")
        print()

        # ---------- 2. 逐条比对 ----------
        print(f"{'REG':>5} {'实测':>5} {'期望':>5}  判定")
        print("-" * 34)
        bad = []
        for r in sorted(EXPECTED):
            got = regs.get(r)
            want = EXPECTED[r]
            if got is None:
                print(f"  0x{r:02X} {'--':>5} 0x{want:02X}  **缺项**")
                bad.append((r, want, None))
            elif got != want:
                tag = "**不一致**" + ("（硬拒项）" if r in PROTECTED else "")
                print(f"  0x{r:02X} 0x{got:02X} 0x{want:02X}  {tag}")
                bad.append((r, want, got))
            else:
                print(f"  0x{r:02X} 0x{got:02X} 0x{want:02X}  ok")
        extra = sorted(set(regs) - set(EXPECTED))
        if extra:
            print(f"  ⚠ 快照里多出表外项：{[hex(x) for x in extra]}")
        print("-" * 34)
        print(f"不一致 {len(bad)} 条" + (f"：{['0x%02X' % b[0] for b in bad]}" if bad else ""))

        # ---------- 3. 先试裸 START（此刻仍是上电原状，未做任何同步） ----------
        print()
        print("--- 发 0x01 START（不做任何同步） ---")
        port.reset_input_buffer()
        port.write(build(0x01))
        port.flush()
        tf2 = Frames()
        harvest(port, tf2, 2.0)
        started = tf2.show >= 3
        if started:
            print(f"[START] ✅ 起流成功（2 s 内 {tf2.show} 帧显示帧）")
            print("        ⇒ 寄存器与金标不一致**并不妨碍**固件 START（它自己写全表 36 条）")
            print("        ⇒ WinApp 那句「禁止 Start」是**上位机 H 组硬闸**，不是芯片写不进去")
        else:
            print(f"[START] ❌ **没起流**（2 s 内仅 {tf2.show} 帧）⇒ 写表回读校验失败，停在 STOP")
            raw = swd_bytes(ADDR_WT_FAIL, 4)
            if raw and len(raw) >= 3:
                reg, want, got = raw[0], raw[1], raw[2]
                print(f"        坏项定位：REG=0x{reg:02X}  期望=0x{want:02X}  "
                      f"实测=0x{got:02X}")
                if got == 0xFD:
                    print("        （got=0xFD ⇒ 写失败；0xFE ⇒ 读失败；0xFF ⇒ 未记录）")
                if reg in PROTECTED:
                    print("        ⚠ 该项属固件硬拒 7 条（参考/时钟/抽率链）⇒ 只能靠 0x12 强写")

        # ---------- 4. 可选：0x12 分批强写差异集 → 复核 → 再 START ----------
        if args.sync_retry:
            print()
            print("--- 0x12 SET_REGS_FORCE 分批下发差异集（排除 0x00 CONFIG） ---")
            items = [(r, w) for (r, w, _g) in bad if r != 0x00]
            if not items:
                print("        差异集为空 ⇒ 无项可发")
            else:
                print(f"        差异集 {len(items)} 项：{['0x%02X' % r for r, _ in items]}")
                acks = sync_force(port, tf, items)
                for k, ack in enumerate(acks):
                    if ack is None:
                        print(f"        批{k + 1}: ❌ 无回执（被拒或超时）")
                    else:
                        rid, ack_items = ack
                        print(f"        批{k + 1}: ✅ 回执 REQID=0x{rid:02X} "
                              f"回读 {len(ack_items)} 项")
                time.sleep(0.10)
                # 复核
                send_collect(port, tf, 0x10, b"", 2.0)
                if tf.snaps:
                    _rid, regs2 = tf.snaps[-1]
                    left = [(r, w) for (r, w, _g) in bad
                            if r != 0x00 and regs2.get(r) != w]
                    print(f"        复核：仍不一致 {len(left)} 条"
                          + (f"：{['0x%02X' % r for r, _ in left]}" if left else "  ✅ 已归零"))
                else:
                    print("        复核：❌ 又没收到快照")
                # 再 START
                print("--- 同步后再发 0x01 ---")
                send_collect(port, tf, 0x01, b"", 2.0)
                if tf.show >= 3:
                    print(f"[START] ✅ 同步后起流成功（{tf.show} 帧）")
                else:
                    print(f"[START] ❌ 同步后仍不起流（{tf.show} 帧）⇒ 不是寄存器值的问题")

    print()
    print("=" * 60)
    if not args.no_reset:
        print("[提示] 本次是 **STM32 NRST 复位**，ADS1293 不受 NRST 影响（芯片仍停在 RUN 后的")
        print("       状态）⇒ 复现不了「上电寄存器异常」。要复现必须**整板断电重上电**，")
        print("       并加 --no-reset 立刻跑本脚本。")
    elif bad:
        print("[判读] 这是**真·上电（POR）**状态，其中 " + str(len(bad)) + " 条与金标不一致：")
        print("       " + ", ".join("0x%02X" % b[0] for b in bad))
        hard = [b for b in bad if b[0] in PROTECTED or b[0] in (0x01, 0x02, 0x27, 0x2F)]
        print(f"       其中 H 组硬闸 {len(hard)} 条 ⇒ WinApp 的 "
              f"`CanStart` 必为 false ⇒ **界面拦 Start**")
    else:
        print("[判读] 上电后 36 条全部与固件表一致 ⇒ 本次未见异常值。")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
