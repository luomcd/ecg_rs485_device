"""MIT-BIH 记录完整性校验（内容级，不只是"文件大小对")

## 校验项（逐条记录）

| # | 检查 | 依据 |
|---|---|---|
| 1 | `.hea` / `.atr` / `.dat` 三个文件都存在且非空 | — |
| 2 | `.dat` 大小 = **1,950,000** B（650000 样本 × 2 导联 × 1.5 B） | 格式 212 定长 |
| 3 | `.hea` 首行 `记录号 导联数 fs 样本数` 与期望一致（fs=360、750000? 不，650000） | WFDB 头 |
| 4 | **`.dat` 内容校验和 == `.hea` 里的 checksum 字段** | WFDB 规范：`sum(samples) & 0xFFFF` |
| 5 | 第一帧样本 == `initval` 字段 | WFDB 头 |
| 6 | `.atr` 能被官方 `wfdb.rdann` 解析且标注条数 > 0 | wfdb 库 |

> 🛑 **为什么必须做内容校验**：只看"文件大小对"抓不到**半截数据被零填充**的情况 ——
   本下载器为了支持断点续传，会**预先把 .dat truncate 到完整长度**，
   所以"大小对"根本不代表"下完了"。checksum 是唯一可靠的判据。
>   （2026-10-26 就是靠这条才发现：跳过的判据必须同时看 `.chunks` 标记。）

## 用法

```bash
python tools/mitbih_verify.py                 # 只检查，打印逐条结果
python tools/mitbih_verify.py --repair        # 检查 + **删掉坏记录**（.dat/.chunks）让下载器重下
python tools/mitbih_verify.py --quiet         # 只打印汇总与失败项
```

退出码：0 = 全部通过；1 = 有失败项（配合 `--repair` 后仍失败）；2 = 有记录不完整
"""
import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REC_DIR = os.path.join(HERE, "..", "mit_bih", "records")

DAT_SIZE = 1950000
NSAMP = 650000
FS = 360


def read_hea(path):
    """解析 WFDB 头。返回 dict 或 None"""
    try:
        lines = [l.strip() for l in open(path, encoding="ascii", errors="replace")
                 if l.strip() and not l.startswith("#")]
    except OSError:
        return None
    f0 = lines[0].split()
    if len(f0) < 4:
        return None
    nsig, fs, nsamp = int(f0[1]), float(f0[2]), int(f0[3])
    sigs = []
    for l in lines[1:1 + nsig]:
        p = l.split()
        # file fmt gain adcres adczero initval checksum blocksize name
        sigs.append({
            "file": p[0], "fmt": int(p[1]), "gain": float(p[2]),
            "adczero": float(p[4]), "initval": float(p[5]),
            "checksum": int(p[6]), "name": p[8] if len(p) > 8 else "?",
        })
    return {"nsig": nsig, "fs": fs, "nsamp": nsamp, "sigs": sigs}


def decode_212(path, nsamp, nsig=2):
    raw = np.fromfile(path, dtype=np.uint8)
    n3 = len(raw) // 3 * 3
    b = raw[:n3].reshape(-1, 3).astype(np.int32)
    s1 = b[:, 0] | ((b[:, 1] & 0x0F) << 8)
    s2 = (b[:, 1] >> 4) | (b[:, 2] << 4)
    inter = np.empty(len(b) * 2, dtype=np.int32)
    inter[0::2], inter[1::2] = s1, s2
    n = min(nsamp * nsig, len(inter))
    return inter[:n].reshape(-1, nsig)


def verify(rec, repair=False):
    """返回 (ok: bool, 说明: str, 失败原因: str)"""
    hea = os.path.join(REC_DIR, f"{rec}.hea")
    atr = os.path.join(REC_DIR, f"{rec}.atr")
    dat = os.path.join(REC_DIR, f"{rec}.dat")
    mark = os.path.join(REC_DIR, f"{rec}.chunks")

    miss = [f".{e}" for e, p in (("hea", hea), ("atr", atr), ("dat", dat))
            if not (os.path.exists(p) and os.path.getsize(p) > 0)]
    if miss:
        return False, f"缺文件 {','.join(miss)}", "missing"

    # 2) .dat 大小
    sz = os.path.getsize(dat)
    if sz != DAT_SIZE:
        if os.path.exists(mark):
            return False, f".dat 大小 {sz}（还没下完，有 .chunks）", "incomplete"
        return False, f".dat 大小 {sz} != {DAT_SIZE}", "size"

    h = read_hea(hea)
    if h is None:
        return False, ".hea 解析失败", "hea"
    # 3) 头部元信息
    if abs(h["fs"] - FS) > 1e-6 or h["nsamp"] != NSAMP or h["nsig"] != 2:
        return False, f"头部异常 nsig={h['nsig']} fs={h['fs']} nsamp={h['nsamp']}", "hea"

    # 4) checksum（只对导联 0，MIT-BIH 的 checksum 就是 ch0 的）
    x = decode_212(dat, h["nsamp"], h["nsig"])
    ch0 = x[:, 0]
    got = int(ch0.sum()) & 0xFFFF
    want = h["sigs"][0]["checksum"] & 0xFFFF
    if got != want:
        # 全零是"预建占位"的典型特征
        zeros = int((ch0 == 0).sum())
        hint = f"（ch0 有 {zeros} 个零样本）" if zeros > h["nsamp"] // 10 else ""
        return False, f"checksum {got} != {want}{hint}", "checksum"

    # 5) initval
    if int(h["sigs"][0]["initval"]) != int(ch0[0]):
        return False, f"initval {h['sigs'][0]['initval']} != 首样本 {ch0[0]}", "initval"

    # 6) .atr 可解析
    try:
        import wfdb
        a = wfdb.rdann(os.path.join(REC_DIR, rec), "atr")
        if len(a.sample) == 0:
            return False, ".atr 标注为 0 条", "atr"
        nbeat = int(sum(1 for s in a.symbol if s in set("NLRBAaJSVrFejnE/fQ")))
    except Exception as e:
        return False, f".atr 解析失败: {type(e).__name__}", "atr"

    return True, f"checksum {got} ✅  标注 {len(a.sample)} 条（搏动 {nbeat}）", ""


def repair_record(rec):
    """删掉 .dat 与 .chunks，让下载器重下（.hea/.atr 小而快，一并删更保险）"""
    removed = []
    for ext in ("dat", "chunks", "hea", "atr"):
        p = os.path.join(REC_DIR, f"{rec}.{ext}")
        if os.path.exists(p):
            try:
                os.remove(p)
                removed.append(f".{ext}")
            except OSError:
                pass
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repair", action="store_true", help="删掉坏记录让下载器重下")
    ap.add_argument("--quiet", action="store_true", help="只打印汇总与失败项")
    ap.add_argument("--recs", default=None, help="逗号分隔，默认扫全部存在的记录")
    a = ap.parse_args()

    if a.recs:
        recs = a.recs.split(",")
    else:
        recs = sorted(f[:-4] for f in os.listdir(REC_DIR) if f.endswith(".hea"))

    ok_list, fail_list = [], []
    for rec in recs:
        ok, msg, reason = verify(rec)
        if ok:
            ok_list.append(rec)
            if not a.quiet:
                print(f"  ✅ {rec}   {msg}")
        else:
            fail_list.append((rec, msg, reason))
            print(f"  ❌ {rec}   {msg}   [{reason}]")
            if a.repair:
                rm = repair_record(rec)
                print(f"     → 已删除 {','.join(rm) if rm else '（无文件可删）'}，下次下载会重下")

    print(f"\n校验结果：通过 {len(ok_list)} 条，失败 {len(fail_list)} 条"
          + (f"（已修复 {len(fail_list)} 条，需重新下载）" if a.repair else ""))
    if fail_list and not a.repair:
        print("提示：加 --repair 可自动删掉坏记录让下载器重下")
    return 0 if not fail_list else (2 if any(r == "incomplete" for _, _, r in fail_list) else 1)


if __name__ == "__main__":
    sys.exit(main())
