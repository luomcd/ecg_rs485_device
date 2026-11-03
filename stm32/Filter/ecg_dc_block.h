#ifndef ADS1293_ECG_DC_BLOCK_H
#define ADS1293_ECG_DC_BLOCK_H

#include "ecg_filter_coeffs.h"   /* ECG_FS_HZ_D */

#ifdef __cplusplus
extern "C" {
#endif

/* ---- T142b (2026-09-28)：预热跳帧 SKIP --------------------------------
 * 背景：ADS1293 ECG 通道 SINC 建立时间 ts = 5 × R1·R2·R3 / fS
 *       = 5 个输出样本（datasheet SNAS602C §8.3.8；本机 ODR 853.24 Hz
 *       ⇒ 5.86 ms）⇒ START 后**前 5 帧数据未建立**。
 *       而 process() 的预热分支会把首帧当 x_prev；若首帧恰是
 *       未建立的异常点（偏高/偏低 δ），差分式 (x[1]-x[0]) = ∓δ
 *       **直接产生一个冲激**，之后按 r^(n-1) 以 τ = 1/(2π·fc) ≈ 7.96 s 衰减
 *       ⇒ 表现为「屏幕基线整条平移、20 s 才回爬完」。
 * 做法：预热期从 1 帧延长为 (SKIP + 1) 帧 —— 前 SKIP 帧只推进 x_prev、
 *       不产生差分，第 (SKIP+1) 帧才定基准。
 *       SKIP = 0 ⇒ 与旧行为**逐字节等价**（用于 A/B 对比）。
 * 依据：process() 是差分式，对常数输入输出恒 0 ⇒ x_prev 只需取到
 *       "已稳定的值"，无需均值（均值只抗噪、不改变本机制）。
 * 选址：dc_block 是滤波链**第一级**（ads1293.c:1098-1099）⇒ 此处丢弃的样本
 *       不会污染后续 ecg_filter_f64。
 * 成本：结构体新增 int 落在原 padding 内 ⇒ sizeof 不变（32 B，±0 RAM）；
 *       稳态路径零额外计算；预热期每样本 1 次比较。
 */
#ifndef ECG_DC_BLOCK_WARM_SKIP
#define ECG_DC_BLOCK_WARM_SKIP   8    /* 0 = 关闭(旧行为) / 8 = 5 帧建立期的 1.60 倍 */
#endif
#if (ECG_DC_BLOCK_WARM_SKIP < 0) || (ECG_DC_BLOCK_WARM_SKIP > 64)
#error "ECG_DC_BLOCK_WARM_SKIP 取值非法（允许 0 ~ 64）"
#endif

/* ---- T160 (2026-09-29)：START 后「快相」时变 fc -------------------------
 * 背景（T159 实测）：源开/START 后基线按 τ = 1/(2π·0.02 Hz) = 7.96 s 松弛到
 *   D = −mean(源)；实测到 ±0.3 px 需 **16.8 s**、到 ±1 µV 需 35~51 s。
 *   猛哥否掉「接受 16.8 s」——真机接上导联就该出波形，不该有可感知的预热。
 * 做法：START 后前 WARMUP_MS 毫秒用**较高**的 fc（快相），随后切回本档稳态 fc。
 *   快相 fc_hi ⇒ τ_hi = 1/(2π·fc_hi)，残余 = |b₀|·e^(−t/τ_hi)：
 *     0.5 Hz ⇒ τ_hi = 0.318 s ⇒ 2 s = 6.28τ ⇒ 残余 0.19%（最坏 b₀=986 µV ⇒ 1.84 µV ≈ 0.06 px）
 *     1.0 Hz ⇒ τ_hi = 0.159 s ⇒ 2 s = 12.6τ ⇒ 残余 ≈ 0（但 1 Hz 成分只保留 71%）
 * 为什么不违反判据 a：快相**只改泄漏速度 r**，不改稳态 fc ⇒ 稳态 Σf（低端极点预算）
 *   与「a1 = A·T·2π·Σf」完全不受影响（依据 T83b/T84）。
 * 切换跳变：Δy = Δr·y_prev，Δr = 2π·(fc_s − fc_hi)/FS = −0.003535
 *   ⇒ 切换瞬间 y_prev ≈ 1.8 µV（快相 2 s 后的残余）⇒ Δy ≈ 0.006 µV，可忽略。
 * 关闭：ECG_DC_BLOCK_WARMUP_MS = 0 ⇒ warm_left 恒 0 ⇒ 与 T159 前**逐字节等价**（A/B 用）。
 * 落点：只动本模块（.h/.c）；ads1293.c **零改动** —— ecg_dc_block_init() 正是三条
 *   START 入口（协议 0x01 / ASCII `test afe start` / reset_afe）的公共复位点（ads1293.c:385）。
 * 语义：init() 进快相（START 时）；reset() **不进** —— 保持 fast_restore_snippet.c
 *   伪迹重同步路径的既有行为不变。若将来要让伪迹后也加速，在 reset() 加同一行即可。
 * 成本：结构体 +1 个 int（32 → 40 B，+8 B RAM）；稳态路径零额外计算；
 *   快相期每样本 1 次比较。稳态分支对 r 的读取与旧版完全一致。
 */
#ifndef ECG_DC_BLOCK_WARMUP_FC_HZ_F
#define ECG_DC_BLOCK_WARMUP_FC_HZ_F   0.50f   /* 快相 fc（Hz）*/
#endif
#ifndef ECG_DC_BLOCK_WARMUP_MS
#define ECG_DC_BLOCK_WARMUP_MS        2000    /* 快相时长(ms)；0 = 关闭(旧行为) */
#endif
#if (ECG_DC_BLOCK_WARMUP_MS < 0) || (ECG_DC_BLOCK_WARMUP_MS > 10000)
#error "ECG_DC_BLOCK_WARMUP_MS 取值非法（允许 0 ~ 10000 ms；0 = 关闭快相、退回 T159 行为）"
#endif

/* 快相样本数（运行期常量表达式，编译器折叠；+0.5 为四舍五入）*/
#define ECG_DC_BLOCK_WARMUP_SAMPLES \
    ((int)(((double)ECG_DC_BLOCK_WARMUP_MS * ECG_FS_HZ_D) / 1000.0 + 0.5))

/*
 * 直流消除器（DC blocker，一阶高通），2026-09-05 新增。
 *
 * 为什么必须用它（2026-09-05 用真实数据逐样本仿真复现，见 README "输入前置条件"）：
 *   ADS1293 的 24-bit 原始码中心在 ~6.075M（由配置决定的直流偏置），ECG 信号只是
 *   叠在其上的 ±几千 codes 波动。若不先去直流直接把 (float)raw 喂 ecg_filter_*：
 *     1) 0.5Hz 高通对 6M 阶跃的建立时间实测长达 15~18s —— 前十几秒输出是
 *        ±数百万 codes 的衰减瞬态（float32 与 float64 都一样）；
 *     2) float32 存 6M 量级输入时，近单位圆极点把舍入噪声放大到 ~800 codes
 *        稳态底噪（float64 同输入仅 ~58 codes，见 mcu_emu.py）。
 *   先经本模块把直流消掉、只留 ±几千 codes 波动后，float32 滤波链精度完全够用
 *   （实测稳态 σ ≈ 58 codes，与 PC 减中值口径一致）。
 *
 * 算法：y[n] = (x[n] - x[n-1]) + r * y[n-1]，r = 1 - 2*pi*fc/fs（默认 fc=0.05Hz，v3 2026-09-09）
 * 内部状态用 double：x_prev 是 ~6M 量级，float32 的 24-bit 尾数存它会损失精度，
 * double 的 53-bit 尾数无损；对外接口 float。一阶运算在 853 SPS 下开销可忽略。
 *
 * 预热：前 (ECG_DC_BLOCK_WARM_SKIP + 1) 个样本只推进 x_prev 并输出 0；
 * 避免 y[0]=x[0]=6M 的尖峰，同时跳过 START 后的 SINC 未建立段
 * （T142b，详见文件头部 SKIP 说明）。
 */

typedef struct {
    double x_prev;   /* 上一输入样本（~6M 量级，必须 double 存储）*/
    double y_prev;   /* 上一输出 */
    double r;        /* 1 - 2*pi*fc/fs（本档**稳态** fc）*/
    int    warmed;   /* 预热标记（跳帧用尽后置 1）*/
    int    warm_skip; /* T142b：预热期剩余跳帧数（落在原 padding 内，sizeof 不变）*/
    int    warm_left; /* T160：快相剩余样本数（>0 ⇒ 用快相 fc）⇒ sizeof 32 → 40 B（+8 B）*/
} ecg_dc_block;

void  ecg_dc_block_init(ecg_dc_block *b, float fc_hz);  /* fc<=0 用默认 0.05Hz */
void  ecg_dc_block_reset(ecg_dc_block *b);              /* 清状态, 重新预热 */
float ecg_dc_block_process(ecg_dc_block *b, float x);   /* 返回已去直流的波动 */

#ifdef __cplusplus
}
#endif

#endif /* ADS1293_ECG_DC_BLOCK_H */
