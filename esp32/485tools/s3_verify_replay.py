"""S3 回放 vs S2 批处理：峰序列逐位比对。

判据（实施计划 S3）：
  回放 `mit-run <rec>` 吐出的峰序列，必须与 S2 批处理跑同一条记录的峰序列
  **逐位完全一致**（同一个文件内相对样本号、同一顺序、个数相同）。
  不一致 ⇒ 相对号转换有 bug，或回放路径丢过样本（`# dropped=` 不为 0）。

用法：
  python tools/s3_verify_replay.py test_logs/s3_mit_run_105_speed8.log
  python tools/s3_verify_replay.py <replay.log> --batch tools/batch_mit_result.txt
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 🛑 不能只认行首 `P `：回放时 esp_console 的 REPL prompt（`cardiolite> `）会粘在
# 第一行 P 前面（实测：`cardiolite> P 3 637 984 ...`），按行首判断会**整行丢掉 16 个峰**。
# 改为行内搜「空白 + P + 一个以上整数」，prompt 前缀不影响。
PEAK_RE = re.compile(r"(?:^|\s)P((?:\s+\d+)+)")
# FILE 行必须带 k=v（`FILE <name> frames=.. samples=.. peaks=.. bpm=..`），
# 否则中文提示「跑完出 FILE 行即可对账」会被误认成数据段。
FILE_RE = re.compile(r"(?:^|\s)FILE\s+(\S+)((?:\s+\w+=\S*)+)")


def parse_dump(path):
    """`P <idx> ...` 累积 → `FILE <name> ...` 提交。返回 {name: [peaks]} 与统计。

    与 tools/ecg_batch_eval.py 的 parse 同口径（都已改为行内容错）。
    """
    out = {}
    order = []
    pend = []
    meta = {}
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.strip()
        m = PEAK_RE.search(line)
        if m is not None:
            for tok in m.group(1).split():
                try:
                    pend.append(int(tok))
                except ValueError:
                    pass
            continue
        if line.startswith("FILE ") or " FILE " in line:
            fm = FILE_RE.search(line)
            if fm is None:
                continue
            name = fm.group(1)
            info = {}
            for p in fm.group(2).split():
                if "=" in p:
                    k, v = p.split("=", 1)
                    # `# dropped=0 speed=8 (…dropped=0)` 里的尾巴不是数字，丢掉
                    if k == "dropped" and not v.isdigit():
                        continue
                    info[k] = v
            out[name] = list(pend)
            order.append(name)
            meta[name] = info
            pend = []
        elif line.startswith("# dropped="):
            # `# dropped=0 speed=8` → 归到最后一条记录
            if order:
                info = meta.setdefault(order[-1], {})
                for p in line[1:].split():
                    if "=" in p:
                        k, v = p.split("=", 1)
                        if not v.isdigit():
                            continue
                        info[k] = v
    return out, meta


def scan_config(path):
    """从日志里捡出固件回显的运行配置（增益 / 倍速），作为「这条日志确实是那个配置」的证据。

    增益只影响绘制，理论上不该改变峰序列 —— 本函数的存在就是为了让这条断言可核对。
    """
    gain_line = None
    speed_line = None
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.strip()
        if "增益:" in line and gain_line is None:
            gain_line = line.split("增益:", 1)[1].strip()
        elif "speed=" in line and "replay:" in line and speed_line is None:
            i = line.find("speed=")
            speed_line = line[i:i + 20].split(",")[0].split()[0]
    return gain_line, speed_line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("replay", help="回放日志（s3_replay_capture.py 产出）")
    ap.add_argument("--batch", default=os.path.join(ROOT, "tools", "batch_mit_result.txt"),
                    help="S2 批处理结果（权威基准）")
    a = ap.parse_args()

    rep, rep_meta = parse_dump(a.replay)
    bat, _ = parse_dump(a.batch)

    # 先亮出这条日志的运行配置 —— 增益变了对账是否还成立，一看配置就知道在比什么
    gain_line, speed_tok = scan_config(a.replay)
    print("日志配置：")
    print(f"  增益: {gain_line if gain_line else '（日志里没有增益回显）'}")
    print(f"  倍速: {speed_tok if speed_tok else '（未记录）'}")
    print()

    if not rep:
        print("⚠ 回放日志里没解析到 FILE 段 —— 回放可能没跑完，或输出没抓到。")
        return 2

    print(f"回放 {len(rep)} 条 / 批处理基准 {len(bat)} 条\n")
    print(f"{'记录':<24}{'回放峰数':>10}{'基准峰数':>10}{'结果':>22}")
    print("-" * 70)

    n_ok = n_tot = 0
    bad_detail = []
    for name in rep:
        peaks = rep[name]
        info = rep_meta.get(name, {})
        dropped = info.get("dropped")
        base = bat.get(name)

        n_tot += 1
        if base is None:
            verdict = "无基准"
            status = "?"
        elif peaks == base:
            status = "OK"
            n_ok += 1
            verdict = "逐位一致"
        else:
            status = "DIFF"
            # 找出第一个差异位置，便于定位偏了多少
            first = None
            for i in range(min(len(peaks), len(base))):
                if peaks[i] != base[i]:
                    first = i
                    break
            if first is None:
                verdict = f"前缀一致，长度差 {len(peaks)-len(base):+d}"
            else:
                verdict = f"首个差异 @#{first}: {peaks[first]} vs {base[first]}"
            bad_detail.append((name, verdict, len(peaks), len(base)))

        drop_s = "" if dropped is None else f" dropped={dropped}"
        print(f"{name:<24}{len(peaks):>10}{len(base) if base is not None else -1:>10}"
              f"{verdict + drop_s:>22}")

    print("-" * 70)
    print(f"\n结论：{n_ok}/{n_tot} 条与批处理**逐位完全一致**")

    # dropped 汇总（逐位一致的前提）
    drops = {n: m.get("dropped") for n, m in rep_meta.items() if m.get("dropped") is not None}
    if drops:
        bad_drop = {n: d for n, d in drops.items() if d not in ("0", None)}
        if bad_drop:
            print(f"⚠ 有 {len(bad_drop)} 条 dropped 不为 0（回放丢过样本，"
                  f"这些记录不能作为逐位一致的证据）：")
            for n, d in list(bad_drop.items())[:10]:
                print(f"   {n}: dropped={d}")
    else:
        print("⚠ 日志里没有 `# dropped=` 行 —— 回放可能没跑到 EOF，无法确认没丢样本。")

    if bad_detail:
        print("\n差异明细：")
        for name, v, a1, a2 in bad_detail:
            print(f"   {name}: {v}（回放 {a1} 峰 / 基准 {a2} 峰）")

    return 0 if (n_ok == n_tot and n_tot > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
