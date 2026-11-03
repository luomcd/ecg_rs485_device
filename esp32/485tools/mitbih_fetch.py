"""MIT-BIH Arrhythmia Database 分块并行下载器（PhysioNet 单连接只有 ~3.4 KB/s，必须并行+续传）

策略：每个文件切成 256 KB 块，6 线程并行 `Range` 拉取，每块落成独立临时文件；
已存在的块跳过 ⇒ **天然断点续传**（连接断在 196 KB 就断，不续传根本下不完）。
全部块齐了再拼成正式文件并校验长度。

用法：
  python mitbih_fetch.py --recs 100              # 只下一条
  python mitbih_fetch.py                        # 下全部 48 条
  python mitbih_fetch.py --recs 100,101 --jobs 8
"""
import argparse
import os
import sys
import threading
import time
import urllib.request

# 🛑 本机有 http_proxy=http://127.0.0.1:54355。实测：physionet 走代理 399 B/s、
# 绕过 2447 B/s（6 倍）；清华镜像走代理 403。**一律绕过**。
# （GitHub 例外：它在代理下 10 路并行能到 600 KB/s，但绕过也不慢，统一处理更简单）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_OPENER)

# ============================================================================
# 🛑 看门狗（2026-10-26 加，教训驱动）
#
# 为什么必须有：定时任务（每小时）做看门狗有**粒度漏洞** ——
#   17:10 开始卡，17:54 那次巡检因为"没超过 1 小时"判正常，下一次要等 18:54。
#   实测就这样漏掉了一次 55 分钟的卡死。
# ⇒ 看门狗必须**在同一进程里、按秒判断**，不能依赖外部调度粒度。
#
# 做法：任何一次"有进展"都 touch 一次时间戳；看门狗线程发现超过 N 分钟没 touch
# 就 os._exit() —— 用 os._exit 是因为普通异常杀不掉挂在 urlopen 里的线程。
# 父进程（tools/mitbih_run_forever.sh）负责把它重新拉起来。
# ============================================================================
_LAST_PROGRESS = [time.time()]


def _touch():
    _LAST_PROGRESS[0] = time.time()


def _watchdog(stall_sec):
    while True:
        time.sleep(20)
        idle = time.time() - _LAST_PROGRESS[0]
        if idle > stall_sec:
            print(f"\n[WATCHDOG] {idle/60:.1f} 分钟无任何进展 ⇒ 强制退出，交给父进程重启",
                  flush=True)
            os._exit(4)

from concurrent.futures import ThreadPoolExecutor

BASE = "https://physionet.org/files/mitdb/1.0.0/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mit_bih", "records")

# MIT-BIH Arrhythmia Database 的 **标准 48 条**（204/206 属于别的库，不在其中）
ALL_RECS = ["100", "101", "102", "103", "104", "105", "106", "107", "108", "109",
            "111", "112", "113", "114", "115", "116", "117", "118", "119",
            "121", "122", "123", "124",
            "200", "201", "202", "203", "205", "207", "208", "209", "210",
            "212", "213", "214", "215", "217", "219", "220", "221", "222",
            "223", "228", "230", "231", "232", "233", "234"]

# MIT-BIH 全部记录的 .dat 都是 650000 样本 × 2 导联 × 1.5 B = 1,950,000 B
# HEAD 拿不到长度时用它兜底（PhysioNet 直连的 HEAD 经常超时）
DAT_SIZE = 1950000

# 🛑 块大小与超时的取舍（2026-10-26 实测调优）：
#   本机到 PhysioNet 单连接约 2.4 KB/s ⇒ 256 KB 块要 ~107 s，一旦中途卡住就会
#   一直占着连接不产出（曾连续 2 h9min 零进度）。改 **128 KB**（~53 s）+
#   超时 120 s + 重试 6 次：卡住时更快失败、已完成的块照样保留。
CHUNK = 128 * 1024
RETRY = 6


def head_size(url, retry=4):
    """取文件长度。HEAD 在 PhysioNet 直连下常超时 ⇒ 重试 + GET Range 兜底 + 已知尺寸兜底。"""
    last = None
    for t in range(retry):
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=30) as r:
                n = int(r.headers.get("Content-Length", 0))
                if n > 0:
                    return n
        except Exception as e:
            last = e
        try:                       # 兜底：GET 一个字节，从 Content-Range 里取总长
            req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                cr = r.headers.get("Content-Range", "")
                if "/" in cr:
                    return int(cr.split("/")[-1])
        except Exception as e:
            last = e
        time.sleep(1 + 2 * t)
    if url.endswith(".dat"):        # MIT-BIH 的 .dat 尺寸是固定已知的
        return DAT_SIZE
    raise IOError(f"无法取长度: {url} ({last})")


def get_range_into(url, lo, hi, fpath):
    """拉 [lo,hi] 并**直接写到 fpath 的 lo 偏移**（不再产生临时分块文件）。

    🛑 为什么改：原实现每块落一个 `<rec>.dat.partNNNN`，几十个文件拼完要删，
       而本机批量删除超过 50 个文件会触发"需确认"的安全策略 ⇒ 脚本 EXIT=1 中断，
       下载在第 12 条断掉。改成就地写入后：**零临时文件、零删除**。
    """
    req = urllib.request.Request(url, headers={"Range": f"bytes={lo}-{hi}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if len(data) != hi - lo + 1:
        raise IOError(f"short read {len(data)} != {hi - lo + 1}")
    with open(fpath, "r+b") as f:
        f.seek(lo)
        f.write(data)


def load_done(mark):
    if not os.path.exists(mark):
        return set()
    try:
        return set(int(x) for x in open(mark).read().split() if x.strip())
    except Exception:
        return set()


def mark_done(mark, i):
    with open(mark, "a") as f:
        f.write(f"{i}\n")
    _touch()


def fetch_small(rec, ext):
    """下载 .hea / .atr。**不做 HEAD**。

    🛑 原来先 HEAD 问长度再下，而 HEAD 在直连下会挂住（30 s × 2 路 × 4 轮 ≈ 4 min），
       .dat 那边已经改成用固定常量绕开，.hea/.atr 尺寸不固定没法照做 ——
       所以干脆**连长度都不问**：只要文件存在且非空就算好。
       内容正确性由后续 mitbih_prep 的 wfdb 解析来把关（解析不了会直接报错）。
    """
    url = f"{BASE}{rec}.{ext}"
    dst = os.path.join(OUT, f"{rec}.{ext}")
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return dst, os.path.getsize(dst), "skip"
    for t in range(RETRY):
        try:
            urllib.request.urlretrieve(url, dst)
            if os.path.getsize(dst) > 0:
                return dst, os.path.getsize(dst), "ok"
        except Exception:
            time.sleep(1 + t)
    return dst, 0, "FAIL"


def fetch_dat(rec, pool, jobs):
    url = f"{BASE}{rec}.dat"
    dst = os.path.join(OUT, f"{rec}.dat")
    mark = os.path.join(OUT, f"{rec}.chunks")
    # 🛑 MIT-BIH 全部记录的 .dat 都是 650000 样本 × 2 导联 × 1.5 B = 1,950,000 B。
    #    **不要再去 HEAD 问服务器** —— 直连下 HEAD 经常挂住（30 s × 2 路 × 4 轮 ≈ 5 min），
    #    这是下载反复"停住不动"的真正原因。直接用已知常量。
    want = DAT_SIZE
    # ⚠ 光看大小不够：本实现会**预先把文件 truncate 到目标长度**，
    #   "大小对"不等于"下完了"。必须同时没有 .chunks 未完成标记才算完。
    if (os.path.exists(dst) and os.path.getsize(dst) == want
            and not os.path.exists(mark)):
        return dst, want, "skip"

    # 预建到目标长度（稀疏/零填充），供各线程按偏移就地写
    if os.path.exists(dst) and os.path.getsize(dst) != want:
        os.remove(dst)
    if not os.path.exists(dst):
        with open(dst, "wb") as f:
            f.truncate(want)

    nchunk = (want + CHUNK - 1) // CHUNK
    done = load_done(mark)

    def one(i):
        if i in done:
            return True
        lo = i * CHUNK
        hi = min((i + 1) * CHUNK, want) - 1
        for t in range(RETRY):
            try:
                get_range_into(url, lo, hi, dst)
                mark_done(mark, i)
                return True
            except Exception:
                time.sleep(1 + 2 * t)
        return False

    ok = list(pool.map(one, range(nchunk)))
    if not all(ok):
        return dst, want, f"FAIL({ok.count(False)}/{nchunk} 块待补)"

    if os.path.exists(mark):
        os.remove(mark)
    sz = os.path.getsize(dst)
    return dst, want, ("ok" if sz == want else f"SIZE {sz}!={want}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recs", default=None, help="逗号分隔；默认全部 48 条")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--watchdog", type=int, default=10,
                    metavar="MIN", help="连续 N 分钟零进展就自杀（默认 10）")
    args = ap.parse_args()

    threading.Thread(target=_watchdog, args=(args.watchdog * 60,), daemon=True).start()
    print(f"看门狗：连续 {args.watchdog} 分钟零进展就退出（由父进程重启）")

    recs = args.recs.split(",") if args.recs else ALL_RECS
    os.makedirs(OUT, exist_ok=True)
    print(f"目标目录 {os.path.abspath(OUT)}  共 {len(recs)} 条记录  并行 {args.jobs}")
    t0 = time.time()
    done = 0

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for rec in recs:
            line = [f"[{rec}]"]
            try:
                for ext in ("hea", "atr"):
                    _, want, st = fetch_small(rec, ext)
                    line.append(f"{ext}:{st}")
                _touch()
                d, want, st = fetch_dat(rec, pool, args.jobs)
                line.append(f"dat:{st}({want//1024}KB)")
                if st in ("ok", "skip"):
                    done += 1
            except Exception as e:
                line.append(f"SKIP({type(e).__name__})")
            el = time.time() - t0
            print("  ".join(line) + f"   [{done}/{len(recs)}  {el/60:.1f} min]", flush=True)

    print(f"\n完成 {done}/{len(recs)}  用时 {(time.time()-t0)/60:.1f} min")
    return 0 if done == len(recs) else 1


if __name__ == "__main__":
    sys.exit(main())
