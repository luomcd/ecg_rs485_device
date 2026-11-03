#!/usr/bin/env python3
"""
检查源码里的中文是否都在字库字集里 —— 防「方块」再犯。

背景：S6 首版上板后，按钮副行出现方块（面/缓/冲/中/完/将/被/不/可 共 9 个字没进字集）。
这类问题**编译期发现不了**（字库缺字不会报错，运行时画成方框），只能靠实机目视。
本脚本把这一步提前到改代码时。

做法：
  1. 从 tools/gen_fonts.sh 里提取 SYMBOLS（字集）
  2. 扫 cardiolite-ecg/main/**/*.c，提取 C 字符串里的中文字符
     （跳过 ESP_LOG* 行 —— 那些走串口，不需要字库）
  3. 差集就是缺字

用法:
    python tools/check_font_chars.py            # 检查
    python tools/check_font_chars.py --list     # 额外列出每处出处

退出码: 0 = 全有；1 = 有缺字。
"""

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(ROOT, "tools", "gen_fonts.sh")
SRC_DIR = os.path.join(ROOT, "cardiolite-ecg", "main")

# CJK 统一表意文字 + 常用中文标点
CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u2018\u2019\u201c\u201d\u2014\u00b7]")


def load_symbols():
    txt = open(GEN, encoding="utf-8").read()
    m = re.search(r'^SYMBOLS="([^"]*)"', txt, re.MULTILINE)
    if not m:
        sys.exit("[错误] gen_fonts.sh 里找不到 SYMBOLS= 定义")
    return set(m.group(1))


def strip_comments(text):
    """剥掉 C 注释，但**保留换行**以维持行号。

    为什么必须剥：注释里常带引号，例如
        * 所以没有"任务结束"可挂回调
    这些中文并不上屏，不剥就会误报缺字（第一版脚本就误报了 33 个）。
    """
    text = re.sub(r'/\*.*?\*/', lambda m: "\n" * m.group(0).count("\n"),
                  text, flags=re.DOTALL)
    text = re.sub(r'//[^\n]*', '', text)
    return text


def scan_sources():
    """返回 {字符: [出处 "文件:行号"]}"""
    found = {}
    for path in glob.glob(os.path.join(SRC_DIR, "**", "*.c"), recursive=True):
        if os.sep + "font" + os.sep in path:
            continue                      # 跳过生成的字体文件
        raw = open(path, encoding="utf-8", errors="replace").read()
        code = strip_comments(raw)
        for i, line in enumerate(code.split("\n"), 1):
            # 这些行的中文不上屏，不查字库：
            #   ESP_LOG*      —— 走串口
            #   *Static_assert —— 编译期断言失败才显示
            if any(k in line for k in ("ESP_LOG", "Static_assert", "static_assert")):
                continue
            # 只取双引号里的内容
            for lit in re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', line):
                for ch in lit:
                    if CJK.match(ch):
                        found.setdefault(ch, []).append(
                            f"{os.path.relpath(path, ROOT)}:{i}")
    return found


def main():
    show_list = "--list" in sys.argv
    symbols = load_symbols()
    found = scan_sources()

    used = sorted(found.keys())
    missing = [c for c in used if c not in symbols]
    unused = sorted(symbols - set(used))

    print(f"字集（gen_fonts.sh）: {len(symbols)} 个字符")
    print(f"源码实际用到        : {len(used)} 个")
    print()

    if unused:
        print(f"字集里但源码没用到的（{len(unused)} 个，可留可删）:")
        print("  " + "".join(unused))
        print()

    if not missing:
        print("✅ 没有缺字：源码里所有中文都在字集内")
        return 0

    print(f"❌ 缺 {len(missing)} 个字（会显示成方块）:")
    print("  " + "".join(missing))
    print()
    print("出处:")
    for ch in missing:
        locs = found[ch]
        head = locs[0] if locs else "?"
        more = f" 等 {len(locs)} 处" if len(locs) > 1 else ""
        print(f"  「{ch}」 {head}{more}")
        if show_list and len(locs) > 1:
            for l in locs[1:]:
                print(f"        {l}")
    print()
    print("修法：把这些字加进 tools/gen_fonts.sh 的 SYMBOLS，再跑 ./tools/gen_fonts.sh")
    return 1


if __name__ == "__main__":
    sys.exit(main())
