#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t205b_phase_sweep.py —— RUN 态下行成功率 vs **发送相位**（T205 第二步）

上一步（`_t205_prefix_probe.py`）结论
------------------------------------
k=0/1/2/3 四组前缀补偿，只有 **k=0 无前缀**能通（2/6），k≥1 全 0/18
⇒ 下行帧**没有丢首字节**，帧格式、CRC、长度全部正确。
但成功率只有 ~33% ⇒ 是**间歇性被挡**，不是"永远被挡"。

本步要回答的问题
----------------
如果是「485 半双工方向在**上行流**期间无法翻转」（自动方向转换器的典型病），
那么失败率应当**与发送相位强相关**：刚发完一帧上行（总线刚释放）时最容易通，
靠近下一帧上行时最容易撞车。

做法：以「收到一帧上行显示帧」为时间零点，扫 `--delay` 0…50 ms，
每个相位重复若干次，看成功率随相位的分布。

判据
----
* 若成功率在某段相位**系统性为 0**，另一段**系统性为 1** ⇒ 确认是方向抢占，
  且**空白段宽度 ≈ 转换器保持方向的时长**（这是选型/加延时的依据）。
* 若各相位成功率都在 1/3 上下、看不出结构 ⇒ 不是相位问题，转去查 RX 事件
  （`comm_rx_events` / `comm_rx_loss`）。

同时测量线路占用（排除"还有别的数据在占线"这一可能）
--------------------------------------------------
打印「原始字节数 / 已解析帧字节数」的比值。若远大于 1，说明线上除显示帧之外
还有其它流量（ASCII 打印、噪声、回读），那会解释为什么占线率远高于 10%。

用法
----
    python tools/_t205b_phase_sweep.py --port COM27
    python tools/_t205b_phase_sweep.py --port COM27 --delay-step 2 --trials 3
"""
import argparse
import collections
import sys
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


CMD_SET_NOTCH = 0x05


class Reader:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.sta = []
        self.bad = 0
        self.raw = 0
        self.frame_bytes = 0
        self.t_last_frame = None

    def feed(self, data: bytes, now: float) -> None:
        self.raw += len(data)
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
                self.bad += 1
                continue
            if ftype == 0x01 and length in (53, 103):
                self.sta.append((now, frame[7]))
                self.frame_bytes += total
                self.t_last_frame = now
            del self.buf[:total]


def notch_of(sta: int) -> int:
    return (sta >> 1) & 0x01


def wait_next_frame(port, rd: Reader, timeout: float = 3.0) -> bool:
    """阻塞到**新的一帧**到达（以帧为时钟源，比墙钟可靠）。"""
    n0 = len(rd.sta)
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        chunk = port.read(64)
        if chunk:
            rd.feed(chunk, time.monotonic())
            if len(rd.sta) > n0:
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="下行成功率 vs 发送相位")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--delay-step", type=float, default=4.0, help="相位步进（ms）")
    ap.add_argument("--delay-max", type=float, default=48.0, help="相位上限（ms）")
    ap.add_argument("--trials", type=int, default=3, help="每个相位试几次")
    args = ap.parse_args()

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.05)
    except Exception as exc:
        print(f"[FAIL] 打不开 {args.port}: {exc}")
        return 6

    with port:
        port.reset_input_buffer()
        rd = Reader()

        # ---- 阶段 0：线路占用与帧周期 ----
        t_end = time.monotonic() + 2.0
        while time.monotonic() < t_end:
            # 64 B ≈ 1.08 帧：读太大会把多帧打进同一个时间戳 ⇒ 帧间隔统计失真
            chunk = port.read(64)
            if chunk:
                rd.feed(chunk, time.monotonic())
        if len(rd.sta) < 10:
            print(f"[FAIL] 2 s 内只收到 {len(rd.sta)} 帧 ⇒ 不在 RUN 态／串口错")
            return 6
        ts = [t for t, _ in rd.sta]
        gaps = [(ts[i + 1] - ts[i]) * 1000.0 for i in range(len(ts) - 1)]
        gaps = [g for g in gaps if g > 0.001]      # 丢掉同一次 read 里的多帧（间隔=0）
        if gaps:
            gaps_sorted = sorted(gaps)
            median = gaps_sorted[len(gaps_sorted) // 2]
            print(f"[ok] 2 s 内 {len(rd.sta)} 帧，帧间隔中位 {median:.2f} ms "
                  f"（⇒ {1000.0 / median:.2f} fps）")
        else:
            print(f"[warn] 2 s 内 {len(rd.sta)} 帧，但全部落在同一次 read 里 ⇒ "
                  f"控制器批量上报，相位扫描不可用（需改用更小的 read 或轮询驱动）")
            return 7
        print(f"     线路占用：原始 {rd.raw} B / 已解析帧 {rd.frame_bytes} B "
              f"= 比值 {rd.raw / max(1, rd.frame_bytes):.2f}")
        print(f"     坏CRC {rd.bad} 个")
        print()

        sta_now = rd.sta[-1][1]
        want = 1 - notch_of(sta_now)
        print(f"     起点 STA=0x{sta_now:02X}，第一次请求陷波={want}")
        print()

        # ---- 阶段 1：相位扫描 ----
        rows = []
        d = 0.0
        while d <= args.delay_max + 1e-9:
            hits = 0
            tries = 0
            for _ in range(args.trials):
                if not wait_next_frame(port, rd):
                    continue
                time.sleep(d / 1000.0)
                port.write(build(CMD_SET_NOTCH, bytes([want])))
                port.flush()
                tries += 1

                # 收够 12 帧（0.6 s）再判
                n0 = len(rd.sta)
                t_wait = time.monotonic() + 1.2
                while time.monotonic() < t_wait and len(rd.sta) - n0 < 12:
                    chunk = port.read(64)
                    if chunk:
                        rd.feed(chunk, time.monotonic())
                tail = [s for _, s in rd.sta[-8:]]
                got = collections.Counter(tail).most_common(1)[0][0] if tail else None
                if got is not None and notch_of(got) == want:
                    hits += 1
                    want ^= 1
            rate = hits / tries if tries else 0.0
            rows.append((d, hits, tries, rate))
            bar = "#" * int(round(rate * 24))
            print(f"  相位 {d:5.1f} ms  {hits}/{tries}  {rate * 100:5.1f}%  |{bar}")
            d += args.delay_step

        print()
        print("=" * 58)
        good = [r for r in rows if r[3] >= 0.99]
        bad = [r for r in rows if r[3] <= 0.01 and r[2] > 0]
        if good and bad:
            print(f"[命中] 相位依赖成立：全通相位 {[f'{r[0]:.0f}ms' for r in good]}")
            print(f"       全灭相位 {[f'{r[0]:.0f}ms' for r in bad]}")
            print("       ⇒ 上行流期间 485 方向无法翻转（自动方向转换器病）")
        elif not bad:
            print("[?] 各相位都没有「全灭」段 ⇒ 不是简单的方向抢占。")
            print("    成功率若平摊在 1/3 上下，问题在接收事件/解析，不在物理层相位。")
        else:
            print("[?] 存在全灭段但没有全通段 ⇒ 占线率很高，需查线上是否还有别流量。")
        print("=" * 58)
        return 0


if __name__ == "__main__":
    sys.exit(main())
