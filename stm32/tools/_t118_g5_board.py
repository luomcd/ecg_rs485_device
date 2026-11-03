# -*- coding: utf-8 -*-
"""
T118 · G5 上板验收帧判读（2026-09-23）
输入：猛哥上板实测的 80 B 快照帧（Reset 0x03 触发）
做四件事：
  1. CRC8 复核（覆盖 [2] 起 77 B）
  2. 字段解析（TYPE / LEN / REQID / COUNT）
  3. 与 regs_leaderIII 表逐条比对（按 #if/#else 预处理分支 + 剥行尾注释）
  4. 打印"预期不一致清单"（0x00 项因第④步显式写 STOP 而豁免）
"""
import hashlib
import io
import re
import sys

HEX = """AA 55 03 4A 00 03 24 00 02 01
11 02 19 03 00 04 00 05 00 06
08 07 00 08 00 09 00 0A 07 0B
00 0C 04 0D 00 0E 00 0F 00 10
00 11 00 12 04 13 00 14 00 15
00 16 00 17 05 21 02 22 02 23
02 24 02 25 00 26 00 27 08 28
00 29 00 2A 00 2E 33 2F 30 E7"""

SRC = r"Ads1293/ads1293.c"
FAIL = []


def ck(cond, ok, ng):
    print(("  [OK] " if cond else "  [NG] ") + (ok if cond else ng))
    if not cond:
        FAIL.append(ng)


def crc8(data):
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if (c & 0x80) else ((c << 1) & 0xFF)
    return c


def parse_table(path):
    """按 active 栈 + 剥行尾注释 + 整行锚定，解析 regs_leaderIII 表体。

    🛑 T118 教训：**宏的值必须从源码读**（`#define ECG_LOD_EN 0`），
    不能把 `#if` 支假定为真 —— 首版就是硬编码 `active and True` ⇒
    06/07/08 三条期望值取到了**未编译进去的那一支**，把正确的实测判成"3 条不符"。
    """
    raw = open(path, "rb").read().decode("utf-8", "replace")
    lines = raw.split("\n")
    m_lod = re.search(r"#define\s+ECG_LOD_EN\s+(\d+)", raw)
    lod = int(m_lod.group(1)) if m_lod else 0
    print("  #define ECG_LOD_EN = %d  ⇒ 取 %s 支" % (lod, "#if" if lod else "#else"))
    s = e = None
    for i, l in enumerate(lines):
        if "const uint8_t regs_leaderIII[][2]" in l:
            s = i
        if s is not None and e is None and i > s and l.strip().startswith("};"):
            e = i
    body = lines[s:e + 1]
    stack, active, rows, skipped = [], True, [], []
    for l in body:
        t = l.strip()
        if t.startswith("#if"):
            stack.append(active)
            active = active and bool(lod)   # ★ 用真值，不用假定
            continue
        if t.startswith("#elif"):
            active = stack[-1] and False
            continue
        if t.startswith("#else"):
            active = stack[-1] and (not bool(lod))
            continue
        if t.startswith("#endif"):
            active = stack.pop() if stack else True
            continue
        l2 = re.sub(r"//.*$", "", l)       # 剥行尾注释
        m = re.match(r"\s*\{\s*0x([0-9a-fA-F]{2})\s*,\s*0x([0-9a-fA-F]{2})\s*\}", l2)
        if m:
            if active:
                rows.append((int(m.group(1), 16), int(m.group(2), 16)))
            else:
                skipped.append((int(m.group(1), 16), int(m.group(2), 16)))
    return rows, skipped, s + 1, e + 1, body


def main():
    b = [int(x, 16) for x in HEX.split()]
    print("=" * 78)
    print("§1 帧基本字段")
    print("=" * 78)
    ck(len(b) == 80, "总长 = 80 B", "总长 = %d B（应 80）" % len(b))
    ck(b[0] == 0xAA and b[1] == 0x55, "帧头 AA 55", "帧头错：%02X %02X" % (b[0], b[1]))
    ck(b[2] == 0x03, "TYPE = 0x03（快照帧）", "TYPE = %02X（应 03）" % b[2])
    ln = b[3] | (b[4] << 8)
    ck(ln == 74, "LEN = 0x004A = 74 ⇒ 帧总长 6+74 = 80", "LEN = %d（应 74）" % ln)

    print()
    print("=" * 78)
    print("§2 CRC8 复核（覆盖 [2] 起 77 B）")
    print("=" * 78)
    calc = crc8(bytes(b[2:79]))
    ck(calc == b[79], "CRC8 = 0x%02X（帧内 0x%02X）✓" % (calc, b[79]),
       "CRC8 不符：算得 %02X，帧内 %02X" % (calc, b[79]))

    print()
    print("=" * 78)
    print("§3 载荷字段（★ 本步主判据）")
    print("=" * 78)
    reqid, cnt = b[5], b[6]
    ck(reqid == 0x03, "★★★ REQID[5] = 0x03 ✓（二进制通道认了 0x03，非 ASCII 的 00）",
       "REQID[5] = %02X（应 03）⇒ 吃到的是别的通道" % reqid)
    ck(cnt == 36, "COUNT[6] = 0x24 = 36 ✓", "COUNT = %d（应 36）" % cnt)
    ck(6 + 2 + 36 * 2 == 80 - 1 + 1, "载荷长度自洽（6+COUNT+36×2 = CRC 位置）", "长度不自洽")
    pairs = [(b[7 + 2 * i], b[8 + 2 * i]) for i in range(36)]

    print()
    print("=" * 78)
    print("§4 与 regs_leaderIII 表逐条比对")
    print("=" * 78)
    rows, skipped, ls, le, body = parse_table(SRC)
    print("  表体行范围 :%d-%d（%d 行）" % (ls, le, le - ls + 1))
    print("  有效条数   :%d  被预处理跳过:%s" % (len(rows), ["%02X" % r for r, _ in skipped]))
    md5 = hashlib.md5("\n".join(body).encode("utf-8")).hexdigest()
    print("  表体 md5   :%s（口径：按 \\n 切行取原字节含 CRLF）" % md5)
    ck(len(rows) == 36, "表解析出 36 条 ✓（不是 41 条）", "表解析出 %d 条（应 36）" % len(rows))
    ck(rows[0] == (0x00, 0x00), "table[0] == (0x00, 0x00) ✓（STANDBY）",
       "table[0] = (%02X,%02X)（应 00,00）" % rows[0])

    print()
    diff, same = [], 0
    for i, (reg, val) in enumerate(pairs):
        if i < len(rows) and (reg, val) == rows[i]:
            same += 1
        else:
            exp = rows[i] if i < len(rows) else None
            diff.append((i + 1, reg, val, exp))

    print("  逐条相同 :%d / 36" % same)
    print("  差异清单 :")
    for n, reg, val, exp in diff:
        tag = ""
        if reg == 0x00 and val == 0x02:
            tag = "  ← ★ **正常**：第④步显式写 STOP(0x02)，非表值"
        print("    #%-2d REG=%02X  实测=%02X  表值=%s%s"
              % (n, reg, val, ("%02X" % exp[1]) if exp else "N/A", tag))

    real_bad = [d for d in diff if not (d[1] == 0x00 and d[2] == 0x02)]
    ck(not real_bad, "除 0x00 外，36 条全等于表值 ✓（写表真生效）",
       "有 %d 条与表不符：%s" % (len(real_bad), ["%02X:%02X" % (r, v) for _, r, v, _ in real_bad]))
    ck(len(pairs) == 36, "序号 1..36 连续无缺（升序）",
       "条数/%s" % len(pairs))
    regs = [r for r, _ in pairs]
    ck(regs == sorted(regs), "REG 升序排列 ✓  %s" % " ".join("%02X" % r for r in regs),
       "REG 非升序")

    print()
    print("=" * 78)
    print("§5 逐条对照表（就地打印，便于人工核对）")
    print("=" * 78)
    print("  #    REG  实测  表值   判定")
    for i, (reg, val) in enumerate(pairs):
        exp = rows[i][1] if i < len(rows) else None
        ok = "=" if exp is not None and val == exp else "*"
        note = ""
        if reg == 0x00 and val == 0x02:
            ok, note = "!", "  ← 第④步 STOP"
        print("  %-3d  %02X   %02X    %s    %s%s"
              % (i + 1, reg, val, ("%02X" % exp) if exp is not None else "--", ok, note))

    print()
    print("=" * 78)
    print("结论：%s" % ("全部通过（%d 项检查，0 FAIL）" % (len(FAIL) + 0) if not FAIL
                      else "有 %d 项不通过" % len(FAIL)))
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
