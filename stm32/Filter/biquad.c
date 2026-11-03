#include "biquad.h"

void biquad_df2t_f32_set(biquad_df2t_f32 *f,
                         float b0, float b1, float b2,
                         float a1, float a2)
{
    if (!f) return;
    f->b0 = b0;
    f->b1 = b1;
    f->b2 = b2;
    f->a1 = a1;
    f->a2 = a2;
    f->d1 = 0.0f;
    f->d2 = 0.0f;
}

void biquad_df2t_f32_reset(biquad_df2t_f32 *f)
{
    if (!f) return;
    f->d1 = 0.0f;
    f->d2 = 0.0f;
}

float biquad_df2t_f32_process(biquad_df2t_f32 *f, float x)
{
    const float y = f->b0 * x + f->d1;
    f->d1 = f->b1 * x - f->a1 * y + f->d2;
    f->d2 = f->b2 * x - f->a2 * y;
    return y;
}

void biquad_df2t_f64_set(biquad_df2t_f64 *f,
                         double b0, double b1, double b2,
                         double a1, double a2)
{
    if (!f) return;
    f->b0 = b0;
    f->b1 = b1;
    f->b2 = b2;
    f->a1 = a1;
    f->a2 = a2;
    f->d1 = 0.0;
    f->d2 = 0.0;
}

void biquad_df2t_f64_reset(biquad_df2t_f64 *f)
{
    if (!f) return;
    f->d1 = 0.0;
    f->d2 = 0.0;
}

double biquad_df2t_f64_process(biquad_df2t_f64 *f, double x)
{
    const double y = f->b0 * x + f->d1;
    f->d1 = f->b1 * x - f->a1 * y + f->d2;
    f->d2 = f->b2 * x - f->a2 * y;
    return y;
}
