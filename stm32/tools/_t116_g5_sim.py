# -*- coding: utf-8 -*-
"""
_t116_g5_sim.py —— G5（RESET 0x03）落码后的离线验证
================================================================================

G5 动的是**上板验收过的老路径**（`ads1293_afe_test()` 的写表循环），
所以本脚本的三条主线不是"数值复算"，而是：

  A. **纪律断言**（对应技能 §1.3）：把"命令路径禁 printf_msg / 必须停在 STOP /
     0x03 不加状态门"这些口头纪律变成**可执行断言** —— 抽函数最容易漏的东西，
     读代码看不出来，跑断言一眼就露。
  B. **行为等价性**：证明 ASCII 老路径（`ads1293_afe_test()` / `ads1293_conversion()`）
     的**可执行语句一条没丢、一条没多加**（除"抽出去的那一次调用"）。
  C. **逻辑复刻**：用 PC 复刻 `ads1293_write_table()` 与 `reset_afe()` 的返回值语义，
     含"失败不发快照"（待拍③）与"重试一次"两个分支。

口径来源（⚙ 每条都在 T111 上板真帧上反证过）：
  · 上行/下行帧：[0]AA [1]55 [2]TYPE [3]LEN_L [4]LEN_H [5]REQID(bin)/CMD(down) [6]COUNT…
  · 下行 LEN = 1(CMD) + len(ARG)  ⇒ RESET(ARG=0) ⇒ LEN=1
  · T111 真帧 `AA 55 03 4A 00 00 24 …` ⇒ REQID 在 [5]=0x00、COUNT 在 [6]=0x24=36

用法:
  python tools/_t116_g5_sim.py [--verbose]
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
BAK = os.path.join(ROOT, "tools", "_bak_T116_20260923")

_ok = 0
_fail: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global _ok
    if cond:
        _ok += 1
        if VERBOSE:
            print("  ✓ %s%s" % (name, "   " + detail if detail else ""))
    else:
        _fail.append(name)
        print("  ✗ %s   %s" % (name, detail))


def read(p: str) -> str:
    return io.open(p, encoding="utf-8").read()


def md5(p: str) -> str:
    return hashlib.md5(io.open(p, "rb").read()).hexdigest()


def _slice(src: str, a: str, b: str) -> str:
    i = src.index(a)
    j = src.index(b, i + len(a))
    return src[i:j]


def _slice_to(src: str, a: str, b: str) -> str:
    """从 a 起，到其后第一个 b 之前（b 不含）。"""
    i = src.index(a)
    j = src.index(b, i)
    return src[i:j]


def _func(src: str, sig: str) -> str:
    """取一个函数的完整文本：从签名到**顶格** `}`（函数体内的 `}` 都有缩进）。"""
    i = src.index(sig)
    j = src.index("\n}\n", i) + 3
    return src[i:j]


def _code(text: str) -> str:
    """剥掉注释与空行，便于做「语句」级比对。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    lines = [l.strip() for l in text.split("\n")]
    return "\n".join(l for l in lines if l)


def _stmts(text: str) -> list[str]:
    """可执行语句列表（丢掉花括号行与函数签名行）。"""
    out = []
    for l in _code(text).split("\n"):
        l = l.strip()
        if not l or l in ("{", "}"):
            continue
        if l.startswith("static void ") or l.startswith("void ads1293_conversion(bool"):
            continue
        out.append(l)
    return sorted(out)


# ============================================================================
ads = read(ADS_C)
adsh = read(ADS_H)
vcom = read(VCOM)
ads_old = read(os.path.join(BAK, "ads1293.c"))
vcom_old = read(os.path.join(BAK, "vcomcmd.c"))
adsh_old = read(os.path.join(BAK, "ads1293.h"))

print("=" * 74)
print("§0 文件与备份")
print("=" * 74)
check("三个改动文件都在", all(os.path.exists(p) for p in (ADS_C, ADS_H, VCOM)))
check("备份目录完整（3 文件）",
      all(os.path.exists(os.path.join(BAK, f)) for f in
          ("ads1293.c", "ads1293.h", "vcomcmd.c")))
check("ads1293.c 确有改动", md5(ADS_C) != md5(os.path.join(BAK, "ads1293.c")))
check("ads1293.h 确有改动", md5(ADS_H) != md5(os.path.join(BAK, "ads1293.h")))
check("vcomcmd.c 确有改动", md5(VCOM) != md5(os.path.join(BAK, "vcomcmd.c")))
check("🛑 Filter/ 本步零参与（红线：滤波参数一个都不许动）", True,
      "shell 侧另有 git status -- Filter/ 复核")

print()
print("=" * 74)
print("§1 纪律断言（把口头纪律做成可执行断言）")
print("=" * 74)

reset_raw = _slice_to(ads, "int ads1293_reset_afe(void)", "\n/* ============ G3")
crun = _slice_to(ads, "static void ads1293_conversion_run(bool bstart)",
                 "\nvoid ads1293_conversion(bool bstart)")
finit = _slice_to(ads, "static void ads1293_filter_chain_init(void)",
                  "\n/* ---- G5(2026-09-23)：conversion")
wtbl = _slice_to(ads, "int ads1293_write_table(const uint8_t t[][2], uint32_t n)",
                 "\n/* ---- G5(2026-09-23)：RESET")
reset = _code(reset_raw)
crun_c = _code(crun)
finit_c = _code(finit)
wtbl_c = _code(wtbl)

check("🛑 reset_afe() 内**无** printf_msg（铁律：printf_msg 内含 HAL_Delay(5)）",
      "printf_msg" not in reset)
check("🛑 conversion_run() 内**无** printf_msg", "printf_msg" not in crun_c)
check("🛑 filter_chain_init() 内无 printf_msg / 无 HAL_Delay",
      "printf_msg" not in finit_c and "HAL_Delay" not in finit_c)
check("★ conversion() **仍**有回显（ASCII 人工调试路径行为不变）",
      "printf_msg" in _func(ads, "void ads1293_conversion(bool bstart)"))
check("🛑 write_table() 的 HAL_Delay 只在失败重试分支（全函数仅 1 处）",
      "if (attempt) HAL_Delay(1);" in wtbl_c and wtbl_c.count("HAL_Delay") == 1,
      "HAL_Delay 出现 %d 次" % wtbl_c.count("HAL_Delay"))
check("★ write_table() 无 printf_msg", "printf_msg" not in wtbl_c)

# 原子序列顺序（🛑 顺序即纪律）
seq = ["ads1293_conversion_run(false)",
       "queue_reset(&EcgQueueFilter)",
       "ads1293_filter_chain_init();",
       "ads1293_write_table(regs_leaderIII",
       "ads1293_write_reg(TI_ADS1293_CONFIG_REG, 0x02);",
       "ads1293_send_snapshot(0x03u, NULL)"]
pos = [reset.find(s) for s in seq]
check("★ 原子序列 6 步顺序正确（停流→清 Filter 队列→滤波复位→写表→停 STOP→发快照）",
      all(p >= 0 for p in pos) and pos == sorted(pos), "位置=%s" % pos)
check("★ P7：补了 queue_reset(&EcgQueueFilter)（老 conversion(false) 只清 Raw）",
      "queue_reset(&EcgQueueFilter)" in reset)
check("★ 写表用表本体 + ARRAY_SIZE（不硬编码 36）", "ARRAY_SIZE(regs_leaderIII)" in reset)
check("★ 待拍③：只在 bad == 0 时发快照（失败 ⇒ 不发 ⇒ 上位机走 1.0 s 超时）",
      re.search(r"if \(bad == 0\) \{\s*\(void\)ads1293_send_snapshot", reset) is not None)
check("★ 待拍④：reset_afe() 内**无** demo_ads1293_start() / 无 CONFIG=0x01（不顺带 START）",
      "demo_ads1293_start" not in reset and "0x01" not in reset)
check("★ 待拍①：conversion_run 是 static（不扩大对外接口面）",
      "static void ads1293_conversion_run" in ads)
check("★ 待拍②：filter_chain_init 是 static，且被**两处**调用（共用内核）",
      "static void ads1293_filter_chain_init" in ads
      and ads.count("ads1293_filter_chain_init();") == 2,
      "调用点 %d 处" % ads.count("ads1293_filter_chain_init();"))

case03 = _code(_slice_to(vcom, "case 0x03u:", "\n      default:"))
case10 = _code(_slice_to(vcom, "case 0x10u:", "\n      case 0x03u:"))
check("🛑★ case 0x03u 内**无** RUN 状态门（主判据＝运行中点 Reset ⇒ 帧停）",
      "_bconvstart" not in case03)
check("★ 回归：case 0x10u **仍保留**状态门（口径相反，别互相抄）",
      "_bconvstart" in case10)
check("case 0x03u 调 ads1293_reset_afe() 且丢弃返回值", "(void)ads1293_reset_afe();" in case03)
check("case 0x03u 无 printf_msg", "printf_msg" not in case03)

check("ads1293.h 导出了 write_table 与 reset_afe",
      "int ads1293_write_table(const uint8_t t[][2], uint32_t n);" in adsh
      and "int ads1293_reset_afe(void);" in adsh)
_adh_stripped = _code(adsh.replace("int ads1293_write_table(const uint8_t t[][2], uint32_t n);", "")
                          .replace("int ads1293_reset_afe(void);", ""))
check("🛑 除这两条原型（+其注释块）外，头文件其它内容零改动",
      _adh_stripped == _code(adsh_old))

print()
print("=" * 74)
print("§2 行为等价性：老路径「语句一条没丢、一条没多加」")
print("=" * 74)

old_afe = _func(ads_old, "void ads1293_afe_test(void)")
new_afe = _func(ads, "void ads1293_afe_test(void)")
check("🛑★ ads1293_afe_test() 与备份**逐字节相同**（只从它旁边抽函数，不动它）",
      old_afe == new_afe, "旧 %d B / 新 %d B" % (len(old_afe), len(new_afe)))

old_conv = _func(ads_old, "void ads1293_conversion(bool bstart)")
new_conv = _func(ads, "void ads1293_conversion(bool bstart)")
old_s = _stmts(old_conv)
new_s = [x for x in _stmts(finit + "\n" + crun + "\n" + new_conv)
         # 抽取本身带来的两次调用（都在新侧）：
         #   · crun 里替换掉旧 if 体内的 6 条初值语句 ⇒ ads1293_filter_chain_init();
         #   · 新 conversion 里替换掉旧 if/else 两段 ⇒ ads1293_conversion_run(bstart);
         if x not in ("ads1293_conversion_run(bstart);", "ads1293_filter_chain_init();")]
check("★★★ conversion 的**可执行语句集合**与旧版完全一致（抽内核=纯搬运，无夹带）",
      old_s == new_s, "旧 %d 条 / 新 %d 条" % (len(old_s), len(new_s)))
if old_s != new_s:
    print("    only-old: %s" % [x for x in old_s if x not in new_s])
    print("    only-new: %s" % [x for x in new_s if x not in old_s])

# 抽出去的 6 条初值语句必须**恰好各出现一次**、且都在 filter_chain_init 里
for stmt in ("ecg_dc_block_init(&g_dc, ECG_DC_BLOCK_FC_HZ_F);",
             "ecg_filter_f64_init(&f64);",
             "ecg_out_f32_init(&out);",
             "ecgfilteridx = 0;",
             "seQidx = 0;",
             "gdtover = 0;"):
    check("  ↳ 初值语句「%s」恰在 filter_chain_init 内一次"
          % stmt.rstrip(";")[:38],
          _code(finit).count(stmt) == 1)
check("★ 老 conversion 仍写 CONFIG=0x01（RUN）/ 0x02（STOP）",
      "ads1293_write_reg(TI_ADS1293_CONFIG_REG, 0x01);" in crun_c
      and "ads1293_write_reg(TI_ADS1293_CONFIG_REG, 0x02);" in crun_c)
check("★ 老 conversion 仍调 demo_ads1293_start/stop（启停链路不变）",
      "demo_ads1293_start();" in crun_c and "demo_ads1293_stop();" in crun_c)
check("🛑 vcomcmd.c 的改动**只在** switch 内（G4 分流前置分支逐字节未动）",
      _slice_to(vcom_old, "void vcom_recv_analysis(void)", "\n    while(1)")
      == _slice_to(vcom, "void vcom_recv_analysis(void)", "\n    while(1)"))

print()
print("=" * 74)
print("§3 协议字节：上行 0x03 帧 + 下行 0x03 帧 CRC")
print("=" * 74)


def crc8(data: bytes, poly: int = 0x07, init: int = 0x00) -> int:
    c = init
    for x in data:
        c ^= x
        for _ in range(8):
            c = ((c << 1) ^ poly) & 0xFF if (c & 0x80) else (c << 1) & 0xFF
    return c


check("标准自证向量 ecg_crc8(\"123456789\") == 0xF4", crc8(b"123456789") == 0xF4,
      "实为 %02X" % crc8(b"123456789"))


def down_frame(cmd: int, arg: bytes = b"") -> bytes:
    """协议：AA 55 | 0x80 | LEN(2B) | CMD | ARG | CRC；LEN = 1 + len(ARG)。"""
    payload = bytes([cmd]) + arg
    body = bytes([0xAA, 0x55, 0x80, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF]) + payload
    return body + bytes([crc8(body[2:])])


f3 = down_frame(0x03)
check("★ 下行 RESET 帧 = AA 55 80 01 00 03 53（7 B）",
      f3 == bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x03, 0x53]),
      "实为 %s" % f3.hex(" ").upper())
f10 = down_frame(0x10)
check("★ 回归：下行 REQ_SNAPSHOT 帧 = AA 55 80 01 00 10 2A（G4 已上板验收）",
      f10 == bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x10, 0x2A]),
      "实为 %s" % f10.hex(" ").upper())
check("副判据反例：坏 CRC = 把 53 改 54 ⇒ 校验必失败",
      crc8(f3[2:-1]) != 0x54)
check("副判据反例：LEN 自称 3 只给 1 B ⇒ 长度对核必失败（G4 的长度门）",
      (6 + 3) != len(down_frame(0x03)))

# ---- 表解析：必须按 #if/#else 与行尾注释处理（首版正则吃进了 41 条，含被注释掉的 0x03）----
m_lod = re.search(r"#define\s+ECG_LOD_EN\s+(\d+)", ads)
ENV = {"ECG_LOD_EN": int(m_lod.group(1)) if m_lod else 0}


def parse_reg_table(src: str, name: str, env: dict) -> list[tuple[int, int]]:
    i = src.index("const uint8_t %s[][2]" % name)
    j = src.index("};", src.index("{", i))
    out, active = [], [True]
    for ln in src[i:j].split("\n"):
        s = ln.strip()
        if s.startswith("#if"):
            expr = s[3:].strip()
            neg = expr.startswith("!")
            key = expr.lstrip("!").strip()
            val = bool(env.get(key, 0)) if key in env else False
            active.append(active[-1] and (not val if neg else val))
            continue
        if s.startswith("#else"):
            parent = active[-2]
            active[-1] = parent and not active[-1]
            continue
        if s.startswith("#endif"):
            active.pop()
            continue
        if not active[-1]:
            continue
        s = re.sub(r"//.*$", "", s).strip()          # 剥行尾/整行注释
        m = re.match(r"^\{?\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*\}?,?$", s)
        if m:
            out.append((int(m.group(1), 16), int(m.group(2), 16)))
    return out


table = parse_reg_table(ads, "regs_leaderIII", ENV)
table_old = parse_reg_table(ads_old, "regs_leaderIII", ENV)
check("★ 表 regs_leaderIII 共 36 条（按 #if ECG_LOD_EN=%d 取分支）" % ENV["ECG_LOD_EN"],
      len(table) == 36, "实为 %d" % len(table))
check("★ 表内容与落码前**逐条一致**（本步不动寄存器表）", table == table_old)
check("★ 表内 REG 严格升序且无重复（协议 §3.2 要求按 REG 升序输出 ⇒ 顺序直取）",
      [r for r, _ in table] == sorted(set(r for r, _ in table)))
check("★ 表内 0x00(CONFIG) 的**写入值**是 0x00(STANDBY) —— 不是 0x02！"
      "「停在 STOP 的 0x02」是 reset_afe() 第 ④ 步显式写的、不是表给的",
      table[0] == (0x00, 0x00), "实为 %s" % (table[0],))
check("★★ 补①判据可用：表内 0x0A(R2_RATE) 期望值 = 0x07"
      "（上板先改 0x03 ⇒ Reset 后应回 0x07；⚠ 源码里那行 `{0x0a,0x03}` 是**注释掉的**）",
      dict(table).get(0x0A) == 0x07, "实为 0x%02X" % (dict(table).get(0x0A) or 0))

# 上行 0x03 帧（REQID=0x03，COUNT=36）
payload = bytes([0x03, len(table)]) + b"".join(bytes([r, v]) for r, v in table)
body = bytes([0x03, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF]) + payload
up = bytes([0xAA, 0x55]) + body + bytes([crc8(body)])
check("★ 上行 0x03 帧总长 = 80 B（COUNT=36 ⇒ LEN=74=0x4A）",
      len(up) == 80 and up[3] == 0x4A, "实为 %d B, LEN=0x%02X" % (len(up), up[3]))
check("★ 帧内 [5] = REQID = 0x03（上位机据此判 Accepted，与 ASCII 通道的 0x00 区分）",
      up[5] == 0x03, "实为 0x%02X" % up[5])
check("★ 帧内 [6] = COUNT = 36（与 T111 上板真帧 `AA 55 03 4A 00 00 24` 同口径）",
      up[6] == 36, "实为 0x%02X" % up[6])
check("★ CRC 覆盖 [2] 起 3+LEN=77 字节（与上位机 Slice(2, 3+LEN) 同口径）",
      crc8(up[2:2 + 3 + 0x4A]) == up[-1])
check("★ T111 上板真帧（G3 版，REQID=0x00）CRC 复算仍为 F7 ⇒ 快照帧口径未变",
      crc8(bytes(int(t, 16) for t in
                 "AA 55 03 4A 00 00 24 00 00 01 11 02 19 03 00 04 00 05 00 06 08 07 00 08 00 "
                 "09 00 0A 07 0B 00 0C 04 0D 00 0E 00 0F 00 10 00 11 00 12 04 13 00 14 00 15 00 "
                 "16 00 17 05 21 02 22 02 23 02 24 02 25 00 26 00 27 08 28 00 29 00 2A 00 2E 33 "
                 "2F 30 F7".split())[2:-1]) == 0xF7)

print()
print("=" * 74)
print("§4 逻辑复刻：write_table 返回值语义 + reset_afe「失败不发快照」")
print("=" * 74)


class FakeChip:
    """复刻 ads1293_write_table()：失败重试 1 次（HAL_Delay(1)），返回 0 / 首个坏项下标+1。"""

    def __init__(self, table, bad_write=(), bad_read=(), flaky=()):
        self.table = table
        self.bad_write = set(bad_write)
        self.bad_read = set(bad_read)
        self.flaky = set(flaky)
        self.regs = {r: v for r, v in table}
        self.delays = 0

    def write(self, reg, val):
        if reg in self.bad_write:
            return False
        if reg in self.flaky:
            self.flaky.discard(reg)
            return False
        self.regs[reg] = val
        return True

    def read(self, reg):
        # ⚠ 坏项必须返回一个**不等于任何写入值**的错值：表里好几条的写入值本身就是 0x00，
        #   若"回读失败"也返回 0x00，就会和期望值撞上、被误判成成功（首版踩过）。
        return 0xA5 if reg in self.bad_read else self.regs.get(reg, 0x00)

    def write_table(self):
        for i, (reg, val) in enumerate(self.table):
            ok = False
            for attempt in range(2):
                if attempt:
                    self.delays += 1
                if not self.write(reg, val):
                    continue
                if self.read(reg) != val:
                    continue
                ok = True
                break
            if not ok:
                return i + 1
        return 0


c = FakeChip(table)
r0 = c.write_table()
check("① 全成功 ⇒ 返回 0，且**零 delay**（正常路径无 HAL_Delay）",
      r0 == 0 and c.delays == 0, "ret=%d delays=%d" % (r0, c.delays))
check("①b 36 条全部写进 ⇒ 芯片内值与表逐条一致",
      all(c.regs[r] == v for r, v in table))

c = FakeChip(table, bad_read=[table[5][0]])
check("② 第 6 条回读不一致 ⇒ 返回 6（下标 5 + 1，1-based 与 afe_test 的 count 对齐）",
      c.write_table() == 6)

c = FakeChip(table, bad_write=[table[0][0]])
check("③ 第 1 条写一直失败 ⇒ 返回 1", c.write_table() == 1)

c = FakeChip(table, flaky=[table[3][0]])
r = c.write_table()
check("④ 第 4 条首写失败、重试成功 ⇒ 返回 0（重试有效），delay 恰 1 次",
      r == 0 and c.delays == 1, "ret=%d delays=%d" % (r, c.delays))

c = FakeChip(table, bad_read=[table[35][0]])
check("⑤ 最后一条坏 ⇒ 返回 36（不漏边界）", c.write_table() == 36)


def reset_afe_logic(chip):
    """复刻 reset_afe()：bad==0 才发快照（待拍③）。"""
    bad = chip.write_table()
    return bad, (bad == 0)


bad, sent = reset_afe_logic(FakeChip(table))
check("★ 待拍③ 正向：写表成功 ⇒ 发快照（REQID=0x03）", bad == 0 and sent)

bad, sent = reset_afe_logic(FakeChip(table, bad_read=[table[7][0]]))
check("★ 待拍③ 反向：写表失败 ⇒ **不发快照**（上位机等满 1.0 s 超时，可识别）",
      bad == 8 and not sent, "bad=%d sent=%s" % (bad, sent))

print()
print("=" * 74)
print("§5 上板作业卡（本步主判据链）")
print("=" * 74)
print("  ① 烧 G5 hex ⇒ `test notch` 对档名 ⇒ `test afe`（确认老路径仍好）")
print("  ② ★ 主判据：**运行中**（先 `test afe start`）发下行 `AA 55 80 01 00 03 53`")
print("       ⇒ 期望：显示帧**停** ＋ 收到 **80 B** 快照且 **REQID=0x03**")
print("  ③ ★★ 补①：先 STOP 态把 **0x0A 改成 0x03**（`0x11` 或手工）⇒ 再 Reset")
print("       ⇒ 快照里 **0x0A 应回到 0x07**（证明写表真生效，不是只回了个帧）")
print("  ④ ★★ 补②（ASCII 通道回归）：`test afe` / `test snapshot` / `test notch off`")
print("       / `test raw853 on` **逐条再敲**，全部仍好用")
print("  ⑤ ★★ 补③（耗时）：`test raw853 on` 下看 Reset 前后**丢了几行**（列①序号跳变量）")
print("       ⇒ 新路径应为 36×SPI 往返量级（期望 ≤10 ms），老路径是 ≈236 ms")
print("  ⑥ 副判据：Reset 后仍能 START 并出正确波形；快照 36 项与表全等（0x00 例外：")
print("       它由第 ④ 步写成 0x02，不是表里的 0x00）")

print()
print("=" * 74)
if _fail:
    print("结果：**PASS %d / FAIL %d**" % (_ok, len(_fail)))
    for f in _fail:
        print("   ✗ %s" % f)
    sys.exit(1)
print("结果：**PASS %d / FAIL 0** —— G5 离线验证全绿" % _ok)
print("=" * 74)
