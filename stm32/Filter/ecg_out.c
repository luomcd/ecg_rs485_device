#include "ecg_out.h"

/* 编译期档位校验：只允许 500 / 250 两档 */
#if (ECG_OUT_FS_HZ != 500u) && (ECG_OUT_FS_HZ != 250u)
#error "ECG_OUT_FS_HZ must be 500 or 250"
#endif

#if ECG_OUT_PERIOD_US > ECG_OUT_TIN_US
/* Tin < Tout：每输入样本至多产生 1 个输出（if 即可）。 */
#endif

void ecg_resample_f32_init(ecg_resample_f32 *r)
{
    if (!r) return;
    r->y_prev  = 0.0f;
    r->acc_us  = 0u;
    r->tin_us  = ECG_OUT_TIN_US;
    r->tout_us = ECG_OUT_PERIOD_US;
    r->first   = 1u;
}

/*
 * 相位累加 + 相邻样本线性插值（详见 ecg_out.h 头注释）。
 *
 * 时序推导（整数 µs，无浮点时钟、无累计漂移）：
 *   输出网格 o_m = m×Tout；输入样本 k 时刻 t_k = k×Tin。
 *   每输入样本 acc += Tin。当 acc >= Tout，网格点 o = t_k - (acc-Tout)
 *   落在 (t_{k-1}, t_k] 区间，相对上一输入样本的分数位置
 *     frac = (o - t_{k-1}) / Tin = (Tin - (acc-Tout)) / Tin，
 *   插值 y_out = y_prev + (y - y_prev) × frac。
 *   首样本(first)时输出网格 o_0=0 与样本 k=0 重合，直接输出 y0。
 */
int ecg_resample_f32_process(ecg_resample_f32 *r, float y, float *out)
{
    uint32_t acc;
    float    frac;

    if (!r || !out) return 0;

    if (r->first) {
        r->y_prev = y;        /* 记住区间起点 */
        r->acc_us = 0u;
        r->first  = 0u;
        *out = y;             /* o_0 = t_0 = 0：输出样本 0 */
        return 1;
    }

    acc = r->acc_us + r->tin_us;
    if (acc >= r->tout_us) {
        acc -= r->tout_us;
        /* acc 现在是 o 距当前输入样本 t_k 的回看时间；Tin < Tout => 0 <= acc < Tin */
        frac = (float)(r->tin_us - acc) / (float)r->tin_us;   /* (0,1] */
        if (frac < 0.0f) frac = 0.0f;   /* 防御：理论上 Tin<Tout 不会发生 */
        *out = r->y_prev + (y - r->y_prev) * frac;
        r->acc_us = acc;
        r->y_prev = y;
        return 1;
    }

    r->acc_us = acc;
    r->y_prev = y;
    return 0;
}

/* ---------------- int16 定标 ---------------- */

float ecg_out_codes_to_uv(float y_codes)
{
    return y_codes * ECG_UV_PER_CODE;      /* codes -> µV（853 域 LSB） */
}

int16_t ecg_out_codes_to_lsb(float y_codes)
{
    const float uv   = y_codes * ECG_UV_PER_CODE;   /* codes -> µV */
    const float lsb  = uv / ECG_LSB_OUT_UV;         /* µV -> 输出 LSB 数 */
    int32_t q;

    /* 饱和 clamp（不绕回）：启动瞬态/意外过幅直接顶格 */
    if (lsb >= 32767.0f) return (int16_t)32767;
    if (lsb <= -32768.0f) return (int16_t)(-32768);

    /* 手写 round（避开 lrintf 库调用）：先取整再二次 clamp 防边界 */
    q = (int32_t)(lsb >= 0.0f ? lsb + 0.5f : lsb - 0.5f);
    if (q > 32767)  q = 32767;
    if (q < -32768) q = -32768;
    return (int16_t)q;
}

/* ---------------- 一步组合 ---------------- */

void ecg_out_f32_init(ecg_out_f32 *o)
{
    if (!o) return;
    ecg_resample_f32_init(&o->rs);
}

int ecg_out_f32_process(ecg_out_f32 *o, float y_codes, int16_t *out_lsb)
{
    float y_rs;
    if (!o || !out_lsb) return 0;
    if (ecg_resample_f32_process(&o->rs, y_codes, &y_rs)) {
        *out_lsb = ecg_out_codes_to_lsb(y_rs);
        return 1;
    }
    return 0;
}
