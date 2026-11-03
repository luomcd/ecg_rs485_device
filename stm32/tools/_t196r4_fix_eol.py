# -*- coding: utf-8 -*-
"""_t196r4_fix_eol.py -- 行尾归一（转 LF），T196r4 体检修复（2026-10-14）

背景：T196r4「单步测试全 11 步体检」发现 2 个 md 含孤立 CRLF
（S0 复盘 1 处 L218；`单步测试S0-S7全面评估报告_2026-10-13.md` 4 处 L138/139/176/177）。
本脚本把指定文件的 `\\r\\n` 一律转为 `\\n`，并清掉孤立 `\\r`。

安全约定（遵从 `_safe_write` 的纪律，但把备份统一落到本轮备份目录）：
  写同目录临时文件 → 内容自检 → 备份原文件 → `os.replace` 原子替换；
  新内容比旧内容短 > 50% 则拒绝（防御"写一半"）。
  🛑 绝不 `open(path, 'w')` 就地截断。

用法：
    python tools/_t196r4_fix_eol.py <file> [<file> ...]
"""
import os
import shutil
import sys
import tempfile

BAK_DIR = r"C:/work2026/VsPro/ads1293_demo/tools/_bak_T196r4_20261014/eol_before"


def fix_one(path):
    raw = open(path, "rb").read()
    n_cr = raw.count(b"\r")
    if n_cr == 0:
        print("跳过（无 CR）：%s" % path)
        return 0
    text = raw.decode("utf-8")
    new_text = text.replace("\r\n", "\n").replace("\r", "")
    if len(new_text) < len(text) * 0.5:
        print("!! 缩水 > 50%%，拒绝写入：%s" % path)
        return 1
    os.makedirs(BAK_DIR, exist_ok=True)
    shutil.copy2(path, os.path.join(BAK_DIR, os.path.basename(path)))
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix="_tmp_eol_", suffix=".md")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    after = open(path, "rb").read()
    print("已归一：%s" % path)
    print("   CR %d -> %d   CRLF %d -> %d   字节 %d -> %d"
          % (n_cr, after.count(b"\r"), raw.count(b"\r\n"),
             after.count(b"\r\n"), len(raw), len(after)))
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    rc = 0
    for p in argv[1:]:
        if not os.path.isfile(p):
            print("!! 不存在：%s" % p)
            rc = 1
            continue
        rc |= fix_one(p)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
