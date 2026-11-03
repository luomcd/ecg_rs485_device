#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C 注释结构体检 —— 揪出「注释提前闭合 / 多余 */ / 未闭合 / 嵌套」四类坑。

为什么需要它（2026-09-30 实发）：
  本项目源码注释大量使用 Markdown 风格加粗（**…**）与单位斜杠（µV/LSB）。
  一旦把单位写成  `**0.1 µV**/LSB`  —— `**/` 里就含 C 块注释结束符 `*/`；
  或者在已有块注释里又收了一个 ` */`  —— 块注释都会**提前闭合**，
  余下正文被当代码解析（IAR 报 `Error[Pe007]: unrecognized token`）。
  ⚠ `arm-none-eabi-gcc -fsyntax-only -Wall -Wextra` **查不出来**（它只在
     ICCARM Warning[Pe009] `nested comment` 这档才可能提示）⇒ 只有真构建才炸。
  ⇒ 本脚本把「真构建才暴露」提前到「改完立刻筛」。

判据（逐字符状态机，跳过字符串/字符字面量与行注释）：
  ① 块注释**外**遇到 `*/`          ⇒ stray-close  ★严重（会让后面整段变代码）
  ② 块注释**内**遇到 `/*`          ⇒ nested      ★严重（ICCARM Pe009 / 提前闭合）
  ③ 读到 EOF 仍停在块注释内        ⇒ unclosed    ★严重
  ④ 行注释里出现 `/*`              ⇒ hint-line   （不致命，只提示）

用法：
  python tools/_chk_comment.py <文件或目录> [更多...]        # 目录递归扫 *.c/*.h
  python tools/_chk_comment.py --quiet <路径>                # 只报有问题的文件
  python tools/_chk_comment.py --all   <路径>                # 连备份/归档目录一起扫
退出码：0 = 全部干净；1 = 有发现。

⚠ 默认**跳过**备份/归档/变异对照目录（`_bak*`・`_archive`・`_deprecated`・`_neg`・
  `__pycache__`・`.git`・`build`）：它们**不是构建输入**，而且其中**刻意**保存着"已知损坏
  的快照"（本项目 `tools/_bak_T166note_20260930/ads1293.c` 正是那版 `/*`×108 vs `*/`×110
  的破注释）⇒ 默认豁免以免长期红灯。**要审计归档就用 `--all`。**
"""

import os
import re
import sys

SRC_EXT = (".c", ".h")

# 默认跳过的目录名特征（子串匹配，大小写不敏感）
SKIP_DIR_KEYS = ("_bak", "_archive", "_deprecated", "_neg", "__pycache__", ".git", "build")

SEV = {
    "stray-close": ("★严重", "块注释外出现 */（注释提前闭合的余波）"),
    "nested":      ("★严重", "块注释内又出现 /*（嵌套注释 / 收尾漏删）"),
    "unclosed":    ("★严重", "文件结束时块注释仍未闭合"),
    "hint-line":   ("提示",   "行注释里含 /*（无害，仅提示）"),
}


def scan(path):
    """返回 issues: [(line, kind, detail)]，以及统计 (open_n, close_n)。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    n = len(src)
    i = 0
    line = 1
    st = "code"                 # code | block | line | str | chr
    open_line = None            # 当前块注释的起始行
    issues = []
    hinted = set()              # 已提示过 hint-line 的行号（同一行多个 `/*` 只报一次）
    n_open = n_close = 0

    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if st == "code":
            if c == "/" and nxt == "*":
                n_open += 1
                st, open_line = "block", line
                i += 2
                continue
            if c == "/" and nxt == "/":
                st = "line"
                i += 2
                continue
            if c == "*" and nxt == "/":
                n_close += 1
                issues.append((line, "stray-close", ""))
                i += 2
                continue
            if c == '"':
                st = "str"
            elif c == "'":
                st = "chr"
        elif st == "block":
            if c == "*" and nxt == "/":
                n_close += 1
                st, open_line = "code", None
                i += 2
                continue
            if c == "/" and nxt == "*":
                issues.append((line, "nested",
                               "外层块注释自第 %s 行开始" % open_line))
                i += 2
                continue
        elif st == "line":
            if c == "\\" and nxt == "\n":
                line += 1
                i += 2
                continue
            if c == "\n":
                st = "code"
                i += 1
                continue
            if c == "/" and nxt == "*" and line not in hinted:
                hinted.add(line)
                issues.append((line, "hint-line", ""))
        elif st == "str":
            if c == "\\":
                i += 2
                continue
            if c == '"':
                st = "code"
        elif st == "chr":
            if c == "\\":
                i += 2
                continue
            if c == "'":
                st = "code"

        if c == "\n":
            line += 1
        i += 1

    if st == "block":
        issues.append((line, "unclosed", "自第 %s 行开始" % open_line))

    return issues, (n_open, n_close)


def collect(targets, include_all=False):
    def skipped(name):
        if include_all:
            return False
        low = name.lower()
        return any(k in low for k in SKIP_DIR_KEYS)

    out = []
    for t in targets:
        if os.path.isdir(t):
            for root, dirs, files in os.walk(t):
                dirs[:] = [d for d in dirs if not skipped(d)]
                for fn in sorted(files):
                    if fn.lower().endswith(SRC_EXT):
                        out.append(os.path.join(root, fn))
        elif os.path.isfile(t):
            out.append(t)
        else:
            print("!! 路径不存在：%s" % t)
    return out


def main(argv):
    quiet = "--quiet" in argv
    include_all = "--all" in argv
    targets = [a for a in argv[1:] if not a.startswith("--")]
    if not targets:
        targets = ["."]

    files = collect(targets, include_all)
    bad = 0
    print("== 注释结构体检：%d 个文件%s =="
          % (len(files), "（含备份/归档）" if include_all else "（已跳过备份/归档）"))
    for p in files:
        issues, (n_open, n_close) = scan(p)
        hard = [x for x in issues if x[1] != "hint-line"]
        if hard:
            bad += 1
            print("\n[FAIL] %s   (/* %d  :  */ %d)" % (p, n_open, n_close))
            for ln, kind, det in issues:
                sev, why = SEV[kind]
                print("   %-12s L%-6d %s%s" % (sev, ln, why,
                                               ("；" + det) if det else ""))
            # 打印现场行，方便直接改
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().split("\n")
            for ln, kind, _d in issues:
                if kind == "hint-line" or ln - 1 >= len(lines):
                    continue
                print("        L%d| %s" % (ln, lines[ln - 1].strip()[:110]))
        elif not quiet:
            print("  ok   %s   (/* %d : */ %d)" % (p, n_open, n_close))

    print("\n== 结果：%d 个文件有问题 / 共 %d 个 ==" % (bad, len(files)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
