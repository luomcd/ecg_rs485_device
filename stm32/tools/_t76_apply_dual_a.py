# -*- coding: utf-8 -*-
"""
_t76_apply_dual_a.py —— 落码 **DUAL_A 档**（T75 候选 A：一套参数、两个 notch 状态各过一项）

为什么用脚本落码而不是手抄
--------------------------
本档 = **两个已验收子系统直接拼接**：
      前  9 节 = IMP620_N2 的陷波组（49.8/50/50.2 Q12 + 99.8/100/100.2 Q20 + 149.8/150/150.2 Q20）
      后  3 节 = DIAG150_D3 的 BP（LP2@250 + 2×HP4@0.05）
"逐位相同"若靠手抄就只能靠眼睛；本脚本**从 ecg_filter.c 原文提取字面量后拼接**
⇒ 逐位相同是**构造保证**，`--verify` 再回读比对一次作为第二道。

它做什么
--------
  ① 从 `Filter/ecg_filter.c` 提取 IMP620_N2 的 F32/F64 表前 9 节、DIAG150_D3 的 F32/F64 表后 3 节
  ② 生成新 `#elif (ECG_FILTER_PROFILE == ECG_PROFILE_DUAL_A)` 分支（两张 12 元素表）
  ③ 在 `Filter/ecg_filter_coeffs.h` 加档位常量 `ECG_PROFILE_DUAL_A 6` 与分支宏
  ④ 两处 `#error` 非法值文案补 `6=DUAL_A`
  ⑤ 插到 **IMP620_N2 之后、ST40 之前**（两文件相对顺序必须一致，
     否则 `_t58_verify_struct.py` 的「块 i ↔ 档 i」配对会错位）

用法
----
  python tools/_t76_apply_dual_a.py --dry      # 只打印将写入的分支，不动文件
  python tools/_t76_apply_dual_a.py --apply    # 备份 + 写入
  python tools/_t76_apply_dual_a.py --verify   # 只回读比对（逐位自证）

自证口径：f64 按 `repr` 级字符串比对（17 位有效数字，0 ULP）；f32 同理由字面量文本直接搬运。
"""
import argparse
import io
import os
import re
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
C_FILE = os.path.join(PROJ, "Filter", "ecg_filter.c")
H_FILE = os.path.join(PROJ, "Filter", "ecg_filter_coeffs.h")
BAK_DIR = os.path.join(HERE, "_bak_T76_20260921")

NEW_IDENT = "ECG_PROFILE_DUAL_A"
NEW_PROF = "DUAL_A"          # 不带 ECG_PROFILE_ 前缀，供 grab_table 的正则用
NEW_VAL = 6
NEW_ALIAS = "DUAL_A"
NEW_NAME = ("DUAL_A: 0.05-250Hz + notch50x3(Q12)+100x3(Q20)+150x3(Q20) (12 sections)")
N_NOTCH, N_BP = 9, 3
SRC_NOTCH = "IMP620_N2"     # 取陷波 9 节
SRC_BP = "DIAG150_D3"       # 取 BP 3 节


# --------------------------------------------------------------- 提取
def grab_table(src, prof, kind):
    """取某档某精度表的正文（`{` 到 `\n};` 之间）。"""
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
    """把表正文拆成元素文本块（保留原字面量与多行格式，剥掉注释）。"""
    out = []
    for m in re.finditer(r'\{[^{}]*\}', body):
        a = body.rfind('\n', 0, m.start()) + 1
        b = body.find('\n', m.end())
        if b < 0:
            b = len(body)
        chunk = re.sub(r'/\*.*?\*/', '', body[a:b], flags=re.S).strip()
        out.append(chunk)
    return out


def norm(text):
    """归一化成可比的数元组（元素级）。"""
    rows = []
    for m in re.finditer(r'\{([^{}]*)\}', text):
        nums = re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', m.group(1))
        rows.append(tuple(float(x) for x in nums))
    return rows


# --------------------------------------------------------------- 生成
def build_c_branch(n2f32, n2f64, d3f32, d3f64):
    """n2* = IMP620_N2 表（13 节，取前 9）；d3* = D3 表（7 节 = 4 陷波 + 3 BP，取**后 3**）。"""
    notch = "\n".join("    " + e for e in n2f32[:N_NOTCH])
    notch64 = "\n".join("    " + e for e in n2f64[:N_NOTCH])
    bp = "\n".join("    " + e for e in d3f32[-N_BP:])
    bp64 = "\n".join("    " + e for e in d3f64[-N_BP:])
    tpl = """#elif (ECG_FILTER_PROFILE == @@IDENT@@)
/* ---------------- @@ALIAS@@ 双态档：0.05~250Hz + 50簇(±0.2,Q12) + 100簇(±0.2,Q20) + 150簇(±0.2,Q20)（12 节） ------
 * 2026-09-21 新增（T75 候选 A ⇒ T76 落码）。**实验档：一套参数、两个 notch 状态各过一项。**
 *
 * 构成 = **两个已验收子系统直接拼接，零新参数、零重新设计**：
 *   第 0~8  节 = IMP620_N2 的陷波组（本文件上面 IMP620_N2 分支第 0~8 节）——**逐位相同**
 *   第 9~11 节 = DIAG150_D3 的 BP（本文件上面 D3 分支第 4~6 节）——**逐位相同**
 *   （T66 之前的 IMP620_N2 就是这个结构；T66 为输入阻抗把 BP 由 LP2@250 回退成 LP4@150，
 *     本档把那一次回退"保留回来"，**不动 IMP620_N2 本身** ⇒ 随时可一行宏切回。）
 *
 * 两种状态各管一项（标准本来就要求分态做，见 GB 9706.225 §201.12.4.105.2/.3 与 T44/T75）：
 *   notch ON （默认；9+3 = 12 节全用）  → 输入阻抗 / 噪声电平
 *                        150 簇把 149.87 Hz（市电 3 次谐波）压 ~−90 dB ⇒ 这是 T73 那 116.55% 的解药
 *   notch OFF（跳过链首 9 节，只剩 3 节 BP）→ 频响 / 低频脉冲 / CMRR / 过载容限
 *                        余下链 **与 D3 的 OFF 态逐位等价** ⇒ 频响同 D3（150 Hz 归一 ~75.6%）
 *
 * 预期指标（离线预测，见 tools/T76_*.md；上线前以实测为准）：
 *   notch OFF：100/120/150 Hz 归一 ≈75%(判限 ≥70%) ✓
 *   notch ON ：输入阻抗 1 s 窗 p-p 中位 ≈105.5% ✓（判限 ≤110%）；p95 ≈111.2% ✗
 *   ⚠ 本档 ON 态带宽仍 250 Hz ⇒ 200/250 Hz 市电谐波被放行 ⇒ 中位比 IMP620_N2 的 ~99.4% 高 ~6 pp。
 *     **若判据取 p95 ⇒ 本档不改用**，直接 `--set n2` 一行切回。
 *
 * ⚠ 与候选 B（双态连 BP 一起切）的差别只在 ON 态：候选 B 的 ON 态带宽变 150 Hz
 *   ⇒ 会构成"可选带宽设置"，须声明为两个独立工作模式；本档无此问题 ⇒ 合规最干净。 */
const ecg_biquad_coeff_f32 ECG_FILTER_COEFFS_F32[ECG_NUM_SECTIONS] = {
    /* 第 0~8 节：陷波组 = 50 簇(±0.2Hz,Q12) + 100 簇(±0.2Hz,Q20) + 150 簇(±0.2Hz,Q20)
       （= IMP620_N2 第 0~8 节，**逐位相同**） */
@@NOTCH@@
    /* 第 9~11 节：0.05~250 Hz BP = 1×LP2@250 + 2×HP4@0.05（= D3 第 4~6 节，**逐位相同**） */
@@BP@@
};

const ecg_biquad_coeff_f64 ECG_FILTER_COEFFS_F64[ECG_NUM_SECTIONS] = {
    /* 第 0~8 节：陷波组（= IMP620_N2 第 0~8 节，逐位相同） */
@@NOTCH64@@
    /* 第 9~11 节：BP = LP2@250 + 2×HP4@0.05（= D3 第 4~6 节，逐位相同） */
@@BP64@@
};

"""
    return (tpl.replace("@@IDENT@@", NEW_IDENT)
               .replace("@@ALIAS@@", NEW_ALIAS)
               .replace("@@NOTCH64@@", notch64)
               .replace("@@NOTCH@@", notch)
               .replace("@@BP64@@", bp64)
               .replace("@@BP@@", bp))


def build_h_branch():
    tpl = """#elif (ECG_FILTER_PROFILE == @@IDENT@@)
#define ECG_NUM_SECTIONS @@N@@
#define ECG_NUM_NOTCH_SECTIONS @@NN@@
#define ECG_NUM_BP_SECTIONS @@NB@@
#define ECG_PROFILE_NAME "@@NAME@@"
"""
    return (tpl.replace("@@IDENT@@", NEW_IDENT)
               .replace("@@N@@", str(N_NOTCH + N_BP))
               .replace("@@NN@@", str(N_NOTCH))
               .replace("@@NB@@", str(N_BP))
               .replace("@@NAME@@", NEW_NAME))


# --------------------------------------------------------------- 落码
def apply_files(dry=False):
    src_c = io.open(C_FILE, encoding="utf-8").read()
    src_h = io.open(H_FILE, encoding="utf-8").read()

    if NEW_IDENT in src_c or NEW_IDENT in src_h:
        print("✗ 已存在 %s —— 本脚本不重复插入；先 git/备份回滚再跑。" % NEW_IDENT)
        return 1

    n2f32 = split_elems(grab_table(src_c, SRC_NOTCH, "f32"))
    n2f64 = split_elems(grab_table(src_c, SRC_NOTCH, "f64"))
    d3f32 = split_elems(grab_table(src_c, SRC_BP, "f32"))
    d3f64 = split_elems(grab_table(src_c, SRC_BP, "f64"))
    print("提取：%s f32=%d 节 / f64=%d 节 ｜ %s f32=%d 节 / f64=%d 节"
          % (SRC_NOTCH, len(n2f32), len(n2f64), SRC_BP, len(d3f32), len(d3f64)))
    if len(n2f32) < N_NOTCH or len(n2f64) < N_NOTCH or len(d3f32) < N_BP or len(d3f64) < N_BP:
        print("✗ 节数不足，停")
        return 1

    c_branch = build_c_branch(n2f32, n2f64, d3f32, d3f64)
    h_branch = build_h_branch()

    # ① ecg_filter.c：插到 ST40 分支前（即 IMP620_N2 之后）
    anchor_c = "#elif (ECG_FILTER_PROFILE == ECG_PROFILE_ST40)\n"
    if src_c.count(anchor_c) != 1:
        print("✗ ecg_filter.c 锚点不唯一（%d 处）" % src_c.count(anchor_c))
        return 1
    new_c = src_c.replace(anchor_c, c_branch + anchor_c, 1)

    # ② coeffs.h：档位常量
    const_anchor = "#define ECG_PROFILE_IMP620_N2  5\n"
    if src_h.count(const_anchor) != 1:
        print("✗ coeffs.h 档位常量锚点不唯一")
        return 1
    new_h = src_h.replace(
        const_anchor,
        const_anchor +
        "#define ECG_PROFILE_DUAL_A     6   /* ★ T76(2026-09-21) 双态档：一套参数、两个 notch 状态各过一项 */\n",
        1)

    # ③ coeffs.h：分支宏（插到 ST40 分支前）
    anchor_h = "#elif (ECG_FILTER_PROFILE == ECG_PROFILE_ST40)\n"
    if new_h.count(anchor_h) != 1:
        print("✗ coeffs.h 分支锚点不唯一（%d 处）" % new_h.count(anchor_h))
        return 1
    new_h = new_h.replace(anchor_h, h_branch + anchor_h, 1)

    # ④ 两处 #error 文案
    err_old = "合法值 0=ST40 / 1=DIAG150 / 2=DIAG150_D2 / 3=DIAG150_D3 / 4=IMP620 / 5=IMP620_N2"
    err_new = err_old + " / 6=DUAL_A"
    n_err = 0
    for tag in ("c", "h"):
        blob = new_c if tag == "c" else new_h
        cnt = blob.count(err_old)
        n_err += cnt
        blob = blob.replace(err_old, err_new)
        if tag == "c":
            new_c = blob
        else:
            new_h = blob
    print("#error 文案更新：%d 处" % n_err)

    print()
    print("将插入 ecg_filter.c 的分支（%d 行）：" % c_branch.count("\n"))
    print("-" * 74)
    print(c_branch)
    print("-" * 74)
    print("将插入 coeffs.h 的分支（%d 行）：" % h_branch.count("\n"))
    print("-" * 74)
    print(h_branch)
    print("-" * 74)

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
    n2f32 = norm(grab_table(src_c, SRC_NOTCH, "f32"))
    n2f64 = norm(grab_table(src_c, SRC_NOTCH, "f64"))
    d3f32 = norm(grab_table(src_c, SRC_BP, "f32"))
    d3f64 = norm(grab_table(src_c, SRC_BP, "f64"))
    nuf32 = norm(grab_table(src_c, NEW_PROF, "f32"))
    nuf64 = norm(grab_table(src_c, NEW_PROF, "f64"))

    print("=" * 74)
    print("DUAL_A 逐位自证（回读源码比对）")
    print("=" * 74)
    ok = True
    off_bp = len(d3f32) - N_BP      # D3 表里 BP 段的起始下标（陷波 4 节）
    for kind, got, exp in (("f32", nuf32, n2f32[:N_NOTCH] + d3f32[-N_BP:]),
                           ("f64", nuf64, n2f64[:N_NOTCH] + d3f64[-N_BP:])):
        n_ok = len(got) == len(exp)
        if not n_ok:
            print("  ✗ %s 节数 %d ≠ 期望 %d" % (kind, len(got), len(exp)))
            ok = False
            continue
        bad = [i for i in range(len(exp)) if got[i] != exp[i]]
        src_tag = ["%s[%d]" % (SRC_NOTCH, i) for i in range(N_NOTCH)] + \
                  ["%s[%d]" % (SRC_BP, off_bp + i - N_NOTCH) for i in range(N_NOTCH, len(exp))]
        if bad:
            ok = False
            for i in bad:
                print("  ✗ %s 第 %d 节 ≠ %s" % (kind, i, src_tag[i]))
                print("      实得 %r" % (got[i],))
                print("      期望 %r" % (exp[i],))
        else:
            print("  ✓ %s 共 %d 节：第 0~%d == %s［逐位］，第 %d~%d == %s［逐位］"
                  % (kind, len(got), N_NOTCH - 1, SRC_NOTCH, N_NOTCH, len(got) - 1, SRC_BP))
    # 结构：节数宏
    h = io.open(H_FILE, encoding="utf-8").read()
    m = re.search(r'ECG_PROFILE_DUAL_A\)\n#define ECG_NUM_SECTIONS (\d+)\n'
                  r'#define ECG_NUM_NOTCH_SECTIONS (\d+)\n#define ECG_NUM_BP_SECTIONS (\d+)', h)
    if m:
        a, b, c = (int(x) for x in m.groups())
        good = (a == N_NOTCH + N_BP and b == N_NOTCH and c == N_BP)
        ok &= good
        print("  %s 宏：SECTIONS %d = NOTCH %d + BP %d" % ("✓" if good else "✗", a, b, c))
        nm = re.search(r'ECG_PROFILE_DUAL_A\)[\s\S]{0,200}?ECG_PROFILE_NAME "([^"]*)"', h)
        print("      NAME = %s" % (nm.group(1) if nm else "?"))
    else:
        ok = False
        print("  ✗ coeffs.h 里找不到 DUAL_A 的节数宏")
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
