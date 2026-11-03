# -*- coding: utf-8 -*-
"""_mirror_sync.py -- 镜像文档同步（T194 起）

用途：某些文档在两个工作目录各存一份（如「原理梳理」同时在
``ads1293_demo\\tools\\`` 与 ``CodexWKspace\\ads1293_filter\\``），
必须**逐字节相同**。本工具是唯一的「判等 + 推平」入口，避免手工 cp 漏改一侧。

用法::

    python tools/_mirror_sync.py --check        # 只比 md5（exit 1 = 不一致）
    python tools/_mirror_sync.py --push         # 源 -> 目标（目标先备份）
    python tools/_mirror_sync.py --push --pair 0

判据：两侧文件名**不同是刻意的**，只看内容 md5 是否相等。
"""
import argparse
import hashlib
import os
import shutil
import sys
from datetime import datetime

# (显示名, 源＝真源侧, 目标)
PAIRS = [
    (
        "T194 滤波链原理梳理",
        r"C:\work2026\VsPro\ads1293_demo\tools\T194_滤波链原理梳理_2026-10-13.md",
        r"C:\work2026\CodexWKspace\ads1293_filter\滤波链原理梳理_2026-10-13.md",
    ),
]

BAK_ROOT = r"C:\work2026\VsPro\ads1293_demo\tools\_bak_mirror"


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def show(name, src, dst):
    """打印两侧状态；返回 (是否一致, 源存在, 目标存在)。"""
    se, de = os.path.isfile(src), os.path.isfile(dst)
    print("== %s" % name)
    if not se:
        print("   !! 源不存在:   %s" % src)
    if not de:
        print("   !! 目标不存在: %s" % dst)
    if not (se and de):
        return False, se, de
    a, b = md5_of(src), md5_of(dst)
    same = a == b
    print("   源   %9d B  md5 %s" % (os.path.getsize(src), a))
    print("   目标 %9d B  md5 %s" % (os.path.getsize(dst), b))
    print("   %s" % ("一致 OK" if same else "不一致 -- 需 --push"))
    return same, se, de


def push(name, src, dst):
    if not os.path.isfile(src):
        print("   !! 源不存在，跳过: %s" % src)
        return
    if os.path.isfile(dst):
        stamp = datetime.now().strftime("%Y%m%d")
        bak = os.path.join(BAK_ROOT, stamp)
        os.makedirs(bak, exist_ok=True)
        target = os.path.join(bak, os.path.basename(dst))
        shutil.copy2(dst, target)
        print("   已备份旧目标 -> %s" % target)
    else:
        print("   目标不存在 -> 新建（无需备份）")
    d = os.path.dirname(dst)
    if d:
        os.makedirs(d, exist_ok=True)
    shutil.copy2(src, dst)
    print("   已推送 %s -> %s" % (os.path.basename(src), dst))


def main():
    ap = argparse.ArgumentParser(description="镜像文档同步（判等 / 推平）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="只比 md5")
    g.add_argument("--push", action="store_true", help="源 -> 目标（目标先备份）")
    ap.add_argument("--pair", type=int, default=None, help="只处理第 N 对（默认全部）")
    args = ap.parse_args()

    if args.pair is not None and not (0 <= args.pair < len(PAIRS)):
        print("!! --pair 越界（共 %d 对）" % len(PAIRS))
        return 2

    pairs = PAIRS if args.pair is None else [PAIRS[args.pair]]
    bad = 0
    for name, src, dst in pairs:
        if args.push:
            push(name, src, dst)
        ok, _, _ = show(name, src, dst)
        if not ok:
            bad += 1

    head = "推送后" if args.push else ""
    print("\n结论：%s" % ("全部一致 OK" if bad == 0 else "%s有 %d 对不一致" % (head, bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
