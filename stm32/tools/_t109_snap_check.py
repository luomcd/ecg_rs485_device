#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_t109_snap_check.py —— 寄存器快照帧（协议 v2.2 §3.2，上行 Type 0x03）校验器
=============================================================================
落码背景：T109/G3 在固件侧新增 `ads1293_snapshot()` / `ads1293_send_snapshot()`
（`Ads1293/ads1293.c`）＋ ASCII 命令 `test snapshot`（`Core/Src/vcomcmd.c`）。

本脚本做两件事，**两侧口径（固件打包 ↔ 上位机解析）都在这里落成可执行断言**：

  1) `--model`    ：从源码 `regs_leaderIII` 现读 36 条 ⇒ 按**固件打包逻辑**造出一帧
                    ⇒ 再用**上位机 C# 解析逻辑**（`Core/Protocol.cs::ParseSnapshot`
                    与长度/CRC 前置校验）回解 ⇒ 断言条目与源码表逐条全等。
                    这等于把"两侧逐字节对齐"从上板试验变成离线断言。
  2) `--hex ...`  ：校验**上板抓到的真实帧**（串口助手粘一段 hex）：
                    CRC + LEN/COUNT 自洽 + 36 条与源码表比对。
                    ★ 这就是 G3 的验收命令（G4/G5/G9/G10 的快照回执同款）。

帧格式（两侧同一口径）：
    AA 55 | 0x03 | LEN(2B LE) | REQID(1) | COUNT(1) | (REG,VAL)×COUNT | CRC8
    LEN   = 2 + 2×COUNT         总长 = 6 + LEN = 8 + 2×COUNT
    COUNT = 36 ⇒ LEN = 74 = 0x4A ⇒ 总长 80 B
    CRC8(poly=0x07, init=0, refin/refout=false, xorout=0) 覆盖 `frame[2]` 起 3+LEN 字节

⚠ 唯一会随运行态变的表内项是 **0x00 CONFIG 的 START_CON 位**
  （`test afe` 后 = 0x00；RUN 中 = 0x01；`test afe stop` 后 = 0x02）
  ⇒ 比对时 0x00 单列，不算失败（与上位机 `Config/expected_regs.json` 排除 0x00 同一理由）。

用法（在工程根执行）：
    PY=C:\\Users\\luom\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe
    $PY tools/_t109_snap_check.py --selftest
    $PY tools/_t109_snap_check.py --model
    $PY tools/_t109_snap_check.py --hex "AA 55 03 4A 00 00 24 ..."

★ Windows/cmd 用户：**不要**直接敲 `_t109_snap_check.py …`（cmd 不把 .py 当可执行文件，
  会退回文件关联去启动，可能落进 pythonw/IDLE ⇒ 当前窗口**一个字都不出**）。
  用同目录的包装器即可：`tools\\_py.cmd tools\\_t109_snap_check.py --hex "…"`
"""
import argparse
import re
import sys
from pathlib import Path

# ---- 输出通道容错（Windows 实测坑）--------------------------------------------
# 本脚本会打印 ✓ ✗ ★ ⚠ ⇒ 等非 GBK 字符。交互式控制台下 Python 3.6+ 走 PEP 528
# （UTF-8 + WriteConsoleW）没问题；但一旦**重定向/管道**（`> out.txt`、`| more`），
# stdout 编码退回活动码页（中文 Windows = cp936），这些字符不在 GBK 内
# ⇒ UnicodeEncodeError 中途崩掉，表现为"跑到一半没声了"。统一改成 UTF-8 + replace。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
SRC_ADS = ROOT / "Ads1293" / "ads1293.c"

TYPE_SNAPSHOT = 0x03
SNAP_MAX_ITEMS = 60          # 协议 §3.2 COUNT 上限
DISPLAY_LEN_LEGACY = 52      # 上位机仍须认（v1.1）
DISPLAY_LEN_SINGLE = 53      # v2.2 +STA（G2）
DISPLAY_LEN_DUAL = 103


# ---------------------------------------------------------------- CRC8 (§ 与 C 实现同口径)
def crc8(data: bytes) -> int:
    crc = 0x00
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def selftest_crc() -> int:
    """ecg_crc8.h 里钉死的三个标准向量（对上了就不会错）"""
    cases = [(b"123456789", 0xF4), (b"\x00", 0x00), (b"\x01", 0x07)]
    bad = 0
    for data, want in cases:
        got = crc8(data)
        flag = "OK " if got == want else "FAIL"
        print(f"  [{flag}] crc8({data!r}) = 0x{got:02X}  期望 0x{want:02X}")
        bad += got != want
    return bad


# ---------------------------------------------------------------- 源码取表
def read_leader_table():
    """从 ads1293.c 现读 regs_leaderIII（按 ECG_LOD_EN 生效分支），返回 [(reg,val), ...]"""
    txt = SRC_ADS.read_text(encoding="utf-8", errors="replace")

    m = re.search(r"^#define\s+ECG_LOD_EN\s+(\d+)", txt, re.M)
    if not m:
        raise SystemExit("找不到 `#define ECG_LOD_EN`，源码结构变了？")
    lod_en = int(m.group(1))

    m = re.search(r"const\s+uint8_t\s+regs_leaderIII\[\]\[2\]\s*=\s*\{(.*?)\n\}\s*;",
                  txt, re.S)
    if not m:
        raise SystemExit("找不到 regs_leaderIII 表，源码结构变了？")
    body = m.group(1)

    rows, active, branch = [], True, 0
    for raw in body.splitlines():
        line = raw.split("//")[0]                       # 去行尾注释
        if re.search(r"#if\s+ECG_LOD_EN", line):
            branch += 1
            active = bool(lod_en)
            continue
        if re.search(r"#else", line):
            active = not bool(lod_en)
            continue
        if re.search(r"#endif", line):
            active = True
            continue
        if not active:
            continue
        for r_, v_ in re.findall(r"\{\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*\}", line):
            rows.append((int(r_, 16), int(v_, 16)))

    if not rows:
        raise SystemExit("解析出 0 条寄存器，源码结构变了？")
    if rows != sorted(rows):
        raise SystemExit("⚠ 表不是按 REG 升序 —— 协议 §3.2 要求升序，固件直接按表序打包！")
    return rows, lod_en


# ---------------------------------------------------------------- 固件侧打包（模型）
def build_frame(reqid: int, items):
    n = len(items)
    payload = bytes([reqid, n]) + bytes(b for it in items for b in it)
    length = len(payload)                                   # = 2 + 2n
    frame = bytearray([0xAA, 0x55, TYPE_SNAPSHOT,
                       length & 0xFF, (length >> 8) & 0xFF]) + payload
    frame.append(crc8(bytes(frame[2:])))                    # 覆盖 TYPE+LEN+PAYLOAD
    return bytes(frame)


# ---------------------------------------------------------------- 上位机侧解析（模型）
class ParseError(Exception):
    pass


def parse_frame(frame: bytes, verbose=False):
    """照抄 Core/Protocol.cs:189-252 + ParseSnapshot 的前置校验与切片口径"""
    if len(frame) < 7:
        raise ParseError("短于最小帧")
    if frame[0] != 0xAA or frame[1] != 0x55:
        raise ParseError("帧头不是 AA 55")
    ftype = frame[2]
    length = frame[3] | (frame[4] << 8)

    if ftype not in (0x01, TYPE_SNAPSHOT):
        raise ParseError(f"未知 TYPE 0x{ftype:02X}（上位机走 UnknownFrames++）")
    if ftype == 0x01 and length not in (DISPLAY_LEN_LEGACY, DISPLAY_LEN_SINGLE, DISPLAY_LEN_DUAL):
        raise ParseError(f"显示帧 LEN={length} 不在 {{52,53,103}}")
    if ftype == TYPE_SNAPSHOT and (length < 2 or (length - 2) % 2 or (length - 2) // 2 > SNAP_MAX_ITEMS):
        raise ParseError(f"快照帧 LEN={length} 不合法（上位机判 ErrorFrames++）")

    frame_length = 6 + length
    if len(frame) < frame_length:
        raise ParseError(f"整帧未到齐：需要 {frame_length} B，只有 {len(frame)} B")
    if len(frame) > frame_length:
        if verbose:
            print(f"  ⚠ 尾部多 {len(frame) - frame_length} B（后续帧/脏字节），本帧只取前 {frame_length} B")

    crc_scope = frame[2:2 + 3 + length]                     # Slice(2, 3 + length)
    if crc8(crc_scope) != frame[frame_length - 1]:
        raise ParseError(f"CRC 不符：收到 0x{frame[frame_length-1]:02X}，算得 0x{crc8(crc_scope):02X}")

    payload = frame[5:5 + length]                           # Slice(5, length)
    reqid = payload[0]
    count = payload[1]
    items = [(payload[2 + i * 2], payload[3 + i * 2]) for i in range(count)]
    return reqid, count, items, frame_length


# ---------------------------------------------------------------- 子命令
def cmd_model(reqid: int):
    table, lod_en = read_leader_table()
    print(f"源码 regs_leaderIII：{len(table)} 条（ECG_LOD_EN={lod_en} 分支），REG 升序 ✓")
    frame = build_frame(reqid, table)
    print(f"固件侧模型打包：REQID=0x{reqid:02X} COUNT={len(table)} "
          f"LEN={len(frame)-6}=0x{len(frame)-6:02X} 总长={len(frame)} B")
    assert len(frame) == 80, "COUNT=36 ⇒ 总长必须 80 B"
    assert frame[:7] == bytes([0xAA, 0x55, 0x03, 0x4A, 0x00, reqid, len(table)]), \
        f"帧头不符：{frame[:7].hex(' ').upper()}"

    r_reqid, r_count, r_items, r_len = parse_frame(frame, verbose=True)
    print(f"上位机侧模型解析：REQID=0x{r_reqid:02X} COUNT={r_count} 帧长={r_len} B ✓")
    assert (r_reqid, r_count, r_items) == (reqid, len(table), table), "回环不一致！"
    print("回环断言：固件打包 ⇒ 上位机解析 ⇒ 条目与源码表逐条全等 ✓")
    print(f"期望帧头（抓包对照）：AA 55 03 4A 00 {reqid:02X} {len(table):02X}")
    if reqid == 0x00:
        print("\n★ 上板期望（G3）：串口助手发 `test snapshot` 收到 80 B，帧头应为")
        print("    AA 55 03 4A 00 00 24 …，回显行 snapshot: items=36 len=74 bytes=80 run=? ret=0")
    return 0


def cmd_hex(hexstr: str, skip_reg00: bool):
    data = bytes(int(t, 16) for t in re.findall(r"[0-9a-fA-F]{2}", hexstr))
    print(f"收到 {len(data)} B：{' '.join(f'{b:02X}' for b in data[:7])} … "
          f"{' '.join(f'{b:02X}' for b in data[-3:])}")
    try:
        reqid, count, items, flen = parse_frame(data, verbose=True)
    except ParseError as e:
        print(f"✗ 解析失败：{e}")
        return 1
    print(f"解析：TYPE=0x03 REQID=0x{reqid:02X} COUNT={count} 帧长={flen} B（收 {len(data)} B）✓")
    if flen != 80:
        print(f"⚠ 帧长 {flen} B ≠ 期望 80 B（COUNT={count}）；本步 G3 只认 COUNT=36")

    table, _ = read_leader_table()
    tmap = dict(table)
    bad = []
    for reg, val in items:
        exp = tmap.get(reg)
        if exp is None:
            bad.append((reg, val, None, "★不在 regs_leaderIII 表内"))
        elif reg == 0x00 and skip_reg00:
            print(f"  [跳过] 0x00 = 0x{val:02X}（START_CON 随运行态变：STANDBY 00／RUN 01／"
                  f"stop 后 02；上位机同样排除该项）")
        elif val != exp:
            bad.append((reg, val, exp, "值不符"))
    print(f"\n比对结果：{len(items)-len(bad)}/{len(items)} 条一致"
          + (f"，不符 {len(bad)} 条：" if bad else "，全等 ✓"))
    for reg, val, exp, why in bad:
        exp_s = "—" if exp is None else f"0x{exp:02X}"
        print(f"  ✗ 0x{reg:02X}: 读回 0x{val:02X} / 期望 {exp_s}  ({why})")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description="寄存器快照帧（0x03）校验器 —— T109/G3")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--selftest", action="store_true", help="CRC8 三个标准向量自检")
    g.add_argument("--model", action="store_true", help="源码取表 ⇒ 造帧 ⇒ 回环解析（两侧口径断言）")
    g.add_argument("--hex", type=str, help="校验上板抓到的真实帧（十六进制串）")
    ap.add_argument("--reqid", type=lambda s: int(s, 0), default=0,
                    help="--model 时用的 REQID（默认 0x00 = 上电主动上报；G4 的 REQ_SNAPSHOT=0x10）")
    ap.add_argument("--keep-reg00", action="store_true",
                    help="--hex 时不跳过 0x00 的比对（默认跳过，因其随运行态变）")
    args = ap.parse_args()

    if args.selftest:
        print("CRC8 标准向量自检（ecg_crc8.h 口径：poly=0x07 init=0 不反射）：")
        bad = selftest_crc()
        print("结果：" + ("全部通过 ✓" if bad == 0 else f"{bad} 项失败 ✗"))
        return 1 if bad else 0
    if args.model:
        return cmd_model(args.reqid)
    return cmd_hex(args.hex, skip_reg00=not args.keep_reg00)


if __name__ == "__main__":
    sys.exit(main())
