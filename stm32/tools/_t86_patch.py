#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T86 补丁：把 _t85_lowfreq_square.py 的三处缺陷修掉 + 新增两个口径。

① loader 坏行判据**实现与规矩不符**：T56 立的规矩是「列数 ≠ 7」，代码却只写了 `len(q) < 7`
   ⇒ **token 数 > 7 的行被静默接受**。T86 实证：本份第 2267 行＝**两行粘连成 11 token**
   （`… -1400.71 20 1258405 -1028 …`）⇒ 列⑥ 被读成 **20**（真值 1979）、列① 少 1 行跨度。
   危害：任何用「列⑥ 中位」判档的脚本，若该行占比升高就会漂；用「列① 增量」判丢行的会误判。
   ⇒ 改为 `len(q) != 7`。（第 ③ 条坏行判据）

② [6] 周期判据**笔误**：`abs(per - 10.0) < 0` 恒为假 ⇒ **永远打「✅ 周期 ≥10 s」**
   ⇒ 与 T85 刚立的「2 s 周期禁区」铁律**直接矛盾**。（本份 2.00 s 竟被判 a1 可用。）

③ [3] 传递口径**缺占空比修正**：周期脉冲经直流阻断链，平台期要分摊基线期的负偏置
   ⇒ 平台 ≈ +A(1−d)|H|、基线 ≈ −A·d|H|（d ＝占空比）⇒ 「平台比 col⑤/col②」**天然被挤掉 ≈ d**。
   本份 d=5% ⇒ 平台比 95.54% 被误读成「链路损耗 −4.39 pp」，其实链路几乎无损。
   正确口径＝**峰—基线峰差**：(平台 − 脉后稳态)/源幅度 ⇒ 占空比与直流落点自动抵消。

附带：make_fig 取不到 args（tag/fig_out）⇒ 传参；[4] 注释从「24 ms」泛化为「窄脉冲(<30 ms)」。
每处断言 count==1，幂等安全。
"""

import io

P = 'tools/_t85_lowfreq_square.py'
s = io.open(P, encoding='utf-8').read()
n0 = len(s)

R = []

# ---------- ① loader：列数 != 7（含 >7） ----------
R.append((
    "        if len(q) < 7:\n"
    "            if q:\n"
    "                bad_rc += 1\n"
    "            continue\n",
    "        if len(q) != 7:   # ★T86：不只 <7 —— **>7 同样坏**（两行粘连/字段移位，T86 实证）\n"
    "            if q:\n"
    "                bad_rc += 1\n"
    "            continue\n",
    '① loader 列数!=7'))

# ---------- ② [0] 坏行说明措辞 ----------
R.append((
    "    p('  坏行：列数≠7 计 %d ｜ **列宽/量级异常计 %d**（★串口丢/多字符 ⇒ token 数仍＝7，'\n"
    "      '只查列数抓不到）' % (bad, bad_w))\n",
    "    p('  坏行：列数≠7 计 %d（★**>7 也是坏行**：两行粘连/字段移位，T86 实证第 2267 行＝'\n"
    "      '11 token ⇒ 列⑥ 被读成 20）｜ **列宽/量级异常计 %d**（★串口丢/多字符 ⇒ token 数仍＝7，'\n"
    "      '只查列数抓不到）' % (bad, bad_w))\n",
    '② [0] 坏行措辞'))

# ---------- ③ [0] 列① 增量措辞：动态最大值 ----------
R.append((
    "    p('     （列①是 DRDY 计数：本文件 1/2 混合 ⇒ 中位口径会得 426.6 Hz 的错值；'\n"
    "      '增量出现 41 一次 = 有一次主循环没跟上 ⇒ 丢 raw 样本）')\n",
    "    p('     （列①是 DRDY 计数：1/2 混合 ⇒ 中位口径会得 426.6 Hz 的错值；'\n"
    "      '本份最大增量 %d ⇒ 有主循环没跟上、丢 raw 样本）' % int(d1.max()))\n",
    '③ [0] 增量措辞'))

# ---------- ④ [3] 新增「峰—基线峰差」口径（占空比修正） ----------
R.append((
    "    p('  ⇒ 平台比与设计差 %.2f pp ✓' % (100 * pl_y / pl_x - 100 * abs(Hs)))\n",
    "    p('  ⇒ 平台比与设计差 %.2f pp（🛑 **含占空比效应，不是链路损耗**，见下）'\n"
    "      % (100 * pl_y / pl_x - 100 * abs(Hs)))\n"
    "    # ★T86 新立：周期脉冲经直流阻断链 ⇒ 平台期必须分摊基线期的负偏置\n"
    "    #   平台 ≈ +A(1−d)|H| ／ 基线 ≈ −A·d|H| ／ 峰谷比 → (1−d)/d  （d ＝占空比）\n"
    "    #   ⇒ 「平台比 col⑤/col②」天然被挤掉 ≈ d（本份 d=5% ⇒ −4.39 pp 里 5.0 pp 是它）\n"
    "    #   ⇒ **正确净传递口径 ＝ 峰—基线峰差**：(平台 − 脉后稳态)/源幅度（d 与直流落点自动抵消）\n"
    "    b_line = float(np.median(y))\n"
    "    d_cyc = w2[0.5] / per\n"
    "    pb = pl_y - b_line\n"
    "    p('  ★★占空比 d = %.4f（%.0f ms / %.0f ms）⇒ 周期脉冲的**必然**落点：平台 ≈ +A(1−d)|H| = %.1f'\n"
    "      '（实测 %.1f）／基线 ≈ −A·d|H| = %.1f（实测 %.1f）｜峰谷比 %.2f vs (1−d)/d = %.2f'\n"
    "      % (d_cyc, 1000 * w2[0.5], 1000 * per, pl_x * (1 - d_cyc) * abs(Hs), pl_y,\n"
    "         -pl_x * d_cyc * abs(Hs), b_line, pl_y / abs(b_line), (1 - d_cyc) / d_cyc))\n"
    "    p('  ★★峰—基线峰差口径（T86 修正 · **不受占空比影响**的净传递）：'\n"
    "      '(%.1f − (%.1f)) = %.1f µV / 源 %.1f ⇒ **%.2f%%** vs 设计 %.2f%% ⇒ 差 %+.2f pp'\n"
    "      % (pl_y, b_line, pb, amp2, 100 * pb / amp2, 100 * abs(Hs), 100 * pb / amp2 - 100 * abs(Hs)))\n",
    '④ [3] 峰—基线峰差口径'))

# ---------- ⑤ [4] 注释泛化 ----------
R.append((
    "    p('[4] 脉冲后尾（★原始值跨脉冲中位，不用包络 —— 24 ms 窄脉冲下 LP40 包络有 ±10%% Gibbs）')\n",
    "    p('[4] 脉冲后尾（★原始值跨脉冲中位，不用包络 —— 窄脉冲(<30 ms)下 LP40 包络有 ±10%% Gibbs）')\n",
    '⑤ [4] 注释泛化'))

# ---------- ⑥ [6] 周期禁区：补「尾面积守恒」解释 ----------
R.append((
    "        p('  🛑 周期 %.2f s < 10 s ⇒ 脉冲前/后基线窗重合 ⇒ a1 循环定义（T81）⇒ 只能读「形状/尾」，不能读 a1 数值。' % per)\n",
    "        p('  🛑 周期 %.2f s < 10 s ⇒ 脉冲前/后基线窗重合 ⇒ a1 循环定义（T81）⇒ 只能读「形状/尾」，'\n"
    "          '不能读 a1 数值。' % per)\n"
    "        p('      ★T85/T86 定理：周期脉冲下两链的**周期均值都必须为 0**（HP 硬约束）⇒ 尾**面积**恒为'\n"
    "          ' −A·w、**与 Σf 无关** ⇒ 判别量在**尾起点深度 ∝ Σf**。')\n"
    "        p('      拿 2 s 数据读「脉冲后稳态基线」会把 A+/A+B 的优劣**读反**（A+ 尾终点反而更小 ——'\n"
    "          '因为它回归更快、但下冲峰更深）。本份的**合格用途**：档位自证／传递／同输入换链。')\n",
    '⑥ [6] 尾面积守恒解释'))

# ---------- ⑦ make_fig 传参 ----------
R.append((
    "def make_fig(name, t, x, y, kpk, yenv, secs, rsecs, dc, ref_dc, pname, rname, PHS, MY, envs, f0, tag='T85'):\n",
    "def make_fig(name, t, x, y, kpk, yenv, secs, rsecs, dc, ref_dc, pname, rname, PHS, MY, envs, f0,\n"
    "             tag='T85', fig_out=None):\n",
    '⑦ make_fig 签名'))
R.append((
    "make_fig(name, t, x, y, kpk, yenv, secs, rsecs, dc, ref_dc, a.prof, a.ref, PHS, MY, envs, a.f0, a.tag)",
    "make_fig(name, t, x, y, kpk, yenv, secs, rsecs, dc, ref_dc, a.prof, a.ref, PHS, MY, envs, a.f0,\n"
    "                 a.tag, a.fig_out)",
    '⑦ make_fig 调用'))
R.append((
    "    o = a.fig_out or os.path.join(HERE, '%s_低频方波判读_2026-09-21.png' % a.tag)",
    "    o = fig_out or os.path.join(HERE, '%s_低频方波判读_2026-09-21.png' % tag)",
    '⑦ 图输出路径'))

bad = [(t, s.count(o)) for o, _, t in R if s.count(o) != 1]
assert not bad, '未命中/多处命中：%s' % bad
for o, n, t in R:
    s = s.replace(o, n)
    print('  %-24s %+5d' % (t, len(n) - len(o)))
io.open(P, 'w', encoding='utf-8', newline='\n').write(s)
print('%s：%d → %d 字符' % (P, n0, len(s)))
