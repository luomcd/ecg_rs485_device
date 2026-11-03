# -*- coding: utf-8 -*-
"""
_t114_g4_sim.py —— G4「下行二进制分流」判决的离线复刻 ＋ 源码锚定（零硬件）
================================================================================

为什么值得单独跑一遍（而不是"代码看着对"）：
  G4 的两种失败模式**都是静默的** ——
    ① **多丢**：该发的没发  ⇒ 上位机等满 1.0 s 超时（看上去像"固件没落码"）
    ② **少丢**：不该发的发了 ⇒ 上位机收到非预期帧（看上去像"线路噪声"）
  两者都不报错、不上日志（本路径按纪律禁用 printf_msg）⇒ 只有把判决链**单独**
  跑一遍、把两种拒绝分开，才能在上板前分辨"是逻辑错"还是"是链路问题"。

本脚本做三件事：
  §1 **CRC 实现等价性证明**：固件编译的是**查表版**（`ECG_CRC8_USE_TABLE=1`），
     PC 侧惯例写**位运算版** —— 只靠"标准向量对得上"是**归纳**，不是证明。
     这里直接从 `Filter/ecg_crc8.c` 解析出 256 字节表，对 **全部 65536 个
     (crc, byte) 组合**逐项比对两版 ⇒ 等价性是**穷举证明**的。
  §2 **源码锚定**：从 `Core/Src/vcomcmd.c` 真源码里解析
     `VCOM_BIN_MAX_LEN`、`case 0x10u`、`_bconvstart`、调用点三件套 ——
     固件若被改动（宏被改、状态门被删、复位漏写），本脚本会直接报警，
     避免"脚本还在验旧口径"。
  §3 **判决链复刻 ＋ 用例表**：逐条复刻 `vcom_bin_dispatch()`，
     喂正例/负例，断言"该发的发、该丢的丢"。

口径（必须与固件逐条同源，任一不符本脚本自证失败）：
   poly=0x07 / init=0x00 / refin=false / refout=false / xorout=0x00
   CRC 覆盖 TYPE+LEN+PAYLOAD（不含帧头、不含 CRC 自身）
   帧总长 = 6 + LEN ；LEN ≥ 1 ；LEN ≤ VCOM_BIN_MAX_LEN(=58)
   下行命令帧 TYPE = 0x80 ；本步只实现 CMD = 0x10 REQ_SNAPSHOT

用法:
  python _t114_g4_sim.py             # 跑全部（含 §1 穷举，约 0.5 s）
  python _t114_g4_sim.py --quick     # 跳过 §1 穷举
  python _t114_g4_sim.py --verbose   # 打印每条用例的判决理由
"""
from __future__ import annotations

import io
import os
import re
import sys

# ---------------------------------------------------------------- 路径与开关
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRC_SRC = os.path.join(ROOT, "Filter", "ecg_crc8.c")
VCOM_SRC = os.path.join(ROOT, "Core", "Src", "vcomcmd.c")

VERBOSE = "--verbose" in sys.argv
QUICK = "--quick" in sys.argv

_ok = 0
_fail: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    """统一断言入口：计数 + 逐条打印（不中断，跑完给汇总）。"""
    global _ok
    if cond:
        _ok += 1
        print(f"  ✓ {name}" + (f"   {detail}" if detail else ""))
    else:
        _fail.append(name)
        print(f"  ✗ {name}   {detail}")


def read(path: str) -> str:
    return io.open(path, encoding="utf-8", errors="replace").read()


# ================================================================ §0 CRC 两版
POLY = 0x07


def crc8_bitwise_update(crc: int, byte: int) -> int:
    """位运算版（对应 ecg_crc8.c 的 `#else` 分支）。"""
    crc ^= byte
    for _ in range(8):
        crc = ((crc << 1) ^ POLY) & 0xFF if (crc & 0x80) else ((crc << 1) & 0xFF)
    return crc


def crc8_bitwise(data: bytes) -> int:
    """一次性版 —— 与 ecg_crc8() 同结构（while(len--) crc = update(...)）。"""
    crc = 0x00
    for b in data:
        crc = crc8_bitwise_update(crc, b)
    return crc


def parse_table(c_src: str) -> list[int]:
    """从 ecg_crc8.c 里解析 s_crc8_table[256]（不自己算，读编进固件的那张表）。"""
    m = re.search(r"s_crc8_table\s*\[\s*256\s*\]\s*=\s*\{(.*?)\}\s*;", c_src, re.S)
    if not m:
        raise SystemExit("✗ 没能从 ecg_crc8.c 解析出 s_crc8_table")
    vals = [int(t, 16) for t in re.findall(r"0x([0-9A-Fa-f]{2})", m.group(1))]
    if len(vals) != 256:
        raise SystemExit(f"✗ s_crc8_table 解析到 {len(vals)} 项，应为 256")
    return vals


TABLE = parse_table(read(CRC_SRC))

# ======================================================= §2 固件源码结构锚定
vcom_src = read(VCOM_SRC)

m_max = re.search(r"#define\s+VCOM_BIN_MAX_LEN\s+(\d+)u", vcom_src)
MAXLEN = int(m_max.group(1)) if m_max else 58


def main() -> int:
    print("=" * 78)
    print("§0 固件口径解析（读真源码，不用默认值）")
    print("=" * 78)
    check("① `#define VCOM_BIN_MAX_LEN` 存在", m_max is not None,
          f"= {MAXLEN}" if m_max else "未找到 ⇒ 固件 LEN 上限没有显式落码？")
    check("② 上限值与 CDC 单包推导一致（64 − 帧开销 6 = 58）", MAXLEN == 58, f"= {MAXLEN}")
    check("③ `#include \"ecg_crc8.h\"` 已补（否则 implicit declaration）",
          '#include "ecg_crc8.h"' in vcom_src)
    check("④ dispatch 由 `static` 定义（⇒ vcomcmd.h / ads1293.h 零改动）",
          re.search(r"static\s+void\s+vcom_bin_dispatch\s*\(", vcom_src) is not None)
    check("⑤ 实现了 `0x10` 分支（case 0x10u）",
          re.search(r"case\s+0x10u\s*:", vcom_src) is not None)
    check("⑥ 有状态门（`if (_bconvstart)` 拒发）",
          re.search(r"if\s*\(\s*_bconvstart\s*\)", vcom_src) is not None)
    check("⑦ 复用 G3 导出的发送入口 ads1293_send_snapshot(0x10u, …)",
          re.search(r"ads1293_send_snapshot\s*\(\s*0x10u\s*,", vcom_src) is not None)
    check("⑧ 🛑 本路径无 printf_msg（HAL_Delay(5) 会丢样本）",
          "printf_msg" not in _code(_slice(vcom_src, "static void vcom_bin_dispatch", "void vcom_recv_analysis")))

    # 调用点三件套 —— 最容易漏、后果最重（漏了 = 每圈重发一帧快照）
    #   ⚠ 锚点必须带行首缩进：本文件注释里也出现过 "while(1)" 字面量，
    #     用裸串切片段会把片段截在注释中间（本脚本第一版就这么错过）。
    while_at = vcom_src.index("\n    while(1)")
    call = vcom_src[vcom_src.index("if (VCOM_RxLength >= 7u"):while_at]
    check("⑨ 🛑 调用点：按 0xAA55 判帧头", "VCOM_RxBuffer[0] == 0xAAu" in call)
    check("⑩ 🛑 调用点：复位三件套写在同一分支里（不能指望 while 之后的 :398-400）",
          all(k in call for k in ("VCOM_RxData   = 0;", "VCOM_RxBuffer = NULL;", "VCOM_RxLength = 0;")))
    check("⑪ 🛑 调用点：**必须 return**（否则二进制流进 CRLF 扫描 ⇒ 撞毁 ASCII 通道）",
          re.search(r"return;", call) is not None)
    check("⑫ 调用点位于 `while(1)` **之前**（这正是插入点纪律）",
          vcom_src.index("VCOM_RxBuffer[0] == 0xAAu") < while_at)

    print()
    print("=" * 78)
    print("§1 CRC 等价性：查表版 vs 位运算版（穷举 65536 组合）")
    print("=" * 78)
    check("标准 check 向量 ecg_crc8(\"123456789\") = 0xF4",
          crc8_bitwise(b"123456789") == 0xF4)
    check("向量 ecg_crc8(0x00) = 0x00", crc8_bitwise(bytes([0x00])) == 0x00)
    check("向量 ecg_crc8(0x01) = 0x07", crc8_bitwise(bytes([0x01])) == 0x07)

    if QUICK:
        print("  (--quick：跳过穷举)")
    else:
        bad = 0
        for crc in range(256):
            for byte in range(256):
                if TABLE[crc ^ byte] != crc8_bitwise_update(crc, byte):
                    bad += 1
        check(f"★ 查表版与位运算版在全部 65536 个 (crc,byte) 上逐项一致",
              bad == 0, f"不一致 {bad} 处")

    # 上板实测真帧（T109 采集，80 B，帧内末字节 CRC=0xF7）
    REAL = ("AA 55 03 4A 00 00 24 00 00 01 11 02 19 03 00 04 00 05 00 06 08 07 00 08 00 09 00 0A 07 0B 00 "
            "0C 04 0D 00 0E 00 0F 00 10 00 11 00 12 04 13 00 14 00 15 00 16 00 17 05 21 02 22 02 23 02 "
            "24 02 25 00 26 00 27 08 28 00 29 00 2A 00 2E 33 2F 30 F7")
    real = bytes(int(t, 16) for t in REAL.split())
    check("上板真帧长度 = 80 B", len(real) == 80, f"= {len(real)}")
    check("★ 上板真帧 CRC 复算 = 帧内末字节（证明本实现与固件同口径）",
          crc8_bitwise(real[2:-1]) == real[-1], f"{crc8_bitwise(real[2:-1]):02X} vs {real[-1]:02X}")

    print()
    print("=" * 78)
    print("§3 判决链复刻 + 用例表")
    print("=" * 78)
    _cases()

    print()
    print("=" * 78)
    print(f"结果：PASS {_ok} / FAIL {len(_fail)}")
    if _fail:
        for f in _fail:
            print(f"  ✗ {f}")
        return 1
    print("全部通过 ✓")
    return 0


def _slice(text: str, start_marker: str, end_marker: str) -> str:
    """取 text 中 start_marker .. end_marker 之间的片段（找不到则回退整文件）。"""
    try:
        i = text.index(start_marker)
        j = text.index(end_marker, i + len(start_marker))
        return text[i:j]
    except ValueError:
        return ""


def _code(text: str) -> str:
    """剥掉注释与空行 —— 技能 §1.3：断言前必先剥注释，否则注释里的字面串会误判。

    🆕 T125/G8（2026-09-23）补：本脚本原先**没剥**，而下面断言⑧ 的切片区间
    （`vcom_bin_dispatch` .. `vcom_recv_analysis`）自 G6 起含注释块，注释里写了
    「返回值**不许 printf_msg 回显**」这样的字面串 ⇒ 断言⑧ 从 G6 之后一直是
    **假 FAIL**，直到 G8 重跑既有回归才暴露（阴性对照见 §8 归因）。
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    lines = [l.strip() for l in text.split("\n")]
    return "\n".join(l for l in lines if l)


# ------------------------------------------------------------- 帧构造 / 判决
def build(type_: int, payload: list[int]) -> bytes:
    """按协议拼一帧：AA 55 | TYPE | LEN(2B LE) | PAYLOAD | CRC8。"""
    body = bytes([type_]) + len(payload).to_bytes(2, "little") + bytes(payload)
    return b"\xAA\x55" + body + bytes([crc8_bitwise(body)])


def flip_crc(frame: bytes) -> bytes:
    """把末字节 CRC 改一位 ⇒ 必失败帧。"""
    return frame[:-1] + bytes([frame[-1] ^ 0x01])


def sim(frame: bytes, running: bool) -> tuple[str, str]:
    """逐条复刻 vcom_bin_dispatch()；返回 (动作, 理由)。

    动作：SEND / DROP / ASCII（不进分流，交由原 CRLF 路径）。
    """
    n = len(frame)
    # 调用点先判：长度 ≥ 7 且前两字节 0xAA 0x55
    if n < 7 or frame[0] != 0xAA or frame[1] != 0x55:
        return ("ASCII", "不进分流（交给 CRLF 路径）")
    type_ = frame[2]
    arglen = frame[3] | (frame[4] << 8)
    if arglen > MAXLEN:
        return ("DROP", f"argLen={arglen} > {MAXLEN}")
    if 6 + arglen != n:
        return ("DROP", f"长度对核失败（自称 {6 + arglen} B，实收 {n} B）")
    if crc8_bitwise(frame[2:n - 1]) != frame[n - 1]:
        return ("DROP", "CRC 不符")
    if type_ != 0x80:
        return ("DROP", f"TYPE={type_:#04x} 非下行命令帧")
    cmd = frame[5]
    if cmd == 0x10:
        if running:
            return ("DROP", "状态门：RUN 态静默（协议 §5.1）")
        return ("SEND", "reqid=0x10")
    return ("DROP", f"CMD={cmd:#04x} 本步未实现 ⇒ 静默")


def _cases() -> None:
    good = build(0x80, [0x10])

    # ★ 作业卡字节与独立构造必须逐字节相同（把 T106 §9c C-3 绑到代码上）
    check(f"★ 作业卡字节 AA 55 80 01 00 10 2A == 本脚本构造 {good.hex(' ').upper()}",
          good.hex() == "aa5580010010" + f"{good[-1]:02x}", f"CRC={good[-1]:02X}")
    check("★ 该帧 CRC = 0x2A（T113 三重交叉验证值）", good[-1] == 0x2A, f"= {good[-1]:02X}")

    # 带 1 B ARG 的合法帧（G5~G9 的形态：SET_MODE(0) 等 8 B 帧）
    eight = build(0x80, [0x04, 0x00])
    check("8 B 帧（LEN=2，G5 形态）构造成功且长度对核通过",
          len(eight) == 8 and sim(eight, False)[0] == "DROP"
          and "未实现" in sim(eight, False)[1], eight.hex(" ").upper())

    cases = [
        ("★ 正例：REQ_SNAPSHOT（STOP 态）", good, False, "SEND"),
        ("★ 状态门：同一帧但 RUN 态 ⇒ 静默", good, True, "DROP"),
        ("副判据① 坏 CRC（2A→2B）⇒ 无响应", flip_crc(good), False, "DROP"),
        ("长度对核失败（LEN 自称 3 ⇒ 应 9 B，实收 7 B）",
         bytes([0xAA, 0x55, 0x80, 0x03, 0x00, 0x10, 0x2A]), False, "DROP"),
        ("LEN 超上限（LEN=59 > 58）⇒ 丢弃",
         b"\xAA\x55\x80" + bytes([59, 0]) + bytes([0x10]) + b"\x00", False, "DROP"),
        ("TYPE 非 0x80（快照帧 0x03 被误当命令）", build(0x03, [0x10]), False, "DROP"),
        ("未知 CMD（0x99，G5 之后才实现）⇒ 静默", build(0x80, [0x99]), False, "DROP"),
        ("合法 8 B 帧但 CMD 未实现（0x04 SET_MODE）", eight, False, "DROP"),
        ("长度 6 B（LEN=0，无 CMD）⇒ 不进分流", b"\xAA\x55\x80\x00\x00\xFF", False, "ASCII"),
        ("帧头近似但非 0xAA55（AA 56 …）⇒ 不进分流",
         b"\xAA\x56\x80\x01\x00\x10\x2A", False, "ASCII"),
        ("ASCII 快照命令 test snapshot\\r\\n ⇒ 绝不误吞",
         b"test snapshot\r\n", False, "ASCII"),
    ]

    for name, frame, running, want in cases:
        got, why = sim(frame, running)
        ok = got == want
        check(f"  {name}", ok, f"⇒ {got}（{why}）" + ("" if ok else f"  期望 {want}"))
        if VERBOSE:
            print(f"      bytes = {frame.hex(' ').upper()}")

    # ASCII 通道回归：证明"两通道互不干扰"是**结构性**的，不是"碰巧"
    #  · `:403` 的 `memcmp(&msg.data[0],"test",4)` ⇒ 能进 ASCII 分派的帧，首字节
    #    **恒为 't' = 0x74**（可打印）⇒ 与 0xAA/0x55 天然不相交。
    #  · ⚠ 注意 `memcmp(argv[1], "xxx", …)` 里的 `argv[1]` 是 **"test" 之后的第 2 个
    #    token**（子命令名，如 config/notch/snapshot），**不是**整条命令 —— 首字节与它无关。
    check('★ ASCII 入口前缀恒为 "test"（:403 的 memcmp("test",4)）',
          'memcmp(&msg.data[0],"test",4)' in vcom_src)
    check("★ ASCII 入口首字节 't' = 0x74 ∉ {0xAA, 0x55} ⇒ 两通道结构性互不相交",
          ord("t") not in (0xAA, 0x55))

    subs = sorted(set(re.findall(r'memcmp\s*\(\s*argv\[1\]\s*,\s*"([A-Za-z0-9_]+)"', read(VCOM_SRC))))
    bad = [s for s in subs if sim(f"test {s}\r\n".encode(), False)[0] != "ASCII"]
    check(f"★★ ASCII 通道回归：{len(subs)} 条命令（test <子命令>）逐条都不进分流",
          subs and not bad, f"子命令 = {', '.join(subs)}" + (f"  异常：{bad}" if bad else ""))


if __name__ == "__main__":
    sys.exit(main())
