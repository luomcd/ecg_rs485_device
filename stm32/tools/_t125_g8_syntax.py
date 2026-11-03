# -*- coding: utf-8 -*-
"""
_t125_g8_syntax.py —— G8 语法门禁：arm-none-eabi-gcc `-Wall -Wextra` 告警集合对照
================================================================================

判据（沿用 T113/T115/T117/T120 的做法）：**新增告警 = 0**。
  · 以 **G7 定稿备份** 为基线，逐文件对照"告警消息集合"；
  · 行号漂移**不算新增**（所以规范化时剥掉 `file:line:col:` 前缀）；
  · 出现新增 ⇒ 打印原始 gcc 行（带行号），便于"精确到新增行"定位。

为什么用 arm-gcc 而不是只看 IAR：IAR 的 `--silent` 不保证把全部 Pa/Pe 告警打出来，
而 gcc `-Wall -Wextra` 是**独立第二意见**；两者都过才叫过。
⚠ 本机没有 x86 编译器（见记忆），只有 arm-none-eabi-gcc ⇒ 用 `-fsyntax-only` 不链接。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAK = os.path.join(ROOT, "tools", "_t125_bak")
GCC = r"C:\Nordic\Toolchian\gcc-arm-none-eabi-10-2020-q4-major\bin\arm-none-eabi-gcc"

INC = [
    "Core/Inc", "Drivers/STM32L4xx_HAL_Driver/Inc", "Drivers/STM32L4xx_HAL_Driver/Inc/Legacy",
    "Drivers/CMSIS/Device/ST/STM32L4xx/Include", "Drivers/CMSIS/Include",
    "USB_DEVICE/App", "USB_DEVICE/Target",
    "Middlewares/ST/STM32_USB_Device_Library/Core/Inc",
    "Middlewares/ST/STM32_USB_Device_Library/Class/CDC/Inc",
    "Ads1293", "Filter", "Drivers/CMSIS/DSP/Include",
]
CPU = ["-mcpu=cortex-m4", "-mfpu=fpv4-sp-d16", "-mfloat-abi=softfp", "-mthumb", "-std=gnu11"]
DEF = ["-DUSE_HAL_DRIVER", "-DSTM32L476xx"]


def run(src: str):
    cmd = [GCC, "-fsyntax-only", "-Wall", "-Wextra"] + CPU + DEF + \
          ["-I" + p for p in INC] + [src]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    raw = (r.stdout or "") + (r.stderr or "")
    msgs, raws = set(), []
    for line in raw.split("\n"):
        m = re.search(r":\d+:\d+:\s*(warning|error|note):\s*(.*)$", line)
        if m:
            msgs.add("%s: %s" % (m.group(1), m.group(2)))
            raws.append(line.strip())
    return msgs, raws


PAIRS = [("Ads1293/ads1293.c", "ads1293.c"), ("Core/Src/vcomcmd.c", "vcomcmd.c")]

ok = True
for cur_rel, bak_name in PAIRS:
    cur = os.path.join(ROOT, cur_rel.replace("/", os.sep))
    bak = os.path.join(BAK, bak_name)
    m_new, raw_new = run(cur)
    m_old, raw_old = run(bak)
    added = sorted(m_new - m_old)
    gone = sorted(m_old - m_new)
    print("=" * 74)
    print("%s" % cur_rel)
    print("=" * 74)
    print("  既有告警（G7/G8 共有）: %d 条" % len(m_new & m_old))
    print("  新增告警              : %d 条" % len(added))
    print("  消失告警              : %d 条" % len(gone))
    for a in added:
        print("    ✗ NEW  %s" % a[:110])
        for line in raw_new:
            if a.split(": ", 1)[1][:40] in line:
                print("           @ %s" % line[:130])
    for g in gone:
        print("    - GONE %s" % g[:110])
    if added:
        ok = False
    print("  ⇒ %s" % ("OK（新增 0 条）" if not added else "FAIL"))
    print()

print("=" * 74)
print("结果：%s" % ("语法门禁通过 ✓（新增告警 0 条）" if ok else "新增告警 ⇒ 必须逐条解释或修掉"))
print("=" * 74)
sys.exit(0 if ok else 1)
