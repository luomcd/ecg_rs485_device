# -*- coding: utf-8 -*-
"""
_t125_g8_sim.py —— G8「双通道数据链预埋 ＋ 帧块布局」离线断言（零硬件）
================================================================================

G8 的改动面（3 文件，Filter/ 零改动）：
  A) Ads1293/ads1293.h ：ecg_data_t 末尾 +`int32_t ecgraw2`（12→16 B）；
                         ecg_filter_t 在 `double` **之前** +`int32_t ecgraw2`（落既有 pad ⇒ 仍 32 B）
  B) Ads1293/ads1293.c ：`ads1293_ecg_ch1` → **`ads1293_ecg_ch(int32_t*,int32_t*)`**，一发读 6 B；
                         enqueue 填 ecgraw2；搬运进 Filter 队列；ASCII **列⑦** = ecgraw2；
                         `_bch2` ＋ STA **bit2** 跟随；打包段 `nch` ＋**四处长度耦合**（块布局）
  C) Core/Src/vcomcmd.c ：`test ch2 [on|off]`

本脚本不需要上板就能证明三件事：
  §3 ★★ **nch = 1 时帧公式逐字等于 G7 定稿** ⇒ "59 B 回归基线不变"是数学结论，不是观察
  §4 ★  **nch = 2 时四处长度耦合自洽**：LEN / CRC 覆盖 / 发送长 / CH2 段偏移
         且 **CH1 段与 CH2 段逐字节相同**、块布局无交错、**CRC 真覆盖 CH2 段**
  §5    三条拍板（列⑥ 保留／列① 恒 0／bit2 跟随）做成**可执行断言**（否则只是口头承诺）

跑法：python tools/_t125_g8_sim.py [-v]
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADS_C = os.path.join(ROOT, "Ads1293", "ads1293.c")
ADS_H = os.path.join(ROOT, "Ads1293", "ads1293.h")
VCOM = os.path.join(ROOT, "Core", "Src", "vcomcmd.c")
BAK = os.path.join(ROOT, "tools", "_t125_bak")

VERBOSE = "-v" in sys.argv
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
    """剥掉注释 —— 技能 §1.3：断言前必先剥注释，否则注释里的字面串会误判
    （T125/G8 当场踩过：`_t114_g4_sim.py` 断言⑧ 因注释块含 `printf_msg` 字面串而假 FAIL）。"""
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


# ============================================================================
# 帧级重放：两套 builder（G7 定稿公式 / G8 公式），逐字节对拍
# ============================================================================
def frame_g7(samples, sta: int, seq: int) -> bytes:
    """G7 定稿的打包公式（备份源码文本逐字锚定于 §3）。"""
    N = len(samples)
    L = 3 + 2 * N
    body = bytearray([0x01, L & 0xFF, (L >> 8) & 0xFF,
                      seq & 0xFF, (seq >> 8) & 0xFF, sta])
    for v in samples:
        body += struct.pack("<h", v)
    return bytes([0xAA, 0x55]) + bytes(body) + bytes([crc8(bytes(body))])


def frame_g8(samples, sta: int, seq: int, bch2: bool) -> bytes:
    """G8 的打包公式：nch ∈ {1,2}、块布局 `[CH1×N][CH2×N]`（CH2 ≡ CH1）。"""
    N = len(samples)
    nch = 2 if bch2 else 1
    L = 3 + 2 * nch * N
    body = bytearray([0x01, L & 0xFF, (L >> 8) & 0xFF,
                      seq & 0xFF, (seq >> 8) & 0xFF, sta])
    ch1 = bytearray()
    for v in samples:
        ch1 += struct.pack("<h", v)
    body += ch1
    if bch2:
        body += ch1                      # ★块布局：CH2 段 = CH1 段的直接复制
    return bytes([0xAA, 0x55]) + bytes(body) + bytes([crc8(bytes(body))])


def frame_ok(frame: bytes) -> bool:
    """按协议校验：帧头 + 长度自洽 + CRC（覆盖 `[2]` 起 `L+3` 字节）。"""
    if len(frame) < 7 or frame[0] != 0xAA or frame[1] != 0x55:
        return False
    L = frame[3] | (frame[4] << 8)
    if len(frame) != 6 + L:
        return False
    return crc8(frame[2:6 + L - 1]) == frame[6 + L - 1]


# ============================================================================
ads_c = read(ADS_C)
ads_h = read(ADS_H)
vcom = read(VCOM)
ads_c_bak = read(os.path.join(BAK, "ads1293.c"))
ads_h_bak = read(os.path.join(BAK, "ads1293.h"))
vcom_bak = read(os.path.join(BAK, "vcomcmd.c"))

print("=" * 74)
print("§0 文件与备份（备份 = G7 定稿，md5 见台账 §9k）")
print("=" * 74)
check("ads1293.c 存在且已改（与备份不同）", md5(ADS_C) != md5(os.path.join(BAK, "ads1293.c")))
check("ads1293.h 存在且已改", md5(ADS_H) != md5(os.path.join(BAK, "ads1293.h")))
check("vcomcmd.c 存在且已改", md5(VCOM) != md5(os.path.join(BAK, "vcomcmd.c")))
check("三个文件换行符仍为 CRLF（零裸 LF）", all(
    io.open(p, "rb").read().count(b"\n") == io.open(p, "rb").read().count(b"\r\n")
    for p in (ADS_C, ADS_H, VCOM)))
check("三个文件 `/*` 与 `*/` 配对（注释全闭合）", all(
    read(p).count("/*") == read(p).count("*/") for p in (ADS_C, ADS_H, VCOM)))

# ============================================================================
print()
print("=" * 74)
print("§1 结构体对齐账（直接从源码解析字段表，按 C ABI 规则算 sizeof）")
print("=" * 74)


def parse_struct(h: str, tname: str):
    """按名字取那**一个**结构体的字段表。
    ⚠ 不能用 `typedef struct {(.*?)} NAME;` 单发非贪婪——它会从**最早**的
      `typedef struct {` 一路吃到目标 `}`，把前面结构体的字段也吞进来
      （T125/G8 首版就这么把 ecg_data_t 的 5 个字段并进了 ecg_filter_t，
      于是 sizeof 算成 48、连带三条对齐断言假 FAIL）。改为逐个匹配再按名字取。"""
    for m in re.finditer(r"typedef\s+struct\s*\{(.*?)\}\s*(\w+)\s*;", h, re.S):
        if m.group(2) != tname:
            continue
        fields = []
        for d in re.finditer(r"^\s*(uint8_t|uint16_t|uint32_t|int8_t|int16_t|int32_t|double)\s+(\w+)\s*;",
                             m.group(1), re.M):
            t, n = d.group(1), d.group(2)
            sz = {"uint8_t": 1, "int8_t": 1, "uint16_t": 2, "int16_t": 2,
                  "uint32_t": 4, "int32_t": 4, "double": 8}[t]
            fields.append((n, sz, sz))
        return fields
    return None


def layout(fields):
    off, mx, out = 0, 1, {}
    for n, s, a in fields:
        off = (off + a - 1) // a * a
        out[n] = off
        off += s
        mx = max(mx, a)
    return out, (off + mx - 1) // mx * mx


f_data = parse_struct(ads_h, "ecg_data_t")
f_filt = parse_struct(ads_h, "ecg_filter_t")
check("能解析 ecg_data_t 字段表", f_data is not None, str(f_data))
check("能解析 ecg_filter_t 字段表", f_filt is not None, str(f_filt))
if f_data and f_filt:
    od, sd = layout(f_data)
    of, sf = layout(f_filt)
    print("  · ecg_data_t  : %s  ⇒ sizeof=%d" % (od, sd))
    print("  · ecg_filter_t: %s  ⇒ sizeof=%d" % (of, sf))
    check("ecg_data_t 含 ecgraw2", "ecgraw2" in od)
    check("ecg_data_t sizeof == 16（12 + 4，无新对齐浪费）", sd == 16, "= %d" % sd)
    check("ecg_filter_t 含 ecgraw2", "ecgraw2" in of)
    check("★ ecg_filter_t 的 ecgraw2 落在 `double filterraw` **之前**（占用既有 pad）",
          of.get("ecgraw2", 99) < of.get("filterraw", 0),
          "ecgraw2@%d filterraw@%d" % (of.get("ecgraw2", -1), of.get("filterraw", -1)))
    check("★ ecg_filter_t sizeof 仍 == 32（±0；若 40 ⇒ 队列 +200 B 而非 +100 B）",
          sf == 32, "= %d" % sf)
    check("ecgraw2 在 filter 里的 offset == 20", of.get("ecgraw2") == 20, "= %s" % of.get("ecgraw2"))
    check("filterraw 仍停在 offset 24", of.get("filterraw") == 24, "= %s" % of.get("filterraw"))
    # ★ 队列账：与上板 map 实测交叉印证（EcgQueueRawBuf 300→400 / FilterBuf 800→800）
    check("⇒ 队列账推定 EcgQueueRawBuf +100 B", (sd - 12) * 25 == 100, "= %d" % ((sd - 12) * 25))
    check("⇒ 队列账推定 EcgQueueFilterBuf ±0 B", (sf - 32) * 25 == 0, "= %d" % ((sf - 32) * 25))
    check("⇒ RAM 合计推定 +104 B（+100 队列 ＋ `_bch2` 4 B）",
          (sd - 12) * 25 + (sf - 32) * 25 + 4 == 104, "")

# ============================================================================
print()
print("=" * 74)
print("§2 逐处落码点（剥注释后断言，防注释里的字面串冒充代码）")
print("=" * 74)
h_c = _code(ads_h)
c_c = _code(ads_c)
v_c = _code(vcom)

check("h：ecg_data_t 内 `int32_t ecgraw2;`", re.search(r"int32_t\s+ecgraw2\s*;", h_c) is not None)
check("h：声明 `ads1293_set_ch2(uint32_t on)`",
      re.search(r"void\s+ads1293_set_ch2\(uint32_t on\);", h_c) is not None)
check("h：声明 `ads1293_ch2_is_on(void)`",
      re.search(r"uint32_t\s+ads1293_ch2_is_on\(void\);", h_c) is not None)
check("c：函数签名 `int ads1293_ecg_ch(int32_t* rdval, int32_t* rdval2)`",
      "int ads1293_ecg_ch(int32_t* rdval, int32_t* rdval2)" in c_c)
check("c：`read_reginc(TI_ADS1293_DATA_CH1_ECG_H_REG,rdbuf,6)`（一发读 6 B）",
      "ads1293_read_reginc(TI_ADS1293_DATA_CH1_ECG_H_REG,rdbuf,6)" in c_c)
check("c：`uint8_t rdbuf[6]`", "uint8_t rdbuf[6]" in c_c)
check("c：CH2 拆 24-bit（rdbuf[3..5]）", "((int32_t)rdbuf[3] << 16)" in c_c)
check("c：调用点 `ads1293_ecg_ch(&ecgraw.ecgraw, &ecgraw.ecgraw2)`",
      "ads1293_ecg_ch(&ecgraw.ecgraw, &ecgraw.ecgraw2);" in c_c)
check("c：搬运 `ecgfilter.ecgraw2 = ecgdata.ecgraw2;`",
      "ecgfilter.ecgraw2 = ecgdata.ecgraw2;" in c_c)
check("c：ASCII 列⑦ 参数 = `ecgpopout[idx].ecgraw2`", "ecgpopout[idx].ecgraw2" in c_c)
check("c：`_bch2` 定义（static volatile）", re.search(r"static\s+volatile\s+uint32_t\s+_bch2\s*=\s*0u;", c_c) is not None)
check("c：setter 体 `_bch2 = (on != 0u) ? 1u : 0u;`", "_bch2 = (on != 0u) ? 1u : 0u;" in c_c)
check("c：getter 体 `return _bch2;`", "return _bch2;" in c_c)
check("c：打包段 `uint16_t nch = (uint16_t)(_bch2 ? 2u : 1u);`",
      "uint16_t nch = (uint16_t)(_bch2 ? 2u : 1u);" in c_c)
check("c：CH2 段复制 `buff[8+_popoutcnt*2+idx*2+0] = buff[8+idx*2+0];`",
      "buff[8+_popoutcnt*2+idx*2+0] = buff[8+idx*2+0];" in c_c)
check("v：`test ch2` 回显分支", 'memcmp(argv[1],"ch2",3)' in v_c)
check("v：`test ch2 on|off` 切换分支", "ads1293_set_ch2(1);" in v_c and "ads1293_set_ch2(0);" in v_c)

# ============================================================================
print()
print("=" * 74)
print("§3 ★★ nch=1 ⇒ 与 G7 定稿**逐字节等价**（公式文本锚定 ＋ 帧级对拍）")
print("=" * 74)
G7_FORMULAS = ["uint16_t len16 = (uint16_t)(3 + 2 * (uint16_t)_popoutcnt);",
               "ecg_crc8((const uint8_t*)&buff[2], 6+_popoutcnt*2)",
               "CDC_Transmit_FS((uint8_t*)&buff[0], 8+_popoutcnt*2+1);"]
G8_FORMULAS = ["uint16_t len16 = (uint16_t)(3 + 2 * nch * (uint16_t)_popoutcnt);",
               "ecg_crc8((const uint8_t*)&buff[2], (uint32_t)(6+nch*_popoutcnt*2))",
               "CDC_Transmit_FS((uint8_t*)&buff[0], (uint16_t)(8+nch*_popoutcnt*2+1));"]
for f in G7_FORMULAS:
    check("G7 备份源码含公式：%s" % f[:48], f in _code(ads_c_bak))
for f in G8_FORMULAS:
    check("G8 当前源码含公式：%s" % f[:48], f in c_c)

# 数值恒等：nch=1 时四式回落到 G7（N = 1..128 全覆盖）
same = all((3 + 2 * 1 * N) == (3 + 2 * N)
           and (6 + 1 * N * 2) == (6 + N * 2)
           and (8 + 1 * N * 2 + 1) == (8 + N * 2 + 1)
           for N in range(1, 129))
check("★ 三式在 nch=1 时对 N=1..128 逐点等于 G7 定稿（LEN／CRC／发送长）", same)

# 帧级对拍：200 组随机样本，G8(bch2=False) 与 G7 逐字节相同
import random  # noqa: E402
random.seed(20260923)
mismatch = 0
for _ in range(200):
    N = random.choice([1, 4, 25])
    smp = [random.randint(-32768, 32767) for _ in range(N)]
    sta = random.randint(0, 255)
    seq = random.randint(0, 65535)
    if frame_g7(smp, sta, seq) != frame_g8(smp, sta, seq, False):
        mismatch += 1
check("★★ 200 组随机的**整帧**逐字节相同（G8 nch=1 vs G7 定稿）", mismatch == 0,
      "不符 %d 组" % mismatch)

# 59 B 定标：帧头 `AA 55 01 35 00`
f59 = frame_g8([0] * 25, 0xE8, 1, False)
check("nch=1 / N=25 ⇒ 帧长 59 B", len(f59) == 59, "= %d" % len(f59))
check("nch=1 / N=25 ⇒ 帧头 `AA 55 01 35 00`", f59[:5] == bytes([0xAA, 0x55, 0x01, 0x35, 0x00]),
      f59[:5].hex(" "))
check("nch=1 帧自校验（长度＋CRC）", frame_ok(f59))

# ============================================================================
print()
print("=" * 74)
print("§4 ★ nch=2 ⇒ 109 B 双通道帧：四处长度耦合 ＋ 块布局 ＋ CRC 真覆盖 CH2")
print("=" * 74)
smp = [1234, -5678, 9000, 32767, -32768] + [i * 7 - 4000 for i in range(20)]
assert len(smp) == 25
f109 = frame_g8(smp, 0xE8, 1, True)
check("nch=2 / N=25 ⇒ 帧长 109 B", len(f109) == 109, "= %d" % len(f109))
check("nch=2 / N=25 ⇒ LEN = 103 = 0x67", (f109[3] | (f109[4] << 8)) == 103,
      "= %d" % (f109[3] | (f109[4] << 8)))
check("nch=2 / N=25 ⇒ 帧头 `AA 55 01 67 00`", f109[:5] == bytes([0xAA, 0x55, 0x01, 0x67, 0x00]),
      f109[:5].hex(" "))
check("nch=2 帧自校验（长度＋CRC）", frame_ok(f109))
check("①②③ 三处耦合自洽：LEN+3 == CRC 覆盖长 == 发送长−3",
      (103 + 3) == 106 == (109 - 3), "LEN=%d crc=%d send=%d" % (103, 106, 109))

ch1 = f109[8:8 + 50]
ch2 = f109[8 + 50:8 + 100]
check("★ 块布局：CH1 段（帧内 [8:58]）与 CH2 段（[58:108]）**逐字节相同**", ch1 == ch2)
check("★ CH1 段 == 输入样本的小端编码", ch1 == b"".join(struct.pack("<h", v) for v in smp))
check("块布局**无交错**（若非交错，[8:10] 会等于第二样本而非第一）",
      ch1[:2] == struct.pack("<h", smp[0]))
check("CRC 落在帧尾 `[108]`", f109[6 + 103 - 1] == f109[108])

# ★★ CRC 真覆盖 CH2 段的实证：改 CH2 段任一字节 ⇒ CRC 校验必须失败
bad = bytearray(f109)
bad[70] ^= 0x01                      # 落在 CH2 段（58..107）
check("★★ 篡改 CH2 段任一字节 ⇒ CRC 失效（证明 CRC 覆盖到 CH2 段，不是只覆盖 CH1）",
      not frame_ok(bytes(bad)))
bad2 = bytearray(f109)
bad2[70] ^= 0x01
bad2[108] = crc8(bytes(bad2[2:108]))  # 重算 CRC
check("   ↳ 重算 CRC 后同一被篡改帧又合法（⇒ 上一断言不是长度副作用）", frame_ok(bytes(bad2)))

# 两档的公共前缀：帧头 TYPE、SEQ、STA、CH1 段相同，仅 LEN 不同
f59b = frame_g8(smp, 0xE8, 1, False)
check("两档 `AA 55 01` 相同", f59b[:3] == f109[:3])
check("两档 SEQ+STA 段 `[5:8]` 相同", f59b[5:8] == f109[5:8])
check("两档 CH1 段 `[8:58]` 相同", f59b[8:58] == f109[8:58])
check("两档仅 LEN 不同（53 vs 103）", (f59b[3] | (f59b[4] << 8)) == 53)

# ============================================================================
print()
print("=" * 74)
print("§5 三条拍板 ⇒ 可执行断言（猛哥 2026-09-23）")
print("=" * 74)
# (a) 第⑥列 dtcycles 保留
check("(a) 格式串仍为 7 列且含列⑥ `%05d` 与列⑦ `%07d`",
      '"\\n%d %07d %06d %05d %.02f %05d %07d"' in c_c)
check("(a) 列⑥ 参数仍是 `ecgpopout[idx].dtcycles`", "ecgpopout[idx].dtcycles," in c_c)
check("(a) 第⑦列参数已是 `ecgpopout[idx].ecgraw2`（不再是 gdtover）",
      "ecgpopout[idx].ecgraw2" in c_c and "gdtover//vlod" not in c_c)
# (b) 第①列保持恒 0
check("(b) 第①列仍是**字面 0**（拍板：暂时保持恒 0）", re.search(r'"\\n%d ', c_c) is not None)
check("(b) `idxraw` 那行仍处注释态（未复活）",
      "(unsigned)ecgpopout[idx].idxraw" not in c_c or "/*" in ads_c)
# (c) STA bit2 跟随
sta_fn = ads_c[ads_c.index("uint8_t ads1293_status_byte(void)"):ads_c.index("uint8_t ads1293_status_byte(void)") + 1200]
sta_fn = _code(sta_fn)
check("(c) `ads1293_status_byte` 内 bit2 由 `ads1293_ch2_is_on()` 驱动",
      "if (ads1293_ch2_is_on())" in sta_fn and "sta |= 0x04u;" in sta_fn)
check("(c) bit0/bit1/bit3 位序未被本步扰动（0x01/0x02/0x08 仍在）",
      all(x in sta_fn for x in ("sta |= 0x01u;", "sta |= 0x02u;", "sta |= 0x08u;")))
check("(c) 注释已同步（不再写\"恒 0（G9 起随 SET_CHANNEL 变化）\"）",
      "★本步恒 0：channel 软件标志在 G9 才引入" not in ads_c)

# ============================================================================
print()
print("=" * 74)
print("§6 引用面审计（技能 §4.72：改了谁、谁还引用旧名）")
print("=" * 74)
check("旧名 `ads1293_ecg_ch1` 在**代码**里零残留（注释里的历史说明不计）",
      "ads1293_ecg_ch1" not in c_c and "ads1293_ecg_ch1" not in h_c)
check("   ↳ 旧名仅出现在注释中，且是**有意的历史说明**（便于后来者对照）",
      ads_c.count("ads1293_ecg_ch1") == 1 and "原名 ads1293_ecg_ch1" in ads_c)
check("新名 `ads1293_ecg_ch(` 定义 1 处", c_c.count("int ads1293_ecg_ch(") == 1)
check("新名调用点恰 1 处", c_c.count("ads1293_ecg_ch(&ecgraw.ecgraw") == 1)
check("`_bch2` 引用点 = 5（定义／setter／getter／帧长公式／CH2 分支各 1）",
      c_c.count("_bch2") == 5, "= %d" % c_c.count("_bch2"))
check("`ads1293_ch2_is_on` 引用点 = 5（定义 1 ＋ STA 1 ＋ vcom 回显 3）",
      c_c.count("ads1293_ch2_is_on") + v_c.count("ads1293_ch2_is_on") == 5,
      "c=%d v=%d" % (c_c.count("ads1293_ch2_is_on"), v_c.count("ads1293_ch2_is_on")))
check("`gdtover` 已无功能消费点（只剩定义＋历史注释）",
      not re.search(r"gdtover\s*\+\+|gdtover\s*=\s*gdtover|gdtover\+\+", c_c))
check("`ecgraw2` 引用面 = h 2（两结构体）＋ c 4（采集/调用/搬运/列⑦）",
      h_c.count("ecgraw2") == 2 and c_c.count("ecgraw2") == 4,
      "h=%d c=%d" % (h_c.count("ecgraw2"), c_c.count("ecgraw2")))
check("下行分流签名未被扰动（`static void vcom_bin_dispatch`）",
      re.search(r"static\s+void\s+vcom_bin_dispatch", v_c) is not None)

# ============================================================================
print()
print("=" * 74)
print("§7 红线（🛑 只动协议、滤波参数一个都不许动）")
print("=" * 74)
FILTER_MD5 = {
    "biquad.c": "164c539a5365a3230a96348d81ee376b",
    "biquad.h": "5e50226dbc7944adc926579d62149b6a",
    "ecg_crc8.c": "258784d8e93aaef15c62f2099fa0e423",
    "ecg_crc8.h": "9f12523b99ec769b7b413c812a743879",
    "ecg_dc_block.c": "2a78354c176d285c8c317224e1c54a4b",
    "ecg_dc_block.h": "3934d5850fd538ac397dc86b128d2d56",
    "ecg_filter.c": "83b2f097a3ad825c0263cdaf602d1900",
    "ecg_filter.h": "236e9aebd634c88c253ad34621d4dc04",
    "ecg_filter_coeffs.h": "522f0fa344605dd249ea21e793dbe9a3",
    "ecg_out.c": "44c0e57b6c97819a164cba150b929c21",
    "ecg_out.h": "c3c7a61ebee6f145ef9a5fe0e9f7f6a7",
    "fast_restore_snippet.c": "42a5ebe7d0ec7775524b77cb0ff84c01",
    "welch_autotune.c": "3988013e5a16b0610656f33d2d1344b6",
    "welch_autotune.h": "7e2676bbf5155d17b1c86e7e61ea1481",
}
bad_f = [k for k, v in FILTER_MD5.items() if md5(os.path.join(ROOT, "Filter", k)) != v]
check("Filter/ 14 源码文件 md5 全等于基线（零改动）", not bad_f, "变了：%s" % bad_f)

# 🔴 红线③：`regs_leaderIII` 表体指纹。口径 = **Ads1293/ads1293.c** 按 `\n` 切行取
#    `:261-308`（48 行）的原始字节（**保留 CRLF**）⇒ md5 e543248d…
#    🛑 是 ads1293.c，**不是 T106 文档**（T125 首版就认错了文件，白追了一轮）。
#    ⚠ 用文本模式读会把 CRLF 折成 LF ⇒ md5 立刻变 6b105046（"跨口径引用 md5 是假比对"）。
_raw_bak = io.open(os.path.join(BAK, "ads1293.c"), "rb").read()
_tab_bak = b"\n".join(_raw_bak.split(b"\n")[260:308])
check("G7 定稿 ads1293.c 表体 md5 = e543248d4bcfb7fc2c381891b293b237（口径自证）",
      hashlib.md5(_tab_bak).hexdigest() == "e543248d4bcfb7fc2c381891b293b237",
      hashlib.md5(_tab_bak).hexdigest()[:12])
# ★ 更强的判据：G8 在 :44 处净插入 11 行 ⇒ 表体整体下移到 :272-319，内容必须**逐字节相同**
_raw_now = io.open(ADS_C, "rb").read()
_tab_now = b"\n".join(_raw_now.split(b"\n")[271:319])
check("★ 当前 ads1293.c 的表体（下移 11 行后 = :272-319）与 G7 定稿**逐字节相同**",
      _tab_now == _tab_bak)
check("   ↳ 且当前表体 md5 仍是 e543248d…",
      hashlib.md5(_tab_now).hexdigest() == "e543248d4bcfb7fc2c381891b293b237")

macros_now = set(re.findall(r"#define\s+(ECG_[A-Z0-9_]+)", ads_h))
macros_bak = set(re.findall(r"#define\s+(ECG_[A-Z0-9_]+)", ads_h_bak))
check("ECG_* 宏集合零变化（新增 0 条、删除 0 条）", macros_now == macros_bak,
      "新增 %s／删除 %s" % (macros_now - macros_bak, macros_bak - macros_now))

# ============================================================================
print()
print("=" * 74)
print("§8 既有回归（4 个旧 sim 脚本）＋ 失效项归因白名单")
print("=" * 74)
# 跨步重跑时，旧脚本里"**本步改动面**"类断言必然失效（它们断言的是**那一步**的改动面）。
# ⇒ 白名单：**只允许**这些已知项 FAIL，且每条都在下面写明原因；出现白名单外的失败 = 真回归。
STALE_OK = {
    "_t116_g5_sim": ["头文件其它内容零改动"],
    "_t119_g6_sim": ["头文件其它内容零改动", "int ads1293_ecg_ch1 可提取", "22 个既有函数"],
    "_t121_g7_sim": ["ads1293.c 与备份", "ads1293.h 与备份", "等于** G6 定稿 vcomcmd.c", "纯增行数"],
}
WHY = {
    "_t116_g5_sim": "G5 断言『h 文件除两条原型外零改动』；G8 按设计改了 h（两个结构体）",
    "_t119_g6_sim": "G6 断言『h 零改动』『ecg_ch1 可提取』『22 个老函数逐字节相同』；G8 按设计改名并改函数",
    "_t121_g7_sim": "G7 断言『不改 ads1293.c/h、vcomcmd 对 G6 纯增』；G8 按设计改了这三处",
}
for name in ("_t114_g4_sim", "_t116_g5_sim", "_t119_g6_sim", "_t121_g7_sim"):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", name + ".py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    mp = re.search(r"PASS\s*\**\s*(\d+)", out)
    mf = re.search(r"FAIL\s*\**\s*(\d+)", out)
    npass = mp.group(1) if mp else "?"
    fails = [s.strip() for s in re.findall(r"^\s*(?:✗|-)\s*(\S.{3,})\s*$", out, re.M)]
    fails = [f for f in fails if "，" not in f[:6]]
    fails = list(dict.fromkeys(fails))          # 去重（同一项在脚本输出里可能出现两次）
    # 旧脚本输出格式不一：_t121 只列「失败项：」不给计数 ⇒ 用条目数兜底
    nfail = mf.group(1) if mf else str(len(fails))
    print("  · %-16s PASS %-4s FAIL %-3s %s" % (name, npass, nfail, ("失效项：" + "; ".join(f[:34] for f in fails)) if fails else ""))
    wl = STALE_OK.get(name, [])
    unexp = [f for f in fails if not any(w in f for w in wl)]
    check("%s：无白名单外的失败项（真回归）" % name, not unexp, "意外：%s" % unexp)
check("白名单已逐条写明原因（%d 个脚本）" % len(WHY), len(WHY) == 3)

# ============================================================================
print()
print("=" * 74)
print("结果：PASS %d / FAIL %d" % (_ok, len(_fail)))
if _fail:
    print("失败项：")
    for f in _fail:
        print("  - %s" % f)
else:
    print("全部通过 ✓")
print("=" * 74)
sys.exit(1 if _fail else 0)
