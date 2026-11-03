/**
 * @file ecg_rpeak.h
 * @brief R 峰检测 + BPM（S7）—— `HeartRateDetector.cs` 的 C 移植
 *
 * 算法来源：`WinApp_ADSECG/ADS1293_Ecg/Processing/HeartRateDetector.cs`（355 行）
 * 流水线：去基线 → 低通 → 高通 → **四点**微分 → 平方 → 60 样本积分 → 自适应阈值
 *         → 不应期 → 回溯搜索 → LocateRPeak
 *   （⚠ 是**四点**微分 4 抽头 `(2,1,-1,-2)/8`，不是教科书 5 抽头；见 .c 第 3 级注释）
 *
 * 移植改动（需求 §5.2）：
 *   - `double` → `float`（S3 只有单精度硬件 FPU，双精度走软件浮点慢一个量级）
 *   - `Queue<T>` → 定长环形数组（免动态分配）
 *   - `lock` → 无（Core 1 独占，状态量任务私有）
 *
 * ⚠ **为与参考实现逐位对齐，判据相关的东西一个都没省**：
 *   阈值公式、T 波判别（spacing<360 && slope<0.5*lastSlope）、
 *   回溯搜索（gap > 1.66×avgRR）、LocateRPeak（窗内找 |value| 最大）全部照搬。
 *   ⚠ LocateRPeak 的**窗口半径是唯一例外**：移植时照搬 ±55，2026-10-27 由 Q6 收窄到
 *     **±40**（见下方 `ECG_RP_LOCATE_HALF`）。C# 参考端（WinApp）**仍是 ±55，未跟随** ——
 *     两条路线对账前必须先对齐，否则 55/40 的差异会盖过模拟链路的真实影响。
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

/* 🛑 定位窗半径。**加 #ifndef 保护是为了让 PC 端 host 夹具能做窗宽敏感性实验**
 *   （`gcc -DECG_RP_LOCATE_HALF=30` 编另一个版本跑同一批数据），固件编译时无人定义它。
 *
 * 📌 2026-10-27 由 **55 收窄为 40**（Q6 定案，依据见
 *   `test_logs/CardioLite_Q6_定位窗宽_20261027.md`）：
 *   · MIT-BIH 48 条 108,000 拍：更准 565 拍 : 更差 99 拍（**5.7 : 1**），
 *     |偏移| 均 6.12 → 5.81 样本，Se 99.08→99.11 / +P 99.06→99.09 / DER 1.86→1.80（三项全改善）
 *   · 阶梯 8 档（30~180 bpm）：**BPM 偏差一位不变**，RR 抖动差 ≤0.1 样本
 *   · S7 验收文件（60 bpm）：有效区间 63 个真峰 **逐位零位移**
 *   · W=30 收益更大但风险比仅 2.3:1；**W≤20 明确有害**（|偏移| 反弹到 7.12、更准:更差 1:4）
 *
 *   为什么能收窄：`locate_rpeak()` 的返回值会写进 `last_peak`，参与 T 波判别与不应期，
 *   所以窗太宽时**相邻心搏的更大极值会被吸进窗内**（RR=250 时 ±55 ⇒ 窗占 RR 44 %）。
 *   收到 ±40 后仍覆盖真峰的正常偏离（回退 30 ± 10），但砍掉了远端干扰。
 *
 *   ⚠ 原 Q6 提议的「自适应 min(55, RR/4)」**无效**：RR=250（120 bpm）时 RR/4 = 62.5 > 55，
 *     min 仍取 55，根本不会收窄。要用自适应得用 RR/8 一类的系数，实测没有必要。 */
#ifndef ECG_RP_LOCATE_HALF
#define ECG_RP_LOCATE_HALF       40     /* LocateRPeak 搜索半径 ±40 样本（原 55） */
#endif
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
