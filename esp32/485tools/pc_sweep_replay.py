#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pc_sweep_replay.py —— PC 侧「多心率采集 + 离线算法对比」一条龙（T223 Path B 的验收台）

它把三件容易做砸的事捆在一起：
  1. 下发 START（PC 的 485-USB 直接给 STM32 发 `0x01`，不需要 ESP32）；
  2. 抓 N 秒原始帧流，落盘成带心率/配置标记的唯一文件名；
  3. 用 **同一份样本** 跑多组算法参数（filt_hist × fid_mode），出横向对比表。

🛑 为什么必须用同一份样本：心率之间会漂（AECG100 换档、电极接触变化），
   拿两次不同的采集比"修法 A vs 修法 B"，差异里混着信号本身的变化，结论就不可信。

用法
----
    py pc_sweep_replay.py --port COM27 --bpms 60,90,120 --secs 120
    py pc_sweep_replay.py --bpms 90 --from-file _raw_90bpm_2mv.bin   # 只重跑算法，不再采
    py pc_sweep_replay.py --bpms 90 --secs 180 --configs 140:0,140:1,512:0

configs 语法：`filt_hist:fid_mode`，逗号分隔。0=fid 事后精修（固件现状），1=候选入池即精修。
"""
import argparse
import importlib
import os

import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def send_start(port):
    from pc485_cmd import build
    import serial
    p = serial.Serial(port, 115200, timeout=0.2)
    p.reset_input_buffer()
    p.rts = True                      # 🛑 本机 CH340-485 必须拉 RTS 才真发得出去
    time.sleep(0.05)
    p.write(build(0x01))
    p.flush()
    time.sleep(1.2)
    back = p.read(4096)
    p.rts = False
    p.close()
    if back and b"start FAIL" in back:
        print("  ✗ STM32 报错：" + back.decode("ascii", "replace").strip())
        return False
    return True


def capture(port, secs, path):
    import serial
    p = serial.Serial(port, 115200, timeout=0.05)
    p.set_buffer_size(rx_size=1048576)
    p.rts = False
    p.reset_input_buffer()
    buf = bytearray()
    t_end = time.time() + secs
    while time.time() < t_end:
        c = p.read(8192)
        if c:
            buf += c
    p.close()
    with open(path, "wb") as f:
        f.write(buf)
    return len(buf)


def run_one(samples, bpm, filt_hist, fid_mode, skip):
    os.environ["RP_FILT_HIST"] = str(filt_hist)
    os.environ["RP_FID_MODE"] = str(fid_mode)
    import replay_rpeak as R
    importlib.reload(R)
    rp = R.RPeak()
    peaks, diags = [], []
    for x in samples:
        for idx, dg in rp.process(x):
            if idx is not None:
                peaks.append(idx)
                diags.append(dg)
    s = R.stats(peaks, bpm, skip=skip)
    if not s:
        return None
    s["n_locate_fail"] = sum(1 for dg in diags[skip:]
                             if "loc" in dg and dg["loc"] == dg["trigger"])
    wins = [dg.get("win", R.REF_WIN) for dg in diags[skip:]]
    s["n_win_short"] = sum(1 for w in wins if w < R.REF_WIN)
    s["min_win"] = min(wins) if wins else 0
    s["max_lag"] = rp.max_lag
    s["pass"] = (s["sigma_e"] <= 0.5 and s["anom"] == 0)
    return s


def main():
    ap = argparse.ArgumentParser(description="多心率采集 + 离线算法对比")
    ap.add_argument("--port", default="COM27")
    ap.add_argument("--bpms", default="60,90,120,150,180,210")
    ap.add_argument("--secs", type=float, default=120.0)
    ap.add_argument("--configs", default="140:0,140:1")
    ap.add_argument("--skip", type=int, default=10, help="统计剔除前 N 拍（去开机瞬态）")
    ap.add_argument("--from-file", action="append", default=[],
                    help="已有抓包，形如 90=_raw_90bpm_2mv.bin；给了就跳过采集")
    ap.add_argument("--no-wait", action="store_true",
                    help="不等人按回车（非交互环境必须给：AECG100 已经设好了）")
    ap.add_argument("--outdir", default=".")
    a = ap.parse_args()

    cfgs = []
    for tok in a.configs.split(","):
        h, m = tok.split(":")
        cfgs.append((int(h), int(m)))

    files = {}
    for tok in a.from_file:
        bpm, path = tok.split("=", 1)
        files[float(bpm)] = path

    todo = [float(v) for v in a.bpms.split(",")]
    rows = []
    for i, bpm in enumerate(todo, 1):
        print("\n" + "=" * 66)
        print("  [%d/%d] %g bpm" % (i, len(todo), bpm))
        print("=" * 66)

        path = files.get(bpm)
        if path is None:
            if not a.no_wait:
                input("  👉 请把 AECG100 设成 %g bpm（振幅 2 mV）、确认屏上有波形，然后回车…" % bpm)
            else:
                print("  👉 假定 AECG100 已是 %g bpm / 2 mV" % bpm)
            if not send_start(a.port):
                print("  ✗ 起流失败，跳过")
                continue
            path = os.path.join(a.outdir, "_raw_%gbpm_2mv.bin" % bpm)
            n = capture(a.port, a.secs, path)
            print("  抓到 %d B -> %s" % (n, path))
            if n < 20000:
                print("  ✗ 字节太少（起流失败或链路断），跳过")
                continue
        else:
            print("  复用 %s" % path)

        import replay_rpeak as R
        samples, seglens, bad = R.load_frames(path)
        print("  样本 %d  连续段 %d  坏帧 %d" % (len(samples), len(seglens), bad))
        if len(samples) < 1000:
            print("  ✗ 可用样本太少，跳过")
            continue

        for (h, m) in cfgs:
            s = run_one(samples, bpm, h, m, a.skip)
            if not s:
                print("    h=%-4d fid=%d  ✗ 峰太少" % (h, m))
                continue
            print("    h=%-4d fid=%d  峰%4d  σ(e)=%6.2f  P95=%5.1f  异常%4d  "
                  "未精修%3d  窗缺%3d(最窄%3d)  滞后%4d  ⇒ %s"
                  % (h, m, s["n"], s["sigma_e"], s["p95"], s["anom"],
                     s["n_locate_fail"], s["n_win_short"], s["min_win"],
                     s["max_lag"], "PASS" if s["pass"] else "FAIL"))
            rows.append((bpm, h, m, s))

    if rows:
        print("\n" + "=" * 66)
        print("  横向汇总（σ(e) 样本；PASS 需 ≤0.5 且异常=0）")
        print("=" * 66)
        heads = sorted({(h, m) for _, h, m, _ in rows})
        print("  bpm  |" + "|".join(" h%-4d fid%d " % (h, m) for h, m in heads))
        for bpm in sorted({r[0] for r in rows}):
            cells = []
            for h, m in heads:
                hit = [s for (b, hh, mm, s) in rows if b == bpm and hh == h and mm == m]
                if not hit:
                    cells.append("     --      ")
                else:
                    s = hit[0]
                    cells.append("%6.2f %-4s" % (s["sigma_e"], "PASS" if s["pass"] else "FAIL"))
            print("  %-4g |" % bpm + "|".join(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
