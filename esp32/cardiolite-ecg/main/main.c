/**
 * @file main.c
 * @brief CardioLite 显示终端 —— 初始化编排 + 阶段验收页
 *
 * 已完成阶段：
 *   S0 工程骨架    —— 编译 / 点亮 / 触摸 / 核归属，四项验收通过
 *   S1 SD 挂载     —— CH422G 片选 + SPI2 + FAT 挂载 + ecg.bin 首帧探测
 *
 * 本文件只做初始化顺序编排与一个验收页。业务模块按实施计划 §4.3 的清单
 * 逐步加入（S2 帧解析 → S3 环缓+双核 → S4 画布 ……）。
 *
 * 初始化顺序（勿随意调整）：
 *   log_banner → BSP(面板+触摸+背光) → SD 挂载 → LVGL adapter
 *   → display/touch 注册 → adapter start → 建页面
 *   理由：背光最先亮（用户立刻看到屏有反应），SD 探测那几百毫秒趁屏幕
 *   还没被 LVGL 接管时做掉，页面一出来就能显示 SD 结论。
 */

#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "sdkconfig.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_chip_info.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_lv_adapter.h"
#include "lvgl.h"
#include "waveshare_rgb_lcd_port.h"

#include "app_config.h"
#include "app_state.h"
#include "cli_console.h"
#include "ecg/ecg_frame.h"
#include "ecg/ecg_pipeline.h"
#include "storage/storage_sd.h"
#include "test_mode.h"
#include "ui/ui_bpm.h"
#include "ui/ui_button_bar.h"
#include "ui/ui_waveform.h"

static const char *TAG = "cardiolite";

/* ============================================================================
 * 编译期硬约束 —— 配置错了就编不过，不靠运行日志去发现
 * ========================================================================== */

/* 本工程的布局常量必须与 BSP 的屏参一致 */
_Static_assert(APP_LCD_H_RES == EXAMPLE_LCD_H_RES, "APP_LCD_H_RES 与 BSP 不一致");
_Static_assert(APP_LCD_V_RES == EXAMPLE_LCD_V_RES, "APP_LCD_V_RES 与 BSP 不一致");

/* LVGL 任务必须钉在 Core 0，把 Core 1 让给数据管线。
 *
 * ⚠ 纠正一个曾经的误解（2026-10-23 查证）：
 *   CONFIG_EXAMPLE_LVGL_PORT_TASK_CORE 是**厂家 demo 遗留的 Kconfig**
 *   （定义在 lcd-bringup/main/Kconfig.projbuild），esp_lvgl_adapter 并不读它，
 *   lcd-bringup/main/main.c 也从未引用 —— 它在旧工程里同样不生效（死配置）。
 *   真正的控制点是 esp_lv_adapter_config_t.task_core_id，
 *   其默认值 ESP_LV_ADAPTER_DEFAULT_TASK_CORE_ID = -1（tskNO_AFFINITY），
 *   即不指定核，任务可能落任意核。
 *   ⇒ 必须在这里显式赋值，不能靠 sdkconfig。
 */
#define APP_LVGL_TASK_CORE      0
_Static_assert(APP_LVGL_TASK_CORE == 0, "LVGL 必须跑 Core 0（Core 1 留给数据管线）");

/* ============================================================================
 * S0 自检页面（临时，S4/S6 会用 ui_root + ui_waveform 取代）
 *
 * 配色取自需求 v1.2 §3.4 的 A 套（浅色纸），目的是让上板验收能直接看到
 * 目标观感。S8 会把这些值迁进 ui_theme_t，届时本页删除。
 * ========================================================================== */

/* ============================================================================
 * 屏幕布局（需求 v1.2 §3.1）
 *   画布 1  y = 0   ~ 215   ← ui_waveform
 *   画布 2  y = 216 ~ 431   ← ui_waveform
 *   按钮栏  y = 432 ~ 479   ← ui_button_bar（九个按钮，S6）
 * ========================================================================== */

/* S2 主文件扫描结果（s2_scan_all 填；S4 起不再显示在屏上，仅留作日志对照，
 * 同时用来算「SD xx s」显示在回放按钮的副行） */
static uint32_t  s_main_frames;
static uint32_t  s_main_samples;
static uint32_t  s_main_crc_err;
static bool      s_main_ok;

static void on_core_probe(lv_timer_t *timer)
{
    static bool logged = false;
    if (logged) {
        return;                     /* 只打一次，S4 起不再占屏幕 */
    }
    logged = true;

    const int core = (int)xPortGetCoreID();
    ESP_LOGI(TAG, "[probe] LVGL worker task actually runs on core %d", core);
    if (core != APP_LVGL_TASK_CORE) {
        ESP_LOGE(TAG, "[probe] 期望 core %d，实际 core %d —— 核归属未生效！",
                 APP_LVGL_TASK_CORE, core);
    }
}

/* 两套配色（需求 v1.2 §3.4）。S8 会改成"长按 BPM 浮层 2 s 切换 + NVS 持久化"，
 * 此处是 S4 补测用的临时入口：点状态条右侧的小按钮切。 */
/* ---- UI 心跳：1 s 检查管线是否播完（EOF → ended）----
 *
 * 为什么用轮询而不是回调：生产者任务在 EOF 后**不退出**（停在循环里等 restart），
 * 所以没有"任务结束"可挂回调；轮询 ecg_pipeline_eof() 最省事且无跨任务竞态。 */
static void on_ui_tick(lv_timer_t *t)
{
    (void)t;
    if (app_state_ro()->play == APP_PLAYING && ecg_pipeline_eof()) {
        if (app_set_ended()) {
            ui_button_bar_refresh();
        }
    }
}


/* ============================================================================
 * S7 离线自检：整份文件喂给 R 峰检测器，输出峰列表与 BPM
 *
 * 用途：与 C# 参考实现比对（`tools/rpeak_ref/ref_peaks.txt`）。
 * 判据（实施计划 §6 S7）：R 峰位置误差 ≤ 2 样本、BPM 误差 ≤ 2 bpm。
 *
 * 只在 RP_SELFTEST=1 时编入；验证通过后置 0，避免每次开机都白扫一遍。
 * ========================================================================== */

#define RP_SELFTEST     0

/* ---- 批处理入口（S2 起由 CLI 的 SD 标记文件运行时触发，不再是编译期宏）----
 * 历史（S7_BATCH 宏）：曾用 `#define S7_BATCH 1` 在编译期打开批处理，上电读
 * `/sdcard/batch.txt` 全速跑检测器。S2 起改为：CLI 命令 `mit-run-all`/`ladder-run-all`
 * 写 SD 标记文件 `/sdcard/batch_pending.flag`，重启后 LVGL 之前读标记自动跑，跑完删标记。
 * 生产固件「不带批处理入口」的语义，现在由「无标记文件就不跑」天然保证。
 * 批处理代码始终编入（CLI 测试模式是固件的一部分）。 */
#include "ecg/ecg_batch.h"

#if RP_SELFTEST
#include "ecg/ecg_rpeak.h"
#include "ecg/rp_selftest_data.h"

/* 用**内嵌样本**跑，不依赖 SD 卡挂载 —— 卡接触不良时也能做回归。
 * 只覆盖前 32 s：参考实现的 Peaks 队列只留最近 32 个，要验证前段就得让总峰数 < 32。 */
static void s7_rpeak_selftest(void)
{
    static ecg_rpeak_t rp;
    static int64_t     peaks[80];

    ecg_rpeak_init(&rp, APP_SAMPLE_RATE_HZ);

    uint32_t hits = 0;
    for (uint32_t i = 0; i < rp_selftest_count; i++) {
        int64_t idx = 0;
        if (ecg_rpeak_process(&rp, (float)rp_selftest_samples[i], &idx)) {
            if (hits < (uint32_t)(sizeof(peaks) / sizeof(peaks[0]))) {
                peaks[hits] = idx;
            }
            hits++;
        }
    }

    const uint32_t bpm10 = (uint32_t)(ecg_rpeak_bpm(&rp) * 10.0f + 0.5f);

    ESP_LOGI(TAG, "===== S7 rpeak selftest (embedded %u samples) =====",
             (unsigned)rp_selftest_count);
    ESP_LOGI(TAG, "peaks=%u bpm=%u.%u", (unsigned)hits,
             (unsigned)(bpm10 / 10), (unsigned)(bpm10 % 10));

    for (uint32_t i = 0; i < hits && i < 64; i += 8) {
        char line[200];
        int  pos = 0;
        for (uint32_t k = i; k < i + 8 && k < hits; k++) {
            pos += snprintf(line + pos, sizeof(line) - (size_t)pos, "%lld ",
                            (long long)peaks[k]);
        }
        ESP_LOGI(TAG, "  peaks[%u]: %s", (unsigned)i, line);
    }
    ESP_LOGI(TAG, "===== end selftest =====");
}
#endif

/* ============================================================================
 * S2 自检：扫描 SD 上的全部测试样本，逐文件对账
 *
 * 期望值来自需求 v1.2 §2.4「录制数据实测验证【实测】」表（该表本身已交叉验证）：
 *   ① 帧数 ② STA ③ 样本数×N÷500Hz == 文档时长 —— 三者互证
 * 例如 1796 × 25 = 44,900 样本 ⇒ 44,900/500 = 89.8 s ✓
 * ========================================================================== */

typedef struct {
    const char *path;
    uint32_t    exp_frames;
    uint16_t    exp_len;
    uint8_t     exp_sta;      /* 需求 v1.2 §2.4 的 STA 列 */
    uint16_t    exp_duration_ds;  /* 期望时长 × 10（0.1 s 分辨率，避免浮点） */
} s2_case_t;

static const s2_case_t k_s2_cases[] = {
    /* path                        frames  LEN  STA    时长(0.1s) */
    { "/sdcard/ecg.bin",            1796,  53, 0x02, 898 },  /* 89.8 s */
    { "/sdcard/alt-120bpm.bin",     1182,  53, 0x02, 591 },  /* 59.1 s */
    { "/sdcard/test-raw853.bin",    1136,  53, 0x0A, 568 },  /* 56.8 s，raw853=1 */
    { "/sdcard/test-dualch.bin",      71, 103, 0x06,  35 },  /* 3.5 s，双通道 */
    { "/sdcard/test-50hz-sine.bin",  141,  53, 0x00,  70 },  /* 7.0 s */
    { "/sdcard/test-short.bin",      116,  53, 0x02,  58 },  /* 5.8 s */
};

#define S2_CASE_COUNT ((int)(sizeof(k_s2_cases) / sizeof(k_s2_cases[0])))

static void s2_scan_all(void)
{
    ESP_LOGI(TAG, "==== S2: frame scan (%d files) ====", S2_CASE_COUNT);

    int pass = 0;
    int total = 0;

    for (int i = 0; i < S2_CASE_COUNT; i++) {
        const s2_case_t *tc = &k_s2_cases[i];
        ecg_scan_stat_t  st;
        const char      *name = tc->path + 8;   /* 跳过 "/sdcard/" */

        if (ecg_scan_file(tc->path, &st) != ESP_OK) {
            ESP_LOGW(TAG, "[%d] %-22s OPEN FAILED", i, name);
            continue;
        }
        total++;

        char sta_str[64];
        ecg_sta_to_str(st.first_sta, st.first_sta_valid, sta_str, sizeof(sta_str));

        /* ---- 五项独立判据 ---- */
        const bool frames_ok = (st.frames == tc->exp_frames);
        const bool len_ok    = (tc->exp_len == ECG_LEN_DUAL)
                               ? (st.cnt_len_103 == st.frames && st.cnt_len_53 == 0)
                               : (st.cnt_len_53  == st.frames && st.cnt_len_103 == 0);
        const bool crc_ok    = (st.crc_errors == 0);
        const bool gap_ok    = (st.seq_gaps == 0);
        const bool tail_ok   = (st.tail_bytes == 0);
        const bool sta_ok    = st.first_sta_valid && (st.first_sta == tc->exp_sta);

        /* 时长反算：samples_ch1 / 500 Hz，与 §2.4 的时长列对账（±0.1 s） */
        const uint32_t dur_ds = (st.samples_ch1 * 10u) / APP_SAMPLE_RATE_HZ;
        const long     dur_err = (long)dur_ds - (long)tc->exp_duration_ds;
        const bool     dur_ok  = (dur_err >= -1 && dur_err <= 1);

        const bool all_ok = frames_ok && len_ok && crc_ok && gap_ok && tail_ok && sta_ok && dur_ok;

        ESP_LOGI(TAG, "[%d] %-22s %s   size=%ld B", i, name, all_ok ? "PASS" : "FAIL", st.file_size);
        ESP_LOGI(TAG, "     frames %u/%u %s | LEN 53=%u 103=%u other=%u",
                 st.frames, tc->exp_frames, frames_ok ? "OK" : "MISMATCH",
                 st.cnt_len_53, st.cnt_len_103, st.other_len);
        ESP_LOGI(TAG, "     crc_err=%u sync_err=%u seq_gap=%u tail=%u",
                 st.crc_errors, st.sync_errors, st.seq_gaps, st.tail_bytes);
        ESP_LOGI(TAG, "     samples ch1=%u ch2=%u  => %u.%u s (expect %u.%u s) %s",
                 st.samples_ch1, st.samples_ch2, dur_ds / 10, dur_ds % 10,
                 tc->exp_duration_ds / 10, tc->exp_duration_ds % 10,
                 dur_ok ? "OK" : "MISMATCH");
        ESP_LOGI(TAG, "     STA 0x%02X (expect 0x%02X) %s | seq %u..%u | %s",
                 st.first_sta, tc->exp_sta, sta_ok ? "OK" : "MISMATCH",
                 st.first_seq, st.last_seq, sta_str);

        if (all_ok) {
            pass++;
        }
        if (i == 0) {
            s_main_frames  = st.frames;
            s_main_samples = st.samples_ch1;
            s_main_crc_err = st.crc_errors;
            s_main_ok      = all_ok;
        }
    }

    ESP_LOGI(TAG, "==== S2 scan result: %d / %d PASS ====", pass, total);
}

/* ============================================================================
 * 启动横幅 —— 把 S0 验收要看的事实一次性打全
 * ========================================================================== */

static void log_banner(void)
{
    esp_chip_info_t chip;
    esp_chip_info(&chip);

    ESP_LOGI(TAG, "==========================================================");
    ESP_LOGI(TAG, " CardioLite display terminal -- S1 SD mount");
    ESP_LOGI(TAG, " IDF       : %s", IDF_VER);
    ESP_LOGI(TAG, " chip      : ESP32-S3 rev %d.%d, %d core(s)",
             chip.revision / 100, chip.revision % 100, chip.cores);
    ESP_LOGI(TAG, " flash     : %d MB", APP_FLASH_SIZE_MB);
    ESP_LOGI(TAG, " PSRAM     : %u KB total, %u KB free",
             (unsigned)(heap_caps_get_total_size(MALLOC_CAP_SPIRAM) / 1024),
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));
    ESP_LOGI(TAG, " int. RAM  : %u KB free",
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024));
    ESP_LOGI(TAG, " panel     : %dx%d RGB565, %d MHz pclk",
             APP_LCD_H_RES, APP_LCD_V_RES, EXAMPLE_LCD_PIXEL_CLOCK_HZ / 1000000);
    ESP_LOGI(TAG, " LVGL task : core = %d (set via adapter_config.task_core_id)",
             APP_LVGL_TASK_CORE);
    ESP_LOGI(TAG, " app_main  : running on core %d", (int)xPortGetCoreID());
    ESP_LOGI(TAG, " calibration: mmX=%.3f px/mm  mmY=%.2f px/mm  1mV=%.1f px @gain5",
             APP_MMX_PX_PER_MM, APP_MMY_PX_PER_MM, APP_PX_PER_MV(APP_GAIN_5));
    ESP_LOGI(TAG, "==========================================================");
}

/* ============================================================================
 * app_main
 * ========================================================================== */

void app_main(void)
{
    /* ---- 0. 上电静默：把全局日志压到 ERROR（只留致命错误）----
     * 串口走 USB Serial JTAG，CLI 要干净。ESP_LOGI 的启动流水、S2 扫描、
     * 每帧 fps 等 INFO 日志会淹没 `cardiolite>` 提示符。
     * 调试时用 CLI 的 `log info/debug/verbose` 按需打开，用完 `log off` 关掉。
     * 这里放在 log_banner 之前，让启动阶段也静默（真正的 error 仍会冒出）。 */
    esp_log_level_set("*", ESP_LOG_ERROR);

    log_banner();

    /* ---- 1. 板级初始化：RGB 面板 + GT911 触摸 + CH422G ---- */
    const esp_lv_adapter_rotation_t rotation = ESP_LV_ADAPTER_ROTATE_0;
    const esp_lv_adapter_tear_avoid_mode_t tear_mode = ESP_LV_ADAPTER_TEAR_AVOID_MODE_DEFAULT_RGB;

    esp_lcd_panel_handle_t panel_handle = NULL;
    esp_lcd_touch_handle_t touch_handle = NULL;

    ESP_ERROR_CHECK(waveshare_esp32_s3_rgb_lcd_init(tear_mode, rotation,
                                                    &panel_handle, &touch_handle));
    ESP_ERROR_CHECK(waveshare_rgb_lcd_backlight_on());
    ESP_LOGI(TAG, "panel ready: panel=%p touch=%p", (void *)panel_handle, (void *)touch_handle);

    /* ---- 1.5 SD 卡挂载 + 首帧探测（S1）----
     * 放在 LVGL 之前：背光已经亮了，SD 探测要几百毫秒，这段时间先把卡读完，
     * 页面一出来就能显示结论。失败不阻断启动，摘要留给页面显示。 */
    ESP_LOGI(TAG, "---- S1: SD card mount ----");
    if (storage_sd_mount() == ESP_OK) {
        storage_sd_probe_ecg_file();   /* 失败也继续：摘要里会写明原因 */

        /* ---- 扫目录，按首帧 LEN 把 .bin 分成单通道(LEN=53)/双通道(LEN=103) ----
         * 切「通道 I / II」时要靠这张表换文件（需求 §2.2 规定文件名固定为 ecg.bin，
         * 所以同一时刻只有一个主数据文件，双通道样本得单独放一个 .bin）。 */
        storage_sd_scan_files();

        /* ---- 1.6 S2 帧解析自检（扫全部样本，逐文件对账）---- */
        s2_scan_all();

        /* ---- S2 批处理：读 SD 标记文件，有则跑清单（解法 A）----
         *
         * 触发链：CLI `mit-run-all`/`ladder-run-all` 写 `/sdcard/batch_pending.flag`
         * （内容=清单文件名，如 `batch_mit.txt`）→ 用户重启 → 此处 LVGL 之前读标记、
         * 拼 `/sdcard/<清单名>`、全速跑检测器、跑完删标记。
         *
         * 🛑 **必须放在 LVGL 初始化之前**。曾经为了"别黑屏 75 s"挪到 UI 之后，
         *   实测是双重灾难，2026-10-26 记：
         *     ① 速度：单文件 7.6 s → 约 1000 s（**130 倍**）。app_main 优先级远低于
         *        LVGL(prio 6)，被持续抢占，只能用剩余 CPU。
         *     ② 输出：两个任务同时写 UART，**字符级交错**，
         *        形如 `P 129510 129910 130312I (150312) ui_wave: fps=...`，峰索引不可解析。
         *   ⇒ 批处理是**一次性离线工具**，宁可黑屏一百秒，也要串口干净、速度正常。
         *   无标记文件时什么都不做，正常启动。 */
        const char *batch_manifest = test_mode_get_batch();
        if (batch_manifest != NULL) {
            char manifest_path[64];
            snprintf(manifest_path, sizeof(manifest_path), "/sdcard/%s", batch_manifest);
            ESP_LOGI(TAG, "---- S2 batch: running %s (screen idle until done) ----", manifest_path);
            (void)ecg_batch_run_manifest(manifest_path);
            test_mode_clear_batch();
            ESP_LOGI(TAG, "---- S2 batch finished ----");
        }
    }

#if RP_SELFTEST
    /* ---- S7 离线自检：R 峰检测（用内嵌样本，**不依赖 SD**）----
     * 放在 mount 判断之外：卡接触不良时也要能跑回归。 */
    s7_rpeak_selftest();
#endif
    ESP_LOGI(TAG, "SD summary: %s", storage_sd_last_summary());

    /* ---- 2. LVGL 适配层 ---- */
    esp_lv_adapter_config_t adapter_config = ESP_LV_ADAPTER_DEFAULT_CONFIG();
    adapter_config.task_stack_size = 12 * 1024;
    adapter_config.stack_in_psram  = true;
    /* ★ 关键：显式钉在 Core 0。
     * adapter 默认 task_core_id = -1（tskNO_AFFINITY），不设就可能落到 Core 1，
     * 与后面的数据管线抢时间。见文件头关于 Kconfig 死配置的说明。 */
    adapter_config.task_core_id    = APP_LVGL_TASK_CORE;
    ESP_ERROR_CHECK(esp_lv_adapter_init(&adapter_config));

    esp_lv_adapter_display_config_t disp_config = ESP_LV_ADAPTER_DISPLAY_RGB_DEFAULT_CONFIG(
        panel_handle, NULL, APP_LCD_H_RES, APP_LCD_V_RES, rotation);
    disp_config.profile.use_psram = true;

    lv_display_t *disp = esp_lv_adapter_register_display(&disp_config);
    assert(disp != NULL);

    if (touch_handle != NULL) {
        esp_lv_adapter_touch_config_t touch_config =
            ESP_LV_ADAPTER_TOUCH_DEFAULT_CONFIG(disp, touch_handle);
        lv_indev_t *indev = esp_lv_adapter_register_touch(&touch_config);
        assert(indev != NULL);
        ESP_LOGI(TAG, "touch registered");
    } else {
        ESP_LOGW(TAG, "touch handle is NULL -- 触摸不可用");
    }

    ESP_ERROR_CHECK(esp_lv_adapter_start());

    /* 内部 RAM 足迹分三段记录，便于定位「谁吃了内存」：
     *   ① log_banner（LVGL 之前）  ② 这里（LVGL 之后）  ③ 管线里的 int_heap
     * S2 实测：① 289 KB，③ 130 KB，差 159 KB —— 环缓 40 KB + 任务栈 7 KB，
     * 其余是 LVGL 初始化（对象池/字体缓存等）。打印出来坐实这个分解。 */
    ESP_LOGI(TAG, "heap after LVGL start: int=%u KB free, psram=%u KB free",
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));

    /* ---- 3. S3 数据管线（Core 1 生产者）----
     * S6 起 loop = **false**：读到 EOF 后停在循环里等 restart，
     * 这样界面能显示「已播完」并按「重播」从头来。
     * （S3 那时用 loop=true 是为了持续供数验证管线，那个目的已经达到。） */
    if (storage_sd_is_mounted()) {
        /* 启动文件取扫描得到的「单通道」那一项（优先 ecg.bin），
         * 没有单通道文件时退回 APP_SD_FILE_PATH。双通道文件只在切通道时用。 */
        const storage_sd_files_t *files = storage_sd_get_files();
        const char *start_path = (files->single[0] != '\0') ? files->single
                                                           : APP_SD_FILE_PATH;

        ESP_LOGI(TAG, "---- S3: pipeline (start file = %s) ----", start_path);
        const esp_err_t pret = ecg_pipeline_start(start_path, false);
        if (pret != ESP_OK) {
            ESP_LOGE(TAG, "pipeline start failed: %s", esp_err_to_name(pret));
        } else {
            /* ---- S4：把环缓的消费权从临时 drain 任务交给 UI ----
             * ⚠ 两者不能并存：并存会互相抢样本，表现为波形缺帧。 */
            ecg_pipeline_detach_consumer();
            /* ---- S6：启动即暂停，等用户点「回放」 ---- */
            ecg_pipeline_pause();
        }
    } else {
        ESP_LOGW(TAG, "SD not mounted, pipeline skipped");
    }

    /* ---- S6：初始化界面状态（file_ok 以「是否解析出帧」为准，不看 CRC 全对）---- */
    const uint32_t file_sec = (s_main_frames > 0)
                              ? (uint32_t)((uint64_t)s_main_frames * 25u / APP_SAMPLE_RATE_HZ)
                              : 0;
    app_state_init(s_main_frames > 0, file_sec);

    /* ---- 4. S4 画布 + S6 九按钮（LVGL 非线程安全，必须持锁）---- */
    if (esp_lv_adapter_lock(-1) == ESP_OK) {
        lv_obj_t *scr = lv_scr_act();
        lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
        /* 屏幕底色跟随默认主题（上电默认深色 B 套，2026-10-23 拍板）。
         * 否则深色画布周围会露出浅色边距。 */
        lv_obj_set_style_bg_color(scr, lv_color_hex(UI_THEME_DARK.canvas_bg), LV_PART_MAIN);

        if (ui_waveform_create(scr) != ESP_OK) {
            ESP_LOGE(TAG, "waveform create failed -- 画布不可用");
        }
        /* S7：BPM 浮层必须建在画布**之后** —— LVGL 后建对象在上层，
         * 反了就变成画布盖住浮层（每帧全量重绘会把数字擦掉）。 */
        if (ui_bpm_create(scr) != ESP_OK) {
            ESP_LOGE(TAG, "bpm overlay create failed -- 心率数值不可用");
        }
        if (ui_button_bar_create(scr) != ESP_OK) {
            ESP_LOGE(TAG, "button bar create failed -- 按钮不可用");
        }
        lv_timer_create(on_ui_tick, 1000, NULL);
        lv_timer_create(on_core_probe, 3000, NULL);
        esp_lv_adapter_unlock();
    }

    ESP_LOGI(TAG, "S6 ready. 验收点: 九按钮布局 + 灰显规则 + 回放四态 + 长按删除");

    /* ---- 5. CLI 测试模式命令台（S1 起）----
     * 放在所有 UI 建好之后：REPL 内部自建独立任务，start 只发 notify、非阻塞，
     * 返回后 app_main 正常结束，不干扰 LVGL 与数据管线。 */
    if (cli_console_start() != ESP_OK) {
        ESP_LOGE(TAG, "CLI 命令台启动失败");
    }

}
