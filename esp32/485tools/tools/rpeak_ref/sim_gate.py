import csv, sys

rows = list(csv.DictReader(open("ref_bpm_trace.txt")))
# 固件每 200 ms 发布一次 => 轨迹每 50 ms 一点，取步长 4
dev = [(float(r["t_s"]), float(r["bpm"])) for r in rows][::4]
dev = [(t, b) for t, b in dev if t <= 12.0]

raw = [b for _, b in dev]
print(f"设备每 200 ms 的原始读数（前 12 s，共 {len(dev)} 个）:")
print("t  :", " ".join(f"{t:>3.0f}" for t, _ in dev))
print("bpm:", " ".join(f"{int(v) if v > 0 else 0:>3}" for v in raw))
print()

A = [(b if 20 <= b <= 250 else 0) for b in raw]

def gate_c(seq, n=3, tol=0.05):
    out, run = [], []
    for b in seq:
        if b <= 0:
            run = []
            out.append(0)
            continue
        if run and abs(b - run[-1]) / max(run[-1], 1e-9) > tol:
            run = [b]
        else:
            run.append(b)
        out.append(b if len(run) >= n else 0)
    return out

D = [(b if t >= 6.5 else 0) for t, b in dev]

for name, seq in [("B 范围过滤", A),
                  ("C 稳定门控(3次/5%)", gate_c(raw)),
                  ("D 保护窗 6.5s", D)]:
    first = next((t for (t, _), v in zip(dev, seq) if v > 0), None)
    print(f"{name:20s} 首个有效读数 t={first:.2f}s")
    print("     ", " ".join(f"{int(v):>3}" if v > 0 else " --" for v in seq))
    print()
