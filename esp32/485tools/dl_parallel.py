"""通用分块并行下载器（本机网络单连接普遍限速，必须并行 + 续传）

用法：
  python dl_parallel.py <url> <out_file> [--jobs 8] [--chunk 262144]

特点：
  · 每块落成 `<out>.partNNN`，已存在且长度正确的块直接跳过 ⇒ **断点续传**
  · 块齐了自动拼接并删临时文件，最后校验总长
  · 失败块会重试；全失败则保留临时块，下次续
"""
import argparse
import os
import sys
import time
import urllib.request

# 🛑 本机有 http_proxy=http://127.0.0.1:54355。实测：physionet 走代理 399 B/s、
# 绕过 2447 B/s（6 倍）；清华镜像走代理 403。**一律绕过**。
# （GitHub 例外：它在代理下 10 路并行能到 600 KB/s，但绕过也不慢，统一处理更简单）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_OPENER)

from concurrent.futures import ThreadPoolExecutor

RETRY = 5


def head_size(url):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers.get("Content-Length", 0))


def get_range(url, lo, hi, path):
    req = urllib.request.Request(url, headers={"Range": f"bytes={lo}-{hi}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    if len(data) != hi - lo + 1:
        raise IOError(f"short read {len(data)} != {hi - lo + 1}")
    with open(path, "wb") as f:
        f.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--chunk", type=int, default=256 * 1024)
    a = ap.parse_args()

    if os.path.exists(a.out):
        print(f"已存在 {a.out}（{os.path.getsize(a.out)} B），跳过")
        return 0

    want = head_size(a.url)
    n = (want + a.chunk - 1) // a.chunk
    print(f"总长 {want} B = {want/1048576:.1f} MB，切 {n} 块 × {a.chunk//1024} KB，并行 {a.jobs}")
    parts = [f"{a.out}.part{i:04d}" for i in range(n)]
    t0 = time.time()
    done = [0]

    def one(i):
        lo = i * a.chunk
        hi = min(lo + a.chunk, want) - 1
        p = parts[i]
        if os.path.exists(p) and os.path.getsize(p) == hi - lo + 1:
            done[0] += 1
            return True
        for t in range(RETRY):
            try:
                get_range(a.url, lo, hi, p)
                done[0] += 1
                el = time.time() - t0
                rate = sum(os.path.getsize(q) for q in parts if os.path.exists(q)) / max(el, 1e-9)
                print(f"  [{done[0]}/{n}]  {rate/1024:.0f} KB/s", end="\r", flush=True)
                return True
            except Exception:
                time.sleep(1 + 2 * t)
        return True

    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        list(pool.map(one, range(n)))

    missing = [i for i, p in enumerate(parts)
               if not os.path.exists(p) or os.path.getsize(p) != min((i + 1) * a.chunk, want) - i * a.chunk]
    if missing:
        print(f"\n还差 {len(missing)} 块: {missing[:10]} —— 重跑本命令续传")
        return 1

    print()
    with open(a.out, "wb") as f:
        for p in parts:
            f.write(open(p, "rb").read())
    for p in parts:
        os.remove(p)
    sz = os.path.getsize(a.out)
    print(f"完成 {a.out}  {sz} B / 期望 {want}  ({'OK' if sz == want else '长度不符'})"
          f"  用时 {(time.time()-t0)/60:.1f} min")
    return 0 if sz == want else 1


if __name__ == "__main__":
    sys.exit(main())
