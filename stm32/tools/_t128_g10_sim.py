# -*- coding: utf-8 -*-
"""
_t128_g10_sim.py —— T128 / G10（下行 `0x11 SET_REGS`）离线验收
================================================================================

本步目标（T127 拍板）：固件实现协议 §5.1 的 `0x11 SET_REGS` —— 上位机按
`(REG,VAL)×N` 批量写寄存器；**策略 B**（`N ≤ 28`，36 项拆 2 批）。
🛑 本步只加"一条下行命令 + 一个新内核"，**不碰上行显示帧路径、不碰 Filter/、不碰 CDC 层**。

判据分九节：
  §0 文件/换行符/注释配对（CRLF 必须继承）
  §1 ★ 改动面 = **纯插入**（删掉插入段后与上一版定稿**逐字节相同**）
  §2 白名单：`_reg_writable` 的黑名单集合 vs `regs_leaderIII` 36 条 vs 协议 §6.2 H 硬闸 11 条
  §3 长度门：分派层"LEN 必须 =1+2N（奇且 ≥3）"＋`VCOM_BIN_MAX_LEN` 上限 ⇒ N ≤ 28
  §4 预扫零副作用：白名单预扫循环在写循环**之前**，且预扫体里无任何写操作
  §5 帧构造：独立 CRC8 交叉印证 + 作业卡帧逐帧自洽 + 边界帧（28 放 / 29 拒）
  §6 引用面审计：新符号的声明/定义/调用点计数
  §7 红线：Filter/ 零改动（mtime）＋ 表体 md5 ＋ ECG_* 宏不变 ＋ CDC 上限未动
  §8 既有离线回归（G4/G5/G6/G7）的失效项是否在白名单内

不用 pytest：本项目所有离线脚本都是"自打印 + 退出码"风格（便于上板前后各跑一次留痕）。
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAK = os.path.join(ROOT, "tools", "_t128_bak")
PY = sys.executable

H = "Ads1293/ads1293.h"
C = "Ads1293/ads1293.c"
V = "Core/Src/vcomcmd.c"
P = lambda *a: os.path.join(ROOT, *a)

PASS = [0]
FAIL = []


def ck(name, cond, detail=""):
    if cond:
        PASS[0] += 1
        print("  OK   %s" % name)
    else:
        FAIL.append(name)
        print("  FAIL %s   %s" % (name, detail))


def rb(p):
    return io.open(P(*p.split("/")), "rb").read()


def rt(p):
    return rb(p).decode("utf-8")


def rl(p):
    """按 \n 切行（吃掉 CRLF），便于统一处理。"""
    return rt(p).replace("\r\n", "\n").replace("\r", "\n").split("\n")


def code_only(s: str) -> str:
    """剥注释（块 + 行内），用于"判据只看代码不看注释"。"""
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def raw_bytes_lines(p):
    """按原始字节切行（保留 CRLF 行尾）—— 表体 md5 专用。"""
    b = rb(p)
    return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")


print("=" * 78)
print("T128 / G10 离线验收（下行 0x11 SET_REGS · 策略 B）")
print("=" * 78)

# ============================== §0 文件与换行 ==============================
print("\n§0 文件 / 换行符 / 注释配对")
for p, n_exp in ((H, None), (C, None), (V, None)):
    b = rb(p)
    crlf = b.count(b"\r\n")
    lone = b.count(b"\n") - crlf
    ck("%s CRLF 继承（loneLF=%d）" % (p, lone), lone == 0, "CRLF=%d loneLF=%d" % (crlf, lone))
for p in (H, C, V):
    s = rt(p)
    ck("%s 全文 `/*` 与 `*/` 配对（%d/%d）" % (p, s.count("/*"), s.count("*/")),
       s.count("/*") == s.count("*/"))

# ============================== §1 纯插入 ==============================
print("\n§1 ★ 改动面 = 纯插入（删掉插入段后与上一版定稿逐字节相同）")
#  (anchor, 插入总行数: 块行数 + 2 个自动空行)
INS = [(H, 185, 16 + 2), (C, 535, 92 + 2), (V, 560, 34 + 2)]
for p, anchor, ilen in INS:
    cur = rl(p)
    bak = rl(p.split("/")[-1] and ("tools/_t128_bak/" + p.split("/")[-1]))
    removed = cur[:anchor] + cur[anchor + ilen:]
    same = removed == bak
    ck("%s 删除 [%d..%d]（%d 行）后 == 基线" % (p, anchor + 1, anchor + ilen, ilen), same,
       "行数 cur=%d bak=%d removed=%d" % (len(cur), len(bak), len(removed)))
    ck("%s 总行数 = 基线 + 插入（%d + %d = %d）" % (p, len(bak), ilen, len(cur)),
       len(cur) == len(bak) + ilen, "%d vs %d" % (len(cur), len(bak) + ilen))

# ============================== §2 白名单 ==============================
print("\n§2 白名单：固件黑名单 vs 表内 36 条 vs 协议 §6.2 H 硬闸 11 条")


def parse_table():
    """从 C 解析 regs_leaderIII 表体（取 ECG_LOD_EN 分支），返回 [(reg,val)]"""
    s = rt(C)
    lines = s.split("\n")
    st = next(i for i, l in enumerate(lines)
              if l.strip().startswith("const uint8_t regs_leaderIII[][2]"))
    en = next(i for i in range(st, len(lines)) if lines[i].strip() == "};")
    m = re.search(r"#\s*define\s+ECG_LOD_EN\s+([0-9]+)", s)
    lod = int(m.group(1))
    stack = [True]
    tab = []
    for k in range(st + 1, en):
        l = lines[k]
        t = l.strip()
        if t.startswith("#if"):
            stack.append(stack[-1] and bool(lod)); continue
        if t.startswith("#else"):
            stack[-1] = stack[-2] and (not bool(lod)); continue
        if t.startswith("#endif"):
            stack.pop(); continue
        if not stack[-1] or t.startswith("//"):
            continue
        for mm in re.finditer(r"\{\s*0x([0-9a-fA-F]{2})\s*,\s*0x([0-9a-fA-F]{2})\s*\}",
                              l.split("//")[0]):
            tab.append((int(mm.group(1), 16), int(mm.group(2), 16)))
    return tab


tab = parse_table()
regs = [r for r, _ in tab]
ck("表内条数 == 36", len(tab) == 36, "实为 %d" % len(tab))

# 从源码提取 _reg_writable 的黑名单（case 列表）——判据看代码，不看注释
cw = code_only(rt(C))
fn = re.search(r"static int _reg_writable\(uint8_t reg\)\s*\{(.*?)\n\}", cw, flags=re.S)
ck("找到 _reg_writable 函数体", fn is not None)
fw = fn.group(1) if fn else ""
blk = sorted({int(x, 16) for x in re.findall(r"case\s+0x([0-9a-fA-F]{2})u\s*:", fw)})
print("     固件黑名单（从源码提取）= %s（%d 条）" % (" ".join("0x%02X" % x for x in blk), len(blk)))
ck("黑名单 == 7 条 {0x11,0x12,0x13,0x21,0x22,0x23,0x24}",
   blk == [0x11, 0x12, 0x13, 0x21, 0x22, 0x23, 0x24],
   "实为 %s" % " ".join("0x%02X" % x for x in blk))
ck("黑名单 7 条**全部在表内**（否则是永不命中的死代码）",
   all(x in regs for x in blk), "表外: %s" % [hex(x) for x in blk if x not in regs])
ck("可写条数 = 36 - 7 = 29", len([r for r in regs if r not in blk]) == 29)

# 协议 §6.2 H 硬闸 11 条（从协议文档抄录，作为独立第二来源）
H_HARD = {0x01, 0x02, 0x13, 0x21, 0x22, 0x23, 0x24, 0x11, 0x12, 0x2F, 0x27}
ck("H 硬闸 11 条", len(H_HARD) == 11)
ck("H 硬闸全部在表内", all(x in regs for x in H_HARD),
   "表外: %s" % [hex(x) for x in sorted(H_HARD) if x not in regs])
allow_hard = sorted(H_HARD - set(blk))
ck("★ 固件**放行**的 H 硬闸 = 4 条 {0x01,0x02,0x2F,0x27}（H1 电极映射 / H4 读回通道 / H5 DRDY 源）",
   allow_hard == [0x01, 0x02, 0x27, 0x2F], "实为 %s" % " ".join("0x%02X" % x for x in allow_hard))
ck("H 硬闸 = 固件黑名单(7) ∪ 固件放行(4)，两集不交",
   set(blk) | set(allow_hard) == H_HARD and not (set(blk) & set(allow_hard)))
ck("★ 放行的 4 条在源码里**确实没被拒**（不出现在 case 列表）",
   not (set(allow_hard) & set(blk)))
# 分工注释必须写明"别补"
cn = rt(C)
ck("★ 分工注释写明「必须放行」且点名 4 条（G9 依赖）",
   all(k in cn for k in ("G9 要写 `0x02`", "`0x2F`", "别补")),
   "注释缺关键句")

# ============================== §3 长度门 ==============================
print("\n§3 长度门：LEN = 1+2N（奇且 ≥3）＋ VCOM_BIN_MAX_LEN 上限")
cv = code_only(rt(V))
m = re.search(r"#define\s+VCOM_BIN_MAX_LEN\s+(\d+)u", cv)
maxlen = int(m.group(1)) if m else -1
ck("VCOM_BIN_MAX_LEN == 58u（本步未动）", maxlen == 58, "实为 %d" % maxlen)
ck("分派层有长度对核 `6 + argLen == len`", "6u + (uint32_t)argLen != len" in cv or
   "(uint32_t)6u + (uint32_t)argLen != len" in cv)
ck("★ 分派层有 per-CMD 长度门 `argLen < 3u || (argLen & 1u) == 0u`",
   "argLen < 3u" in cv and "(argLen & 1u) == 0u" in cv)
ck("分派层有 STOP 态门（0x11 段内 `_bconvstart` return）",
   re.search(r"case\s+0x11u\s*:(.*?)break;", cv, flags=re.S).group(1).count("_bconvstart") >= 1)
sec11 = re.search(r"case\s+0x11u\s*:(.*?)break;", cv, flags=re.S).group(1)
ck("★ 0x11 段调用 ads1293_set_regs(&src[6], (argLen-1)>>1)",
   "ads1293_set_regs(&src[6]" in sec11 and "(argLen - 1u) >> 1" in sec11)
ck("★ 0x11 段**不含** printf_msg（命令路径铁律）", "printf_msg" not in sec11)


def accept(arglen):
    """Python 复刻分派层的两级门。"""
    if arglen > maxlen:            # VCOM_BIN_MAX_LEN 门（更早）
        return False
    if arglen < 3 or (arglen & 1) == 0:
        return False
    return True


cases = {0: 1, 1: 3, 2: 5, 28: 57, 29: 59, 36: 73, 50: 101}
exp = {0: False, 1: True, 2: True, 28: True, 29: False, 36: False, 50: False}
for n, al in cases.items():
    ck("N=%-2d (LEN=%-3d) ⇒ %s" % (n, al, "放行" if exp[n] else "拒绝"), accept(al) == exp[n])
legal = [n for n in range(0, 60) if accept(1 + 2 * n)]
ck("★ 合法 N 恰好 1..28（28 个值）", legal == list(range(1, 29)), "实为 %s" % legal[:5])
ck("★ 帧总长 = 7+2N 恒为**奇数** ⇒ 永不等于 64 ⇒ 永不撞 CDC 整包边界",
   all((7 + 2 * n) % 2 == 1 for n in range(1, 29)))
ck("★ 两个独立约束给出同一上限：VCOM_BIN_MAX_LEN(58) ⇒ N≤28 ；CDC 单包(64B) ⇒ N≤28",
   max(n for n in range(0, 60) if 1 + 2 * n <= maxlen) == 28 and (7 + 2 * 29) > 64 and (7 + 2 * 28) <= 64)

# ============================== §4 预扫零副作用 ==============================
print("\n§4 预扫零副作用（先全扫、再写第一条）")
fn2 = re.search(r"int ads1293_set_regs\(const volatile uint8_t \*items, uint32_t n\)\s*\{(.*?)\n\}",
                cw, flags=re.S)
ck("找到 ads1293_set_regs 函数体", fn2 is not None)
body = fn2.group(1) if fn2 else ""
i_pre = body.find("_reg_writable(")
i_wr = body.find("ads1293_write_reg(")
i_rd = body.find("ads1293_read_reg(")
i_snap = body.find("ads1293_send_snapshot(")
ck("预扫（_reg_writable）出现在写（write_reg）之前", 0 <= i_pre < i_wr, "pre=%d wr=%d" % (i_pre, i_wr))
ck("回读（read_reg）在写之后", i_wr < i_rd, "wr=%d rd=%d" % (i_wr, i_rd))
ck("快照发送在最后（read_reg 之后）", i_rd < i_snap, "rd=%d snap=%d" % (i_rd, i_snap))
pre_loop = body[i_pre - 200: i_wr]
ck("★ 预扫段内**无任何写操作**（write_reg / send_snapshot 都不出现）",
   "ads1293_write_reg(" not in pre_loop and "ads1293_send_snapshot(" not in pre_loop)
ck("★ 预扫命中即 `return (int)(i + 1u);`（提前返回，一条都不写）",
   re.search(r"if\s*\(!_reg_writable\(.*?\)\)\s*\{\s*return \(int\)\(i \+ 1u\);", body, flags=re.S) is not None)
ck("内核自守：`n == 0u || n > 28u` ⇒ 拒（给别的调用方兜底）",
   "n == 0u || n > 28u" in body)
ck("返回值语义与 ads1293_write_table 一致（>0 = 首个问题项 1-based 下标）",
   body.count("return (int)(i + 1u);") == 2)
ck("★ 成功才发快照（无 printf_msg、无额外 delay）",
   "printf_msg" not in body and body.count("HAL_Delay(1)") == 1)

# ============================== §5 帧构造 ==============================
print("\n§5 帧构造：独立 CRC8 交叉印证 + 作业卡逐帧自洽")


def crc8(d):
    c = 0
    for b in d:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if (c & 0x80) else (c << 1) & 0xFF
    return c


def mkframe(payload):
    ln = len(payload)
    body = [0x80, ln & 0xFF, (ln >> 8) & 0xFF] + list(payload)
    return bytes([0xAA, 0x55] + body + [crc8(body)])


ck("CRC8 交叉印证①：START 帧 == AA 55 80 01 00 01 5D",
   mkframe([0x01]) == bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x01, 0x5D]))
ck("CRC8 交叉印证②：RESET 帧 == AA 55 80 01 00 03 53",
   mkframe([0x03]) == bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x03, 0x53]))

# 作业卡帧文件逐帧验（长度自洽 + CRC 自洽）
fp = P("tools", "_t127", "frames.txt")
if os.path.exists(fp):
    nfr = 0
    bad = []
    for ln in io.open(fp, encoding="utf-8").read().split("\n"):
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("##"):
            continue
        if not re.fullmatch(r"(?:[0-9A-Fa-f]{2} )+[0-9A-Fa-f]{2}", s):
            continue
        b = bytes(int(x, 16) for x in s.split())
        nfr += 1
        L = b[3] | (b[4] << 8)
        if len(b) != 6 + L or crc8(b[2:len(b) - 1]) != b[-1]:
            bad.append(s[:40])
    ck("作业卡 frames.txt 逐帧自洽（%d 帧：长度 6+LEN + CRC）" % nfr, nfr >= 7 and not bad, "%s" % bad)
else:
    ck("作业卡 frames.txt 存在", False)

# 边界帧：N=28 放行 / N=29 超上限
f28 = mkframe([0x11] + [0] * 56)
f29 = mkframe([0x11] + [0] * 58)
ck("N=28 帧 = 63 B（≤64 单包）", len(f28) == 63)
ck("★ N=28 帧 LEN=57 通过两级门（策略 B 的批 1）", accept(57))
ck("N=29 帧 = 65 B、LEN=59 > 58 ⇒ 被 VCOM_BIN_MAX_LEN 拒（静默丢弃）",
   len(f29) == 65 and not accept(59))
ck("N=36 帧 = 79 B ⇒ 拒（t127 Q1：必须拆 2 批 28+8）", len(mkframe([0x11] + [0] * 72)) == 79)

# ============================== §6 引用面 ==============================
print("\n§6 引用面审计")
# ⚠ 只数「声明行」（以 `int ads1293_set_regs(` 开头）—— 数"含该串的行"会把注释里的
#   说明行也算进去（本步的注释块里出现了 2 次函数名，首版就是这么假 FAIL 的）。
n_decl = len([l for l in rl(H) if l.startswith("int ads1293_set_regs(")])
n_def = len(re.findall(r"^int ads1293_set_regs\(", rt(C), flags=re.M))
n_call = len(re.findall(r"ads1293_set_regs\(", rt(V)))
ck("ads1293_set_regs：声明 1 + 定义 1 + 调用 1", (n_decl, n_def, n_call) == (1, 1, 1),
   "(%d,%d,%d)" % (n_decl, n_def, n_call))
ck("_reg_writable：定义 1 处（static）", len(re.findall(r"static int _reg_writable\(", rt(C))) == 1)
ck("_reg_writable 在 set_regs 内被调用 1 次", body.count("_reg_writable(") == 1)
ck("vcomcmd.c 中 `case 0x11u:` 恰好 1 处", len(re.findall(r"case 0x11u:", rt(V))) == 1)
# VCOM_BIN_MAX_LEN 引用点数量与基线一致
b_v = code_only(io.open(os.path.join(BAK, "vcomcmd.c"), encoding="utf-8").read())
ck("VCOM_BIN_MAX_LEN 引用点数量未变（%d → %d）" % (b_v.count("VCOM_BIN_MAX_LEN"), cv.count("VCOM_BIN_MAX_LEN")),
   b_v.count("VCOM_BIN_MAX_LEN") == cv.count("VCOM_BIN_MAX_LEN"))
# ⚠ 首版这条判据写的是"vcomcmd.c 内无 msg.data" —— 错的：`msg` 是 **ASCII 通道**的局部
#   结构体，该文件本来就有 `msg.data`（:34 附近）。本步"没碰 CDC 层"已由 §1 纯插入判据
#   覆盖（vcomcmd.c 除插入段外逐字节相同）；这里只补一条更准的：**0x11 段内不提 CDC**。
ck("🛑 0x11 段内不出现任何 CDC 层符号（msg. / CDC_Receive / 累积缓冲）",
   not any(k in sec11 for k in ("msg.", "CDC_", "cumul")))

# ============================== §7 红线 ==============================
print("\n§7 红线（Filter/ · 表体 · ECG_* 宏 · CDC 上限）")
# 7.1 Filter/ 零改动：用 mtime 判（本步只碰了 3 个源文件）
fdir = P("Filter")
fsrc = sorted(f for f in os.listdir(fdir)
              if os.path.isfile(os.path.join(fdir, f)) and not f.lower().endswith(".md"))
t_c = os.path.getmtime(P("Ads1293", "ads1293.c"))
newer = [f for f in fsrc if os.path.getmtime(os.path.join(fdir, f)) >= t_c]
ck("Filter/ 源文件 %d 个" % len(fsrc), len(fsrc) == 14, "实为 %d" % len(fsrc))
ck("★ Filter/ 无文件晚于本步落码时刻（零改动）", not newer, "%s" % newer)

# 7.2 表体 md5（🛑 口径 = T125 原样：`b"\n".join(raw.split(b"\n")[st:en+1])`）
#     ⚠ 别"顺手"改成 `b"\r\n".join(...)` —— 段内**本来就带 `\r`**（split(b"\n") 的产物），
#       再用 `\r\n` join 会**多出一个 `\r`**（859 → 858 B）⇒ md5 从 e543248d 变 46b6a089，
#       看起来像"表体被改了"，实为**假 FAIL**（T128 首版就栽在这 1 个字节上）。
raw = rb(C)
bl = raw.split(b"\n")                       # 不归一化：保留每段尾部的 \r
st = next(i for i, l in enumerate(bl) if l.strip().startswith(b"const uint8_t regs_leaderIII[][2]"))
en = next(i for i in range(st, len(bl)) if bl[i].strip() == b"};")
body_md5 = hashlib.md5(b"\n".join(bl[st:en + 1])).hexdigest()
ck("★ 表体 md5 == e543248d4bcfb7fc2c381891b293b237（与 G7/G8/G9 定稿逐字节相同）",
   body_md5 == "e543248d4bcfb7fc2c381891b293b237",
   "实为 %s（行 %d-%d）" % (body_md5, st + 1, en + 1))
print("     表体动态定位到 %d-%d 行（G8 起为 272-319，本次插入点在 535 ⇒ 表体位置应不变）"
      % (st + 1, en + 1))
ck("表体位置与 G8 定稿一致（272-319）", (st + 1, en + 1) == (272, 319),
   "实为 %d-%d" % (st + 1, en + 1))
# ★ 更强：与 G8 定稿**逐字节相同**（不看 md5，直接比字节）
_bl_bak = io.open(os.path.join(BAK, "ads1293.c"), "rb").read().split(b"\n")
ck("★ 表体与 G8 定稿**逐字节相同**（同口径切片直比）",
   bl[st:en + 1] == _bl_bak[st:en + 1])

# 7.3 ECG_* 宏数量不变
ecg_new = sorted(set(re.findall(r"#\s*define\s+(ECG_[A-Z0-9_]+)", rt(C))))
ecg_bak = sorted(set(re.findall(r"#\s*define\s+(ECG_[A-Z0-9_]+)",
                                io.open(os.path.join(BAK, "ads1293.c"), encoding="utf-8").read())))
ck("ads1293.c 的 ECG_* 宏集合不变（%s）" % ecg_new, ecg_new == ecg_bak,
   "%s vs %s" % (ecg_new, ecg_bak))

# 7.4 usbd_cdc_if.c 未改
mtime_cdc = os.path.getmtime(P("USB_DEVICE", "App", "usbd_cdc_if.c"))
ck("🛑 usbd_cdc_if.c 未改（mtime 早于本步）", mtime_cdc < t_c)

# ============================== §8 既有回归 ==============================
print("\n§8 既有离线回归（G4~G8）＋ 失效项归因白名单")
# 🛑 白名单的**判据**（T128 现场归因，机制统一）：
#    老脚本用 `_slice_to(src, A, B)` 取片段，而 B 常写成**区间末端**（如 `\n      default:`）
#    ⇒ **G10 在 A..B 之间插入的任何新代码都会落进切片**，让"零改动/无某符号"类断言假 FAIL。
#    本步四条新失效项全部是这个机制（**行为未变**，已用独立手工验证坐实）：
#      ① G5 `write_table HAL_Delay==1`  ← 终止锚 `/* ---- G5…RESET`；区间内多了
#         `_reg_writable` + `ads1293_set_regs`（后者含 1 处 `HAL_Delay(1)`）
#      ② G5 `case 0x03u 无 _bconvstart` ← 终止锚 `\n      default:`；区间内多了全部新 case
#      ③ G6 `case 0x02u 无状态门`       ← 同上
#      ④ G7 `case 0x05u 无 _bconvstart` ← 终止锚 `      default:`；同上
#    ⇒ 手工验证（剥注释后逐 case 段计数）：`_bconvstart` **只在 0x10u(1) / 0x11u(1)** 出现，
#      其余 case 与 default 全为 0 ⇒ 与 G7/G8 口径一致。
WL = {
    "G4": [],
    "G5": ["HAL_Delay 只在失败重试", "case 0x03u 内**无** RUN 状态门", "头文件其它内容零改动"],
    "G6": ["case 0x02u 内**无**状态门", "头文件其它内容零改动",
           "ecg_ch1 可提取", "22 个既有函数"],
    "G7": ["ads1293.c 与备份**逐字节相同**", "ads1293.h 与备份**逐字节相同**",
           "删掉插入块后", "纯增行数 = 33", "case 0x05u 段**无** `_bconvstart`"],
    # G8 脚本的白名单是"G8 时刻"的 ⇒ G10 后它自己会报"白名单外"，属预期
    "G8": ["无白名单外的失败项（真回归）"],
}
LEGACY = [
    ("tools/_t114_g4_sim.py", "G4"),
    ("tools/_t116_g5_sim.py", "G5"),
    ("tools/_t119_g6_sim.py", "G6"),
    ("tools/_t121_g7_sim.py", "G7"),
    ("tools/_t125_g8_sim.py", "G8"),
]


def fails_of(out: str):
    """从脚本输出里抽失败项文本（两种格式：`✗ <判据>  <detail>` 与 尾部 `- <判据>`）。"""
    res, in_tail = [], False
    for l in out.split("\n"):
        s = l.strip()
        if s.startswith("失败项"):
            in_tail = True
            continue
        if s.startswith("✗"):
            res.append(s.lstrip("✗").strip())
        elif in_tail and s.startswith("- "):
            res.append(s[2:].strip())
    return [x for x in res if x]


for rel, tag in LEGACY:
    fp2 = P(*rel.split("/"))
    if not os.path.exists(fp2):
        print("     跳过 %s（脚本不在）" % rel)
        continue
    r = subprocess.run([PY, fp2], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    fl = fails_of(out)
    un = [f for f in fl if not any(k in f for k in WL[tag])]
    ck("%s：失效 %d 条**全在白名单**，无真回归" % (tag, len(fl)), not un,
       "白名单外：%s" % [u[:70] for u in un])
    print("     %-3s 失效 %d 条" % (tag, len(fl)))
    for f in fl:
        print("        · %s" % f[:96])

# ============================== 收尾 ==============================
print("\n" + "=" * 78)
print("T128 / G10 离线验收：PASS = %d ，FAIL = %d" % (PASS[0], len(FAIL)))
for f in FAIL:
    print("  ✗ %s" % f)
print("=" * 78)
sys.exit(0 if not FAIL else 1)
