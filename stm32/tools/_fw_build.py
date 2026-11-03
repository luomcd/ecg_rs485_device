# -*- coding: utf-8 -*-
"""
_fw_build.py —— 命令行构建 + 镜像核对（一条命令跑完）

背景（重要，别再走弯路）
------------------------
本工程**不是**用 IAR IDE 的 `EWARM/demo_ads1293.ewp` 构建的 —— 那个 .ewp 最后保存于
2026-09-09，文件清单里**缺** `Filter/ecg_crc8.c` 和 `Filter/fast_restore_snippet.c`，
用它构建会在链接期报 Li005（找不到 `ecg_crc8` / `ecg_restore_process`）。

真实构建入口是 **VS Code 的 EIDE 插件（cl.eide）**：
  - 工程定义 : `EWARM/demo_ads1293/.eide/eide.yml`（outDir=build）
  - 构建器   : `unify_builder.exe`（EIDE 自带，.NET）
  - 工具链   : IAR EWARM **9.70.2**（`C:\\iar\\ewarm-9.70.2`，iccarm/ilinkarm 可用）
  - 参数文件 : `EWARM/demo_ads1293/build/demo_ads1293/builder.params`

⇒ 命令行等价物：`unify_builder.exe -p <builder.params> --rebuild`

用法
----
  python _fw_build.py                  # 完整重建 + 镜像核对
  python _fw_build.py --no-build       # 只核对现有产物
  python _fw_build.py --inc            # 增量构建（不加 --rebuild）
  python _fw_build.py --no-verify      # 只构建
  python _fw_build.py --no-lint        # 跳过注释结构体检
  python _fw_build.py --strict-warn    # 出现基线外的新告警时以退出码 5 失败
"""
import argparse
from collections import Counter
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
EIDE_ROOT = os.path.join(PROJ, "EWARM", "demo_ads1293")
PARAMS = os.path.join(EIDE_ROOT, "build", "demo_ads1293", "builder.params")
ELF = os.path.join(EIDE_ROOT, "build", "demo_ads1293", "demo_ads1293.elf")
MAP = os.path.join(EIDE_ROOT, "build", "demo_ads1293", "demo_ads1293.map")
HEX = os.path.join(EIDE_ROOT, "build", "demo_ads1293", "demo_ads1293.hex")

CANDIDATE_BUILDERS = [
    os.path.expandvars(r"%USERPROFILE%\.vscode\extensions"),
]


def find_builder():
    """在 VS Code 扩展目录里找 EIDE 自带的 unify_builder.exe。"""
    root = os.path.expandvars(r"%USERPROFILE%\.vscode\extensions")
    if not os.path.isdir(root):
        return None
    for d in sorted(os.listdir(root), reverse=True):
        if d.startswith("cl.eide-"):
            exe = os.path.join(root, d, "res", "tools", "win32", "unify_builder", "unify_builder.exe")
            if os.path.exists(exe):
                return exe
    return None


# ---------------------------------------------------------------------------
# 编译器诊断（🆕 2026-09-30：**构建成功时也必须报 Warnings 计数**）
# ---------------------------------------------------------------------------
# 起因（本项目 `NOW.md` 遗留项⑧）：
#   `unify_builder.exe` 在**成功**时正文里**没有任何 "Warnings: N" 汇总行**，而本脚本旧版
#   只 print 最后 6 行 ⇒ **告警被整段吞掉** ⇒ 「构建 ✅」这句话**不可核验**。
#   ⇒ 现在自己数，并与**已知基线**对账（新增/消失都要看得见）。
#
# ICCARM 诊断行格式（实测，2026-09-30）：
#     "c:\...\Core\Src\vcomcmd.c",612  Warning[Pa082]:
#               undefined behavior: the order of volatile accesses is undefined in
#               this statement
#   · 源码行回显含中文、在非 UTF-8 控制台下是 **mojibake**（别慌，那不是源码坏了）；
#   · 但**诊断消息本身是 ASCII** ⇒ 可以安全解析与匹配。
DIAG_RE = re.compile(r'^"([^"]+)",(\d+)\s+(Warning|Error)\[(\w+)\]:\s*(.*)$', re.M)

# 已知告警基线（2026-09-30 真实构建固化；⚠ **刻意不绑行号** —— 源码增长会让行号漂移，
# 本题实测就从 `:457` 漂到了 `:612`）。匹配键 = (文件名, 告警标签)，按**多重集**对账。
KNOWN_WARNS = [
    # 待授权未修：volatile 读序未定义（拆成两条语句即可消，语义逐位等价）
    ("vcomcmd.c", "Pa082"),
]


def diagnostics(text):
    """从构建日志里抠出全部 `Warning[Tag]` / `Error[Tag]` 诊断。"""
    out = []
    for m in DIAG_RE.finditer(text or ""):
        path = m.group(1).replace("\\", "/")
        out.append({
            "base": os.path.basename(path),
            "path": m.group(1),
            "line": int(m.group(2)),
            "kind": m.group(3),        # Warning | Error
            "tag": m.group(4),         # Pa082 | Pe007 | ...
            "msg": m.group(5).strip(),
        })
    return out


def report_diags(diags, show_count=True):
    """打印诊断明细 ＋ 与基线的多重集对账。

    返回 **(需处置条数, 消失条数)**；`需处置` = 基线外的新增告警 **＋ 全部 Error**。
    ⚠ Errors 一律计入「需处置」而**不**受基线豁免 —— 否则「回码 0 却带 Error 行」
      这种自相矛盾的日志会被本门悄悄放过（2026-09-30 阴性对照当场抓出）。
    """
    warns = [d for d in diags if d["kind"] == "Warning"]
    errs = [d for d in diags if d["kind"] == "Error"]
    if show_count:
        print("  Warnings: %d  ｜ Errors: %d" % (len(warns), len(errs)))
    for d in diags:
        print("    %-7s %s:%d  [%s] %s"
              % (d["kind"], d["base"], d["line"], d["tag"], d["msg"][:64]))

    cur = Counter((d["base"], d["tag"]) for d in warns)
    base = Counter(KNOWN_WARNS)
    new, gone = cur - base, base - cur
    fmt = lambda c: "、".join("%s[%s]×%d" % (a, b, n) for (a, b), n in sorted(c.items()))
    if new:
        print("  🛑 **新增告警 %d 条** —— 必须逐条判定去留，别直接放过：%s"
              % (sum(new.values()), fmt(new)))
    if gone:
        print("  ⚠ 基线告警消失 %d 条（基线该更新了，或那条路径没被编进来）：%s"
              % (sum(gone.values()), fmt(gone)))
    if errs:
        print("  🛑🛑 **出现 Error 诊断 %d 条** —— 构建不可能算干净，先处置：%s"
              % (len(errs), fmt(Counter((d["base"], d["tag"]) for d in errs))))
    if not new and not gone and not errs:
        print("  ✓ 告警与基线一致（基线 %d 条：%s）"
              % (len(KNOWN_WARNS), fmt(base) or "无"))
    return sum(new.values()) + len(errs), sum(gone.values())


def mem_summary(mapfile):
    """从 IAR map 尾部取三分区用量。"""
    if not os.path.exists(mapfile):
        return None
    txt = open(mapfile, encoding="utf-8", errors="replace").read()
    d = {}
    for k, pat in (("code", r"([\d']+) bytes of readonly\s+code memory"),
                   ("rodata", r"([\d']+) bytes of readonly\s+data memory"),
                   ("rwdata", r"([\d']+) bytes of readwrite data memory")):
        m = re.search(pat, txt)
        if m:
            d[k] = int(m.group(1).replace("'", ""))
    sym = re.search(r"^ECG_FILTER_COEFFS_F64\s+0x[0-9a-fA-F']+\s+(0x[0-9a-fA-F]+)\s", txt, re.M)
    if sym:
        d["coeffs_bytes"] = int(sym.group(1), 16)
    else:
        d["coeffs_bytes"] = None
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--inc", action="store_true", help="增量构建（默认 --rebuild 全量）")
    ap.add_argument("--no-lint", action="store_true",
                    help="跳过注释结构体检（默认先跑 _chk_comment.py）")
    ap.add_argument("--strict-warn", action="store_true",
                    help="出现基线外的新告警时以退出码 5 失败（默认只大声报出来）")
    a = ap.parse_args()

    if not a.no_build:
        if not os.path.exists(PARAMS):
            print("✗ 找不到 builder.params:", PARAMS)
            print("  ⇒ 先用 VS Code + EIDE 打开工程生成一次，或检查路径")
            return 1
        exe = find_builder()
        if not exe:
            print("✗ 找不到 EIDE 的 unify_builder.exe（VS Code 扩展 cl.eide 未安装？）")
            return 1

        # ---- 注释结构前置门（2026-09-30 加，起因＝一次注释改动连炸两次）----
        # 注释正文里出现 `*/`（例：Markdown 加粗紧贴斜杠写成 `**0.1 µV**/LSB`，或在已有
        # 块注释里又收一个 ` */`）⇒ 块注释**提前闭合**、余下正文被当代码：
        #   · 报错行 ≠ 出错行（ICCARM 报的是"变成代码"那一行）
        #   · 修一处才暴露下一处
        #   · gcc -fsyntax-only -Wall -Wextra **查不出来**，只有真构建才炸
        # ⇒ 提前筛一遍，报错是干净的中文行号（IAR 输出在非 UTF-8 控制台下是 mojibake）。
        if not a.no_lint:
            lint = os.path.join(HERE, "_chk_comment.py")
            if os.path.exists(lint):
                targets = [os.path.join(PROJ, d) for d in ("Ads1293", "Core", "Filter")]
                lp = subprocess.run([sys.executable, lint, "--quiet"] + targets,
                                    capture_output=True, text=True, errors="replace")
                if lp.returncode != 0:
                    print("✗ 注释结构体检未过 —— 先修下面这些（不是编译器问题）：")
                    print(lp.stdout)
                    return 4
                print("  ✓ 注释结构体检通过（_chk_comment.py）")
            else:
                print("  (跳过注释结构体检：找不到 _chk_comment.py)")

        cmd = [exe, "-p", PARAMS, "--no-color"] + ([] if a.inc else ["--rebuild"])
        print(">> 构建:", " ".join(cmd))
        p = subprocess.run(cmd, cwd=EIDE_ROOT, capture_output=True, text=True, errors="replace")
        diags = diagnostics(p.stdout + "\n" + p.stderr)

        if p.returncode != 0 or "build successfully" not in p.stdout:
            print("✗ 构建失败（返回码 %d）" % p.returncode)
            # ★ 失败时**先报结构化诊断**（比裸贴 3000 字符日志有用得多：
            #   报错行 ≠ 出错行、且 IAR 的源码回显是 mojibake，逐条列出来才看得清）
            if diags:
                report_diags(diags)
            tail = [l for l in p.stdout.splitlines() if l.strip()][-4:]
            for l in tail:
                print("  ", l)
            print(p.stdout[-2000:])
            return 2

        # ★ **成功也要报告警计数**（旧版只贴最后 6 行 ⇒ 告警被整段吞掉 ⇒ 「构建 ✅」不可核验）
        tail = [l for l in p.stdout.splitlines() if l.strip()][-3:]
        for l in tail:
            print("  ", l)
        nw = len([d for d in diags if d["kind"] == "Warning"])
        ne = len([d for d in diags if d["kind"] == "Error"])
        print("✓ 构建成功 ｜ Warnings: %d ｜ Errors: %d" % (nw, ne))
        n_new, _n_gone = report_diags(diags, show_count=False)
        if n_new and a.strict_warn:
            print("✗ 出现基线外的新告警（--strict-warn）⇒ 退出码 5")
            return 5

    # ---- 产物与资源账 ----
    print()
    print("=" * 72)
    print("产物")
    print("=" * 72)
    for f in (HEX, ELF, MAP):
        print("  %-14s %s" % (os.path.basename(f),
                              ("%d 字节" % os.path.getsize(f)) if os.path.exists(f) else "缺失"))
    ms = mem_summary(MAP)
    if ms:
        print()
        print("  只读代码 %s B ｜ 只读数据(rodata) %s B ｜ 读写数据 %s B"
              % (ms.get("code"), ms.get("rodata"), ms.get("rwdata")))
        if ms.get("coeffs_bytes"):
            print("  ECG_FILTER_COEFFS_F64 段 = %d B = %d 节 × 40 B"
                  % (ms["coeffs_bytes"], ms["coeffs_bytes"] // 40))

    # ---- 编译期档位（2026-09-21 加：防「改了宏但产物没变」与「宏名拼错静默变 ST40」）----
    # 解析方式＝预处理头文件（与源码完全同一套预处理），不依赖 EIDE / builder.params。
    try:
        sys.path.insert(0, HERE)
        import _set_profile as _sp
        ident = _sp.read_source_line()[1]
        alias = _sp.BY_IDENT.get(ident, ("?",))[0]
        r = _sp.resolve()
        print()
        print("  编译期档位 = %s（%s）" % (alias, ident))
        print("  档名 = %s" % r.get("name"))
        print("  节数 = %s = 陷波 %s + BP %s" % (r.get("n"), r.get("notch"), r.get("bp")))
        if ms and ms.get("coeffs_bytes") and r.get("n"):
            exp = r["n"] * 40
            ok = "✓" if ms["coeffs_bytes"] == exp else "✗ 不一致！宏改了但产物没跟着变（或反之）"
            print("  镜像账：ELF 系数段 %d B vs 期望 %d B  %s"
                  % (ms["coeffs_bytes"], exp, ok))
        print("  ⇒ 烧录后请敲 `test notch`，profile: 必须等于上面这条档名（D3 与 ST40 同为 7 节，"
              "只能靠字符串区分）")
    except Exception as e:
        print("  (跳过档位解析：%s)" % e)

    # ---- 镜像核对 ----
    if not a.no_verify:
        print()
        v = os.path.join(HERE, "_t58_verify_elf.py")
        if os.path.exists(v):
            p = subprocess.run([sys.executable, v], capture_output=True, text=True, errors="replace")
            for l in p.stdout.splitlines():
                if l.startswith(("A.", "B.", "C.", "结论", "  ✗", "  ✓", "  期望", "  旧系数", "  60", "  f64", "  地址")):
                    print(l)
            if p.returncode != 0:
                return 3
        else:
            print("  (跳过：找不到 _t58_verify_elf.py)")

    print()
    print("下一步：烧录（猛哥侧，ST-Link V3；本脚本不碰烧录）")
    print("  " + HEX)
    print("  烧录后先敲 `test notch` 核对 profile: 与上面「档名」逐字一致（唯一档位判据）。")
    print("  再按 tools/T70_频响上板作战卡_2026-09-21.md §2 抓数（前置：raw853 on → afe start → notch off）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
