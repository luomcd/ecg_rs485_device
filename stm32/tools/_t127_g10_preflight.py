# -*- coding: utf-8 -*-
"""
T127 · G10（0x11 SET_REGS）落码前预检卡生成器（2026-09-23）
================================================================
🛑 只读脚本：**不修改任何源码**。产出 T106 §9n 预检卡所需的全部事实。

七节：
  §1  regs_leaderIII 表体解析（按 #if ECG_LOD_EN 预处理分支）⇒ 36 条 (REG,VAL)
  §2  白名单判定表：表内 ∩ 非黑名单 ⇒ 可写 ／ 其余 ⇒ 拒写
  §3  长度边界表：N vs LEN vs 总长 vs 固件门 vs CDC 单包
  §4  作业卡 CRC 帧清单（含交叉印证 START/RESET 已知帧）
  §5  落码插入点定位（3 文件 4 处锚点）
  §6  零改动核对（红线指纹：表体 md5 / Filter md5 / ECG_* 宏 / VCOM_BIN_MAX_LEN）
  §7  基线现状（0x11 落到 default ⇒ 静默丢弃）
"""
import hashlib
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_C = os.path.join(ROOT, "Ads1293", "ads1293.c")
SRC_H = os.path.join(ROOT, "Ads1293", "ads1293.h")
SRC_V = os.path.join(ROOT, "Core", "Src", "vcomcmd.c")
FILTER_DIR = os.path.join(ROOT, "Filter")
BAK = os.path.join(ROOT, "tools", "_t125_bak")

DENY = [0x11, 0x12, 0x13, 0x21, 0x22, 0x23, 0x24]   # T106 §4-G10 白名单规则
FAIL = []
N_OK = 0


def ck(cond, ok, ng):
    global N_OK
    print(("  [OK] " if cond else "  [NG] ") + (ok if cond else ng))
    if cond:
        N_OK += 1
    else:
        FAIL.append(ng)


def read(p):
    with open(p, "rb") as f:
        return f.read()


def txt(p, enc="utf-8"):
    raw = read(p)
    return raw.decode(enc, errors="replace")


def crc8(data):
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if (c & 0x80) else ((c << 1) & 0xFF)
    return c


def frame(payload):
    """payload = CMD + ARG... ⇒ LEN = len(payload)；总长 6+LEN。"""
    ln = len(payload)
    body = [0x80, ln & 0xFF, (ln >> 8) & 0xFF] + list(payload)
    return bytes([0xAA, 0x55] + body + [crc8(body)])


def hexs(b):
    return " ".join("%02X" % x for x in b)


# ---------------------------------------------------------------- §1 表体解析
print("=" * 78)
print("§1  regs_leaderIII 表体解析（含 #if ECG_LOD_EN 分支）")
print("=" * 78)

s_c = txt(SRC_C)

m = re.search(r"#\s*define\s+ECG_LOD_EN\s+([0-9]+)", s_c)
lod_en = int(m.group(1)) if m else None
print("  ECG_LOD_EN = %s" % lod_en)

lines = s_c.split("\n")
start = None
for i, l in enumerate(lines):
    if l.strip().startswith("const uint8_t regs_leaderIII[][2]"):
        start = i
        break
ck(start is not None, "表体起始行找到（行号 %d，1-based %d）" % (start, start + 1), "表体起始行未找到")

end = None
for j in range(start, len(lines)):
    if lines[j].strip() == "};":
        end = j
        break
ck(end is not None, "表体结束行找到（行号 %d，1-based %d）" % (end, end + 1), "表体结束行未找到")

# active 栈：处理 #if ECG_LOD_EN / #else / #endif
table = []
stack = [True]
for k in range(start + 1, end):
    l = lines[k]
    st = l.strip()
    if st.startswith("#if"):
        stack.append(stack[-1] and bool(lod_en))
        continue
    if st.startswith("#else"):
        parent = stack[-2]
        stack[-1] = parent and (not bool(lod_en))
        continue
    if st.startswith("#endif"):
        stack.pop()
        continue
    if not stack[-1]:
        continue
    if st.startswith("//"):
        continue
    # ★ 剥行内注释：`{0x2f, 0x30},//{0x2f, 0x30},` 若不剥会把注释里的第二份也数进去
    #   （首版就踩了这个坑：数出 37 条、连带"可写 30 条"与"批2 N=9"三条假 FAIL）
    code = st.split("//")[0]
    for mm in re.finditer(r"\{\s*0x([0-9a-fA-F]{2})\s*,\s*0x([0-9a-fA-F]{2})\s*\}", code):
        table.append((int(mm.group(1), 16), int(mm.group(2), 16)))

print("  解析条目数 = %d" % len(table))
ck(len(table) == 36, "表体 = 36 条（协议 §6 权威值）", "表体 = %d 条（应为 36）" % len(table))

d = dict(table)
for probe, exp, tag in [
    (0x00, 0x00, "CONFIG 写值 0x00=STANDBY"),
    (0x0A, 0x07, "判据① 落点：0x0A 原值 ≠ 0x03 ⇒ 有区分度"),
    (0x21, 0x02, "判据② 落点：0x21 原值 == 0x02 ⇒ 与判据文案一致"),
    (0x2F, 0x30, "H4 CH_CNFG = 0x30（E1_EN+E2_EN）"),
    (0x27, 0x08, "H5 DRDYB_SRC = 0x08（ECG 就绪）"),
    (0x01, 0x11, "H1 FLEX_CH1_CN = 0x11"),
    (0x02, 0x19, "H1 FLEX_CH2_CN = 0x19"),
]:
    ck(d.get(probe) == exp, "0x%02X = 0x%02X  [%s]" % (probe, exp, tag),
       "0x%02X = %s（期望 0x%02X）[%s]" % (probe, d.get(probe), exp, tag))

# ---------------------------------------------------------------- §2 白名单判定
print()
print("=" * 78)
print("§2  白名单判定表（表内 ∩ 非黑名单 = 可写）")
print("=" * 78)

addr_in_table = [r for r, _ in table]
writable = [r for r in addr_in_table if r not in DENY]
denied = [r for r in addr_in_table if r in DENY]
print("  黑名单（固件硬拒）= %s" % " ".join("0x%02X" % x for x in DENY))
print("  拒写且在表内     = %s" % " ".join("0x%02X" % x for x in denied))
print("  可写条数         = %d ／ 表内 %d" % (len(writable), len(addr_in_table)))

ck(len(denied) == 7, "黑名单 7 条全部落在表内（快照仍会传它们）", "黑名单命中表内 %d 条（应为 7）" % len(denied))
ck(len(writable) == 29, "可写 = 29 条（36 − 7）", "可写 = %d 条" % len(writable))

# 黑名单是否有漏网（表外黑名单无意义 ⇒ 报出来）
miss = [r for r in DENY if r not in addr_in_table]
print("  ⚠ 黑名单中不在表内的 = %s" % ("(无)" if not miss else " ".join("0x%02X" % x for x in miss)))

# 关键放行项（协议 §6.2 H 闸里但固件应放行）
for r, why in [(0x01, "H1 电极映射（G9 切导联要写）"), (0x02, "H1 电极映射"),
               (0x2F, "H4 读回通道"), (0x27, "H5 DRDY 源"), (0x00, "CONFIG（STANDBY 解锁）")]:
    ck(r in writable, "0x%02X 固件放行 [%s]" % (r, why), "0x%02X 被误拒 [%s]" % (r, why))

# 表外地址（副判据）
for r in (0x30, 0x2B, 0x31, 0x40, 0x50):
    ck(r not in addr_in_table, "0x%02X 表外 ⇒ 拒写（副判据）" % r, "0x%02X 竟在表内" % r)

# ---------------------------------------------------------------- §3 边界表
print()
print("=" * 78)
print("§3  长度边界表（N vs LEN=1+2N vs 总长 6+LEN）")
print("=" * 78)
print("  %-4s %-6s %-8s %-12s %-22s" % ("N", "LEN", "总长B", "固件门", "CDC 单包"))
rows = [(0, "拒（argLen<3）"), (1, "收"), (2, "收"), (28, "收 ★策略B上限"),
        (29, "拒（argLen>58）"), (36, "拒（一次全表）")]
for n, exp in rows:
    ln = 1 + 2 * n
    tot = 6 + ln
    gate = "收" if (ln >= 3 and (ln & 1) and ln <= 58) else "拒"
    onepkt = "1 包（≤64）" if tot <= 64 else "2 包 ⇒ 覆盖式赋值 ⇒ 丢弃"
    print("  %-4d %-6d %-8d %-12s %-22s  %s" % (n, ln, tot, gate, onepkt, exp))
    if n in (1, 28):
        ck(gate == "收" and tot <= 64, "N=%d ⇒ LEN=%d／%dB 天然单包 ✓" % (n, ln, tot),
           "N=%d 边界异常" % n)
    if n in (29, 36):
        ck(gate == "拒", "N=%d ⇒ LEN=%d > 58 ⇒ 上层静默丢弃（不崩、不误写）" % (n, ln),
           "N=%d 竟被接受" % n)

ck((58 - 1) // 2 == 28, "策略 B 上限推导：N ≤ (VCOM_BIN_MAX_LEN−1)/2 = 28",
   "策略 B 上限推导不符（应为 28）")

# ---------------------------------------------------------------- §4 CRC 帧清单
print()
print("=" * 78)
print("§4  作业卡 CRC 帧清单（下行 0x80；CRC8 poly=0x07/init=0x00/不反射）")
print("=" * 78)

print("  自检：ecg_crc8(b'\\x01',1) = 0x%02X（源码注释期望 0x07）" % crc8([0x01]))
f_start = frame([0x01])
f_reset = frame([0x03])
ck(hexs(f_start) == "AA 55 80 01 00 01 5D", "交叉印证 START = AA 55 80 01 00 01 5D（台账已记录）",
   "START 帧不符：%s" % hexs(f_start))
ck(hexs(f_reset) == "AA 55 80 01 00 03 53", "交叉印证 RESET = AA 55 80 01 00 03 53（台账已记录）",
   "RESET 帧不符：%s" % hexs(f_reset))
print()

CARDS = [
    ("① 判据1+2 同帧：0x0A=0x03（应生效）＋ 0x21=0x01（应被拒）", [0x11, 0x0A, 0x03, 0x21, 0x01]),
    ("② 单条基线：0x0A=0x03",                                    [0x11, 0x0A, 0x03]),
    ("③ 副判据：表外 0x30=0x11 ⇒ 拒写不崩",                       [0x11, 0x30, 0x11]),
    ("④ H4 放行证：0x2F=0x30（回写原值，可验证固件不拒 H 闸）",      [0x11, 0x2F, 0x30]),
    ("⑤ 长度门证：argLen 偶数（非法）",                            [0x11, 0x0A]),
]
for name, p in CARDS:
    f = frame(p)
    print("  %-58s %2dB  %s" % (name, len(f), hexs(f)))
print()

# 策略 B 两批的实际帧（36 项 = 28 + 8）
ALL = [(r, v) for r, v in table]
b1 = ALL[:28]
b2 = ALL[28:]
for tag, batch in (("批1 N=28", b1), ("批2 N=%d" % len(b2), b2)):
    p = [0x11]
    for r, v in batch:
        p += [r, v]
    f = frame(p)
    print("  %-12s %2dB  %s%s" % (tag, len(f), hexs(f[:14]), " … " + hexs(f[-3:])))
ck(len(frame([0x11] + [x for pair in ALL[:28] for x in pair])) == 63,
   "批1 = 63 B（LEN=57）⇒ 单包落地 ✓", "批1 长度不符")
ck(len(frame([0x11] + [x for pair in ALL[28:] for x in pair])) == 6 + 1 + 2 * 8,
   "批2 = %d B（LEN=17）✓" % (6 + 1 + 2 * 8), "批2 长度不符")

# ---------------------------------------------------------------- §5 插入点
print()
print("=" * 78)
print("§5  落码插入点定位（3 文件 4 处）")
print("=" * 78)

v_lines = txt(SRC_V).split("\n")
for i, l in enumerate(v_lines):
    if re.match(r"\s*case\s+0x05u:", l):
        print("  vcomcmd.c  case 0x05u（SET_NOTCH）起始 = 1-based %d" % (i + 1))
        c05 = i + 1
    if "default:" in l and i > 400:
        print("  vcomcmd.c  default: 落点          = 1-based %d  ← case 0x11 插在此之前" % (i + 1))
        cdef = i + 1
ck(cdef - c05 > 0, "case 0x11 插入区间 = (%d, %d) 合法" % (c05, cdef), "插入区间异常")

c_lines = s_c.split("\n")
for i, l in enumerate(c_lines):
    if l.startswith("int ads1293_write_table"):
        print("  ads1293.c  ads1293_write_table     = 1-based %d  ← _reg_writable + set_regs 插在其后" % (i + 1))
        c_wt = i + 1
    if l.strip() == "};" and i > 270 and i < 320:
        c_end = i + 1
print("  ads1293.c  regs_leaderIII 表尾      = 1-based %d（新函数必须在表之后）" % c_end)
ck(c_wt > c_end, "新函数落在表体之后（%d > %d）⇒ 可引用 regs_leaderIII" % (c_wt, c_end),
   "新函数位置在表体之前 ⇒ 编译失败")

h_lines = txt(SRC_H).split("\n")
for i, l in enumerate(h_lines):
    if "int ads1293_reset_afe" in l:
        print("  ads1293.h  声明 ads1293_reset_afe = 1-based %d  ← 新声明插在附近" % (i + 1))
        c_h = i + 1
ck("ads1293_write_reg" not in txt(SRC_H),
   "ads1293.h **未**导出 ads1293_write_reg（⇒ 新函数必须封在 ads1293.c，vcomcmd 不直接碰 SPI）",
   "ads1293.h 竟然已导出 ads1293_write_reg")

# ---------------------------------------------------------------- §6 红线核对
print()
print("=" * 78)
print("§6  零改动核对（红线指纹）")
print("=" * 78)

body = "\n".join(lines[start:end + 1])
print("  表体 md5（当前）      = %s" % hashlib.md5(body.encode("utf-8")).hexdigest())
print("  表体行区间            = %d-%d（1-based）" % (start + 1, end + 1))
bak_c = os.path.join(BAK, "ads1293.c")
if os.path.exists(bak_c):
    bk = txt(bak_c).split("\n")
    bs = next(i for i, l in enumerate(bk) if l.strip().startswith("const uint8_t regs_leaderIII[][2]"))
    be = next(i for i in range(bs, len(bk)) if bk[i].strip() == "};")
    body_b = "\n".join(bk[bs:be + 1])
    print("  表体 md5（G7 定稿）   = %s" % hashlib.md5(body_b.encode("utf-8")).hexdigest())
    ck(hashlib.md5(body.encode("utf-8")).hexdigest() == hashlib.md5(body_b.encode("utf-8")).hexdigest(),
       "表体与 G7 定稿**逐字节相同** ✓", "表体与 G7 定稿不同（红线破）")
    ck(be - bs + 1 == end - start + 1, "表体行数不变（%d 行）" % (end - start + 1), "表体行数变了")

# Filter/ 14 文件 md5（⚠ 只数 .c/.h —— 目录里另有 1 个 .md 讲解文档，不是源码）
fmd5 = {}
for fn in sorted(os.listdir(FILTER_DIR)):
    fp = os.path.join(FILTER_DIR, fn)
    if os.path.isfile(fp) and fn.endswith((".c", ".h")):
        fmd5[fn] = hashlib.md5(read(fp)).hexdigest()
print("  Filter/ 源文件数      = %d（.c/.h；目录另有 %d 个非源码文件）"
      % (len(fmd5), len([f for f in os.listdir(FILTER_DIR) if os.path.isfile(os.path.join(FILTER_DIR, f))]) - len(fmd5)))
ck(len(fmd5) == 14, "Filter/ = 14 源文件（G7/G8 基线）", "Filter/ = %d 个 .c/.h（基线 14）" % len(fmd5))

# Filter/ 是否被今天碰过（落码前应为"全部早于今天"）
import datetime
_today = datetime.date.today()
_touched = [fn for fn in fmd5 if datetime.date.fromtimestamp(os.path.getmtime(os.path.join(FILTER_DIR, fn))) >= _today]
ck(not _touched, "Filter/ 全部文件 mtime 早于今天（本步零改动 ✓）",
   "Filter/ 今日被改动：%s" % " ".join(_touched))

# ECG_* 宏：ads1293.c 内只应有 ECG_LOD_EN（值须不变），Filter 参数宏（ECG_SECTION_* 等）不在此文件
ecg_macros = sorted(set(re.findall(r"#\s*define\s+(ECG_[A-Z0-9_]+)\s+([0-9]+)", s_c)))
print("  ads1293.c 的 ECG_* 宏 = %s" % (" ".join("%s=%s" % (k, v) for k, v in ecg_macros) or "(无)"))
ck(ecg_macros == [("ECG_LOD_EN", "0")],
   "ads1293.c 的 ECG_* 宏集合 = {ECG_LOD_EN=0}（与 G7/G8 定稿一致）",
   "ECG_* 宏集合变了：%s" % ecg_macros)

vmax = re.search(r"#\s*define\s+VCOM_BIN_MAX_LEN\s+([0-9]+)u", s_c if False else txt(SRC_V))
print("  VCOM_BIN_MAX_LEN      = %s（本步**不改**）" % (vmax.group(1) if vmax else "?"))
ck(vmax and int(vmax.group(1)) == 58, "VCOM_BIN_MAX_LEN 仍 58u ⇒ 策略 B 不碰它 ✓",
   "VCOM_BIN_MAX_LEN 被改动")

# ---------------------------------------------------------------- §7 基线现状
print()
print("=" * 78)
print("§7  基线现状（落码前）")
print("=" * 78)
v = txt(SRC_V)
ck("case 0x11u" not in v, "vcomcmd.c **无** case 0x11 ⇒ 当前 0x11 帧落到 default ⇒ 静默丢弃（不回任何字节）",
   "vcomcmd.c 已存在 case 0x11")
ck("case 0x10u" in v, "case 0x10u（REQ_SNAPSHOT）在位 ⇒ 状态门口径可照抄", "case 0x10u 缺失（口径参照丢了）")
ck("_bconvstart" in v, "状态门变量 _bconvstart 可见（vcomcmd.c:21 extern）", "_bconvstart 不可见")

# ---------------------------------------------------------------- 汇总
print()
print("=" * 78)
print("汇总：%d PASS ／ %d FAIL" % (N_OK, len(FAIL)))
for f in FAIL:
    print("  FAIL: %s" % f)
print("=" * 78)
sys.exit(1 if FAIL else 0)
