/* ============================================================================
 * _t239_ab_host.c —— T239 步1 的**主机侧** A/B 比对（不碰硬件）
 *
 * 为什么还需要主机侧：板端 A/B（STA bit6/bit7）只能覆盖
 *   真实信号 + notch/mode 往返 + STOP→START；
 *   **伪迹空白路径（50 mV 阶跃 ⇒ 整链复位 + 200 ms 空白）**在台架上很难人为触发，
 *   因为需要 ≥50 mV 的输入跳变。本程序用合成输入把这条路径确定性打满。
 *
 * 判据：`mismatch == 0`，且确实发生过 blank（`zeros > 0`）。
 *
 * 编译（在 stm32/ 目录下）：
 *   gcc -O2 -std=c99 -DECG_RESTORE_AB_VERIFY=1 -IFilter \
 *       -o /tmp/t239_ab tools/_t239_ab_host.c \
 *       Filter/fast_restore_snippet.c Filter/ecg_filter.c \
 *       Filter/ecg_dc_block.c Filter/biquad.c -lm
 *
 * ⚠ 这只是"重构没改数值"的**补充**证据：x86 与 ARM 的 FP 行为在本工程一致
 *   （见 T227：xtensa 与 x86 逐位一致），但**主证据仍是板端 A/B**。
 * ========================================================================= */
#include <stdio.h>
#include <string.h>
#include <stdint.h>

#include "ecg_filter.h"

/* 参照实现（fast_restore_snippet.c 末尾，宏=1 才有） */
extern void   ecg_restore_ref_chain_init(void);
extern void   ecg_restore_ref_start(void);
extern void   ecg_restore_ref_set_notch(int on);
extern void   ecg_restore_ref_set_mode(int mon);
extern double ecg_restore_process_ref(int32_t x_raw);

/* 确定性伪随机（不用 rand，避免不同 CRT 行为差异） */
static uint32_t s_lcg = 20261112u;
static double u01(void)
{
    s_lcg = s_lcg * 1664525u + 1013904223u;
    return (double)((s_lcg >> 8) & 0xFFFFu) / 65536.0;   /* [0,1) */
}

#define UV_PER_CODE   0.1122
#define MV_TO_CODES(mv)  ((double)(mv) * 1000.0 / UV_PER_CODE)

int main(void)
{
    const int32_t  n_total = 400000;
    double  base  = 6075000.0;      /* 24-bit 原始码域直流，与实板同量级 */
    double  drift = 0.0;
    int32_t step_pos = 0;
    int     i;
    uint32_t total = 0u, mismatch = 0u, zeros = 0u, first_bad = 0xFFFFFFFFu;

    ecg_restore_chain_init();
    ecg_restore_ref_chain_init();
    ecg_restore_start();
    ecg_restore_ref_start();

    for (i = 0; i < n_total; i++) {
        double x;
        double a, b;
        uint64_t ba, bb;

        /* ---- 合成输入：慢漂移 + 噪声 + 6 次 ±60 mV 阶跃（跨越 50 mV 判据） ----
         * 阶跃持续 400 样本（≈0.47 s）后回跳 ⇒ 每次都会走
         *   「进入空白 → hold 续期 → 平静 → 整链复位 → 恢复」
         * 这条正是板端最难触发的分支。 */
        drift += (u01() - 0.5) * 8.0;
        if (drift >  20000.0) drift =  20000.0;
        if (drift < -20000.0) drift = -20000.0;

        if ((i % 60000) == 20000) {
            step_pos = i + 400;
        }
        x = base + drift + (u01() - 0.5) * 4000.0
          + ((i >= 20000 && i < step_pos) ? MV_TO_CODES(60.0) : 0.0);

        /* ---- notch / mode 往返：两条 ctx 必须**同时**设，否则比的是不同滤波 ---- */
        if (i == 50000 || i == 90000 || i == 250000) {
            ecg_restore_ctx_set_notch(ecg_restore_ctx_ch1(), 0);
            ecg_restore_ref_set_notch(0);
        }
        if (i == 70000 || i == 110000 || i == 270000) {
            ecg_restore_ctx_set_notch(ecg_restore_ctx_ch1(), 1);
            ecg_restore_ref_set_notch(1);
        }
        if (i == 130000 || i == 290000) {
            ecg_restore_ctx_set_mode(ecg_restore_ctx_ch1(), 1);
            ecg_restore_ref_set_mode(1);
        }
        if (i == 150000 || i == 310000) {
            ecg_restore_ctx_set_mode(ecg_restore_ctx_ch1(), 0);
            ecg_restore_ref_set_mode(0);
        }

        /* ---- 每 50000 样本重走一次 START 复位（覆盖 session_start） ---- */
        if (i == 200000 || i == 350000) {
            ecg_restore_start();
            ecg_restore_ref_start();
        }

        a = ecg_restore_process((int32_t)x);
        b = ecg_restore_process_ref((int32_t)x);

        memcpy(&ba, &a, sizeof ba);
        memcpy(&bb, &b, sizeof bb);
        total++;
        if (ba != bb) {
            mismatch++;
            if (first_bad == 0xFFFFFFFFu) { first_bad = (uint32_t)i; }
        }
        if (i > 6000 && a == 0.0) { zeros++; }   /* 伪迹空白期输出恒 0 */
    }

    printf("samples      = %u\n", (unsigned)total);
    printf("mismatch     = %u%s\n", (unsigned)mismatch,
           mismatch ? "   <== FAIL" : "   (PASS)");
    if (mismatch) { printf("first_bad_at = %u\n", (unsigned)first_bad); }
    printf("blank_zeros  = %u  (must be > 0 ⇒ 伪迹空白路径确实跑到了)\n",
           (unsigned)zeros);
    printf("restore_cnt  = %u  (CH1 ctx 进入伪迹态次数)\n",
           (unsigned)ecg_restore_ctx_ch1()->restore_cnt);

    return (mismatch == 0u && zeros > 0u) ? 0 : 1;
}
