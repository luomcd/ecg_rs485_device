# -*- coding: utf-8 -*-
"""T85：往 MEMORY.md 加两条铁律，按「置换制」先压后加（每处断言 count==1）。"""
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


# ---------------- 先压（退役/压缩，腾配额）
rep('- 输入阻抗（判据/口径/达标/620k瞬态）· 工频干扰三条结论 ⇒ **已迁至 `SETTLED.md §S1 / §S3`**（逐字保留）。',
    '- 输入阻抗·工频干扰三条结论 ⇒ 已迁 `SETTLED.md §S1/§S3`。', 'S1 结案区')
rep('，非`.ewp`**（09-09缺2源⇒`iarbuild`假Li005）⇒`tools/_fw_build.py`。',
    '，非`.ewp`**⇒`tools/_fw_build.py`。', 'S2 构建')
rep('- 各档节数/上角 · `ECG_NUM_BP_SECTIONS` 按档定义 · 宏名打错静默 · ST40 `#error` 已修 ⇒ **已迁至 `SETTLED.md §S2`**。',
    '- 各档节数/上角 · `ECG_NUM_BP_SECTIONS` 按档 · 宏名打错静默 · ST40 `#error` 已修 ⇒ 已迁 `SETTLED.md §S2`。',
    'S3 档位段')
rep('100µV＝3mV的3.3%；间隔**≥10s**。🛑**口径未钉死＝6.8×**；🛑**脉冲必off**。',
    '🛑**口径未钉死＝6.8×**；🛑**脉冲必off**。', 'S4 判据冗余')
rep('（dc_block 独占，`ECG_DC_BLOCK_FC_HZ_F`=0.02）', '(dc独占，按档宏 **0.02**)', 'S5 低端按档')
rep('★fc250令σ6.95→8.83。', '', 'S6 σ细节')
rep('／`A+`(18节) ON**105.76✓/p95 109.93✓** ⇒', '／A+ ON**105.76✓/109.93✓** ⇒', 'S7 A+读数')
rep('(⚠下游fs必改,否则1.7065×)；列③/④恒0；⚠上位机按500⇒853只看txt。',
    '(⚠fs必改,否则1.7065×)；列③/④恒0。', 'S8 raw853冗余')
rep('🛑ASCII853⇒**行撕裂**(坏行`len!=7`)⇒用`ascii-0`。', '🛑ASCII853⇒**行撕裂**(坏行见§1)⇒用`ascii-0`。', 'S9 指向新条')
rep('**切档唯一点＝`coeffs.h`的 `ECG_FILTER_PROFILE` 默认宏**(入口`tools/_set_profile.py`)；',
    '切档唯一点＝`coeffs.h` 的 `ECG_FILTER_PROFILE` 默认宏(`tools/_set_profile.py`)；', 'S10 切档点')

# ---------------- 再加（新铁律）
rep('- ⚠**列⑤=code**(`%.02f`;×0.1122⇒µV,**别当µV**,差8.912×)。',
    '- ⚠**列⑤=code**(`%.02f`;×0.1122⇒µV,**别当µV**,差8.912×)。\n'
    '- 🛑**读txt两条坏行判据**：①列数≠7 ②**逐列量程**(`%07d`只保**最小**宽度⇒串口丢/多字符时'
    ' token 数仍＝7，**只查列数必漏判**)。列②量程＝**24bit无符号[0,16777215]**；⑤须两位小数。',
    'A1 loader铁律')
rep('**AFE才是主要限制**(150Hz−2.22dB)。详`SETTLED §S4~S6`。',
    '**AFE才是主要限制**(150Hz−2.22dB)。\n'
    '- ★★★**a1 必单脉冲/间隔≥10s**：2s周期下两链**周期均值都必须为0**(HP硬约束)⇒尾**面积**恒'
    '`−A·w`(**与Σf无关**)⇒**拿 2s 数据读「脉冲后基线」会把优劣读反**(实测尾终点 A+✓／A+B✗，'
    '而尾起点 −349／−197) ⇒ 判别量在**尾起点 ∝Σf**，不在终点。详`SETTLED §S4~S6`。', 'A2 a1铁律')

io.open(P, 'w', encoding='utf-8').write(s)
print('\nMEMORY.md：%d → %d 字符（限 3000，余 %d）' % (before, len(s), 3000 - len(s)))
