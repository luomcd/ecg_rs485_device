#!/usr/bin/env bash
# ============================================================================
#  MIT-BIH 下载守护运行器
#
#  为什么需要它：下载器内部有看门狗（连续 N 分钟零进展就 os._exit），
#  但"自杀"之后需要有人把它拉起来 —— 这个脚本就是那个"人"。
#
#  🛑 为什么不用定时任务做看门狗（2026-10-26 实测教训）：
#     定时任务最小粒度是 1 小时。17:10 开始卡，17:54 那次巡检因为"没超 1 小时"
#     判了正常，下一次要等到 18:54 —— 实际漏掉了一次 55 分钟的卡死。
#     **看门狗必须按分钟级判断，只能放在进程内。**
#
#  用法：  ./tools/mitbih_run_forever.sh
#  用一个前台/nohup 进程跑着即可；48 条齐了自动退出。
# ============================================================================
set -u

cd "$(dirname "$0")/.." || exit 1
PY="/c/Users/luom/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
TARGET=48
STALL_MIN=10

for round in $(seq 1 300); do
    n=$(ls mit_bih/records/*.dat 2>/dev/null | wc -l)
    echo "[$(date '+%H:%M:%S')] === 第 $round 轮：当前 $n/$TARGET 条 ==="

    # ① 下载（内部有看门狗：连续 STALL_MIN 分钟零进展就自杀）
    "$PY" -u tools/mitbih_fetch.py --jobs 10 --watchdog "$STALL_MIN" 2>&1 | tee -a mit_bih/fetch.log

    # ② 🛑 内容级校验（checksum + 标注可解析），坏记录当场删掉让下一轮重下
    #    只看"文件大小对"抓不到"预建占位"的情况 —— 这是 2026-10-26 实测教训。
    echo "[$(date '+%H:%M:%S')] --- 校验 ---"
    "$PY" tools/mitbih_verify.py --repair 2>&1 | tee -a mit_bih/verify.log
    vrc=${PIPESTATUS[0]}

    n=$(ls mit_bih/records/*.dat 2>/dev/null | wc -l)
    if [ "$vrc" -eq 0 ] && [ "$n" -ge "$TARGET" ]; then
        echo "[$(date '+%H:%M:%S')] ✅ 全部完成且全部校验通过：$n/$TARGET"
        exit 0
    fi
    echo "[$(date '+%H:%M:%S')] 仍需继续（$n/$TARGET，校验码 $vrc），5 s 后下一轮"
    sleep 5
done

echo "重试次数用尽，仍为 $(ls mit_bih/records/*.dat 2>/dev/null | wc -l)/$TARGET"
exit 1
