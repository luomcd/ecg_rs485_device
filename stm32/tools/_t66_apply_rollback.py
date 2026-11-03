# -*- coding: utf-8 -*-
"""T66 · 按档分离落码：IMP620 / IMP620_N2 的 BP 回退 fc250 → fc150

依据（T66 取证，数据＝四份 215252）
----
1. fc250 在 **≤150 Hz 增益差 = 0.00 dB**（逐点相同）⇒ 对输入阻抗测试**零收益**。
2. 它把 **155~426 Hz 放行 3.5~1241×** ⇒ 620 kΩ 接入时把输入阻抗读数推高：
   853 域真 raw 离线重放，**1 s 窗 p-p 中位比值 99.36%(fc150) → 105.51%(fc250)**，
   p95 口径 **102.53% → 111.15%**（越 110% 线）。
3. IMP620 / IMP620_N2 自注「**专用于 620 kΩ∥4.7 nF 输入阻抗测试，勿用于监护**」
   ⇒ 它**不需要** 250 Hz 带宽；D1/D2/D3（诊断档）才需要（150 Hz 频响 ≥70%）。

⇒ **按档分离**：D1/D2/D3 保留 fc250（BP 3 节）；IMP620 / IMP620_N2 回 fc150（BP 4 节）。

顺带修复
----
`ECG_NUM_BP_SECTIONS` 由**全局 3** 改为**按档定义** ⇒ ST40（NOTCH 3 + BP 4 = 7）
的 `#error` 自动消失。D1/D2/D3 的 BP 节数仍为 3（值不变）。

用法
----
    python _t66_apply_rollback.py --dry     # 只看将改什么，不写盘
    python _t66_apply_rollback.py --apply   # 备份后写盘
"""
import io
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..'))
F_C = os.path.join(ROOT, 'Filter', 'ecg_filter.c')
F_H = os.path.join(ROOT, 'Filter', 'ecg_filter_coeffs.h')
BAK = os.path.join(HERE, '_bak_T66_20260920')

OUT = []


def say(s=''):
    print(s)
    OUT.append(s)


# --------------------------------------------------------------- 新文本

# f32：LP4@150 两节（首节 b0≠1 ⇒ 非归一化）+ HP4@0.05 两节
BP_F32_150 = """    /* 第 {i}~{j} 节：0.05~150 Hz Butterworth 带通（总 8 阶拆 4 biquad，与 D2/D3 逐位相同）
       ★T66(2026-09-20)：本档＝**输入阻抗测试专用**，BP 回退 fc 250→150（LP2@250 → LP4@150）。
       依据：fc250 在 ≤150 Hz 增益差＝0（对输入阻抗测试零收益），却把 155~426 Hz
       放行 3.5~1241×；620 kΩ 接入时 1 s 窗 p-p 中位比值 99.4%（fc150）→ 105.5%（fc250）。
       见 `tools/T66_620k_fc代价与按档分离_2026-09-20.md`。 */
    {{ 0.030899521f,  0.0617990419f, 0.030899521f, -0.493050277f, 0.0958790183f }},
    {{ 1.0f,          2.0f,          1.0f,         -0.670181155f, 0.490696579f  }},
    {{ 1.0f,         -2.0f,          1.0f,         -1.99931943f,  0.999319613f  }},
    {{ 1.0f,         -2.0f,          1.0f,         -1.99971819f,  0.999718368f  }}"""

BP_F64_150 = """    {{ 0.030899520236696225, 0.06179904047339245, 0.030899520236696225,
      -0.49305028725906408, 0.095879018845577341 }}, /* BP sec0 ({i}-{j} 之首, 0.05-150) */
    {{ 1, 2, 1, -0.67018117413271905, 0.49069657728421018 }},   /* BP sec1 */
    {{ 1, -2, 1, -1.9993194733745707, 0.99931960903409622 }},   /* BP sec2 */
    {{ 1, -2, 1, -1.9997182191996452, 0.99971835477155957 }}    /* BP sec3 */"""


def patch_c(text):
    """在 IMP620 / IMP620_N2 两档内把 BP 段由 LP2@250 换回 LP4@150。"""
    log = []

    for prof, nnotch in (('IMP620', 5), ('IMP620_N2', 9)):
        m = re.search(r'(#elif \(ECG_FILTER_PROFILE == ECG_PROFILE_%s\))(.*?)(?=\n#(?:elif|else))'
                      % prof, text, re.S)
        if not m:
            raise RuntimeError('未找到档 %s' % prof)
        body = m.group(2)
        new = body

        # ---- f32 段
        pat32 = re.compile(
            r' *?/\* ★T58\(2026-09-20\).*?\*/\s*\n'
            r'\s*\{ 0\.37672776f,.*?\}\s*,\s*\n'
            r'\s*\{ 1\.0f,\s*-2\.0f,.*?\}\s*,\s*\n'
            r'\s*\{ 1\.0f,\s*-2\.0f,.*?\}\s*\n', re.S)
        rep32 = BP_F32_150.format(i=nnotch, j=nnotch + 3) + '\n'
        new, c32 = pat32.subn(rep32, new, count=1)
        if c32 != 1:
            raise RuntimeError('%s f32 段替换次数 %d（应为 1）' % (prof, c32))
        log.append('%s f32：LP2@250(1 节) → LP4@150(2 节)' % prof)

        # ---- f64 段
        pat64 = re.compile(
            r'\s*\{ 0\.37672776742306063,[^{}]*?\}\s*,\s*/\* LP2@250.*?\*/\s*\n'
            r'\s*\{ 1, -2, 1, -1\.9993194733745707, 0\.99931960903409622 \}\s*,\s*/\*.*?\*/\s*\n'
            r'\s*\{ 1, -2, 1, -1\.9997182191996452, 0\.99971835477155957 \}\s*/\*.*?\*/',
            re.S)
        rep64 = BP_F64_150.format(i=nnotch, j=nnotch + 3)
        new, c64 = pat64.subn(rep64, new, count=1)
        if c64 != 1:
            raise RuntimeError('%s f64 段替换次数 %d（应为 1）' % (prof, c64))
        log.append('%s f64：LP2@250(1 节) → LP4@150(2 节)' % prof)

        # ---- 档注释里的上角描述与节数
        old_head = new[:600]
        hh = old_head
        hh = hh.replace('（12 节）', '（13 节）')
        hh = hh.replace('（8 节）', '（9 节）')
        hh = hh.replace('0.05~250Hz', '0.05~150Hz')
        if hh != old_head:
            new = hh + new[600:]
            log.append('%s 档头注释：上角 250→150 Hz ＋ 节数同步' % prof)

        text = text[:m.start(2)] + new + text[m.end(2):]

    return text, log


VMAP = {
    'DIAG150': (4, 1, 3),
    'DIAG150_D2': (5, 2, 3),
    'DIAG150_D3': (7, 4, 3),
    'IMP620': (9, 5, 4),
    'IMP620_N2': (13, 9, 4),
    'ST40': (7, 3, 4),
}

NAME = {
    'DIAG150': 'D1 diag: 0.05-250Hz + notch50 (4 sections)',
    'DIAG150_D2': 'D2 diag: 0.05-250Hz + notch50/100 (5 sections)',
    'DIAG150_D3': 'D3 diag: 0.05-250Hz + notch49.9/50.0/50.1(Q20)+100(Q15) (7 sections)',
    'IMP620': 'IMP620: 0.05-150Hz + notch49.8/50.0/50.2(Q12)+100(Q15)+150(Q30) (9 sections)',
    'IMP620_N2': 'IMP620_N2: 0.05-150Hz + notch50x3(Q12)+100x3(Q20)+150x3(Q20) (13 sections)',
    'ST40': 'v3 ST/monitor: 0.05-40Hz + notch50/100/150 (7 sections)',
}


def patch_h(text):
    log = []

    # 1) 去掉全局 BP 宏（改为按档定义）
    old = '#define ECG_NUM_BP_SECTIONS 3\n'
    if old not in text:
        raise RuntimeError('未找到全局 ECG_NUM_BP_SECTIONS 3')
    text = text.replace(old, '/* ECG_NUM_BP_SECTIONS 改为**按档定义**（见下方各档分支）；'
                             'T66(2026-09-20) 按档分离所需。 */\n')
    log.append('删除全局 `ECG_NUM_BP_SECTIONS 3`（改按档定义）')

    # 2) 各档分支：注入 BP 定义 + 改 SECTIONS / NAME
    for prof, (nsec, nnotch, nbp) in VMAP.items():
        m = re.search(r'(#(?:if|elif) \(ECG_FILTER_PROFILE == ECG_PROFILE_%s\)\n)'
                      r'#define ECG_NUM_SECTIONS (\d+)\n'
                      r'#define ECG_NUM_NOTCH_SECTIONS (\d+)\n'
                      r'#define ECG_PROFILE_NAME "([^"]*)"'
                      % prof, text)
        if not m:
            raise RuntimeError('未找到档分支 %s' % prof)
        new = ('%s#define ECG_NUM_SECTIONS %d\n'
               '#define ECG_NUM_NOTCH_SECTIONS %d\n'
               '#define ECG_NUM_BP_SECTIONS %d\n'
               '#define ECG_PROFILE_NAME "%s"'
               % (m.group(1), nsec, nnotch, nbp, NAME[prof]))
        if (int(m.group(2)), int(m.group(3))) != (nsec, nnotch) or m.group(4) != NAME[prof]:
            log.append('%s：SECTIONS %s→%d ｜ NOTCH %s ｜ **BP=%d（新增）** ｜ NAME 同步'
                       % (prof, m.group(2), nsec, m.group(3), nbp))
        text = text[:m.start()] + new + text[m.end():]

    # 3) 链结构注释：BP 节数由固定 3 改为按档
    text = text.replace('*      [ 陷波节 × ECG_NUM_NOTCH_SECTIONS ]  ->  [ BP 节 × 3 ]',
                        '*      [ 陷波节 × ECG_NUM_NOTCH_SECTIONS ]  ->  [ BP 节 × ECG_NUM_BP_SECTIONS ]')
    text = text.replace(
        ' *   ST40       [ 7] = 3 陷波 (50/100/150 Q30)              + 4 BP(0.05~40)   ← 本轮不动\n'
        ' *   DIAG150    [ 4] = 1 陷波 (50 Q30)                      + 3 BP(0.05~250)\n'
        ' *   DIAG150_D2 [ 5] = 2 陷波 (50/100 Q30)                  + 3 BP(0.05~250)\n'
        ' *   DIAG150_D3 [ 7] = 4 陷波 (49.9/50/50.1 Q20 + 100 Q15)  + 3 BP(0.05~250)\n'
        ' *   IMP620     [ 8] = 5 陷波 (50簇×3 Q12 + 100 Q15 + 150 Q30) + 3 BP(0.05~250)\n'
        ' *   IMP620_N2  [12] = 9 陷波 (50簇×3 + 100簇×3 + 150簇×3)   + 3 BP(0.05~250)',
        ' *   ST40       [ 7] = 3 陷波 (50/100/150 Q30)              + 4 BP(0.05~40)   ← 本轮不动\n'
        ' *   DIAG150    [ 4] = 1 陷波 (50 Q30)                      + 3 BP(0.05~250)\n'
        ' *   DIAG150_D2 [ 5] = 2 陷波 (50/100 Q30)                  + 3 BP(0.05~250)\n'
        ' *   DIAG150_D3 [ 7] = 4 陷波 (49.9/50/50.1 Q20 + 100 Q15)  + 3 BP(0.05~250)\n'
        ' *   IMP620     [ 9] = 5 陷波 (50簇×3 Q12 + 100 Q15 + 150 Q30) + 4 BP(0.05~150) ← T66 回退\n'
        ' *   IMP620_N2  [13] = 9 陷波 (50簇×3 + 100簇×3 + 150簇×3)   + 4 BP(0.05~150) ← T66 回退')
    log.append('链结构注释：BP 节数按档化 ＋ 两档核对表更新')

    # 4) T58 说明段后追加 T66 段
    anchor = (' *   依据：150 Hz 频响 54.76% ✗（表 201.107 第 3 行下限 70%）＋过冲由 LP 阶数定。\n'
              ' *   改后：150 Hz → 75.64% ✓（余量 +5.64 pp）。见 `tools/T58_*.md`。\n')
    if anchor not in text:
        raise RuntimeError('未找到 T58 说明锚点')
    text = text.replace(anchor, anchor + (
        ' *\n'
        ' * ★ T66(2026-09-20) **按档分离**：上面的 fc250 只保留在 D1/D2/D3（诊断档，\n'
        ' *   `ECG_NUM_BP_SECTIONS=3`）；**IMP620 / IMP620_N2 回 LP4@150（BP=4 节，9/13 节）**。\n'
        ' *   依据：fc250 在 ≤150 Hz 增益差＝0（对输入阻抗测试零收益），却放行 155~426 Hz\n'
        ' *   3.5~1241× ⇒ 620 kΩ 接入时 1 s 窗 p-p 中位比值 99.4%→105.5%（p95 102.5%→111.2% ✗）。\n'
        ' *   副作用为正：ST40 的 `NOTCH 3 + BP 4 = 7` 自洽 ⇒ 原 `#error` 自动消失。\n'))
    log.append('新增 T66 按档分离说明段')

    return text, log


def main():
    apply_ = '--apply' in sys.argv
    say('T66 按档分离落码 ｜ 模式＝%s' % ('APPLY（写盘）' if apply_ else 'DRY（只看）'))

    c = io.open(F_C, encoding='utf-8').read()
    h = io.open(F_H, encoding='utf-8').read()
    n0c, n0h = len(c.splitlines()), len(h.splitlines())

    c2, logc = patch_c(c)
    h2, logh = patch_h(h)

    say()
    say('改动清单：')
    for s in logc + logh:
        say('  · %s' % s)

    # ---------------- 自检（写盘前）
    say()
    say('自检：')
    # C：IMP620/N2 段内不得再有 LP2@250；D 档必须保留 6 处
    def sec_of(t, prof):
        m = re.search(r'#(?:if|elif) \(ECG_FILTER_PROFILE == ECG_PROFILE_%s\)(.*?)(?=\n#(?:elif|else))'
                      % prof, t, re.S)
        return m.group(1) if m else ''

    n_imp = sec_of(c2, 'IMP620').count('0.3767277') + sec_of(c2, 'IMP620_N2').count('0.3767277')
    say('  ① IMP620/N2 段残留 LP2@250 系数：%d 处（应为 0）%s'
        % (n_imp, '✓' if n_imp == 0 else '✗'))
    n_d = c2.count('0.37672776f') + c2.count('0.37672776742306063')
    say('  ② D 档保留 LP2@250 系数：%d 处（应为 12 ＝ 3 档 × (f32×2 + f64×2)）%s'
        % (n_d, '✓' if n_d == 12 else '✗'))
    # f32 表元素数
    for prof, n, nnotch in (('IMP620', 9, 5), ('IMP620_N2', 13, 9),
                            ('DIAG150', 4, 1), ('DIAG150_D2', 5, 2),
                            ('DIAG150_D3', 7, 4), ('ST40', 7, 3)):
        m = re.search(r'#(?:if|elif) \(ECG_FILTER_PROFILE == ECG_PROFILE_%s\)(.*?)(?=\n#(?:elif|else))'
                      % prof, c2, re.S)
        body = m.group(1)
        t32 = re.search(r'F32\[[^\]]*\]\s*=\s*\{(.*?)\n\};', body, re.S).group(1)
        t64 = re.search(r'F64\[[^\]]*\]\s*=\s*\{(.*?)\n\};', body, re.S).group(1)
        k32 = len(re.findall(r'\{\s*[-0-9]', re.sub(r'/\*.*?\*/', '', t32, flags=re.S)))
        k64 = len(re.findall(r'\{\s*[-0-9]', re.sub(r'/\*.*?\*/', '', t64, flags=re.S)))
        ok = k32 == n and k64 == n
        say('  ③ %-10s f32 %2d / f64 %2d 元素（应 %d）%s'
            % (prof, k32, k64, n, '✓' if ok else '✗'))
    # H：宏自洽
    for prof, (nsec, nnotch, nbp) in VMAP.items():
        ok = (nnotch + nbp == nsec)
        say('  ④ %-10s NOTCH %d + BP %d = %d ｜ SECTIONS %d %s'
            % (prof, nnotch, nbp, nnotch + nbp, nsec, '✓' if ok else '✗'))

    say()
    say('行数：ecg_filter.c %d → %d ｜ coeffs.h %d → %d'
        % (n0c, len(c2.splitlines()), n0h, len(h2.splitlines())))

    if not apply_:
        say()
        say('（DRY：未写盘。加 --apply 执行）')
    else:
        if not os.path.isdir(BAK):
            os.makedirs(BAK)
        shutil.copy2(F_C, os.path.join(BAK, 'ecg_filter.c.bak'))
        shutil.copy2(F_H, os.path.join(BAK, 'ecg_filter_coeffs.h.bak'))
        io.open(F_C, 'w', encoding='utf-8', newline='\n').write(c2)
        io.open(F_H, 'w', encoding='utf-8', newline='\n').write(h2)
        say()
        say('已写盘；备份 → %s' % os.path.relpath(BAK, ROOT))

    io.open(os.path.join(HERE, '_t66_apply_out.txt'), 'w', encoding='utf-8').write('\n'.join(OUT))


if __name__ == '__main__':
    main()
