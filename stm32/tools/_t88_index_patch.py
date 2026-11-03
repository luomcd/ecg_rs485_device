#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T88 · tools/INDEX.md 补丁（幂等；每处断言 count==1）

三处插入：① §1 报告清单 ② §2 脚本清单 ③ §4 日报 §↔T 对照
"""
import io, os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(HERE, 'INDEX.md')

REPORT_ANCHOR = '｜`滤波链演进全解读`｜T41~T45（09-18）'
SCRIPT_ANCHOR = '（出图名与标题可参数化）｜**`_mem_arch'

REPORT_ADD = (
 '｜**`T88_定版验收与参数汇总_2026-09-21.md` ＋ `T88_频响与尾段_2026-09-21.png` ＋ `T88_低频方波判读_2026-09-21.png`'
 '（★★★ **本轮主交付：A+B 定版验收与参数汇总**（🛑 **无源码改动**）：**素材** `3mv-0.5hz-100ms-Rectangle pulse-notchoff-2026-09-21 175515.txt`'
 '（**间隔 2 s → 10 s**）—— 25459 有效行／50.96 s／行率 **499.61 Hz**／周期 **10.0000 s**／脉宽 **100.0 ms**／幅度 **+3040.5 µV＝标称 101.4%**／notch off；'
 '**① 档位自证** `dtc` 中位 **1971 ⇒ 0.91 节 ⇒ A+B ✓**；'
 '**② ★★★ 频响指标（新方法＝谐波域实测传函）** —— 10 s 周期脉冲串频谱是 **0.1 Hz 整数倍线谱** ⇒ 逐周期对 col②/col⑤ 各做 DFT 求**复数比**'
 '＝ **真·频响实测**（**与源频谱无关**、**与「慢极点是否收敛」也无关**）：**0.1 Hz 98.45%** vs 设计 98.07%（**+0.38 pp**）／vs **A+ 89.33%（+9.12 pp）**；'
 '0.2/0.3/0.5/1/2 Hz 实测 99.87~100.37% vs 设计 99.51~100.00%；'
 '**③ ★★ 相位口径反解低端极点** `φ＝90°−atan(f/fc)`（⚠ 必须先扣**管线系统偏差**）⇒ **fc＝0.02000 Hz（±0.0003 Hz）**＝按档宏 `ECG_DC_BLOCK_FC_HZ_F=0.02f` **逐位相符**'
 '⇒ **读极点用相位、不用 τ**（τ 拟合 8.491 s、逐周期 6.76~10.92 ⇒ 散布 ±25% 不可定值）；'
 '**④ ★★★ 判据 a 三项首次全部实测通过** —— **a1 37.7 µV**（＝极点预算 Σf≤0.0531 的**预测值**；限 100 ⇒ 用量 **38%**）／**a2 6.4 µV·s⁻¹**（限 300 ⇒ **2.1%**）'
 '／**a3 4.85%**（限 10% ⇒ **48%**；A+ 为 6.7~8.7% 贴限）；'
 '**⑤ 时域净传递** 峰—基线峰差 **99.80%** vs 99.93%（平台 预测 2993.0／实测 3005.5；基线 −30.2／−29.1）；'
 '**⑥ 同输入换链** 尾段逐点差 **A+B 3.9 µV vs A+ 248.3 µV ＝ 64×**；'
 '**⑦ 结论＝A+B（16 节／dc@0.02 Hz）可以定版**；'
 '**⑧ 本轮新立 2 坑** —— 🛑**`raw853 on` 时丢行判据断链**（列① 已被固件置字面 0 ＋**列③ 在 raw853 恒 0** ⇒ **两条判据同时失效**，而 T70 频响清单／T64 620 kΩ 清单**都要求 `raw853 on`**；'
 '建议列① 改**模式自适应** `_braw853 ? idxraw : 0`）／★`col⑤` 出自 `ecg_restore_process()` 带 **50 mV/200 ms 伪迹空白**状态机 ⇒ 620 kΩ／大偏置实验须扫「长零段」（本轮实测 0 段）；'
 '**⑨ 修 3 处脚本缺陷**（列① 恒 0 ⇒ `row_rate` 除零崩溃 ⇒ 时钟列自适应／中文串裸 `%` ⇒ `%%` ／图内 `⇒`·`Σ` 字形缺失；'
 '备 `_bak_t85_script_pre_T88.py`）**）**'
)

SCRIPT_ADD = (
 '｜**`_t88_freqresp.py` — ★T88 谐波域频响＋尾段极点＋判据 a 读数统一入口**'
 '（`--file/--prof/--fig/--out`；[0] 身份与档位 [1] 周期网格 [2] **谐波域实测传函**（逐周期 col②/col⑤ DFT **复数比**）[2b] **相位反解 fc**（含「模型过同一管线」偏差校准）'
 '[3] 尾段单极点拟合（含残差 MSE 曲线留痕）[4] a1 多口径 [4b] a2/a3 [5] 定版判词；★`dft_ratio()` 逐周期取**中位**抗丢行；★窗取**整周期**否则泄漏）'
 '｜**`_t88_robust.py`**（稳健性：τ 逐周期估计／下落沿 2 ms 网格／谐波分周期一致性）'
 '｜**`_t88_patch.py`**（`_t85_lowfreq_square.py` 的「**时钟列自适应**」幂等补丁 —— 列① 恒 0 时自动换列③；修 `ZeroDivisionError`）'
 '｜**`_t88_mem_patch.py`**（记忆库补丁：MEMORY 置换 ＋ SETTLED 结案；★patch() **逐条顺序**校验防「假未命中」）'
)

DAILY_ADD = (
 '｜**§23 T88 定版验收与参数汇总（🛑 无源码改动；素材间隔 2 s→10 s）**：新方法 **谐波域实测传函** 0.1 Hz **98.45%** vs 设计 98.07%（A+ 89.33，**+9.12 pp**）；'
 '**相位反解 fc 0.02000 Hz（±0.0003）**＝按档宏；**a1 37.7 µV／a2 6.4 µV·s⁻¹／a3 4.85% 三项全 ✓**；净传递 99.80%；换链差 **3.9 vs 248.3 µV＝64×**；'
 '**结论 A+B 可定版**；🛑**新立「raw853 丢行判据断链」**；记忆归档 MEMORY **2989/3000**·SETTLED **4982/5000**；INDEX 续编号 → **T89**'
)


def ins_once(s, anchor, add, tag):
    assert s.count(anchor) == 1, '%s 锚点 %d 处' % (tag, s.count(anchor))
    return s.replace(anchor, add + anchor)


def main():
    src = io.open(P, encoding='utf-8').read()
    n0 = len(src)
    if 'T88_定版验收与参数汇总' in src:
        print('!! INDEX.md 已含 T88 条目，放弃（幂等保护）')
        return
    bak = os.path.join(HERE, '_bak_INDEX_pre_T88.md')
    shutil.copy2(P, bak)
    print('已备份 -> %s' % os.path.relpath(bak, os.path.dirname(HERE)))

    s = ins_once(src, REPORT_ANCHOR, REPORT_ADD, '报告清单')
    print('  报告条 +%d' % (len(REPORT_ADD),))
    s = ins_once(s, SCRIPT_ANCHOR, SCRIPT_ADD, '脚本清单')
    print('  脚本条 +%d' % (len(SCRIPT_ADD),))

    lines = s.split('\n')
    # §4：L58（index 57）以 '）' 结尾，插入点＝最后一个 '）' 之前
    i = 57
    l = lines[i]
    assert l.startswith('｜`2026-09-21.md`') and l.endswith('）'), '§4 第 %d 行形态变了：%r' % (i + 1, l[-20:])
    assert '§23' not in l
    lines[i] = l[:-1] + DAILY_ADD + '）'
    print('  §4 对照条 +%d（L%d）' % (len(DAILY_ADD), i + 1))
    s = '\n'.join(lines)

    io.open(P, 'w', encoding='utf-8', newline='\n').write(s)
    print('⇒ %d → %d 字符（%+d）' % (n0, len(s), len(s) - n0))


main()
