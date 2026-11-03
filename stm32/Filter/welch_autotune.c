#include "welch_autotune.h"

#include <math.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

uint32_t ecg_welch_work_floats(uint16_t fft_size)
{
    return (uint32_t)fft_size * 4u + 1u;   /* window + in + out + (N/2+1 power) 的保守上界 */
}

int ecg_welch_init(ecg_welch_t *w, float fs_hz, uint16_t fft_size,
                   float *window, float *fft_in, float *fft_out, float *power)
{
    uint16_t i;
    if (!w || fft_size < 32u || (fft_size & (fft_size - 1u)) != 0u) {
        return -1;
    }
    if (!window || !fft_in || !fft_out || !power) {
        return -1;
    }

    w->fft_size = fft_size;
    w->fs_hz    = fs_hz;
    w->window   = window;
    w->fft_in   = fft_in;
    w->fft_out  = fft_out;
    w->power    = power;

    if (arm_rfft_fast_init_f32(&w->fft, fft_size) != ARM_MATH_SUCCESS) {
        return -1;
    }

    /* Hann 窗（对称） */
    for (i = 0; i < fft_size; i++) {
        const float ph = 2.0f * (float)M_PI * (float)i / (float)(fft_size - 1u);
        window[i] = 0.5f * (1.0f - cosf(ph));
    }
    return 0;
}

int ecg_welch_find_peak(ecg_welch_t *w, const float *x, uint32_t n_samples,
                        uint32_t hop, float f_lo, float f_hi,
                        float *peak_hz, float *peak_db)
{
    const uint16_t N = w ? w->fft_size : 0u;
    const float df = (w && N) ? w->fs_hz / (float)N : 0.0f;
    float *power;
    uint32_t nseg = 0u;
    uint32_t klo, khi, k, kmax;
    float pmax = -1e30f;
    uint32_t start;

    if (!w || !x || !peak_hz || !peak_db || N < 32u || df <= 0.0f) {
        return -1;
    }
    if (hop == 0u) {
        hop = N / 2u;
    }
    if (hop == 0u) {
        hop = 1u;
    }

    power = w->power;
    memset(power, 0, ((uint32_t)N / 2u + 1u) * sizeof(float));

    for (start = 0u; start + (uint32_t)N <= n_samples; start += hop) {
        uint16_t i;
        for (i = 0u; i < N; i++) {
            w->fft_in[i] = x[start + i] * w->window[i];
        }
        arm_rfft_fast_f32(&w->fft, w->fft_in, w->fft_out, 0u);

        power[0] += w->fft_out[0] * w->fft_out[0];            /* DC */
        for (i = 1u; i < N / 2u; i++) {
            const float re = w->fft_out[2u * i - 1u];
            const float im = w->fft_out[2u * i];
            power[i] += re * re + im * im;
        }
        power[N / 2u] += w->fft_out[N - 1u] * w->fft_out[N - 1u];  /* Nyquist */
        nseg++;
    }

    if (nseg == 0u) {
        return -2;   /* 数据不足一个 FFT 窗 */
    }

    klo = (uint32_t)ceilf(f_lo / df);
    khi = (uint32_t)floorf(f_hi / df);
    if (klo < 1u) klo = 1u;
    if (khi > (uint32_t)(N / 2u)) khi = (uint32_t)(N / 2u);
    if (klo > khi) {
        return -3;   /* 频率范围不合法 */
    }

    kmax = klo;
    for (k = klo; k <= khi; k++) {
        const float p = 10.0f * log10f(power[k] / (float)nseg + 1e-20f);
        if (p > pmax) {
            pmax = p;
            kmax = k;
        }
    }

    /* 抛物线插值（dB 域），把峰位估到亚 bin 精度 */
    {
        float delta = 0.0f;
        if (kmax > klo && kmax < khi) {
            const float a = 10.0f * log10f(power[kmax - 1u] / (float)nseg + 1e-20f);
            const float b = pmax;
            const float c = 10.0f * log10f(power[kmax + 1u] / (float)nseg + 1e-20f);
            const float denom = a - 2.0f * b + c;
            if (fabsf(denom) > 1e-12f) {
                delta = 0.5f * (a - c) / denom;
                if (delta >  0.5f) delta =  0.5f;
                if (delta < -0.5f) delta = -0.5f;
            }
        }
        *peak_hz = ((float)kmax + delta) * df;
    }
    *peak_db = pmax;
    return 0;
}
