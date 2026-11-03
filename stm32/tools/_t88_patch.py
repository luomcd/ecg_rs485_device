#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T88 补丁 · 给 _t85_lowfreq_square.py 加「时钟列自适应」

起因：固件把列① 改成**字面 0**（`"%d ...", 0 /* idxraw */`，见 Ads1293/ads1293.c:679~681），
      于是 `row_rate = FS*n/(c1[-1]-c1[0])` 除零崩溃、丢行判据也全废。
处置：列① 一旦恒为常数 ⇒ 自动换用**列③ idxfilter（500 Hz 域，正常 Δ=1）**做时钟列。
      ★两条判据都保留：本份实测两类坏行各 1 处（8 token 粘连 + 丢 21 行）。

每处替换断言 count==1 ⇒ 幂等安全。
"""
import io

P = 'tools/_t85_lowfreq_square.py'
s = io.open(P, encoding='utf-8').read()
n0 = len(s)

OLD_A = """    d1 = np.diff(c1).astype(int)
    u, cu = np.unique(d1, return_counts=True)
    # ★行率必须用「总跨度/总行数」求，不能用增量中位（增量是 1/2 混合，中位=2 ⇒ 得 426 Hz 的错值）
    row_rate = FS * n / float(c1[-1] - c1[0])
    p('  列① 增量分布 %s ｜ 中位 %.0f ｜ **行率 = FS×行数/(列①跨度) = %.1f Hz**'
      % (dict(zip(u.tolist()[:6], cu.tolist()[:6])), np.median(d1), row_rate))
    p('     （列①是 DRDY 计数：1/2 混合 ⇒ 中位口径会得 426.6 Hz 的错值；'
      '本份最大增量 %d ⇒ 有主循环没跟上、丢 raw 样本）' % int(d1.max()))
"""

NEW_A = """    # ★T88 时钟列自适应：列① 一旦被固件置成**字面 0**（`"%d ...", 0 /* idxraw */`），
    #   就毫无判据力 ⇒ 自动换用**列③ idxfilter（500 Hz 域，正常 Δ=1）**。
    #   ★源码注释自己写明：列① 在 **500 模式**增量 1~2 混合、**本就不能**当丢包判据
    #     （只有 raw853 模式 Δ 恒 1 才是首选判据）⇒ 500 模式下换列③ 是**零损失**的。
    #   🛑 反面：**`raw853 on` 时列③ 恒 0** ⇒ 那时两条判据**同时失效**（见 [0] 末提示）。
    clk = clk_fs = clk_nm = clk_max = None
    for cv, cfs, cnm, cmax in ((c1, FS, '列①', 5), (c3, FS_T, '列③', 3)):
        if float(cv.max()) - float(cv.min()) > 0:
            clk, clk_fs, clk_nm, clk_max = cv, cfs, cnm, cmax
            break
    if clk is None:
        clk_nm, row_rate, dclk = '无(全常数)', float('nan'), np.zeros(0, dtype=int)
        p('  🛑 列①/列③ **恒为常数** ⇒ 无任何时钟列 ⇒ 行率/丢行**不可判**（时间轴只能按标称 500 Hz 假定）')
    else:
        span = float(clk[-1] - clk[0])
        row_rate = clk_fs * n / span
        dclk = np.diff(clk).astype(int)
        u, cu = np.unique(dclk, return_counts=True)
        p('  时钟列 = **%s**（%s 域）｜ 跨度 %.0f ｜ **行率 = %.0f × %d / %.0f = %.1f Hz**'
          % (clk_nm, 'DRDY 853' if clk_nm == '列①' else 'idxfilter 500', span, clk_fs, n, span, row_rate))
        p('    增量分布 %s ｜ 中位 %.0f ｜ 单调不减 %s ｜ 增量 ≤0 或 >%g 处 %d'
          % (dict(zip(u.tolist()[:6], cu.tolist()[:6])), np.median(dclk),
             '✓' if bool(np.all(dclk >= 0)) else '✗',
             clk_max, int(np.sum((dclk <= 0) | (dclk > clk_max)))))
        p('    ⚠ 行率**只能用总跨度**求，不能用增量中位（列① 1/2 混合 ⇒ 中位=2 ⇒ 得 426.6 Hz 的错值）')
        if clk_nm == '列③':
            p('    ⚠ 本份**列① 恒 0**（固件已置字面 0）⇒ 判据已自动换列③；'
              '🛑 `raw853 on` 时列③ 恒 0 ⇒ 两条判据**同时失效**，那是唯一会「看不见丢行」的工况')
"""

OLD_B = """    p('  ★丢行/撕裂检查（T85 新立）：列③ 增量≠1 处 %d 个（%s）｜列① 增量 ≤0 或 >5 处 %d 个'
      % (int(np.sum(d3 != 1)), np.unique(d3[d3 != 1])[:6].tolist(),
         int(np.sum((d1 <= 0) | (d1 > 5)))))
    p('     （列① 是 DRDY 计数 ⇒ 必须**单调不减**；增量 ≤0 = 该列被串口截断过，'
      '增量 >5 = 主循环没跟上丢 raw 样本）')
"""

NEW_B = """    p('  ★丢行/撕裂检查（T85 新立 / T88 自适应）：列③ 增量≠1 处 %d 个（%s）'
      '｜时钟列 %s 增量 ≤0 或 >%g 处 %d 个'
      % (int(np.sum(d3 != 1)), np.unique(d3[d3 != 1])[:8].tolist(), clk_nm, clk_max,
         int(np.sum((dclk <= 0) | (dclk > clk_max))) if clk is not None else -1))
    p('     （时钟列必须**单调不减**；增量 ≤0 = 该列被串口截断过，增量超限 = 主循环没跟上丢样本）')
"""

OLD_C = "int((np.median(dk) - dk[odd[0]]) / np.median(d1))))"
NEW_C = "int((np.median(dk) - dk[odd[0]]) / (np.median(dclk) if clk is not None else 1))))"

REPS = [(OLD_A, NEW_A, '[0] 时钟列自适应'),
        (OLD_B, NEW_B, '[2] 丢行判据换列'),
        (OLD_C, NEW_C, '[2] 丢行行数换算')]

bad = [(t, s.count(o)) for o, _, t in REPS if s.count(o) != 1]
assert not bad, '未命中：%s' % bad
for o, nn, t in REPS:
    s = s.replace(o, nn)
    print('  %-20s %+5d 字符' % (t, len(nn) - len(o)))

io.open(P, 'w', encoding='utf-8', newline='\n').write(s)
print('_t85_lowfreq_square.py：%d → %d 字符' % (n0, len(s)))
