# -*- coding: utf-8 -*-
"""
_size_delta.py —— 固件落码的体积增量：**双次构建对照**（通用版）
================================================================================

为什么不能引用历史数字：台账里登记的增量都是"上一版相对更上一版"的，
本次落码的增量只能由**同一次工具链、同一份其它源码、只切换本次改动的那几个文件**
的对照构建给出。

用法（当前工作区 = NEW 版，`--old-dir` 里同名文件 = OLD 版）::

  python tools/_size_delta.py \
      --new-label G5 --old-label G4 \
      --old-dir tools/_bak_T116_20260923 \
      --new Core/Src/vcomcmd.c Ads1293/ads1293.c Ads1293/ads1293.h \
      --watch ads1293_write_table ads1293_reset_afe vcom_bin_dispatch vcom_recv_analysis

流程（全程 md5 校验，末了必须把产物与源码恢复成 NEW 版并复验）：
  1. 当前三文件另存 `_size_delta_new/`
  2. 构建 → 记体积（含 map 里 watch 符号的函数大小）
  3. 换成 OLD 版 → 构建 → 记同样三项
  4. 恢复 NEW 版 → 再构建一次（别把 OLD 的 hex 留在 build/ 里）
  5. 校验 md5 回到 NEW 版的值
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools")
PY = sys.executable
NEWDIR = os.path.join(TOOL, "_size_delta_new")
MAP = os.path.join(ROOT, "EWARM", "demo_ads1293", "build", "demo_ads1293",
                   "demo_ads1293.map")


def md5(path: str) -> str:
    return hashlib.md5(io.open(path, "rb").read()).hexdigest()


def build() -> str:
    r = subprocess.run([PY, os.path.join(TOOL, "_fw_build.py")], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def sizes(txt: str):
    m = re.search(r"只读代码\s+(\d+)\s*B.*?只读数据\(rodata\)\s+(\d+)\s*B.*?读写数据\s+(\d+)\s*B",
                  txt, re.S)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def func_sizes() -> dict:
    """从 IAR map 抠函数大小：<符号>  0x地址  0x大小  Code  <段>  <对象>"""
    if not os.path.exists(MAP):
        return {}
    t = io.open(MAP, encoding="utf-8", errors="replace").read()
    d = {}
    for m in re.finditer(r"^\s*(\w+)\s+0x[0-9a-fA-F]+\s+0x([0-9a-fA-F]+)\s+Code", t, re.M):
        d[m.group(1)] = max(d.get(m.group(1), 0), int(m.group(2), 16))
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new-label", required=True)
    ap.add_argument("--old-label", required=True)
    ap.add_argument("--old-dir", required=True)
    ap.add_argument("--new", nargs="+", required=True, help="相对仓库根的路径")
    ap.add_argument("--watch", nargs="*", default=[])
    a = ap.parse_args()

    old_dir = os.path.join(ROOT, a.old_dir) if not os.path.isabs(a.old_dir) else a.old_dir
    if not os.path.isdir(old_dir):
        print("🛑 备份目录不存在：%s" % old_dir)
        return 2

    # 0) 计划盘点 + 文件是否都存在
    plan = []
    for rel in a.new:
        cur = os.path.join(ROOT, rel.replace("/", os.sep))
        old = os.path.join(old_dir, os.path.basename(rel))
        if not os.path.exists(cur):
            print("🛑 当前文件不存在：%s" % rel)
            return 2
        if not os.path.exists(old):
            print("🛑 备份里没有：%s" % os.path.basename(rel))
            return 2
        plan.append((rel, cur, old))

    os.makedirs(NEWDIR, exist_ok=True)
    new_saves = []
    for rel, cur, old in plan:
        save = os.path.join(NEWDIR, os.path.basename(rel))
        shutil.copyfile(cur, save)
        new_saves.append((rel, cur, save, old))
        same = md5(cur) == md5(old)
        print("· %-24s NEW md5=%s  OLD md5=%s  %s"
              % (rel, md5(cur)[:8], md5(old)[:8], "⚠ 两者相同（本文件本次没改？）" if same else ""))

    res = {}

    def one(tag: str, chooser):
        for rel, cur, save, old in new_saves:
            shutil.copyfile(chooser(save, old, cur), cur)
        print("\n=== 构建 %s 版…" % tag)
        out = build()
        sz = sizes(out)
        fs = func_sizes()
        if sz is None:
            print("🛑 没能解析体积 ⇒ 构建可能失败。尾部输出：")
            print(out[-1500:])
            return False
        res[tag] = (sz, fs)
        print("    ROM(code)=%d  rodata=%d  RAM(rw)=%d" % sz)
        for f in a.watch:
            if f in fs:
                print("    fn %-24s = %5d B" % (f, fs[f]))
        return True

    if not one(a.new_label, lambda save, old, cur: save):
        return 3
    if not one(a.old_label, lambda save, old, cur: old):
        return 3

    # 恢复 NEW 并重建
    for rel, cur, save, old in new_saves:
        shutil.copyfile(save, cur)
    print("\n=== 恢复 %s 并重建…" % a.new_label)
    out = build()
    ok = (sizes(out) == res[a.new_label][0]) and all(
        md5(cur) == md5(save) for rel, cur, save, old in new_saves)
    print("    恢复校验：md5 %s｜体积与首次一致 %s"
          % ("✓" if all(md5(cur) == md5(save) for _, cur, save, _ in new_saves) else "✗",
             "✓" if sizes(out) == res[a.new_label][0] else "✗"))

    (cn, rn, wn), fn = res[a.new_label]
    (co, ro, wo), fo = res[a.old_label]

    print()
    print("=" * 70)
    print("%s 体积增量（%s − %s，同工具链同源码对照）" % (a.new_label, a.new_label, a.old_label))
    print("=" * 70)
    print("  只读代码 (ROM, code) : %6d − %6d = %+5d B" % (cn, co, cn - co))
    print("  只读数据 (rodata)    : %6d − %6d = %+5d B" % (rn, ro, rn - ro))
    print("  读写数据 (RAM)       : %6d − %6d = %+5d B" % (wn, wo, wn - wo))
    print("  ROM 合计             : %6d − %6d = %+5d B" % (cn + rn, co + ro, (cn + rn) - (co + ro)))
    print()
    for f in a.watch:
        x, y = fn.get(f), fo.get(f)
        if x is not None and y is not None:
            print("  fn %-24s %5d − %5d = %+5d B" % (f, x, y, x - y))
        elif x is not None:
            print("  fn %-24s %5d B（%s 版无此符号 ⇒ 新增）" % (f, x, a.old_label))
        elif y is not None:
            print("  fn %-24s %5d B（%s 版已删除）" % (f, y, a.new_label))

    print()
    if wn == wo:
        print("结论：RAM **零增量** ✓")
    else:
        print("结论：⚠ RAM %+d B —— 必须说明来源" % (wn - wo))
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
