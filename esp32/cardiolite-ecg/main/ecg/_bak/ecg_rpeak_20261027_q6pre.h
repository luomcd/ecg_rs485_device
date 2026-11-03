/**
 * @file ecg_rpeak.h
 * @brief R 峰检测 + BPM（S7）—— `HeartRateDetector.cs` 的 C 移植
 *
 * 算法来源：`WinApp_ADSECG/ADS1293_Ecg/Processing/HeartRateDetector.cs`（355 行）
 * 流水线：低通 → 高通 → 三点微分 → 平方 → 60 样本积分 → 自适应阈值 → 不应期
 *
 * 移植改动（需求 §5.2）：
 *   - `double` → `float`（S3 只有单精度硬件 FPU，双精度走软件浮点慢一个量级）
 *   - `Queue<T>` → 定长环形数组（免动态分配）
 *   - `lock` → 无（Core 1 独占，状态量任务私有）
 *
 * ⚠ **为与参考实现逐位对齐，判据相关的东西一个都没省**：
 *   阈值公式、T 波判别（spacing<360 && slope<0.5*lastSlope）、
 *   回溯搜索（gap > 1.66×avgRR）、LocateRPeak（±55 窗口找 |value| 最大）全部照搬。
 *   **省掉的只有纯诊断字段**：`baselineSlow` / `rawHistory` / `recent`+`amplitude`
 *   （后者在 C# 里只赋给 `lastAmplitude`，不参与任何判据；跳过它还顺手免了 O(n²) 的滑窗求 max）。
 *
 * BPM 口径（**以源码为准，不是需求 §5.3 的"均值"**）：
 *   最近 5 个 RR → 过滤 [80, 1000] → **排序取中位数** → `HR = 60*fs/rr`
 *   需求 §5.3 写的是"均值"，但源码 `AcceptPeak()` 里明确是 median。
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

/* ---- 与参考实现一致的常量 ---- */
#define ECG_RP_REFRACTORY        80     /* 不应期 160 ms */
#define ECG_RP_MIN_RR            80     /* 最小 RR 160 ms */
#define ECG_RP_INTEG_WIN         60     /* 积分窗 120 ms */
#define ECG_RP_LOWPASS_A         0.25f
#define ECG_RP_HIGHPASS_A        0.05f
#define ECG_RP_BASELINE_A        0.002f /* 主基线跟踪（注意：不是 0.25） */

#define ECG_RP_FILT_HIST         140    /* filteredHistory 深度 */
#define ECG_RP_CAND_MAX          64     /* candidates 深度 */
#define ECG_RP_PEAK_MAX          32     /* peaks 深度（与 C# 一致：只保留最近 32 个） */

#define ECG_RP_LOCATE_HALF       55     /* LocateRPeak 搜索半径 ±55 样本 */
#define ECG_RP_AVG_RR_TAKE       5      /* BPM 取最近 5 个 RR */

typedef struct {
    int64_t index;
    float   value;
} ecg_rp_hist_t;

typedef struct {
    int64_t index;
    float   value;
    float   slope;
} ecg_rp_cand_t;

typedef struct {
    uint32_t fs;

    /* 滤波/微分状态 */
    float    baseline;
    float    low_pass;
    float    high_pass;
    float    v_cur;        /* value[n]     */
    float    v_p1;         /* value[n-1]   */
    float    v_p2;         /* value[n-2]   */
    float    v_p3;         /* value[n-3]   */
    float    deriv_p1;     /* derivative[n-1] */

    /* 积分窗 */
    float    integ_win[ECG_RP_INTEG_WIN];
    int      integ_win_n;  /* 当前个数（≤ 60） */
    int      integ_win_pos;
    float    integ_sum;

    /* 阈值估计 */
    float    signal_peak;
    float    noise_peak;
    float    last_accepted_slope;

    /* 上一个/上上个积分值（峰检测用） */
    float    integ_p1;
    float    integ_p2;

    /* 峰历史（定长环） */
    int64_t  peaks[ECG_RP_PEAK_MAX];
    int      peak_n;
    int      peak_pos;
    int64_t  last_peak;    /* INT64_MIN = 还没有 */

    /* filteredHistory（LocateRPeak 用） */
    ecg_rp_hist_t filt_hist[ECG_RP_FILT_HIST];
    int      filt_n;
    int      filt_pos;

    /* candidates（回溯搜索用） */
    ecg_rp_cand_t cand[ECG_RP_CAND_MAX];
    int      cand_n;
    int      cand_pos;

    /* 统计 */
    int64_t  sample_index;
    uint32_t peak_total;   /* 累计检出峰数（不做环覆盖，只增） */
    float    bpm;
    int64_t  last_rr;
} ecg_rpeak_t;

/** @brief 初始化（fs 必须与数据一致，当前固定 500） */
void ecg_rpeak_init(ecg_rpeak_t *rp, uint32_t fs);

/** @brief 清零所有状态（清屏时调用） */
void ecg_rpeak_reset(ecg_rpeak_t *rp);

/**
 * @brief 喂一个样本。
 * @param out_index 检出峰时写入该峰的样本索引
 * @return true = 本次调用检出了新峰
 */
bool ecg_rpeak_process(ecg_rpeak_t *rp, float sample, int64_t *out_index);

/** @brief 当前 BPM（0 = 尚不可用，UI 显示 `--`） */
float ecg_rpeak_bpm(const ecg_rpeak_t *rp);

/** @brief 累计检出峰数 */
uint32_t ecg_rpeak_peak_count(const ecg_rpeak_t *rp);

/** @brief 最近一个峰的索引（INT64_MIN = 无） */
int64_t ecg_rpeak_last_peak(const ecg_rpeak_t *rp);

/** @brief 已处理样本数 */
int64_t ecg_rpeak_sample_index(const ecg_rpeak_t *rp);
