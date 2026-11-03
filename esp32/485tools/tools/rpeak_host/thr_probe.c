/**
 * @file thr_probe.c
 * @brief 诊断夹具：把 `ecg_rpeak.c` **内部阈值三兄弟**（signal_peak / noise_peak / threshold）
 *        逐点打印出来，用于回答「纯基线为什么也会出峰」这类机理问题。
 *
 * 它不解包任何算法，只是调 `ecg_rpeak_process()` 然后读结构体里的公开字段 ——
 * 跑的是固件同一份 `.c`，不是复刻。
 *
 * 编译：
 *   gcc -O2 -o thr_probe.exe tools/rpeak_host/thr_probe.c \
 *       cardiolite-ecg/main/ecg/ecg_rpeak.c -Icardiolite-ecg/main/ecg
 *
 * 用法：
 *   thr_probe.exe <input.bin> [每N样本打一行，默认100]
 * 输出：
 *   `#` 开头 = 周期快照（样本号, integ, signal_peak, noise_peak, threshold）
 *   `P` 开头 = 检出事件（located样本号, bpm, 粗峰号, delta, 距上峰, sig, noi, thr, total）
 *
 * 2026-10-30 域混用诊断：`spacing = 粗峰(idx−31) − last_peak(已locate)` 两个域混用
 *   （ecg_rpeak.c:226-227，照搬 C# 同构）。P 行加打 coarse/delta/prev_gap 用于：
 *   - delta = located − coarse = locate_rpeak 在 ±W 窗内的偏移分布；
 *   - prev_gap = 本 located − 上 located = located 域 RR。
 *   coarse 反推：主路径 coarse = rp.sample_index − 32（返回时 sample_index 已 ++，
 *   :210 粗峰 = ++前 sample_index − 1 − INTEG_WIN/2）；若 |delta| > W 则必是回溯路径
 *   （:279，主路径 |delta| ≤ W 恒真），扫候选池物理数组取 |pk−c.index| ≤ W 的最大
 *   value 候选（回溯 accept 后候选被 drop，只能扫物理槽位）。
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "ecg_rpeak.h"

#define SAMPLE_RATE 500

static float threshold_of(const ecg_rpeak_t *rp)
{
    const float t = rp->noise_peak + 0.25f * (rp->signal_peak - rp->noise_peak);
    return t > 1e-12f ? t : 1e-12f;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s <input.bin> [stride]\n", argv[0]);
        return 2;
    }
    const int stride = (argc >= 3) ? atoi(argv[2]) : 100;

    FILE *f = fopen(argv[1], "rb");
    if (f == NULL) {
        fprintf(stderr, "open %s failed\n", argv[1]);
        return 1;
    }

    static ecg_rpeak_t rp;
    ecg_rpeak_init(&rp, SAMPLE_RATE);

    uint8_t hdr[5];
    uint8_t payload[512];
    uint8_t crcb;
    int64_t nsamp = 0;

    printf("#sample,integ,signal_peak,noise_peak,threshold\n");

    for (;;) {
        if (fread(hdr, 1, 5, f) != 5) {
            break;
        }
        if (hdr[0] != 0xAA || hdr[1] != 0x55) {
            break;
        }
        const int len = hdr[3] | (hdr[4] << 8);
        if (len <= 0 || len > (int)sizeof(payload)) {
            break;
        }
        if (fread(payload, 1, (size_t)len, f) != (size_t)len ||
            fread(&crcb, 1, 1, f) != 1) {
            break;
        }
        const int off = (len % 2 == 1) ? 3 : 2;
        const int n   = (len - off) / 2;

        for (int i = 0; i < n; i++) {
            const int16_t v = (int16_t)(payload[off + 2 * i] |
                                        (payload[off + 2 * i + 1] << 8));
            int64_t pk = 0;
            /* 记录**喂之前**的阈值 —— 判据用的就是这份 */
            const float sig = rp.signal_peak, noi = rp.noise_peak;
            const float thr = threshold_of(&rp);

            if (ecg_rpeak_process(&rp, (float)v, &pk)) {
                /* ---- 域混用诊断（2026-10-30）：反推粗峰号 ---- */
                int64_t coarse = rp.sample_index - 1 - ECG_RP_INTEG_WIN / 2;
                int64_t delta  = pk - coarse;
                if (delta > ECG_RP_LOCATE_HALF || -delta > ECG_RP_LOCATE_HALF) {
                    /* 回溯路径：扫候选池物理槽位（accept 后已 drop，逻辑区不含它） */
                    int64_t bc     = coarse;
                    float   bestv  = -1.0f;
                    int     found  = 0;
                    for (int k = 0; k < ECG_RP_CAND_MAX; k++) {
                        const ecg_rp_cand_t *c = &rp.cand[k];
                        int64_t d = pk - c->index;
                        if (d < 0) {
                            d = -d;
                        }
                        if (d <= ECG_RP_LOCATE_HALF && c->index < rp.sample_index - ECG_RP_REFRACTORY
                            && (!found || c->value > bestv)) {
                            bestv = c->value;
                            bc    = c->index;
                            found = 1;
                        }
                    }
                    if (found) {
                        coarse = bc;
                        delta  = pk - coarse;
                    }
                }
                int64_t prev_gap = -1;
                if (rp.peak_n >= 2) {
                    const int64_t prev_pk =
                        rp.peaks[(rp.peak_pos + ECG_RP_PEAK_MAX - 2) % ECG_RP_PEAK_MAX];
                    prev_gap = pk - prev_pk;
                }
                printf("P%lld,bpm=%.1f,coarse=%lld,delta=%lld,prev_gap=%lld,"
                       "sig=%.4g,noi=%.4g,thr=%.4g,total=%u\n",
                       (long long)pk, (double)rp.bpm, (long long)coarse, (long long)delta,
                       (long long)prev_gap, (double)sig, (double)noi,
                       (double)thr, (unsigned)rp.peak_total);
            }
            nsamp++;
            if (stride > 0 && nsamp % stride == 0) {
                printf("#%lld,%.6g,%.6g,%.6g,%.6g\n", (long long)nsamp,
                       (double)rp.integ_p1, (double)rp.signal_peak,
                       (double)rp.noise_peak, (double)threshold_of(&rp));
            }
        }
    }
    fclose(f);

    printf("# ---- done: samples=%lld peaks=%u bpm=%.2f ----\n",
           (long long)nsamp, (unsigned)rp.peak_total, (double)rp.bpm);
    return 0;
}
