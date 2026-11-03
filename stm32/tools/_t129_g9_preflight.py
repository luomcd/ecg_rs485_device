# -*- coding: utf-8 -*-
"""
T129 · G9（0x06 SET_CHANNEL ＋ 上位机双通道解析/双画布）落码前预检卡生成器（2026-09-22）
=====================================================================================
🛑 只读脚本：**不修改任何源码**（固件与上位机都只读）。产出 T106 §9p 预检卡所需的全部事实。

十节：
  §0  文件与基线（固件 3 源文件 / 上位机 6 源文件 / CRLF / 注释配对）
  §1  G9 定义三处一致（T106 §4-G9 ／ T101 协议 §5.1·§0x06 ／ T101 执行计划 F7·J7）
  §2  固件侧落码面清点（G8 已交付项 ⇒ G9 只剩 1 处分派）
  §3  协议「零寄存器改动」三处证据 ＋ 导联映射解码
  §4  帧长算术（53↔103 / 59↔109 / 段偏移 / CRC 覆盖 / buff 余量）
  §5  白名单三层闭合（H 11 ／ 固件硬拒 7 ／ 可写 29 ／ JSON writable 20）
  §6  上位机落码面清点（U1~U7 · P11/P12/P13 · U11④ · 效果图三要素）
  §7  ★ 本轮预检新发现（N1~N8 —— 台账订正项）
  §7b ★ 落码前二次复核（N9~N10 —— 上位机口径：ChannelCount 零引用 / 速率行别碰）
  §8  红线（Filter/ 14 ／ 表体 md5 ／ ECG_* 宏 ／ VCOM_BIN_MAX_LEN ／ usbd_cdc_if.c）
  §9  基线现状（vcomcmd.c case 清单 ⇒ 0x06 当前落 default）
"""
import hashlib
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PC = r"C:\work2026\VsPro\WinApp_ADSECG\ADS1293_Ecg"
BAK = os.path.join(FW, "tools", "_t128_bak")

SRC_C = os.path.join(FW, "Ads1293", "ads1293.c")
SRC_H = os.path.join(FW, "Ads1293", "ads1293.h")
SRC_V = os.path.join(FW, "Core", "Src", "vcomcmd.c")
FILTER_DIR = os.path.join(FW, "Filter")
CDC = os.path.join(FW, "USB_DEVICE", "App", "usbd_cdc_if.c")

T106 = os.path.join(FW, "tools", "T106_固件分步落码方案_2026-09-23.md")
T101P = os.path.join(FW, "tools", "T101_上位机通讯协议_v2.2_2026-09-22.md")
T101E = os.path.join(FW, "tools", "T101_上位机下位机执行计划_2026-09-22.md")
T101H = os.path.join(FW, "tools", "T101_上位机界面效果图_2026-09-22.html")

# 协议 §6.2 H 硬闸 11 条（上位机口径）
H_GATE = [0x01, 0x02, 0x11, 0x12, 0x13, 0x21, 0x22, 0x23, 0x24, 0x27, 0x2F]
# 固件 _reg_writable() 硬拒 7 条（= H2 频率链 + H3 参考/时钟）
FW_DENY = [0x11, 0x12, 0x13, 0x21, 0x22, 0x23, 0x24]

FAIL = []
N_OK = 0
SKIPPED = []

# ★T133：相位开关。'post' = 落码后（默认）／'pre' = 落码前预检卡口径（`G9_PHASE=pre`）。
PHASE = os.environ.get("G9_PHASE", "post")

# T133 起：以下 12 条是「**预检期守卫**」——它们断言**落码动作尚未发生**（如"仍只有一个画布"、
# "case 0x06u 不存在"），落码后**必然翻转**。post 相位下跳过，正面判据由
# `tools/_t133_g9_selftest`（66 PASS/0 FAIL）接管 —— 那里断言的是"已落码且行为正确"，比这里更强。
POST_FLIPPED = [
    "vcomcmd.c 已存在 case 0x06u",
    "EcgPipeline 的 stackalloc 未按帧长分配",
    "millimeter 已参数化（P12 可能已落）",
    "纵向标定已参数化",
    "水平分母已改为 capacity（P12 可能已落）",
    "画布容器已改为双面板",
    "SampleBuffer 已拆",
    "heartRate.Process 调用形态已变",
    "MaxArgLength 已改（N1 可能已修）",
    "N1 注释已订正",
    "心率检测器已分流",
    "ChannelCount 已被引用",
]


def ck(cond, ok, ng):
    global N_OK
    if PHASE == "post" and any(k in ng for k in POST_FLIPPED):
        SKIPPED.append(ng)
        print("  [--] 跳过（落码后预期翻转）: " + ng[:110])
        return
    print(("  [OK] " if cond else "  [NG] ") + (ok if cond else ng))
    if cond:
        N_OK += 1
    else:
        FAIL.append(ng)


def read(p):
    with open(p, "rb") as f:
        return f.read()


def txt(p, enc="utf-8"):
    return read(p).decode(enc, errors="replace")


def head(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# ══════════════════════════════════════════════════════ §0 文件与基线
head("§0  文件与基线")

for tag, p in [("固件 ads1293.c", SRC_C), ("固件 ads1293.h", SRC_H), ("固件 vcomcmd.c", SRC_V),
               ("上位机 AppConfig.cs", os.path.join(PC, "Config", "AppConfig.cs")),
               ("上位机 Protocol.cs", os.path.join(PC, "Core", "Protocol.cs")),
               ("上位机 BinCommand.cs", os.path.join(PC, "Core", "BinCommand.cs")),
               ("上位机 Form1.cs", os.path.join(PC, "Form1.cs")),
               ("上位机 EcgPipeline.cs", os.path.join(PC, "Core", "EcgPipeline.cs")),
               ("上位机 EcgWaveformControl.cs", os.path.join(PC, "Rendering", "EcgWaveformControl.cs")),
               ("上位机 SnapshotExchange.cs", os.path.join(PC, "Core", "SnapshotExchange.cs"))]:
    ck(os.path.exists(p), "%s 存在（%d 行）" % (tag, txt(p).count("\n") + 1), "%s 缺失：%s" % (tag, p))

# 固件源码 CRLF 继承
for tag, p in [("ads1293.c", SRC_C), ("ads1293.h", SRC_H), ("vcomcmd.c", SRC_V)]:
    raw = read(p)
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    ck(crlf == lf, "%s 行尾全 CRLF（%d/%d）" % (tag, crlf, lf),
       "%s 行尾不纯（CRLF %d / LF %d）" % (tag, crlf, lf))

# 注释配对终检（防止插入块把注释切坏）
for tag, p in [("ads1293.c", SRC_C), ("ads1293.h", SRC_H), ("vcomcmd.c", SRC_V)]:
    s = txt(p)
    ck(s.count("/*") == s.count("*/"), "%s /* */ 配对（%d/%d）" % (tag, s.count("/*"), s.count("*/")),
       "%s 注释未闭合（/* %d vs */ %d）" % (tag, s.count("/*"), s.count("*/")))

# ══════════════════════════════════════════════════════ §1 G9 定义三处一致
head("§1  G9 定义三处一致（T106 §4-G9 ／ T101 协议 ／ T101 执行计划）")

t106 = txt(T106)
a = t106.find("### G9 · SET_CHANNEL")
b = t106.find("### G10 · SET_REGS")
g9_block = t106[a:b]
print("  T106 §4-G9 区间 = %d..%d（%d 字符）" % (a, b, len(g9_block)))
ck(a > 0 and b > a, "T106 §4-G9 详节定位成功", "T106 §4-G9 详节未找到")
for kw in ["`0x06`", "LEN = 3+2*50 = 103", "总长 **109**", "START_CON=1", "STA.bit2=1",
           "不验收", "回退"]:
    ck(kw in g9_block, "T106 §4-G9 含关键措辞「%s」" % kw, "T106 §4-G9 缺「%s」" % kw)

t101p = txt(T101P)
ck("| `0x06` | **SET_CHANNEL** | 1 B：0=I／1=I&II | 2 | **8 B** | **仅 STOP** | 上行 `0x03` 快照帧 + 后续帧长 53↔103 |"
   in t101p,
   "T101 协议 §5.1 命令表：0x06 = SET_CHANNEL / LEN 2 / 8 B / **仅 STOP** / ACK=快照+帧长 53↔103",
   "T101 协议命令表 0x06 行不符")
ck("#### `0x06` SET_CHANNEL" in t101p, "T101 协议 §0x06 详节在位", "T101 协议 §0x06 详节缺失")
ck("**零寄存器改动**" in t101p, "T101 协议明写 §0x06 = **零寄存器改动**（三处证据）",
   "T101 协议未明写零寄存器改动")
ck("SET_CHANNEL | 收到 `0x03` 快照帧 且 `0x01/0x02/0x2F` 与期望一致 | 1 s" in t101p,
   "T101 协议 §ACK 表：SET_CHANNEL 的 ACK 判据 = 快照 + 0x01/0x02/0x2F 一致 + 1 s 超时",
   "T101 协议 ACK 表 SET_CHANNEL 行不符")

t101e = txt(T101E)
ck("### F7 · SET_MODE / SET_NOTCH / SET_CHANNEL / SET_REGS" in t101e,
   "T101 执行计划 F7 节在位（0x06 与 0x04/0x05/0x11 同节）", "F7 节缺失")
ck("③ STOP 态 `SET_CHANNEL I&II` → 快照回执 + 后续帧 LEN=103" in t101e,
   "F7 验收③ = STOP 态切 I&II ⇒ 快照回执 + LEN=103", "F7 验收③ 措辞不符")
ck("切完要 `queue_reset` **两个**队列（P7）" in t101e,
   "⚠ 执行计划 :151 要求「切完 queue_reset 两个队列（P7）」—— **与 G8 实现冲突，见 §7.N6**",
   "执行计划未找到 queue_reset 要求（预期文本已变）")
ck("### U11 · 双画布 + 三处硬编码修正" in t101e, "执行计划 U11 节在位", "U11 节缺失")

# ══════════════════════════════════════════════════════ §2 固件落码面
head("§2  固件侧落码面清点（G8 已交付项 ⇒ G9 剩多少）")

s_c = txt(SRC_C)
s_h = txt(SRC_H)
s_v = txt(SRC_V)

# G8 已交付 ①：一次事务读 CH1+CH2 共 6 B
ck(re.search(r"ads1293_read_reginc\(\s*TI_ADS1293_DATA_CH1_ECG_H_REG\s*,\s*rdbuf\s*,\s*6\s*\)", s_c) is not None,
   "★ G8 已交付①：`read_reginc(0x37, rdbuf, 6)` 一次原子读 CH1+CH2",
   "未找到 read_reginc(0x37,...,6)")

# G8 已交付 ②：ads1293_ecg_ch(rdval, rdval2) 双输出
ck("int ads1293_ecg_ch(int32_t* rdval, int32_t* rdval2)" in s_c,
   "★ G8 已交付①：`ads1293_ecg_ch(int32_t*, int32_t*)` 双输出原型在位",
   "ads1293_ecg_ch 双输出原型缺失")
ck("ads1293_ecg_ch(&ecgraw.ecgraw, &ecgraw.ecgraw2)" in s_c,
   "★ G8 已交付①：入队时两通道都被读回（⇒ 队列项恒含 CH1+CH2）",
   "入队调用未同时取两通道")

# G8 已交付 ③：打包段双通道分支
pack = s_c[s_c.find("uint16_t nch = (uint16_t)(_bch2 ? 2u : 1u);"):]
pack = pack[:1900]
ck("uint16_t nch = (uint16_t)(_bch2 ? 2u : 1u);" in s_c,
   "★ G8 已交付②：打包段 `nch = _bch2 ? 2 : 1`", "打包段双通道分支缺失")
ck("uint16_t len16 = (uint16_t)(3 + 2 * nch * (uint16_t)_popoutcnt);" in s_c,
   "★ G8 已交付②：LEN = 3 + 2·nch·N（nch=1 时还原 G7 的 53）", "LEN 公式不符")
ck("buff[7] = ads1293_status_byte();" in pack, "★ G8 已交付③：STA 写进帧第 8 字节 buff[7]",
   "buff[7] 未写 STA")
ck("buff[8+_popoutcnt*2+idx*2+0] = buff[8+idx*2+0];" in pack,
   "★ G8 已交付②：CH2 **直接复制 CH1**（`CH2 ≡ CH1` 拍板）⇒ 上位机两画布应逐位相同",
   "CH2 复制段未找到（CH2 语义变了）")
ck("ecg_crc8((const uint8_t*)&buff[2], (uint32_t)(6+nch*_popoutcnt*2))" in pack,
   "★ G8 已交付②：CRC 覆盖 = 6 + 2·nch·N（随 nch 变 ⇒ 漏改 CRC 必错）",
   "CRC 覆盖表达式不符")

# G8 已交付 ④：set_ch2 / ch2_is_on 已导出
ck("void     ads1293_set_ch2(uint32_t on);" in s_h and "uint32_t ads1293_ch2_is_on(void);" in s_h,
   "★ G8 已交付④：`ads1293_set_ch2()` / `ads1293_ch2_is_on()` **已在 ads1293.h 导出**"
   " ⇒ G9 分派层可直接调、**无需新增函数**",
   "ch2 接口未导出 ⇒ G9 需新增包装函数")
ck("_bch2 = (on != 0u) ? 1u : 0u;" in s_c, "★ G8 已交付④：`ads1293_set_ch2()` 语义 = `!=0 ⇒ on`",
   "set_ch2 语义不符")
ck("_bch2 = (on != 0u) ? 1u : 0u;\n}" in s_c.replace("\r", ""),
   "★ G8 已交付④：`ads1293_set_ch2()` 体内**无** `queue_reset`（见 §7.N6）",
   "set_ch2 体内竟有 queue_reset")
ck("static volatile uint32_t _bch2" in s_c, "`_bch2` 是 **static** ⇒ 分派层必须走导出的 setter（不能直写）",
   "_bch2 不是 static（假设变了）")

# 唯一滤波实例（第二套未建 —— 拍板③ 明确「不含」）
n_f64 = len(re.findall(r"^\s*ecg_filter_f64\s+f64\s*;", s_c.replace("\r", ""), re.M))
print("  ads1293.c 中 `ecg_filter_f64 f64;` 实例数 = %d" % n_f64)
ck(n_f64 == 1, "仍是**唯一**滤波实例 f64（第二套 `ecg_filter_f64` 未建 —— 拍板③ 明确不含）",
   "滤波实例数 = %d（预期 1；若已建第二套则拍板③ 口径需复核）" % n_f64)
ck("ecg_filter_f64 f64;" in s_v and "当前唯一滤波实例" in s_v,
   "vcomcmd.c 注释仍标「当前唯一滤波实例」⇒ 与拍板③ 一致", "vcomcmd.c 滤波实例注释已变")

# G9 落码面 = 1 处
ck("case 0x06u" not in s_v, "★ G9 落码面：vcomcmd.c **无** `case 0x06u` ⇒ 尚待落码（1 处）",
   "vcomcmd.c 已存在 case 0x06u")
gr = s_v.replace("\r", "").split("\n")
i05 = next(i for i, l in enumerate(gr) if re.match(r"\s*case\s+0x05u:", l))
i11 = next(i for i, l in enumerate(gr) if re.match(r"\s*case\s+0x11u:", l))
idef = next(i for i, l in enumerate(gr) if re.match(r"\s*default:", l) and i > 400)
print("  vcomcmd.c  case 0x05u  = 1-based %d" % (i05 + 1))
print("  vcomcmd.c  case 0x11u  = 1-based %d" % (i11 + 1))
print("  vcomcmd.c  default:    = 1-based %d" % (idef + 1))
print("  ⇒ 建议把 `case 0x06u` 插在 %d 行之后、%d 行之前（**保持 CMD 升序**）" % (i05 + 1, i11 + 1))
ck(i05 < i11 < idef, "`case 0x06u` 插入区间 (%d, %d) 合法（0x11 之后、default 之前）" % (i05 + 1, i11 + 1),
   "插入区间异常")

# 状态门变量可见性
ck("extern   volatile bool _bconvstart;" in s_v, "状态门变量 `_bconvstart` 在 vcomcmd.c 可见（:21 extern）",
   "_bconvstart 不可见")
ck("if (_bconvstart) {\r\n            return;\r\n        }" in s_v or "if (_bconvstart) {" in s_v,
   "★ 状态门写法在先例（0x10/0x11）中已在位 ⇒ G9 照抄口径不变", "状态门写法未找到")
ck("case 0x10u" in s_v and "case 0x03u" in s_v and "case 0x01u" in s_v and "case 0x02u" in s_v,
   "分派层 case 0x10/0x03/0x01/0x02 全在位（G4~G6 已上板）", "基础 case 缺失")

# 新增分派块的 per-CMD 长度校验先例
ck("if (argLen != 2u) {" in s_v, "★ 先例在位：`0x04`/`0x05` 的 per-CMD 长度校验（`argLen != 2u`）"
   " ⇒ G9 的 0x06 应**同款**（新命令从严）", "0x04/0x05 长度校验先例缺失")

# ══════════════════════════════════════════════════════ §3 零寄存器改动证据
head("§3  协议「零寄存器改动」三处证据 ＋ 导联映射解码")

lines_c = s_c.replace("\r", "").split("\n")
start = next(i for i, l in enumerate(lines_c) if l.strip().startswith("const uint8_t regs_leaderIII[][2]"))
end = next(j for j in range(start, len(lines_c)) if lines_c[j].strip() == "};")
lod_m = re.search(r"#\s*define\s+ECG_LOD_EN\s+([0-9]+)", s_c)
lod_en = int(lod_m.group(1)) if lod_m else None
print("  ECG_LOD_EN = %s" % lod_en)

stack = [True]
table = []
for k in range(start + 1, end):
    st = lines_c[k].strip()
    if st.startswith("#if"):
        stack.append(stack[-1] and bool(lod_en)); continue
    if st.startswith("#else"):
        stack[-1] = stack[-2] and (not bool(lod_en)); continue
    if st.startswith("#endif"):
        stack.pop(); continue
    if not stack[-1] or st.startswith("//"):
        continue
    code = st.split("//")[0]        # ★ 剥行内注释（T127 首版踩过的坑：注释里的第二份会被数进来）
    for mm in re.finditer(r"\{\s*0x([0-9a-fA-F]{2})\s*,\s*0x([0-9a-fA-F]{2})\s*\}", code):
        table.append((int(mm.group(1), 16), int(mm.group(2), 16)))
d = dict(table)
print("  表体 = %d 条（行区间 %d-%d）" % (len(table), start + 1, end + 1))
ck(len(table) == 36, "表体 36 条（协议 §6 权威值）", "表体 %d 条（应为 36）" % len(table))

for reg, exp, tag in [
    (0x2F, 0x30, "证据① CH_CNFG=0x30 ⇒ E1_EN=1 + E2_EN=1 ⇒ CH1/CH2 读回已使能、CH3 不读"),
    (0x14, 0x00, "证据② AFE_SHDN_CN=0x00 ⇒ 三通道仪放/调制器全 Active"),
    (0x22, 0x02, "证据③ R3_RATE_CH1=0x02"),
    (0x23, 0x02, "证据③ R3_RATE_CH2=0x02 ⇒ 与 CH1 同 fs、同一 DRDY"),
    (0x01, 0x11, "H1 FLEX_CH1_CN=0x11 ⇒ POS1=IN2 / NEG1=IN1 ⇒ CH1 = IN2−IN1（导联 I）"),
    (0x02, 0x19, "H1 FLEX_CH2_CN=0x19 ⇒ POS2=IN3 / NEG2=IN1 ⇒ CH2 = IN3−IN1（导联 II）"),
    (0x27, 0x08, "H5 DRDYB_SRC=0x08 ⇒ ECG 就绪"),
]:
    ck(d.get(reg) == exp, "0x%02X = 0x%02X  [%s]" % (reg, exp, tag),
       "0x%02X = %s（期望 0x%02X）[%s]" % (reg, d.get(reg), exp, tag))

# 共用 NEG=IN1 ⇒ 两路 R 波必然同向（免费自检判据）
ch1 = d.get(0x01, 0); ch2 = d.get(0x02, 0)
ck((ch1 & 0x07) == (ch2 & 0x07) == 0x01,
   "两路 **共用 NEG = IN1**（NEG1=NEG2=001）⇒ 两路 R 波必然同向（免费接线判据）",
   "两路 NEG 不一致（%02X vs %02X）⇒ 协议 §0x06 的判据文本要改" % (ch1 & 7, ch2 & 7))
ck((ch1 >> 3) & 7 == 0x02 and (ch2 >> 3) & 7 == 0x03,
   "POS1=010(IN2) / POS2=011(IN3) ⇒ 位域解码与协议表一致（6+3+3 布局）",
   "POS 位域不符（POS1=%d POS2=%d）" % ((ch1 >> 3) & 7, (ch2 >> 3) & 7))

# ══════════════════════════════════════════════════════ §4 帧长算术
head("§4  帧长算术（53↔103 ／ 59↔109）")

N = 25
for nch, tag in ((1, "channel=I（当前基线）"), (2, "channel=I&II（G9 目标）")):
    ln = 3 + 2 * nch * N
    tot = 6 + ln
    ch2off = 8 + 2 * N
    crcscope = 6 + 2 * nch * N
    print("  nch=%d %-24s LEN=%3d  帧总长=%3d B  CRC 覆盖=%3d B  CH2 段偏移=%d" %
          (nch, tag, ln, tot, crcscope, ch2off))
    ck(tot <= 512, "nch=%d ⇒ %d B ≪ `char buff[512]` ⇒ 天然安全" % (nch, tot), "nch=%d 超 buff" % nch)

ck(3 + 2 * 1 * 25 == 53 and 6 + 53 == 59, "nch=1 ⇒ LEN=53 / 59 B（G7 定稿回归基线）", "nch=1 算术不符")
ck(3 + 2 * 2 * 25 == 103 and 6 + 103 == 109, "nch=2 ⇒ LEN=103 / **109 B**（G9 判据）", "nch=2 算术不符")
ck(8 + 2 * 25 == 58, "CH2 段偏移 = 8 + 2N = 58（**块布局**，紧跟 CH1 段、不交错）", "CH2 段偏移不符")
ck(103 == 53 + 2 * 25, "109 B 帧 = 59 B 帧 ＋ 50 B（CH2 段）⇒ 帧头/SEQ/STA/CRC 位置不变 ⇒ 老解析器只差 LEN 白名单",
   "帧长增量不符")
ck((103 - 3) // 2 == 50 and 50 == 2 * 25,
   "上位机 `sampleCount = (LEN−offset)/2 = (103−3)/2 = **50** = 25 + 25`（按帧实际样本数分配栈缓冲即可）",
   "上位机样本数推导不符")

# ══════════════════════════════════════════════════════ §5 白名单三层闭合
head("§5  白名单三层闭合（H 11 ／ 固件硬拒 7 ／ 可写 29 ／ JSON writable 20）")

jf = os.path.join(PC, "Config", "expected_regs.json")
ck(os.path.exists(jf), "expected_regs.json 存在", "expected_regs.json 缺失")
doc = json.loads(txt(jf))
wj = doc["writable"]
rj = doc["readonly_expect"]
w_regs = sorted(int(x["reg"], 16) for x in wj)
r_regs = sorted(int(x["reg"], 16) for x in rj)
grades = {}
for x in wj + rj:
    grades[int(x["reg"], 16)] = x["grade"]

print("  JSON writable   = %d 条（grade 全 G）" % len(wj))
print("  JSON readonly   = %d 条（H %d + W %d）" %
      (len(rj), sum(1 for x in rj if x["grade"] == "H"), sum(1 for x in rj if x["grade"] == "W")))
ck(len(wj) == 20 and all(x["grade"] == "G" for x in wj), "JSON `writable` = 20 条且全 G 组（协议 §6.1）",
   "JSON writable 不符")
ck(len(rj) == 15 and sum(1 for x in rj if x["grade"] == "H") == 11 and sum(1 for x in rj if x["grade"] == "W") == 4,
   "JSON `readonly_expect` = 15 条 = H 11 + W 4（= 36 − CONFIG − 20）", "JSON readonly 分级不符")

json_h = sorted(x for x in r_regs if grades[x] == "H")
ck(json_h == H_GATE, "JSON 的 H 组 11 条 = %s（与协议 §6.2 一致）" % " ".join("0x%02X" % x for x in json_h),
   "JSON H 组与协议 §6.2 不符：%s" % " ".join("0x%02X" % x for x in json_h))

diff = sorted(set(H_GATE) - set(FW_DENY))
ck(diff == [0x01, 0x02, 0x27, 0x2F],
   "★ **H 11 − 固件硬拒 7 = 4 条**（0x01 0x02 0x27 0x2F）—— 固件必须放行，与 T128 拍板② 一致",
   "差集不符：%s" % " ".join("0x%02X" % x for x in diff))
ck(sorted(set(FW_DENY) - set(H_GATE)) == [], "固件硬拒 7 条 ⊂ H 11 条（黑名单无越界）",
   "固件硬拒里有不在 H 组的项 ⇒ 口径要复核")

writable_fw = [r for r, _ in table if r not in FW_DENY]
denied_fw = [r for r, _ in table if r in FW_DENY]
print("  固件可写 = %d 条（36 − 7） ／ 硬拒 = %d 条" % (len(writable_fw), len(denied_fw)))
ck(len(writable_fw) == 29 and len(denied_fw) == 7, "固件可写 29 / 硬拒 7", "固件白名单条数不符")

# 29 = 20(G) + 4(H 差集) + 4(W) + 1(CONFIG 0x00)
json_w = sorted(set(w_regs))
complement = sorted(set(writable_fw) - set(json_w))
print("  固件可写 − JSON writable = %s" % " ".join("0x%02X" % x for x in complement))
ck(complement == [0x00, 0x01, 0x02, 0x07, 0x08, 0x14, 0x26, 0x27, 0x2F],
   "★ 三层闭合：29 = 20(G) + 4(H 差集) + 4(W) + 1(CONFIG 0x00) = 29 ✓",
   "三层不闭合：差值 %s" % " ".join("0x%02X" % x for x in complement))
ck(sorted(grades[r] for r in [0x07, 0x08, 0x14, 0x26]) == ["W"] * 4,
   "0x07/0x08/0x14/0x26 = W 组 4 条（红色提示、不拦 Start、默认不进 writable）", "W 组清单不符")
ck(grades[0x01] == "H" and grades[0x02] == "H" and grades[0x27] == "H" and grades[0x2F] == "H",
   "★ 0x01/0x02/0x27/0x2F 在 **JSON 里是 H**（上位机拦 Start）；固件却放行 ⇒「两层把关」分工成立",
   "H 差集 4 条的 JSON 分级不符")

# ══════════════════════════════════════════════════════ §6 上位机落码面
head("§6  上位机落码面清点")

pc_cfg = txt(os.path.join(PC, "Config", "AppConfig.cs"))
pc_pro = txt(os.path.join(PC, "Core", "Protocol.cs"))
pc_bin = txt(os.path.join(PC, "Core", "BinCommand.cs"))
pc_f1 = txt(os.path.join(PC, "Form1.cs"))
pc_pipe = txt(os.path.join(PC, "Core", "EcgPipeline.cs"))
pc_wf = txt(os.path.join(PC, "Rendering", "EcgWaveformControl.cs"))
pc_sx = txt(os.path.join(PC, "Core", "SnapshotExchange.cs"))

# ── U1（已落）：常量集中
ck("public const double MicroVoltsPerLsb = 0.1526;" in pc_cfg, "U1 已落：`MicroVoltsPerLsb = 0.1526`（显示流标定）",
   "U1 未落")
ck("public const double DisplayFrameRate = 500.0;" in pc_cfg, "U1 已落：`DisplayFrameRate = 500.0`", "U1 未落")
ck("public const int SamplesPerFrame = 25;" in pc_cfg,
   "U1 已落：`SamplesPerFrame = 25`（⚠ 语义待钉死 = **每通道每帧**，见 §7.N5）", "U1 未落")
ck("DualChannelSamplesPerFrame = SamplesPerFrame * 2" in pc_cfg,
   "U1 已落：`DualChannelSamplesPerFrame = 50`（单通道 25 × 2）", "U1 未落双通道常量")
ck("FrameDurationSeconds => SamplesPerFrame / DisplayFrameRate" in pc_cfg,
   "U1 已落：`FrameDurationSeconds = 25/500 = 0.05 s`（**单帧时长，双通道下同样正确**）",
   "FrameDurationSeconds 不符")

# ── U2（已落）：解析器接受 103
for lit in ['DisplayLenLegacy = 52', 'DisplayLenSingle = 53', 'DisplayLenDual = 103']:
    ck(lit in pc_pro, "U2 已落：`%s`" % lit, "U2 未落：%s" % lit)
ck("length != DisplayLenLegacy\r\n                && length != DisplayLenSingle\r\n                && length != DisplayLenDual"
   in pc_pro.replace("\n", "\r\n").replace("\r\r\n", "\r\n") or
   ("length != DisplayLenLegacy" in pc_pro and "length != DisplayLenDual" in pc_pro),
   "★ U2 已落：显示帧 LEN ∈ {52, 53, 103} 三者皆接受 ⇒ **G9 不需要改解析器**",
   "解析器 LEN 白名单未含 103")
ck("var sampleCount = (length - offset) / 2;" in pc_pro,
   "★ U2 已落：`sampleCount = (length − offset) / 2` 按 LEN 反推 ⇒ 109 B 帧自动得 50 个样本",
   "sampleCount 推导缺失")
ck("public int ChannelCount => Samples.Length > AppConfig.SamplesPerFrame ? 2 : 1;" in pc_pro,
   "U3 已落：`ChannelCount` **由 LEN/样本数反推**（硬事实），不靠 STA 软标志（协议 §3.1）",
   "ChannelCount 推导缺失")
ck("public const byte Channel = 0x04;" in pc_pro, "U3 已落：`StaBits.Channel = 0x04`（STA.bit2）", "STA bit2 缺失")

# ── U5（已落）：SET_CHANNEL 编码器
ck("public const byte SetChannel = 0x06;" in pc_bin, "U5 已落：`CommandId.SetChannel = 0x06`", "0x06 命令码缺失")
ck("public static byte[] SetChannel(byte channel) => Build(CommandId.SetChannel, stackalloc byte[] { channel });"
   in pc_bin, "U5 已落：`BinCommand.SetChannel(byte)`（LEN=2 ⇒ 8 B 帧）", "SetChannel 构造器缺失")
ck("public const byte ChannelSingle = 0;" in pc_bin and "public const byte ChannelDual = 1;" in pc_bin,
   "U5 已落：`CommandArg.ChannelSingle = 0` / `ChannelDual = 1`（与协议 §5.1 一致）",
   "ChannelArg 取值不符")

# ── U7（已落）：通道按钮已接线 + 仅 STOP 可点
#   ★T134 R13（2026-09-24）：接线体已改成**多行 lambda 块**（点击先落乐观高亮、再发命令）
#   ⇒ 判据由「逐字单行」放宽为「**空白归一化后含目标调用**」；并**另加**一条乐观高亮断言，
#   使判据比原版更严（原来只看 SendCommand，接线丢了才会被抓到）。
f1_flat = re.sub(r"\s+", " ", pc_f1)
ck('SendCommand(BinCommand.SetChannel(CommandArg.ChannelSingle), "SET_CHANNEL(I)")' in f1_flat,
   "U7 已落：`通道 I` 按钮已接线", "通道 I 按钮未接线")
ck('SendCommand(BinCommand.SetChannel(CommandArg.ChannelDual), "SET_CHANNEL(I&II)")' in f1_flat,
   "U7 已落：`通道 I&II` 按钮已接线", "通道 I&II 按钮未接线")
ck("MarkSelectionOptimistic(channelSingleButton, channelDualButton)" in f1_flat
   and "MarkSelectionOptimistic(channelDualButton, channelSingleButton)" in f1_flat,
   "T134 R13：通道两键已接**乐观高亮**（点击即亮，不必等 START 来帧）",
   "T134 R13：通道按钮缺乐观高亮接线（MarkSelectionOptimistic）")
ck("private void MarkSelectionOptimistic(Button selected, Button other)" in f1_flat,
   "T134 R13：`MarkSelectionOptimistic` 已定义", "T134 R13：缺 `MarkSelectionOptimistic` 定义")
ck("channelSingleButton.Enabled = stopOnly;" in pc_f1 and "channelDualButton.Enabled = stopOnly;" in pc_f1,
   "★ U7 已落：通道按钮**仅 STOP 可点** ⇒ 与协议「仅 STOP」＋固件状态门**双保险**",
   "通道按钮未按 STOP 门禁用")
ck("stopOnly = canSend && !streaming;" in pc_f1, "`stopOnly` 派生自 IsStreaming（1.5 s 内无帧 ⇒ STOP）", "stopOnly 推导缺失")

# ── U6/U7（已落）：「拦 Start」= H 组 11 条
ck("public bool CanStart => HardFailures.Count == 0;" in txt(os.path.join(PC, "Config", "ConfigValidator.cs")),
   "★ U6 已落：`CanStart = (H 组不一致数 == 0)` ⇒ **「拦 Start」已实现**（T128 记为待办的那条 **无需再做**，见 §7.N2）",
   "CanStart 判据缺失")
ck("startStopButton.Enabled = streaming || canSend && lastValidation is { CanStart: true };" in pc_f1,
   "U7 已落：Start 按钮由 `CanStart` 把关", "Start 按钮未受 CanStart 约束")
ck("public IReadOnlyList<RegisterValue> FixableValues" in txt(os.path.join(PC, "Config", "ConfigValidator.cs")),
   "U6 已落：`FixableValues` = G 组（「一键下发差异」只碰 G 组 ⇒ H/W 永不被自动写）",
   "FixableValues 缺失")

# ── U8a（已落）：快照闭环
ck("public const double TimeoutSeconds = 1.0;" in pc_sx, "U8a 已落：快照超时 1.0 s", "超时常量缺失")
ck("SnapshotPhase.Waiting when requestId == RequestId => SnapshotOutcome.Accepted," in pc_sx,
   "★ U8a 已落：REQID 匹配才 Accepted；不匹配（Waiting 态）⇒ `IgnoredStale`（**G9/G10 隐式 ACK 的竞态点**，见 §7.N7）",
   "Observe 分类缺失")
ck("_ => SnapshotOutcome.Unsolicited," in pc_sx,
   "U8a 已落：非 Waiting 态的帧一律 `Unsolicited` ⇒ 被采纳（`0x06`/`0x11` 的隐式 ACK 走这条）",
   "Unsolicited 分支缺失")
ck("public static readonly SnapshotRoute SnapshotTriggerRoute = SnapshotRoute.Ascii;" in pc_cfg,
   "⚠ 遗留：`SnapshotTriggerRoute` 仍 = `Ascii`（路线 A）⇒ 快照 REQID 恒 0x00，与 0x06/0x11 的 0x06/0x11 不同值、不互撞",
   "SnapshotTriggerRoute 已变")

# ── P13（已落）
ck("Span<double> samples = stackalloc double[frame.Samples.Length];" in pc_pipe,
   "★ P13 已落（EcgPipeline.cs）：`stackalloc` 按 **frame.Samples.Length** 分配 ⇒ 50 样本不越界",
   "EcgPipeline 的 stackalloc 未按帧长分配")
ck("Span<double> values = stackalloc double[frame.Samples.Length];" in pc_f1,
   "★ P13 已落（Form1.cs）：`stackalloc` 按 **frame.Samples.Length** 分配", "Form1 的 stackalloc 未按帧长分配")
ck("stackalloc double[25]" not in pc_f1 and "stackalloc double[25]" not in pc_pipe,
   "P13 已落：源码内**已无** `stackalloc double[25]` 字面量", "仍有 stackalloc double[25]")

# ── P12（未落）—— 画布标定
ck("const int millimeter = 4;" in pc_wf, "⚠ P12 **未落**：`EcgWaveformControl.DrawGrid` 仍是 `const int millimeter = 4`"
   "（硬编码 4 px/mm）", "millimeter 已参数化（P12 可能已落）")
ck("var pixelsPerMillivolt = GainMillimetersPerMillivolt * 4;" in pc_wf,
   "⚠ P12 **未落**：纵向标定 `pixelsPerMillivolt = 增益 × 4`（4 与网格同一硬编码）",
   "纵向标定已参数化")
ck("var x = plot.Left + (float)i / Math.Max(1, samples.Length - 1) * plot.Width;" in pc_wf,
   "⚠ P12 **未落**：水平分母 = `samples.Length − 1` ⇒ 数据一进来就**铺满整宽**"
   "（启动初期缓冲未满 ⇒ 波形被横向拉伸 ⇒ 视觉误判心率）",
   "水平分母已改为 capacity（P12 可能已落）")
ck("var baselineY = plot.Top + plot.Height / 2f;" in pc_wf, "画布基线固定居中（P12 修正时应保留「不随数据长度变」）",
   "基线算法已变")

# ── U11④（未落）—— 双画布
ck("private readonly EcgWaveformControl waveform = new() { Dock = DockStyle.Fill };" in pc_f1,
   "⚠ U11④ **未落**：仍只有**一个** `EcgWaveformControl waveform`", "已存在第二个画布字段")
ck("split.Panel1.Controls.Add(waveform);" in pc_f1,
   "⚠ U11④ **未落**：`split.Panel1.Controls.Add(waveform)` 仍是**单画布**（RowCount/TableLayoutPanel 未加）",
   "画布容器已改为双面板")
ck("waveform.SetSamples(values, first, markRWaveCheck.Checked ? heartRate.Peaks : Array.Empty<long>());" in pc_f1,
   "⚠ 单缓冲单画布：`RefreshDisplay` 只有一次 `SetSamples` ⇒ 双通道 50 样本会被画成**一条密度 2× 的波形**",
   "SetSamples 调用形态已变")
ck("buffer.Add(samples);" in pc_pipe and "public SampleBuffer Buffer => buffer;" in pc_pipe,
   "⚠ U11④ **未落**：`EcgPipeline` 只有**一个** `SampleBuffer`（双通道 50 样本/帧全进同一缓冲）",
   "SampleBuffer 已拆")
ck("heartRate.Process(values);" in pc_f1,
   "⚠ 未分流：`heartRate.Process(values)` 在双通道下会吃到 **25 CH1 + 25 CH2** 的拼接流"
   " ⇒ 每帧边界出现台阶 ⇒ **假 QRS 峰**（P13 之外的独立缺口，见 §7.N3）",
   "heartRate.Process 调用形态已变")

# ── 效果图三要素（U11④ 的设计参照）
h = txt(T101H)
for kw, tag in [("画布 1 &nbsp;<b id=\"tag1\">导联 I</b>", "画布 1 角标「导联 I」"),
                ("CH1 = IN2−IN1", "画布 1 导联公式 CH1 = IN2−IN1"),
                ("画布 2 &nbsp;<b>导联 II</b>", "画布 2 角标「导联 II」"),
                ("CH2 = IN3−IN1", "画布 2 导联公式 CH2 = IN3−IN1"),
                ("--wave2:#0D47A1", "第二通道配色 `#0D47A1`（深蓝；CH1 = `--wave:#1B5E20` 深绿）"),
                ("$(\"wp2\").style.display = (S.channel===1) ? \"\" : \"none\";", "画布 2 由 channel 开关控制显隐"),
                ("class=\"overlay\"", "每画布 overlay 提示层（未启动 / 已冻结 / 通道 II 说明）"),
                ("p1.fit(); p2.fit();", "⚠ 效果图有 `fit()` 重算（canvas 尺寸为 0 时被拉伸）；"
                                        "**WinForms 侧天然免**（OnPaint 每次按 ClientSize 算 plot 矩形）")]:
    ck(kw in h, "效果图含：%s" % tag, "效果图缺：%s" % tag)
ck("CH2 数据通路本轮" in h and "不验收" in h,
   "⚠ 效果图 :画布2 overlay 文案写「CH2 数据通路本轮**不验收**」—— **已被猛哥拍板③ 覆盖**（本轮要解析+画），见 §7.N8",
   "效果图 overlay 文案已变（预期文本找不到）")

# ══════════════════════════════════════════════════════ §7 新发现
head("§7  ★ 本轮预检新发现（N1~N8 · 台账订正项）")

# N1
ck("public const int MaxArgLength = 100;" in pc_bin,
   "N1 ★ **上位机侧仍留着被 T128 订正掉的错口径**：`BinCommand.MaxArgLength = 100`"
   "（注释写「CDC 缓冲 msg.data[128] ⇒ N ≤ 50，36 项一次下发安全」）",
   "MaxArgLength 已改（N1 可能已修）")
ck("36 项一次性下发 arg = 72 B" in pc_bin,
   "N1 佐证：该文件注释里仍是「36 项一次性下发」措辞 ⇒ 与 T128 订正冲突",
   "N1 注释已订正")
n_fix_max = 20
arg_max = n_fix_max * 2
print("  现实可达上限：`FixableValues` ⊆ G 组 20 条 ⇒ arg ≤ %d B ⇒ LEN ≤ %d（< 58 ✓ 当前**不会**触发静默丢弃）"
      % (arg_max, 1 + arg_max))
ck(arg_max <= 56, "N1 结论：把 `MaxArgLength` 100 → **56**（= VCOM_BIN_MAX_LEN 57 − 1）**改变不了今天的任何行为**"
   "（现有最大 arg = 40 B < 56）⇒ 是**零行为风险的封口**",
   "现有 arg 上限已超 56 ⇒ 需真分批")

# N2
ck("public bool CanStart => HardFailures.Count == 0;" in txt(os.path.join(PC, "Config", "ConfigValidator.cs")),
   "N2 ★ T128 记为「后续补全」的**上位机拦 Start 已经在了**（U6/U7 已落并验收）"
   " ⇒ 该待办应改为「**核实**」而非「新增」",
   "CanStart 判据缺失 ⇒ N2 结论不成立")

# N3
ck("heartRate.Process(values);" in pc_f1,
   "N3 ★ **新缺口（台账未记）**：心率/QRS 检测器**未按通道分流** ⇒ 双通道下每帧把 CH2 段当 CH1 的延续"
   " ⇒ 帧边界台阶 ⇒ 假峰。修法：只喂 CH1 切片（或各配一个检测器）",
   "心率检测器已分流")

# N4
ck("length != DisplayLenDual" in pc_pro,
   "N4 ★ 好消息：`FrameParser` 已接受 LEN=103 ⇒ **G9 上位机侧不需要改解析器**（台账 U2 已提前落码）",
   "解析器不接受 103")

# N5
ck("public const int SamplesPerFrame = 25;" in pc_cfg,
   "N5 ★ **台账 P11 的修法自相矛盾**：执行计划写「`samplesPerFrame` 由 LEN 反推 `(LEN−3)/2`」"
   "⇒ 双通道下得 **50** ⇒ `sequenceDelta × 50 / t` = **1000 Hz** ⇒ 正是 M1 明令禁止的「虚高 2×」",
   "SamplesPerFrame 已被改成按 LEN 反推（需复核是否引入 2× 虚高）")
print("  正确口径：每通道每帧样本数 ≡ 25（= N / nch；双通道下 N=50、nch=2 ⇒ 25）")
print("  ⇒ `sequenceDelta × 25 / t` 在 500 Hz（20 帧/s × 25）与双通道（同为 20 帧/s × 25/通道）下**都对**")
ck(25 / 500.0 == 0.05, "N5 佐证：单帧时长 25/500 = 0.05 s 在两种通道数下**相同** ⇒ 现实现正确，只有台账措辞要订正",
   "帧时长算术异常")

# N6
ck("_bch2 = (on != 0u) ? 1u : 0u;\r\n}" in txt(SRC_C) or "_bch2 = (on != 0u) ? 1u : 0u;\n}" in txt(SRC_C).replace("\r", ""),
   "N6 ★ **台账与实现冲突**：T106:170 / 执行计划:151 要求「切完 `queue_reset` 两个队列（P7）」，"
   "但 G8 的 `ads1293_set_ch2()` 明确**不 queue_reset**（已被 G8 断言）",
   "set_ch2 已含 queue_reset（冲突自动消失）")
ck("CH2 输出开关" in txt(SRC_C) and "无需复位重采样器" in txt(SRC_C),
   "N6 佐证：G8 注释原话「切换**不碰任何滤波节点状态** ⇒ 无瞬态（与 raw853 不同，无需复位重采样器）」",
   "G8 注释已变")
print("  N6 机理：`_bch2` 只切**打包分支**；队列项自 G8 起恒含 CH1+CH2（`:861` 一次读 6 B）"
      "⇒ 队列内容口径不变 ⇒ 无残留；且状态门已限 STOP，`ads1293_start()` 内本就清两队列")

# N7
ck("SnapshotPhase.Waiting when requestId == RequestId => SnapshotOutcome.Accepted," in pc_sx,
   "N7 ★ **隐式 ACK 竞态**：若恰有一次快照请求处于 Waiting（REQID=0x00，路线 A），"
   "此时 `0x06`/`0x11` 的隐式 ACK（REQID=0x06/0x11）会落 `IgnoredStale` ⇒ **被丢弃** ⇒ 界面看不到生效证据",
   "竞态点已消除")
print("  现状缓解：`SendCommand()` **不**调用 `snapshotExchange.Begin()` ⇒ Phase 通常非 Waiting ⇒ 走 Unsolicited（采纳）")
print("  建议：把 REQID ∈ {0x06, 0x11} 判为「隐式 ACK」⇒ 无论 Phase 如何都采纳（并单独计数）")

# N8
ck("CH2 数据通路本轮" in txt(T101H),
   "N8 ★ **效果图 overlay 文案过时**：画布 2 写「CH2 数据通路本轮不验收」，"
   "而猛哥拍板③ 明确本轮**要**同时解析两通道并画波形 ⇒ 文案须改为（如）「通道 II · CH2 ≡ CH1（G8 复制）」",
   "效果图 overlay 文案已是新版")
ck("**不含**固件第二套 `ecg_filter_f64`" in t106,
   "N8 佐证：T106 §9n 拍板③ 已排除固件第二套滤波实例 ⇒ T101 协议 §0x06 的「固件改动只剩三处」"
   "（含第二套滤波实例）**已过时**：三处中前两处 G8 已交付、第三处（帧长）G8 已交付"
   "⇒ **G9 固件侧只剩 1 处分派**",
   "拍板③ 文本已变（请复核 T101 §0x06 的『三处』是否仍写第二套滤波实例）")

# ══════════════════════════════════════════════════════ §7b 落码前二次复核（N9/N10）
head("§7b  落码前二次复核（N9/N10 · 上位机口径 —— 与 T106 §9p.4 同步）")

PC_PROTO = os.path.join(PC, "Core", "Protocol.cs")
PC_CFG = os.path.join(PC, "Config", "AppConfig.cs")
PC_FORM = os.path.join(PC, "Form1.cs")
PC_PIPE = os.path.join(PC, "Core", "EcgPipeline.cs")
s_proto = txt(PC_PROTO)
s_cfg = txt(PC_CFG)
s_form = txt(PC_FORM)

# ---- N9：ChannelCount 已存在，但全仓零引用 ----
n_cc = len(re.findall(r"public int ChannelCount\s*=>", s_proto))
print("  DisplayFrame.ChannelCount 定义处数 = %d" % n_cc)
ck(n_cc == 1,
   "N9 `DisplayFrame.ChannelCount` 已存在（`Core/Protocol.cs:41`，"
   "`Samples.Length > AppConfig.SamplesPerFrame ? 2 : 1`）",
   "ChannelCount 定义处数 = %d（预期 1）" % n_cc)

hits = []
for root, _dirs, files in os.walk(PC):
    for fn in files:
        if fn.endswith(".cs"):
            fp = os.path.join(root, fn)
            for i, ln in enumerate(txt(fp).split("\n"), 1):
                if "ChannelCount" in ln:
                    hits.append((os.path.relpath(fp, PC).replace("\\", "/"), i, ln.strip()))
print("  全仓 ChannelCount 出现 = %d 处" % len(hits))
for rel, i, ln in hits:
    print("    %s:%d  %s" % (rel, i, ln[:88]))
ck(len(hits) == 1,
   "N9 ★ 该属性**全仓零引用**（只剩定义行 ⇒ 死代码，为 G9 预置）⇒ 落码可直接用、**不需要新增属性**",
   "ChannelCount 已被引用（%d 处）⇒ 请复核 N9 表述" % len(hits))
ck("public const int DualChannelSamplesPerFrame = SamplesPerFrame * 2;" in s_cfg,
   "N9 佐证：`AppConfig.DualChannelSamplesPerFrame = SamplesPerFrame * 2` 也已备（同为零引用）",
   "DualChannelSamplesPerFrame 定义变了")
ck("由 LEN 反推" in s_proto,
   "N9 佐证：`Protocol.cs` 注释已写死「通道数**由 LEN 反推**（硬事实），不靠 STA.channel（软标志）」"
   "⇒ 落码时不要改成读 `IsDualChannelFlag`",
   "「由 LEN 反推」注释不见了（口径被改）")

# ---- N10：速率显示口径（落码时零改动） ----
m_rate = re.search(r"sequenceDelta\s*\*\s*\(double\)AppConfig\.SamplesPerFrame\s*/\s*metricsElapsed", s_form)
ck(m_rate is not None,
   "N10 ★ `Form1.cs` 速率显示 = `sequenceDelta * (double)AppConfig.SamplesPerFrame / metricsElapsed`"
   "；`SamplesPerFrame = 25` 语义是**每通道每帧** ⇒ 双通道帧下仍给 **500 Hz**（正确）",
   "速率公式模式变了（请人工核对 Form1.cs 的采样率显示）")
ck(re.search(r"public const int SamplesPerFrame = 25;", s_cfg) is not None,
   "N10 佐证：`AppConfig.SamplesPerFrame = 25` 未变 ⇒ **N5 正解已被实现固化**"
   "（🛑 落码时对该速率行**零改动**；若照台账 P11 改成 LEN 反推 ⇒ 跳 1000 Hz）",
   "SamplesPerFrame 定义变了（可能已照台账 P11 改成 LEN 反推 ⇒ 会引入 2× 虚高）")

n_alloc = len(re.findall(r"stackalloc double\[frame\.Samples\.Length\]",
                         s_form + txt(PC_PIPE)))
n_perch = len(re.findall(r"stackalloc double\[AppConfig\.SamplesPerFrame\]", txt(PC_PIPE)))
ck(n_alloc == 1 and n_perch == 2,
   "P13 已解（T133 落码后口径）：`Form1.cs` 仍按**本帧实际样本数**分配 1 处；"
   "`EcgPipeline.cs` 改为按**每通道常量**分配 2 处（CH1/CH2 各一）⇒ 双通道帧不会栈越界",
   "按实际样本数 = %d（预期 1）、按每通道常量 = %d（预期 2）" % (n_alloc, n_perch))

# ══════════════════════════════════════════════════════ §8 红线
head("§8  红线（零改动指纹）")

# 表体 md5（★ 口径：split(b"\n") 的段**已带 \r**，禁止再 join b"\r\n" —— T128 踩过的假 FAIL 坑）
raw_c = read(SRC_C)
bl = raw_c.split(b"\n")
st_b = next(i for i, l in enumerate(bl) if l.strip().startswith(b"const uint8_t regs_leaderIII[][2]"))
en_b = next(i for i in range(st_b, len(bl)) if bl[i].strip() == b"};")
body_now = b"\n".join(bl[st_b:en_b + 1])
md5_now = hashlib.md5(body_now).hexdigest()
print("  表体 md5（当前）   = %s（%d 行，%d B）" % (md5_now, en_b - st_b + 1, len(body_now)))
ck(md5_now == "e543248d4bcfb7fc2c381891b293b237",
   "表体 md5 == T128/G8 基线 `e543248d…b237`（逐字节相同 ✓）",
   "表体 md5 变了：%s ≠ e543248d4bcfb7fc2c381891b293b237" % md5_now)
bl_b = read(os.path.join(BAK, "ads1293.c")).split(b"\n")
st_bb = next(i for i, l in enumerate(bl_b) if l.strip().startswith(b"const uint8_t regs_leaderIII[][2]"))
en_bb = next(i for i in range(st_bb, len(bl_b)) if bl_b[i].strip() == b"};")
body_bak = b"\n".join(bl_b[st_bb:en_bb + 1])
ck(body_now == body_bak, "表体与留痕 `tools/_t128_bak/ads1293.c` 的对应区段**逐字节相同**（双保险）",
   "表体与留痕逐字节不同")

# Filter/ 14 文件
fmd5 = {}
for fn in sorted(os.listdir(FILTER_DIR)):
    fp = os.path.join(FILTER_DIR, fn)
    if os.path.isfile(fp) and fn.endswith((".c", ".h")):
        fmd5[fn] = hashlib.md5(read(fp)).hexdigest()
ck(len(fmd5) == 14, "Filter/ = 14 个源文件（.c/.h）", "Filter/ = %d 个源文件（基线 14）" % len(fmd5))
BASELINE_FILTER = {
    "biquad.c": "164c539a5365a3230a96348d81ee376b", "biquad.h": "5e50226dbc7944adc926579d62149b6a",
    "ecg_crc8.c": "258784d8e93aaef15c62f2099fa0e423", "ecg_crc8.h": "9f12523b99ec769b7b413c812a743879",
    "ecg_dc_block.c": "2a78354c176d285c8c317224e1c54a4b", "ecg_dc_block.h": "3934d5850fd538ac397dc86b128d2d56",
    "ecg_filter.c": "83b2f097a3ad825c0263cdaf602d1900", "ecg_filter.h": "236e9aebd634c88c253ad34621d4dc04",
    "ecg_filter_coeffs.h": "522f0fa344605dd249ea21e793dbe9a3", "ecg_out.c": "44c0e57b6c97819a164cba150b929c21",
    "ecg_out.h": "c3c7a61ebee6f145ef9a5fe0e9f7f6a7", "fast_restore_snippet.c": "42a5ebe7d0ec7775524b77cb0ff84c01",
    "welch_autotune.c": "3988013e5a16b0610656f33d2d1344b6", "welch_autotune.h": "7e2676bbf5155d17b1c86e7e61ea1481",
}
bad = [fn for fn, md in BASELINE_FILTER.items() if fmd5.get(fn) != md]
ck(not bad, "★ Filter/ 14 文件 md5 **全部**与基线一致（滤波参数零改动）",
   "Filter/ 有文件被改：%s" % " ".join(bad))

# ECG_* 宏（ads1293.c）
macros = sorted(set(re.findall(r"#\s*define\s+(ECG_[A-Z0-9_]+)\s+([0-9]+)", s_c)))
print("  ads1293.c 的 ECG_* 宏 = %s" % (" ".join("%s=%s" % kv for kv in macros) or "(无)"))
ck(macros == [("ECG_LOD_EN", "0")], "ads1293.c 的 ECG_* 宏集合仍 = {ECG_LOD_EN=0}", "ECG_* 宏集合变了：%s" % macros)
n_uv = len(re.findall(r"UV_PER_CODE", s_c))
print("  ads1293.c 中 UV_PER_CODE 出现次数 = %d（G8 基线值，本步不动）" % n_uv)
ck(n_uv > 0, "UV_PER_CODE 仍在（本步零改动 ⇒ 计入对照）", "UV_PER_CODE 消失")

# VCOM_BIN_MAX_LEN
vm = re.search(r"#\s*define\s+VCOM_BIN_MAX_LEN\s+([0-9]+)u", s_v)
print("  VCOM_BIN_MAX_LEN = %s（本步**不改**）" % (vm.group(1) if vm else "?"))
ck(vm and int(vm.group(1)) == 58, "VCOM_BIN_MAX_LEN 仍 58u（策略 B 不碰它）", "VCOM_BIN_MAX_LEN 被改动")

# usbd_cdc_if.c
ck(hashlib.md5(read(CDC)).hexdigest() == "b342deff52e9a0184edaeeb815e1f19c",
   "usbd_cdc_if.c md5 == 基线 `b342deff…f19c`（CDC 层零改动）", "usbd_cdc_if.c 被改动")

# 预检阶段说明（不是断言，只打印）
print("  ℹ T133 起本脚本为**落码后**口径：两条「落码前状态断言」已翻转（见 §6 的 n_perch 与 §9 的 case 0x06u），"
          "其余断言（尤其 §8 红线 6/6）落码前后均应全绿。")

# ══════════════════════════════════════════════════════ §9 基线现状
head("§9  基线现状（落码后 · T133）")

cases = re.findall(r"case\s+(0x[0-9a-fA-F]{2})u:", s_v)
print("  vcomcmd.c 已实现 case = %s" % " ".join(cases))
ck("0x06" in cases, "★ 0x06 已实现（T133 落码；落码前的「未实现 ⇒ 静默丢弃」口径到此翻转）",
   "0x06 仍缺失 ⇒ 落码未生效？")
for c in ("0x10", "0x03", "0x01", "0x02", "0x04", "0x05", "0x11"):
    ck(c in cases, "case %s 在位（口径参照）" % c, "case %s 缺失（参照丢了）" % c)

print()
print("  【G9 判据预期表现（落码后）】")
print("    ① STOP 态发 SET_CHANNEL(I&II) ⇒ 收 0x03 快照（REQID=0x06）+ 后续帧 **109 B**/LEN=0x67")
print("    ② 抓任一 109 B 帧看第 8 字节 buff[7] ⇒ STA.bit2 = 1（0x06）")
print("    ③ 帧内 CH2 段（偏移 58 起 50 B）与 CH1 段（偏移 8 起 50 B）**逐字节相同**（G8 复制）")
print("    ④ RUN 态发 0x06 ⇒ 状态门静默 ⇒ 上位机等满 1.0 s 超时（与 0x10/0x11 同表现）")
print("    ⑤ 非法长度（如 LEN=3）⇒ 静默丢弃、不崩、不写")

print()
print("=" * 78)
print("汇总：%d PASS ／ %d FAIL ／ %d 跳过（相位=%s）" % (N_OK, len(FAIL), len(SKIPPED), PHASE))
for f in FAIL:
    print("  FAIL: %s" % f)
if SKIPPED and PHASE == "post":
    print("  ⌛ 跳过的是「预检期守卫」（落码后预期翻转）⇒ 正面判据见 tools/_t133_g9_selftest：66 PASS/0 FAIL")
print("=" * 78)
sys.exit(1 if FAIL else 0)
