# -*- coding: utf-8 -*-
"""T84 · SETTLED.md 增量改 · 第 2 遍（把余量做到 ≥30）
------------------------------------------------------------------
第 1 遍后 5093/5000（超 93）。第 2 遍只做四件事，全部「零信息损失压缩」或
「同一数值换更短写法」，不退役任何独立结论；数值门槛（69.1→85.1 µV）与
「代价 BP 3→4 节」都保留。

用法：python tools/_t84_mem_settled2.py [--dry]
"""
import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SETTLED = os.path.join(os.path.dirname(HERE), '.workbuddy', 'memory', 'SETTLED.md')

EDITS = [
    # ① T84 条：判据 a 的预测值已在 §S6▶ 与 NOW 里有，这里只留「改了哪两处 + Σf 变化 + 节数」
    ("- ★★**T84 落码 A+B（投产）**：**A+ 逐位删 HP4 两节**＋**`dc_block` fc 提升为按档宏 "
     "`ECG_DC_BLOCK_FC_HZ_F`**（原硬编码于 `ads1293.c` ⇒ 档与 Σf 脱钩）⇒ "
     "**Σf 0.18069→0.02000（340.6%→37.7%）**、a1 预测 340.6→**37.7 µV**；"
     "**16 节／hex 89456 B**。",
     "- ★★**T84 落码 A+B（默认档）**：**A+ 逐位删 HP4 两节**＋**`dc_block` fc 提升为按档宏 "
     "`ECG_DC_BLOCK_FC_HZ_F`**（原硬编码于 `ads1293.c` ⇒ 档与 Σf 脱钩）⇒ "
     "**Σf 0.18069→0.02000（340.6%→37.7%）**、**16 节／hex 89456 B**。",
     "T84 条压缩"),

    # ② S5：去虚词
    ("但其 BP(LP4@150) 使频响 **54.8% ✗** ⇒ **两需求不在同一档**。",
     "但 BP(LP4@150) ⇒ 频响 **54.8% ✗** ⇒ **两需求不在同一档**。",
     "S5 去虚词"),

    # ③ S6：A/B 节数已在上文「九档」行写过一次
    ("**A**（删 HP4，16 节）稳健最差 sm100 **93.3✓**｜**B**（删 HP4＋dc@0.02，16 节）**46.6✓**⇒ **推荐 B**。",
     "**A**（删 HP4）稳健最差 sm100 **93.3✓**｜**B**（＋dc@0.02）**46.6✓**⇒ **选 B**。",
     "S6 去重复节数"),

    # ④ S4：4 阶@200 保留「门槛 69.1→85.1 µV」与「代价 BP 3→4」，去掉已可反推的 dB 明细
    ("- ★**4 阶@200 是\"免费改进\"**：150/200/250Hz 75.5%／↓2.1／↓5.1dB ⇒ 中位门槛 69.1→**85.1 µV**（源侧只需 −4.0 dB）；🛑p95 仍无解。代价 BP 3→4 节。",
     "- ★**4 阶@200 是\"免费改进\"**：中位门槛 69.1→**85.1 µV**（源侧只需 −4.0 dB）；🛑p95 仍无解。代价 BP 3→4 节。",
     "S4 缩 4阶@200（dB 明细见 T74 报告）"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()

    txt = io.open(SETTLED, encoding='utf-8').read()
    before = len(txt)
    print('SETTLED.md 改前：%d 字符（限 5000，余 %d）' % (before, 5000 - before))
    bad = 0
    for old, new, label in EDITS:
        n = txt.count(old)
        ok = (n == 1)
        bad += (not ok)
        print('  %s %-30s 命中 %d 次  Δ=%+d' % ('✓' if ok else '✗', label, n, len(new) - len(old)))
        if ok and not a.dry:
            txt = txt.replace(old, new)
    if bad:
        print('⇒ **有 %d 处没命中，未写盘**' % bad)
        return 1
    if a.dry:
        print('⇒ [dry] 未写盘（预计改后约 %d 字符）' % (before + sum(len(n) - len(o) for o, n, _ in EDITS)))
        return 0
    io.open(SETTLED, 'w', encoding='utf-8', newline='\n').write(txt)
    print('SETTLED.md 改后：%d 字符（余 %d）  Δ=%+d' % (len(txt), 5000 - len(txt), len(txt) - before))
    print('⇒ 已写盘')
    return 0


if __name__ == '__main__':
    sys.exit(main())
