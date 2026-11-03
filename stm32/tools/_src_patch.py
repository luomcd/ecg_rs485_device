# -*- coding: utf-8 -*-
"""
_src_patch.py —— 按行号给**源码**打补丁的统一入口（带断言 + **换行符继承**）
================================================================================

为什么要有它（T116/G5 踩出来的三条）：
  1. **内容一律走文件** —— 中文 + 换行塞进命令行会被 shell 截断/转义坏（T113 那次
     反引号被当命令替换、把 INDEX 一行插坏，就是内联写法的代价）。
  2. **🔴 必须继承原文件的换行符** —— 本项目的固件源码是 **CRLF**；脚本若以
     `newline="\\n"` 写盘，会把**整个文件**改成 LF：编译器不在乎，但 diff 全脏、
     git 每次报 "LF will be replaced by CRLF"。T116 首版就是这么把 `ads1293.c`
     从 900 个 CRLF 写成 982 个裸 LF 的，事后再补一次转换。
  3. **落点必须带断言** —— 行号会漂（技能 §1.1）；断言写进同一次调用，插错就**不写盘**。

用法::

  # ops.txt（操作走文件；`#` 开头是注释）
  #   replace A B BLOCK       把 1-based 第 A..B 行（含两端）换成 BLOCK 的内容
  #   insert-after N BLOCK    在第 N 行之后插入 BLOCK（前后各补一个空行）
  python tools/_src_patch.py --file Ads1293/ads1293.c --ops tools/_x/ops.txt \\
      --expect "L356=void ads1293_conversion(bool bstart)" \\
      --expect "L465=}" [--dry-run]

⚠ 多处操作按**从后往前**执行 ⇒ 前面的行号不被后面的扰动。
"""
from __future__ import annotations

import argparse
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve(p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(ROOT, p.replace("/", os.sep))


def read_lines(path: str):
    """读成行列表 + 原换行符（'\\r\\n' / '\\n'）。"""
    b = io.open(path, "rb").read()
    nl = "\r\n" if b.count(b"\r\n") > 0 else "\n"
    text = b.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return text.split("\n"), nl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--ops", required=True,
                    help="操作文件：每行 `replace A B BLOCK` 或 `insert-after N BLOCK`")
    ap.add_argument("--expect", action="append", default=[], metavar="L<n>=<前缀>")
    ap.add_argument("--expect-file", default=None,
                    help="expect 清单文件（每行一个 L<n>=<前缀>，`#` 注释）—— 一次性改动多处时"
                         "免得命令行被十几个 --expect 撑爆（且中文/空格易被 shell 吃掉）")
    ap.add_argument("--no-comment-check", action="store_true",
                    help="🆕T165b：跳过**全文** `/*` `*/` 配对检查。"
                         "⚠ 这个检查是为 **C 源码**写的（注释没闭合会让后面整个函数被吞、报错点却在几十行外），"
                         "而 **markdown/文本** 里 `/**`（加粗斜杠）与 `*/` 是**正常写法** ⇒ 必然误报。"
                         "🛑 `.c` / `.h` **不要**加这个开关。")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.expect_file:
        ep = resolve(a.expect_file)
        if not os.path.exists(ep):
            print("🛑 expect 清单不存在：%s" % ep)
            return 2
        for ln in io.open(ep, encoding="utf-8").read().split("\n"):
            s = ln.strip()
            if s and not s.startswith("#"):
                a.expect.append(s)

    path = resolve(a.file)
    if not os.path.exists(path):
        print("🛑 文件不存在：%s" % path)
        return 2

    lines, nl = read_lines(path)
    n0 = len(lines)
    print("· %-28s %d 行，换行符 = %s" % (a.file, n0, "CRLF" if nl == "\r\n" else "LF"))

    fails = []

    def chk(name, cond, detail=""):
        if cond:
            print("  OK   %s" % name)
        else:
            fails.append(name)
            print("  FAIL %s   %s" % (name, detail))

    # ---- 断言（改动之前，全部基于原始行号）----
    for e in a.expect:
        if not e.startswith("L") or "=" not in e:
            chk("expect 格式应为「L<n>=<前缀>」", False, "收到 %r" % e)
            continue
        key, want = e.split("=", 1)
        try:
            ln = int(key[1:])
        except ValueError:
            chk("expect 行号可解析", False, "收到 %r" % key)
            continue
        got = lines[ln - 1] if 0 < ln <= n0 else "<越界>"
        chk("L%d 以 %r 开头" % (ln, want), got.startswith(want), "实为 %r" % got[:60])

    # ---- 解析 ops ----
    ops = []
    opsp = resolve(a.ops)
    if not os.path.exists(opsp):
        chk("ops 文件存在（%s）" % a.ops, False)
        return 1
    for raw in io.open(opsp, encoding="utf-8").read().split("\n"):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        f = s.split()
        if f[0] == "replace" and len(f) == 4:
            ops.append((int(f[2]), "replace", int(f[1]), f[3]))
        elif f[0] == "insert-after" and len(f) == 3:
            ops.append((int(f[1]), "insert", int(f[1]), f[2]))
        else:
            chk("ops 行可解析（%r）" % s, False)

    if fails:
        print("\n断言/解析未过 %d 条 ⇒ **不写盘**" % len(fails))
        return 1

    ops.sort(key=lambda x: -x[0])          # 从后往前

    for anchor, kind, A, blk in ops:
        bp = resolve(blk)
        if not os.path.exists(bp):
            chk("块文件存在（%s）" % blk, False)
            continue
        content = (io.open(bp, encoding="utf-8").read()
                   .replace("\r\n", "\n").rstrip("\n").split("\n"))
        if kind == "replace":
            B = anchor
            chk("replace 范围合法（1 ≤ %d ≤ %d ≤ %d）" % (A, B, n0), 1 <= A <= B <= n0)
            if fails:
                break
            # ★T125/G8 教训：整段 replace 最容易吃掉注释块的闭合 `*/`——
            #   被替换段的末行常常正是 `... 。 */`，新块若忘了补 `*/`，
            #   **后面整个函数会被吞进注释**，报错点却在几十行之外（级联）。
            #   判据＝**未闭合计数差守恒**：段内 (/* 数 − */ 数) 必须 = 块内的同一个差
            #   （只数各自的个数会误判"往段内加一段带注释的新代码"这种正常情形）。
            old_txt = "\n".join(lines[A - 1:B])
            new_txt = "\n".join(content)
            if a.no_comment_check:
                print("  · 跳过段内 `/*` `*/` 守恒检查（--no-comment-check）")
            else:
                d_old = old_txt.count("/*") - old_txt.count("*/")
                d_new = new_txt.count("/*") - new_txt.count("*/")
                chk("注释未闭合差守恒（段内 %+d → 块内 %+d）" % (d_old, d_new), d_old == d_new)
                if fails:
                    break
            print("  · replace      %4d..%-4d（%-3d 行）→ %s（%d 行）"
                  % (A, B, B - A + 1, blk, len(content)))
            lines = lines[:A - 1] + content + lines[B:]
        else:
            chk("insert 锚点合法（1 ≤ %d ≤ %d）" % (anchor, n0), 1 <= anchor <= n0)
            if fails:
                break
            print("  · insert-after %4d      → %s（%d 行）" % (anchor, blk, len(content)))
            lines = lines[:anchor] + [""] + content + [""] + lines[anchor:]

    if fails:
        print("\n断言未过 %d 条 ⇒ **不写盘**" % len(fails))
        return 1

    # ★T125/G8 加固：全文 `/*` 与 `*/` 必须配对——注释一旦没闭合，后面的代码全被吞，
    #   报错点却在几十行之外，极难归因（G8 首轮就栽了一次）。⚠ 若某处 `*/` 出现在
    #   字符串字面量里会造成误判，届时人工确认后改这里的判据。
    #   🆕T165b：本检查**只管 C 源码**；markdown/文本 用 `--no-comment-check` 显式跳过
    #   （正文里 `**加粗**/路径` 与 `/**` 到处都有，硬套必然误报 —— v3.0 文档就栽在这条上）。
    whole = "\n".join(lines)
    if a.no_comment_check:
        print("\n· 已按 --no-comment-check 跳过全文 `/*` `*/` 配对检查（非 C 文本 ⇒ 该判据不适用）")
    else:
        nopen, nclose = whole.count("/*"), whole.count("*/")
        if nopen != nclose:
            print("\n🛑 全文注释未配对（/* = %d 个，*/ = %d 个）⇒ **不写盘**。" % (nopen, nclose))
            return 1

    if a.dry_run:
        print("\n--dry-run：计划通过，**未写盘**（预计 %d 行）" % len(lines))
        return 0

    io.open(path, "wb").write(nl.join(lines).encode("utf-8"))
    print("\n已写入 %s：%d 行 → %d 行（换行符保持 %s）"
          % (a.file, n0, len(lines), "CRLF" if nl == "\r\n" else "LF"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
