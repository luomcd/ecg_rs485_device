/**
 * @file ecg_rpeak.c
 * @brief R 峰检测 C 实现 —— 逐行对应 HeartRateDetector.cs
 *
 * 对照关系（C# 行号 → 本文件）：
 *   Process()            :113-229  → ecg_rpeak_process()
 *   LocateRPeak()        :231-257  → locate_rpeak()
 *   AcceptPeak()         :259-288  → accept_peak()
 *   Clear()              :315-354  → ecg_rpeak_reset()
 */

#include "ecg_rpeak.h"

#include <math.h>
#include <string.h>

#define NO_PEAK     INT64_MIN
#define TINY        1e-12f

/* ============================================================================
 * 环形缓冲辅助
 * ========================================================================== */

static inline int64_t peak_at(const ecg_rpeak_t *rp, int k)
{
    /* k = 0 表示最旧的一个 */
    const int idx = (rp->peak_pos - rp->peak_n + k + ECG_RP_PEAK_MAX * 2) % ECG_RP_PEAK_MAX;
    return rp->peaks[idx];
}

static void peak_push(ecg_rpeak_t *rp, int64_t idx)
{
    rp->peaks[rp->peak_pos] = idx;
    rp->peak_pos = (rp->peak_pos + 1) % ECG_RP_PEAK_MAX;
    if (rp->peak_n < ECG_RP_PEAK_MAX) {
        rp->peak_n++;
    }
}

/* ============================================================================
 * 内部：LocateRPeak —— 在 ±`ECG_RP_LOCATE_HALF`（当前 40）样本窗口的 filteredHistory
 *   里找 |value| 最大的点。⚠ 窗口半径**只引用宏、不写字面量** —— 2026-10-27 就吃过
 *   一次亏：Q6 把宏从 55 改到 40，这里还留着 "±55"，读代码的人会按错的值推理。
 * ========================================================================== */

static int64_t locate_rpeak(const ecg_rpeak_t *rp, int64_t integrated_peak)
{
    if (rp->filt_n == 0) {
        return integrated_peak;
    }

    const int64_t start = integrated_peak - ECG_RP_LOCATE_HALF;
    const int64_t end   = integrated_peak + ECG_RP_LOCATE_HALF;

    int64_t best_idx     = integrated_peak;
    float   best_abs     = -1.0f;
    bool    found        = false;

    /* 遍历环形里的全部有效项（≤140），挑 index 落在窗口内且 |value| 最大的 */
    const int first = (rp->filt_pos - rp->filt_n + ECG_RP_FILT_HIST * 2) % ECG_RP_FILT_HIST;
    for (int k = 0; k < rp->filt_n; k++) {
        const ecg_rp_hist_t *h = &rp->filt_hist[(first + k) % ECG_RP_FILT_HIST];
        if (h->index < start || h->index > end) {
            continue;
        }
        const float a = fabsf(h->value);
        if (!found || a > best_abs) {
            found    = true;
            best_abs = a;
            best_idx = h->index;
        }
    }
    return found ? best_idx : integrated_peak;
}

/* ============================================================================
 * 内部：BPM（最近 5 个 RR → 过滤 [80,1000] → 排序 → 中位数）
 * ========================================================================== */

static void update_bpm(ecg_rpeak_t *rp)
{
    if (rp->peak_n < 2) {
        return;
    }

    /* 取最近 5 个 RR（相邻峰差） */
    float rr[ECG_RP_AVG_RR_TAKE];
    int   n = 0;
    const int last = rp->peak_n - 1;
    for (int k = last; k >= 1 && n < ECG_RP_AVG_RR_TAKE; k--) {
        const float d = (float)(peak_at(rp, k) - peak_at(rp, k - 1));
        if (d >= (float)ECG_RP_MIN_RR && d <= 1000.0f) {
            rr[n++] = d;
        }
    }
    if (n == 0) {
        return;
    }

    /* 插入排序（n ≤ 5） */
    for (int i = 1; i < n; i++) {
        const float key = rr[i];
        int j = i - 1;
        while (j >= 0 && rr[j] > key) {
            rr[j + 1] = rr[j];
            j--;
        }
        rr[j + 1] = key;
    }

    const float med = (n % 2 == 1) ? rr[n / 2]
                                   : (rr[n / 2 - 1] + rr[n / 2]) * 0.5f;

    rp->last_rr = (int64_t)med;
    rp->bpm     = 60.0f * (float)rp->fs / med;
}

/* ============================================================================
 * 内部：AcceptPeak
 * ========================================================================== */

static void accept_peak(ecg_rpeak_t *rp, int64_t peak, float value, float slope,
                        int64_t *out_index)
{
    rp->signal_peak        = 0.125f * value + 0.875f * rp->signal_peak;
    rp->last_peak          = peak;
    rp->last_accepted_slope = slope;
    rp->peak_total++;
    peak_push(rp, peak);

    update_bpm(rp);

    if (out_index != NULL) {
        *out_index = peak;
    }
}

/* ============================================================================
 * 公开接口
 * ========================================================================== */

void ecg_rpeak_init(ecg_rpeak_t *rp, uint32_t fs)
{
    memset(rp, 0, sizeof(*rp));
    rp->fs        = fs;
    rp->last_peak = NO_PEAK;
}

void ecg_rpeak_reset(ecg_rpeak_t *rp)
{
    const uint32_t fs = rp->fs;
    ecg_rpeak_init(rp, fs);
}

bool ecg_rpeak_process(ecg_rpeak_t *rp, float sample, int64_t *out_index)
{
    bool detected = false;

    /* ---- 1. 主基线（α=0.002，注意不是低通的 0.25）---- */
    rp->baseline += (sample - rp->baseline) * ECG_RP_BASELINE_A;
    const float centered = sample - rp->baseline;

    /* ---- 2. 低通 → 高通 ---- */
    rp->low_pass  += (centered - rp->low_pass) * ECG_RP_LOWPASS_A;
    rp->high_pass += (rp->low_pass - rp->high_pass) * ECG_RP_HIGHPASS_A;
    const float value = rp->low_pass - rp->high_pass;

    /* ---- 3. 四点微分（4 抽头：2v[n]+v[n-1]-v[n-2]-2v[n-3]）/8 ----
     * ⚠ 不是教科书 Pan-Tompkins 的 **5 抽头** `(2,1,0,-1,-2)/8`（那个跨 5 个样本）。
     *   本实现只滞后 3 个样本 ⇒ @500 Hz 峰值 90.9 Hz，教科书版是 68.9 Hz。
     *   对高频噪声更敏感，靠第 4 级的 60 样本积分窗压回来。
     *   🛑 别照着教科书换成 5 抽头 —— 会连带动 filteredHistory 的时序对齐与
     *      LocateRPeak 的偏移分布（详见 docs/Pan-Tompkins算法汇总_20261027.md §3.3）。 */
    const float derivative = (2.0f * value + rp->v_p1 - rp->v_p2 - 2.0f * rp->v_p3) * 0.125f;
    const float squared    = derivative * derivative;

    /* ---- 4. 积分窗（60 样本，先加后减，除以实际个数）---- */
    if (rp->integ_win_n < ECG_RP_INTEG_WIN) {
        rp->integ_win[rp->integ_win_pos] = squared;
        rp->integ_win_pos = (rp->integ_win_pos + 1) % ECG_RP_INTEG_WIN;
        rp->integ_win_n++;
        rp->integ_sum += squared;
    } else {
        /* 满了：减去最旧的、加上新的（同一槽位） */
        rp->integ_sum -= rp->integ_win[rp->integ_win_pos];
        rp->integ_win[rp->integ_win_pos] = squared;
        rp->integ_win_pos = (rp->integ_win_pos + 1) % ECG_RP_INTEG_WIN;
        rp->integ_sum += squared;
    }
    const float integrated = rp->integ_sum / (float)rp->integ_win_n;

    /* ---- 5. filteredHistory（LocateRPeak 用）---- */
    rp->filt_hist[rp->filt_pos].index = rp->sample_index;
    rp->filt_hist[rp->filt_pos].value = value;
    rp->filt_pos = (rp->filt_pos + 1) % ECG_RP_FILT_HIST;
    if (rp->filt_n < ECG_RP_FILT_HIST) {
        rp->filt_n++;
    }

    /* ---- 6. 峰检测（用**上一个**积分值）---- */
    {
        const float prev_integ   = rp->integ_p1;
        const float prevprev_integ = rp->integ_p2;

        const float threshold = fmaxf(TINY,
                                      rp->noise_peak + 0.25f * (rp->signal_peak - rp->noise_peak));

        if (prev_integ >= prevprev_integ && prev_integ > integrated) {
            /* 积分峰对应到滤波信号的峰：回退 1 + 半个积分窗 */
            const int64_t peak  = rp->sample_index - 1 - ECG_RP_INTEG_WIN / 2;
            const float   slope = fabsf(rp->deriv_p1);
            const float   sec_thr = rp->noise_peak + 0.5f * (rp->signal_peak - rp->noise_peak);

            /* 候选池（回溯搜索用） */
            if (prev_integ > fmaxf(TINY, sec_thr)) {
                rp->cand[rp->cand_pos].index = peak;
                rp->cand[rp->cand_pos].value = prev_integ;
                rp->cand[rp->cand_pos].slope = slope;
                rp->cand_pos = (rp->cand_pos + 1) % ECG_RP_CAND_MAX;
                if (rp->cand_n < ECG_RP_CAND_MAX) {
                    rp->cand_n++;
                }
            }

            if (prev_integ > threshold) {
                const int64_t spacing = (rp->last_peak == NO_PEAK)
                                        ? INT64_MAX : (peak - rp->last_peak);
                /* T 波判别：离上一个峰太近且斜率不到它的一半 */
                const bool is_twave = (spacing < 360) && (rp->last_accepted_slope > 0.0f)
                                      && (slope < rp->last_accepted_slope * 0.5f);

                if (!is_twave && spacing >= ECG_RP_REFRACTORY) {
                    accept_peak(rp, locate_rpeak(rp, peak), prev_integ, slope, out_index);
                    detected = true;
                } else {
                    rp->noise_peak = 0.125f * prev_integ + 0.875f * rp->noise_peak;
                }
            } else {
                rp->noise_peak = 0.125f * prev_integ + 0.875f * rp->noise_peak;
            }
        }
    }

    /* ---- 7. 回溯搜索：漏检后回头找 ----
     * 条件：距上一峰已超过 1.66×平均 RR，且候选池非空 */
    if (rp->last_peak != NO_PEAK && rp->peak_n >= 2) {
        /* 平均 RR（最近 5 个，不中位数、要 mean） */
        float sum = 0.0f;
        int   m   = 0;
        const int last = rp->peak_n - 1;
        for (int k = last; k >= 1 && m < ECG_RP_AVG_RR_TAKE; k--) {
            sum += (float)(peak_at(rp, k) - peak_at(rp, k - 1));
            m++;
        }
        const float avg_rr = sum / (float)m;

        if ((float)(rp->sample_index - rp->last_peak) > avg_rr * 1.66f && rp->cand_n > 0) {
            /* 在 (lastPeak+REFRACTORY, sampleIndex-REFRACTORY) 里找 value 最大的候选 */
            int     best_k = -1;
            float   best_v = 0.0f;
            const int cfirst = (rp->cand_pos - rp->cand_n + ECG_RP_CAND_MAX * 2) % ECG_RP_CAND_MAX;
            for (int k = 0; k < rp->cand_n; k++) {
                const ecg_rp_cand_t *c = &rp->cand[(cfirst + k) % ECG_RP_CAND_MAX];
                if (c->index <= rp->last_peak + ECG_RP_REFRACTORY) {
                    continue;
                }
                if (c->index >= rp->sample_index - ECG_RP_REFRACTORY) {
                    continue;
                }
                if (best_k < 0 || c->value > best_v) {
                    best_k = k;
                    best_v = c->value;
                }
            }
            if (best_k >= 0) {
                const ecg_rp_cand_t *c = &rp->cand[(cfirst + best_k) % ECG_RP_CAND_MAX];
                const float need = rp->noise_peak + 0.5f * (rp->signal_peak - rp->noise_peak);
                if (c->value > need) {
                    accept_peak(rp, locate_rpeak(rp, c->index), c->value, c->slope, out_index);
                    detected = true;

                    /* 丢弃 <= 该候选的一切候选 */
                    int drop = 0;
                    for (int k = 0; k < rp->cand_n; k++) {
                        const ecg_rp_cand_t *cc = &rp->cand[(cfirst + k) % ECG_RP_CAND_MAX];
                        if (cc->index <= c->index) {
                            drop++;
                        }
                    }
                    rp->cand_n -= drop;
                }
            }
        }
    }

    /* ---- 8. 推进历史量 ---- */
    rp->deriv_p1 = derivative;
    rp->v_p3     = rp->v_p2;
    rp->v_p2     = rp->v_p1;
    rp->v_p1     = value;
    rp->integ_p2 = rp->integ_p1;
    rp->integ_p1 = integrated;
    rp->sample_index++;

    return detected;
}

float ecg_rpeak_bpm(const ecg_rpeak_t *rp)
{
    return rp->bpm;
}

uint32_t ecg_rpeak_peak_count(const ecg_rpeak_t *rp)
{
    return rp->peak_total;
}

int64_t ecg_rpeak_last_peak(const ecg_rpeak_t *rp)
{
    return rp->last_peak;
}

int64_t ecg_rpeak_sample_index(const ecg_rpeak_t *rp)
{
    return rp->sample_index;
}
