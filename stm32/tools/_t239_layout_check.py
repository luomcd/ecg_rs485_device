# -*- coding: utf-8 -*-
"""
T239 · `ecg_filter_t` 布局 / 双通道帧长 / 打包口径 离线断言（2026-11-12）
================================================================================
🛑 只读脚本：**不修改任何源码**（只读 ads1293.h / ads1293.c）。

为什么单独写一个（而不是继续用 `_t125_g8_sim.py`）：
  那个脚本按**源码比对**（`tools/_t125_bak/` 的 G7 定稿备份）工作，而 T239 改的是
  `ecg_filter_t` 本身 ⇒ 备份早已不是真值、且 `_t125_bak/` 目录已不存在。
  ⇒ 本脚本改为**从源码解析字段、按 AAPCS 自然对齐规则自己算偏移**，与备份无关，
     所以对以后的字段增删同样有效（唯一前提：结构体成员写成一行一个的简单声明）。

五项：
  §1  结构体布局 —— `sizeof == 32`、`dtcycles@4`、`dsrateraw@16`、`ch2_out@18`、
      `ecgraw2@20`、`filterraw@24`
  §2  帧长算术 —— nch=1 ⇒ 59 B，nch=2 ⇒ 109 B（N = _popoutcnt = 25）
  §3  打包口径 —— CH2 段**不再**引用 CH1 段（G8 时代是 `buff[8+idx*2+0]` 复制）
  §4  默认开关 —— `_bch2` 初值 = 1u（T239 Q1），`_braw853` 初值 = 0u
  §5  扇出完整性 —— `ads1293_set_notch/set_mode` 体内各含 **2** 次 ctx 设置（CH1+CH2）

行尾纪律：本文件自身按工程规范为纯 LF。
"""
import io
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_H = os.path.join(FW, "Ads1293", "ads1293.h")
SRC_C = os.path.join(FW, "Ads1293", "ads1293.c")

# C 类型 -> (size, align)，按 ARM AAPCS（Cortex-M4, ARM EABI）：
#   int16=2/2  int32=4/4  uint32=4/4  double=8/8（EABI：double 双字对齐）
TSIZE = {
    "uint8_t": (1, 1), "int8_t": (1, 1),
    "uint16_t": (2, 2), "int16_t": (2, 2),
    "uint32_t": (4, 4), "int32_t": (4, 4),
    "uint64_t": (8, 8), "int64_t": (8, 8),
    "float": (4, 4), "double": (8, 8), "char": (1, 1),
}

_ok = 0
_fail = []


def check(name, cond, detail=""):
    global _ok
    if cond:
        _ok += 1
        print("  OK  %s%s" % (name, ("   " + detail) if detail else ""))
    else:
        _fail.append(name)
        print("  XX  %s   %s" % (name, detail))


def read(p):
    return io.open(p, encoding="utf-8").read()


def strip_comments(text):
    """剥注释 —— 技能 §1.3：断言前必先剥注释，注释里的字面串会造成假 FAIL。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def parse_struct(text, name):
    """解析 `typedef struct { ... } name;` 的成员列表 ⇒ [(type, field, arr)]（只认简单声明）。

    🛑 体内容用 `[^{}]*` 而**不是** `.*?`：非贪婪的 `.*?` 会从**本文件第一个**
    `typedef struct {` 起步，一路吞到目标名后面的 `}`（本文件里 `ecg_data_t` 的 5 个
    成员就这样被算进了 `ecg_filter_t` ⇒ 偏移整体 +20 的假 FAIL，2026-11-12 首跑踩过）。
    """
    m = re.search(r"typedef\s+struct\s*\{([^{}]*)\}\s*" + re.escape(name) + r"\s*;",
                  text, flags=re.S)
    if not m:
        return None
    fields = []
    for line in m.group(1).split("\n"):
        line = line.strip().rstrip(";")
        if not line or line.startswith("#"):
            continue
        # 去掉数组维度（本结构体没有，但保留通用性）
        mm = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?$", line)
        if not mm:
            continue
        fields.append((mm.group(1), mm.group(2), mm.group(3)))
    return fields


def layout(fields):
    """返回 (offsets{field: off}, sizeof)；含尾部补齐。"""
    off = 0
    maxalign = 1
    offsets = {}
    for ctype, fname, arr in fields:
        if ctype not in TSIZE:
            raise SystemExit("!! 未知类型 %r（字段 %s）—— 需要先在 TSIZE 里登记" % (ctype, fname))
        size, align = TSIZE[ctype]
        if arr:
            n = int(arr.strip("[]"))
            size *= n
            align = max(align, 1)
        maxalign = max(maxalign, align)
        pad = (-off) % align
        off += pad
        offsets[fname] = off
        off += size
    total = off + ((-off) % maxalign)
    return offsets, total


ads_h = strip_comments(read(SRC_H))
ads_c_raw = read(SRC_C)
ads_c = strip_comments(ads_c_raw)

print("=" * 74)
print("§1  ecg_filter_t 布局（按 AAPCS 自然对齐自己算，不依赖备份）")
print("=" * 74)

fields = parse_struct(ads_h, "ecg_filter_t")
if not fields:
    print("  XX  解析 ecg_filter_t 失败 —— 成员是否写成了非「类型 名称」形式？")
    sys.exit(2)

offs, sz = layout(fields)
for ctype, fname, arr in fields:
    print("     %-10s %-12s @%-3d" % (ctype, fname, offs[fname]))
print("     %-10s %-12s = %d" % ("", "sizeof", sz))

check("sizeof == 32", sz == 32, "实得 %d" % sz)
for fname, want in (("idxraw", 0), ("dtcycles", 4), ("ecgraw", 8), ("idxfilter", 12),
                    ("dsrateraw", 16), ("ch2_out", 18), ("ecgraw2", 20), ("filterraw", 24)):
    got = offs.get(fname)
    check("%s@%d" % (fname, want), got == want, "实得 @%s" % got)

# dtcycles 必须是 32 位（P0：16 位装不下 93.7 kcyc 的 DRDY 周期）
dt_type = [t for t, f, _ in fields if f == "dtcycles"]
check("dtcycles 是 uint32_t", dt_type == ["uint32_t"], "实得 %s" % dt_type)
# ch2_out 必须是 int16_t（打包处按 2 字节写入）
c2_type = [t for t, f, _ in fields if f == "ch2_out"]
check("ch2_out 是 int16_t", c2_type == ["int16_t"], "实得 %s" % c2_type)

print()
print("=" * 74)
print("§2  双通道帧长算术（N = _popoutcnt = 25）")
print("=" * 74)
N = 25
for nch, want in ((1, 59), (2, 109)):
    L = 3 + 2 * nch * N
    total = 8 + nch * N * 2 + 1
    check("nch=%d ⇒ LEN=%d、总长=%d" % (nch, L, total), total == want,
          "实得 %d（期望 %d）" % (total, want))

print()
print("=" * 74)
print("§3  打包口径 —— CH2 段必须是真实数据，不得复制 CH1 段")
print("=" * 74)
# 反例（G8 时代）：buff[8+_popoutcnt*2+idx*2+0] = buff[8+idx*2+0];
bad = re.search(r"buff\[8\+_popoutcnt\*2\+idx\*2\+0\]\s*=\s*buff\[8\+idx\*2\+0\]", ads_c)
check("无 CH1→CH2 复制语句", bad is None, "仍存在复制：%s" % (bad.group(0) if bad else ""))
# 正例：CH2 段的值来自 ch2_out
good = re.search(r"v2\s*=\s*ecgpopout\[idx\]\.ch2_out", ads_c)
check("CH2 段取 ch2_out", good is not None)

print()
print("=" * 74)
print("§4  默认开关")
print("=" * 74)
check("_bch2 初值 = 1u（T239 Q1：默认双通道）",
      re.search(r"_bch2\s*=\s*1u\s*;", ads_c) is not None)
check("_braw853 初值 = 0u（500 Hz 输出，回归基线）",
      re.search(r"_braw853\s*=\s*0u\s*;", ads_c) is not None)

print()
print("=" * 74)
print("§5  设置扇出完整性（notch / mode 必须同时作用于 CH1 与 CH2）")
print("=" * 74)


def body_of(text, sig):
    """抓函数体（按花括号配平），返回 (body, 是否找到)。"""
    i = text.find(sig)
    if i < 0:
        return "", False
    j = text.find("{", i)
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j:k + 1], True
    return "", False


for sig, call in (("void ads1293_set_notch(int on)", "ecg_restore_ctx_set_notch"),
                  ("void ads1293_set_mode(int mon)", "ecg_restore_ctx_set_mode")):
    b, found = body_of(ads_c, sig)
    n = b.count(call) if found else -1
    check("%s 内 %s 出现 2 次（CH1+CH2）" % (sig.split("(")[0], call), n == 2,
          "实得 %d" % n)

print()
print("=" * 74)
print("结论：%d 项通过，%d 项失败" % (_ok, len(_fail)))
if _fail:
    for f in _fail:
        print("  XX  " + f)
    sys.exit(1)
print("全部通过 ✓")
