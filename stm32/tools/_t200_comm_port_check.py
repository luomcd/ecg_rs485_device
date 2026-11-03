#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t200_comm_port_check.py —— T198 通信通道抽象层「语义门禁」

背景（为什么要这道门）
----------------------
`arm-none-eabi-gcc -fsyntax-only` 只回答「能不能编过」，对「改错了地方」零覆盖
（技能 §3.1b 实测：把 `if (allow)` 改成 `if (1)` 语法全绿）。本次改造的风险面恰好
全在语义层：

  · 5 处发送点里 **2 处在 `#if 0` 死码里** —— 只数"全文出现几次 `CDC_Transmit_FS`"
    会把死码算进来，得出"还有 2 处没改"的假红；反之，若死码被误活，又查不出来。
  · `MX_USB_DEVICE_Init()` 必须**整个不被调用**（USB/UART5 物理互斥），
    "调用了但没初始化成功"不够 —— 那是两个推挽输出对驱。
  · `comm_send()` 的**忙判必须在拷贝之前**，顺序挪一下就从"丢一帧"变成"覆盖在途数据"。

⇒ 本脚本把上述纪律写成断言，并**自带阴性对照**（故意改坏一处必须报 FAIL）。

用法
----
  python tools/_t200_comm_port_check.py
  python tools/_t200_comm_port_check.py --selftest   # 只跑阴性对照

退出码：0 全过 / 3 有 FAIL / 4 自检未通过
"""
import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

FAILS = []
OKS = []


def ok(tag, msg=""):
    OKS.append((tag, msg))


def bad(tag, msg):
    FAILS.append((tag, msg))


def read(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


def strip_comments(s):
    """剥注释后再做「不得出现」类断言 —— 否则注释里的字样会报假失败。"""
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def live_text(src):
    """按 `#if 0 ... #else ... #endif` 求值，只保留**真会编译**的文本。

    🛑 极简求值器，只认识 `#if 0` / `#if 1`；其余条件一律当"真"（=保留）。
    这样做的理由是：本脚本只关心"那 2 处 `#if 0` 里的死码有没有被误算/误活"，
    不需要完整的预处理语义；但**必须**断言 live_text 确实比原文短，
    否则说明求值器没生效（= 判据空跑）。
    """
    out = []
    stack = []          # 每层: [父是否活, 当前分支是否活, 本层是否已取过某分支]
    alive = True
    for line in src.split("\n"):
        t = line.strip()
        m = re.match(r"#\s*if(n?def)?\b(.*)$", t)
        if m:
            cond = m.group(2).strip()
            if cond.startswith("0"):
                this = False
            elif cond.startswith("1"):
                this = True
            else:
                this = True         # 未知条件 ⇒ 当活（保守：宁可多查）
            stack.append([alive, alive and this, this])
            alive = alive and this
            continue
        if re.match(r"#\s*else\b", t):
            if stack:
                parent, cur, taken = stack[-1]
                stack[-1] = [parent, parent and (not taken), True]
                alive = parent and (not taken)
            continue
        if re.match(r"#\s*endif\b", t):
            if stack:
                parent = stack.pop()[0]
                alive = parent
            continue
        if alive:
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------
def check_all():
    # 1) 通道开关默认必须是 UART5
    h = read("Core/Inc/comm_port.h")
    if h is None:
        bad("comm_port.h", "文件不存在")
        return
    hc = strip_comments(h)
    m = re.search(r"#define\s+ECG_COMM_PORT\s+(ECG_COMM_PORT_\w+)", hc)
    if not m:
        bad("默认通道", "comm_port.h 里找不到 ECG_COMM_PORT 的默认定义")
    elif m.group(1) != "ECG_COMM_PORT_UART5":
        bad("默认通道", "默认是 %s，本轮上板要求 UART5" % m.group(1))
    else:
        ok("默认通道", "ECG_COMM_PORT = ECG_COMM_PORT_UART5")

    if re.search(r"#error", hc) and "ECG_COMM_PORT_USB(0)" in hc:
        ok("编译期断言", "非法宏值有 #error 拦截（已用 -DECG_COMM_PORT=7 实测触发）")
    else:
        bad("编译期断言", "comm_port.h 缺少 ECG_COMM_PORT 取值校验的 #error")

    # 2) HAL UART 模块必须打开
    conf = read("Core/Inc/stm32l4xx_hal_conf.h")
    if conf is None:
        bad("hal_conf", "文件不存在")
    elif re.search(r"^\s*#define\s+HAL_UART_MODULE_ENABLED", conf, re.M):
        ok("HAL UART 模块", "已启用")
    else:
        bad("HAL UART 模块", "HAL_UART_MODULE_ENABLED 仍被注释 ⇒ 链接期必炸")

    # 3) EIDE 工程清单必须含三个新文件（srcDirs: [] 是逐条枚举，不是扫目录）
    y = read("EWARM/demo_ads1293/.eide/eide.yml")
    if y is None:
        bad("eide.yml", "文件不存在")
    else:
        for k in ("Core/Src/comm_port.c",
                  "stm32l4xx_hal_uart.c",
                  "stm32l4xx_hal_uart_ex.c"):
            n = y.count(k)
            if n == 1:
                ok("eide.yml", "%s ×1" % k)
            else:
                bad("eide.yml", "%s 出现 %d 次（应为 1）" % (k, n))

    # 4) ads1293.c：活码里 CDC 调用必须为 0、comm_send 必须为 3
    a = read("Ads1293/ads1293.c")
    if a is None:
        bad("ads1293.c", "文件不存在")
    else:
        la = strip_comments(live_text(a))
        if len(la) >= len(a):
            bad("死码剥离", "live_text 没有比原文短 ⇒ 求值器没生效，第 4 条判据空跑")
        else:
            ok("死码剥离", "%d → %d 字节（剥掉 #if 0 段）" % (len(a), len(la)))
        ncdc = len(re.findall(r"\bCDC_Transmit_FS\s*\(", la))
        ncs = len(re.findall(r"\bcomm_send\s*\(", la))
        if ncdc == 0:
            ok("ads1293 活码", "CDC_Transmit_FS 0 处")
        else:
            bad("ads1293 活码", "仍有 %d 处活码 CDC_Transmit_FS" % ncdc)
        if ncs == 3:
            ok("ads1293 活码", "comm_send 3 处（快照 / ASCII 档 / 二进制主通路）")
        else:
            bad("ads1293 活码", "comm_send 出现 %d 处（应为 3）" % ncs)
        # 死码里的 2 处必须还在（证明我们没顺手删历史代码）
        ndead = len(re.findall(r"\bCDC_Transmit_FS\s*\(", strip_comments(a))) - ncdc
        if ndead == 2:
            ok("ads1293 死码", "#if 0 里保留 2 处（未顺手删历史代码）")
        else:
            bad("ads1293 死码", "#if 0 里剩 %d 处（应为 2）" % ndead)

    # 5) demo.c：`printf_msg` 走 comm_send，且 UART 模式下不得有 HAL_Delay(5)
    d = read("Core/Src/demo.c")
    if d is None:
        bad("demo.c", "文件不存在")
    else:
        # 🛑 必须走 live_text：`printf_msg` 里那段旧的 CDC 调用被 `#if 0` 包着
        #    （首版是"重试 3 次"的写法），只剥注释不剥死码会报假 FAIL —— 本轮实踩。
        dc = strip_comments(live_text(d))
        if len(re.findall(r"\bCDC_Transmit_FS\s*\(", dc)) == 0:
            ok("demo.c", "CDC_Transmit_FS 0 处")
        else:
            bad("demo.c", "仍有活码 CDC_Transmit_FS")
        if len(re.findall(r"\bcomm_send\s*\(", dc)) == 1:
            ok("demo.c", "comm_send 1 处（printf_msg）")
        else:
            bad("demo.c", "comm_send 出现 %d 处（应为 1）" % len(re.findall(r"\bcomm_send\s*\(", dc)))
        # HAL_Delay(5) 必须被 ECG_COMM_PORT_USB 的 #if 包住
        seg = d[d.find("comm_send"):]
        seg = seg[:seg.find("#endif")] if "#endif" in seg else seg
        if "ECG_COMM_PORT_USB" in seg and "HAL_Delay" in seg:
            ok("demo.c", "HAL_Delay(5) 已被 USB 分支包住（UART 模式无 5 ms 阻塞）")
        else:
            bad("demo.c", "HAL_Delay(5) 未被 ECG_COMM_PORT_USB 包住 ⇒ UART 模式会被回显拖垮")

    # 6) main.c：USB 初始化必须被条件包住；comm_init 必须在 demo 之前
    m1 = read("Core/Src/main.c")
    if m1 is None:
        bad("main.c", "文件不存在")
    else:
        if re.search(r"#if\s*\(ECG_COMM_PORT\s*==\s*ECG_COMM_PORT_USB\)\s*\n\s*MX_USB_DEVICE_Init\(\)",
                     m1):
            ok("main.c", "MX_USB_DEVICE_Init 被 ECG_COMM_PORT_USB 包住")
        else:
            bad("main.c", "MX_USB_DEVICE_Init 未被条件包住 ⇒ UART5 模式下会与 USB 对驱同一网络")
        nusb = len(re.findall(r"\bMX_USB_DEVICE_Init\s*\(\s*\)\s*;", strip_comments(m1)))
        if nusb == 1:
            ok("main.c", "MX_USB_DEVICE_Init 调用点 1 处")
        else:
            bad("main.c", "MX_USB_DEVICE_Init 调用点 %d 处（应为 1）" % nusb)
        ic = m1.find("comm_init()")
        idm = m1.find("demo_ads1293()")
        if 0 <= ic < idm:
            ok("main.c", "comm_init() 在 demo_ads1293() 之前")
        else:
            bad("main.c", "comm_init() 缺失或晚于 demo_ads1293()（晚于则 printf_msg 无通道）")

    # 7) stm32l4xx_it.c：UART5 中断必须接上 HAL
    it = read("Core/Src/stm32l4xx_it.c")
    if it is None:
        bad("it.c", "文件不存在")
    elif re.search(r"void\s+UART5_IRQHandler\s*\(\s*void\s*\)\s*\{[^}]*HAL_UART_IRQHandler\s*\(\s*&huart5\s*\)",
                   strip_comments(it), re.S):
        ok("it.c", "UART5_IRQHandler → HAL_UART_IRQHandler(&huart5)")
    else:
        bad("it.c", "UART5_IRQHandler 未接 HAL ⇒ 中断落到 Default_Handler 死循环")

    # 8) comm_port.c：忙判必须在拷贝之前
    c = read("Core/Src/comm_port.c")
    if c is None:
        bad("comm_port.c", "文件不存在")
    else:
        cc = strip_comments(c)
        i_busy = cc.find("if (s_tx_busy != 0u)")
        i_copy = cc.find("s_tx_buf[i] = buf[i]")
        if 0 <= i_busy < i_copy:
            ok("comm_port.c", "忙判在拷贝之前（顺序正确）")
        else:
            bad("comm_port.c", "忙判不在拷贝之前 ⇒ 会把在途缓冲覆盖掉一半再判忙")
        if cc.count("HAL_UARTEx_ReceiveToIdle_IT") == 2:
            ok("comm_port.c", "ReceiveToIdle 挂载 2 处（初挂 + 回调内重挂）")
        else:
            bad("comm_port.c", "ReceiveToIdle 出现 %d 次（应为 2）" % cc.count("HAL_UARTEx_ReceiveToIdle_IT"))
        # 中断优先级必须低于 DRDY（EXTI1 = 0）
        mp = re.search(r"HAL_NVIC_SetPriority\s*\(\s*UART5_IRQn\s*,\s*(\d+)", cc)
        if mp and int(mp.group(1)) > 0:
            ok("comm_port.c", "UART5 抢占优先级 %s > DRDY 的 0" % mp.group(1))
        else:
            bad("comm_port.c", "UART5 优先级未设或与 DRDY 同级 ⇒ 会推迟采样响应")

    # 9) 行尾纯 LF
    for rel in ("Core/Src/comm_port.c", "Core/Inc/comm_port.h"):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            b = open(p, "rb").read()
            if b.count(b"\r\n") == 0:
                ok("行尾", "%s 纯 LF" % rel)
            else:
                bad("行尾", "%s 含 %d 个 CRLF" % (rel, b.count(b"\r\n")))


# ---------------------------------------------------------------------------
# 阴性对照：故意改坏一处，判据必须报 FAIL
# ---------------------------------------------------------------------------
def selftest():
    """真跑一遍判据：把 comm_port.c 换成"忙判在拷贝之后"的坏版本，第 8 条必须报 FAIL。

    🛑 不"只打印计划"——技能 §4.115 的教训：dry-run 必须真跑断言逻辑，
       否则风险最高那一步（判据到底有没有判别力）根本看不见。
    """
    global read, FAILS, OKS
    print("=" * 70)
    print("阴性对照：comm_port.c 的忙判挪到拷贝之后 —— '忙判在拷贝之前' 必须报 FAIL")
    print("=" * 70)

    real_read = read
    real = real_read("Core/Src/comm_port.c")
    if real is None:
        print("  ✗ comm_port.c 不存在，无法自检")
        return False

    busy_blk = ("    if (s_tx_busy != 0u) {\n"
                "        comm_tx_drops++;\n"
                "        return 1u;\n"
                "    }\n")
    copy_blk = ("    for (uint16_t i = 0u; i < len; i++) {\n"
                "        s_tx_buf[i] = buf[i];\n"
                "    }\n")
    good = busy_blk + "\n" + copy_blk
    swapped = copy_blk + "\n" + busy_blk
    if good not in real:
        print("  ✗ 锚点没命中（源码结构变了），自检作废 —— 请更新 selftest 锚点")
        return False
    broken = real.replace(good, swapped, 1)

    def fake_read(rel):
        if rel == "Core/Src/comm_port.c":
            return broken
        return real_read(rel)

    read = fake_read
    FAILS.clear()
    OKS.clear()
    check_all()
    read = real_read

    hit = [m for t, m in FAILS if t == "comm_port.c"]
    if any("忙判不在拷贝之前" in m for m in hit):
        print("  ✓ 判据生效：坏版本被抓到 ——", hit[0])
        print("  ⇒ 说明第 8 条不是装饰。")
        return True
    print("  ✗ 坏版本没被抓到 ⇒ 第 8 条是装饰，判据无效")
    for t, m in FAILS:
        print("     FAIL %-14s %s" % (t, m))
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return 0 if selftest() else 4

    check_all()

    print("=" * 70)
    for t, m in OKS:
        print("  [OK]   %-14s %s" % (t, m))
    print("-" * 70)
    for t, m in FAILS:
        print("  [FAIL] %-14s %s" % (t, m))
    print("=" * 70)
    print("通过 %d 条 / 失败 %d 条" % (len(OKS), len(FAILS)))
    if len(OKS) == 0:
        print("🛑 一条都没通过 = 判据空跑（路径或求值器有问题），不算成功")
        return 4
    return 0 if not FAILS else 3


if __name__ == "__main__":
    sys.exit(main())
