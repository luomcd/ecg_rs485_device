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
 *
 * `out_win`（可为 NULL）：回填**窗口内真正扫到**的样本数。
 *   🛑 满窗是 **72**（`ECG_RP_LOCATE_HALF*2+1 = 81` 是**不存在的基准**）：
 *      调用时 `trigger = sample_index - 1 - INTEG_WIN/2`，窗尾 `trigger+40` 落在**未来**，
 *      最多只能取到当前样本 ⇒ 左半 40 + [trigger, trigger+31] 32 个 = 72。
 *      `out_win < 72` 即发生了截断 —— 这是 T224 形态②（部分覆盖、静默选错极值）
 *      唯一能被观测到的物理量。
 * ========================================================================== */

#define LOCATE_WIN_FULL  (ECG_RP_LOCATE_HALF + 1 + (ECG_RP_INTEG_WIN / 2 + 1))

static int64_t locate_rpeak(const ecg_rpeak_t *rp, int64_t integrated_peak, int *out_win)
{
    if (out_win != NULL) {
        *out_win = 0;
    }
    if (rp->filt_n == 0) {
        return integrated_peak;
    }

    const int64_t start = integrated_peak - ECG_RP_LOCATE_HALF;
    const int64_t end   = integrated_peak + ECG_RP_LOCATE_HALF;

    int64_t best_idx     = integrated_peak;
    float   best_abs     = -1.0f;
    bool    found        = false;
    int     nwin         = 0;

    /* 遍历环形里的全部有效项（≤140），挑 index 落在窗口内且 |value| 最大的 */
    const int first = (rp->filt_pos - rp->filt_n + ECG_RP_FILT_HIST * 2) % ECG_RP_FILT_HIST;
    for (int k = 0; k < rp->filt_n; k++) {
        const ecg_rp_hist_t *h = &rp->filt_hist[(first + k) % ECG_RP_FILT_HIST];
        if (h->index < start || h->index > end) {
            continue;
        }
        nwin++;
        const float a = fabsf(h->value);
        if (!found || a > best_abs) {
            found    = true;
            best_abs = a;
            best_idx = h->index;
        }
    }
    if (out_win != NULL) {
        *out_win = nwin;
    }
    return found ? best_idx : integrated_peak;
}

#if ECG_RP_DIAG
static void set_diag(ecg_rpeak_t *rp, int64_t trigger, int64_t fid, int64_t lag,
                     int win, int src)
{
    rp->last_diag.trigger   = trigger;
    rp->last_diag.fid       = fid;
    rp->last_diag.lag       = lag;
    rp->last_diag.win       = (int16_t)((win > 32767) ? 32767 : win);
    rp->last_diag.src       = (int8_t)src;
    rp->last_diag.win_short = (int8_t)((win >= 0 && win < LOCATE_WIN_FULL) ? 1 : 0);
}
#endif

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

/* ============================================================================
 * ★T229-B：统一的阈值计算（含绝对下限）
 *
 * 🛑 三处入口**必须**共用本函数：
 *    ①主路径接受（ratio=0.25）②候选入池（0.5）③回溯接受（0.5）。
 *    只给①加下限的话，弱噪声峰仍会经②进池、再由③捞回打点（T230 §2.1/§7.1）。
 *
 * 量级关系（稳态 signal_peak≈10⁵、noise≈10、thr_floor≈1,720）：
 *    0.25 比例 ≈ 25,000、0.5 比例 ≈ 50,000 —— 都远高于 thr_floor，
 *    所以**正常心电时 thr_floor 不生效**；它只在 signal_peak 尚未建立
 *    （无信号 / 刚清零）时兜底。
 * ========================================================================== */
static float effective_threshold(const ecg_rpeak_t *rp, float ratio)
{
    const float adaptive = rp->noise_peak + ratio * (rp->signal_peak - rp->noise_peak);
    const float t        = (adaptive > rp->thr_floor) ? adaptive : rp->thr_floor;
    return (t > TINY) ? t : TINY;
}

void ecg_rpeak_init(ecg_rpeak_t *rp, uint32_t fs)
{
    memset(rp, 0, sizeof(*rp));
    rp->fs        = fs;
    rp->last_peak = NO_PEAK;

    /* ★T229-B：把"等效 µV 幅度"换算成积分域下限（只在这里算一次，别每样本换算）。
     * integ ≈ k_int × A²(LSB)，A(LSB) = µV / UV_PER_LSB ⇒ 平方后乘 k_int。 */
    {
        const float a_lsb = ECG_RP_THR_FLOOR_UV / ECG_RP_UV_PER_LSB;
        rp->thr_floor = ECG_RP_K_INT * a_lsb * a_lsb;
    }
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

        const float threshold = effective_threshold(rp, 0.25f);

        if (prev_integ >= prevprev_integ && prev_integ > integrated) {
            /* 积分峰对应到滤波信号的峰：回退 1 + 半个积分窗 */
            const int64_t peak  = rp->sample_index - 1 - ECG_RP_INTEG_WIN / 2;
            const float   slope = fabsf(rp->deriv_p1);
            const float   sec_thr = effective_threshold(rp, 0.5f);

            /* ★T225 §4.2：落点只在**候选入池那一刻**算一次，主路径直接复用。
             *   🛑 不能写“主路径另调一次 locate_rpeak”——那就是两套解析，
             *      而且入池时的窗口覆盖与主路径接受时**完全相同**，重算纯属浪费。 */
            int64_t fid_now = 0;
            int     win_now = 0;
            bool    fid_ready = false;

            /* 候选池（回溯搜索用） */
            if (prev_integ > sec_thr) {
#if ECG_RP_USE_STORED_FID
                fid_now   = locate_rpeak(rp, peak, &win_now);
                fid_ready = true;
#endif
                rp->cand[rp->cand_pos].index = peak;
                rp->cand[rp->cand_pos].value = prev_integ;
                rp->cand[rp->cand_pos].slope = slope;
#if ECG_RP_USE_STORED_FID
                rp->cand_fid_delta[rp->cand_pos] = (int16_t)(fid_now - peak);
#if ECG_RP_DIAG
                rp->cand_win[rp->cand_pos]       = (uint8_t)((win_now > 255) ? 255 : win_now);
#endif
#endif
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
                    int64_t fid;
                    if (fid_ready) {
                        fid = fid_now;                       /* 复用入池时的结果 */
                    } else {
                        fid = locate_rpeak(rp, peak, &win_now);
                    }
#if ECG_RP_DIAG
                    set_diag(rp, peak, fid, 0, win_now, 0);
#endif
                    accept_peak(rp, fid, prev_integ, slope, out_index);
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
                const int cslot = (cfirst + best_k) % ECG_RP_CAND_MAX;
                const ecg_rp_cand_t *c = &rp->cand[cslot];
                const float need = effective_threshold(rp, 0.5f);
                if (c->value > need) {
                    int64_t fid;
                    int     win = 0;
#if ECG_RP_USE_STORED_FID
                    /* ★T225 §5.1：直接取入池时算好的落点。
                     * 🛑 范围保护不可省：若将来改了 LOCATE_HALF 而 int16 溢出、
                     *    或环形池被覆写过，宁可退回 trigger 也不能跳出去读陈旧槽位。 */
                    fid = c->index + (int64_t)rp->cand_fid_delta[cslot];
                    if (fid < c->index - ECG_RP_LOCATE_HALF ||
                        fid > c->index + ECG_RP_LOCATE_HALF) {
                        fid = c->index;
                    }
#if ECG_RP_DIAG
                    win = (int)rp->cand_win[cslot];
#endif
#else
                    fid = locate_rpeak(rp, c->index, &win);
#endif
                    const int64_t lag = rp->sample_index - c->index;
#if ECG_RP_DIAG
                    set_diag(rp, c->index, fid, lag, win, 1);
#endif
                    accept_peak(rp, fid, c->value, c->slope, out_index);
                    detected = true;

                    /* 丢弃 <= 该候选的一切候选（🛑 仍按 trigger 判，P3A 不混坐标） */
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

#if ECG_RP_DIAG
const ecg_rp_diag_t *ecg_rpeak_last_diag(const ecg_rpeak_t *rp)
{
    return &rp->last_diag;
}
#endif
