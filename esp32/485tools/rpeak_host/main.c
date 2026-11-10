/**
 * @file main.c
 * @brief R 峰检测器的**宿主（PC）测试夹具**（MIT-BIH 验证用）
 *
 * 目的：在 PC 上跑**固件同一份 `ecg_rpeak.c`**（不复制、不改写、不重新实现），
 *       对任意 T101 v2.2 `.bin` 批量出峰，交给 `tools/mitbih_eval.py` 算 Se/+P。
 *
 * 为什么不用 C# 参考实现做对照：参考实现是 `double`，固件是 `float`（S3 无硬件双精度）。
 *       在干净数据上两者逐位一致（S7 已验 39/39），但 MIT-BIH 的噪声/PVC 段可能分叉。
 *       要声称"验证的是固件"，就必须跑同一份 `.c`。
 *
 * 编译（MinGW gcc，无任何 ESP 依赖）：
 *   gcc -O2 -o rpeak_host.exe tools/rpeak_host/main.c \
 *       cardiolite-ecg/main/ecg/ecg_rpeak.c -Icardiolite-ecg/main/ecg
 *
 * 用法：
 *   rpeak_host.exe <input.bin> <out_peaks.txt>
 * 输出：`index,time_s`（与 tools/rpeak_ref 的 --all-peaks 同格式）
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ecg_rpeak.h"

#define SAMPLE_RATE 500

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s <input.bin> <out_peaks.txt> [diag.csv]\n", argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (f == NULL) {
        fprintf(stderr, "open %s failed\n", argv[1]);
        return 1;
    }

    static ecg_rpeak_t rp;
    ecg_rpeak_init(&rp, SAMPLE_RATE);

    uint8_t  payload[512];
    int64_t  nsamp = 0, nframe = 0, npeak = 0, nbad = 0;
    FILE    *out = fopen(argv[2], "w");

    if (out == NULL) {
        fprintf(stderr, "open %s failed\n", argv[2]);
        fclose(f);
        return 1;
    }
    fprintf(out, "index,time_s\n");

#if ECG_RP_DIAG
    /* 可选诊断输出（T225 §6.3）：每峰记录 trigger / fid / lag / 窗宽 / 来源
     * 🛑 这是「落点是否被窗口截断」在**板端唯一可观测**的量（T224 形态②
     *    在 loc!=trigger 时完全隐身，只有 win 能抓）。 */
    FILE *dg = NULL;
    if (argc >= 4) {
        dg = fopen(argv[3], "w");
        if (dg != NULL) {
            fprintf(dg, "index,trigger,fid,fid_delta,lag,win,win_short,src\n");
        }
    }
#endif

    /* 🛑 必须**重同步**（逐字节找 AA55），不能"头部不对就 break"。
     *    485 直抓的文件开头常常是半帧（抓包从流量中间开始），
     *    旧版在这种情况下直接判 0 峰，会把"夹具缺陷"误读成"检测器不工作"。
     *    做法与 `cap485_quality.py` / `replay_rpeak.py` 完全一致：
     *    LEN 必须 ∈ {52,53,103}，CRC8(poly 0x07/init 0x00/不反射) 必须对。 */
    static const int FRAME_LENS[] = { 52, 53, 103 };
    int  c;
    long pos = 0;
    for (;;) {
        /* --- 找帧头 --- */
        pos = ftell(f);
        c = fgetc(f);
        if (c == EOF) {
            break;
        }
        if (c != 0xAA) {
            continue;
        }
        c = fgetc(f);
        if (c == EOF) {
            break;
        }
        if (c != 0x55) {
            fseek(f, -1, SEEK_CUR);
            continue;
        }
        /* --- 读 TYPE/LEN --- */
        uint8_t h[3];
        if (fread(h, 1, 3, f) != 3) {
            break;
        }
        const int len = h[1] | (h[2] << 8);
        int lenok = 0;
        for (int k = 0; k < 3; k++) {
            if (FRAME_LENS[k] == len) {
                lenok = 1;
            }
        }
        if (!lenok || len > (int)sizeof(payload)) {
            nbad++;
            fseek(f, pos + 1, SEEK_SET);        /* 从 AA 的下一个字节重扫 */
            continue;
        }
        if (fread(payload, 1, (size_t)len, f) != (size_t)len) {
            break;
        }
        uint8_t crcb = 0;
        if (fread(&crcb, 1, 1, f) != 1) {
            break;
        }
        uint8_t crc = 0;
        /* 🛑 CRC8 覆盖 TYPE + LEN(2) + payload —— LEN 在 h[1]/h[2] 里，
         *    旧写法只 XOR 了 h[0]，会漏掉 LEN 两个字节 ⇒ 每一帧都判 CRC 错。 */
        for (int i = 0; i < 3; i++) {            /* TYPE, LEN_L, LEN_H */
            crc ^= h[i];
            for (int b = 0; b < 8; b++) {
                crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
            }
        }
        for (int i = 0; i < len; i++) {          /* payload */
            crc ^= payload[i];
            for (int b = 0; b < 8; b++) {
                crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
            }
        }
        if (crc != crcb) {
            nbad++;
            fseek(f, pos + 1, SEEK_SET);
            continue;
        }

        /* LEN 奇数：SEQ(2) + STA(1) + samples；偶数：SEQ(2) + samples
         * 🛑 双通道帧 LEN=103 含 50 个样本（CH1 25 + CH2 25），这里只取 CH1 前 25 个，
         *    与 `ecg_pipeline.c` / `replay_rpeak.py` 口径一致。 */
        const int off = (len % 2 == 1) ? 3 : 2;
        int       n   = (len - off) / 2;
        if (n > 25) {
            n = 25;
        }

        for (int i = 0; i < n; i++) {
            /* 逐字节合成 —— 与固件一致，绝不做 *(int16_t*) 强转 */
            const int16_t v = (int16_t)(payload[off + 2 * i] |
                                        (payload[off + 2 * i + 1] << 8));
            int64_t pk = 0;
            if (ecg_rpeak_process(&rp, (float)v, &pk)) {
                fprintf(out, "%lld,%.4f\n", (long long)pk, (double)pk / SAMPLE_RATE);
                npeak++;
#if ECG_RP_DIAG
                if (dg != NULL) {
                    const ecg_rp_diag_t *d = ecg_rpeak_last_diag(&rp);
                    fprintf(dg, "%lld,%lld,%lld,%lld,%lld,%d,%d,%d\n",
                            (long long)pk, (long long)d->trigger, (long long)d->fid,
                            (long long)(d->fid - d->trigger), (long long)d->lag,
                            (int)d->win, (int)d->win_short, (int)d->src);
                }
#endif
            }
            nsamp++;
        }
        nframe++;
    }
    fclose(out);
#if ECG_RP_DIAG
    if (dg != NULL) {
        fclose(dg);
    }
#endif
    fclose(f);

    printf("frames=%lld samples=%lld bad=%lld\n", (long long)nframe, (long long)nsamp,
           (long long)nbad);
    printf("peaks=%lld  bpm=%.2f  (%.2f s @ %d Hz)\n", (long long)npeak,
           (double)ecg_rpeak_bpm(&rp), (double)nsamp / SAMPLE_RATE, SAMPLE_RATE);
    printf("sizeof(ecg_rpeak_t)=%u  USE_STORED_FID=%d  DIAG=%d\n",
           (unsigned)sizeof(ecg_rpeak_t), ECG_RP_USE_STORED_FID, ECG_RP_DIAG);
    return 0;
}
