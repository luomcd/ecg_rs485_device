# -*- coding: utf-8 -*-
"""T85 收尾：把 MEMORY.md 从 3058 压回 ≤3000（只压措辞/删重复交叉引用，不删任何数值）。
每处断言 count==1；改完自检字符数。"""
import io
P = '.workbuddy/memory/MEMORY.md'
s = io.open(P, encoding='utf-8').read()
before = len(s)


def rep(old, new, tag, expect=1):
    global s
    n = s.count(old)
    assert n == expect, '%s: count=%d (expect %d)' % (tag, n, expect)
    s = s.replace(old, new)
    print('  %-14s %+5d' % (tag, len(new) - len(old)))


# ---------- §1 措辞 ----------
rep('★**陷波中心禁用arccos**', '★陷波中心**禁用arccos**', 'L8 措辞')
rep('列②量程＝**24bit无符号[0,16777215]**；⑤须两位小数。',
    '列②量程＝**24bit无符号[0,16777215]**；⑤两位小数。', 'L10 措辞')

# ---------- §6 维护 ----------
rep(r'IAR＝**`C:\iar\ewarm-9.70.2`**。', r'IAR＝`C:\iar\ewarm-9.70.2`。', 'L17 IAR去粗')
rep(' ST40 `#error` 已修 ⇒ 已迁 ', ' ST40 `#error`已修 ⇒ 迁 ', 'L22 句缩')

# ---------- §8 三条长条：删与 §S2/§S4 重复的交叉引用 + 措辞 ----------
rep('**滑窗驱动量(T62)**', '**滑窗驱动量**', 'L29 去T号')
rep('- ★★★**滑窗驱动量', '- ★★**滑窗驱动量', 'L29 降星')
rep('区间**无特殊性**；', '区间无特殊性；', 'L29 去粗')
rep('；各档见`SETTLED §S2`。', '。', 'L27 去交叉引用')
rep('- ★★★**达标两事**', '- ★★**达标两事**', 'L28 降星')
rep('，间距0.087%)', ')', 'L30 去重复数')
rep('（A+ 退基线对照；', '（A+作对照；', 'L30 措辞')

# ---------- §9 交叉引用 / 空格 ----------
rep('「p-p比≤1.10」**仅ON**。', '「p-p比≤1.10」仅ON。', 'L35 去粗')
rep('（或 `DUAL_A`/`A+`/`A+B` 的 OFF 态，逐点同）｜输入阻抗→**`A+B`(默认)**／`IMP620_N2`（见§8）。',
    '（或`DUAL_A`/`A+`/`A+B` 的OFF态，逐点同）｜输入阻抗→**`A+B`(默认)**／`IMP620_N2`。',
    'L37 去空格与§8')
rep('列③/④恒0。坑：切回off必', '列③/④恒0。切回off必', 'L36 去字')
rep('⇒**只认探针字符串**。详见`SETTLED §S2`。', '⇒**只认探针字符串**。', 'L38 去交叉引用')

io.open(P, 'w', encoding='utf-8').write(s)
print('\nMEMORY.md：%d → %d 字符（限 3000，余 %d）' % (before, len(s), 3000 - len(s)))
assert len(s) <= 3000, '仍超限！'
print('✅ 合规')
