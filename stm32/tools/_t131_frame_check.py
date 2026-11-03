#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T131 · G10 上板作业卡「帧级自检」—— 把 tools/_t127/frames.txt 里每一条帧
都过三重校验：① 长度对核（实收 == 6+LEN）② 帧长门（LEN ≤ VCOM_BIN_MAX_LEN）③ CRC8。

为什么要有这个脚本（T130/T131 的教训）：
  作业卡是**人手工粘贴**的物料，"卡里写错一个字节"的代价是——用户看到"没反应"
  ⇒ 误判固件坏了 ⇒ 排查方向全错。卡里的帧必须**机器校验过**才准发出去。

负向帧怎么标：卡里在该帧行尾写 `#!NEG=键[,键]`，脚本就把对应检查**倒过来判**。
  键：lenparity（0x11 的 LEN 奇且>=3）／nmax（N ≤ 28）／lenmax（LEN ≤ 58）
  ⇒ 未被标为 NEG 的帧，任何一条不过就是**卡写错了**（脚本 exit 1）。

口径（与固件逐字对齐）：
  帧式   AA 55 | TYPE | LEN_lo LEN_hi | CMD | ARG... | CRC8
  长度   len == 6 + LEN                                     (vcomcmd.c:465)
  帧长门 LEN <= VCOM_BIN_MAX_LEN = 58                       (vcomcmd.c:440,462)
  CRC8   poly=0x07 / init=0x00 / 不反射，覆盖 [2, len-1)     (vcomcmd.c:470-476)
  TYPE   下行命令帧必须 == 0x80                              (vcomcmd.c:478)
  per-CMD 长度门：
     0x01/0x02/0x03/0x10  LEN == 1
     0x11 SET_REGS        LEN 奇且 >= 3，且 N=(LEN-1)/2 <= 28   (vcomcmd.c:591,594)
用法： python tools/_t131_frame_check.py [作业卡路径]
"""
import io
import os
import re
import sys

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARD = sys.argv[1] if len(sys.argv) > 1 else os.path.join(FW, "tools", "_t127", "frames.txt")

VCOM_BIN_MAX_LEN = 58
TYPE_DOWN = 0x80
LEN1_CMDS = {0x01, 0x02, 0x03, 0x10}

n_pass = 0
n_neg = 0
fails = []


def crc8(buf):
    c = 0
    for x in buf:
        c ^= x
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def run_checks(tag, checks, expect_fail):
    """checks = [(key, cond, msg)]；expect_fail 里的键**倒过来判**（NEG 帧）。"""
    global n_pass, n_neg
    ok_all = True
    for key, cond, msg in checks:
        if key in expect_fail:
            if cond:                       # 期望被拒，却通过了 ⇒ 卡/固件对不上
                fails.append("%s [NEG 未触发] %s" % (tag, msg))
                ok_all = False
            else:
                n_neg += 1
        else:
            if cond:
                n_pass += 1
            else:
                fails.append("%s %s" % (tag, msg))
                ok_all = False
    return ok_all


def main():
    raw = io.open(CARD, encoding="utf-8", newline="").read()
    lines = raw.split("\n")
    print("作业卡：%s（%d 行）" % (os.path.relpath(CARD, FW), len(lines)))
    print("=" * 100)
    rows = []
    pat = re.compile(r"^([A-Z][0-9A-Z]*)\.\s+((?:[0-9A-Fa-f]{2}\s+)+[0-9A-Fa-f]{2})")
    negpat = re.compile(r"#!NEG=([a-z,]+)")
    for ln, text in enumerate(lines, 1):
        t = text.rstrip("\r").strip()
        m = pat.match(t)
        if not m:
            continue
        tag, hexs = m.group(1), m.group(2)
        negm = negpat.search(t)
        expect_fail = set(negm.group(1).split(",")) if negm else set()
        h = bytes(int(x, 16) for x in hexs.split())
        LEN = h[3] | (h[4] << 8)
        typ, cmd = h[2], h[5]
        n_items = (LEN - 1) // 2 if cmd == 0x11 else 0
        checks = [
            ("minlen", len(h) >= 7, "总长 %d < 7" % len(h)),
            ("hdr", h[0] == 0xAA and h[1] == 0x55, "帧头非 AA 55"),
            ("type", typ == TYPE_DOWN, "TYPE=0x%02X != 0x80" % typ),
            ("lenchk", len(h) == 6 + LEN,
             "长度对核失败：实收 %d != 6+LEN(%d)=%d" % (len(h), LEN, 6 + LEN)),
            ("crc", crc8(h[2:len(h) - 1]) == h[-1],
             "CRC 复算 0x%02X != 帧内 0x%02X" % (crc8(h[2:len(h) - 1]), h[-1])),
            ("lenmax", LEN <= VCOM_BIN_MAX_LEN,
             "LEN=%d > %d（长度门拒）" % (LEN, VCOM_BIN_MAX_LEN)),
            ("known", cmd in LEN1_CMDS or cmd == 0x11, "未知 CMD 0x%02X" % cmd),
            ("len1", (LEN == 1) if cmd in LEN1_CMDS else True,
             "CMD 0x%02X 要求 LEN==1，实为 %d" % (cmd, LEN)),
            ("lenparity", (LEN >= 3 and (LEN & 1) == 1) if cmd == 0x11 else True,
             "0x11 要求 LEN 奇且 >=3，实为 %d" % LEN),
            ("nmax", (n_items <= 28) if cmd == 0x11 else True,
             "0x11 项数 N=%d > 28" % n_items),
        ]
        ok = run_checks("L%d/%s" % (ln, tag), checks, expect_fail)
        resp = {0x01: "起流(无回帧)", 0x02: "停流(无回帧)", 0x03: "0x03 快照",
                0x10: "0x03 快照", 0x11: "0x03 快照(REQID=0x11) 或 静默"}.get(cmd, "?")
        rows.append((tag, ln, len(h), LEN, cmd, len(h[6:len(h) - 1]), n_items, ok,
                     "NEG" if expect_fail else "", resp))
    for tag, ln, tot, LEN, cmd, narg, nit, ok, neg, resp in rows:
        print("%-4s L%-4d 总长%3dB LEN=0x%02X CMD=0x%02X ARG=%2dB N=%2d %-4s %-3s ⇒ %s"
              % (tag, ln, tot, LEN, cmd, narg, nit, "OK" if ok else "FAIL", neg, resp))
    print("=" * 100)
    print("帧数：%d 条；断言：%d PASS ／ %d NEG-OK（按标记如期被拒）／ %d FAIL"
          % (len(rows), n_pass, n_neg, len(fails)))
    if fails:
        print("-" * 100)
        for f in fails:
            print("FAIL " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
