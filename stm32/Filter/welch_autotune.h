#ifndef ADS1293_ECG_WELCH_AUTOTUNE_H
#define ADS1293_ECG_WELCH_AUTOTUNE_H

#include <stdint.h>
#include "arm_math.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * 可选：CMSIS-DSP 实现的 Welch PSD 自检。
 *
 * 用途：开机/上电后用一段已采数据（例如 4~10 秒）测出 40~80 Hz 主导峰，
 *       判断工频是否真的在 50 Hz（自检），而不是运行时在线合成滤波器系数。
 *       若峰值落在 49~51 Hz 外，说明环境/时间基准异常，可报警并触发离线重新标定。
 *
 * 注意：CMSIS-DSP 的 arm_rfft_fast_f32 要求 FFT 长度 N 为 32~4096 的 2 的幂，
 *       且使用 M4F 的 FPU（fpv4-sp-d16）。
 */

typedef struct {
    uint16_t fft_size;                 /* N */
    float    fs_hz;
    arm_rfft_fast_instance_f32 fft;
    float   *window;                   /* N  个 float，调用者提供 */
    float   *fft_in;                   /* N  个 float */
    float   *fft_out;                  /* N  个 float */
    float   *power;                    /* N/2+1 个 float */
} ecg_welch_t;

/* N=1024 时所需工作区：window/fft_in/fft_out 各 N，power 为 N/2+1 -> 共 4*N+1 个 float */
uint32_t ecg_welch_work_floats(uint16_t fft_size);

int ecg_welch_init(ecg_welch_t *w, float fs_hz, uint16_t fft_size,
                   float *window, float *fft_in, float *fft_out, float *power);

/*
 * 对 x[0..n_samples) 做 Welch 平均功率谱，返回 [f_lo, f_hi] 内最强峰的
 * 频率(Hz)与功率(dB)。hop 为分段步进（0 表示 N/2，50% 重叠）。
 * 返回 0 成功；<0 参数/数据不足。
 */
int ecg_welch_find_peak(ecg_welch_t *w, const float *x, uint32_t n_samples,
                        uint32_t hop, float f_lo, float f_hi,
                        float *peak_hz, float *peak_db);

#ifdef __cplusplus
}
#endif

#endif /* ADS1293_ECG_WELCH_AUTOTUNE_H */
