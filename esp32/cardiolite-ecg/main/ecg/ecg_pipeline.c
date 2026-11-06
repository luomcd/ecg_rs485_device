/**
 * @file ecg_pipeline.c
 * @brief 数据管线实现（S3）
 */

#include "ecg_pipeline.h"

#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "app_config.h"
#include "app_state.h"      /* ★T209：把每帧 STA 上抛给 UI/CLI（此前零调用 ⇒ 假值） */
#include "ecg_frame.h"
#include "ecg_rpeak.h"
#include "ecg_stream.h"
#include "rs485_port.h"

static const char *TAG = "ecg_pipe";

/* ============================================================================
 * S9a：数据源类型（FILE = SD 回放；RS485 = 真机实时流）
 * ========================================================================== */
typedef enum {
    ECG_SRC_NONE = 0,
    ECG_SRC_FILE,
    ECG_SRC_RS485,
} ecg_src_t;

static volatile ecg_src_t s_src = ECG_SRC_NONE;
static ecg_stream_t      s_stream;      /* 485 字节流状态机（生产者任务私有） */

/* ★T208（11-05）：快照帧计数。生产者写、CLI 读，32 位对齐读写在 Xtensa 上是原子的，
 * 与既有 s_frames 同口径，不再加锁。 */
static volatile uint32_t s_snapshots = 0u;

/* ---- 40 KB 环缓放静态区（内部 RAM）。
 * 不放 PSRAM：PSRAM 有 cache miss 抖动，生产者按 2 ms 硬节拍写，
 * 内部 RAM 的确定性更好。当前内部 RAM 余 ~289 KB，占 40 KB 可接受。 */
static ecg_ringbuf_t s_ring;

static TaskHandle_t     s_prod_task = NULL;
static TaskHandle_t     s_cons_task = NULL;
static volatile bool    s_running   = false;
static volatile bool    s_consumer_enabled = true;
static volatile bool    s_paused    = false;
static volatile bool    s_eof       = false;
static volatile bool    s_restart   = false;
static volatile bool    s_change    = false;
static char             s_path_next[80];
static volatile uint32_t s_popped   = 0;
static volatile uint32_t s_frames   = 0;

static const char *s_path = NULL;
static bool        s_loop = false;

/* 生产者栈内大缓冲改静态，避免吃任务栈 */
static uint8_t s_raw[ECG_HDR_LEN + ECG_LEN_SANITY_MAX + 1];

/* ============================================================================
 * S7：R 峰检测（生产者任务私有，Core 1）
 *
 * 约 4 KB 静态区（filt_hist 140 + cand 64 + peaks 32），不计入 heap free。
 * 计算量约 25 kFLOP/s ⇒ <0.1% CPU（需求 §5.2 的推算已由 S7 实测印证）。
 * ========================================================================== */

static ecg_rpeak_t        s_rp;
static int64_t            s_rp_last_peak_us = 0;   /* esp_timer，仅生产者写 */
static uint32_t           s_rp_pub_tick     = 0;   /* 发布节拍（每 100 样本） */

/* 跨核发布：BPM + 峰数 + 时刻 走 seqlock（需求 §6.2「单变量 + 版本号」） */
static volatile uint32_t  s_hr_seq          = 0;
static volatile float     s_hr_bpm          = 0.0f;
static volatile uint32_t  s_hr_peaks        = 0;
static volatile int64_t   s_hr_last_peak_us = 0;
static volatile uint32_t  s_hr_fed          = 0;   /* 本 epoch 已喂样本数 */
static volatile uint32_t  s_rp_epoch        = 1;

/* 跨核发布：R 峰索引走定长 SPSC 环 */
static uint32_t           s_rp_q[APP_RPEAK_QUEUE_DEPTH];
static volatile uint32_t  s_rp_q_wr = 0;
static volatile uint32_t  s_rp_q_rd = 0;

/* 重置请求（UI → 生产者） */
static volatile bool      s_rp_reset_req = false;

/* ============================================================================
 * S3：回放对账输出（峰序列 `P <文件内相对号>`）+ 倍速
 * ========================================================================== */

static volatile bool      s_rp_dump = false;   /* 是否输出峰序列（CLI mit-run 开） */
static volatile int       s_speed   = 1;       /* 回放倍速 1/2/4/8 */

/* 文件内相对号基准 = 当前文件第一个样本的**全局序号**（`s_push_seq` 空间）。
 * 换文件 / 重播时刷新为当时的 `s_push_seq`。
 * 🛑 不用 `ecg_rpeak_sample_index()`：UI 清屏会 reset 检测器把它归零，但文件继续播，
 *    归零之后再拿它当文件内号就整体偏移（对账会全错）。 */
static volatile uint32_t  s_file_base_seq = 0;

/* 当前文件的统计（FILE 收尾行用；peaks 不能用 ecg_rpeak_peak_count —— 清屏会归零） */
static uint32_t           s_file_frames   = 0;
static uint32_t           s_file_peaks    = 0;
static uint32_t           s_file_drop0    = 0;   /* 文件起点时的 dropped，用于算差值 */

/* P 行攒行：满 16 个峰一次性 printf 整行（与批处理一致的列数），
 * 减少与 REPL prompt 的字符级交错。 */
static char               s_dump_line[176];
static int                s_dump_len = 0;
static int                s_dump_cnt = 0;

/* 文件名（不含路径），FILE 行用 */
static char               s_dump_name[64] = "";

/* ---- S3+：单条回放结果落盘（`mit-run <rec>` 生成 mit_<rec>_result.txt）----
 *
 * 🛑 **绝不能在生产者任务里直接 fwrite/fprintf 到 SD 卡**：生产者跑 2 ms 硬节拍
 *   （Core 1 / prio 5），而 SD 卡一次写入要几十 ms ⇒ 节拍崩、环缓空、波形卡顿。
 * 做法：峰序列先攒进这块静态缓冲，**EOF 时一次性** fwrite（那时已不再喂样本，抖动无影响）。
 *
 * 为什么不像 S2 那样逐行 file_out：批处理是全速离线跑（无节拍约束），这里不是。
 * 容量：单条 MIT 最多约 3000 峰 × 6 B ≈ 18 KB，32 KB 有充分余量；满了只置溢出标志。 */
#define DUMP_BUF_SIZE   (32 * 1024)
static char   s_dump_buf[DUMP_BUF_SIZE];
static size_t s_dump_fill     = 0;
static bool   s_dump_overflow = false;
static char   s_dump_path[80] = "";

/* 串口进度节拍：每 5 万样本打一行 PROGRESS。
 * 30 min（902,800 样本）约 18 行 —— 行数与倍速无关，1x 和 8x 都是 18 行。 */
#define PROG_STEP_SAMPLES  50000u
static uint32_t s_prog_next = PROG_STEP_SAMPLES;

/* **永不重置**的全局推入序号：每次成功 push 样本 +1，跨 epoch / 跨换文件一直累加。
 * 用途只有一件事 —— 给 R 峰打一个"绝对样本号"，让 UI 能用自己**同样永不重置**的
 * 取样本计数去减。⚠ 这与早先"epoch 内相对索引"有本质区别：那套每换一次文件就归零，
 * 归零时刻两边不同步 ⇒ 永久错位；本序号从不归零，两边在整段运行期始终同一空间。 */
static volatile uint32_t  s_push_seq = 0;

/** 重置检测器并把代数 +1（**只在生产者任务内调用**） */
static void rp_restart(void)
{
    ecg_rpeak_reset(&s_rp);
    s_rp_last_peak_us = 0;
    s_rp_epoch++;
}

/** 把一次检出的峰推进发布环（**只在生产者任务内调用**）
 *
 * @param abs_idx 该峰的**全局绝对样本号**（`s_push_seq` 空间，永不重置）。
 *
 * 为什么必须用「全局永不重置的序号」而不是两种更"聪明"的做法：
 *   ① epoch 内相对索引 —— 每次换文件/重播/清屏归零，而 UI 要到下一帧才发现，
 *      归零时刻不同步 ⇒ 永久错位一帧（约 25 样本），点画在下降支、最新一两拍被丢；
 *   ② 发布"相对年龄"让 UI 自己累加 —— 峰是在一帧的取数批次**中途**检出的，
 *      "检出之后到本帧取数结束"之间取走的样本数每拍不同（0~37），
 *      累加时补不进这一段 ⇒ 每点有 ±25 样本（±7.5 px）的**随机**误差。
 *   全局序号没有上述任何时间差：生产者与 UI 数的是同一条样本流，从不归零。 */
static void rp_q_push(uint32_t abs_idx)
{
    const uint32_t wr = s_rp_q_wr;
    if (wr - s_rp_q_rd >= APP_RPEAK_QUEUE_DEPTH) {
        return;     /* 满：丢新（与环缓同策略），深度 64 在 60 bpm 下够 64 s */
    }
    s_rp_q[wr % APP_RPEAK_QUEUE_DEPTH] = abs_idx;
    s_rp_q_wr = wr + 1;
}

/* ---- S3：峰序列攒行输出（生产者任务私有，无并发） ---- */

static void dump_line_reset(void)
{
    s_dump_len = 0;
    s_dump_cnt = 0;
    s_dump_line[0] = '\0';
}

/** 往落盘缓冲追加（满了只置溢出标志，绝不越界；未设文件路径时直接跳过） */
static void dump_buf_append(const char *fmt, ...)
{
    if (s_dump_path[0] == '\0') {
        return;
    }
    if (s_dump_fill + 1 >= DUMP_BUF_SIZE) {
        s_dump_overflow = true;
        return;
    }
    va_list ap;
    va_start(ap, fmt);
    const int n = vsnprintf(s_dump_buf + s_dump_fill,
                            DUMP_BUF_SIZE - s_dump_fill, fmt, ap);
    va_end(ap);
    if (n > 0) {
        s_dump_fill += (size_t)n;
        if (s_dump_fill >= DUMP_BUF_SIZE) {
            s_dump_fill    = DUMP_BUF_SIZE - 1;
            s_dump_overflow = true;
        }
    }
}

/** 把缓冲一次性落盘（只在 EOF 调；此时已不再喂样本，一次几十 ms 抖动无影响） */
static void dump_buf_flush_to_file(void)
{
    if (s_dump_path[0] == '\0') {
        return;
    }
    FILE *f = fopen(s_dump_path, "w");
    if (f == NULL) {
        ESP_LOGW(TAG, "cannot open replay result file %s -- 本次结果只在串口", s_dump_path);
        return;
    }
    const size_t w = fwrite(s_dump_buf, 1, s_dump_fill, f);
    fclose(f);
    ESP_LOGI(TAG, "replay result -> %s (%u B%s)", s_dump_path, (unsigned)w,
             s_dump_overflow ? ", TRUNCATED!" : "");
}

/** 把攒够的一行**只写落盘缓冲**（串口不再刷 P 峰，只给进度 —— 猛哥 13:51 要求）。
 *
 * 为什么串口不打 P 峰：
 *   ① 一条 MIT 约 2600 峰 / 163 行，刷屏后 REPL 提示符被淹没，人看不清进度；
 *   ② prompt 会粘在首行 P 前面（`cardiolite> P 3 637 ...`），解析要额外容错；
 *   ③ 数据量虽小但"串口 = 数据通道"这个假设本身不可靠（会丢、会粘）。
 * 于是：**串口 = 进度展示**，**SD 卡文件 = 权威数据**，与 S2 批处理同一套路。 */
static void dump_line_flush(void)
{
    if (s_dump_cnt == 0) {
        return;
    }
    dump_buf_append("%s\n", s_dump_line);
    dump_line_reset();
}

/** 追加一个峰的文件内相对号；满 16 个自动成行 */
static void dump_peak(uint32_t rel)
{
    const size_t cap = sizeof(s_dump_line);
    int n;
    if (s_dump_cnt == 0) {
        n = snprintf(s_dump_line, cap, "P %u", (unsigned)rel);
    } else {
        n = snprintf(s_dump_line + s_dump_len, cap - (size_t)s_dump_len,
                     " %u", (unsigned)rel);
    }
    if (n > 0) {
        s_dump_len += n;
        if (s_dump_len >= (int)cap) {
            s_dump_len = (int)cap - 1;      /* 防御：不让 len 越界 */
        }
    }
    if (++s_dump_cnt >= 16) {
        dump_line_flush();
    }
}

/** 取路径里的文件名部分（FILE 行用，与批处理的 base_name 同口径） */
static void dump_set_name(const char *path)
{
    const char *p = (path != NULL) ? strrchr(path, '/') : NULL;
    const char *b = (p != NULL) ? p + 1 : path;
    if (b == NULL) {
        b = "";
    }
    /* 用 `%.*s` 显式限定长度：-Werror=format-truncation 不允许
     * 「源可能比目标长」的裸 `%s`（路径缓冲 80 B，本缓冲 64 B）。 */
    snprintf(s_dump_name, sizeof(s_dump_name), "%.*s",
             (int)sizeof(s_dump_name) - 1, b);
}

/** 文件起点：刷新相对号基准与统计（start / restart / change 三处调用） */
static void file_epoch_begin(void)
{
    uint32_t dropped = 0;
    ecg_ring_stats(&s_ring, NULL, &dropped, NULL);

    s_file_base_seq = s_push_seq;
    s_file_frames   = 0;
    s_file_peaks    = 0;
    s_file_drop0    = dropped;
    dump_line_reset();
    s_dump_fill     = 0;        /* 换了文件 ⇒ 上一段的结果作废 */
    s_dump_overflow = false;
    s_prog_next     = PROG_STEP_SAMPLES;

    /* 结果文件自带头部，保证脱离串口也能独立分析（不落盘时 append 是空操作） */
    dump_buf_append("BATCH-BEGIN total=1\n");
    dump_buf_append("# CardioLite S3 replay: fs=%d Hz, file=%s, speed=%dx, "
                    "峰号=文件内相对样本号\n",
                    APP_SAMPLE_RATE_HZ, s_dump_name, s_speed);
}

/** 发布心率快照（seqlock 写侧，**只在生产者任务内调用**） */
static void hr_publish(void)
{
    __sync_synchronize();
    s_hr_seq++;                                 /* 奇数 = 写入中 */
    __sync_synchronize();
    s_hr_bpm          = ecg_rpeak_bpm(&s_rp);
    s_hr_peaks        = ecg_rpeak_peak_count(&s_rp);
    s_hr_last_peak_us = s_rp_last_peak_us;
    s_hr_fed          = (uint32_t)ecg_rpeak_sample_index(&s_rp);
    __sync_synchronize();
    s_hr_seq++;                                 /* 偶数 = 稳定 */
    __sync_synchronize();
}


/* ============================================================================
 * 推送一个样本（S9a 抽出：FILE 源与 485 源共用同一段逻辑）
 *
 * 这是"485 显示与 SD 回放等价"的关键：环缓写入、R 峰全局绝对号换算、BPM 发布、
 * 回放对账输出全部在这一处，两个数据源没有任何分叉。
 * ========================================================================== */

static void push_sample(int32_t v1, int32_t v2)
{
    /* S7：**只有在样本真正进入管线之后才喂检测器**。
     * 环缓满会丢样本（丢新），此时这里也跳过 —— 于是「检测器的 sample_index」
     * 与「UI 取到的样本序号」两边同步跳过同一个样本，索引始终等价，
     * 不会因为丢了几个样本就让 R 峰标记越画越偏。 */
    if (ecg_ring_push(&s_ring, v1, v2)) {
        const uint32_t newest_global = s_push_seq;
        s_push_seq = newest_global + 1u;

        int64_t pk = 0;
        if (ecg_rpeak_process(&s_rp, (float)v1, &pk) && pk >= 0) {
            s_rp_last_peak_us = esp_timer_get_time();
            /* sample_index 在处理末尾 ++，所以刚处理的样本的 epoch 内索引 = fed-1。
             * 它与该峰的 epoch 内索引之差，就是"峰落后当前样本多少"；
             * 用全局序号减掉它，得到该峰的**全局绝对样本号**。 */
            const uint32_t fed  = (uint32_t)ecg_rpeak_sample_index(&s_rp);
            const uint32_t back = (fed - 1u) - (uint32_t)pk;
            const uint32_t abs_idx = (back <= newest_global)
                                     ? (newest_global - back)
                                     : newest_global;
            rp_q_push(abs_idx);

            /* ---- S3：回放对账输出（文件内相对号）----
             * rel = 全局绝对号 − 文件起点全局号。理论上 abs_idx ≥ base（峰必在
             * 文件起点之后）；防下溢仍做一次钳位。 */
            s_file_peaks++;
            if (s_rp_dump) {
                const uint32_t base = s_file_base_seq;
                dump_peak((abs_idx >= base) ? (abs_idx - base) : 0u);
            }
        }
        /* 每 100 样本（200 ms）发布一次心率快照：UI 侧 200 ms 刷一次 BPM，
         * 10 Hz 的发布频率足够，不必每样本都进 seqlock。 */
        if (++s_rp_pub_tick >= 100) {
            s_rp_pub_tick = 0;
            hr_publish();
        }

        /* ---- 串口进度（只在回放对账模式下打）----
         * 一行几十字节、每 5 万样本一次（1x 下 100 s 一次），不会阻塞 2 ms 节拍。 */
        if (s_rp_dump) {
            const uint32_t rel = s_push_seq - s_file_base_seq;
            if (rel >= s_prog_next) {
                s_prog_next += PROG_STEP_SAMPLES;
                printf("PROGRESS %s samples=%u peaks=%u\n",
                       s_dump_name, (unsigned)rel, (unsigned)s_file_peaks);
            }
        }
    }
}

/* ============================================================================
 * S9a：485 生产者（Core 1）—— 收到整帧即推，无自打节拍
 *
 * 为什么不做 2 ms 节拍：PC/STM32 按实时速率发帧（25 样本/50 ms），数据自带时序。
 * 再套一层节拍器只会把数据压进缓冲、增加延迟；且 UART 驱动 4 KB 缓冲已能吸收
 * 任务唤醒抖动（≈68 帧 ≈1.7 s 实时数据）。
 *
 * ⚠ 不支持"暂停"：暂停会让 UART 缓冲溢出丢数据。UI 的「冻结」是叠加标志
 *   （只停绘制不停管线），与本函数无关。
 * ⚠ 永不置 s_eof：与 FILE 源不同，实时流没有"播完"。
 * ========================================================================== */

static void rs485_producer_loop(void)
{
    ESP_LOGI(TAG, "rs485 producer started on core %d (baud=%lu)",
             (int)xPortGetCoreID(), (unsigned long)rs485_baud());

    ecg_frame_t fr;
    memset(&fr, 0, sizeof fr);

    uint8_t          buf[256];
    const TickType_t t0          = xTaskGetTickCount();
    uint32_t         next_log_ms = 5000;

    while (s_running) {
        /* ---- S7 重置请求（清屏）：UI 置标志，这里在帧边界上执行 ---- */
        if (s_rp_reset_req) {
            s_rp_reset_req = false;
            rp_restart();
            ESP_LOGI(TAG, "rpeak detector reset on request (epoch=%u)",
                     (unsigned)s_rp_epoch);
        }

        const int n = rs485_read(buf, sizeof buf, APP_485_READ_TIMEOUT_MS);
        if (n > 0) {
            for (int i = 0; i < n; i++) {
                if (!ecg_stream_feed(&s_stream, buf[i], &fr)) {
                    continue;                       /* 还没凑满一帧 */
                }
                if (!fr.crc_ok) {
                    continue;                       /* CRC 错帧丢弃（已计入 crc_errors） */
                }
                /* ★T208（11-05）：按 TYPE 分流。
                 * 🛑 此前没有这道过滤 ⇒ 80 B 快照帧（TYPE=0x03, LEN=74）被当显示帧
                 *   解成 36 个"样本"推进环缓，屏上多出 36 个垃圾点，
                 *   且 `frames` 被虚增（T206 §9.1b）。
                 *   现在：快照帧只计数，进波形的必须且只能是 TYPE=0x01。 */
                if (fr.type == ECG_TYPE_SNAPSHOT) {
                    s_snapshots++;
                    continue;
                }
                if (fr.type != ECG_TYPE_DISPLAY) {
                    continue;                       /* 未知类型：不污染波形、不计数 */
                }
                s_frames++;
                /* ★T209（11-05）：喂 STA 给 UI/CLI。
                 * 🛑 此前 `app_state_set_sta()` 全工程**零调用者** ⇒
                 *   `ui_button_bar.c:458` 的 `st->has_sta` 恒 false，「诊断/陷波」
                 *   两个按钮上显示的是硬编码兜底值 0x02，**不是真值**；
                 *   `485-send mode/notch` 也因此没有任何可验收的迹象。
                 * 写侧＝生产者任务（Core 1）、读侧＝UI/CLI（Core 0），
                 *   单字节 8 位存储天然原子，与相邻 `s_frames` 同口径不加锁。 */
                app_state_set_sta(fr.sta, fr.has_sta);
                for (uint16_t k = 0; k < fr.n_ch1; k++) {
                    const int32_t v2 = (k < fr.n_ch2) ? fr.ch2[k] : 0;
                    push_sample(fr.ch1[k], v2);
                }
            }
        }

        /* 每 5 s 一次运行统计（与 FILE 源同格式，便于横向比较） */
        const uint32_t elapsed_ms =
            (uint32_t)((xTaskGetTickCount() - t0) * portTICK_PERIOD_MS);
        if (elapsed_ms >= next_log_ms) {
            next_log_ms += 5000;

            uint32_t pushed = 0, dropped = 0;
            ecg_ring_stats(&s_ring, &pushed, &dropped, NULL);
            rs485_stat_t rs;
            rs485_get_stat(&rs);
            ESP_LOGI(TAG,
                     "t=%" PRIu32 " s | 485 rx=%" PRIu32 " B frames=%" PRIu32
                     " crc=%" PRIu32 " resync=%" PRIu32 " gaps=%" PRIu32
                     " | pushed=%" PRIu32 " dropped=%" PRIu32 " bpm=%.1f",
                     elapsed_ms / 1000u, rs.rx_bytes, s_stream.frames,
                     s_stream.crc_errors, s_stream.resync_bytes, s_stream.seq_gaps,
                     pushed, dropped, (double)ecg_rpeak_bpm(&s_rp));
        }
    }

    ESP_LOGI(TAG, "rs485 producer exited (frames=%" PRIu32 " crc_err=%" PRIu32 ")",
             s_stream.frames, s_stream.crc_errors);
}


/* ============================================================================
 * 生产者（Core 1）—— FILE 源；485 源在进入前分支走 rs485_producer_loop()
 * ========================================================================== */

static void producer_task(void *arg)
{
    (void)arg;

    if (s_src == ECG_SRC_RS485) {
        rs485_producer_loop();
        s_running   = false;
        s_src       = ECG_SRC_NONE;
        s_prod_task = NULL;
        vTaskDelete(NULL);
        return;
    }

    FILE *f = fopen(s_path, "rb");
    if (f == NULL) {
        ESP_LOGE(TAG, "open %s failed", s_path);
        s_running = false;
        s_prod_task = NULL;
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "producer started on core %d, file=%s, loop=%d, speed=%dx",
             (int)xPortGetCoreID(), s_path, (int)s_loop, s_speed);

    ecg_frame_t fr;
    memset(&fr, 0, sizeof(fr));
    uint16_t idx        = 0;
    bool     have_frame = false;

    /* ---- S3：回放节拍 ----
     * 名义节拍 = 1000/fs ms（500 Hz ⇒ 2 ms = 2 tick）。倍速靠「微秒累计器」实现：
     * 每喂一个样本累加 `us_per_sample` µs，累计够 1000 µs 才 delay 一次。
     * 1x 时每次累加 2000 µs ⇒ 每次 delay 2 tick，与改动前**逐位等价**。
     * 🛑 不能直接 `pdMS_TO_TICKS(2000/speed)`：tick=1000 Hz 下 4x（0.5 ms）
     *    会算出 0 tick ⇒ 空转不节流。累计器把不足 1 tick 的部分攒到下一轮。 */
    const uint32_t base_tick_ms = 1000u / APP_SAMPLE_RATE_HZ;   /* 500 Hz ⇒ 2 ms */
    uint32_t       us_acc       = 0;
    TickType_t     last         = xTaskGetTickCount();
    const TickType_t t0         = last;
    uint32_t       next_log_ms  = 5000;

    dump_set_name(s_path);
    file_epoch_begin();

    while (s_running) {
        /* ---- S7 重置请求（清屏）：UI 置标志，这里在样本边界上执行 ---- */
        if (s_rp_reset_req) {
            s_rp_reset_req = false;
            rp_restart();
            ESP_LOGI(TAG, "rpeak detector reset on request (epoch=%u)",
                     (unsigned)s_rp_epoch);
        }

        /* ---- 重播请求（★T211：**必须排在暂停判断之前**）----
         * 两者语义正交：restart = 回放位置归零，paused = 要不要继续推进。
         * 🛑 放在 `s_paused` 之后会有一个**静默失效**：
         *   清屏的语义是「位置归零 + 保持暂停」⇒ 若暂停分支先 `continue`，
         *   `s_restart` 永远不被消费 ⇒ "归零"没执行，下次恢复播放仍从**中断处**
         *   继续，而屏上已经清成空白 ⇒ 状态对不上。
         * ⚠ 正常播放（`s_paused == false`）时两种顺序完全等价，MIT 回放行为不变。 */
        if (s_restart) {
            s_restart  = false;
            s_eof      = false;
            have_frame = false;
            idx        = 0;
            fseek(f, 0, SEEK_SET);
            last = xTaskGetTickCount();
            us_acc = 0;
            rp_restart();               /* 从头播 ⇒ R 峰/BPM 也从零开始 */
            file_epoch_begin();         /* S3：相对号基准跟着刷新 */
            ESP_LOGI(TAG, "restart from beginning (rpeak epoch=%u)",
                     (unsigned)s_rp_epoch);
            continue;
        }

        /* ---- 暂停：保持任务存活，重置节拍基准避免恢复时"追赶" ---- */
        if (s_paused) {
            vTaskDelay(pdMS_TO_TICKS(10));
            last = xTaskGetTickCount();
            continue;
        }

        /* ---- 换文件请求（切单/双通道）----
         * 先开新的、成功后再关旧的 —— 打不开就保持原文件，不把管线搞死。 */
        if (s_change) {
            s_change   = false;
            FILE *nf   = fopen(s_path_next, "rb");
            if (nf == NULL) {
                ESP_LOGE(TAG, "switch: open %s failed, keep current file", s_path_next);
                continue;
            }
            if (f != NULL) {
                fclose(f);
            }
            f          = nf;
            have_frame = false;
            idx        = 0;
            s_eof      = false;
            last       = xTaskGetTickCount();
            us_acc     = 0;
            rp_restart();               /* 换了数据 ⇒ 旧峰与新波形无关，必须重置 */
            dump_set_name(s_path_next);
            file_epoch_begin();         /* S3：换文件 ⇒ 文件内相对号从 0 重新计 */
            ESP_LOGI(TAG, "switched data file -> %s (rpeak epoch=%u)",
                     s_path_next, (unsigned)s_rp_epoch);
            continue;
        }

        /* ---- 需要新帧？ ---- */
        if (!have_frame || idx >= fr.n_ch1) {
            const esp_err_t ret = ecg_frame_read(f, &fr, s_raw, sizeof(s_raw));
            if (ret == ESP_ERR_INVALID_RESPONSE) {
                continue;                        /* 假帧头，已内部重同步 */
            }
            if (ret != ESP_OK) {
                if (s_loop) {
                    fseek(f, 0, SEEK_SET);
                    have_frame = false;
                    continue;
                }
                /* 非 loop：置 EOF 但**不退出任务** —— 停在循环里等 restart，
                 * 这样重播不必销毁重建任务（省掉反复分配 4 KB 栈）。 */
                if (!s_eof) {
                    s_eof = true;

                    /* ---- S3：回放对账收尾 ----
                     * `FILE` 行是 ecg_batch_eval.py 的**段提交点**（它靠这行落定
                     * 前面攒的 P 序列），格式与批处理一致。dropped 不为 0 表示
                     * 环缓丢过样本 ⇒ 喂给检测器的序列少了 ⇒ 不能与批处理逐位比对。 */
                    if (s_rp_dump) {
                        dump_line_flush();
                        uint32_t dropped = 0;
                        ecg_ring_stats(&s_ring, NULL, &dropped, NULL);
                        const uint32_t samples = s_push_seq - s_file_base_seq;
                        const uint32_t bpm10 =
                            (uint32_t)(ecg_rpeak_bpm(&s_rp) * 10.0f + 0.5f);

                        /* FILE 行 + dropped 行：串口和文件都写（内容一致） */
                        printf("FILE %s frames=%u samples=%u peaks=%u bpm=%u.%u\n",
                               s_dump_name, (unsigned)s_file_frames, (unsigned)samples,
                               (unsigned)s_file_peaks,
                               (unsigned)(bpm10 / 10u), (unsigned)(bpm10 % 10u));
                        dump_buf_append("FILE %s frames=%u samples=%u peaks=%u bpm=%u.%u\n",
                                        s_dump_name, (unsigned)s_file_frames,
                                        (unsigned)samples, (unsigned)s_file_peaks,
                                        (unsigned)(bpm10 / 10u), (unsigned)(bpm10 % 10u));

                        printf("# dropped=%u speed=%d (逐位对账要求 dropped=0)\n",
                               (unsigned)(dropped - s_file_drop0), s_speed);
                        dump_buf_append("# dropped=%u speed=%d\n",
                                        (unsigned)(dropped - s_file_drop0), s_speed);

                        /* 一次性落盘（此时已 EOF，不再喂样本 ⇒ 允许一次几十 ms 抖动） */
                        dump_buf_flush_to_file();
                        if (s_dump_path[0] != '\0') {
                            printf("# 完整峰序列已写入 %s（%u B）\n",
                                   s_dump_path, (unsigned)s_dump_fill);
                        }

                        s_rp_dump = false;
                    }
                    ESP_LOGI(TAG, "EOF reached, waiting for restart");
                }
                vTaskDelay(pdMS_TO_TICKS(20));
                continue;
            }
            idx        = 0;
            have_frame = true;
            s_frames++;
            s_file_frames++;
        }

        /* ---- 推一个样本（严格 2 ms 节拍） ----
         * 推入后的全部动作（环缓 / 检测器 / R 峰绝对号 / BPM / 对账输出）
         * 都在 push_sample() 里，485 源共用同一段（S9a）。 */
        const int32_t v1 = fr.ch1[idx];
        const int32_t v2 = (idx < fr.n_ch2) ? fr.ch2[idx] : 0;
        push_sample(v1, v2);
        idx++;

        /* ---- S3：倍速节拍（微秒累计器，见 producer 开头的说明）----
         * 每个样本消耗 base_tick_ms*1000/speed µs；累够 1 ms 才 delay 一次。 */
        {
            int sp = s_speed;
            if (sp < 1) {
                sp = 1;
            }
            us_acc += (base_tick_ms * 1000u) / (uint32_t)sp;
            if (us_acc >= 1000u) {
                const TickType_t d = (TickType_t)(us_acc / 1000u);
                us_acc %= 1000u;
                vTaskDelayUntil(&last, d);
            }
        }

        /* ---- 每 5 s 打一次运行统计 ---- */
        const uint32_t elapsed_ms = (uint32_t)((xTaskGetTickCount() - t0) * portTICK_PERIOD_MS);
        if (elapsed_ms >= next_log_ms) {
            next_log_ms += 5000;

            uint32_t pushed = 0, dropped = 0, ow = 0;
            ecg_ring_stats(&s_ring, &pushed, &dropped, &ow);
            const uint32_t bpm10 = (uint32_t)(ecg_rpeak_bpm(&s_rp) * 10.0f + 0.5f);

            /* ⚠ 不打 popped：S4 调 ecg_pipeline_detach_consumer() 之后 drain 任务已退出，
             * s_popped 不再增长（实际消费者变成了 ui_waveform 的 pull_samples）。
             * 这个字段留在日志里会误导成"只消费了几个样本"。看 ring 水位与 drop 即可。 */
            ESP_LOGI(TAG,
                     "t=%" PRIu32 ".%" PRIu32 " s | pushed=%" PRIu32
                     " drop=%" PRIu32
                     " | ring=%u/%d | int_heap=%u KB | frames=%" PRIu32
                     " | bpm=%u.%u peaks=%" PRIu32 " epoch=%" PRIu32,
                     elapsed_ms / 1000, (elapsed_ms % 1000) / 100,
                     pushed, dropped,
                     (unsigned)ecg_ring_count(&s_ring), ECG_RING_CAPACITY,
                     (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
                     s_frames,
                     bpm10 / 10, bpm10 % 10,
                     ecg_rpeak_peak_count(&s_rp), (unsigned)s_rp_epoch);
        }
    }

    if (f != NULL) {
        fclose(f);
    }
    ESP_LOGI(TAG, "producer exited");
    s_prod_task = NULL;
    vTaskDelete(NULL);
}

/* ============================================================================
 * 消费者（Core 0）—— S3 临时 drain，只取不画
 *
 * ⚠ S4 起由 ui_waveform 的绘制路径取代本任务。保留它是因为 S3 需要一个
 *   "消费端"才能验证 SPSC 环缓的正确性（推入数 == 取出数 + 水位）。
 * ========================================================================== */

static void drain_task(void *arg)
{
    (void)arg;

    static int32_t b1[128];
    static int32_t b2[128];

    ESP_LOGI(TAG, "drain(consumer) started on core %d", (int)xPortGetCoreID());

    while (s_running && s_consumer_enabled) {
        const size_t n = ecg_ring_pop(&s_ring, b1, b2, 128);
        s_popped += (uint32_t)n;
        /* 20 ms 一轮：500 Hz 下每轮约 10 个样本，远小于 128 上限 */
        vTaskDelay(pdMS_TO_TICKS(20));
    }

    /* 收尾：把剩余样本取干净，便于对账。
     * ⚠ 卸下消费者（detach）时**不要**做这一步 —— 剩下的样本要留给 UI 去取。 */
    if (s_running) {
        for (;;) {
            const size_t n = ecg_ring_pop(&s_ring, b1, b2, 128);
            if (n == 0) {
                break;
            }
            s_popped += (uint32_t)n;
        }
    }

    ESP_LOGI(TAG, "drain(consumer) exited, popped=%" PRIu32, s_popped);
    s_cons_task = NULL;
    vTaskDelete(NULL);
}

/* ============================================================================
 * 对外接口
 * ========================================================================== */

/** 公共启动骨架（两个数据源共用；差别只在 s_src 与 producer_task 内的分支） */
static esp_err_t pipeline_launch(const char *path, bool loop, ecg_src_t src)
{
    if (s_running) {
        ESP_LOGW(TAG, "already running");
        return ESP_ERR_INVALID_STATE;
    }
    if (src == ECG_SRC_FILE && path == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    s_src  = src;
    s_path = path;
    s_loop = loop;

    ecg_ring_init(&s_ring, APP_RING_CHANNELS);
    s_popped = 0;
    s_frames = 0;
    s_running = true;
    s_consumer_enabled = true;
    s_paused  = false;
    s_eof     = false;
    s_restart = false;
    s_change  = false;

    /* ---- S7：检测器初始化（必须在生产者任务起来之前）---- */
    ecg_rpeak_init(&s_rp, APP_SAMPLE_RATE_HZ);
    s_rp_last_peak_us = 0;
    s_rp_pub_tick     = 0;
    s_rp_q_wr         = 0;
    s_rp_q_rd         = 0;
    s_rp_epoch++;
    s_rp_reset_req    = false;
    s_hr_seq          = 0;
    s_hr_bpm          = 0.0f;
    s_hr_peaks        = 0;
    s_hr_last_peak_us = 0;
    s_hr_fed          = 0;

    /* 生产者：Core 1，优先级 5（LVGL 是 6，必须低于它） */
    BaseType_t ok = xTaskCreatePinnedToCore(producer_task, "ecg_prod",
                                            APP_TASK_PIPE_STACK, NULL,
                                            APP_TASK_PIPE_PRIO, &s_prod_task,
                                            APP_TASK_PIPE_CORE);
    if (ok != pdPASS) {
        s_running = false;
        ESP_LOGE(TAG, "create producer task failed");
        return ESP_ERR_NO_MEM;
    }

    /* 消费者：Core 0，优先级 3（同核上低于 LVGL 6） */
    /* 消费者：Core 0，优先级 3（同核上低于 LVGL 6）。
     * ⚠ 只有 FILE 源需要它：S3 用它验证 SPSC；485 源（S9a）由 UI 直接消费，
     *   创建 drain 会让它在启动瞬间把环缓排干、和 UI 抢样本。 */
    if (src == ECG_SRC_FILE) {
        ok = xTaskCreatePinnedToCore(drain_task, "ecg_drain",
                                     3072, NULL, 3, &s_cons_task, 0);
        if (ok != pdPASS) {
            ESP_LOGE(TAG, "create consumer task failed");
            s_running = false;
            return ESP_ERR_NO_MEM;
        }
    } else {
        s_cons_task = NULL;
    }

    ESP_LOGI(TAG, "pipeline started: src=%d prod core=%d prio=%d, cons core=0 prio=3, ring=%d samples/ch",
             (int)src, APP_TASK_PIPE_CORE, APP_TASK_PIPE_PRIO, ECG_RING_CAPACITY);
    return ESP_OK;
}

esp_err_t ecg_pipeline_start(const char *path, bool loop)
{
    return pipeline_launch(path, loop, ECG_SRC_FILE);
}

/* ============================================================================
 * S9a：485 实时源
 * ========================================================================== */

esp_err_t ecg_pipeline_start_rs485(uint32_t baud)
{
    /* 两源互斥：FILE 源在**暂停态**下 s_running 仍为 true（生产者任务存活等 restart），
     * 不先停就会撞 pipeline_launch 的 "already running"。 */
    if (s_running) {
        ESP_LOGI(TAG, "485: stopping current source (src=%d) first", (int)s_src);
        ecg_pipeline_stop();
    }

    const esp_err_t err = rs485_init(baud);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "485 init failed: %s", esp_err_to_name(err));
        return err;
    }
    rs485_reset_stat();
    ecg_stream_init(&s_stream);

    /* ⚠ 不调 detach_consumer：UI 早已接管环缓（main.c 上电即 detach），
     *   485 源沿用同一个消费端。drain 任务若在场会抢样本，这里显式关掉其开关。 */
    s_consumer_enabled = false;

    const esp_err_t ret = pipeline_launch(NULL, false, ECG_SRC_RS485);
    if (ret != ESP_OK) {
        return ret;
    }
    ESP_LOGI(TAG, "485 source started: UART%d TX=IO%d RX=IO%d @%lu 8N1",
             APP_485_UART_NUM, APP_485_TX_GPIO, APP_485_RX_GPIO,
             (unsigned long)rs485_baud());
    return ESP_OK;
}

void ecg_pipeline_get_stream_stat(ecg_pipeline_stream_stat_t *out)
{
    if (out == NULL) {
        return;
    }
    out->active       = (s_src == ECG_SRC_RS485) && s_running;
    out->frames       = s_stream.frames;
    out->crc_errors   = s_stream.crc_errors;
    out->resync_bytes = s_stream.resync_bytes;
    out->bad_len      = s_stream.bad_len;
    out->seq_gaps     = s_stream.seq_gaps;
    out->snapshots    = s_snapshots;
}

bool ecg_pipeline_src_is_rs485(void)
{
    return (s_src == ECG_SRC_RS485) && s_running;
}

void ecg_pipeline_stop(void)
{
    if (!s_running) {
        return;
    }
    s_running = false;

    /* 等两个任务自然退出（生产者会自动 fclose / vTaskDelete） */
    for (int i = 0; i < 200 && (s_prod_task != NULL || s_cons_task != NULL); i++) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    ESP_LOGI(TAG, "pipeline stopped");
}

bool ecg_pipeline_running(void)
{
    return s_running;
}

void ecg_pipeline_detach_consumer(void)
{
    if (!s_consumer_enabled) {
        return;
    }
    s_consumer_enabled = false;
    /* 等 drain 退出（它每 20 ms 一轮，最多等 200 ms） */
    for (int i = 0; i < 20 && s_cons_task != NULL; i++) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    ESP_LOGI(TAG, "consumer detached -- ring buffer now owned by UI");
}

/* ---- S6 回放控制 ---- */

void ecg_pipeline_pause(void)
{
    s_paused = true;
    ESP_LOGI(TAG, "paused");
}

void ecg_pipeline_resume(void)
{
    s_paused = false;
    ESP_LOGI(TAG, "resumed");
}

bool ecg_pipeline_is_paused(void)
{
    return s_paused;
}

bool ecg_pipeline_eof(void)
{
    return s_eof;
}

void ecg_pipeline_restart(void)
{
    s_restart = true;
    s_paused  = false;      /* 重播隐含恢复 */
    ESP_LOGI(TAG, "restart requested");
}

void ecg_pipeline_set_file(const char *path)
{
    if (path == NULL || path[0] == '\0') {
        return;
    }
    strncpy(s_path_next, path, sizeof(s_path_next) - 1);
    s_path_next[sizeof(s_path_next) - 1] = '\0';
    s_change = true;
    s_paused = false;       /* 换文件隐含恢复播放 */
    ESP_LOGI(TAG, "switch requested -> %s", s_path_next);
}

void ecg_pipeline_get_stats(ecg_pipeline_stats_t *out)
{
    if (out == NULL) {
        return;
    }
    memset(out, 0, sizeof(*out));
    ecg_ring_stats(&s_ring, &out->pushed, &out->dropped, NULL);
    out->popped    = s_popped;
    out->frames_read = s_frames;
    out->ring_level  = ecg_ring_count(&s_ring);
    out->heap_int_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    /* elapsed / loops 由生产者内部日志给出；这里留 0，避免跨任务再取时间 */
}

ecg_ringbuf_t *ecg_pipeline_ringbuf(void)
{
    return &s_ring;
}

/* ============================================================================
 * S7：心率 / R 峰跨核读侧（Core 0）
 * ========================================================================== */

bool ecg_pipeline_get_hr(ecg_pipeline_hr_t *out)
{
    if (out == NULL) {
        return false;
    }
    /* seqlock 读侧：读到奇数（正在写）或前后版本号不一致（写进来了）就重试。
     * 生产者每 200 ms 才写一次，重试 8 次必然拿到稳定值；真拿不到就让调用方
     * 沿用上次的值，不要在这里自旋等待。 */
    for (int i = 0; i < 8; i++) {
        const uint32_t s1 = s_hr_seq;
        if (s1 & 1u) {
            continue;
        }
        __sync_synchronize();
        out->bpm          = s_hr_bpm;
        out->peak_total   = s_hr_peaks;
        out->last_peak_us = s_hr_last_peak_us;
        out->epoch        = s_rp_epoch;
        out->fed          = s_hr_fed;
        __sync_synchronize();
        if (s_hr_seq == s1) {
            return true;
        }
    }
    return false;
}

uint32_t ecg_pipeline_pop_rpeaks(uint32_t *abs_idx, uint32_t max)
{
    uint32_t       rd = s_rp_q_rd;
    const uint32_t wr = s_rp_q_wr;
    uint32_t       n  = 0;

    /* abs_idx == NULL 表示只丢弃不取 —— epoch 变更时用它清空陈旧条目 */
    while (rd != wr && (abs_idx == NULL || n < max)) {
        if (abs_idx != NULL) {
            abs_idx[n] = s_rp_q[rd % APP_RPEAK_QUEUE_DEPTH];
        }
        rd++;
        n++;
    }
    s_rp_q_rd = rd;
    return n;
}

void ecg_pipeline_rpeak_reset(void)
{
    s_rp_reset_req = true;
}

/* ============================================================================
 * S3：回放对账输出 + 倍速
 * ========================================================================== */

void ecg_pipeline_set_rpeak_dump(bool on)
{
    /* 开启时清掉攒了一半的行，避免上一段的残行粘到新序列前面 */
    if (on && !s_rp_dump) {
        dump_line_reset();
    }
    s_rp_dump = on;
    ESP_LOGI(TAG, "rpeak dump -> %s", on ? "on" : "off");
}

void ecg_pipeline_set_speed(int speed)
{
    if (speed != 1 && speed != 2 && speed != 4 && speed != 8) {
        ESP_LOGW(TAG, "unsupported speed %d -- fallback to 1x", speed);
        speed = 1;
    }
    s_speed = speed;
    ESP_LOGI(TAG, "replay speed -> %dx", speed);
}

void ecg_pipeline_set_dump_file(const char *path)
{
    if (path == NULL || path[0] == '\0') {
        s_dump_path[0] = '\0';
        s_dump_fill    = 0;
        return;
    }
    /* `%.*s` 限定长度：-Werror=format-truncation 不允许「源可能比目标长」的裸 %s */
    snprintf(s_dump_path, sizeof(s_dump_path), "%.*s",
             (int)sizeof(s_dump_path) - 1, path);
    s_dump_fill     = 0;
    s_dump_overflow = false;
    ESP_LOGI(TAG, "replay result file -> %s", s_dump_path);
}
