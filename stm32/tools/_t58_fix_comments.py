# -*- coding: utf-8 -*-
"""
_t58_fix_comments.py —— 同步 Filter/ecg_filter.c 里的档位注释（T58 之后）

⚠ 为什么要**逐条精确定位**、不能全局替换 "N 节"：
   本文件里 "N 节" 有 20+ 处，其中 10+ 处是**历史记录**（如 "代价：8→9 节"、
   "9 节以上全是外推"、"CPU 6 节 4.884% → 8 节 6.507%"、"Q8 / 4 节 50Hz 簇 / 5 节 50Hz 簇"），
   全局减 1 会把它们统统写错 ⇒ 这类注释错误比系数错误更隐蔽（编译不报错）。

只改两类：
  ① 频率字样 `0.05~150Hz` -> `0.05~250Hz`（10 处：顶部档位表 5 + 块头 5）
  ② **档位节数标记**：`[N 节]` / `（N 节）` 中属于档位总数的那 13 处
     N ∈ {13,9,8,6,5} 的**括号形式**（`[x 节]` 与 `（x 节）`）；
     历史的"8→9 节"、"6 节 4.884%"、"4 节 50Hz 簇"、"链尾 4 节" 等不在此列。
"""
import io
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(HERE, '..', 'Filter', 'ecg_filter.c')

RULES = [
    # ① 频率字样（顶部档位表 + 五个块头，共 10 处）—— 幂等，不产生新匹配
    ("0.05~150Hz", "0.05~250Hz", 10),
    # ② 链尾 BP 节数（T48b 瞬态机制描述）
    ("BP 节（链尾 4 节）", "BP 节（链尾 3 节）", 1),
    ("后级 4 节 BP", "后级 3 节 BP", 1),
]

# ② 节数标记：必须**单遍**替换。
#   ⚠ 若逐条 str.replace（先 9→8 再 8→7），刚由 9 生成的 8 会被第二次再减 1 ⇒ 重入错误。
SEC_MAP = {"13": "12", "9": "8", "8": "7", "6": "5", "5": "4"}
PAT_SEC = [                                  # (正则, 模板, 各 N 的期望处数)
    (r"\[(\d+) 节\]", "[%s 节]",
     {"13": 1, "9": 1, "8": 1, "6": 1, "5": 1}),
    (r"（(\d+) 节）", "（%s 节）",
     {"13": 2, "5": 1, "6": 1, "8": 1, "9": 1}),
]

# 反证标记：这些**历史记录**必须原样保留
KEEP = ["代价：8→9 节", "9 节以上全是外推", "CPU 6 节 4.884% → 8 节 6.507%",
        "Q8 / 4 节 50Hz 簇 / 5 节 50Hz 簇", "f64/f32 各 8/8 节 0 ULP",
        "第 3~6 节：0.05~40 Hz", "[7 节]"]


def main():
    raw = io.open(P, 'rb').read()
    crlf = b'\r\n' in raw
    s = raw.decode('utf-8')
    if crlf:
        s = s.replace('\r\n', '\n')

    print("替换计数：")
    bad = False
    for old, new, exp in RULES:
        n = s.count(old)
        if n != exp:
            bad = True
        print("  %-22s -> %-22s  %2d 处（期望 %d）%s"
              % (old, new, n, exp, "" if n == exp else "  !! "))
        s = s.replace(old, new)

    for pat, tmpl, exp in PAT_SEC:
        hits = []

        def _sub(m, tmpl=tmpl, hits=hits):
            n = m.group(1)
            if n in SEC_MAP:
                hits.append(n)
                return tmpl % SEC_MAP[n]
            return m.group(0)

        s = re.sub(pat, _sub, s)

        c = Counter(hits)
        for n, v in exp.items():
            got = c.get(n, 0)
            if got != v:
                bad = True
            print("  %-22s -> %-22s  %2d 处（期望 %d）%s"
                  % (pat.replace(r"(\d+)", n), tmpl % SEC_MAP[n], got, v,
                     "" if got == v else "  !! "))

    for mk in KEEP:
        if mk not in s:
            print("  !! 历史记录被误改：%s" % mk)
            bad = True
    print("  历史记录抽查 %d 项 %s" % (len(KEEP), "完好 ✓" if not bad else "有问题 ✗"))

    if bad:
        print("⇒ **未写盘**")
        return 1

    if crlf:
        s = s.replace('\n', '\r\n')
    io.open(P, 'wb').write(s.encode('utf-8'))
    print("⇒ 已写入 %s" % os.path.relpath(P))
    return 0


if __name__ == '__main__':
    sys.exit(main())
