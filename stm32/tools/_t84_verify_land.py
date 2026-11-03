# -*- coding: utf-8 -*-
"""T84 · A+B 档（T83 候选 B）落码自检 —— 从**源码文本**反解，验证「删 2 行」没删错、Σf 对得上
------------------------------------------------------------------------------------------
为什么需要这一层
----------------
B 的表**不是重新设计的**，而是「A+ 的表逐位照抄后删掉最后 2 行」。这类改动最容易犯的错是：
  ① 手抄时某一位数字敲错（16 行 × 5 数 × 2 精度 = 160 个数，肉眼抄必错）；
  ② 删错了行（把 LP2@250 删掉、留下 HP4 —— 上角就没人管了，而**编译不报错**）；
  ③ f32 表改了、f64 表忘了（或反之），两表不同源；
  ④ dc_block 那边 `0.02` 没跟上（只删了 HP4 但 dc 还是 0.05 ⇒ 变成未分析过的第 9 种组合）。

本脚本用**两条独立路径**对同一件事做交叉验证：
  A) 从 `Filter/ecg_filter.c` 解析出 A+ 与 B 的 f32/f64 四张表 ⇒ 逐位比较（不是近似）；
  B) 从 `Filter/ecg_filter_coeffs.h` 解析出 B 档的三个节数宏 ＋ `ECG_DC_BLOCK_FC_HZ_F`
     ⇒ 重算极点预算 Σf 与 a1 预测，与 T83 §9 的离线值对账。

判据（全部必须 ✓）
  A1  B 的 f32/f64 表各 16 行
  A2  B[0..15] 与 A+[0..15] **逐位相同**（bit-exact，非近似）
  A3  B[15] 是 LP（b=[b0,b1,b2] 非 [1,-2,1]）；A+[16]/A+[17] 是 HP（b=[1,-2,1]）
  A4  A+ 的第 16/17 行在 B 里**确实不存在**（不是被挪到了别处）
  B1  coeffs.h：SECTIONS 16 / NOTCH 15 / BP 1，且 15+1==16
  B2  coeffs.h：B 档 `ECG_DC_BLOCK_FC_HZ_F` == 0.02
  B3  Σf(B) = dc_pole(0.02) = 0.02000；Σf(A+) = dc_pole(0.05)+HP4两节 = 0.18069
  B4  预算占用：B ≈ 37.7%（余量 62%）｜A+ ≈ 341%（✗）
  B5  `Ads1293/ads1293.c` 里**不再有** `ecg_dc_block_init(&g_dc, 0.05f)` 硬编码，
      且确实引用了 `ECG_DC_BLOCK_FC_HZ_F`

用法
----
  python tools/_t84_verify_land.py            # 全量自检
  python tools/_t84_verify_land.py --quiet    # 只打印结论
"""
import argparse
import importlib.util
import io
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, '..'))
C_FILE = os.path.join(PROJ, 'Filter', 'ecg_filter.c')
H_FILE = os.path.join(PROJ, 'Filter', 'ecg_filter_coeffs.h')
ADS_C = os.path.join(PROJ, 'Ads1293', 'ads1293.c')

_spec = importlib.util.spec_from_file_location('t51', os.path.join(HERE, '_t51_yy0885_pulse.py'))
m = importlib.util.module_from_spec(_spec)
sys.modules['t51'] = m
_spec.loader.exec_module(m)
FS = m.FS

A_PLUS = 'ECG_PROFILE_DUAL_A_PLUS'
B_PLUS = 'ECG_PROFILE_DUAL_A_PLUS_B'
BUDGET = 0.1e-3 / (3.0e-3 * 0.10) / (2.0 * math.pi)   # 0.05306 Hz

OUT = []
FAIL = []


def p(s=''):
    print(s)
    OUT.append(s)


def chk(tag, cond, msg):
    ok = bool(cond)
    if not ok:
        FAIL.append('%s %s' % (tag, msg))
    p('  %s %-4s %s' % ('✓' if ok else '✗', tag, msg))
    return ok


def parse_f32(path_c, macro):
    """解析某档 ECG_FILTER_COEFFS_F32 表（_t51 只提供 f64 版，这里补 f32）。"""
    txt = io.open(path_c, encoding='utf-8').read()
    mo = re.search(r'#(?:if|elif)\s*\(ECG_FILTER_PROFILE\s*==\s*' + re.escape(macro) + r'\)', txt)
    if not mo:
        raise SystemExit('ecg_filter.c 里找不到档位分支: ' + macro)
    seg = txt[mo.end():]
    mt = re.search(r'const\s+ecg_biquad_coeff_f32\s+ECG_FILTER_COEFFS_F32\s*\[[^\]]*\]\s*=\s*\{', seg)
    if not mt:
        raise SystemExit('该分支找不到 ECG_FILTER_COEFFS_F32 表: ' + macro)
    body = seg[mt.end():]
    body = body[:body.index('};')]
    body = re.sub(r'/\*.*?\*/', '', body, flags=re.S)
    body = re.sub(r'//[^\n]*', '', body)
    rows = []
    for item in re.finditer(r'\{([^{}]*)\}', body):
        nums = [float(v) for v in re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', item.group(1))]
        if len(nums) != 5:
            raise SystemExit('f32 系数条目数字个数 != 5')
        rows.append(tuple(nums))
    return rows


def branch_macros(hdr, macro):
    """从 coeffs.h 的某档分支里读出节数宏 ＋ dc_block fc（原始字符串，保留精度）。"""
    mo = re.search(r'#(?:if|elif)\s*\(ECG_FILTER_PROFILE\s*==\s*' + re.escape(macro) + r'\)'
                   r'([\s\S]*?)(?=#(?:elif|else|endif))', hdr)
    if not mo:
        raise SystemExit('coeffs.h 里找不到档位分支: ' + macro)
    seg = mo.group(1)
    seg = re.sub(r'/\*.*?\*/', ' ', seg, flags=re.S)
    seg = re.sub(r'//[^\n]*', ' ', seg)
    out = {}
    for k in ('ECG_NUM_SECTIONS', 'ECG_NUM_NOTCH_SECTIONS', 'ECG_NUM_BP_SECTIONS'):
        mm = re.search(r'#define\s+' + k + r'\s+(\d+)', seg)
        out[k] = int(mm.group(1)) if mm else None
    mm = re.search(r'#define\s+ECG_DC_BLOCK_FC_HZ_F\s+([0-9.]+)f', seg)
    out['ECG_DC_BLOCK_FC_HZ_F'] = float(mm.group(1)) if mm else None
    return out


def pole_freqs(sec):
    """一节的极点等效频率（Hz）列表。"""
    a1, a2 = sec[3], sec[4]
    return [(-math.log(abs(r)) * FS / (2.0 * math.pi)) for r in np.roots([1.0, a1, a2])]


def dc_pole(fc):
    return -math.log(1.0 - 2.0 * math.pi * fc / FS) * FS / (2.0 * math.pi)


def is_hp(sec):
    """b = [1, -2, 1] 判定（HP4 节的特征；LP2 的 b 是 [0.3767, 0.7535, 0.3767]）。"""
    return abs(sec[0] - 1.0) < 1e-12 and abs(sec[1] + 2.0) < 1e-9 and abs(sec[2] - 1.0) < 1e-12


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()

    hdr = io.open(H_FILE, encoding='utf-8').read()

    p('=' * 108)
    p('T84 · A+B 档落码自检（源文本 → 逐位比对 + 极点预算对账）')
    p('=' * 108)

    # ---------------- A) 系数表逐位比对 ----------------
    p()
    p('【A】系数表：B 必须是「A+ 的前 16 行」，逐位相同')
    p('  解析路径：Filter/ecg_filter.c 的 #if/#elif 分支（f32 / f64 各两档）')
    tabs = {}
    for macro, tag in ((A_PLUS, 'A+'), (B_PLUS, 'B ')):
        tabs[tag, 'f64'] = list(m.parse_coeffs_f64(C_FILE, H_FILE, macro)[0])
        tabs[tag, 'f32'] = parse_f32(C_FILE, macro)
        p('  %s f64 %2d 行 ｜ f32 %2d 行' % (tag, len(tabs[tag, 'f64']), len(tabs[tag, 'f32'])))
    p()

    for kind in ('f64', 'f32'):
        A, B = tabs['A+', kind], tabs['B ', kind]
        chk('A1', len(B) == len(A) - 2 == 16,
            '%s：B 的 %d 行 == A+ 的 %d 行 - 2' % (kind, len(B), len(A)))
        diff = [(i, max(abs(B[i][k] - A[i][k]) for k in range(5)))
                for i in range(min(len(B), len(A)))]
        worst = max(d for _, d in diff)
        chk('A2', worst == 0.0,
            '%s：B[0..15] 与 A+[0..15] **逐位相同**（max|Δ| = %g）' % (kind, worst))
        chk('A3', (not is_hp(B[15])) and is_hp(A[16]) and is_hp(A[17]),
            '%s：B[15] 是 LP2@250（非 HP）；A+[16]/A+[17] 是 HP4' % kind)
        chk('A4', len(A) == 18 and not any(is_hp(r) for r in B),
            '%s：A+ 的 2 行 HP4 在 B 里确实不存在（B 中无 b=[1,-2,1] 节）' % kind)
        p()

    # ---------------- B) 宏 ＋ 极点预算 ----------------
    p('【B】coeffs.h 宏 ＋ 极点预算 Σf 对账（a1 ≈ A·T·2π·Σf）')
    mA = branch_macros(hdr, A_PLUS)
    mB = branch_macros(hdr, B_PLUS)
    for tag, mm in (('A+', mA), ('B ', mB)):
        p('  %s：SECTIONS %s = NOTCH %s + BP %s ｜ dc_block fc = %s Hz'
          % (tag, mm['ECG_NUM_SECTIONS'], mm['ECG_NUM_NOTCH_SECTIONS'],
             mm['ECG_NUM_BP_SECTIONS'], mm['ECG_DC_BLOCK_FC_HZ_F']))
    p()

    chk('B1', mB['ECG_NUM_SECTIONS'] == 16 and mB['ECG_NUM_NOTCH_SECTIONS'] == 15
        and mB['ECG_NUM_BP_SECTIONS'] == 1
        and mB['ECG_NUM_NOTCH_SECTIONS'] + mB['ECG_NUM_BP_SECTIONS'] == mB['ECG_NUM_SECTIONS'],
        'B 档三宏自洽：16 == 15 + 1')
    chk('B2', abs(mB['ECG_DC_BLOCK_FC_HZ_F'] - 0.02) < 1e-12,
        'B 档 ECG_DC_BLOCK_FC_HZ_F == 0.02（T83 §9 的落点）')
    chk('B2b', abs(mA['ECG_DC_BLOCK_FC_HZ_F'] - 0.05) < 1e-12,
        'A+ 档仍为 0.05（基线档未被污染 ⇒ 仍可作对照）')

    hp_rows = [r for r in tabs['A+', 'f64'][mA['ECG_NUM_NOTCH_SECTIONS']:] if is_hp(r)]
    sf_a = dc_pole(mA['ECG_DC_BLOCK_FC_HZ_F']) + sum(f for r in hp_rows for f in pole_freqs(r))
    sf_b = dc_pole(mB['ECG_DC_BLOCK_FC_HZ_F'])
    p()
    p('  Σf 预算 = X/(A·T·2π) = 0.1mV/(3mV × 0.1s × 2π) = %.5f Hz' % BUDGET)
    p('  %-6s %10s %10s %9s %9s %9s' % ('档', 'Σf(Hz)', '预算占用', '预测a1µV', '占限%', '余量%'))
    p('  ' + '-' * 62)
    for tag, sf in (('A+', sf_a), ('B', sf_b)):
        pred = 3000.0 * 0.10 * 2.0 * math.pi * sf
        p('  %-6s %10.5f %9.1f%% %9.1f %8.1f%% %8.1f%%'
          % (tag, sf, 100 * sf / BUDGET, pred, pred, 100 - pred))
    p()
    chk('B3', abs(sf_b - 0.02000) < 1e-4 and abs(sf_a - 0.18069) < 1e-4,
        'Σf：B = %.5f（期望 0.02000）｜A+ = %.5f（期望 0.18069）' % (sf_b, sf_a))
    chk('B4', 30.0 < 100 * sf_b / BUDGET < 45.0 and 100 * sf_a / BUDGET > 300.0,
        '预算占用：B = %.1f%%（期望 ~37.7，✓）｜A+ = %.1f%%（期望 ~341，✗）'
        % (100 * sf_b / BUDGET, 100 * sf_a / BUDGET))

    # ---------------- C) ads1293.c 不再硬编码 ----------------
    p()
    p('【C】Ads1293/ads1293.c：dc_block 初始化必须走按档宏')
    ads = io.open(ADS_C, encoding='utf-8').read()
    # ⚠ 先去注释再判：文件里保留了历史注释 `//ecg_dc_block_init(&g_dc, 0.5f);`，
    #   不过滤会把它当成"未删干净"（0.5f 会被 0\.0?5f? 这个宽 regex 命中 ⇒ 假失败）。
    ads_nc = re.sub(r'/\*.*?\*/', ' ', ads, flags=re.S)
    ads_nc = re.sub(r'//[^\n]*', ' ', ads_nc)
    mm = re.search(r'ecg_dc_block_init\(\s*&g_dc\s*,\s*([^)]*)\)', ads_nc)
    arg = mm.group(1).strip() if mm else None
    chk('B5a', arg == 'ECG_DC_BLOCK_FC_HZ_F',
        '唯一的 dc_block 初始化调用形如 `ecg_dc_block_init(&g_dc, <宏>)`（实参 = %r）' % arg)
    chk('B5b', not re.search(r'ecg_dc_block_init\(\s*&g_dc\s*,\s*[0-9]', ads_nc),
        '实参不再出现任何数字字面量（否则改 1 行常数会**静默**污染其它档的 Σf）')

    p()
    p('=' * 108)
    if FAIL:
        p('⇒ 落码自检 **失败 ✗**（%d 项）' % len(FAIL))
        for x in FAIL:
            p('   ' + x)
    else:
        p('⇒ 落码自检 **全部通过 ✓**（B ＝ A+ 逐位删 2 行；Σf 0.02 已在 coeffs.h 里可读可查）')
    p('=' * 108)

    with io.open(os.path.join(HERE, '_t84_verify_out.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(OUT) + '\n')
    if not a.quiet:
        print()
        print('[写出] _t84_verify_out.txt')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
