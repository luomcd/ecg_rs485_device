# -*- coding: utf-8 -*-
"""T85 收尾之二：再压 ~10 字符，把余量做到 ≥25（纯措辞）。"""
import io
P = '.workbuddy/memory/MEMORY.md'
s = io.open(P, encoding='utf-8').read()
before = len(s)


def rep(old, new, tag):
    global s
    n = s.count(old)
    assert n == 1, '%s: count=%d' % (tag, n)
    s = s.replace(old, new)
    print('  %-12s %+5d' % (tag, len(new) - len(old)))


rep('切档只换系数/长度⇒**RAM/CPU零额外**', '切档只换系数⇒**RAM/CPU零额外**', '算力句')
rep('> ads1293_demo｜≤3000字符 · 开局必读 ｜ ★设备事实→ `DEVICE.md` ｜ ★**结案区→ `SETTLED.md`**',
    '> ads1293_demo｜≤3000字符·开局必读｜★设备事实→`DEVICE.md`｜★**结案区→`SETTLED.md`**', '抬头')
rep('宽度⇒串口丢/多字符时 token 数仍＝7', '宽度⇒串口丢/多字符时token数仍＝7', 'loader句')

io.open(P, 'w', encoding='utf-8').write(s)
print('\nMEMORY.md：%d → %d 字符（限 3000，余 %d）' % (before, len(s), 3000 - len(s)))
assert len(s) <= 3000
print('✅ 合规')
