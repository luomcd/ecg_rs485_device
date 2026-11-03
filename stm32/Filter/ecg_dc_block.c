#include "ecg_dc_block.h"
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* T160：快相 fc 对应的递推系数（编译期常量表达式，编译器折叠）*/
#define ECG_DC_BLOCK_WARMUP_R \
    (1.0 - 2.0 * M_PI * (double)ECG_DC_BLOCK_WARMUP_FC_HZ_F / ECG_FS_HZ_D)

void ecg_dc_block_init(ecg_dc_block *b, float fc_hz)
{
    double fc;
    if (!b) return;
    fc = (fc_hz > 0.0f) ? (double)fc_hz : 0.05;  /* 默认 fc = 0.05 Hz (v3, 2026-09-09) */
    b->r      = 1.0 - 2.0 * M_PI * fc / ECG_FS_HZ_D;
    b->x_prev = 0.0;
    b->y_prev = 0.0;
    b->warmed = 0;
    b->warm_skip = ECG_DC_BLOCK_WARM_SKIP;          /* T142b */
    b->warm_left = ECG_DC_BLOCK_WARMUP_SAMPLES;     /* ★T160：START 后先跑「快相」*/
}

void ecg_dc_block_reset(ecg_dc_block *b)
{
    if (!b) return;
    b->x_prev = 0.0;
    b->y_prev = 0.0;
    b->warmed = 0;
    b->warm_skip = ECG_DC_BLOCK_WARM_SKIP;   /* T142b */
    b->warm_left = 0;                        /* ★T160：reset 不进快相（保持伪迹重同步的既有行为）*/
}

float ecg_dc_block_process(ecg_dc_block *b, float x)
{
    double y, r;
    if (!b) return x;
    if (!b->warmed) {                 /* 预热期: 只推进 x_prev, 不产生差分 */
        b->x_prev = (double)x;
        if (b->warm_skip > 0) {       /* T142b: 跳帧未用尽 ⇒ 继续跳(输出 0 = 正确基线) */
            b->warm_skip--;
            return 0.0f;
        }
        b->warmed = 1;
        return 0.0f;
    }
    r = b->r;                         /* 本档稳态 fc */
    if (b->warm_left > 0) {           /* ★T160：快相 —— 高 fc ⇒ 启动残值按更短 τ 泄漏掉 */
        b->warm_left--;
        r = ECG_DC_BLOCK_WARMUP_R;
    }
    y = ((double)x - b->x_prev) + r * b->y_prev;
    b->x_prev = (double)x;
    b->y_prev = y;
    return (float)y;
}
