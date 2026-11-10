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

/* ============================================================================
 * ★T224/T225（2026-11-10）：落点「双坐标」最小修复 —— 候选入池即保存 fid 偏移
 *
 * 问题（T224 §3）：回溯路径在**很晚**的时刻才调 `locate_rpeak(c->index)`，
 *   此时 `[trigger±40]` 早已滑出 `filt_hist`（140）：
 *     · 整个窗口滑出 ⇒ found=false ⇒ 原样返回 trigger（误差 ~9 样本）；
 *     · 只有半边可见 ⇒ found=true 但在残缺窗口里挑极值（误差可达 ~27，且指标查不出来）。
 *   实测 90 bpm 21 个回溯峰 100% 未精修、120 bpm 13 个部分覆盖。
 *
 * 修法：`locate_rpeak()` 在**候选入池那一刻**执行一次（此刻窗口覆盖与主路径完全相同），
 *   把 `fid - trigger` 存进并列数组；回溯直接取用。**与心率无关**——不像加大
 *   `ECG_RP_FILT_HIST` 那样，所需历史 ≈1.66×RR−80+41 会随心率线性增长
 *   （60 bpm 要 ≥794、40 bpm 要 ≥1170，512 都不够）。
 *
 * 🛑 这是 **P3A 最小修复**，不是完整的双坐标重构（T225 §4.3）：
 *   触发、`spacing`、不应期、候选筛选与删除**全部继续用 trigger**，
 *   `last_peak` 的语义也**不变**。要彻底分坐标是 P3B，别在这里顺手改。
 *
 * 置 0 可编译出「现状基线」，用于逐峰证明主路径没被改动（T225 §5.2）。
 * ========================================================================== */
#ifndef ECG_RP_USE_STORED_FID
#define ECG_RP_USE_STORED_FID    1
#endif

/* 诊断模式（T225 §6.3）：记录最近一次接受峰的触发/落点/滞后/窗宽/来源。
 * 只写字段、不参与任何判据；默认开（20 B），置 0 可省。 */
#ifndef ECG_RP_DIAG
#define ECG_RP_DIAG              1
#endif

typedef struct {
    int64_t index;
    float   value;
} ecg_rp_hist_t;

typedef struct {
    int64_t index;
    float   value;
    float   slope;
} ecg_rp_cand_t;

#if ECG_RP_DIAG
/** 最近一次「接受峰」的决策上下文（只读诊断，不参与判据） */
typedef struct {
    int64_t trigger;   /* 粗触发坐标（积分峰回退 30） */
    int64_t fid;       /* 最终落点 */
    int64_t lag;       /* 回溯触发时 trigger 距当前样本的滞后；主路径为 0 */
    int16_t win;       /* 本次 locate 在窗口内真正扫到的样本数（满窗 72） */
    int8_t  src;       /* 0 = 主路径，1 = 回溯路径 */
    int8_t  win_short; /* 1 = 窗口不满（发生了截断） */
} ecg_rp_diag_t;
#endif

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
#if ECG_RP_USE_STORED_FID
    /* 🛑 并列数组、不要塞进 ecg_rp_cand_t：结构体是 int64+float+float = 16 B，
     *    塞一个 int16 会被 8 字节对齐撑到 24 B（64 项白涨 512 B）。
     *    这里只涨 64×2 = 128 B，且不动原结构布局（T225 §4.1）。 */
    int16_t  cand_fid_delta[ECG_RP_CAND_MAX];   /* fid - trigger */
#if ECG_RP_DIAG
    uint8_t  cand_win[ECG_RP_CAND_MAX];         /* 入池时窗口真正扫到的样本数（≤72） */
#endif
#endif
    int      cand_n;
    int      cand_pos;

#if ECG_RP_DIAG
    ecg_rp_diag_t last_diag;
#endif

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

#if ECG_RP_DIAG
/** @brief 最近一次接受峰的决策上下文（诊断/回归用，不参与判据） */
const ecg_rp_diag_t *ecg_rpeak_last_diag(const ecg_rpeak_t *rp);
#endif
