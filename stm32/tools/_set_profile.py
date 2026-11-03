# -*- coding: utf-8 -*-
"""
_set_profile.py —— 档位切换统一入口（改一行宏 + 预处理复核 + 打印期望探针）

为什么需要它
------------
本工程有 **九档** 滤波链，全部系数表**同时**存在于 `Filter/ecg_filter.c`（`#if/#elif` 分支），
`Filter/ecg_filter_coeffs.h` 里也有对应的九组 `ECG_NUM_SECTIONS / _NOTCH_ / _BP_ / _DC_BLOCK_FC_HZ_F / NAME`。
⇒ **切档 = 改一处宏的值，零参数改动**（不动任何系数、不动任何结构体大小）。

★ 唯一切换点（canonical）：`Filter/ecg_filter_coeffs.h` 的
      #define ECG_FILTER_PROFILE <标识符>
  这一行被 `#ifndef` 包着，**不要**改 `EWARM/demo_ads1293/.eide/eide.yml` 的 defineList：
  那个 yml 只是 EIDE 配置源，真正喂给 `unify_builder` 的是它派生出来的
  `build/demo_ads1293/builder.params`（`"defines": [...]`）—— 改了 yml 而 EIDE 没重新生成
  params，构建**静默沿用旧宏**，你会以为切成功而其实没有。源码里的那一行没有这个风险。

⚠ 拼写陷阱（务必知道）
----------------------
`ECG_PROFILE_ST40 == 0`，而**未定义的标识符在 `#if` 里也按 0 处理**。
⇒ 若把标识符拼错（如写成 `ECG_PROFILE_DIAG150_D33`），`#if` 会命中 `== ECG_PROFILE_ST40`
分支，**编译器不报错**，你得到的是 ST40 档（0.05-40 Hz）。
⇒ 更巧的是 **D3 与 ST40 都是 7 节**，所以"系数段 = 节数×40 B"的镜像账**也区分不了**。
⇒ 唯一可靠判据是 **`test notch` 探针回显的 `profile:` 字符串**（本脚本会打印它）。

用法
----
  python tools/_set_profile.py                  # 显示当前档（预处理解析）
  python tools/_set_profile.py --list           # 九档对照表
  python tools/_set_profile.py --set d3         # 切到 D3（自动备份 + 预处理复核）
  python tools/_set_profile.py --set aplusb     # 切到 A+B（T84 投产档，16 节）
  python tools/_set_profile.py --set n2 --dry   # 试算，不写文件
  python tools/_set_profile.py --resolve        # 只解析（供 _fw_build.py 调用）
  python tools/_set_profile.py --selftest       # 九档全表自检 + 拼写陷阱复现
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
COEFFS_H = os.path.join(PROJ, "Filter", "ecg_filter_coeffs.h")
FILTER_DIR = os.path.join(PROJ, "Filter")
GCC = r"C:\Nordic\Toolchian\gcc-arm-none-eabi-10-2020-q4-major\bin\arm-none-eabi-gcc.exe"
BAK_DIR = os.path.join(HERE, "_bak_T72_20260921")

# 别名 → (源码标识符, 枚举值, 期望节数, 人读档名)  ★ 2026-09-21 T76 加 duala / T77 加 aplus / T84 加 aplusb
PROFILES = [
    ("st40",   "ECG_PROFILE_ST40",       0,  7, "ST40"),
    ("d1",     "ECG_PROFILE_DIAG150",    1,  4, "D1"),
    ("d2",     "ECG_PROFILE_DIAG150_D2", 2,  5, "D2"),
    ("d3",     "ECG_PROFILE_DIAG150_D3", 3,  7, "D3"),
    ("imp620", "ECG_PROFILE_IMP620",     4,  9, "IMP620"),
    ("n2",     "ECG_PROFILE_IMP620_N2",  5, 13, "IMP620_N2"),
    ("duala",  "ECG_PROFILE_DUAL_A",     6, 12, "DUAL_A"),
    ("aplus",  "ECG_PROFILE_DUAL_A_PLUS", 7, 18, "A+"),
    ("aplusb", "ECG_PROFILE_DUAL_A_PLUS_B", 8, 16, "A+B"),
]
BY_ALIAS = {p[0]: p for p in PROFILES}
BY_IDENT = {p[1]: p for p in PROFILES}

TU = """#include "ecg_filter_coeffs.h"
const char *ecg__probe_name = ECG_PROFILE_NAME;
const int   ecg__n_sections = ECG_NUM_SECTIONS;
const int   ecg__n_notch    = ECG_NUM_NOTCH_SECTIONS;
const int   ecg__n_bp       = ECG_NUM_BP_SECTIONS;
const double ecg__dc_fc     = (double)ECG_DC_BLOCK_FC_HZ_F;
"""


def resolve(extra_defines=()):
    """跑预处理，解析出 (档名串, 总节数, 陷波节数, BP节数, dc_block 截止频率)。"""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "_probe.c")
        with open(src, "w", encoding="utf-8") as f:
            f.write(TU)
        cmd = [GCC, "-E", "-P", "-I", FILTER_DIR]
        for d in extra_defines:
            cmd.append("-D" + d)
        cmd.append(src)
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        return {"error": (p.stderr or p.stdout).strip()[:400]}
    out = p.stdout
    m = re.search(r'ecg__probe_name\s*=\s*"([^"]*)"', out)
    nums = {}
    for key in ("ecg__n_sections", "ecg__n_notch", "ecg__n_bp"):
        mm = re.search(re.escape(key) + r"\s*=\s*(\d+)", out)
        nums[key] = int(mm.group(1)) if mm else None
    md = re.search(r"ecg__dc_fc\s*=\s*\(double\)\s*([\d.]+)f?", out)
    return {"name": m.group(1) if m else None,
            "n": nums["ecg__n_sections"],
            "notch": nums["ecg__n_notch"],
            "bp": nums["ecg__n_bp"],
            "dc_fc": float(md.group(1)) if md else None}


def read_source_line():
    with open(COEFFS_H, encoding="utf-8") as f:
        lines = f.readlines()
    for i, ln in enumerate(lines):
        m = re.match(r"#define\s+ECG_FILTER_PROFILE\s+(\S+)", ln)
        if m:
            return i, m.group(1), lines
    return None, None, lines


def show_current(tag="当前档"):
    _, ident, _ = read_source_line()
    r = resolve()
    if "error" in r:
        print("✗ 预处理失败：\n" + r["error"])
        return 1
    alias = BY_IDENT.get(ident, ("?",))[0]
    ok = (r["n"] == r["notch"] + r["bp"])
    print("%s：源码宏 = %s（%s）" % (tag, ident, alias))
    print("  解析结果：%s" % r["name"])
    print("  节数：%d = 陷波 %d + BP %d  %s" % (r["n"], r["notch"], r["bp"],
                                              "✓" if ok else "✗ 结构账不平"))
    print("  dc_block 截止频率 = %s Hz（Σf 的 dc 那一半，另半在 BP 的 HP 节里）"
          % r.get("dc_fc"))
    print("  镜像账预期：系数段 = %d 节 × 40 B = %d B" % (r["n"], r["n"] * 40))
    print("  期望探针：notch:on profile:%s" % r["name"])
    return 0 if ok else 1


def list_profiles():
    print("%-8s %-6s %-26s %-6s %s" % ("别名", "档名", "源码标识符", "节数", "用途"))
    use = {"st40": "监护/ST（0.05-40）", "d1": "诊断 D1（notch50）",
           "d2": "诊断 D2（notch50/100）", "d3": "★频响/脉冲测试主用（0.05-250）",
           "imp620": "输入阻抗档 v1", "n2": "★输入阻抗档 v2（0.05-150）",
           "duala": "双态档 v1（0.05-250，ON 过阻抗／OFF 过频响）",
           "aplus": "A+ 档（18 节）＝**基线对照档，判据 a ✗ 勿投产**",
           "aplusb": "★★当前投产档 A+B（16 节，T83 候选 B；双口径双状态全过＋判据 a 过）"}
    for alias, ident, val, n, name in PROFILES:
        print("%-8s %-6s %-26s %-6d %s" % (alias, name, ident, n, use[alias]))
    return 0


def do_set(alias, dry=False):
    if alias not in BY_ALIAS:
        print("✗ 未知档位别名 %r；可选：%s" % (alias, ", ".join(BY_ALIAS)))
        return 1
    _, ident, val, exp_n, name = BY_ALIAS[alias]

    # 目标档自检：整份头文件在 -D 覆盖下必须解析成期望节数
    r_target = resolve(["ECG_FILTER_PROFILE=%d" % val])
    if "error" in r_target or r_target["n"] != exp_n:
        print("✗ 目标档自检失败：期望 %d 节，实得 %r" % (exp_n, r_target))
        return 1

    idx, cur_ident, lines = read_source_line()
    if idx is None:
        print("✗ 在 %s 里找不到 `#define ECG_FILTER_PROFILE <标识符>`" % COEFFS_H)
        return 1
    cur_alias = BY_IDENT.get(cur_ident, ("?",))[0]

    print("切换：%s → %s（%s → %s，%d 节）" % (cur_alias, alias, cur_ident, ident, exp_n))
    if cur_ident == ident:
        print("  （已经是该档，无需改动）")
        return show_current()

    new_line = ("#define ECG_FILTER_PROFILE %s   "
                "/* ★ 当前档：%s（%d 节）｜切换＝改本行标识符，由 tools/_set_profile.py 负责；"
                "切档后必敲 `test notch` 复核 */\n" % (ident, name, exp_n))
    lines[idx] = new_line
    if dry:
        print("  [dry-run] 将写入：\n    " + new_line.rstrip())
        return 0

    os.makedirs(BAK_DIR, exist_ok=True)
    shutil.copy2(COEFFS_H, os.path.join(BAK_DIR, "ecg_filter_coeffs.h"))
    with open(COEFFS_H, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)
    print("  备份 → %s" % os.path.join(BAK_DIR, "ecg_filter_coeffs.h"))
    print()
    rc = show_current("改后复核")
    print()
    print("下一步：① python tools/_fw_build.py ② 烧录 ③ 敲 `test notch` 看 profile: 是否等于上面那串")
    return rc


def selftest():
    print("=" * 72)
    print("九档全表自检（-D 覆盖，不改文件）")
    print("=" * 72)
    bad = 0
    for alias, ident, val, exp_n, name in PROFILES:
        r = resolve(["ECG_FILTER_PROFILE=%d" % val])
        n_ok = r.get("n") == exp_n
        s_ok = r.get("n") == (r.get("notch") or -1) + (r.get("bp") or -1)
        d_ok = r.get("dc_fc") is not None
        tag = "✓" if (n_ok and s_ok and d_ok) else "✗"
        if not (n_ok and s_ok and d_ok):
            bad += 1
        print("  %s %-9s 值=%d  节数 %s（期望 %d）｜陷波 %s + BP %s = %s｜dc_block %s Hz" % (
            tag, name, val, r.get("n"), exp_n, r.get("notch"), r.get("bp"),
            r.get("n"), r.get("dc_fc")))
        print("      name = %s" % r.get("name"))
    print()
    print("=" * 72)
    print("拼写陷阱复现（演示为什么必须用探针，而不是只看节数）")
    print("=" * 72)
    r = resolve(["ECG_FILTER_PROFILE=ECG_PROFILE_DIAG150_D33"])
    print("  故意拼错 ECG_PROFILE_DIAG150_D33 ⇒ 解析成：%s" % r.get("name"))
    print("  节数 = %s（与 D3 的 7 节相同 ⇒ 镜像账无法区分）" % r.get("n"))
    print("  ⇒ 结论：改档后**必须**敲 `test notch` 看 profile: 字符串。")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--set", metavar="ALIAS")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--resolve", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(GCC):
        print("✗ 找不到 arm-none-eabi-gcc：%s" % GCC)
        return 1
    if a.list:
        return list_profiles()
    if a.selftest:
        return selftest()
    if a.set:
        return do_set(a.set.lower(), a.dry)
    return show_current()


if __name__ == "__main__":
    sys.exit(main())
