#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S9a 端到端联调：COM35 起 485 源 → COM27 发波形 → 读回对账（单进程两头管）。

为什么必须一个进程管两个口：
  🛑 open/close COM35 会让 ESP32 复位（DTR/RTS 行为），板子重来一遍就丢掉
     「485 模式」这个运行时状态。如果分成两个脚本跑，发完数据再去抓 stat 时
     板子已经被复位了，看到的永远是源=FILE、rx=0。所以本脚本一次开两个口、
     保持 ESP 口不断，中途只通过它发命令/收日志。

用法：
  # ① 回放文件（S9a 现状）：PC 从 ecg.bin 发包
  python tools/s9a_link_check.py                      # 默认发 20 s（1x）
  python tools/s9a_link_check.py --seconds 90         # 发满一整份 ecg.bin（89.8 s）
  python tools/s9a_link_check.py --no-start           # 板上已在 485 模式，只发+对账
  python tools/s9a_link_check.py --file .../alt-120bpm.bin --seconds 30

  # ② 对接真实源（S9b）：STM32 的 485 挂在另一个 USB 口上，本脚本原样转发
  python tools/s9a_link_check.py --from-port COM12                # STM32→COM12→本脚本→COM27→ESP32
  python tools/s9a_link_check.py --from-port COM12 --passive      # 只旁听（PC 转换器与 ESP32 同挂总线）

两类源的区别只有"字节从哪来"：一个来自磁盘回放，一个来自另一个串口。
对账判据、485-stat 口径、UI 行为完全一致。

退出码：0 = 全绿；1 = 某条判据不过（细节见输出）。
"""
import argparse
import os
import re
import sys
import threading
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs485_stream import split_frames, check_file, StreamParser  # noqa: E402  同一份切帧口径

PROMPT = b"cardiolite>"


class EspPort:
    """ESP32 console 口：读线程常驻，命令按 prompt 判定收工。"""

    def __init__(self, port, baud=115200):
        self.ser = serial.Serial(port, baud, timeout=0.05, write_timeout=2.0)
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.stop = False
        self.th = threading.Thread(target=self._reader, daemon=True)
        self.th.start()

    def _reader(self):
        while not self.stop:
            try:
                n = self.ser.in_waiting
                if n:
                    d = self.ser.read(n)
                    with self.lock:
                        self.buf.extend(d)
                else:
                    time.sleep(0.01)
            except Exception:
                return

    def take(self) -> bytes:
        with self.lock:
            d = bytes(self.buf)
            self.buf.clear()
        return d

    def wait_prompt(self, timeout=10.0) -> bool:
        end = time.time() + timeout
        acc = bytearray()
        while time.time() < end:
            acc.extend(self.take())
            if PROMPT in acc:
                return True
            time.sleep(0.05)
        return False

    def cmd(self, line: str, settle=2.0) -> str:
        """发一条命令，收到 prompt 后再收 settle 秒，返回可读文本。"""
        self.take()
        self.ser.write((line + "\r\n").encode())
        self.ser.flush()
        end = time.time() + 8.0
        acc = bytearray()
        got_prompt = False
        while time.time() < end:
            acc.extend(self.take())
            if not got_prompt and PROMPT in acc:
                got_prompt = True
                end = time.time() + settle
            time.sleep(0.05)
        return acc.decode("utf-8", "replace")

    def close(self):
        self.stop = True
        time.sleep(0.05)
        try:
            self.ser.close()
        except Exception:
            pass


def send_frames(bus, frames, speed, seconds, log_every=5.0):
    """按实时速率发帧（每帧 样本数/500/speed 秒），返回 (帧数, 字节数)。"""
    t0 = time.time()
    next_t = t0
    sent_f = sent_b = 0
    last_log = t0
    idx = 0
    while True:
        frame, n_ch1, _seq = frames[idx]
        bus.write(frame)
        sent_f += 1
        sent_b += len(frame)
        idx += 1
        next_t += n_ch1 / 500.0 / max(speed, 0.01)
        now = time.time()
        if next_t > now:
            time.sleep(next_t - now)
        else:
            next_t = now
        if now - last_log >= log_every:
            last_log = now
            print(f"  [发送 {now - t0:5.1f}s] 帧 {sent_f:5d}  {sent_b / 1024:7.2f} KB",
                  flush=True)
        if seconds > 0 and now - t0 >= seconds:
            break
        if idx >= len(frames):
            idx = 0        # 发完就循环（屏上连续滚动，便于肉眼判断）
    return sent_f, sent_b


def run_from_port(a, esp=None):
    """从 --from-port 实时读，原样转发到 --bus（或 --passive 只旁听），同时做帧级统计。

    两个拓扑都覆盖：
      转发：STM32 → COMxx（USB-485）→ 本脚本 → COM27(485) → ESP32
      旁听：STM32 → 485 总线 → ESP32，PC 的转换器挂同一总线只听（--passive，不开 bus）
    🛑 不重新打包、不改时序：字节原样转发，统计只旁路观察 —— 这样 PC 上的
       帧级日志与 ESP32 收到的完全是同一串字节，对账才有意义。

    返回 (源端 StreamParser, 中途采到的 ESP 侧 stat 列表, 实际运行秒数)。
    """
    src = serial.Serial(a.from_port, a.baud, timeout=0.05)
    dst = None
    if not a.passive:
        dst = serial.Serial(a.bus, a.baud, timeout=0.2, write_timeout=2.0)
        time.sleep(0.1)
        dst.reset_input_buffer()

    st = StreamParser()
    esp_stats = []
    t0 = time.time()
    last = last_esp = t0
    print(f"  源 {a.from_port} → {'转发到 ' + a.bus if dst else '旁听（不转发，与 ESP32 同挂总线）'}"
          f"，跑 {a.seconds:g} s", flush=True)
    try:
        while True:
            d = src.read(4096)
            if d:
                st.feed(d)
                if dst is not None:
                    dst.write(d)
            now = time.time()
            if now - last >= 5.0:
                last = now
                el = max(now - t0, 1e-9)
                print(f"  [{el:5.1f}s] {st.summary()}  {st.frames / el:.1f} 帧/s",
                      flush=True)
            # 中途抓一次 ESP 侧，能在"仍在跑"的时候定位是源没发、还是板子丢
            if esp is not None and a.esp_stat_every > 0 and now - last_esp >= a.esp_stat_every:
                last_esp = now
                g = parse_stat(esp.cmd("485-stat", settle=0.5))
                esp_stats.append(g)
                print(f"           └ 板侧 rx={g.get('rx', -1)} B  frames={g.get('frames', -1)}"
                      f"  crc_err={g.get('crc_err', -1)}  dropped={g.get('dropped', -1)}",
                      flush=True)
            if a.seconds > 0 and now - t0 >= a.seconds:
                break
    except KeyboardInterrupt:
        print("\n  中断")
    finally:
        src.close()
        if dst is not None:
            dst.close()
    print(f"  源端统计：{st.summary()}")
    return st, esp_stats, max(time.time() - t0, 1e-9)


def parse_stat(text: str):
    g = {}
    m = re.search(r"rx=(\d+) B", text)
    if m:
        g["rx"] = int(m.group(1))
    for key in ("frames", "crc_err", "resync", "bad_len", "seq_gaps"):
        m = re.search(key + r"=(\d+)", text)
        if m:
            g[key] = int(m.group(1))
    m = re.search(r"pushed=(\d+)\s+dropped=(\d+)", text)
    if m:
        g["pushed"] = int(m.group(1))
        g["dropped"] = int(m.group(2))
    m = re.search(r"bpm=([\d.]+)", text)
    if m:
        g["bpm"] = float(m.group(1))
    g["src_485"] = ("RS485" in text)
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--esp", default="COM35", help="ESP32 console 口")
    ap.add_argument("--bus", default="COM27", help="485 侧串口（回放时是本脚本发送用的口）")
    ap.add_argument("--file", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "cardiolite-ecg", "sdcard", "ecg.bin"),
        help="回放源（给了 --from-port 则忽略）")
    ap.add_argument("--baud", type=int, default=115200, help="485 链路波特率（两端必须一致）")
    ap.add_argument("--esp-baud", type=int, default=115200,
                    help="ESP32 console 波特率（走 USB-JTAG，实际不生效，仅接口完整）")
    ap.add_argument("--speed", type=float, default=1.0, help="回放倍速（仅 --file 模式）")
    ap.add_argument("--seconds", type=float, default=20.0,
                    help="运行时长（--file 模式=发送时长，0=一圈就停）")
    ap.add_argument("--no-start", action="store_true", help="跳过 485-start（板上已在 485 模式）")
    ap.add_argument("--from-port", default=None,
                    help="从该串口实时读帧并转发到 --bus（对接 STM32 用；不给则从 --file 回放）")
    ap.add_argument("--passive", action="store_true",
                    help="配合 --from-port：只旁听不转发（PC 转换器与 ESP32 同挂一条 485 总线）")
    ap.add_argument("--esp-stat-every", type=float, default=10.0,
                    help="--from-port 模式下每 N 秒抓一次板上 485-stat（0=不抓）")
    a = ap.parse_args()

    from_port = bool(a.from_port)

    frames = None
    if not from_port:
        if not os.path.exists(a.file):
            print(f"✗ 文件不存在: {a.file}")
            return 1
        print(f"== 数据文件 ==")
        if check_file(a.file) != 0:
            print("✗ 文件自检未通过（CRC 错），先修数据")
            return 1
        frames, _, _ = split_frames(open(a.file, "rb").read())
    else:
        print(f"== 源 = {a.from_port}（实时）==")
        print("  模式：" + ("旁听，不转发（PC 与 ESP32 同挂 485 总线）" if a.passive
                          else f"原样转发 → {a.bus}"))

    print(f"\n== 打开 {a.esp}（会复位板子，等 REPL）==")
    try:
        esp = EspPort(a.esp, a.esp_baud)
    except Exception as e:
        print(f"✗ 打不开 {a.esp}: {e}")
        print("  ⇒ 关掉占用该口的串口助手/终端再试")
        return 1
    if not esp.wait_prompt(10.0):
        esp.ser.write(b"\r\n")
        if not esp.wait_prompt(5.0):
            print("✗ 没等到 cardiolite> 提示符")
            esp.close()
            return 1
    print("  REPL 就绪")

    if not a.no_start:
        print(f"\n== 485-start {a.baud} ==")
        out = esp.cmd(f"485-start {a.baud}", settle=1.5)
        print("  " + "\n  ".join(l for l in out.splitlines() if l.strip())[:800])
        if "已启动" not in out:
            print("✗ 485 源没起来，后续无意义")
            esp.close()
            return 1

    # ---------- 路径 A：从 --from-port 实时收（对接 STM32） ----------
    if from_port:
        # 板侧基线：起跑前快照，之后只看增量。转发模式下增量应 == 源端计数；
        # 旁听模式是"中途加入总线"，绝对计数天然比板子少（加入前板子已在收），
        # 所以旁听只能比「帧率」，不能比「帧数」——这点已在 --passive 的判据里区分。
        pre = parse_stat(esp.cmd("485-stat", settle=0.6))
        print(f"\n== 实时收 {a.seconds:g} s ==")
        src_st, _mid, elapsed = run_from_port(a, esp)
        time.sleep(0.3)
        print(f"\n== 485-stat 对账 ==")
        stat_txt = esp.cmd("485-stat", settle=2.0)
        print("  " + "\n  ".join(l for l in stat_txt.splitlines() if l.strip()))
        esp.close()
        st = parse_stat(stat_txt)

        def d(key, default_missing=-1):
            """板侧增量 = 结束时 - 起跑前（起跑前缺失则按 0 起）"""
            if key not in st:
                return default_missing
            return st[key] - pre.get(key, 0)

        d_frames, d_rx = d("frames"), d("rx")
        d_crc, d_gaps, d_resync = d("crc_err"), d("seq_gaps"), d("resync")
        src_fps = src_st.frames / elapsed
        esp_fps = d_frames / elapsed

        if a.passive:
            print(f"\n== 判据（旁听模式：按帧率比，不比绝对帧数）==")
            print(f"  源 {src_fps:.2f} 帧/s   板 {esp_fps:.2f} 帧/s   "
                  f"（PC 加入时刻晚于板子，绝对帧数差 {d_frames - src_st.frames} 属正常）")
            checks = [
                ("源 = RS485", st.get("src_485", False)),
                (f"帧率一致（{src_fps:.2f} vs {esp_fps:.2f} 帧/s，差 ≤5%）",
                 esp_fps > 0 and abs(src_fps - esp_fps) <= 0.05 * esp_fps),
                (f"板侧增量 ≥ 源端帧数（{d_frames} ≥ {src_st.frames}，否则是板子丢帧）",
                 d_frames >= src_st.frames),
                (f"crc_err 两侧皆 0（源 {src_st.crc_err} / 板 {d_crc}）",
                 src_st.crc_err == 0 and d_crc == 0),
                (f"resync = 0（源 {src_st.resync} / 板 {d_resync}）",
                 src_st.resync == 0 and d_resync == 0),
                ("dropped = 0", st.get("dropped", -1) == 0),
            ]
            return report(checks, st)

        print("\n== 判据（转发模式：源端 vs 板侧增量，逐字节可比）==")
        checks = [
            ("源 = RS485", st.get("src_485", False)),
            (f"帧数一致（源 {src_st.frames} vs 板 {d_frames}，差 ≤2）",
             abs(d_frames - src_st.frames) <= 2),
            (f"字节一致（源 {src_st.bytes} vs 板 {d_rx}，差 ≤2 帧）",
             abs(d_rx - src_st.bytes) <= 2 * 59),
            (f"crc_err 两侧皆 0（源 {src_st.crc_err} / 板 {d_crc}）",
             src_st.crc_err == 0 and d_crc == 0),
            (f"seq_gaps ≤1（源 {src_st.seq_gaps} / 板 {d_gaps}）",
             src_st.seq_gaps <= 1 and d_gaps <= 1),
            (f"resync = 0（源 {src_st.resync} / 板 {d_resync}）",
             src_st.resync == 0 and d_resync == 0),
            ("dropped = 0", st.get("dropped", -1) == 0),
        ]
        return report(checks, st)

    # ---------- 路径 B：从 --file 回放（S9a 现状） ----------
    print(f"\n== 打开 {a.bus} 并发送 {a.seconds:g} s ==")
    try:
        bus = serial.Serial(a.bus, a.baud, timeout=0.2, write_timeout=2.0)
    except Exception as e:
        print(f"✗ 打不开 {a.bus}: {e}")
        esp.close()
        return 1
    time.sleep(0.1)
    bus.reset_input_buffer()
    sent_f, sent_b = send_frames(bus, frames, a.speed, a.seconds)
    bus.close()
    print(f"  发送完成：{sent_f} 帧 / {sent_b} B")

    time.sleep(0.3)
    print(f"\n== 485-stat 对账 ==")
    stat_txt = esp.cmd("485-stat", settle=2.0)
    print("  " + "\n  ".join(l for l in stat_txt.splitlines() if l.strip()))
    esp.close()

    st = parse_stat(stat_txt)
    print("\n== 判据 ==")
    checks = [
        ("源 = RS485", st.get("src_485", False)),
        (f"frames ≈ 发送帧数（{st.get('frames', -1)} vs {sent_f}，差 ≤2）",
         abs(st.get("frames", -1) - sent_f) <= 2),
        ("crc_err = 0", st.get("crc_err", -1) == 0),
        ("seq_gaps = 0（首帧可能有 1 次，属正常起点）", st.get("seq_gaps", 99) <= 1),
        ("dropped = 0", st.get("dropped", -1) == 0),
        (f"rx ≈ 发送字节数（{st.get('rx', -1)} vs {sent_b}，差 ≤2 帧）",
         abs(st.get("rx", -1) - sent_b) <= 2 * 59),
    ]
    return report(checks, st)


def report(checks, st) -> int:
    """统一打印判据表并给退出码。"""
    ok = True
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    if st.get("bpm", 0) > 0:
        print(f"  [INFO] BPM = {st['bpm']:.1f}")
    print("\n" + ("全绿：链路打通，波形应已在屏上滚动" if ok else "有判据未过，见上表"))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # 中断点可能在发包中途、也可能在等 prompt 时；两种情况都别吐 traceback。
        # 串口句柄由系统在进程退出时回收，可以直接重跑；但板子的「485 模式」是
        # 运行时状态，不会自己恢复 —— 必须显式 485-stop 才回到 SD 回放。
        print("\n中断（Ctrl+C）")
        print("  ⚠ 板子仍停在 485 模式：需要时在串口里敲 485-stop 回到 SD 回放")
        print("  串口已由系统回收，可直接重跑本脚本")
        sys.exit(130)
