#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mitrun.py —— 板端 `mit-run <rec> --speed N` 抽样跑测 + 峰序列回传

与 `mit-set`/`mit-run-all`（batch，黑屏全速）的分工
--------------------------------------------------
| 路径 | 屏幕 | 速度 | 输入序列 | 用途 |
|---|---|---|---|---|
| batch  | 黑屏 | 全速（约 195×） | **逐样本同步喂**，无环缓 | 逐峰对账的**权威**路径 |
| mit-run | 有波形 | 1/2/4/8× | 走 pipeline + 环缓，**可能丢样本** | 抽样验证「倍速回放」是否等效 |

🛑 `mit-run` 的指标只有在 `FILE` 行报 `dropped=0` 时才可用于对账。
   今天实测：1× 也丢过 23%（`fed=77700 / pushed=101075`），8× 反而这一跑是 0。
   **丢不丢取决于当时 UI 负载，与倍速不构成单调关系** —— 必须逐次读 `dropped=`。

用法
----
    py mitrun.py --records 100,105,203,208 --speed 8
    py mitrun.py --records 108 --speed 1 --outdir _board_mitrun1x
"""
import argparse
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from s9a_link_check import EspPort      # noqa: E402
from esp_run import dump_file           # noqa: E402


def run_one(e, rec, speed, outdir):
    """跑一条 → 等 FILE 行 → 回传峰序列。返回 dict"""
    tag = "%s_%dx" % (rec, speed)
    print("\n===== %s =====" % tag, flush=True)
    e.take()
    e.ser.write(("mit-run %s --speed %d\r\n" % (rec, speed)).encode())
    e.ser.flush()

    acc = bytearray()
    t0 = time.time()
    while time.time() - t0 < 420:                 # 30 min 记录 @1x 需要 1806 s，本工具按 8x 设计
        acc.extend(e.take())
        if b"FILE mit_" in acc and b"dropped=" in acc:
            time.sleep(0.6)
            acc.extend(e.take())
            break
        time.sleep(0.15)
    txt = acc.decode("utf-8", "replace")
    wall = time.time() - t0

    m = re.search(r"FILE (\S+) frames=(\d+) samples=(\d+) peaks=(\d+) bpm=([\d.]+)", txt)
    d = re.search(r"dropped=(\d+)\s+speed=(\d+)", txt)
    info = dict(rec=rec, speed=speed, wall=wall,
                frames=int(m.group(2)) if m else -1,
                samples=int(m.group(3)) if m else -1,
                peaks=int(m.group(4)) if m else -1,
                bpm=m.group(5) if m else "?",
                dropped=int(d.group(1)) if d else -1)
    print("  frames=%d samples=%d peaks=%d bpm=%s dropped=%d  墙钟 %.0f s"
          % (info["frames"], info["samples"], info["peaks"], info["bpm"],
             info["dropped"], wall), flush=True)

    data = dump_file(e, "mit_%s_result.txt" % rec, timeout=180)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "%s.txt" % tag)
    open(path, "w", encoding="utf-8", newline="\n").write(data)
    # 串口日志也留一份，便于回溯 dropped / 进度
    open(os.path.join(outdir, "%s.log" % tag), "w", encoding="utf-8", newline="\n").write(txt)
    print("  峰序列 -> %s（%d 字符）" % (path, len(data)), flush=True)
    info["file"] = path
    return info


def main():
    ap = argparse.ArgumentParser(description="板端 mit-run 抽样跑测（任意倍速）")
    ap.add_argument("--records", required=True, help="逗号分隔记录号，如 100,105,208")
    ap.add_argument("--speed", type=int, default=8, choices=[1, 2, 4, 8])
    ap.add_argument("--outdir", default="_board_mitrun")
    a = ap.parse_args()

    recs = [r.strip() for r in a.records.split(",") if r.strip()]
    e = EspPort("COM35")
    time.sleep(0.5)
    print("端口就绪，共 %d 条 @ %dx" % (len(recs), a.speed), flush=True)

    rows = []
    for rec in recs:
        try:
            rows.append(run_one(e, rec, a.speed, a.outdir))
        except Exception as ex:
            print("  ✗ %s 失败: %s" % (rec, ex), flush=True)
            rows.append(dict(rec=rec, speed=a.speed, wall=0, frames=-1, samples=-1,
                             peaks=-1, bpm="?", dropped=-1, file=""))
    e.close()

    print("\n" + "=" * 76)
    print("%-6s %8s %8s %8s %7s %8s %9s" % ("记录", "frames", "samples", "peaks", "bpm", "dropped", "墙钟 s"))
    print("-" * 76)
    for r in rows:
        print("%-6s %8d %8d %8d %7s %8d %9.0f"
              % (r["rec"], r["frames"], r["samples"], r["peaks"], r["bpm"], r["dropped"], r["wall"]))
    bad = [r["rec"] for r in rows if r["dropped"] != 0]
    print("\ndropped!=0 的记录：%s" % (", ".join(bad) if bad else "无（全部可作对账）"))


if __name__ == "__main__":
    sys.exit(main())
