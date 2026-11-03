#ifndef ADS1293_ECG_BIQUAD_H
#define ADS1293_ECG_BIQUAD_H

#ifdef __cplusplus
extern "C" {
#endif

/*
 * 二阶 IIR 节（biquad），Direct Form II Transposed（DF2T）。
 *
 *   H(z) = (b0 + b1*z^-1 + b2*z^-2) / (1 + a1*z^-1 + a2*z^-2)
 *
 *   y[n] = b0*x[n] + d1
 *   d1   = b1*x[n] - a1*y[n] + d2
 *   d2   = b2*x[n] - a2*y[n]
 *
 * 约定：a0 恒为 1；调用者传入的 a1/a2 即上式中的 a1/a2（标准负号约定）。
 */

typedef struct {
    float  b0, b1, b2;
    float  a1, a2;
    float  d1, d2;              /* DF2T 状态 */
} biquad_df2t_f32;

typedef struct {
    double b0, b1, b2;
    double a1, a2;
    double d1, d2;
} biquad_df2t_f64;

void  biquad_df2t_f32_set(biquad_df2t_f32 *f,
                          float b0, float b1, float b2,
                          float a1, float a2);
void  biquad_df2t_f32_reset(biquad_df2t_f32 *f);
float biquad_df2t_f32_process(biquad_df2t_f32 *f, float x);

void   biquad_df2t_f64_set(biquad_df2t_f64 *f,
                           double b0, double b1, double b2,
                           double a1, double a2);
void   biquad_df2t_f64_reset(biquad_df2t_f64 *f);
double biquad_df2t_f64_process(biquad_df2t_f64 *f, double x);

#ifdef __cplusplus
}
#endif

#endif /* ADS1293_ECG_BIQUAD_H */
