"""校验 `result-dump` 回传的完整性 —— 自校验，不依赖 SD 卡原件。

**为什么必须有这个工具**（2026-10-29 实测教训）：
  板子回传结束会打一行 `# ---- END <path> (771619 B) ----`，这个数字是**固件 fread
  读到的字节数**，不是 PC 实际收到的字节数 ⇒ **它证明不了传输完整**。
  实测那次 771 KB 回传：END 行自报 771,619 B 与原文件分毫不差，实际却
  **丢了 5 行 `P` + 1 行被削掉前 3 字节**（`P 569095` → `69095`），
  导致 MIT 111 的 Se 从 99.95 % 假掉到 96.16 %（漏检 1 → 81），全程零报错。

两道判据：
  ① **自校验（无需原件）**：每个 `FILE <name> ... peaks=<n>` 段里实测的峰数必须 == n。
     丢行或截断必然让两者不等 —— 这条能在没有原件的情况下抓住丢数据。
  ② **与原文件比对（可选 `--ref`）**：逐行 diff，直接指出丢了哪几行、哪一行被截断。

⚠ 回传时串口把 `\\n` 变 `\\r\\n`，所以**不能逐字节比**（778,126 vs 771,619 B 是正常的），
   一律先规范化成 LF 再按行比。

用法：
  python tools/s3_dump_verify.py test_logs/s3_result_dump_105.log
  python tools/s3_dump_verify.py test_logs/s3_resultdump_batch.log --ref tools/batch_mit_result.txt
"""
import argparse
import difflib
import os
import re
import sys

# 与 ecg_batch_eval.py / s3_verify_replay.py 同口径：行内搜「空白 + P + 整数」，
# 因为 REPL prompt（`cardiolite> `）会粘在首行 P 前面。
PEAK_RE = re.compile(r"(?:^|\s)P((?:\s+\d+)+)")
BEGIN_RE = re.compile(r"^# ---- BEGIN (\S+) ----$", re.M)
END_RE = re.compile(r"^# ---- END (\S+) \((\d+) B\) ----$", re.M)


def extract_body(raw: bytes):
    """剥掉 `# ---- BEGIN/END` 包裹行，返回 (正文行列表, 文件名, 板子自报字节数)。"""
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n")
    mb = BEGIN_RE.search(text)
    me = END_RE.search(text)
    name = mb.group(1) if mb else "?"
    declared = int(me.group(2)) if me else 0
    if mb and me:
        text = text[mb.end():me.start()]
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines, name, declared


def parse_segments(lines):
    """按 `FILE ... peaks=<n>` 切段，返回 [(name, peaks, declared_peaks, bad_lines)]。

    `bad_lines` 收集「长得像数据但没被 P 正则匹配」的行 —— 截断行就落在这里
    （例如 `69095 569561 ...`，前 3 字节 `P 5` 被吃掉）。"""
    segs = []
    pend, bad = [], []
    for line in lines:
        m = PEAK_RE.search(line)
        if m is not None:
            for tok in m.group(1).split():
                try:
                    pend.append(int(tok))
                except ValueError:
                    pass
            continue
        if line.startswith("FILE "):
            parts = line.split()
            info = dict(p.split("=", 1) for p in parts[2:] if "=" in p)
            try:
                declared = int(info.get("peaks", "-1"))
            except ValueError:
                declared = -1
            segs.append((parts[1], pend, declared, bad))
            pend, bad = [], []
        elif line and line[0].isdigit():
            # 纯数字开头、却不是合法 P 行 ⇒ 高度疑似被削头的截断行
            bad.append(line)
    if pend or bad:                      # 文件末尾没有 FILE 行（异常）也要报出来
        segs.append(("<尾部无 FILE 行>", pend, -1, bad))
    return segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", help="result-dump 抓下来的日志")
    ap.add_argument("--ref", default=None, help="SD 卡上的原件（拔卡拷的那份），用于逐行比对")
    a = ap.parse_args()

    raw = open(a.log, "rb").read()
    lines, name, declared = extract_body(raw)
    segs = parse_segments(lines)

    print(f"日志      : {a.log}")
    print(f"回传文件  : {name}")
    print(f"正文      : {len(lines)} 行 / {len(raw)} B（含包裹行与 CRLF）")
    print(f"板子自报  : {declared} B  ⚠ 这是 fread 的字节数，**不能**当完整性证明\n")

    print(f"{'记录':<22}{'实测峰数':>9}{'peaks=':>9}{'差':>7}  判定")
    print("-" * 62)
    n_bad = 0
    for nm, peaks, dec, bad in segs:
        diff = len(peaks) - dec if dec >= 0 else 0
        flag = "✅" if (dec >= 0 and diff == 0 and not bad) else "❌"
        if flag == "❌":
            n_bad += 1
        tail = ""
        if bad:
            tail = f"   截断行 {len(bad)} 条：{bad[0][:40]!r}"
        print(f"{nm:<22}{len(peaks):>9}{dec:>9}{diff:>7}  {flag}{tail}")
    print("-" * 62)

    total_peaks = sum(len(p) for _, p, _, _ in segs)
    print(f"共 {len(segs)} 段 / {total_peaks} 峰；峰数与 peaks= 不符的段：**{n_bad}**")

    # ---- 判据 ① 结论 ----
    if n_bad == 0 and not any(b for _, _, _, b in segs):
        print("判据① 自校验：✅ **通过**（每段峰数 = peaks=，无截断行）")
    else:
        print("判据① 自校验：❌ **失败** —— 回传丢了数据，别拿这份做分析")

    # ---- 判据 ② 与原件比对 ----
    if a.ref:
        if not os.path.exists(a.ref):
            print(f"\n判据② 跳过：找不到原件 {a.ref}")
        else:
            ref = open(a.ref, "rb").read().decode("utf-8", "replace").replace("\r\n", "\n")
            ref_lines = ref.splitlines()
            while ref_lines and not ref_lines[-1].strip():
                ref_lines.pop()
            d = list(difflib.unified_diff(ref_lines, lines, "orig", "dump", lineterm="", n=0))
            adds = [x for x in d if x.startswith("+") and not x.startswith("+++")]
            dels = [x for x in d if x.startswith("-") and not x.startswith("---")]
            print(f"\n判据② 与原件比对（{a.ref}）：原件 {len(ref_lines)} 行 / 回传 {len(lines)} 行")
            print(f"        丢失行 {len(dels)} 条 / 多出行 {len(adds)} 条")
            if not dels and not adds:
                print("        判据②：✅ **逐行完全一致**（CRLF 已规范化）")
            else:
                print("        判据②：❌ 有差异，前 12 条：")
                for x in d[2:14]:
                    print(f"          {x[:110]}")

    ok = (n_bad == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
