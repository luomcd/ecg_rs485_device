# -*- coding: utf-8 -*-
"""
_t77_apply_aplus.py —— 落码 **A+ 档**（T76 §4 的 A+ 路径；18 节 = 15 陷波 + 3 BP）

A+ 是什么
---------
DUAL_A（12 节）**再往链首补 200 / 250 Hz 各一簇**（各 3 节 Q20）。
依据 = T76 已验证的结构性质：**陷波节恒在链首、`notch OFF` 就是整段跳过链首**
⇒ **往链首加陷波对 OFF 态频响零代价**（A+ 的 OFF 态与 DUAL_A / D3 逐位相同）。

200/250 簇的参数由 `_t77_design_aplus.py` 定量定案（见该脚本 [1a] 口径标定）：
  · 250 Hz 属市电 **5 次**谐波，漂移 ±0.2453 Hz（= 5 × 0.0981，T31 实测基波漂移带宽）
  · 三节 Q20 簇在 250 Hz 的「深覆盖(60 dB)」= **1.300 Hz ⇒ 余量 2.65×**（150 簇同口径 2.81×）
  · ⇒ **同款配方 ±0.2 / Q20 自洽可扩展，不需要为高频加宽**（深覆盖与 f0 成正比）
  · 实测谱线处：199.74 Hz → −86.1 dB；249.73 Hz → −92.0 dB

本脚本做什么
------------
  ① 从 `ecg_filter.c` 原文提取 DUAL_A 的 F32/F64 表（前 9 节陷波 + 后 3 节 BP）
  ② 用 scipy iirnotch 生成 6 个新节（200 簇 3 + 250 簇 3）
  ③ 拼出 `#elif (ECG_FILTER_PROFILE == ECG_PROFILE_DUAL_A_PLUS)` 分支（两张 18 元素表）
  ④ `coeffs.h` 加档位常量 `ECG_PROFILE_DUAL_A_PLUS 7` 与分支宏
  ⑤ 两处 `#error` 文案补 `7=DUAL_A_PLUS`
  ⑥ 插到 **DUAL_A 之后、ST40 之前**（两文件相对顺序必须一致，
     否则 `_t58_verify_struct.py` 的「块 i ↔ 档 i」配对会错位）

用法
----
  python tools/_t77_apply_aplus.py --dry      # 只打印将写入的分支，不动文件
  python tools/_t77_apply_aplus.py --apply    # 备份 + 写入
  python tools/_t77_apply_aplus.py --verify   # 回读比对（第 0~8 与第 15~17 节逐位 == DUAL_A；第 9~14 节 == 现场设计值）
"""
import argparse
import importlib.util
import io
import os
import re
import shutil
import sys

import numpy as np
from scipy import signal as sps

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
C_FILE = os.path.join(PROJ, "Filter", "ecg_filter.c")
H_FILE = os.path.join(PROJ, "Filter", "ecg_filter_coeffs.h")
BAK_DIR = os.path.join(HERE, "_bak_T77_20260921")

FS = 1e6 / 1172.0

NEW_IDENT = "ECG_PROFILE_DUAL_A_PLUS"
NEW_PROF = "DUAL_A_PLUS"
NEW_VAL = 7
NEW_ALIAS = "A+"
NEW_NAME = ("A+: 0.05-250Hz + notch50x3(Q12)+100x3+150x3+200x3+250x3(Q20) (18 sections)")

SRC = "DUAL_A"                     # 源档：取前 9 节（陷波）与后 3 节（BP）
N_NOTCH_SRC = 9                    # DUAL_A 的陷波节数
N_NEW = 6                          # 新增：200 簇 3 + 250 簇 3
N_NOTCH = N_NOTCH_SRC + N_NEW      # 15
N_BP = 3
N_ALL = N_NOTCH + N_BP             # 18

# ---- 新簇的设计参数（来自 _t77_design_aplus.py 的扫描定案）----
NEW_CLUSTERS = [
    # (中心 Hz, 间距 Hz, Q, 备注)
    (200.0, 0.20, 20.0, "200 簇：市电 4 次谐波；漂移带 0.3924 Hz；深覆盖 1.059 Hz（余量 2.70×）"),
    (250.0, 0.20, 20.0, "250 簇：市电 5 次谐波；漂移带 0.4905 Hz；深覆盖 1.300 Hz（余量 2.65×）"),
]


# --------------------------------------------------------------- 提取/工具
def grab_table(src, prof, kind):
    m = re.search(r'#(?:if|elif) \(ECG_FILTER_PROFILE == ECG_PROFILE_%s\)(.*?)(?=\n#(?:elif|else))'
                  % prof, src, re.S)
    if not m:
        raise SystemExit("✗ 找不到档 %s" % prof)
    m2 = re.search(r'const ecg_biquad_coeff_%s ECG_FILTER_COEFFS_%s\s*\[\s*ECG_NUM_SECTIONS\s*\]\s*=\s*\{(.*?)\n\};'
                   % (kind, kind.upper()), m.group(1), re.S)
    if not m2:
        raise SystemExit("✗ 档 %s 里找不到 %s 表" % (prof, kind))
    return m2.group(1)


def split_elems(body):
    out = []
    for m in re.finditer(r'\{[^{}]*\}', body):
        a = body.rfind('\n', 0, m.start()) + 1
        b = body.find('\n', m.end())
        if b < 0:
            b = len(body)
        out.append(re.sub(r'/\*.*?\*/', '', body[a:b], flags=re.S).strip())
    return out


def norm(text):
    rows = []
    for m in re.finditer(r'\{([^{}]*)\}', text):
        nums = re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', m.group(1))
        rows.append(tuple(float(x) for x in nums))
    return rows


def notch(f0, Q, fs=FS):
    b, a = sps.iirnotch(f0, Q, fs=fs)
    r = sps.tf2sos(b, a)[0]
    return (r[0], r[1], r[2], r[4], r[5])


def f32_lit(v):
    s = "%.9g" % np.float32(v)
    if "e" not in s and "." not in s:
        s += ".0"
    return s + "f"


def row_f32(c):
    return "{ %s, %s, %s, %s, %s }" % tuple(f32_lit(v) for v in c)


def row_f64(c):
    return "{ %s, %s, %s,\n      %s, %s }" % tuple("%.17g" % v for v in c)


def new_sections():
    """返回 6 个新节 [(f0, spacing, Q, coeff), ...]"""
    out = []
    for f0, sp, Q, _cm in NEW_CLUSTERS:
        for f in (f0 - sp, f0, f0 + sp):
            out.append((f, sp, Q, notch(f, Q)))
    return out


# --------------------------------------------------------------- 生成分支
def build_c_branch(d32, d64, new):
    head32 = "\n".join("    " + e for e in d32[:N_NOTCH_SRC])
    head64 = "\n".join("    " + e for e in d64[:N_NOTCH_SRC])
    bp32 = "\n".join("    " + e for e in d32[N_NOTCH_SRC:])
    bp64 = "\n".join("    " + e for e in d64[N_NOTCH_SRC:])

    lines32, lines64 = [], []
    for f0, sp, Q, c in new:
        tag = "200 簇" if abs(f0 - 200.0) <= abs(f0 - 250.0) else "250 簇"
        lines32.append("    " + row_f32(c) + ",  /* %.2f Hz notch Q%g · %s */" % (f0, Q, tag))
        lines64.append("    " + row_f64(c) + ",  /* %.2f Hz notch Q%g · %s */" % (f0, Q, tag))
    new32 = "\n".join(lines32)
    new64 = "\n".join(lines64)

    tpl = """#elif (ECG_FILTER_PROFILE == @@IDENT@@)
/* ---------------- @@ALIAS@@ 双态档：0.05~250Hz + 50簇(Q12) + 100/150/200/250簇(Q20)（18 节） ------
 * 2026-09-21 新增（T76 §4 的 A+ 路径 ⇒ T77 落码）。**实验档：一套参数、两个 notch 状态各过一项。**
 *
 * 构成 = **DUAL_A 全盘照搬 + 链首再补 200/250 各一簇**（**零改动、零重设计**）：
 *   第 0~8  节 = DUAL_A 的陷波组（= IMP620_N2 第 0~8 节）——**逐位相同**
 *   第 9~14 节 = **新增** 200 簇 + 250 簇各三节（Q20，±0.2 Hz）
 *   第 15~17 节 = DUAL_A 的 BP（= D3 第 4~6 节：LP2@250 + 2×HP4@0.05）——**逐位相同**
 *
 * ★ 为什么"往链首加陷波"是免费的（T76 已验证 / T77 复核 max|ΔH| = 0）：
 *   陷波节**恒在链首**，而 `notch OFF` 的定义就是**跳过链首 ECG_NUM_NOTCH_SECTIONS 节**
 *   ⇒ 旁路态只剩那 3 节 BP，往链首加多少陷波都**碰不到 OFF 态**
 *   ⇒ **A+ 的 OFF 态（频响/脉冲/CMRR/过载容限）与 DUAL_A、D3 逐位等价。**
 *
 * ★ 200/250 簇参数定案（`tools/_t77_design_aplus.py`）：
 *   250 Hz 属市电 **5 次**谐波 ⇒ 漂移 = 5 × 0.0981 = **±0.2453 Hz**（T31 实测基波漂移带宽）
 *   三节 Q20 簇「深覆盖(≥60 dB)」：200 Hz **1.059 Hz（余量 2.70×）**、250 Hz **1.300 Hz（余量 2.65×）**
 *   ⇒ 与 150 簇（0.826 Hz / 2.81×）同级 ⇒ **同款配方自洽**，不需为高频加宽
 *   ⇒ 实测谱线处衰减：199.74 Hz **−86.1 dB**、249.73 Hz **−92.0 dB**
 *
 * 两种状态各管一项（标准本来就要求分态做，见 GB 9706.225 §201.12.4.105.2/.3 与 T44/T75）：
 *   notch ON （15+3 = 18 节全用）  → 输入阻抗 / 噪声电平
 *                        50/100/150/200/250 五个簇把 **五根**市电谐波线（49.9/99.7/149.9/199.7/249.7）
 *                        全压到 < −85 dB ⇒ 620 kΩ 接入时的 p-p 只剩真信号与底噪
 *   notch OFF（跳过链首 15 节，只剩 3 节 BP）→ 频响 / 低频脉冲 / CMRR / 过载容限
 *                        余下链 **与 DUAL_A / D3 逐位等价** ⇒ 频响同 D3（150 Hz 归一 ~75.6%）
 *
 * 预期指标（离线预测，见 tools/T77_*.md；上线前以实测为准）：
 *   notch OFF：100/120/150 Hz 归一 **89.05 / 84.29 / 75.64%**（判限 ≥70%）✓ —— 与 D3 逐点 0 差
 *   notch ON ：输入阻抗 1 s 窗 p-p **中位 105.76% ✓（余量 4.24 pp）/ p95 109.93% ✓**（判限 ≤110%）
 *              ⇒ **本档是唯一"两个口径 × 两种状态"全过的结构**
 *   ⚠ 代价：+6 节。CPU 按 0.8115 pp/节外推 ≈ **14.6%**（12 节 9.75%）⇒ 🛑 **必须上板 DWT 实测**。
 *   ⚠ ON 态 200/250 Hz 诊断信息被吃掉（ON 态本就只做输入阻抗/噪声，不诊断 ⇒ 可接受）。
 *
 * 回退：`python tools/_set_profile.py --set duala`（12 节）／`--set d3`（7 节）／`--set n2`（13 节）。 */
const ecg_biquad_coeff_f32 ECG_FILTER_COEFFS_F32[ECG_NUM_SECTIONS] = {
    /* 第 0~8 节：陷波组 = 50 簇(±0.2Hz,Q12) + 100 簇(±0.2Hz,Q20) + 150 簇(±0.2Hz,Q20)
       （= DUAL_A 第 0~8 节，**逐位相同**） */
@@HEAD32@@
    /* 第 9~14 节：**新增** 200 簇 + 250 簇（各三节，Q20，±0.2 Hz） */
@@NEW32@@
    /* 第 15~17 节：0.05~250 Hz BP = 1×LP2@250 + 2×HP4@0.05（= DUAL_A 第 9~11 节，**逐位相同**） */
@@BP32@@
};

const ecg_biquad_coeff_f64 ECG_FILTER_COEFFS_F64[ECG_NUM_SECTIONS] = {
    /* 第 0~8 节：陷波组（= DUAL_A 第 0~8 节，逐位相同） */
@@HEAD64@@
    /* 第 9~14 节：新增 200 簇 + 250 簇 */
@@NEW64@@
    /* 第 15~17 节：BP（= DUAL_A 第 9~11 节，逐位相同） */
@@BP64@@
};

"""
    return (tpl.replace("@@IDENT@@", NEW_IDENT)
               .replace("@@ALIAS@@", NEW_ALIAS)
               .replace("@@HEAD32@@", head32).replace("@@HEAD64@@", head64)
               .replace("@@NEW32@@", new32).replace("@@NEW64@@", new64)
               .replace("@@BP32@@", bp32).replace("@@BP64@@", bp64))


def build_h_branch():
    tpl = """#elif (ECG_FILTER_PROFILE == @@IDENT@@)
#define ECG_NUM_SECTIONS @@N@@
#define ECG_NUM_NOTCH_SECTIONS @@NN@@
#define ECG_NUM_BP_SECTIONS @@NB@@
#define ECG_PROFILE_NAME "@@NAME@@"
"""
    return (tpl.replace("@@IDENT@@", NEW_IDENT)
               .replace("@@N@@", str(N_ALL))
               .replace("@@NN@@", str(N_NOTCH))
               .replace("@@NB@@", str(N_BP))
               .replace("@@NAME@@", NEW_NAME))


# --------------------------------------------------------------- 落码
def apply_files(dry=False):
    src_c = io.open(C_FILE, encoding="utf-8").read()
    src_h = io.open(H_FILE, encoding="utf-8").read()

    if NEW_IDENT in src_c or NEW_IDENT in src_h:
        print("✗ 已存在 %s —— 本脚本不重复插入；先备份回滚再跑。" % NEW_IDENT)
        return 1

    d32 = split_elems(grab_table(src_c, SRC, "f32"))
    d64 = split_elems(grab_table(src_c, SRC, "f64"))
    print("提取源档 %s：f32=%d 节 / f64=%d 节" % (SRC, len(d32), len(d64)))
    if len(d32) != N_NOTCH_SRC + N_BP or len(d64) != N_NOTCH_SRC + N_BP:
        print("✗ 源档节数不是 %d，停" % (N_NOTCH_SRC + N_BP))
        return 1

    new = new_sections()
    print("新生成 6 节：")
    for f0, sp, Q, c in new:
        print("    %8.2f Hz  Q%-4g  b0=%.9g" % (f0, Q, c[0]))

    c_branch = build_c_branch(d32, d64, new)
    h_branch = build_h_branch()

    anchor_c = "#elif (ECG_FILTER_PROFILE == ECG_PROFILE_ST40)\n"
    if src_c.count(anchor_c) != 1:
        print("✗ ecg_filter.c 锚点不唯一（%d 处）" % src_c.count(anchor_c))
        return 1
    new_c = src_c.replace(anchor_c, c_branch + anchor_c, 1)

    const_anchor = ("#define ECG_PROFILE_DUAL_A     6   "
                    "/* ★ T76(2026-09-21) 双态档：一套参数、两个 notch 状态各过一项 */\n")
    if src_h.count(const_anchor) != 1:
        print("✗ coeffs.h 档位常量锚点不唯一（%d 处）" % src_h.count(const_anchor))
        return 1
    new_h = src_h.replace(
        const_anchor,
        const_anchor +
        "#define ECG_PROFILE_DUAL_A_PLUS 7  /* ★ T77(2026-09-21) A+ 档：DUAL_A + 链首 200/250 簇（18 节） */\n",
        1)

    anchor_h = "#elif (ECG_FILTER_PROFILE == ECG_PROFILE_ST40)\n"
    if new_h.count(anchor_h) != 1:
        print("✗ coeffs.h 分支锚点不唯一（%d 处）" % new_h.count(anchor_h))
        return 1
    new_h = new_h.replace(anchor_h, h_branch + anchor_h, 1)

    err_old = ("合法值 0=ST40 / 1=DIAG150 / 2=DIAG150_D2 / 3=DIAG150_D3 / 4=IMP620 / 5=IMP620_N2"
               " / 6=DUAL_A")
    err_new = err_old + " / 7=DUAL_A_PLUS"
    n_err = 0
    for tag in ("c", "h"):
        blob = new_c if tag == "c" else new_h
        n_err += blob.count(err_old)
        blob = blob.replace(err_old, err_new)
        if tag == "c":
            new_c = blob
        else:
            new_h = blob
    print("#error 文案更新：%d 处" % n_err)

    print()
    print("将插入 ecg_filter.c 的分支（%d 行）" % c_branch.count("\n"))
    print("-" * 74)
    print(c_branch)
    print("-" * 74)
    print("将插入 coeffs.h 的分支：")
    print(h_branch)

    if dry:
        print("[dry-run] 未写入任何文件。")
        return 0

    os.makedirs(BAK_DIR, exist_ok=True)
    for p in (C_FILE, H_FILE):
        shutil.copy2(p, os.path.join(BAK_DIR, os.path.basename(p)))
    io.open(C_FILE, "w", encoding="utf-8", newline="\n").write(new_c)
    io.open(H_FILE, "w", encoding="utf-8", newline="\n").write(new_h)
    print("备份 → %s" % BAK_DIR)
    print("已写入：ecg_filter.c %d→%d 字符 ｜ coeffs.h %d→%d 字符"
          % (len(src_c), len(new_c), len(src_h), len(new_h)))
    return 0


def verify():
    src_c = io.open(C_FILE, encoding="utf-8").read()
    d32 = norm(grab_table(src_c, SRC, "f32"))
    d64 = norm(grab_table(src_c, SRC, "f64"))
    n32 = norm(grab_table(src_c, NEW_PROF, "f32"))
    n64 = norm(grab_table(src_c, NEW_PROF, "f64"))
    new = new_sections()

    print("=" * 74)
    print("A+ 逐位自证（回读源码比对）")
    print("=" * 74)
    ok = True
    newv = [c for _f, _s, _q, c in new]
    for kind, got, src_ in (("f32", n32, d32), ("f64", n64, d64)):
        exp = src_[:N_NOTCH_SRC] + newv + src_[N_NOTCH_SRC:]
        # f32 字面量只有 9 位有效数字 ⇒ 与设计值比对必须放容差；f64 逐位（%.17g 往返无损）
        tol = 1e-6 if kind == "f32" else 0.0
        if len(got) != len(exp):
            print("  ✗ %s 节数 %d ≠ 期望 %d" % (kind, len(got), len(exp)))
            ok = False
            continue
        bad = [i for i in range(len(exp))
               if max(abs(got[i][k] - exp[i][k]) for k in range(5)) > tol]
        if bad:
            ok = False
            for i in bad:
                print("  ✗ %s 第 %d 节 不一致" % (kind, i))
                print("      实得 %r" % (got[i],))
                print("      期望 %r" % (exp[i],))
        else:
            print("  ✓ %s 共 %d 节：第 0~%d == %s［逐位］｜ 第 %d~%d == 现场设计值［tol=%g］｜ 第 %d~%d == %s［逐位］"
                  % (kind, len(got), N_NOTCH_SRC - 1, SRC, N_NOTCH_SRC, N_NOTCH - 1, tol,
                     N_NOTCH, len(got) - 1, SRC))
    h = io.open(H_FILE, encoding="utf-8").read()
    m = re.search(r'ECG_PROFILE_DUAL_A_PLUS\)\n#define ECG_NUM_SECTIONS (\d+)\n'
                  r'#define ECG_NUM_NOTCH_SECTIONS (\d+)\n#define ECG_NUM_BP_SECTIONS (\d+)', h)
    if m:
        a, b, c = (int(x) for x in m.groups())
        good = (a == N_ALL and b == N_NOTCH and c == N_BP)
        ok &= good
        print("  %s 宏：SECTIONS %d = NOTCH %d + BP %d" % ("✓" if good else "✗", a, b, c))
        nm = re.search(r'ECG_PROFILE_DUAL_A_PLUS\)[\s\S]{0,300}?ECG_PROFILE_NAME "([^"]*)"', h)
        print("      NAME = %s" % (nm.group(1) if nm else "?"))
    else:
        ok = False
        print("  ✗ coeffs.h 里找不到 A+ 的节数宏")
    print()
    print("⇒ 逐位自证 %s" % ("通过 ✓" if ok else "**失败 ✗**"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify:
        return verify()
    if a.apply or a.dry:
        return apply_files(dry=a.dry)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
