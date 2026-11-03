"""准备 SD 卡批处理载荷并拷到卡上

做四件事：
  1. 心率阶梯 .bin（WinApp 录的，已是 T101 格式）→ 改名后放进 `s7_batch/`
  2. MIT-BIH 记录 → 先 `mitbih_prep.py` 转成 500 Hz 的 T101 `.bin`，再放进去
  3. 生成两份清单（S2 起拆分）：`batch_mit.txt`（只 MIT）/ `batch_ladder.txt`（只阶梯）
  4. **先查目标盘剩余空间**，不够就拒绝拷贝并列出所需/可得

用法：
  python make_sdcard_batch.py --drive E:                 # 拷到 E: 盘（SD 卡）
  python make_sdcard_batch.py --drive E: --mit all        # 含全部已下载的 MIT 记录
  python make_sdcard_batch.py --dry-run                   # 只看要拷什么、占多少
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REC_DIR = os.path.join(ROOT, "mit_bih", "records")
# 🛑 阶梯文件的来源目录会变：WinApp 那个 recordings 目录已被清空（0 个 *_sr_*bpm.bin），
#    实际源文件在工程的 recordings_winapp/。**按顺序找，取第一个有货的** ——
#    原来的写法只认死 WinApp 路径，结果"重建卡"时静默少拷 8 个阶梯文件（只打一行 ⚠ 就继续）。
WINAPP_CANDIDATES = [
    os.path.join(ROOT, "recordings_winapp"),
    r"C:\work2026\VsPro\WinApp_ADSECG\ADS1293_Ecg\bin\Debug\net10.0-windows\recordings",
]
PYEXE = sys.executable

# SD 卡上的目录（固件按 /sdcard/batch_mit.txt / batch_ladder.txt 读）
SD_SUB = "s7_batch"


def ladder_files():
    """心率阶梯文件：文件名里带 _sr_<n>bpm。

    按 WINAPP_CANDIDATES 顺序找，**第一个有货的目录**说了算（避免两个目录混用后重复）。"""
    out = []
    for d in WINAPP_CANDIDATES:
        files = glob.glob(os.path.join(d, "*_sr_*bpm.bin"))
        if not files:
            continue
        for p in files:
            bpm = int(os.path.basename(p).split("_sr_")[1].split("bpm")[0])
            out.append((bpm, p))
        print(f"  阶梯源目录：{d}")
        break
    return sorted(out)


def mit_records(which):
    recs = sorted(f[:-4] for f in os.listdir(REC_DIR) if f.endswith(".hea"))
    if which != "all":
        want = set(which.split(","))
        recs = [r for r in recs if r in want]
    return recs


def ensure_prep(rec):
    """确保 <rec>_500.bin 与 <rec>_ann500.txt 存在"""
    b = os.path.join(REC_DIR, f"{rec}_500.bin")
    a = os.path.join(REC_DIR, f"{rec}_ann500.txt")
    if os.path.exists(b) and os.path.exists(a):
        return True, 0
    r = subprocess.run([PYEXE, os.path.join(HERE, "mitbih_prep.py"), rec, "--dir", REC_DIR],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  prep {rec} 失败: {r.stderr.strip()[:160]}")
        return False, 0
    return True, os.path.getsize(b)


def free_bytes(drive):
    try:
        u = shutil.disk_usage(drive + os.sep)
        return u.free, u.total
    except Exception as e:
        print(f"无法读取 {drive} 的容量: {e}")
        return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drive", default=None, help="SD 卡盘符，如 E:")
    ap.add_argument("--mit", default="downloaded", help="'all' / '100,101' / 'downloaded'")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    plan = []          # (源路径, 卡上的相对路径)

    lad = ladder_files()
    if not lad:
        print("⚠ 没找到心率阶梯文件（*_sr_*bpm.bin）")
    for bpm, p in lad:
        plan.append((p, f"{SD_SUB}/sr_{bpm:03d}bpm.bin"))

    recs = mit_records("all" if a.mit in ("all", "downloaded") else a.mit)
    mit_ok = []
    for rec in recs:
        ok, _ = ensure_prep(rec)
        if ok:
            plan.append((os.path.join(REC_DIR, f"{rec}_500.bin"), f"{SD_SUB}/mit_{rec}_500.bin"))
            mit_ok.append(rec)

    total = sum(os.path.getsize(src) for src, _ in plan)
    print(f"计划拷贝 {len(plan)} 个文件（阶梯 {len(lad)} + MIT {len(mit_ok)}），"
          f"合计 {total/1048576:.2f} MB")
    print(f"  MIT 记录: {','.join(mit_ok) if mit_ok else '无'}")

    if a.dry_run or not a.drive:
        print("\n（未指定 --drive，只做预演。加上 --drive E: 实际拷贝）")
        return 0

    free, tot = free_bytes(a.drive)
    if free is None:
        return 2
    # 清单本身很小，留 128 KB 余量
    need = total + 128 * 1024
    print(f"\n目标盘 {a.drive}  总 {tot/1048576:.1f} MB  可用 {free/1048576:.1f} MB"
          f"  需要 {need/1048576:.2f} MB")
    if free < need:
        print(f"❌ 空间不足：差 {(need-free)/1048576:.2f} MB。"
              f"请减少 MIT 记录（--mit 100,101,...）或换卡。")
        return 1
    print(f"✅ 空间足够，剩余 {(free-need)/1048576:.1f} MB")

    dst_dir = os.path.join(a.drive + os.sep, SD_SUB)
    os.makedirs(dst_dir, exist_ok=True)
    for k, (src, rel) in enumerate(plan, 1):
        dst = os.path.join(a.drive + os.sep, rel.replace("/", os.sep))
        shutil.copy2(src, dst)
        print(f"  [{k}/{len(plan)}] {rel}  {os.path.getsize(dst)/1024:.0f} KB", end="\r")
    print()

    # 清单（S2 起拆两份：batch_mit.txt 只 MIT，batch_ladder.txt 只阶梯）
    # 固件 CLI 命令 mit-run-all / ladder-run-all 各跑各的清单。
    mit_lines = ["# CardioLite S2 batch manifest: MIT records only\n",
                 "# one absolute .bin path per line\n"]
    ladder_lines = ["# CardioLite S2 batch manifest: ladder records only\n",
                    "# one absolute .bin path per line\n"]
    for _, rel in plan:
        line = "/sdcard/" + rel + "\n"
        # rel 形如 "s7_batch/mit_100_500.bin"，按**文件名**前缀分流
        base = os.path.basename(rel)
        if base.startswith("mit_"):
            mit_lines.append(line)
        elif base.startswith("sr_"):
            ladder_lines.append(line)

    mit_manifest = os.path.join(a.drive + os.sep, "batch_mit.txt")
    with open(mit_manifest, "w", encoding="ascii", newline="\n") as f:
        f.writelines(mit_lines)
    ladder_manifest = os.path.join(a.drive + os.sep, "batch_ladder.txt")
    with open(ladder_manifest, "w", encoding="ascii", newline="\n") as f:
        f.writelines(ladder_lines)
    print(f"清单 -> {mit_manifest}  ({len(mit_lines) - 2} 条 MIT)")
    print(f"清单 -> {ladder_manifest}  ({len(ladder_lines) - 2} 条阶梯)")
    print("\n下一步：把卡插回板子，上电进 CLI，敲 `mit-run-all` 或 `ladder-run-all` 登记，")
    print("       然后 `restart` 重启，串口出 BATCH-BEGIN … BATCH-END 后跑 tools/ecg_batch_eval.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
