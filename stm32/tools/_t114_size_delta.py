# -*- coding: utf-8 -*-
"""
_t114_size_delta.py —— G4 落码的体积增量：**双次构建对照**（G3 ↔ G4）
================================================================================

为什么不能引用历史数字：T106 登记的 "+350 B" 是 **G3 相对 G2** 的增量。
G4 的增量只能由**同一次工具链、同一份其它源码、只切换 `vcomcmd.c`** 的对照构建给出。

流程（全程 md5 校验，末了必须恢复 G4 版并复验）：
  1. 当前 `Core/Src/vcomcmd.c` 存一份 `vcomcmd_g4.c`（落码后的真身）
  2. 构建 → 记体积 ＋ 从 map 抠 `vcom_bin_dispatch` / `vcom_recv_analysis` 的函数大小
  3. 换成 G3 版（`_bak_T114_20260923/vcomcmd.c` = 落码前备份）→ 构建 → 记同样三项
  4. 恢复 G4 版 → 再构建一次（把产物恢复成 G4，别把 G3 的 hex 留在 build/ 里）
  5. 校验 `vcomcmd.c` 的 md5 回到落码后的值

用法:
  python _t114_size_delta.py
"""
from __future__ import annotations

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

VCOM = os.path.join(ROOT, "Core", "Src", "vcomcmd.c")
BAK = os.path.join(TOOL, "_bak_T114_20260923")
G3 = os.path.join(BAK, "vcomcmd.c")          # 落码前备份 ⇒ G3 版
G4 = os.path.join(BAK, "vcomcmd_g4.c")       # 落码后另存 ⇒ G4 版
MAP = os.path.join(ROOT, "EWARM", "demo_ads1293", "build", "demo_ads1293", "demo_ads1293.map")

WATCH = ("vcom_bin_dispatch", "vcom_recv_analysis", "vcom_test_analysis")


def md5(path: str) -> str:
    return hashlib.md5(io.open(path, "rb").read()).hexdigest()


def build() -> str:
    r = subprocess.run([PY, os.path.join(TOOL, "_fw_build.py")], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def sizes(txt: str) -> tuple[int, int, int] | None:
    m = re.search(r"只读代码\s+(\d+)\s*B.*?只读数据\(rodata\)\s+(\d+)\s*B.*?读写数据\s+(\d+)\s*B",
                  txt, re.S)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def func_sizes() -> dict[str, int]:
    """从 IAR map 抠函数大小：<符号>  0x地址  0x大小  Code  <段>  <对象>"""
    if not os.path.exists(MAP):
        return {}
    t = io.open(MAP, encoding="utf-8", errors="replace").read()
    d = {}
    for m in re.finditer(r"^\s*(\w+)\s+0x[0-9a-fA-F]+\s+0x([0-9a-fA-F]+)\s+Code",
                         t, re.M):
        d[m.group(1)] = max(d.get(m.group(1), 0), int(m.group(2), 16))
    return d


def main() -> int:
    if not os.path.exists(G4):
        shutil.copyfile(VCOM, G4)
        print(f"· 已存 G4 版 → {os.path.relpath(G4, ROOT)}")
    m_g4, m_g3 = md5(G4), md5(G3)
    print(f"· G4 版 md5 = {m_g4}")
    print(f"· G3 版 md5 = {m_g3}")
    if md5(VCOM) not in (m_g4, m_g3):
        print("🛑 当前 vcomcmd.c 既不是 G3 也不是 G4 版 ⇒ 停手（先弄清工作区状态）")
        return 2

    res: dict[str, tuple] = {}

    for tag, src in (("G4", G4), ("G3", G3)):
        shutil.copyfile(src, VCOM)
        print(f"\n=== 构建 {tag} 版（vcomcmd.c md5={md5(VCOM)[:8]}）…")
        out = build()
        sz = sizes(out)
        fs = func_sizes()
        if sz is None:
            print("🛑 没能解析体积 ⇒ 构建可能失败。尾部输出：")
            print(out[-1500:])
            return 3
        res[tag] = (sz, fs)
        print(f"    ROM(code)={sz[0]}  rodata={sz[1]}  RAM(rw)={sz[2]}")
        for f in WATCH:
            if f in fs:
                print(f"    fn {f:22s} = {fs[f]:5d} B")

    # 恢复 G4 并重建（别把 G3 的 hex 留在 build/ 里）
    shutil.copyfile(G4, VCOM)
    print(f"\n=== 恢复 G4 并重建（md5={md5(VCOM)[:8]}）…")
    out = build()
    ok_restore = (md5(VCOM) == m_g4) and (sizes(out) == res["G4"][0])
    print(f"    恢复校验：md5 {'✓' if md5(VCOM) == m_g4 else '✗'}｜体积与首次 G4 一致 "
          f"{'✓' if sizes(out) == res['G4'][0] else '✗'}")

    (c4, r4, w4), f4 = res["G4"]
    (c3, r3, w3), f3 = res["G3"]

    print()
    print("=" * 70)
    print("G4 体积增量（G4 − G3，同工具链同源码对照）")
    print("=" * 70)
    print(f"  只读代码 (ROM, code) : {c4:6d} − {c3:6d} = {c4 - c3:+5d} B")
    print(f"  只读数据 (rodata)    : {r4:6d} − {r3:6d} = {r4 - r3:+5d} B")
    print(f"  读写数据 (RAM)       : {w4:6d} − {w3:6d} = {w4 - w3:+5d} B")
    print(f"  ROM 合计             : {c4 + r4:6d} − {c3 + r3:6d} = {(c4 + r4) - (c3 + r3):+5d} B")
    print()
    for f in WATCH:
        a, b = f4.get(f), f3.get(f)
        if a is not None and b is not None:
            print(f"  fn {f:22s} {a:5d} − {b:5d} = {a - b:+5d} B")
        elif a is not None:
            print(f"  fn {f:22s} {a:5d} B（G3 版无此符号 ⇒ 新增）")

    print()
    print("结论：", end="")
    if w4 == w3:
        print(f"RAM **零增量** ✓（dispatch 无新增静态数据：用的是 G3 已有的 "
              f"ads1293_send_snapshot 的 static 帧缓冲）")
    else:
        print(f"⚠ RAM 增加 {w4 - w3} B —— 需说明来源")
    return 0 if ok_restore else 4


if __name__ == "__main__":
    sys.exit(main())
