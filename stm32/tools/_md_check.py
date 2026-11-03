# -*- coding: utf-8 -*-
"""_md_check.py —— markdown 交付体检（行尾 / 单行长度 / 表格列数 / §引用闭合 / 名词解释）。

用法：
    "$PY" tools/_md_check.py FILE [FILE ...]
    "$PY" tools/_md_check.py --maxline 400 .workbuddy/memory/NOW.md
    "$PY" tools/_md_check.py --require-terms tools/_t169/*.md tools/T169*.md tools/T174*.md

检查项（按 `firmware-change-verify` 纪律）：
  ① **行尾**：本项目 md 一律 **LF**（CRLF 必须为 0）—— 体检一行：
     `CRLF == 0`。⚠ 编辑动作会把整文件行尾换掉，编译/`git diff` 都看不见。
  ② **单行长度 ≤ --maxline**（默认 400，NOW.md 的硬纪律）。
  ③ **表格列数一致**：同一张表内所有行的 `|` 个数必须相同。
  ④ **§引用计数**：只打印，便于与台账对数（不判错）。
  ⑤ **`名词解释`**（仅 `--require-terms` 时）：**交付文档**须含一行标题
     `## 名词解释（本文用到的）`，定义一律引用单一真源 `tools/GLOSSARY.md`
     （规范见 GLOSSARY 附 A）。**豁免**（不参与本条）：
     文件名以 `_` 开头（工作碎片）、含 `INDEX`（台账/索引）、`GLOSSARY.md`
     （真源自身）、路径含 `.workbuddy`（记忆/日报）、含 `_bak`/`backup`/`/bin/`/`/obj/`。
     🛑 **标题刻意不带 § 号** —— 本项目的 `§0` 已被「结论先行／速览」占用，
     给名词解释编号会与既有 `§0` 撞车（且重编号会破坏外部 § 引用）。

退出码：0 = 全过；1 = 有告警。适合挂进交付前检查。

🛑 工具修正（防后人重踩）：
  - 表格列数用 `(?<!\\\\)\\|` 计数 —— **必须先剥掉行内转义竖线 `\\|`**，
    否则 `max\\|Δ\\|` / `AA55\\|TYPE\\|` 之类会被当列分隔符 ⇒ **全表假阳性**。
  - `--require-terms` **默认关闭**：存量 ~400 份历史文档中，非 T1 范围者尚未补「名词解释」
    ⇒ 默认开启会制造大量噪音。交付文档验收时显式加该开关（见上文用法）。
"""
import argparse
import os
import re
import sys

# ⑤ 名词解释 —— 视为「交付文档」时才要求；工作碎片/台账/真源/记忆一律豁免
# 🛑 判据是**标题行里出现「名词解释」**，不要求带 § 号（§0 已被「结论先行」占用）
TERM_HEAD = re.compile(r"^#{1,6}[^\n]*名词解释", re.MULTILINE)
TERM_EXEMPT_DIRS = (".workbuddy", "_bak", "backup", os.sep + "bin" + os.sep, os.sep + "obj" + os.sep)


def terms_exempt(path):
    """⑤ 的豁免判据：工作碎片 / 台账 / 真源自身 / 记忆 / 备份 / 构建产物。"""
    name = os.path.basename(path)
    norm = path.replace("/", os.sep)
    if any(d in norm for d in TERM_EXEMPT_DIRS):
        return "路径含备份/构建/记忆目录"
    if name.startswith("_"):
        return "文件名以 _ 开头（工作碎片）"
    if "INDEX" in name.upper():
        return "索引/台账文件"
    if name.upper() == "GLOSSARY.MD":
        return "术语真源自身"
    return None


def check(path, maxline, require_terms=False):
    b = open(path, "rb").read()
    try:
        txt = b.decode("utf-8")
    except UnicodeDecodeError as e:
        print(f"  !! UTF-8 解码失败: {e}")
        return 1
    lines = txt.split("\n")
    crlf = b.count(b"\r\n")
    lf = b.count(b"\n")
    bad = 0

    print(f"== {path}")
    print(f"  bytes {len(b)}  LF {lf}  CRLF {crlf}", end="")
    if crlf != 0:
        print("  !! 行尾不是纯 LF（应 CRLF=0）")
        bad += 1
    else:
        print("  (行尾 ✓)")

    over = [(i, len(l)) for i, l in enumerate(lines, 1) if len(l) > maxline]
    if over:
        bad += 1
        for i, n in over:
            print(f"  !! 单行超 {maxline}: line {i} = {n} 字符")
    else:
        mn = max((len(l) for l in lines), default=0)
        print(f"  单行长度 ✓（最长 {mn} ≤ {maxline}）")

    # 表格列数：连续以 | 开头的行成块
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|"):
            blk, j = [], i
            while j < len(lines) and lines[j].strip().startswith("|"):
                blk.append(lines[j])
                j += 1
            pipes = [len(re.findall(r"(?<!\\)\|", x)) for x in blk]
            if len(set(pipes)) > 1:
                bad += 1
                print(f"  !! 表格列数不一致（line {i + 1} 起，pipe 数 {sorted(set(pipes))}）")
                print(f"     表头: {blk[0][:70]}")
            i = j
        else:
            i += 1
    if bad == 0:
        print("  表格列数 ✓")

    if require_terms:
        why = terms_exempt(path)
        if why:
            print(f"  名词解释 —（豁免：{why}）")
        elif TERM_HEAD.search(txt):
            print("  名词解释 ✓（含「名词解释（本文用到的）」小节）")
        else:
            bad += 1
            print("  !! 缺 `## 名词解释（本文用到的）` —— 交付文档必须带"
                  "（模板见 tools/GLOSSARY.md 附 A）")

    print(f"  §引用出现 {len(re.findall('§', txt))} 次（仅打印）")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--maxline", type=int, default=400)
    ap.add_argument("--require-terms", action="store_true",
                    help="要求交付文档含 `§0 名词解释`（见 docstring ⑤；工作碎片/台账/真源自动豁免）")
    a = ap.parse_args()
    rc = 0
    for p in a.files:
        rc |= check(p, a.maxline, a.require_terms)
    print("\n结论：" + ("全部通过 ✓" if rc == 0 else "有告警 ✗"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
