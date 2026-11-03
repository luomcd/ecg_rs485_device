# -*- coding: utf-8 -*-
"""
_t121_g7_sim.py —— G7（SET_MODE 0x04 / SET_NOTCH 0x05）落码后的离线验证
================================================================================

G7 的特殊性：**只改 1 个文件**（`Core/Src/vcomcmd.c`），且只调 `Filter/` **已有**的
公开函数（`ecg_filter_f64_set_mode/set_notch`）。所以本脚本的主线是：

  A. **纯增性（最硬的一条）** —— 把插入块**删掉**后，`vcomcmd.c` 必须**逐字节等于**
     G6 定稿备份。这不是"看着像新增"，是"删掉就回到原样"。
  B. **纪律断言**（把 4 条拍板做成可执行断言）：
       ① **不加状态门** —— `0x04`/`0x05` 段内无 `_bconvstart`
          🛑 且 `0x10` 的门**必须还在**（口径相反的两个分支各断言一次）
       ② STOP 态 **不回快照** —— 两段内无 `ads1293_send_snapshot`
       ③ **加** per-CMD 长度校验 —— 两段内各一条 `if (argLen != 2u) { return; }`
          ★ 且**老命令不动**：`0x01`/`0x02`/`0x03`/`0x10` 四段内**无**长度校验
       ④ ARG 非法值**交给内核** —— 两段内无 `if (... == 0u)` 之类自行判值
  C. **零改动的既有面** —— `ads1293.c`/`ads1293.h` 与备份**逐字节相同**；
     `vcomcmd.c` 的**下行前置分流**（长度/CRC/TYPE）与 **ASCII 分支**逐字节未动。
  D. **协议字节** —— 4 条下行帧（diag/mon/off/on）＋ 与 T106 §9g.4 的 `E8`/`FA` 对账。
  E. **逻辑复刻** —— PC 复刻 `set_mode`/`set_notch`/`ads1293_status_byte`，
     证明"发命令 ⇒ 下一帧 STA 跟随"以及长度门的三个边界。

口径来源：
  · 下行帧：`AA 55 | 0x80 | LEN(2B LE) | CMD | ARG | CRC8`，`LEN = 1 + len(ARG)`
    ⇒ `0x04`/`0x05` 的 LEN 恒 = 2、帧总长恒 = 8 B
  · ACK（协议 §5.1）：**下一帧显示帧的 STA**（不是快照）——
    `bit0` = mode（0 diag / 1 mon）｜`bit1` = notch（0 off / 1 on）
    ⚠ 权威来源＝源码 `ads1293.c` 的 `ads1293_status_byte()`；🛑 T106 §9i.7 曾把
      这两位**标注写反**（数值对、名字互换），本脚本把"位序"也做成断言防止复发。

用法:
  python tools/_t121_g7_sim.py [--verbose]
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import sys

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADS_C = os.path.join(ROOT, "Ads1293", "ads1293.c")
ADS_H = os.path.join(ROOT, "Ads1293", "ads1293.h")
VCOM = os.path.join(ROOT, "Core", "Src", "vcomcmd.c")
BAK = os.path.join(ROOT, "tools", "_bak_T121_20260923")

_ok = 0
_fail: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global _ok
    if cond:
        _ok += 1
        if VERBOSE:
            print("  OK  %s%s" % (name, "   " + detail if detail else ""))
    else:
        _fail.append(name)
        print("  XX  %s   %s" % (name, detail))


def read(p: str) -> str:
    return io.open(p, encoding="utf-8").read()


def md5(p: str) -> str:
    return hashlib.md5(io.open(p, "rb").read()).hexdigest()


def _code(text: str) -> str:
    """剥掉注释与空行 —— 技能 §1.3：断言前必先剥注释，否则注释里的字面串会误判。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    lines = [l.strip() for l in text.split("\n")]
    return "\n".join(l for l in lines if l)


def crc8(data: bytes) -> int:
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if (c & 0x80) else ((c << 1) & 0xFF)
    return c


def frame(cmd: int, arg: bytes = b"") -> bytes:
    L = 1 + len(arg)
    body = bytes([0x80, L & 0xFF, (L >> 8) & 0xFF, cmd]) + arg
    return bytes([0xAA, 0x55]) + body + bytes([crc8(body)])


# ============================================================================
ads = read(ADS_C)
adsh = read(ADS_H)
vcom = read(VCOM)
ads_old = read(os.path.join(BAK, "ads1293.c"))
adsh_old = read(os.path.join(BAK, "ads1293.h"))
vcom_old = read(os.path.join(BAK, "vcomcmd.c"))

print("=" * 74)
print("§0 文件与备份")
print("=" * 74)
check("三个相关文件都在", all(os.path.exists(p) for p in (ADS_C, ADS_H, VCOM)))
check("备份目录完整（3 文件）",
      all(os.path.exists(os.path.join(BAK, f)) for f in
          ("ads1293.c", "ads1293.h", "vcomcmd.c")))
check("🛑 ads1293.c 与备份**逐字节相同**（G7 不改它）", md5(ADS_C) == md5(os.path.join(BAK, "ads1293.c")))
check("🛑 ads1293.h 与备份**逐字节相同**（G7 不改它）", md5(ADS_H) == md5(os.path.join(BAK, "ads1293.h")))
check("🆕 vcomcmd.c 确有改动", md5(VCOM) != md5(os.path.join(BAK, "vcomcmd.c")))
check("★ 改动面 = **1 个文件**（G5/G6 是 3 个）", True,
      "shell 侧另有 git status 复核")

# ============================================================================
print()
print("=" * 74)
print("§1 ★★ 纯增性：删掉插入块后，逐字节等于 G6 定稿")
print("=" * 74)

BLK_START = "      case 0x04u:"
BLK_END_LINE = "        ecg_filter_f64_set_notch(&f64, src[6]);\n        break;\n"
assert BLK_START in vcom and BLK_END_LINE in vcom, "插入块锚点缺失 ⇒ 后续断言无意义"

i0 = vcom.index(BLK_START)
i1 = vcom.index(BLK_END_LINE) + len(BLK_END_LINE)
BLOCK = vcom[i0:i1]
blk_lines = BLOCK.rstrip("\n").split("\n")

check("插入块可定位（非零长度）", len(BLOCK) > 0)
check("插入块行数 = 32", len(blk_lines) == 32, "实为 %d 行" % len(blk_lines))
check("插入块首行 = `case 0x04u:`", blk_lines[0].startswith("      case 0x04u:"))
check("插入块末行 = case05 的 `break;`", blk_lines[-1].strip() == "break;")

# vcom 结构 = A + "\n" + BLOCK + "\n" + B ；old 结构 = A + "\n" + B
_stripped = vcom.replace("\n" + BLOCK + "\n", "\n", 1)
check("★★ 删掉插入块后 **逐字节等于** G6 定稿 vcomcmd.c", _stripped == vcom_old,
      "若不等于 ⇒ 有非预期的删改，不是纯增")
check("★ 纯增行数 = 33（32 行代码 + 1 个空行）",
      len(vcom.split("\n")) - len(vcom_old.split("\n")) == 33,
      "实为 %+d" % (len(vcom.split("\n")) - len(vcom_old.split("\n"))))
check("🛑 全程 CRLF（裸 LF = 0）", b"\n" not in io.open(VCOM, "rb").read().replace(b"\r\n", b""))

# ============================================================================
print()
print("=" * 74)
print("§2 纪律断言（把 4 条拍板做成可执行断言）")
print("=" * 74)

SW_I = vcom.index("    switch (src[5]) {")


def case_seg(tag: str, nxt: str) -> str:
    i = vcom.index(tag, SW_I)
    j = vcom.index(nxt, i)
    return vcom[i:j]


c10 = case_seg("      case 0x10u:", "      case 0x03u:")
c03 = case_seg("      case 0x03u:", "      case 0x01u:")
c01 = case_seg("      case 0x01u:", "      case 0x02u:")
c02 = case_seg("      case 0x02u:", "      case 0x04u:")
c04 = case_seg("      case 0x04u:", "      case 0x05u:")
c05 = case_seg("      case 0x05u:", "      default:")

c04c, c05c = _code(c04), _code(c05)
c10c, c03c = _code(c10), _code(c03)
c01c, c02c = _code(c01), _code(c02)

# ---- ① 不加状态门 ----
check("① case 0x04u 段**无** `_bconvstart`（不加状态门）", "_bconvstart" not in c04c)
check("① case 0x05u 段**无** `_bconvstart`（不加状态门）", "_bconvstart" not in c05c)
check("🛑 口径相反项：case 0x10u 的门**还在**（`if (_bconvstart)`）",
      "if (_bconvstart)" in c10c, "若没了 ⇒ 误改了已验收的分支")
check("🛑 口径相反项：case 0x03u 仍**无**门", "_bconvstart" not in c03c)

# ---- ② 不回快照 ----
check("② case 0x04u 段**无** `ads1293_send_snapshot`（不回快照）",
      "send_snapshot" not in c04c)
check("② case 0x05u 段**无** `ads1293_send_snapshot`（不回快照）",
      "send_snapshot" not in c05c)
check("🛑 口径相反项：case 0x10u 仍**有** `send_snapshot`（那条本来就要回）",
      "ads1293_send_snapshot(0x10u, NULL)" in c10c)

# ---- ③ per-CMD 长度校验（新命令从严、老命令不动）----
check("③ case 0x04u 段**有** `if (argLen != 2u)` ⇒ return",
      re.search(r"if \(argLen != 2u\) \{\s*return;\s*\}", c04c) is not None)
check("③ case 0x05u 段**有** `if (argLen != 2u)` ⇒ return",
      re.search(r"if \(argLen != 2u\) \{\s*return;\s*\}", c05c) is not None)
for _tag, _seg, _nm in (("0x01", c01c, "START"), ("0x02", c02c, "STOP"),
                        ("0x03", c03c, "RESET"), ("0x10", c10c, "REQ_SNAPSHOT")):
    check("★ 老命令不动：case %s（%s）段内**无**长度校验" % (_tag, _nm),
          "argLen" not in _seg, "若加了 ⇒ 改了已上板验收的基线")

# ---- ④ ARG 非法值交给内核 ----
check("④ case 0x04u 段**无**自行判值的 if（交内核容错）",
      re.search(r"src\[6\]\s*==\s*[01]u?", c04c) is None)
check("④ case 0x05u 段**无**自行判值的 if（交内核容错）",
      re.search(r"src\[6\]\s*==\s*[01]u?", c05c) is None)
check("④ 两段均把 `src[6]` **直接**传给内核",
      "ecg_filter_f64_set_mode(&f64, src[6]);" in c04c and
      "ecg_filter_f64_set_notch(&f64, src[6]);" in c05c)

# ---- 下行路径禁 printf_msg（内含 HAL_Delay(5)）----
check("🛑 case 0x04u 段**无** `printf_msg`（下行路径禁回显）", "printf_msg" not in c04c)
check("🛑 case 0x05u 段**无** `printf_msg`（下行路径禁回显）", "printf_msg" not in c05c)

# ---- 调用的是**已有公开函数**（G7 不改 Filter/、不改 .h）----
check("调用的内核是 Filter/ 的公开函数（G7 零新增依赖）",
      "ecg_filter_f64_set_mode" in c04c and "ecg_filter_f64_set_notch" in c05c)

print()
print("=" * 74)
print("§3 零改动的既有面")
print("=" * 74)

# 下行前置分流（长度/CRC/TYPE）逐字节未动
pre_new = vcom[vcom.index("    uint8_t  calc = 0x00u;"):SW_I]
pre_old = vcom_old[vcom_old.index("    uint8_t  calc = 0x00u;"):vcom_old.index("    switch (src[5]) {")]
check("★★ 下行前置分流（最小帧长/帧头/LEN 对核/CRC/TYPE）**逐字节未动**",
      pre_new == pre_old, "本步最危险的一段，必须零改动")

# ASCII 分支逐字节未动
ascii_new = vcom[vcom.index("void vcom_recv_analysis(void)"):]
ascii_old = vcom_old[vcom_old.index("void vcom_recv_analysis(void)"):]
check("★★ `vcom_recv_analysis()`（ASCII 通道）**逐字节未动**", ascii_new == ascii_old)

# ASCII 侧对同一内核的调用点未动
for _call in ("ecg_filter_f64_set_notch(&f64, 1);",
              "ecg_filter_f64_set_notch(&f64, 0);",
              "ecg_filter_f64_set_mode(&f64, 0);",
              "ecg_filter_f64_set_mode(&f64, 1);"):
    check("ASCII 侧调用点未动：`%s`" % _call, _call in vcom)

# ---- STA 位序（★ T106 §9i.7 曾标注写反）----
sta = ads[ads.index("uint8_t ads1293_status_byte(void)"):]
sta = sta[:sta.index("\n}\n") + 3]
sta_c = _code(sta)
check("★ STA 位序：`bit0` = mode ⇒ 用 `ecg_filter_f64_mode_is_mon`（0 diag / 1 mon）",
      "sta |= 0x01u;" in sta_c and
      sta_c.index("mode_is_mon") < sta_c.index("sta |= 0x01u;"),
      "🛑 §9i.7 曾把 bit0/bit1 的**名字**写反（数值是对的）——下一轮判读方向就靠这条")
check("★ STA 位序：`bit1` = notch ⇒ 用 `ecg_filter_f64_notch_is_on`（0 off / 1 on）",
      "sta |= 0x02u;" in sta_c and
      "notch_is_on" in sta_c and
      sta_c.index("notch_is_on") < sta_c.index("sta |= 0x02u;"))
check("★ STA 是**运行期读取**（协议 §4 硬约束：不得用编译期宏）",
      "mode_is_mon(&f64)" in sta_c and "notch_is_on(&f64)" in sta_c)
check("★ 显示帧打包用 `buff[7] = ads1293_status_byte()` ⇒ 命令后**下一帧**STA 即跟随",
      "buff[7] = ads1293_status_byte();" in _code(ads))

print()
print("=" * 74)
print("§4 协议字节（4 条下行帧 ＋ 与 T106 §9g.4 对账）")
print("=" * 74)

CASES = [
    ("SET_MODE(diag)", 0x04, bytes([0x00]), "AA 55 80 02 00 04 00 EF"),
    ("SET_MODE(mon)", 0x04, bytes([0x01]), "AA 55 80 02 00 04 01 E8"),
    ("SET_NOTCH(off)", 0x05, bytes([0x00]), "AA 55 80 02 00 05 00 FA"),
    ("SET_NOTCH(on)", 0x05, bytes([0x01]), "AA 55 80 02 00 05 01 FD"),
]
for nm, cmd, arg, want in CASES:
    f = frame(cmd, arg)
    check("%-16s = %s（len=%d）" % (nm, want, len(f)),
          " ".join("%02X" % x for x in f) == want)
    check("  ↳ 帧长恒 8 B ／ LEN 恒 2（LEN = 1 + len(ARG)）",
          len(f) == 8 and f[3] == 2 and f[4] == 0)
    check("  ↳ 末字节 CRC 自校验通过", f[-1] == crc8(f[2:-1]))

check("★★ 与 T106 §9g.4 记录逐字节一致（mon=`E8` / notch off=`FA`）",
      CASES[1][3].endswith("E8") and CASES[2][3].endswith("FA"))
check("★ G6 已验命令未被本步影响（START=`5D` / STOP=`54` 重算仍一致）",
      " ".join("%02X" % x for x in frame(0x01)) == "AA 55 80 01 00 01 5D" and
      " ".join("%02X" % x for x in frame(0x02)) == "AA 55 80 01 00 02 54")

# ---------------------------------------------------------------------------
# ★ 长度门的负用例（供上板用）—— 唯一来源，§5 直接复用
#   🛑 纪律（本脚本首版就栽在这）：负用例帧必须**除被测关卡外全合法** ——
#      TYPE/长度对核/CRC 全对、**只有 LEN ≠ 2**；否则会被更早的关卡拦下，
#      测到的是"CRC 坏"而不是"长度门生效"，两种原因在设备侧表现**都是无反应**、
#      根本分不出来。
def neg_04(arglen: int) -> bytes:
    body = bytes([0x80, arglen & 0xFF, 0x00, 0x04]) + bytes(arglen - 1)
    return bytes([0xAA, 0x55]) + body + bytes([crc8(body)])


NEG_LEN1 = neg_04(1)   # 7 B：缺 ARG
NEG_LEN3 = neg_04(3)   # 9 B：多 ARG
for nm, f in (("LEN=1（缺 ARG）", NEG_LEN1), ("LEN=3（多 ARG）", NEG_LEN3)):
    check("负用例全合法（TYPE/长度对核/CRC 都对，**只有 LEN≠2**）：0x04 的 %s" % nm,
          f[2] == 0x80 and len(f) == 6 + f[3] and f[-1] == crc8(f[2:-1]) and f[5] == 0x04,
          " ".join("%02X" % x for x in f))

print()
print("=" * 74)
print("§5 逻辑复刻（命令 ⇒ 下一帧 STA 跟随 ＋ 长度门三边界）")
print("=" * 74)


class FakeFilter:
    """复刻 ecg_filter_f64 的 mode/notch 状态 + ads1293_status_byte 的位装配。"""

    MODE_DEFAULT = 0        # ECG_MODE_DEFAULT = diag
    NOTCH_DEFAULT = 1       # ECG_NOTCH_DEFAULT_ON = 1

    def __init__(self):
        self.mode = self.MODE_DEFAULT
        self.notch = self.NOTCH_DEFAULT
        self.raw853 = 0

    # --- 复刻 ecg_filter_f64_set_mode（含容错 + 幂等）---
    def set_mode(self, mon):
        mon = 1 if mon else 0          # ← ④ 交内核的容错语义：非零即真
        if self.mode == mon:
            return False               # 幂等：无动作
        self.mode = mon
        return True                    # 换系数 + DC 预置（波形一次瞬态台阶）

    def set_notch(self, on):
        on = 1 if on else 0
        if self.notch == on:
            return False
        self.notch = on
        return True

    # --- 复刻 ads1293_status_byte ---
    def sta(self):
        s = 0
        if self.mode:
            s |= 0x01          # bit0 mode
        if self.notch:
            s |= 0x02          # bit1 notch
        if self.raw853:
            s |= 0x08          # bit3 raw853
        return s

    # --- 复刻 ecg_filter_f64_init 的 fail-safe 写回（P8）---
    def filter_chain_init(self):
        self.mode = self.MODE_DEFAULT
        self.notch = self.NOTCH_DEFAULT


def dispatch(f: FakeFilter, raw: bytes) -> str:
    """复刻 vcom_bin_dispatch 的 G7 段（只关心 0x04/0x05 与长度门）。"""
    if len(raw) != 6 + raw[3]:            # argLen 对核
        return "drop:len"
    if raw[-1] != crc8(raw[2:-1]):
        return "drop:crc"
    if raw[2] != 0x80:
        return "drop:type"
    cmd = raw[5]
    argLen = raw[3]
    if cmd in (0x04, 0x05):
        if argLen != 2:
            return "drop:argLen"
        v = raw[6]
        if cmd == 0x04:
            f.set_mode(v)
        else:
            f.set_notch(v)
        return "ok"
    return "other"


fl = FakeFilter()
check("上电初始：STA.bit0=0(diag) / bit1=1(notch on)", fl.sta() == 0x02,
      "实得 0x%02X" % fl.sta())

dispatch(fl, frame(0x04, bytes([1])))
check("★ 发 SET_MODE(mon) ⇒ 下一帧 STA.bit0 == 1", (fl.sta() & 0x01) == 0x01)
dispatch(fl, frame(0x04, bytes([0])))
check("★ 发 SET_MODE(diag) ⇒ 下一帧 STA.bit0 == 0", (fl.sta() & 0x01) == 0x00)
dispatch(fl, frame(0x05, bytes([0])))
check("★ 发 SET_NOTCH(off) ⇒ 下一帧 STA.bit1 == 0", (fl.sta() & 0x02) == 0x00)
dispatch(fl, frame(0x05, bytes([1])))
check("★ 发 SET_NOTCH(on) ⇒ 下一帧 STA.bit1 == 1", (fl.sta() & 0x02) == 0x02)

# 幂等
dispatch(fl, frame(0x04, bytes([1])))
s1 = fl.sta()
r = dispatch(fl, frame(0x04, bytes([1])))
check("幂等：连发两次 SET_MODE(mon) ⇒ STA 不变（第二次**不是失败**）",
      r == "ok" and fl.sta() == s1)

# ④ ARG 非法值走容错
dispatch(fl, frame(0x04, bytes([0x00])))
dispatch(fl, frame(0x04, bytes([0xA5])))
check("④ ARG=0xA5（非法值）⇒ 内核按非零＝mon 处理（bit0=1），**不报错**",
      (fl.sta() & 0x01) == 0x01)

# ③ 长度门三边界
f2 = FakeFilter()
before = f2.sta()
r1 = dispatch(f2, NEG_LEN1)     # ← §4 里构造的同一份帧（单一来源）
check("③ LEN=1（缺 ARG）⇒ 静默丢弃且**丢在长度门**，状态不变",
      r1 == "drop:argLen" and f2.sta() == before, "实得 %r" % r1)
r3 = dispatch(f2, NEG_LEN3)
check("③ LEN=3（多 ARG）⇒ 静默丢弃且**丢在长度门**，状态不变",
      r3 == "drop:argLen" and f2.sta() == before, "实得 %r" % r3)
check("★ 丢弃原因**可区分**（长度门 vs CRC vs 帧长）—— 上板无反应时能反推是哪道关",
      dispatch(FakeFilter(), NEG_LEN1) == "drop:argLen" and
      dispatch(FakeFilter(), NEG_LEN1[:-1] + bytes([0x00])) == "drop:crc")
r2 = dispatch(f2, frame(0x04, bytes([1])))
check("③ LEN=2 ⇒ 通过，状态改变", r2 == "ok" and (f2.sta() & 0x01) == 0x01)

# P8：START 会复位 mode/notch
f2.filter_chain_init()
check("★ P8：filter_chain_init（START 内）把 mode/notch 写回默认 ⇒ bit0=0 / bit1=1",
      f2.sta() == 0x02,
      "⇒ START 后必须**补发** SET_MODE/SET_NOTCH（这是 G6 已记的上位机口径）")

# ============================================================================
print()
print("=" * 74)
print("结果：%d 通过 / %d 失败" % (_ok, len(_fail)))
if _fail:
    print("失败项：")
    for f in _fail:
        print("  - %s" % f)
print("=" * 74)
sys.exit(1 if _fail else 0)
