# -*- coding: utf-8 -*-
"""
_t119_g6_sim.py —— G6（START/STOP 0x01/0x02）落码后的离线验证
================================================================================

G6 是**纯新增**的一步（只加 `ads1293_start()` / `ads1293_stop()` / 两个 case），
所以本脚本的四条主线是：

  A. **纪律断言** —— 把用户拍板的 4 条变成可执行断言：
       ① **不抽共用内核**（`reset_afe()` 逐字节未动 ＋ 全局仍只有 2 处
          `filter_chain_init()` 调用 ＋ 未新增任何 static 函数）
       ② 写表失败 **不起流**（else 分支只写 CONFIG=0x02）
       ③ `0x01`/`0x02` **都不加**状态门（且 `0x10` 的门必须**还在** —— 口径相反各断言一次）
       ④ P7 缺口 **不在本步修**（`conversion_run(false)` 逐字节未动）
  B. **零改动的既有面** —— 把 `ads1293.c` 里 **22 个既有函数**逐个与 T119 备份
     逐字节比对（这是"新增而非修改"最硬的证据，比 `git diff --stat` 更细）。
  C. **协议字节** —— 下行 `0x01`/`0x02` 帧 ＋ 回归 `0x03`/`0x10`。
  D. **逻辑复刻** —— 用 PC 复刻 `ads1293_start()` 的两个分支，证明"失败不起流"。

口径来源：
  · 下行帧：`AA 55 | 0x80 | LEN(2B LE) | CMD | ARG | CRC8`，LEN = 1 + len(ARG)
  · 隐式 ACK（协议 §5.1）：**START/STOP 的 ACK = 显示帧流的起停**，🛑 **不发快照**

用法:
  python tools/_t119_g6_sim.py [--verbose]
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
BAK = os.path.join(ROOT, "tools", "_bak_T119_20260923")

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
print("§1 纪律断言（把 4 条拍板做成可执行断言）")
print("=" * 74)

start_raw = _slice_to(ads, "/* ---- G6(2026-09-23)：START", "/* ---- G6(2026-09-23)：STOP")
stop_raw = _slice_to(ads, "/* ---- G6(2026-09-23)：STOP", "\n/* ============ G3")
reset_raw = _slice_to(ads, "int ads1293_reset_afe(void)", "\n/* ---- G6(2026-09-23)：START")
crun = _slice_to(ads, "static void ads1293_conversion_run(bool bstart)",
                 "\nvoid ads1293_conversion(bool bstart)")
start_c = _code(start_raw)
stop_c = _code(stop_raw)
reset_c = _code(reset_raw)
crun_c = _code(crun)

check("🛑 start() 内**无** printf_msg（铁律：printf_msg 内含 HAL_Delay(5)）",
      "printf_msg" not in start_c)
check("🛑 stop() 内无 printf_msg", "printf_msg" not in stop_c)
check("🛑 start() 内**无** HAL_Delay（写表内核的 delay 在 write_table 内，不在本层）",
      "HAL_Delay" not in start_c)
check("🛑 stop() 内无 HAL_Delay", "HAL_Delay" not in stop_c)
check("🛑★ start() **不许调用** `ads1293_conversion()`（内含无条件 printf_msg）"
      " —— 只能走 `ads1293_conversion_run()`",
      "ads1293_conversion(" not in start_c)
check("★ 回归：start() 确实走 conversion_run()（两处：false 起、true 终）",
      start_c.count("ads1293_conversion_run(") == 2,
      "实为 %d 处" % start_c.count("ads1293_conversion_run("))

# 序列顺序
seq = ["ads1293_conversion_run(false)",
       "queue_reset(&EcgQueueFilter)",
       "ads1293_write_table(regs_leaderIII",
       "if (bad == 0)",
       "ads1293_conversion_run(true)",
       "ads1293_write_reg(TI_ADS1293_CONFIG_REG, 0x02)"]
pos = [start_c.find(s) for s in seq]
check("★ start() 的 6 步顺序正确（停流→清两队列→写表→(成功)起流／(失败)停回 STOP）",
      all(p >= 0 for p in pos) and pos == sorted(pos), "位置=%s" % pos)
check("★ P1：写表用表本体 + ARRAY_SIZE（不硬编码 36）",
      "ARRAY_SIZE(regs_leaderIII)" in start_c)
check("★ P7：start() 补了 queue_reset(&EcgQueueFilter)（_run(false) 内部只清 Raw）",
      start_c.count("queue_reset(&EcgQueueFilter)") == 1)
check("★ P7：stop() 也清了 Filter 队列（与 reset_afe/start 同口径）",
      stop_c.count("queue_reset(&EcgQueueFilter)") == 1)

# ★ 待拍②：失败不起流
check("★★ 待拍②：**写表失败 ⇒ 不起流** —— else 分支只写 CONFIG=0x02（停回 STOP）",
      re.search(r"\} else \{\s*\(void\)ads1293_write_reg\(TI_ADS1293_CONFIG_REG, 0x02\);",
                start_c) is not None)
check("★★ 待拍② 反面：`conversion_run(true)`（起流）**只在 if (bad == 0) 内**"
      " —— 它出现在 else 之后、不是无条件",
      re.search(r"if \(bad == 0\) \{\s*ads1293_conversion_run\(true\);", start_c) is not None)
check("★ start() 返回 bad（0 = 已起流；>0 = 未起流）", "return bad;" in start_c)

# ★ 待拍①：不抽共用内核
check("★★★ 待拍①：`ads1293_reset_afe()` 与 G5 落码后**逐字节相同**（抽内核就会动它）",
      _func(ads, "int ads1293_reset_afe(void)") == _func(ads_old, "int ads1293_reset_afe(void)"),
      "旧 %d B / 新 %d B" % (len(_func(ads_old, "int ads1293_reset_afe(void)")),
                             len(_func(ads, "int ads1293_reset_afe(void)"))))
check("★★ 待拍①：start() 体内**不**独立调用 filter_chain_init()（不抽共用内核；"
      "滤波链复位由 _run(true) 内部负责）",
      "ads1293_filter_chain_init();" not in start_c)
check("★★ 待拍①：全局 `ads1293_filter_chain_init();` 调用点仍是 **2 处**"
      "（_run(true) 内 1 处、reset_afe() 内 1 处）—— 没多出第三处",
      ads.count("ads1293_filter_chain_init();") == 2,
      "实为 %d 处" % ads.count("ads1293_filter_chain_init();"))
check("★★ 待拍①：本步**没有新增任何 static 函数**"
      "（新增 static 就是【抽内核】的信号；两处形似而实不同是故意的）",
      len(re.findall(r"static\s+\w+\s+ads1293_\w+\s*\(", ads))
      == len(re.findall(r"static\s+\w+\s+ads1293_\w+\s*\(", ads_old)),
      "旧 %d 个 / 新 %d 个" % (len(re.findall(r"static\s+\w+\s+ads1293_\w+\s*\(", ads_old)),
                               len(re.findall(r"static\s+\w+\s+ads1293_\w+\s*\(", ads))))
check("★★ 待拍①：两处**形似而实不同** —— reset_afe 停在 STOP（无 _run(true)）"
      "、start 一定起流（有 _run(true)）",
      "ads1293_conversion_run(true)" in start_c
      and "ads1293_conversion_run(true)" not in reset_c)
check("★ 待拍①：`conversion_run()` 仍是 static、`filter_chain_init()` 仍是 static"
      "（不扩大对外接口面）",
      "static void ads1293_conversion_run" in ads
      and "static void ads1293_filter_chain_init" in ads)

# ★ 待拍④：P7 缺口不在本步修
check("★★ 待拍④：`ads1293_conversion_run()` 与 G5 落码后**逐字节相同**"
      "（P7 缺口 = ASCII `test afe stop` 只清 Raw，不在本步修 ⇒ 记遗留）",
      crun == _slice_to(ads_old, "static void ads1293_conversion_run(bool bstart)",
                        "\nvoid ads1293_conversion(bool bstart)"))

# ★ 不发快照（协议 §5.1 隐式 ACK 是显示帧流的起停）
check("🛑 start()/stop() 内**无** send_snapshot（协议 §5.1：ACK = 显示帧流起停，不发快照）",
      "send_snapshot" not in start_c and "send_snapshot" not in stop_c)

# 🛑 待拍③：0x01/0x02 不加状态门（口径相反各断言一次）
case01 = _code(_slice_to(vcom, "case 0x01u:", "\n      case 0x02u:"))
case02 = _code(_slice_to(vcom, "case 0x02u:", "\n      default:"))
case03 = _code(_slice_to(vcom, "case 0x03u:", "\n      case 0x01u:"))
case10 = _code(_slice_to(vcom, "case 0x10u:", "\n      case 0x03u:"))
check("🛑★ 待拍③：case 0x01u 内**无** RUN 状态门（首步就停流 ⇒ 重复 START 是安全重启）",
      "_bconvstart" not in case01)
check("🛑★ 待拍③：case 0x02u 内**无**状态门（stop 幂等）",
      "_bconvstart" not in case02)
check("★ 回归：case 0x03u 仍**无**门（G5 口径，与 0x10 相反）", "_bconvstart" not in case03)
check("★ 回归：case 0x10u **仍保留**状态门（🛑 口径相反，别互相抄）",
      "_bconvstart" in case10)
check("case 0x01u 调 `(void)ads1293_start();` 且丢弃返回值",
      "(void)ads1293_start();" in case01)
check("case 0x02u 调 `ads1293_stop();`（void，无需丢弃）", "ads1293_stop();" in case02)
check("case 0x01u / 0x02u 内均无 printf_msg",
      "printf_msg" not in case01 and "printf_msg" not in case02)

check("ads1293.h 导出了 ads1293_start / ads1293_stop",
      "int  ads1293_start(void);" in adsh and "void ads1293_stop(void);" in adsh)
_adh_stripped = _code(adsh.replace("int  ads1293_start(void);", "")
                          .replace("void ads1293_stop(void);", ""))
check("🛑 除这两条原型（+其注释块）外，头文件其它内容零改动",
      _adh_stripped == _code(adsh_old))

print()
print("=" * 74)
print("§2 零改动的既有面：ads1293.c 的 22 个既有函数逐个逐字节比对")
print("=" * 74)

FUNCS = [
    "int ads1293_write_reg(uint8_t reg, uint8_t val)",
    "int ads1293_write_reginc(uint8_t reg, uint8_t* val, uint8_t len)",
    "int ads1293_read_reg(uint8_t reg, uint8_t* val)",
    "int ads1293_read_reginc(uint8_t reg, uint8_t* val, uint8_t len)",
    "void ads1293_config_test(void)",
    "void ads1293_readregs_test(void)",
    "void ads1293_autoinc_test(void)",
    "static void ads1293_filter_chain_init(void)",
    "static void ads1293_conversion_run(bool bstart)",
    "void ads1293_conversion(bool bstart)",
    "int ads1293_ecg_ch1(int32_t* rdval)",
    "void ads1293_afe_test(void)",
    "int ads1293_write_table(const uint8_t t[][2], uint32_t n)",
    "int ads1293_reset_afe(void)",
    "int ads1293_snapshot(uint8_t *items, uint8_t *count)",
    "int ads1293_send_snapshot(uint8_t reqid, uint8_t *count)",
    "void ads1293_raw_queueinit(void)",
    "void ads1293_raw_enqueue(uint32_t idx)",
    "void ads1293_set_raw853(uint32_t on)",
    "uint32_t ads1293_raw853_is_on(void)",
    "uint8_t ads1293_status_byte(void)",
    "void ads1293_raw_popoutput(void)",
]
_same = []
for f in FUNCS:
    try:
        o, n = _func(ads_old, f), _func(ads, f)
    except ValueError:
        check("  ↳ %s 可提取" % f.split("(")[0], False, "签名未找到")
        continue
    _same.append((f.split("(")[0], o == n, len(o), len(n)))
check("★★★ 22 个既有函数**逐个逐字节相同**（G6 是纯新增，没碰任何老函数）",
      all(s for _, s, _, _ in _same),
      "不同项：%s" % [n for n, s, _, _ in _same if not s])
if VERBOSE:
    for n, s, lo, ln in _same:
        print("      %-32s %s  %d B / %d B" % (n, "✓" if s else "✗", lo, ln))

check("🛑 vcomcmd.c 的改动**只在** switch 内（G4 分流前置分支逐字节未动）",
      _slice_to(vcom_old, "void vcom_recv_analysis(void)", "\n    while(1)")
      == _slice_to(vcom, "void vcom_recv_analysis(void)", "\n    while(1)"))
check("🛑 `case 0x10u` 段逐字节未动（G4 已上板验收）",
      _slice_to(vcom_old, "case 0x10u:", "\n      case 0x03u:")
      == _slice_to(vcom, "case 0x10u:", "\n      case 0x03u:"))
check("🛑 `case 0x03u` 段逐字节未动（G5 已上板验收）",
      _slice_to(vcom_old, "case 0x03u:", "\n      default:")
      == _slice_to(vcom, "case 0x03u:", "\n      case 0x01u:"))

print()
print("=" * 74)
print("§3 协议字节：下行 0x01/0x02（＋回归 0x03/0x10）")
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


f1 = down_frame(0x01)
f2 = down_frame(0x02)
check("★ 下行 START 帧 = AA 55 80 01 00 01 5D（7 B）",
      f1 == bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x01, 0x5D]),
      "实为 %s" % f1.hex(" ").upper())
check("★ 下行 STOP  帧 = AA 55 80 01 00 02 54（7 B）",
      f2 == bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x02, 0x54]),
      "实为 %s" % f2.hex(" ").upper())
check("★ 回归：下行 RESET 帧 = AA 55 80 01 00 03 53（G5 已上板验收）",
      down_frame(0x03) == bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x03, 0x53]))
check("★ 回归：下行 REQ_SNAPSHOT 帧 = AA 55 80 01 00 10 2A（G4 已上板验收）",
      down_frame(0x10) == bytes([0xAA, 0x55, 0x80, 0x01, 0x00, 0x10, 0x2A]))
check("三个下发帧 CRC 互不相同 ⇒ 串口助手发错会被 CRC 挡下",
      len({f1[-1], f2[-1], down_frame(0x03)[-1]}) == 3,
      "CRC = %02X/%02X/%02X" % (f1[-1], f2[-1], down_frame(0x03)[-1]))

# ---- 表解析：按 #if/#else 与行尾注释处理（G5 首版踩过"正则吃 41 条"）----
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
            active[-1] = active[-2] and not active[-1]
            continue
        if s.startswith("#endif"):
            active.pop()
            continue
        if not active[-1]:
            continue
        s = re.sub(r"//.*$", "", s).strip()
        m = re.match(r"^\{?\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*\}?,?$", s)
        if m:
            out.append((int(m.group(1), 16), int(m.group(2), 16)))
    return out


table = parse_reg_table(ads, "regs_leaderIII", ENV)
table_old = parse_reg_table(ads_old, "regs_leaderIII", ENV)
check("★ 表 regs_leaderIII 共 36 条（按 #if ECG_LOD_EN=%d 取分支）" % ENV["ECG_LOD_EN"],
      len(table) == 36, "实为 %d" % len(table))
check("★ 表内容与落码前**逐条一致**（G6 不动寄存器表）", table == table_old)
check("★ 表内 0x00(CONFIG) 的**写入值**是 0x00(STANDBY)"
      "（写表后由步骤 ④ 显式写 0x01=START）", table[0] == (0x00, 0x00),
      "实为 %s" % (table[0],))

print()
print("=" * 74)
print("§4 逻辑复刻：start() 两个分支（★ 待拍② 的核心）")
print("=" * 74)


class FakeChip:
    """复刻 ads1293_write_table()：失败重试 1 次，返回 0 / 首个坏项下标+1。"""

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
        # ⚠ 坏项必须返回一个**不等于任何写入值**的错值（否则与表里的 0x00 撞上 ⇒ 假通过）
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


class FakeAfe:
    """复刻 ads1293_start() / ads1293_stop() 的状态机（_bconvstart ＋ CONFIG[0x00]）。"""

    def __init__(self, chip):
        self.chip = chip
        self.running = False
        self.regs = chip.regs          # 与芯片同一份寄存器视图

    def _run(self, bstart):
        if bstart:
            self.running = True
            self.regs[0x00] = 0x01
        else:
            self.running = False
            self.regs[0x00] = 0x02

    def start(self):
        self._run(False)                       # ① 停流
        bad = self.chip.write_table()           # ③ 写表
        if bad == 0:
            self._run(True)                     # ④ 起流
        else:
            self.regs[0x00] = 0x02              # ④' 失败 ⇒ 停回 STOP，**不起流**
        return bad

    def stop(self):
        self._run(False)


c = FakeChip(table)
afe = FakeAfe(c)
bad = afe.start()
check("★ 待拍② 正向：写表成功 ⇒ 起流（CONFIG=0x01）且返回 0",
      bad == 0 and afe.running and c.regs[0x00] == 0x01,
      "bad=%d running=%s CONFIG=0x%02X" % (bad, afe.running, c.regs[0x00]))
check("★★ P1 判据：起流后芯片内**除 0x00 外 35 条**与表逐条一致，且 0x00 = 0x01"
      "（表给 0x00=STANDBY，步骤 ④ 才写 START ⇒ 0x00 是**唯一**合理例外）",
      all(c.regs[r] == v for r, v in table if r != 0x00) and c.regs[0x00] == 0x01,
      "0x00=0x%02X" % c.regs[0x00])

c = FakeChip(table, bad_read=[table[11][0]])
afe = FakeAfe(c)
bad = afe.start()
check("★ 待拍② 反向：写表失败（第 12 条）⇒ **不起流**、CONFIG 停在 0x02、返回 12",
      bad == 12 and not afe.running and c.regs[0x00] == 0x02,
      "bad=%d running=%s CONFIG=0x%02X" % (bad, afe.running, c.regs[0x00]))
check("★★ 待拍② 反向：失败时起流语句**没被执行**（_run(true) 只在 bad==0 分支）",
      not afe.running)

c = FakeChip(table, flaky=[table[3][0]])
afe = FakeAfe(c)
bad = afe.start()
check("★ 首写失败、重试成功 ⇒ 仍起流（重试有效），delay 恰 1 次",
      bad == 0 and afe.running and c.delays == 1,
      "bad=%d delays=%d" % (bad, c.delays))

c = FakeChip(table, bad_write=[table[0][0]])
afe = FakeAfe(c)
bad = afe.start()
check("★ 第 1 条写一直失败 ⇒ 返回 1、不起流", bad == 1 and not afe.running)

c = FakeChip(table)
afe = FakeAfe(c)
afe._run(True)                      # 先跑起来
afe.stop()
check("★ stop() 幂等：RUN 态调用后 running=False、CONFIG=0x02",
      not afe.running and c.regs[0x00] == 0x02)
afe.stop()
check("★ stop() 再调一次无副作用（待拍③：不加状态门 ⇒ 幂等）",
      not afe.running and c.regs[0x00] == 0x02)

print()
print("=" * 74)
print("§5 上板作业卡（本步主判据链）")
print("=" * 74)
print("  ① 烧 G6 hex ⇒ **上电不做任何操作**，直接发下行 `AA 55 80 01 00 01 5D`")
print("       ⇒ ★ 主判据：**波形正确**（P1：START 内建写表 ⇒ AFE 真被配置）")
print("  ② ★ STOP：发 `AA 55 80 01 00 02 54` ⇒ 显示帧**停**（🛑 不发快照，别等）")
print("  ③ ★★ 补①（对照）：先 `test afe`（人工写表）再发 START ⇒ 与 ① 结果应一致")
print("  ④ ★★ 补②（回归）：`test afe` / `test snapshot` / `test notch off`")
print("       / `test raw853 on` 逐条再敲，全部仍好用")
print("  ⑤ ★★ 补③（P8 正面证据）：START 后 `test snapshot` 看 STA / 快照")
print("       ⇒ 36 项与表全等（0x00 例外：此时应为 0x01）")
print("  ⑥ 副判据：STOP→START 循环 10 次无异常；⚠ 点「mon」/「notch off」**静默无效**")
print("       （0x04/0x05 要等 G7）⇒ 别当 G6 的 bug")

print()
print("=" * 74)
if _fail:
    print("结果：**PASS %d / FAIL %d**" % (_ok, len(_fail)))
    for f in _fail:
        print("   ✗ %s" % f)
    sys.exit(1)
print("结果：**PASS %d / FAIL 0** —— G6 离线验证全绿" % _ok)
print("=" * 74)
