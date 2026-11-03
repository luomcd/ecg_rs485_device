#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_glossary_freq.py —— 统计某篇 md 对 GLOSSARY 符号的**词频**，用于刷新该文
「名词解释（本文用到的）」小节。

依据：doc-term-glossary §4 「选条判据」——
  * 按**本文词频降序**取前 N 条；
  * **同频次按真源定义序**（GLOSSARY 里出现的先后）；
  * 小节标题写的是"**本文**用到的" ⇒ 判据只能是"本文用得多的"，不是真源定义序。

用法：
  python tools/_glossary_freq.py <doc.md> [--top 12] [--section 名词解释] [--quiet]

设计要点（踩过的坑）：
  1. 计数**先取反引号精确匹配**（`dc_block`）—— 本文术语一律反引号包裹，最稳；
     反引号 0 命中时，才退化为字面匹配，且**仅当 token 长度 ≥ 3**（防单字符 `r`/`f` 乱配）。
  2. 统计前**先剔除本文自己的「名词解释」小节**（否则表内符号自计数，把频次刷高）。
  3. 不改任何文件 —— **只读 + 打印**（要改由人/上层脚本决定）。
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GLOSSARY = os.path.join(HERE, "GLOSSARY.md")

# 表头 / 分隔行 / 非符号首格
_SKIP_FIRST = {"符号", "撞名"}


def parse_glossary(path):
    """→ [(order, token, sym, all_tokens)] 按真源出现顺序（**去重**同 token）。"""
    rows = []
    order = 0
    seen = set()
    for line in open(path, encoding="utf-8"):
        s = line.rstrip("\n")
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        sym = cells[0]
        if sym in _SKIP_FIRST or set(sym) <= set("-: "):
            continue
        toks = re.findall(r"`([^`]+)`", sym)
        if not toks:
            toks = [re.sub(r"\*\*", "", sym).strip()]
        if toks[0] in seen:          # 同名（§2 真条目 vs 附 A 示例）只留首次
            continue
        seen.add(toks[0])
        order += 1
        rows.append((order, toks[0], sym, toks))
    return rows


def strip_section(doc, section):
    """剔除 `## <section>…` 到下一个 `## ` 之间（含尾随的 `---` 块）。"""
    m = re.search(r"^##\s*%s.*?$" % re.escape(section), doc, re.M)
    if not m:
        return doc
    start = m.start()
    nxt = re.search(r"^##\s", doc[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(doc)
    return doc[:start] + doc[end:]


def count_token(doc, tok):
    """纯 ASCII 标识符 ⇒ **词边界**匹配（防 `bl`←`bls`、`fc`←`fc_hi`、`STA`←`START`、
    `P1`←`S2-P1`）；其余（中文 / 含 `/`·`()`·`-` 的）⇒ 字面匹配（len ≥ 2）。

    🛑 边界集**必须含连字符**（2026-10-13 修）：否则 `P1` 会误吃掉 `S2-P1` 里的 `P1`
    （`-` 不在 `[A-Za-z0-9_]` 里 ⇒ 被当成词边界），把撞名项的频次算高。
    """
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok):
        return len(re.findall(r"(?<![A-Za-z0-9_-])%s(?![A-Za-z0-9_-])"
                              % re.escape(tok), doc)), "word"
    if len(tok) >= 2:
        return doc.count(tok), "raw"
    return doc.count("`%s`" % tok), "code"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--section", default="名词解释")
    ap.add_argument("--extra", default="",
                    help="额外候选符号（逗号分隔）——用于'真源缺但本文在用'的术语，命中标 *")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    raw = open(a.doc, encoding="utf-8").read()
    doc = strip_section(raw, a.section)

    rows = parse_glossary(GLOSSARY)
    extra = [x.strip() for x in a.extra.split(",") if x.strip()]
    have = {r[1] for r in rows}
    extra = [t for t in extra if t not in have]     # 已在真源的不重复计入
    for k, t in enumerate(extra):
        rows.append((1000 + k, t, t, [t]))      # 序 ≥1000 ⇒ 同频次时排在真源之后

    hits = []
    for order, tok, sym, toks in rows:
        best_n, best_m = 0, "-"
        for t in toks:
            n, m = count_token(doc, t)
            if n > best_n:
                best_n, best_m = n, m
        hits.append((best_n, order, tok, sym, best_m))

    # 词频降序；同频次按真源定义序（稳定）
    hits.sort(key=lambda x: (-x[0], x[1]))

    print("== %s" % os.path.basename(a.doc))
    print("   节名称剔除「%s」后正文字符 %d（原 %d）" % (a.section, len(doc), len(raw)))
    print("   GLOSSARY 符号 %d 条 ＋ 额外 %d 条；命中（≥1）%d 条"
          % (len(rows) - len(extra), len(extra), sum(1 for h in hits if h[0])))
    print()
    print("   频次  序   匹配  符号（主 token）        *＝不在真源")
    print("   ----  ---  ----  ----------------------  ----------")
    for n, order, tok, sym, m in hits[: max(a.top, 0) or len(hits)]:
        if n == 0:
            break
        mark = "*" if order >= 1000 else " "
        print("   %4d  %3d  %-4s  %s%s" % (n, order, m, tok, mark))
    print()
    if not a.quiet:
        zero = [h[2] for h in hits if h[0] == 0]
        print("   未命中（%d 条）：%s" % (len(zero), "／".join(zero[:20]) + (" …" if len(zero) > 20 else "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
