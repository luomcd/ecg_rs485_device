# -*- coding: utf-8 -*-
"""T85 收尾之七：SETTLED 退役 §S1 的 T82 col② 待办条（该条属"未结案待办"，NOW.md ⑤ 已是真源）＋余量整理。"""
import io
P = '.workbuddy/memory/SETTLED.md'
s = io.open(P, encoding='utf-8').read()
before = len(s)


def rep(old, new, tag):
    global s
    n = s.count(old)
    assert n == 1, '%s: count=%d' % (tag, n)
    s = s.replace(old, new)
    print('  %-16s %+5d' % (tag, len(new) - len(old)))


# ★退役：未结案待办 ⇒ 真源在 NOW.md ⑤（T85 又补了 24bit 量程实证）
rep('- ⚠**T82：`col②`(%07d) 峰值已达上限 90.2%**（300mV⇔8.82e6码 ⇒ 上限≈340mV）⇒ **偏移再大即列错位、脚本丢行**，宜放宽至 `%08d`。\n',
    '', 'S1 退役T82待办')

rep('- ★★**档位探针＝`test notch`(无参)**⇒回显', '- ★★**档位探针＝`test notch`(无参)**⇒回显', 'noop')
rep('  ⇒ 🛑改档后两法复核：① `test notch` 看 `profile:`；② 系数段大小＝**节数×40 B**',
    '  ⇒🛑改档后两法复核：①`test notch` 看 `profile:`；②系数段＝**节数×40 B**', 'S2 复核句')
rep('σ23／σd15µV⇒rawmax 落 6~25σ ⇒「每采样点≤100」', 'σ23／σd15µV⇒rawmax 落6~25σ⇒「每采样点≤100」', 'S7 边界2')
rep('**4阶@200⇒中位门槛 85.1µV**(BP 3→4；🛑p95 无解)', '**4阶@200⇒门槛 85.1µV**(BP3→4；🛑p95 无解)', 'S4 四阶')
rep('## S7 判据 a 与口径纪律（T83 结案 · T85 复核）', '## S7 判据 a 与口径纪律（T83 · T85 结案）', 'S7 题')
rep('## S6 双态档族（DUAL_A / A+ / A+B）· 结构性质（结案）', '## S6 双态档族（DUAL_A/A+/A+B）· 结构性质（结案）', 'S6 题')

io.open(P, 'w', encoding='utf-8').write(s)
print('SETTLED.md：%d → %d（限 5000，余 %d）' % (before, len(s), 5000 - len(s)))
assert len(s) <= 5000
print('✅ 合规')
