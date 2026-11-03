#!/usr/bin/env python3
"""
单文件语法/类型预检 —— 在全量 build 之前抓代码错误。

为什么需要：ESP-IDF 冷构建 25~50 min，而 main 组件依赖其他所有组件、排在最后编，
main.c 里一个拼写错误要等 40 min 才暴露。本工具把发现时间压到**几秒**。

原理：
  1. 从 build/compile_commands.json 取出目标文件的**真实编译命令**（含全部 -I / -D）
  2. 去掉 -o <file> 与 -c，加上 -fsyntax-only
  3. 写进响应文件 .rsp 后用 @file 执行

  为什么要响应文件：main.c 的完整命令约 8.9 K 字符，超过 Windows 命令行 8191 上限。
  为什么路径要换 `/`：GCC 读响应文件时会处理反斜杠转义，
   `C:\\a\\b.c` 会变成 `C:ab.c`；但 `-DIDF_VER=\\"v6.1.0\\"` 里的 `\\"` 必须保留。

前置条件：先跑过一次 `idf.sh reconfigure`（生成 compile_commands.json）。

用法：
    python tools/syntax_check.py cardiolite-ecg main/main.c
    python tools/syntax_check.py cardiolite-ecg main/main.c main/storage/storage_sd.c
    python tools/syntax_check.py cardiolite-ecg --all      # 检查本工程全部 C/C++ 源

退出码：0 = 全通过；1 = 有文件报错或找不到编译命令。
"""

import json
import os
import re
import shlex
import subprocess
import sys

# 本地解释器优先（系统 python 可能缺依赖；本脚本只用了标准库，但保持一致）
PY = sys.executable


def load_commands(build_dir):
    path = os.path.join(build_dir, "compile_commands.json")
    if not os.path.isfile(path):
        sys.exit(f"[错误] 找不到 {path}\n"
                 f"       先运行: ./tools/idf.sh -C <project> reconfigure")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def norm(p):
    return p.replace("\\", "/")


def protect_quotes(arg):
    """把路径反斜杠换成 /，但不碰已转义的 \\" """
    token = arg.replace('\\"', "\x00")
    token = token.replace("\\", "/")
    return token.replace("\x00", '\\"')


def build_syntax_cmd(entry):
    """从一条 compile_commands 记录构造 -fsyntax-only 命令参数列表。"""
    args = shlex.split(entry["command"], posix=False)
    compiler, rest = args[0], args[1:]

    out = []
    skip_next = False
    for a in rest:
        if skip_next:
            skip_next = False
            continue
        if a == "-o":          # 去掉输出文件
            skip_next = True
            continue
        if a == "-c":          # 去掉「只编译不链接」
            continue
        out.append(protect_quotes(a))
    out.append("-fsyntax-only")
    return compiler, out


def check_one(entries, want_suffix, build_dir):
    hit = [e for e in entries if norm(e["file"]).endswith(norm(want_suffix))]
    if not hit:
        print(f"[跳过] {want_suffix} —— compile_commands.json 里没有它的编译命令")
        print("       新加的文件需要先 reconfigure 才会出现")
        return None

    entry = hit[0]
    compiler, args = build_syntax_cmd(entry)

    # ⚠ 必须给 gcc 一个**绝对路径且用正斜杠**的响应文件路径：
    #   1) 相对路径会相对于 entry["directory"](= build/) 解析，用 project 相对路径会错位
    #   2) 反斜杠会被 gcc 当转义，导致路径无效，于是 @file 被当成链接输入 →
    #      报 "ld.exe: cannot find @xxx.rsp"，表现为 -fsyntax-only 完全没生效
    rsp = norm(os.path.abspath(os.path.join(build_dir, "_syntax_check.rsp")))
    with open(rsp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(args))

    print(f"[检查] {want_suffix}  ({len(args)} 个参数, 命令原长 {len(entry['command'])})")
    r = subprocess.run([compiler, "@" + rsp], cwd=entry["directory"],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode == 0 and not out:
        print("       通过")
        return True
    print("       失败:")
    for line in out.splitlines():
        print("         " + line)
    return False


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    project = sys.argv[1]
    build_dir = os.path.join(project, "build")
    entries = load_commands(build_dir)

    targets = sys.argv[2:]
    if targets == ["--all"]:
        # 只查本工程自己的源文件（排除 managed_components 与 IDF）
        proj_abs = norm(os.path.abspath(project))
        targets = []
        for e in entries:
            f = norm(e["file"])
            if f.startswith(proj_abs) and "/managed_components/" not in f:
                targets.append(os.path.relpath(e["file"], project))

    results = {}
    for t in targets:
        results[t] = check_one(entries, t, build_dir)

    print()
    bad = [k for k, v in results.items() if v is False]
    skip = [k for k, v in results.items() if v is None]
    ok = [k for k, v in results.items() if v is True]

    print(f"结果: 通过 {len(ok)} / 失败 {len(bad)} / 跳过 {len(skip)}")
    if bad:
        print("失败文件: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
