# -*- coding: utf-8 -*-
"""
_t58_verify_struct.py —— 编译前结构门禁（防「数组元素数 ≠ ECG_NUM_SECTIONS 静默补零」）

为什么必须做
------------
C 里 `const T a[N] = { ... };` 若初始化元素**少于 N**，GCC **不报错**，而是补 0
（= b0=b1=b2=a1=a2=0 的"空节"）⇒ 链尾多出一节恒零滤波 ⇒ **静默行为改变**。
节数改动的场景下这是最容易犯的错，所以必须**显式数元素**。

检查项
------
  1. 各档 f32/f64 数组的元素数 == 该档 `ECG_NUM_SECTIONS`
  2. 三宏自洽：`NOTCH + BP == SECTIONS`（Python 侧先查一遍，编译期 #error 是第二道）
  3. 各档 BP 段结构（**按档**判定，T66 起多套并存，T84 起再加一套）：
       BP=1 ⇒ [1×LP2@250]                                     （A+B：低端 HP 整段交给 dc_block，T84）
       BP=3 ⇒ [1×LP2@250] + [2×HP4@0.05]                      （D1/D2/D3/DUAL_A/DUAL_A_PLUS 诊断档）
       BP=4 & 非 ST40 ⇒ [2×LP4@150] + [2×HP4@0.05]            （IMP620/N2 输入阻抗档，T66 回退）
       BP=4 & ST40   ⇒ [2×LP4@40 等价 4 节]（本轮不动）

★ 2026-09-20 T66 修订（原版盲区）
--------------------------------
旧版把 `EXPECT = [(档, 陷波, BP, 总节)]` **硬编码**。T58 改档数后面 ST40 的
`3+3≠7` 矛盾**门禁测不出来**（它自己写的就是 ST40 BP=4，而宏是全局 3）⇒ 编译期
`#error` 才暴露。现改为**从 `ecg_filter_coeffs.h` 各档分支直接读宏**，硬编码消失。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
C_FILE = os.path.join(HERE, '..', 'Filter', 'ecg_filter.c')
H_FILE = os.path.join(HERE, '..', 'Filter', 'ecg_filter_coeffs.h')

LP2_250 = {
    'f32': (0.37672776, 0.75345552, 0.37672776, 0.317465425, 0.18944566),
    'f64': (0.37672776742306063, 0.75345553484612127, 0.37672776742306063,
            0.31746541217881613, 0.18944565751342665),
}
LP4_150 = {
    'f32': ((0.030899521, 0.0617990419, 0.030899521, -0.493050277, 0.0958790183),
            (1.0, 2.0, 1.0, -0.670181155, 0.490696579)),
    'f64': ((0.030899520236696225, 0.06179904047339245, 0.030899520236696225,
             -0.49305028725906408, 0.095879018845577341),
            (1, 2, 1, -0.67018117413271905, 0.49069657728421018)),
}
LP4_40_ST40 = {
    'f32': ((0.0003275869, 0.0006551738, 0.0003275869, -1.51014405, 0.577817187),
            (1.0, 2.0, 1.0, -1.72297496, 0.800463821)),
    'f64': None,      # f64 从文件读，容差比对（ST40 未改，作弱校验）
}


def read_macros(hdr):
    """从 coeffs.h 各档分支读 (SECTIONS, NOTCH, BP)，保持文件顺序。

    ★ T84(2026-09-21) 放宽：分支头与第一个 `#define` 之间**允许夹注释行**
      （A+B 分支就带一段说明）。原版要求 `#elif (...)\\n` 紧跟 `#define ECG_NUM_SECTIONS`，
      加注释会让该档**整支匹配不到** ⇒ 宏表少一项，后面「顺序对齐」全错位。
    """
    out = []
    for m in re.finditer(
            r'#(?:if|elif) \(ECG_FILTER_PROFILE == ECG_PROFILE_(\w+)\)\n'
            r'(?:[^\n]*\n)*?'
            r'#define ECG_NUM_SECTIONS (\d+)\n'
            r'#define ECG_NUM_NOTCH_SECTIONS (\d+)\n'
            r'#define ECG_NUM_BP_SECTIONS (\d+)', hdr):
        out.append((m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))))
    return out


def parse_cells(body):
    """把数组正文拆成每个 { } 元素的 5 个数。"""
    out = []
    for m in re.finditer(r"\{([^{}]*)\}", body):
        nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", m.group(1))
        out.append([float(x) for x in nums[:5]])
    return out


def close(a, b, tol=1e-9):
    return max(abs(a[i] - b[i]) for i in range(5)) <= tol


def main():
    src = io.open(C_FILE, encoding='utf-8').read()
    hdr = io.open(H_FILE, encoding='utf-8').read()

    macros = read_macros(hdr)
    print("coeffs.h 档位宏（顺序即 ecg_filter.c 中各数组顺序）：")
    ok = True
    for nm, ns, nn, nb in macros:
        good = (nn + nb == ns)
        ok &= good
        print("  %-11s SECTIONS %2d = NOTCH %d + BP %d  %s"
              % (nm, ns, nn, nb, '✓' if good else '**✗ 三宏不自洽**'))

    pat = re.compile(
        r"const ecg_biquad_coeff_(f32|f64) ECG_FILTER_COEFFS_\w+"
        r"\[ECG_NUM_SECTIONS\] = \{(.*?)\n\};", re.S)
    blocks = [(m.group(1), parse_cells(m.group(2))) for m in pat.finditer(src)]
    print()
    print("找到 %d 个系数数组（期望 %d = %d 档 × f32/f64）"
          % (len(blocks), 2 * len(macros), len(macros)))
    if len(blocks) != 2 * len(macros):
        print("⇒ 数量不符，停")
        return 1

    for k, (name, ns, nn, nb) in enumerate(macros):
        for j, kind in enumerate(('f32', 'f64')):
            got_kind, cells = blocks[k * 2 + j]
            msgs = []
            if got_kind != kind:
                print("  !! 顺序错：第 %d 块应为 %s，实为 %s" % (k * 2 + j, kind, got_kind))
                ok = False
                continue
            n = len(cells)
            if n != ns:
                ok = False
                msgs.append("!! 元素数 %d ≠ SECTIONS %d" % (n, ns))
            bp = cells[nn:]
            if len(bp) != nb:
                ok = False
                msgs.append("!! BP 节数 %d ≠ BP 宏 %d" % (len(bp), nb))

            if nb == 1:
                # ★ T84 新增：A+B 档 —— BP 只剩 1 节 LP2@250，下角 HP 交给 dc_block。
                #   必须显式判 BP[0]，否则"删表删成只剩一节且节选错"会静默通过。
                tgt = LP2_250[kind]
                d = max(abs(bp[0][i] - tgt[i]) for i in range(5))
                if d > 1e-6:
                    ok = False
                    msgs.append("!! BP[0] ≠ LP2@250 (max|Δ|=%.2g)" % d)
                else:
                    msgs.append("BP[0]=LP2@250 ✓（HP 由 dc_block 承担）")
            elif nb == 3:
                tgt = LP2_250[kind]
                d = max(abs(bp[0][i] - tgt[i]) for i in range(5))
                if d > 1e-6:
                    ok = False
                    msgs.append("!! BP[0] ≠ LP2@250 (max|Δ|=%.2g)" % d)
                else:
                    msgs.append("BP[0]=LP2@250 ✓")
                hp = (abs(bp[1][1] + 2) < 1e-9 and abs(bp[2][1] + 2) < 1e-9)
                msgs.append("BP[1..2]=HP4@0.05 ✓" if hp else "!! HP4 异常")
                if not hp:
                    ok = False
            elif name == 'ST40':
                msgs.append("BP 4 节(0.05~40, 本轮不动) 参照")
            else:
                exp = LP4_150[kind]
                d0 = max(abs(bp[0][i] - exp[0][i]) for i in range(5))
                d1 = max(abs(bp[1][i] - exp[1][i]) for i in range(5))
                if d0 > 1e-6 or d1 > 1e-6:
                    ok = False
                    msgs.append("!! BP[0..1] ≠ LP4@150 (max|Δ|=%.2g/%.2g)" % (d0, d1))
                else:
                    msgs.append("BP[0..1]=LP4@150 ✓")
                hp = abs(bp[2][1] + 2) < 1e-9 and abs(bp[3][1] + 2) < 1e-9
                msgs.append("BP[2..3]=HP4@0.05 ✓" if hp else "!! HP4 异常")
                if not hp:
                    ok = False

            print("  %-11s %s  陷波%2d + BP%2d = %2d 节  %s"
                  % (name, kind, nn, n - nn, n, ' ｜ '.join(msgs)))

    print()
    print("⇒ 结构门禁 %s" % ("通过 ✓" if ok else "**失败 ✗**"))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
