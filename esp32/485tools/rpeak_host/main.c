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
        fprintf(stderr, "usage: %s <input.bin> <out_peaks.txt>\n", argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (f == NULL) {
        fprintf(stderr, "open %s failed\n", argv[1]);
        return 1;
    }

    static ecg_rpeak_t rp;
    ecg_rpeak_init(&rp, SAMPLE_RATE);

    uint8_t  hdr[5];
    uint8_t  payload[512];
    uint8_t  crcb;
    int64_t  nsamp = 0, nframe = 0, npeak = 0, nbad = 0;
    FILE    *out = fopen(argv[2], "w");

    if (out == NULL) {
        fprintf(stderr, "open %s failed\n", argv[2]);
        fclose(f);
        return 1;
    }
    fprintf(out, "index,time_s\n");

    for (;;) {
        if (fread(hdr, 1, 5, f) != 5) {
            break;
        }
        if (hdr[0] != 0xAA || hdr[1] != 0x55) {
            nbad++;
            break;                      /* 假帧头：本夹具不做重同步，直接停 */
        }
        const int len = hdr[3] | (hdr[4] << 8);
        if (len <= 0 || len > (int)sizeof(payload)) {
            nbad++;
            break;
        }
        if (fread(payload, 1, (size_t)len, f) != (size_t)len ||
            fread(&crcb, 1, 1, f) != 1) {
            nbad++;
            break;
        }
        /* LEN 奇数：SEQ(2) + STA(1) + samples；偶数：SEQ(2) + samples */
        const int off = (len % 2 == 1) ? 3 : 2;
        const int n   = (len - off) / 2;

        for (int i = 0; i < n; i++) {
            /* 逐字节合成 —— 与固件一致，绝不做 *(int16_t*) 强转 */
            const int16_t v = (int16_t)(payload[off + 2 * i] |
                                        (payload[off + 2 * i + 1] << 8));
            int64_t pk = 0;
            if (ecg_rpeak_process(&rp, (float)v, &pk)) {
                fprintf(out, "%lld,%.4f\n", (long long)pk, (double)pk / SAMPLE_RATE);
                npeak++;
            }
            nsamp++;
        }
        nframe++;
    }
    fclose(out);
    fclose(f);

    printf("frames=%lld samples=%lld bad=%lld\n", (long long)nframe, (long long)nsamp,
           (long long)nbad);
    printf("peaks=%lld  bpm=%.2f  (%.2f s @ %d Hz)\n", (long long)npeak,
           (double)ecg_rpeak_bpm(&rp), (double)nsamp / SAMPLE_RATE, SAMPLE_RATE);
    return 0;
}
