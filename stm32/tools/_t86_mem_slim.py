#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T86 · MEMORY.md 第二轮压缩（把 -27 的赤字抹平并留余量）。纯措辞层，不动结论。"""

import io

P = '.workbuddy/memory/MEMORY.md'
s = io.open(P, encoding='utf-8').read()
n0 = len(s)

R = [
    ('d越大越重，100ms 达5%', 'd大则重，100ms 达5%', 'A1 占空比'),
    ('**悄悄撤掉一条铁律**(T86 实证)。', '**悄悄撤掉一条铁律**(T86)。', 'A2 去实证'),
    ('**净传递＝(平台−脉后稳态)/源**。详`SETTLED §S7`。', '**净传递＝(平台−脉后稳态)/源**。详`§S7`。', 'A1 指针'),
    ('**标准要求频响/脉冲/CMRR/过载关陷波、噪声开陷波**⇒开关必需。',
     '**标准：频响/脉冲/CMRR/过载须关陷波、噪声须开**⇒开关必需。', 'S2c 标准句'),
    ('→**`D3`**（或`DUAL_A`/`A+`/`A+B` 的OFF态，逐点同）｜输入阻抗→**`A+B`(默认)**／`IMP620_N2`。',
     '→**`D3`**(或 `DUAL_A`/`A+`/`A+B` 的OFF态，逐点同)｜输入阻抗→**`A+B`(默认)**／`N2`。', 'S3d 选档'),
    ('(各档节数/上角·`ECG_NUM_BP_SECTIONS`按档', '(节数/上角·`ECG_NUM_BP_SECTIONS`按档', 'S1d 指针段'),
    ('(喂编译器的是其派生副本`builder.params`)', '(喂编译器的是派生副本`builder.params`)', 'S3e eide'),
    ('**`test raw853 on|off`**(默认**off**)：853每样本直接入队', '**`test raw853 on|off`**(默认**off**)：每样本直接入队', 'S3f raw853'),
]

bad = [(t, s.count(o)) for o, _, t in R if s.count(o) != 1]
assert not bad, '未命中/多处命中：%s' % bad
for o, n, t in R:
    s = s.replace(o, n)
    print('  %-14s %+4d' % (t, len(n) - len(o)))
io.open(P, 'w', encoding='utf-8', newline='\n').write(s)
print('MEMORY.md：%d → %d 字符（限 3000，余 %d）' % (n0, len(s), 3000 - len(s)))
assert len(s) <= 3000, '仍超限'
print('✅ 合规')
