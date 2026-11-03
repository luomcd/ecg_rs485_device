#ifndef ADS1293_ECG_OUT_H
#define ADS1293_ECG_OUT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * 输出级：853.24 Hz 滤波样本 -> 软件重采样(500/250 Hz) -> int16 定标裸流。
 * 2026-09-07 按方案《ECG_500-250Hz串口输出_方案_2026-09-07.md》实现。
 *
 * 位置：本模块放在既有滤波链（ecg_dc_block -> ecg_filter_f32）之后，
 *       "853 Hz 域先滤波 -> 后降采样 -> 最后定标"（顺序纪律，勿颠倒）。
 *
 * ── 宏配置（本文件顶部，用户可改，见方案 §3.2/§4）───────────────
 *   ECG_OUT_FS_HZ   500 或 250（默认 500）；派生周期 ECG_OUT_PERIOD_US。
 *   ECG_UV_PER_CODE 853 域 codes->µV（AECG100 1mV 实测 0.1122，2026-09-09 定标，只改这一行）
 *   ECG_LSB_OUT_UV  输出 LSB（±5mV<->int16±32767 => 0.1526 µV/LSB，已定稿）
 *
 * ── 重采样算法（相位累加 + 相邻样本线性插值）──────────────────────
 *   输入节拍 Tin = ECG_OUT_TIN_US(1172 µs，ADS1293 DRDY 实测)；
 *   输出节拍 Tout = ECG_OUT_PERIOD_US(2000/4000 µs)。
 *   每输入样本 acc += Tin；acc >= Tout 即有一输出样本落在"上一输入样本 ~
 *   当前输入样本"区间内，按分数位置 frac = (Tin-acc)/Tin 线性插值后 acc -= Tout。
 *   - 整数 µs 累加，无累计漂移（输出率 = 输入率×Tin/Tout，长期精确）；
 *   - 500 档每 128 输入约出 75 输出，250 档每 256 输入约出 75（见方案 §3.1）；
 *   - 本应用档位 Tin < Tout，每输入样本至多产生 1 个输出（if 而非 while）。
 *
 * ── int16 定标（方案 §4）────────────────────────────────────────
 *   out = clamp(round(y_codes × ECG_UV_PER_CODE / ECG_LSB_OUT_UV), ±32767)
 *   有符号小端裸流由调用方发送（直接取 (uint8_t*)&i16 两字节，低位在前）。
 *   上电延迟 ~5 s 再开始输出由上层控制（滤波链从 6M 直流起步的建立期）。
 */

/* ============ 输出域配置（用户可改的集中区） ============ */

/* 输出采样率档位：500 或 250（二选一，编译期校验见 ecg_out.c） */
#ifndef ECG_OUT_FS_HZ
#define ECG_OUT_FS_HZ           500u
#endif

/* 输出样本周期(µs)：编译期派生，勿手写 */
#ifndef ECG_OUT_PERIOD_US
#define ECG_OUT_PERIOD_US       (1000000u / ECG_OUT_FS_HZ)
#endif

/* 输入(ADS1293 DRDY)实测周期 µs：1e6/853.2423 ≈ 1172.0 */
#ifndef ECG_OUT_TIN_US
#define ECG_OUT_TIN_US          1172u
#endif

/* 两级换算（解耦，定标只改 ECG_UV_PER_CODE；ECG_LSB_OUT_UV 已定稿不动） */
#ifndef ECG_UV_PER_CODE
#define ECG_UV_PER_CODE         0.1122f    /* AECG100 1mV 实测校准(2026-09-09): R=0.88mV 口径; 1mV≈8920codes */
#endif
/* ── ECG_LSB_OUT_UV 定标说明（设计意图，勿"为兼容 ±300mV 偏置"而扩大）────────
 * 满量程 ±5 mV 是配生理信号(ECG p-p 1~3 mV)与上位机固定刻度定的：1 mV p-p 只占
 *    ±3277 LSB(10%)，稳态永不饱和，正常工况碰不到 clamp。
 * 超量程硬顶格(±32767) 是预期行为，防的是 int16 绕回画出假波形（医用 ECG 机遇
 *   除颤脉冲同样顶格，不会为瞬态牺牲 1 mV 的分辨率）。
 * 1 mV 与 300 mV 差 60 dB，扩量程会让 1 mV R 峰在屏上只剩 0.4 px 成平线；
 *   需看超量程瞬态请用五列 ASCII 的列5(filterraw, float, 不饱和)，勿用列4。
 */
#ifndef ECG_LSB_OUT_UV
#define ECG_LSB_OUT_UV          0.1526f    /* ±5 mV <-> int16 ±32767 (5mV/32767) */
#endif

/* 合并增益 codes -> LSB 数（编译期折叠为常量，供内部使用） */
#define ECG_CODES_TO_LSB        (ECG_UV_PER_CODE / ECG_LSB_OUT_UV)

/* 每批输入/输出样本数（仅供注释与块级参考；本实现为逐样本相位累加，不依赖块节拍） */
#define ECG_OUT_IN_PER_BLOCK    ((ECG_OUT_FS_HZ == 500u) ? 128u : 256u)
#define ECG_OUT_SAMP_PER_BLOCK  75u

/* ============ 重采样器（853Hz 滤波 codes -> 500/250Hz codes） ============ */

typedef struct {
    float    y_prev;      /* 上一输入滤波样本（区间插值起点） */
    uint32_t acc_us;      /* 自上次输出以来累计的输入时间(0..Tout-1) */
    uint32_t tin_us;      /* ECG_OUT_TIN_US */
    uint32_t tout_us;     /* ECG_OUT_PERIOD_US */
    uint8_t  first;       /* 首样本标记：直接输出 y0(输出网格 t=0) */
} ecg_resample_f32;

void ecg_resample_f32_init(ecg_resample_f32 *r);

/* 每输入一个 853Hz 滤波样本调用一次；
 * 返回 1 且有 *out 写入 -> 本样本区间内有一个 500/250Hz 输出样本；
 * 返回 0 -> 无输出。 */
int  ecg_resample_f32_process(ecg_resample_f32 *r, float y, float *out);

/* ============ int16 定标（codes -> µV -> LSB，独立可测） ============ */

float    ecg_out_codes_to_uv(float y_codes);   /* y_codes × ECG_UV_PER_CODE */
int16_t  ecg_out_codes_to_lsb(float y_codes);  /* round + clamp(±32767) */

/* ============ 一步组合输出级 ============ */

typedef struct {
    ecg_resample_f32 rs;   /* 先重采样(500/250Hz codes)再定标 */
} ecg_out_f32;

void ecg_out_f32_init(ecg_out_f32 *o);

/* 每输入一个 853Hz 滤波样本调用一次；
 * 返回 1 且 *out_lsb 写入 -> 有一个 int16 输出样本待发送；
 * 返回 0 -> 无输出。 */
int ecg_out_f32_process(ecg_out_f32 *o, float y_codes, int16_t *out_lsb);

#ifdef __cplusplus
}
#endif

#endif /* ADS1293_ECG_OUT_H */
