# -*- coding: utf-8 -*-
"""
_t128_g10_syntax.py —— G10 语法门禁：arm-none-eabi-gcc `-Wall -Wextra` 告警集合对照
================================================================================

判据（沿用 T113/T115/T117/T120/T125 的做法）：**新增告警 = 0**。
  · 以 **上一版定稿备份**（`--bak`，默认 `tools/_t128_bak` = G8 上板版）为基线，
    逐文件对照"告警消息集合"；
  · 行号漂移**不算新增**（规范化时剥掉 `file:line:col:` 前缀）—— 本次三个文件都有
    净插入，行号必飘；
  · 出现新增 ⇒ 打印原始 gcc 行（带行号），便于"精确到新增行"定位。

为什么用 arm-gcc 而不是只看 IAR：IAR 的 `--silent` 不保证把全部 Pa/Pe 告警打出来，
而 gcc `-Wall -Wextra` 是**独立第二意见**；两者都过才叫过。
⚠ 本机没有 x86 编译器（见记忆），只有 arm-none-eabi-gcc ⇒ 用 `-fsyntax-only` 不链接。

用法::
  python tools/_t128_g10_syntax.py [--bak tools/_t128_bak]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bak", default="tools/_t128_bak",
                    help="基线备份目录（内含上一版定稿的同名源文件）")
    a = ap.parse_args()
    bak_dir = os.path.join(ROOT, a.bak.replace("/", os.sep))
    if not os.path.isdir(bak_dir):
        print("🛑 基线目录不存在：%s" % bak_dir)
        return 2

    pairs = [("Ads1293/ads1293.c", "ads1293.c"), ("Core/Src/vcomcmd.c", "vcomcmd.c")]
    ok = True
    for cur_rel, bak_name in pairs:
        cur = os.path.join(ROOT, cur_rel.replace("/", os.sep))
        bak = os.path.join(bak_dir, bak_name)
        m_new, raw_new = run(cur)
        m_old, raw_old = run(bak)
        added = sorted(m_new - m_old)
        gone = sorted(m_old - m_new)
        print("=" * 74)
        print("%s        （基线 %s）" % (cur_rel, bak_name))
        print("=" * 74)
        print("  新文件告警总数        : %d 条" % len(m_new))
        print("  基线告警总数          : %d 条" % len(m_old))
        print("  既有告警（两版共有）  : %d 条" % len(m_new & m_old))
        print("  新增告警              : %d 条" % len(added))
        print("  消失告警              : %d 条" % len(gone))
        for x in added:
            print("    ✗ NEW  %s" % x[:110])
            for line in raw_new:
                if x.split(": ", 1)[1][:40] in line:
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
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
