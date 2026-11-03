#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t204_cmd_probe.py —— T204 下行命令链路探针（SET_MODE 0x04 / SET_NOTCH 0x05）

为什么要这个脚本
----------------
T203 已经证到「上行通」（显示帧 → 485 → PC，WinApp 出波形），但**下行通不通是另一件事**，
而且本次链路上有它自己的物理嫌疑：

    232↔485 转换器（UT-2211 一类）是**半双工自动方向**器件 —— 它靠总线空闲来把方向翻过来。
    而显示帧是**近似连续**的上行流（20 帧/s × 59 B ≈ 1180 B/s，帧间隔 50 ms、单帧占线
    5.1 ms ⇒ 每个间隙只有 45 ms）。若器件的方向保持时间（典型 1~5 ms 量级，但差型号
    可到十几 ms）压不住，PC 下发的 8 B 小帧就可能**整帧被吞**。

所以"能收到"不是显然的，必须实测。**判据不能是"点了没报错"**，必须是固件回传的 STA 位。

固件侧这条链（已核过源码，与本次改造无关，零改动）
-------------------------------------------------
    UART5 IDLE 中断 → `HAL_UARTEx_RxEventCallback`（`comm_port.c:213`）
      → `vcom_recv()` 只存指针/长度
      → 主循环 `vcom_recv_analysis()`（`vcomcmd.c:824`）
      → 前两字节 = AA 55 ⇒ `vcom_bin_dispatch()`（`vcomcmd.c:606`）
      → `case 0x04` / `case 0x05`（`vcomcmd.c:706` / `:727`）⇒ `ecg_filter_f64_set_*`

两条命令都**不加状态门**、`argLen` 必须 == 2；ACK 不是回帧，而是「**下一帧显示帧的
STA 字节跟随**」—— 这正是本脚本的观测点。

帧格式（协议 v2.2 §5）
----------------------
下行：`AA 55 | 80 | LEN(2B LE) | CMD | ARG | CRC8`，LEN = 1 + N，总长 = 6 + LEN
上行显示帧：`AA 55 | 01 | LEN=53 | SEQ(2) | STA(1) | 25×int16 | CRC8`，总长 59
            ⇒ **STA = frame[7]**（更稳的算法：payload 起点 = 5，STA = payload[2]）

STA 位（`Core/Protocol.cs:8-15`）
---------------------------------
    bit0 Mode   : 0 = 诊断 diag ／ 1 = 监护 mon
    bit1 Notch  : 0 = 关 ／ 1 = 开
    bit2 Channel: 0 = I ／ 1 = I&II
    bit3 Raw853 / bit4 Lod

用法
----
    # 先关掉 WinApp（串口独占），再跑：
    python tools/_t204_cmd_probe.py --port COM27
    python tools/_t204_cmd_probe.py --port COM27 --frames 40 --repeat 3
    python tools/_t204_cmd_probe.py --port COM27 --start       # STOP 态（无显示帧）先起流

退出码：0 四条命令全部跟随 ／ 5 至少一条未跟随 ／ 6 收不到显示帧（上行/端口问题）

🛑 本脚本**只发 0x04 / 0x05**（外加可选 0x01 START）；不发 STOP、不发 SET_REGS，
   不会改动 AFE 配置、不会中断已开始的采集。
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
    """打包下行帧，CRC 覆盖 TYPE + LEN(2) + CMD + ARG。"""
    length = 1 + len(arg)
    body = bytes([0x80, length & 0xFF, (length >> 8) & 0xFF, cmd]) + arg
    return bytes([0xAA, 0x55]) + body + bytes([crc8(body)])


CMD_START = 0x01
CMD_SET_MODE = 0x04
CMD_SET_NOTCH = 0x05


class FrameReader:
    """滚动解析器：只认上行 TYPE=0x01（显示帧）/ 0x03（快照），其余整段丢弃。

    🛑 为什么要自己解析、不复用 WinApp 的 FrameParser：这里要拿到**逐帧 STA 序列**
    （看它何时翻、翻了几帧），而不是"最后一帧的 STA"—— 因为 485 上可能出现
    "命令被吞但下一帧 STA 又被别的东西改回去"这种瞬态，只看末值会漏。
    """

    def __init__(self) -> None:
        self.buf = bytearray()
        self.frames = 0
        self.bad_crc = 0
        self.other = 0
        self.sta_hist = collections.Counter()
        self.sta_seq = []          # [(时间戳, STA)]，按到达顺序
        self.t_first = None
        self.t_last = None
        self.seq_seq = []          # 帧序号，用来算 SEQ gap（真丢帧）

    def feed(self, data: bytes, now: float) -> None:
        self.buf += data
        while True:
            idx = self.buf.find(b"\xAA\x55")
            if idx < 0:
                # 没有帧头：只留最后 1 B（可能是半个 AA）
                if len(self.buf) > 1:
                    del self.buf[: len(self.buf) - 1]
                return
            if idx > 0:
                del self.buf[:idx]        # 帧前垃圾（含 485 回读的命令帧）直接丢
            if len(self.buf) < 5:
                return
            ftype = self.buf[2]
            length = self.buf[3] | (self.buf[4] << 8)
            total = 6 + length
            if total > 200:               # 长度离谱 ⇒ 假帧头，跳过 AA 重找
                del self.buf[0:1]
                self.other += 1
                continue
            if len(self.buf) < total:
                return
            frame = bytes(self.buf[:total])
            if crc8(frame[2 : total - 1]) != frame[total - 1]:
                del self.buf[0:1]
                self.bad_crc += 1
                continue
            if ftype == 0x01 and length in (53, 103):
                self.frames += 1
                sta = frame[7]
                self.sta_hist[sta] += 1
                self.sta_seq.append((now, sta))
                self.seq_seq.append(frame[5] | (frame[6] << 8))
                if self.t_first is None:
                    self.t_first = now
                self.t_last = now
            elif ftype == 0x03:
                self.other += 1
            else:
                self.other += 1
            del self.buf[:total]

    # ---- 由**实测**到达时刻算速率 -------------------------------------------------
    def span(self) -> float:
        if self.t_first is None or self.t_last is None or self.t_last <= self.t_first:
            return 0.0
        return self.t_last - self.t_first

    def fps(self) -> float:
        span = self.span()
        return (self.frames - 1) / span if span > 0 else 0.0

    def sample_rate(self) -> float:
        """样本率 = 帧率 × 25（LEN=53 单通道帧）。"""
        return self.fps() * 25.0

    def seq_gaps(self) -> collections.Counter:
        gaps = collections.Counter()
        for i in range(len(self.seq_seq) - 1):
            gaps[(self.seq_seq[i + 1] - self.seq_seq[i]) & 0xFFFF] += 1
        return gaps


def collect(port, min_frames: int = 20, timeout: float = 8.0) -> FrameReader:
    """**按帧数收**，不按墙钟收 —— 这是本脚本踩过的坑。

    🛑 教训（T204 首测）：原来写的是「读满 1.5 s」，但 `port.read(4096)` 在 CH340
    上实测会阻塞到 **3.49 s**（超时语义与 pyserial 标称不符）⇒ 帧数照收、时间却按
    1.5 s 算 ⇒ 帧率被高估 2.3 倍（报成 46 fps，真值 19.6 fps，样本率 495 Hz 才对得上
    500 Hz 降采样输出）。**凡是要算速率，分母必须是首末帧的实测到达时刻差。**
    """
    reader = FrameReader()
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end and reader.frames < min_frames:
        # ⚠ 收 64 B（≈1.1 帧）而不是 4096：批量读会把「一次 read 里解析出的多帧」
        #   打上同一个时间戳 ⇒ 首末帧跨度被压缩 ⇒ 帧率被高估。64 B 下时间戳粒度 ≈ 一帧。
        chunk = port.read(64)
        if chunk:
            reader.feed(chunk, time.monotonic())
    return reader


def mode_of(sta: int) -> int:
    return sta & 0x01


def notch_of(sta: int) -> int:
    return (sta >> 1) & 0x01


def describe(sta: int) -> str:
    return "工况 {0}·陷波{1}".format(
        "监护" if mode_of(sta) else "诊断", "开" if notch_of(sta) else "关"
    )


def steady_sta(reader: FrameReader, tail: int = 10) -> int | None:
    """取窗口内**最后若干帧**的众数 —— 抗"命令生效前一帧还在路上"的边界情况。"""
    if not reader.sta_seq:
        return None
    tail_sta = [s for _, s in reader.sta_seq[-tail:]]
    return collections.Counter(tail_sta).most_common(1)[0][0]


# ============================ 主流程 ======================================

STEPS = [
    # (说明, 下行帧, 判据字段)  —— 判据全部来自固件回传的 STA 位，不看"点了没报错"
    ("SET_NOTCH(off=0)", build(CMD_SET_NOTCH, b"\x00"), "notch:0"),
    ("SET_NOTCH(on=1)",  build(CMD_SET_NOTCH, b"\x01"), "notch:1"),
    ("SET_MODE(mon=1)",  build(CMD_SET_MODE,  b"\x01"), "mode:1"),
    ("SET_MODE(diag=0)", build(CMD_SET_MODE,  b"\x00"), "mode:0"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="T204 下行命令链路探针")
    ap.add_argument("--port", required=True, help="例如 COM27")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--frames", type=int, default=25, help="每一步收多少帧再判定（25 帧≈1.3 s）")
    ap.add_argument("--timeout", type=float, default=8.0, help="单步最长等待（秒）")
    ap.add_argument("--repeat", type=int, default=3, help="每条命令重发次数（半双工可能吞帧）")
    ap.add_argument("--start", action="store_true", help="没有显示帧时先发 START")
    args = ap.parse_args()

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.1)
    except Exception as exc:
        print(f"[FAIL] 打不开 {args.port}: {exc}")
        print("       ↑ PermissionError 基本都是别的程序（WinApp／串口助手）占着")
        return 6

    with port:
        port.reset_input_buffer()

        # ---- 阶段 0：基线 ----
        base = collect(port, args.frames, args.timeout)
        if base.frames == 0:
            if args.start:
                print("[..] 没有显示帧 ⇒ 当前多半是 STOP 态，发 START 起流")
                port.write(build(CMD_START))
                port.flush()
                time.sleep(0.2)
                base = collect(port, args.frames, args.timeout)
            if base.frames == 0:
                print(f"[FAIL] 收不到显示帧（坏CRC={base.bad_crc} 其它帧={base.other}）")
                print("       ⇒ STOP 态（先 --start）／波特率不对／串口选错。先解决上行。")
                return 6

        sta0 = steady_sta(base)
        print(f"[ok] 基线：{base.frames} 帧，实测跨度 {base.span():.2f} s "
              f"⇒ {base.fps():.2f} fps，样本率 {base.sample_rate():.1f} Hz")
        print(f"     STA=0x{sta0:02X}（{describe(sta0)}）  分布 "
              f"{ {f'0x{k:02X}': v for k, v in base.sta_hist.items()} }")
        gaps = base.seq_gaps()
        lost = sum((g - 1) * n for g, n in gaps.items() if g > 1)
        print(f"     SEQ gap {dict(gaps)}  坏CRC {base.bad_crc} 个 ⇒ 本轮丢 {lost} 帧")
        print()

        # ---- 阶段 1：逐条命令 ----
        rows = []
        ok_all = True
        for name, frame, field in STEPS:
            print(f"[->] {name}   帧＝{frame.hex(' ').upper()}")
            for _ in range(max(1, args.repeat)):
                port.write(frame)
                port.flush()
                time.sleep(0.08)

            window = collect(port, args.frames, args.timeout)
            sta_now = steady_sta(window)
            if sta_now is None:
                print("     ✗ 窗口内 0 帧（上行中断？）")
                rows.append((name, "-", "-", "无帧"))
                ok_all = False
                continue

            want = field.split(":")[1]
            got = str(notch_of(sta_now) if field.startswith("notch") else mode_of(sta_now))
            good = got == want
            ok_all &= good
            flag = "✓" if good else "✗"
            print(f"     {flag} STA=0x{sta_now:02X}（{describe(sta_now)}）"
                  f"  期望 {field.split(':')[0]}={want} 实测={got}  [{window.frames} 帧]")
            rows.append((name, f"0x{sta_now:02X}", f"{field.split(':')[0]}={got}", flag))

        # ---- 结论 ----
        print()
        print("=" * 66)
        print(f"{'命令':<20}{'之后STA':<10}{'实测':<12}{'判定'}")
        print("-" * 66)
        for name, sta, got, flag in rows:
            print(f"{name:<20}{sta:<10}{got:<12}{flag}")
        print("=" * 66)

        if ok_all:
            print("[PASS] 四条命令全部生效 ⇒ 下行链路（PC → 485 → 232 → UART5）通，")
            print("       且 STM32 解析、内核 setter 全部正常。半双工方向抢占**未发生**。")
            return 0

        print("[FAIL] 至少一条未跟随。分辨方法：")
        print("  ① 若无帧 ⇒ 上行断了（与本命令无关）")
        print("  ② 若帧正常但 STA 不变 ⇒ 下行字节没到 STM32 或没被解析：")
        print("     · 用调试器读 comm_rx_events（有符号就用符号，别用绝对地址）：")
        print("       点一次按钮它 +1 ⇒ 字节到了，问题在解析/内核；纹丝不动 ⇒ 字节没过来。")
        print("     · 没过来优先怀疑 485 半双工方向抢占：把 --repeat 提到 10 再试；")
        print("       仍不行则在 WinApp 里 STOP 后连点（STOP 态无上行流，方向必然空闲）做对照。")
        return 5


if __name__ == "__main__":
    sys.exit(main())
