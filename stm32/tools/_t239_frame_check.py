# -*- coding: utf-8 -*-
"""
T239/T243 · 上行二进制帧抓包校验与物理层取证（2026-11-12）
================================================================================
在任意串口上抓 STM32 发出的上行显示帧，做四件事：
  ① 帧头/长度自洽（`len == 6 + LEN`）② CRC8（覆盖 `[2, 6+LEN-1)`）③ 序号连续性
  ④ **双通道块内容比对** —— `[CH1×N]` 与 `[CH2×N]` 是否**逐点相同**

④ 是 T239 步2 的**核心判据**：G8 时代 CH2 ≡ CH1（打包时复制）⇒ 两段必然相同；
步2 起 CH2 是真实数据 ⇒ **两段必须不同**（"仍然相同"＝还在复制，改动漏了）。

## `--forensic`：坏帧取证（T243）
两个判据：
  1. **帧头间距**（相邻两个 `AA 55 01` 的距离）
        间距 == 期望帧长 ⇒ 字节数没变、内容错 ⇒ **位替换**（线上干扰/接地）
        间距 <  期望帧长 ⇒ 帧内**丢了**若干字节
        间距 >  期望帧长 ⇒ 帧内**多出**字节（噪声造出假起止位）
  2. **定向假设检验**：帧头字段的值已知（TYPE=0x01、LEN=帧长-6、STA 低 6 位）
        ⇒ 把它们改回期望值后重算 CRC，若立刻对上，就证明**整帧只坏这 1 个字节**。

🛑 **为什么不用"单字节穷举"定位坏字节**：CRC8 只有 8 位，对任意一个字节位置，
   必然存在**唯一**一个取值能让 CRC 对上（实测 109 B 帧 106 个位置各有 1 个解）
   ⇒ 候选数等于没有信息。必须带结构约束去试。

## `--also-esp COM35`：两侧同时对照（T243）
在 `--port` 上抓包的同时，读 ESP32 的 `485-stat` 取**同一时间窗**的计数差。判据：
    PC 0% 而 ESP32 仍有错 ⇒ 坏在 **RS485 段**（收发器/线/终端电阻）
    两侧都有错            ⇒ 坏在 **STM32 的 UART5 输出侧**或共模（两路同源）

## 用法
    python tools/_t239_frame_check.py --secs 10 --expect 109                 # 双通道结构验收
    python tools/_t239_frame_check.py --secs 10 --expect 59                  # 单通道回归基线
    python tools/_t239_frame_check.py --secs 60 --expect 109 --forensic      # 坏帧取证
    python tools/_t239_frame_check.py --secs 60 --also-esp COM35 --forensic  # 两侧对照

🛑 抓包期间**不要**开 WinApp/其它终端抢同一个口（同一个串口只能一个程序占用）。
🛑 ESP32 的 USB-Serial-JTAG 在**每次开串口**时都会复位芯片（DTR/RTS 电平）
   ⇒ `--also-esp` 开完口后先等 2 s 再发 `485-start`。

帧式（协议 §5）：`AA 55 | TYPE(0x01) | LEN(2B LE) | SEQ(2B LE) | STA(1) | 样本 | CRC8`
   总长 = 6 + LEN；N = (LEN-3)/2/nch，本工程 `_popoutcnt = 25`
   ⇒ nch=1：LEN=53 ⇒ 59 B；nch=2：LEN=103 ⇒ 109 B
"""
import argparse
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import serial
except ImportError:
    raise SystemExit("!! 缺 pyserial：pip install pyserial")

POPOUTCNT = 25                    # _popoutcnt
LEN_1CH = 3 + 2 * POPOUTCNT       # 53
LEN_2CH = 3 + 4 * POPOUTCNT       # 103


# ---------------------------------------------------------------- CRC8（查表）
def _crc8_table():
    """CRC8（poly=0x07 / init=0 / 不反射）的 256 项查表。

    🛑 必须查表：早期版本用逐位算法 + "单字节穷举"，90 s 抓包的处理阶段拖到几分钟，
    进程被外层 SIGTERM 杀掉且 stdout 未 flush ⇒ 表现为"完全无输出"。
    """
    tab = []
    for v in range(256):
        c = v
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if (c & 0x80) else ((c << 1) & 0xFF)
        tab.append(c)
    return tab


_CRC8_TAB = _crc8_table()


def crc8(data):
    c = 0
    for b in data:
        c = _CRC8_TAB[c ^ b]
    return c


# ---------------------------------------------------------------- 抓包
def capture(port, baud, secs):
    buf = bytearray()
    with serial.Serial(port, baud, timeout=0.05) as sp:
        # 🛑 打开后**立刻**把 RTS 拉低：CH340-485 的 RTS 是方向控制（高=本机驱动总线）；
        #    pyserial 打开串口时 RTS 默认 asserted(高) ⇒ 不管它，本机驱动器会一直顶着
        #    总线，与 STM32 的上行帧**电气对撞**。纯接收必须 rts=False。
        #    ⚠ 纯 TTL 直连的转接器上 RTS 无副作用（没接方向控制）。
        sp.rts = False
        sp.reset_input_buffer()
        t_end = time.time() + secs
        while time.time() < t_end:
            n = sp.in_waiting
            if n:
                buf += sp.read(n)
            else:
                time.sleep(0.005)
    return bytes(buf)


# ---------------------------------------------------------------- 坏帧定位
def crc_ok_after_patch(buf, pos, total, patch):
    """把帧内指定字节改成 `patch` 后重算 CRC，返回是否与收到的 CRC 相等。"""
    f = bytearray(buf[pos:pos + total])
    for k, v in patch.items():
        f[k] = v
    return crc8(bytes(f[2:total - 1])) == f[total - 1]


def localize(buf, pos, total, exp_len):
    """给坏帧一个**最小修正假设**。返回 (描述, 需要修正的字节数)。"""
    lo, hi = exp_len & 0xFF, (exp_len >> 8) & 0xFF
    recv_lo, recv_hi = buf[pos + 3], buf[pos + 4]

    if crc_ok_after_patch(buf, pos, total, {3: lo, 4: hi}):
        if recv_lo != lo and recv_hi != hi:
            return ("LEN 两字节都坏", 2)
        if recv_lo != lo:
            return ("只坏 LEN 低字节 %02X→%02X" % (recv_lo, lo), 1)
        if recv_hi != hi:
            return ("只坏 LEN 高字节 %02X→%02X" % (recv_hi, hi), 1)
        return ("LEN 本来是对的（矛盾）", 0)
    if crc_ok_after_patch(buf, pos, total, {3: lo}):
        return ("只坏 LEN 低字节 %02X→%02X（高字节本来对）" % (recv_lo, lo), 1)
    if crc_ok_after_patch(buf, pos, total, {4: hi}):
        return ("只坏 LEN 高字节 %02X→%02X" % (recv_hi, hi), 1)
    return ("≥2 处坏（不在 LEN 上）", 9)


# ---------------------------------------------------------------- 解析
def parse(buf, expect=None, forensic=False):
    """解析整段缓冲。返回 stats dict（含 `spacing` 间距分布与 `bad` 坏帧明细）。"""
    st = dict(frames=0, crc_err=0, bad_len=0, resync=0, seq_gaps=0,
              lens={}, same_frames=0, diff_frames=0, bytes=len(buf), first=None,
              ch1_max=0, ch2_max=0, bad=[], spacing={})
    n = len(buf)

    # ---- 第一遍：所有候选帧头位置（AA 55 01）----
    heads = []
    i = 0
    while i + 7 <= n:
        if buf[i] == 0xAA and buf[i + 1] == 0x55 and buf[i + 2] == 0x01:
            heads.append(i)
        i += 1

    # resync 口径：帧头之间"不属于任何合法帧"的字节数（近似，够体检用）
    for k in range(len(heads) - 1):
        L = buf[heads[k] + 3] | (buf[heads[k] + 4] << 8)
        gap = heads[k + 1] - heads[k]
        st["resync"] += max(0, gap - (6 + L))

    prev_seq = None
    for k, pos in enumerate(heads):
        L = buf[pos + 3] | (buf[pos + 4] << 8)
        total = 6 + L
        if L < 3 or pos + total > n:
            st["bad_len"] += 1
            continue

        nxt = heads[k + 1] if k + 1 < len(heads) else None
        spacing = (nxt - pos) if nxt is not None else None
        if spacing is not None:
            st["spacing"][spacing] = st["spacing"].get(spacing, 0) + 1

        body = buf[pos + 2:pos + total - 1]
        crc_ok = (crc8(body) == buf[pos + total - 1])
        seq = buf[pos + 5] | (buf[pos + 6] << 8)
        sta = buf[pos + 7]

        if not crc_ok:
            st["crc_err"] += 1
            if forensic:
                # 真帧长：LEN 本身可能就坏了 ⇒ 用**帧头间距**反推
                tt = total
                if spacing is not None and spacing in (59, 109):
                    tt = spacing
                if pos + tt <= n:
                    why, nfix = localize(buf, pos, tt, (tt - 6) & 0xFFFF)
                else:
                    why, nfix = ("帧不完整，跳过定位", 9)
                st["bad"].append(dict(
                    pos=pos, spacing=spacing, L=L, total=total, true_total=tt,
                    seq=seq, sta=sta, why=why, nfix=nfix,
                    crc_recv=buf[pos + total - 1], crc_calc=crc8(body),
                    head=" ".join("%02X" % b for b in buf[pos:pos + 10]),
                    seq_ok=(prev_seq is not None and ((seq - prev_seq) & 0xFFFF) == 1),
                ))
            continue

        # ---- 合法帧 ----
        if expect is not None and total != expect:
            st["bad_len"] += 1
        nch = 1 if L == LEN_1CH else (2 if L == LEN_2CH else 0)
        st["lens"][total] = st["lens"].get(total, 0) + 1
        if nch:
            base = pos + 8
            ch1 = [int.from_bytes(buf[base + 2 * j: base + 2 * j + 2], "little", signed=True)
                   for j in range(POPOUTCNT)]
            st["ch1_max"] = max(st["ch1_max"], max(abs(v) for v in ch1))
            if nch == 2:
                b2 = base + POPOUTCNT * 2
                ch2 = [int.from_bytes(buf[b2 + 2 * j: b2 + 2 * j + 2], "little", signed=True)
                       for j in range(POPOUTCNT)]
                st["ch2_max"] = max(st["ch2_max"], max(abs(v) for v in ch2))
                if ch1 == ch2:
                    st["same_frames"] += 1
                else:
                    st["diff_frames"] += 1
                if st["first"] is None:
                    st["first"] = dict(seq=seq, sta=sta, ch1=ch1, ch2=ch2)
            elif st["first"] is None:
                st["first"] = dict(seq=seq, sta=sta, ch1=ch1, ch2=None)

        if prev_seq is not None and ((seq - prev_seq) & 0xFFFF) != 1:
            st["seq_gaps"] += 1
        prev_seq = seq
        st["frames"] += 1
    return st


# ---------------------------------------------------------------- ESP32 侧对照
def analyze_pattern(buf, expect, pat_byte, maxlist=40):
    """按**已知常量图案**逐字节核对整段缓冲（不依赖 CRC）。

    ★T244：为什么不能用 CRC 判坏帧 —— CRC 一旦不过，帧就被丢掉，而我们要的正是
    "它坏成什么样"。所以这里**按帧头 + 期望帧长切帧**，再逐字节比期望值：
        已知：AA 55 01 | LEN(lo,hi) | STA(低 6 位合法) | 样本区 = 图案
        未知：SEQ（用连续性命中）、CRC（单独按实际字节算）
    ⇒ 凡是偏离期望值的字节，都能给出**偏移 + 实际值**，这就是"线上把它变成什么"。

    `pat_byte`：样本区期望字节（0x00 或 0xFF）。
    返回 stats dict：frames / dev_frames / off_hist / val_hist / crc_err / samples
    """
    n = len(buf)
    L = (expect - 6) & 0xFFFF
    lo, hi = L & 0xFF, (L >> 8) & 0xFF
    st = dict(frames=0, dev_frames=0, crc_err=0,
              off_hist={}, val_hist={}, samples=[])
    heads = []
    i = 0
    while i + expect <= n:
        if buf[i] == 0xAA and buf[i + 1] == 0x55 and buf[i + 2] == 0x01:
            heads.append(i)
            i += expect
        else:
            i += 1
    prev_seq = None
    for pos in heads:
        fr = buf[pos:pos + expect]
        st["frames"] += 1
        # 期望值表：None = 未知（SEQ/CRC/STA 低位）
        want = [0xAA, 0x55, 0x01, lo, hi, None, None, None] + [pat_byte] * (expect - 9) + [None]
        devs = []
        for k, w in enumerate(want):
            if w is None:
                continue
            if fr[k] != w:
                devs.append((k, fr[k], w))
                st["off_hist"][k] = st["off_hist"].get(k, 0) + 1
                st["val_hist"][fr[k]] = st["val_hist"].get(fr[k], 0) + 1
        # STA 高位应为 0（bit6=AB、bit7=total≥100k 只在验证宏下用）
        if fr[7] & 0xC0:
            devs.append((7, fr[7], fr[7] & 0x3F))
            st["off_hist"][7] = st["off_hist"].get(7, 0) + 1
        seq = fr[5] | (fr[6] << 8)
        seq_bad = (prev_seq is not None and ((seq - prev_seq) & 0xFFFF) != 1)
        prev_seq = seq
        if crc8(fr[2:expect - 1]) != fr[expect - 1]:
            st["crc_err"] += 1
        if devs:
            st["dev_frames"] += 1
            if len(st["samples"]) < maxlist:
                st["samples"].append(dict(
                    pos=pos, seq=seq, seq_bad=seq_bad,
                    devs=devs[:8],
                    head=" ".join("%02X" % b for b in fr[:10]),
                ))
    return st


def run_two_ports(port_a, port_b, baud, secs, expect, forensic):
    """**两个口同时抓同一路数据**（例如 TTL 直连 + RS485 各一路），比对坏帧 SEQ 集合。

    ★T243 用途（判"源侧 vs 线路"，与 `--also-esp` 互补）：
        两条腿从**同一个** `comm_send()` 输出出发 ⇒ 若坏帧的 SEQ 集合**完全重合**，
        说明 STM32 发出的就是坏数据（**源侧**）；若**互不相交**，则各自在自己那段线上坏。
    为什么不能只用 ESP32 的计数：它只给总数、不给 SEQ，无法做集合比对。
    """
    import threading
    out = {}

    def worker(key, port):
        try:
            out[key] = capture(port, baud, secs)
        except Exception as e:                      # 一个口挂了另一个照跑
            out[key] = b""
            print("   !! %s 抓包失败：%s" % (port, e))

    t1 = threading.Thread(target=worker, args=("a", port_a))
    t2 = threading.Thread(target=worker, args=("b", port_b))
    t1.start(); t2.start()
    t1.join(); t2.join()

    sa = parse(out.get("a", b""), expect, forensic=forensic)
    sb = parse(out.get("b", b""), expect, forensic=forensic)
    return sa, sb


def _esp_stat(sp, wait=1.0, hard=2.5):
    sp.reset_input_buffer()
    sp.write(b"485-stat\r\n")
    buf = bytearray()
    t0 = time.time()
    t_end = t0 + wait
    while True:
        now = time.time()
        if now >= t_end or (now - t0) >= hard:
            break
        k = sp.in_waiting
        if k:
            buf += sp.read(k)
            t_end = time.time() + 0.2
        else:
            time.sleep(0.01)
    return buf.decode("utf-8", "replace")


def _grab(txt, key):
    m = re.search(key + r"=(\d+)", txt)
    return int(m.group(1)) if m else None


def run_pair(port, baud, secs, esp_port, expect, forensic):
    """在 `port` 抓 `secs` 秒，**同时**取 ESP32 同窗口的计数差。"""
    import threading
    cap = {}

    def worker():
        cap["data"] = capture(port, baud, secs)

    th = threading.Thread(target=worker)
    th.start()
    time.sleep(0.2)

    esp = serial.Serial(esp_port, 115200, timeout=0.05)
    try:
        time.sleep(2.0)                    # 开串口会复位 ESP32，等它起来
        _esp_stat(esp)
        esp.write(b"485-start\r\n")        # 只开"源"，不发 0x01（STM32 本来就在流）
        time.sleep(1.0)
        _esp_stat(esp)
        a = _esp_stat(esp)
        th.join()
        b = _esp_stat(esp)
    finally:
        esp.close()

    raw = cap.get("data", b"")
    st = parse(raw, expect, forensic=forensic)
    d = dict(
        frames=(_grab(b, "frames") or 0) - (_grab(a, "frames") or 0),
        crc=(_grab(b, "crc_err") or 0) - (_grab(a, "crc_err") or 0),
        resync=(_grab(b, "resync") or 0) - (_grab(a, "resync") or 0),
        seq_gaps=(_grab(b, "seq_gaps") or 0) - (_grab(a, "seq_gaps") or 0),
    )
    d["rate"] = (100.0 * d["crc"] / d["frames"]) if d["frames"] else 0.0
    return raw, st, d


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="上行帧抓包校验 / 物理层取证（T239·T243）")
    ap.add_argument("--port", default="COM26")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--secs", type=float, default=10.0)
    ap.add_argument("--expect", type=int, default=109,
                    help="每帧期望总长（109=双通道 / 59=单通道回归基线）")
    ap.add_argument("--forensic", action="store_true",
                    help="坏帧取证：帧头间距 + 定向假设检验（判位替换还是丢字节）")
    ap.add_argument("--maxlist", type=int, default=25, help="取证时最多列多少条坏帧")
    ap.add_argument("--pattern", default=None, choices=["00", "ff"],
                    help="★T244 临时诊断：样本区应恒为 0x00/0xFF（固件 `test patt 0|ff`）。"
                         "按**已知图案**逐字节核对，不依赖 CRC —— 输出每个偏离字节的偏移与实际值。")
    ap.add_argument("--pair", default=None, metavar="COMx",
                    help="**同时**抓 --port 与 COMx（两路独立接收同一份数据），比对坏帧 SEQ 集合："
                         "集合重合 ⇒ 坏在源侧；互不相交 ⇒ 各自线上坏。")
    ap.add_argument("--also-esp", default=None, metavar="COMx",
                    help="同时在 ESP32 口上统计**同一时间窗**的坏帧，做两侧对照。"
                         "★T243：判『坏在线上还是某一端接收』的唯一硬手段。")
    args = ap.parse_args()

    print("抓包 %s @%d，%.1f s（期望帧长 %d B）..." % (args.port, args.baud, args.secs, args.expect))

    if args.pattern:
        raw = capture(args.port, args.baud, args.secs)
        pat = 0x00 if args.pattern == "00" else 0xFF
        r = analyze_pattern(raw, args.expect, pat, args.maxlist)
        print("=" * 74)
        print("★ 图案核对：样本区应恒为 0x%02X（共收 %d B）" % (pat, len(raw)))
        print("=" * 74)
        print("按帧头切帧 %d 帧；偏离期望的帧 **%d** 帧（%.2f%%）；CRC 不过 %d 帧"
              % (r["frames"], r["dev_frames"],
                 100.0 * r["dev_frames"] / r["frames"] if r["frames"] else 0, r["crc_err"]))
        if not r["dev_frames"]:
            print("⇒ **整段数据与期望逐字节一致**：这条链路上没有发生任何位错。")
            return 0
        print()
        print("偏离字节的**偏移分布**（偏移 = 相对帧头 AA 的字节下标）：")
        for k in sorted(r["off_hist"]):
            print("    +%-3d × %-4d" % (k, r["off_hist"][k]))
        print()
        print("实际收到值的分布（%s）：" % ("期望 00，出现这些非零值" if pat == 0
                                        else "期望 FF，出现这些值"))
        for v, c in sorted(r["val_hist"].items(), key=lambda kv: -kv[1])[:12]:
            print("    %02X × %d" % (v, c))
        if r["samples"]:
            print()
            print("样例（最多 %d 条）：" % len(r["samples"]))
            for j, x in enumerate(r["samples"], 1):
                dev = "，".join("+%d %02X→%02X" % (k, g, w) for k, g, w in x["devs"])
                print("  %-2d seq=%-5d %s%s" % (j, x["seq"], dev,
                                                "  ⚠SEQ 跳变" if x["seq_bad"] else ""))
        print("-" * 74)
        return 0 if r["dev_frames"] == 0 else 1
    esp_d = None
    pair_b = None
    if args.pair:
        st, pair_b = run_two_ports(args.port, args.pair, args.baud, args.secs,
                                   args.expect, args.forensic)
    elif args.also_esp:
        raw, st, esp_d = run_pair(args.port, args.baud, args.secs, args.also_esp,
                                  args.expect, args.forensic)
    else:
        raw = capture(args.port, args.baud, args.secs)
        st = parse(raw, args.expect, forensic=args.forensic)

    print("=" * 74)
    print("收 %d B ／ 合法帧 %d ／ 重同步丢弃 %d 字节" % (st["bytes"], st["frames"], st["resync"]))
    print("帧长分布：", ", ".join("%d B×%d" % (k, v) for k, v in sorted(st["lens"].items())) or "(无)")
    print("crc_err=%d  bad_len=%d  seq_gaps=%d" % (st["crc_err"], st["bad_len"], st["seq_gaps"]))
    if st["first"] is not None and st["first"]["ch2"] is not None:
        print("双通道块比对：相同帧 %d ／ 不同帧 %d" % (st["same_frames"], st["diff_frames"]))
        print("  最大幅值：CH1=%d LSB  CH2=%d LSB" % (st["ch1_max"], st["ch2_max"]))
        f = st["first"]
        print("  首帧 seq=%d sta=0x%02X" % (f["seq"], f["sta"]))
        print("  CH1[:10] = %s" % f["ch1"][:10])
        print("  CH2[:10] = %s" % f["ch2"][:10])

    if args.forensic:
        print()
        print("=" * 74)
        print("★ 坏帧取证（间距==期望 ⇒ 位替换；< ⇒ 丢字节；> ⇒ 多字节）")
        print("=" * 74)
        sp = st["spacing"]
        if sp:
            print("帧头间距分布（应有且仅有 %d）：" % args.expect)
            for k in sorted(sp):
                if k == args.expect:
                    tag = "  ← 正常"
                elif k < args.expect:
                    tag = "  ← 少 %d B（丢字节）" % (args.expect - k)
                else:
                    tag = "  ← 多 %d B（假起止位/位错成 AA55 01）" % (k - args.expect)
                print("   %4d B × %-5d%s" % (k, sp[k], tag))
        bad = st["bad"]
        if not bad:
            print("本次未捕获到坏帧。")
        else:
            print()
            print("坏帧明细（最多 %d / 共 %d 条）：" % (args.maxlist, len(bad)))
            print("  #  间距 LEN  SEQok STA CRC收/算  最小修正假设")
            for j, b in enumerate(bad[:args.maxlist], 1):
                print("  %-2d %4s %3d  %-5s %02X  %02X/%02X   %s"
                      % (j, b["spacing"], b["L"], "Y" if b["seq_ok"] else "N",
                         b["sta"], b["crc_recv"], b["crc_calc"], b["why"]))
            print()
            print("  帧头 10 B（按收到的原样）：")
            for j, b in enumerate(bad[:args.maxlist], 1):
                print("    %-2d %s" % (j, b["head"]))

            from collections import Counter
            cat = Counter()
            for b in bad:
                w = b["why"]
                if w.startswith("只坏 LEN 低"):
                    cat["只坏 LEN 低字节"] += 1
                elif w.startswith("只坏 LEN 高"):
                    cat["只坏 LEN 高字节"] += 1
                elif w.startswith("LEN 两字节"):
                    cat["LEN 两字节都坏"] += 1
                elif w.startswith("≥2"):
                    cat["≥2 处坏（非 LEN）"] += 1
                else:
                    cat[w] += 1
            print()
            print("  最小修正假设分布：", "，".join("%s ×%d" % (k, v) for k, v in cat.items()))

            if sp:
                n_less = sum(v for k, v in sp.items() if k < args.expect)
                n_more = sum(v for k, v in sp.items() if k > args.expect)
                print()
                if n_less > n_more:
                    print("⇒ 有 %d 帧间距偏小（丢字节），%d 帧偏大" % (n_less, n_more))
                elif n_more > 0:
                    print("⇒ 有 %d 帧间距偏大（假起止位），%d 帧偏小" % (n_more, n_less))
                else:
                    print("⇒ 间距全部 = %d ⇒ 字节数没变，是**位替换**" % args.expect)

    if pair_b is not None:
        print()
        print("=" * 74)
        print("★ 两路同时抓包：坏帧 SEQ 集合比对（判源侧 vs 线路）")
        print("=" * 74)
        print("  %-18s %8s %8s %8s" % ("接收端", "帧数", "crc_err", "坏帧率"))
        for tag, sx, port in (("A", st, args.port), ("B", pair_b, args.pair)):
            print("  %-18s %8d %8d %7.2f%%" % ("%s／%s" % (tag, port), sx["frames"],
                                              sx["crc_err"],
                                              100.0 * sx["crc_err"] / sx["frames"] if sx["frames"] else 0))
        qa = set(b["seq"] for b in st["bad"])
        qb = set(b["seq"] for b in pair_b["bad"])
        both = qa & qb
        print()
        print("  坏帧 SEQ：A 有 %d 个、B 有 %d 个，**重合 %d 个**" % (len(qa), len(qb), len(both)))
        if qa or qb:
            print("  仅 A：%s" % (sorted(list(qa - qb))[:12] or "（无）"))
            print("  仅 B：%s" % (sorted(list(qb - qa))[:12] or "（无）"))
            print("  交集：%s" % (sorted(list(both))[:12] or "（无）"))
            if both and len(both) >= max(1, min(len(qa), len(qb)) * 0.6):
                print("  ⇒ **集合基本重合 ⇒ 坏在源侧**（STM32 发出的就是坏数据）")
            elif both:
                print("  ⇒ 有部分重合（%d 个）：源侧坏 %d 个 + 各自线路坏若干" % (len(both), len(both)))
            else:
                print("  ⇒ **两路互不相交 ⇒ 各自在自己的线上坏**（源侧干净）")

    if esp_d is not None:
        print()
        print("=" * 74)
        print("★ 两侧对照（同一时间窗）")
        print("=" * 74)
        print("  %-20s %8s %8s %8s" % ("接收端", "帧数", "crc_err", "坏帧率"))
        print("  %-20s %8d %8d %7.2f%%" % ("PC／%s" % args.port, st["frames"],
                                          st["crc_err"],
                                          100.0 * st["crc_err"] / st["frames"] if st["frames"] else 0))
        print("  %-20s %8d %8d %7.2f%%" % ("ESP32／%s" % args.also_esp, esp_d["frames"],
                                          esp_d["crc"], esp_d["rate"]))
        print()
        if st["frames"] and esp_d["frames"]:
            if st["crc_err"] == 0 and esp_d["crc"] > 0:
                print("⇒ **PC 侧 0、ESP32 侧有错** ⇒ 坏点在 **RS485 段**（收发器/线/终端电阻）")
            elif st["crc_err"] > 0 and esp_d["crc"] > 0:
                print("⇒ **两侧都坏** ⇒ 坏点在 STM32 的 UART5 输出侧或共模（两路同源）")
            else:
                print("⇒ 两侧都干净")

    print("-" * 74)
    ok = True
    if st["frames"] == 0:
        print("XX  一帧都没解出来 —— 先确认在出流、波特率、以及抓包期间没有别的程序占着 %s"
              % args.port)
        return 2
    if st["crc_err"] or st["bad_len"] or st["seq_gaps"]:
        ok = False
        print("XX  crc_err/bad_len/seq_gaps 非 0 —— 先查链路再谈波形（T218 铁律）")
    if args.expect == 109:
        if st["lens"].get(109, 0) != st["frames"]:
            ok = False
            print("XX  存在非 109 B 帧 —— 帧长公式或 nch 分支被改坏")
        elif st["first"] is not None and st["first"]["ch2"] is not None:
            if st["diff_frames"] == 0:
                ok = False
                print("XX  CH1 段与 CH2 段**逐点相同** —— 说明还在复制 CH1，步2 改动漏了")
            else:
                print("OK  双通道 109 B、CRC 全对、CH1/CH2 内容不同（真实两路）")
    else:
        print("OK  单通道 %d B 帧结构正常（回归基线）" % args.expect if ok else "XX 基线异常")
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
