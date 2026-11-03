"""合并多次批处理抓取日志，按「峰数 == 文件头 peaks=」挑完整的那一份

动机（2026-10-27 实测）：固件走 USB-Serial-JTAG 控制台输出，主机侧偶发阻塞
（Defender 扫描日志、磁盘 flush 等）时设备端环形缓冲溢出，**整行 P 索引被丢**，
每轮丢 2~3 条、且每轮落在不同记录上；两轮中 51 条完整记录逐条峰列表完全一致
⇒ 检测器本身确定性，丢行纯属采集链路问题。故用两轮互校：某条在任一轮完整即可用。

用法：
  python tools/ecg_batch_merge.py out.txt log1.txt log2.txt [log3.txt ...]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ecg_batch_eval as be          # noqa: E402


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    out_path, logs = sys.argv[1], sys.argv[2:]

    picked, lost, order = {}, [], []
    for lg in logs:
        for name, peaks, info in be.parse(lg):
            exp = int(info.get("peaks", "-1"))
            ok = (len(peaks) == exp)
            if name not in picked:
                order.append(name)
                picked[name] = (ok, peaks, info, lg)
            elif not picked[name][0] and ok:
                picked[name] = (ok, peaks, info, lg)
            if not ok:
                lost.append((os.path.basename(lg), name, len(peaks), exp))

    miss = [n for n in order if not picked[n][0]]
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"BATCH-BEGIN total={len(order)}\n")
        for name in order:
            ok, peaks, info, lg = picked[name]
            for i in range(0, len(peaks), 16):
                f.write("P " + " ".join(str(v) for v in peaks[i:i + 16]) + "\n")
            body = " ".join(f"{k}={v}" for k, v in info.items())
            f.write(f"FILE {name} {body}\n")
            f.write(f"PENV {name}\n")
        f.write(f"BATCH-END ok={len(order) - len(miss)} fail=0 elapsed_ms=0\n")

    print(f"# 合并 {len(logs)} 份日志 → {out_path}")
    print(f"# 记录 {len(order)} 条，完整 {len(order) - len(miss)} 条")
    if lost:
        print("# 各轮缺失（已被其它轮补上或仍缺）：")
        for lg, name, got, exp in lost:
            print(f"#   {lg}: {name} 解析 {got} / 头 {exp}（丢 {exp - got}）")
    if miss:
        print(f"# ❌ 仍不完整：{miss}")
        return 1
    print("# ✅ 全部记录峰数与文件头一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
