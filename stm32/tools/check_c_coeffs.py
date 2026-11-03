#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⛔ 已作废（2026-09-21，T72）—— 请勿使用，本文件不再做任何校验（故意的）。

作废原因：**四处都已过时，任一处都会给出错误结论**。
  1. **路径错** —— `C_FILE` 指向 `<仓库根>/ecg_filter.c`，实际在 `Filter/ecg_filter.c`
     ⇒ 运行直接输出「找不到」并以 1 退出。
  2. **分支结构错** —— 原解析假设 `#if D1 / #elif D2 / #else ST40` 三支；
     实际已是**九支**（末支是 `#elif ST40` 再 `#else #error`）⇒ 会取到错的分支。
  3. **链模型错** —— D1/D2 用 `butter(4,[0.05,150])`，那是 **T58 之前**的旧结构，
     现在两者都是 `LP2@250 + HP4@0.05 ×2` ⇒ 数值对照必然不符。
  4. **期望值硬编码** —— 原断言 `#define ECG_FILTER_PROFILE ECG_PROFILE_DIAG150_D2`，
     而默认档已多次变更（D2 → N2 → 现 D3）⇒ 会报**假失败**。

⇒ 处置：**保留本文件作墓碑**（避免有人照旧用法去跑），不做修复。
   系数值本身的逐位校验职责已由生成器承担（见下），不重复搬运。

现行门禁（五条，**全部动态读宏，无硬编码期望值**）：
  · 结构账 九档「陷波/BP 节数 ＋ 各节阶数与上角」  → python tools/_t58_verify_struct.py
  · 落码账 B 档「＝A+ 逐位删 2 行」＋ Σf 预算对账   → python tools/_t84_verify_land.py
  · 镜像账 段大小 / 逐字节 / 残留                  → python tools/_t58_verify_elf.py（由 _fw_build.py 自动调用）
  · 档位账 当前档位解析 ＋ 九档全表自检 ＋ 拼写陷阱 → python tools/_set_profile.py [--selftest]
  · 系数值 与 scipy 设计值同源                     → python tools/gen_diag_coeffs.py / gen_d3_coeffs.py / gen_f3_coeffs.py

原始实现（D1/D2/ST40 三支版，2026-09-10）的过程记录见 `.workbuddy/memory/archive/2026-09-10.md`。
"""
import sys

if __name__ == "__main__":
    print(__doc__.strip())
    sys.exit(1)
