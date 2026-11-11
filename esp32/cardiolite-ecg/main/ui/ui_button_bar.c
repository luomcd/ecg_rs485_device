/**
 * @file ui_button_bar.c
 * @brief 九按钮栏实现（S6）
 *
 * 布局（需求 v1.2 §3.3）：y = 432，高 48，内边距 4、间隙 3、按钮 85×40（flex 均分）。
 *
 * **单行居中**（2026-10-23 按实机观感调整）：每个按钮只有一个 13 px 标签，
 * 不再分主行/副行 —— 85 px 宽放两行反而挤，单行更清楚。文案形如
 *   开始 / 冻结 / 清屏 / 5mm/mV / 25mm/s / 诊断 / 陷波ON / 通道I / 回放
 *
 * 灰显规则（需求 §8.2 / §8.5；★T209 2026-11-05 修订）：
 *   - **采集：恒灰、永不响应点击**。采集启停全部走 CLI（`485-send start|stop`）——
 *     本设计没有 STOP 键（T206 拍板⑤），屏上按钮一旦写成「采集中」就再也变不回来，
 *     只能复位 STM32，屏上状态与真值长期打架。详见 `s_btn` 上方注释。
 *   - 模式 / 陷波 / 通道：**灰显**（三参数属采集端命令 0x04/0x05/0x06，回放无采集端可改），
 *     但**文字显示实际状态**（从 STA 解出）——
 *     ⚠ ★T209 起 STA 才是真值：`ecg_pipeline.c` 每帧上抛 `app_state_set_sta()`。
 *     T209 之前该函数**零调用** ⇒ 这里显示的是硬编码兜底值 0x02（假值）。
 *   - 回放/记录：SD 无文件时灰
 *   - 冻结 / 清屏 / 增益 / 走纸：有文件即可点
 */

#include "ui_button_bar.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"

#include "app_config.h"
#include "app_state.h"
#include "ecg/ecg_frame.h"      /* ECG_STA_BIT_MODE / NOTCH / CHANNEL */
#include "ecg_pipeline.h"
#include "storage/storage_sd.h"
#include "test_mode.h"
#include "ui_bpm.h"
#include "ui_waveform.h"

static const char *TAG = "ui_btn";

/* 中文子集字库（由 tools/gen_fonts.sh 生成，见 main/ui/font/） */
extern const lv_font_t font_zh_13;

/* ============================================================================
 * 状态
 * ========================================================================== */

typedef struct {
    lv_obj_t *btn;
    lv_obj_t *label;
} btn_t;

static btn_t            s_btn[BTN_COUNT];
static lv_obj_t        *s_bar;
static const ui_theme_t *s_theme;

/* ★T209（11-05）：「开始」按钮**永久置灰、不承担任何功能**。
 *
 * 为什么删掉它（回溯 T208 → T209）：
 *   T208 让它点一下发 `0x01 START` 并置灰显示「采集中」。实测下来这条路走不通 ——
 *   **本设计没有 STOP 键**（T206 拍板⑤：START 后一直跑），所以要停只能复位 STM32；
 *   而一旦屏上文字变成「采集中」，复位后 STM32 已停、按钮文字**永远变不回去**
 *   （`s_capture_started` 只在源切换时才复位）⇒ 屏上显示与真实状态长期打架。
 *   ⇒ 采集的启停**全部走 CLI**（`485-send start` / `485-send stop`），
 *     按钮保留只为布局不变、且明确告诉人"这里不是操作入口"。
 * ⚠ 随之删除的还有 `s_capture_started` 变量与 `ecg_cmd_start()` 调用 ——
 *   本文件**不再发任何下行命令**，「诊断/陷波/通道」三个键同样只显示 STA、不可点。 */

/* 增益 / 走纸的档位（与 ui_waveform 的当前值保持同步） */
static const float k_gain_v[]  = { APP_GAIN_2_5, APP_GAIN_5, APP_GAIN_10, APP_GAIN_20 };
static const float k_speed_v[] = { APP_SPEED_12_5_MM_S, APP_SPEED_25_MM_S, APP_SPEED_50_MM_S };
#define GAIN_N   ((int)(sizeof(k_gain_v) / sizeof(k_gain_v[0])))
#define SPEED_N  ((int)(sizeof(k_speed_v) / sizeof(k_speed_v[0])))

/* ============================================================================
 * 样式
 * ========================================================================== */

static void apply_btn_style(btn_t *b, bool selected, bool enabled)
{
    const ui_theme_t *th = s_theme;
    uint32_t bg, txt, brd;

    if (!enabled) {
        bg  = th->btn_dis_bg;
        txt = th->btn_dis_text;
        brd = th->btn_dis_bg;
    } else if (selected) {
        bg  = th->btn_sel_bg;
        txt = th->btn_sel_text;
        brd = th->btn_sel_border;
    } else {
        bg  = th->btn_bg;
        txt = th->btn_text;
        brd = th->btn_border;
    }

    lv_obj_set_style_bg_color(b->btn, lv_color_hex(bg), LV_PART_MAIN);
    lv_obj_set_style_border_color(b->btn, lv_color_hex(brd), LV_PART_MAIN);
    lv_obj_set_style_text_color(b->label, lv_color_hex(txt), LV_PART_MAIN);

    if (enabled) {
        lv_obj_clear_state(b->btn, LV_STATE_DISABLED);
        lv_obj_add_flag(b->btn, LV_OBJ_FLAG_CLICKABLE);
    } else {
        lv_obj_add_state(b->btn, LV_STATE_DISABLED);
        lv_obj_clear_flag(b->btn, LV_OBJ_FLAG_CLICKABLE);
    }
}

/* 定点格式化：输出 "2.5" / "5" / "12.5" 这类，避免 %f（LVGL 的浮点格式默认关） */
static void fmt_1dp(char *buf, size_t n, float v)
{
    const int v10 = (int)(v * 10.0f + 0.5f);
    if (v10 % 10 == 0) {
        snprintf(buf, n, "%d", v10 / 10);
    } else {
        snprintf(buf, n, "%d.%d", v10 / 10, v10 % 10);
    }
}

/* ============================================================================
 * ★T212：清屏动作（屏上键 + `ui-clear` CLI 共用）
 * ==========================================================================*/

/* 清屏 = 只清视觉：波形缓冲 + R 峰队列 + BPM。
 * 不动播放状态、不动回放位置（需求 §8.4 2026-11-06 订正，方案 B）。
 *
 * 🛑 调用方必须持有 LVGL 锁（内部会动 lv 标签）：
 *   - 屏上按钮回调在 LVGL 任务里 ⇒ 已持锁，直接调；
 *   - `ui-clear` CLI 在 console 任务里 ⇒ 先 `esp_lv_adapter_lock(-1)`。
 * 本函数**故意不加锁** —— 加了会与按钮路径二重加锁，是否安全取决于适配层的
 * 互斥量是否递归，不去赌。
 *
 * 抽成独立函数的目的：让自动化验收（`_t211_clear_probe.py --trigger cli`）
 * 能自己触发清屏，不必靠人点屏。⚠ 它只覆盖动作层，
 * **不覆盖 LVGL 事件分发**（`on_btn_click` 的 `case BTN_CLEAR`）——
 * 那条路径只能靠人手点一次确认。 */
void ui_clear_apply(void)
{
    if (!app_action_clear()) {
        return;         /* 无文件 ⇒ 与屏上灰键一致：什么都不做 */
    }
    ui_waveform_clear();            /* 清波形 + 已绘制的 R 峰标记（纯视觉） */
    ui_waveform_set_frozen(false);
    /* ★A-clear（T230 批准，2026-11-11）：清屏**只清视觉**，不再复位检测器与 BPM。
     *
     * 原实现调用 `ecg_pipeline_rpeak_reset()` + `ui_bpm_reset()`，实际后果：
     *   · `peaks` / `fed` 计数归零、epoch 递增 ⇒ 测量期间计数器被"无故清掉"
     *     （11-11 实测踩过：Δpeaks 出现负数，密度算出假值）；
     *   · BPM 回到 `--` 重新收敛 ⇒ 与 T212「清屏＝纯视觉」的语义不符。
     * 需要真正复位时用 `rpeak reset` 命令（显式、可追溯到操作者）。 */
    ui_button_bar_refresh();        /* 冻结态被解除 ⇒ 冻结键文字要跟着变回「冻结」 */
}

/* ============================================================================
 * 按钮动作
 * ========================================================================== */

static void on_btn_click(lv_event_t *e)
{
    const btn_id_t id = (btn_id_t)(uintptr_t)lv_event_get_user_data(e);
    const app_state_t *st = app_state_ro();

    /* CLI 测试模式下禁用 UI 按钮：UI 仅作波形展示，所有交互交给串口命令。
     * 直接 return，不打印（避免测试模式下串口被点击日志污染）。 */
    if (test_mode_get() != TEST_NORMAL) {
        return;
    }

    ESP_LOGI(TAG, "click btn %d", (int)id);

    switch (id) {
    case BTN_CAPTURE:
        /* ★T209：**本键恒灰、永不可点**（见文件头 `s_btn` 上方注释）。
         * 保留这个 case 只为占位：万一将来又放开可点，事件路由不会掉进 default
         * 而静默什么都不做。采集启停改走 CLI：`485-send start` / `485-send stop`。 */
        break;

    case BTN_FREEZE:
        if (app_action_freeze()) {
            ui_waveform_set_frozen(st->frozen);
        }
        break;

    case BTN_CLEAR:
        /* ★T212（2026-11-06 猛哥拍板方案 B）：**清屏 = 只清视觉，不动播放状态、不动回放位置**。
         *
         * 与 T211 的差别（T211 的补丁已在 T212 里整段撤掉）：
         *   T211 的做法＝`restart()` 归零 + 按 `prev_play` 按需补 `pause()`；
         *   而 `app_action_clear()` 当时又把自己的 `play` 强改成 IDLE ⇒
         *   **同一个键两次点击行为相反**：播放态第一次点是「从头重播」，
         *   第二次点却变成「暂停」（因为 play 已被上一次改成 IDLE）。
         *   现在：位置原地不动，只清显示缓冲 ⇒ 画面空白后，
         *   **后续样本从左侧接着铺**（不重复、不倒退）。
         *
         * 🛑 不再调用 `ecg_pipeline_restart()`：它会 `fseek(f, 0, SEEK_SET)`
         *   （`ecg_pipeline.c:538`）⇒ 5 分钟数据在 1 分钟处清屏，会把进度拨回 0，
         *   白白重看 1 分钟。要「从头看」请按「回放」键（它自己走 restart）。
         *
         * 具体动作在 `ui_clear_apply()`（与 `ui-clear` CLI 共用同一段），
         * 本回调在 LVGL 任务里，已持锁 ⇒ 直接调，不要再加锁。 */
        ui_clear_apply();
        break;

    case BTN_GAIN: {
        /* 以 ui_waveform 的当前值为准反查档位，避免与 main 里的索引不同步 */
        const float cur = ui_waveform_get_gain();
        int idx = 0;
        for (int i = 0; i < GAIN_N; i++) {
            if (k_gain_v[i] == cur) {
                idx = i;
                break;
            }
        }
        idx = (idx + 1) % GAIN_N;
        ui_waveform_set_gain(k_gain_v[idx]);
        break;
    }

    case BTN_SPEED: {
        const float cur = ui_waveform_get_speed();
        int idx = 0;
        for (int i = 0; i < SPEED_N; i++) {
            if (k_speed_v[i] == cur) {
                idx = i;
                break;
            }
        }
        idx = (idx + 1) % SPEED_N;
        ui_waveform_set_speed(k_speed_v[idx]);
        break;
    }

    case BTN_CHANNEL: {
        /* ★T209：485 源下本键已置灰（见 `ui_button_bar_refresh`）。这里再挡一道 ——
         * 通道切换的真值源在 STM32（`0x06`，且仅 STOP 态生效），屏上切画布只会
         * 造出"CH2 一条零基线"的假象。485 源请用 `485-send ch2 0|1`。 */
        if (ecg_pipeline_src_is_rs485()) {
            ESP_LOGW(TAG, "485 源：通道切换请用 485-send ch2 0|1（屏上此键已置灰）");
            break;
        }
        /* SD 回放源：切「通道 I / II」= 切换到对应帧型的数据文件。
         *   通道 I  → 单通道文件（LEN=53）
         *   通道 II → 双通道文件（LEN=103）
         * 文件由 storage_sd 在启动时扫目录按首帧 LEN 分类得到。
         * 目标类型的文件不存在就**不切**，只打日志 —— 免得切过去是一屏基线让人误判。 */
        const bool want_dual = !ui_waveform_is_dual_channel();
        const storage_sd_files_t *files = storage_sd_get_files();
        const char *target = want_dual ? files->dual : files->single;

        if (target[0] == '\0') {
            ESP_LOGW(TAG, "没有 %s 数据文件（%s），保持当前模式",
                     want_dual ? "双通道 LEN=103" : "单通道",
                     want_dual ? "如需体验请放一个 LEN=103 的 .bin" : "");
            break;
        }

        ESP_LOGI(TAG, "channel -> %s, file=%s (%u s)",
                 want_dual ? "II (dual)" : "I (single)", target,
                 (unsigned)(want_dual ? files->dual_sec : files->single_sec));

        ui_waveform_set_dual_channel(want_dual);
        ui_waveform_clear();
        ecg_pipeline_set_file(target);      /* 换文件并从头播放 */
        app_state_ro();                     /* 状态不变，仅刷新按钮 */
        break;
    }

    case BTN_REPLAY: {
        const app_play_t prev = st->play;
        if (app_action_replay()) {
            const app_state_t *ns = app_state_ro();
            if (prev == APP_PLAY_ENDED) {
                ui_waveform_clear();
                ecg_pipeline_restart();
            } else if (prev == APP_PLAY_IDLE) {
                if (ns->frozen) {
                    ui_waveform_set_frozen(false);
                }
                ecg_pipeline_restart();     /* idle 从头开始 */
            } else if (prev == APP_PLAYING) {
                ecg_pipeline_pause();
            } else if (prev == APP_PAUSED) {
                ecg_pipeline_resume();
            }
        }
        break;
    }

    default:
        break;   /* 灰显按钮不该被点到 */
    }

    ui_button_bar_refresh();
}

/* ---- 长按回放按钮：删除确认（需求 §8.1）---- */

static void on_mbox_value_changed(lv_event_t *e)
{
    lv_obj_t *mbox = lv_event_get_current_target(e);
    const char *txt = lv_msgbox_get_active_btn_text(mbox);
    if (txt != NULL && strstr(txt, "删除") != NULL) {
        const int rc = storage_sd_delete_ecg_file();
        ESP_LOGW(TAG, "delete ecg.bin -> rc=%d", rc);
        app_state_set_file_ok(false);
        ecg_pipeline_pause();
    }
    lv_msgbox_close(mbox);
    ui_button_bar_refresh();
}

static void show_delete_confirm(void)
{
    static const char *btns[] = { "取消", "删除", "" };
    lv_obj_t *mbox = lv_msgbox_create(NULL, "删除录制数据？",
                                      "ecg.bin 将被删除，不可恢复",
                                      btns, true);
    if (mbox == NULL) {
        return;
    }
    /* msgbox 内部用默认字体（Montserrat，无中文），把中文字体挂到根 obj 让子 obj 继承 */
    lv_obj_set_style_text_font(mbox, &font_zh_13, LV_PART_MAIN);
    lv_obj_add_event_cb(mbox, on_mbox_value_changed, LV_EVENT_VALUE_CHANGED, NULL);
    lv_obj_center(mbox);
}

static void on_replay_longpress(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_LONG_PRESSED) {
        return;
    }
    /* CLI 测试模式下禁用（删除确认也一并禁用） */
    if (test_mode_get() != TEST_NORMAL) {
        return;
    }
    if (!app_state_ro()->file_ok) {
        return;
    }
    ESP_LOGI(TAG, "long press on replay -> delete confirm");
    show_delete_confirm();
}

/* ---- 长按「增益」切配色（临时入口，S8 换成 BPM 浮层长按 2 s）----
 * 上电默认 **深色 B 套**（2026-10-23 拍板），所以索引从 1 开始。 */
static const ui_theme_t *k_themes[] = { &UI_THEME_LIGHT, &UI_THEME_DARK };
#define THEME_N ((int)(sizeof(k_themes) / sizeof(k_themes[0])))
static int s_theme_i = 1;      /* 1 = UI_THEME_DARK（默认） */

static void on_gain_longpress(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_LONG_PRESSED) {
        return;
    }
    /* CLI 测试模式下禁用（配色切换也一并禁用） */
    if (test_mode_get() != TEST_NORMAL) {
        return;
    }
    s_theme_i = (s_theme_i + 1) % THEME_N;
    const ui_theme_t *th = k_themes[s_theme_i];

    ui_waveform_set_theme(th);
    ui_button_bar_set_theme(th);
    ui_bpm_set_theme(th);                   /* 三处必须一起切，漏一处会出现色块 */
    /* 屏幕底色：深色画布周围不能露出浅色边距 */
    lv_obj_set_style_bg_color(lv_scr_act(), lv_color_hex(th->canvas_bg), LV_PART_MAIN);

    ESP_LOGI(TAG, "theme -> %s (long press on GAIN)", th->name);
}

/* ============================================================================
 * 对外接口
 * ========================================================================== */

esp_err_t ui_button_bar_create(lv_obj_t *scr)
{
    if (scr == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    s_theme = k_themes[s_theme_i];      /* 默认深色 B 套，见文件头 */

    /* ---- 按钮栏容器：flex 均分，内边距 4、间隙 3（需求 §3.3）---- */
    s_bar = lv_obj_create(scr);
    lv_obj_set_size(s_bar, APP_LCD_H_RES, APP_BTN_BAR_H);
    lv_obj_set_pos(s_bar, 0, APP_BTN_BAR_Y);
    lv_obj_set_flex_flow(s_bar, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(s_bar, LV_FLEX_ALIGN_SPACE_BETWEEN,
                          LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_all(s_bar, 4, LV_PART_MAIN);
    lv_obj_set_style_pad_column(s_bar, 3, LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_bar, lv_color_hex(s_theme->bar_bg), LV_PART_MAIN);
    lv_obj_set_style_border_width(s_bar, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(s_bar, 0, LV_PART_MAIN);
    lv_obj_clear_flag(s_bar, LV_OBJ_FLAG_SCROLLABLE);

    /* ---- 九个按钮：单行居中 ---- */
    for (int i = 0; i < BTN_COUNT; i++) {
        btn_t *b = &s_btn[i];

        b->btn = lv_btn_create(s_bar);
        lv_obj_set_size(b->btn, APP_BTN_W, APP_BTN_H);
        lv_obj_set_style_radius(b->btn, 3, LV_PART_MAIN);
        lv_obj_set_style_border_width(b->btn, 1, LV_PART_MAIN);
        lv_obj_set_style_pad_all(b->btn, 0, LV_PART_MAIN);

        b->label = lv_label_create(b->btn);
        lv_obj_set_style_text_font(b->label, &font_zh_13, LV_PART_MAIN);
        lv_label_set_text(b->label, "-");
        lv_obj_center(b->label);           /* 单行居中 */

        lv_obj_add_event_cb(b->btn, on_btn_click, LV_EVENT_CLICKED,
                            (void *)(uintptr_t)i);
    }

    /* 第九个：长按删除确认（需求 §8.1） */
    lv_obj_add_event_cb(s_btn[BTN_REPLAY].btn, on_replay_longpress,
                        LV_EVENT_LONG_PRESSED, NULL);

    /* 增益：长按切配色（临时入口） */
    lv_obj_add_event_cb(s_btn[BTN_GAIN].btn, on_gain_longpress,
                        LV_EVENT_LONG_PRESSED, NULL);

    ui_button_bar_refresh();

    ESP_LOGI(TAG, "button bar: %d buttons, %dx%d each, pad=4 gap=3, single-line centered",
             BTN_COUNT, APP_BTN_W, APP_BTN_H);
    return ESP_OK;
}

void ui_button_bar_refresh(void)
{
    if (s_bar == NULL) {
        return;
    }
    const app_state_t *st = app_state_ro();
    char buf[48];       /* 够放下 "<最多31字符数字>mm/mV"；用 28 会触发 -Wformat-truncation */

    /* 1. 采集（★T209）：**恒灰，文字恒为「开始」**。
     *
     * 🛑 为什么不显示「采集中」：本设计没有 STOP 键（T206 拍板⑤ START 后一直跑），
     *   要停只能复位 STM32 ⇒ 一旦写上「采集中」就再也变不回来，屏上与真实状态长期
     *   打架（这正是 T208 方案被撤掉的原因）。
     * ⇒ 采集真值请用 `485-stat`：`frames` 在涨 = STM32 在出数据（唯一可信判据，
     *   按钮文字和 `ecg_pipeline_running()` 都判不出来 —— 后者从 `485-start`
     *   起就恒 true）。 */
    lv_label_set_text(s_btn[BTN_CAPTURE].label, "开始");
    apply_btn_style(&s_btn[BTN_CAPTURE], false, false);

    /* 2. 冻结 / 继续（单行） */
    lv_label_set_text(s_btn[BTN_FREEZE].label, st->frozen ? "继续" : "冻结");
    apply_btn_style(&s_btn[BTN_FREEZE], st->frozen, st->file_ok);

    /* 3. 清屏（单行） */
    lv_label_set_text(s_btn[BTN_CLEAR].label, "清屏");
    apply_btn_style(&s_btn[BTN_CLEAR], false, st->file_ok);

    /* 4. 增益：数字 + 单位在同一行，如 5mm/mV */
    {
        char num[32];       /* 给足：%d.%d 理论上可达 23 字符，用 12 会触发 -Wformat-truncation */
        fmt_1dp(num, sizeof(num), ui_waveform_get_gain());
        snprintf(buf, sizeof(buf), "%smm/mV", num);
        lv_label_set_text(s_btn[BTN_GAIN].label, buf);
        apply_btn_style(&s_btn[BTN_GAIN], false, st->file_ok);
    }

    /* 5. 走纸：如 25mm/s */
    {
        char num[32];
        fmt_1dp(num, sizeof(num), ui_waveform_get_speed());
        snprintf(buf, sizeof(buf), "%smm/s", num);
        lv_label_set_text(s_btn[BTN_SPEED].label, buf);
        apply_btn_style(&s_btn[BTN_SPEED], false, st->file_ok);
    }

    /* 6/7/8. 模式 / 陷波 / 通道：**灰显但显示实际状态**（从 STA 解）
     *
     * STA 位定义（协议 v2.2 §5.1）：bit0 mode（0=诊断 1=监护）、
     *                              bit1 notch（0=关 1=开）、bit2 channel（0=I 1=I&II）
     * 无 STA（v1.1 旧帧）时用 0x02 作为默认：诊断 + 陷波开 + 通道 I。
     *
     * ⚠ 需求 §8.5 原本要求无 STA 时显示「未知」以免把未知伪装成已知；
     *   2026-10-23 拍板改为显示默认值 —— 因为演示数据（LEN=53/103）全部带 STA，
     *   此分支实际不会触发，且「未知」占位反而更难读。 */
    {
        const uint8_t sta = st->has_sta ? st->sta : 0x02;

        lv_label_set_text(s_btn[BTN_MODE].label,
                          (sta & ECG_STA_BIT_MODE) ? "监护" : "诊断");
        lv_label_set_text(s_btn[BTN_NOTCH].label,
                          (sta & ECG_STA_BIT_NOTCH) ? "陷波ON" : "陷波OFF");

        /* 通道按钮：**文字反映当前画布模式**（不是 STA）。
         *
         * 🛑 ★T209（11-05）修正「假使能」：
         *   T208 期间这个键在 485 源下也可点，但它只调 `ui_waveform_set_dual_channel()`
         *   **换画布、一个字节都不发给 STM32** ⇒ STM32 照旧发 LEN=53（`n_ch2=0`）
         *   ⇒ `ecg_pipeline.c:429` 的 `v2 = (k < fr.n_ch2) ? fr.ch2[k] : 0` 把 CH2 整段
         *   填 0 ⇒ **屏上第二条正好是一条贴零的水平基线**。猛哥 11-05 报的
         *   「使能通道II 后只有一条基线」就是这个，不是 STM32 没数据。
         *   （STM32 侧 CH2 ≡ CH1，`ads1293.c:1533` 直接复制 ⇒ 真发了必然两路一样。）
         * ⇒ 485 源下本键**置灰**：通道切换必须由 STM32 决定，且 `0x06` **仅 STOP 态**
         *   生效，做成可点就只能在 STOP 态点，反而更容易误判。改走
         *   `485-send ch2 0|1`（那条会在回执后把画布一起切）。
         * ⇒ SD 回放源仍可点：那里它本义是"切到 LEN=103 的数据文件"，与采集端无关。 */
        const bool dual = ui_waveform_is_dual_channel();
        const bool ch_clickable = st->file_ok && !ecg_pipeline_src_is_rs485();

        lv_label_set_text(s_btn[BTN_CHANNEL].label, dual ? "通道II" : "通道I");

        apply_btn_style(&s_btn[BTN_MODE],  false, false);
        apply_btn_style(&s_btn[BTN_NOTCH], false, false);
        apply_btn_style(&s_btn[BTN_CHANNEL], dual, ch_clickable);
    }

    /* 9. 回放四态：单行只显示动词（需求 §8.1） */
    {
        const char *txt;
        bool selected = false;

        switch (st->play) {
        case APP_PLAYING:
            txt      = "暂停";
            selected = true;
            break;
        case APP_PAUSED:
            txt      = "播放";
            selected = true;
            break;
        case APP_PLAY_ENDED:
            txt      = "重播";
            break;
        case APP_PLAY_IDLE:
        default:
            txt      = "回放";
            break;
        }

        /* 无文件时强制「回放」+ 灰显（需求 §8.1 第一行） */
        if (!st->file_ok) {
            txt      = "回放";
            selected = false;
        }

        lv_label_set_text(s_btn[BTN_REPLAY].label, txt);
        apply_btn_style(&s_btn[BTN_REPLAY], selected, st->file_ok);
    }
}

void ui_button_bar_set_theme(const ui_theme_t *theme)
{
    if (theme == NULL || s_bar == NULL) {
        return;
    }
    s_theme = theme;
    lv_obj_set_style_bg_color(s_bar, lv_color_hex(theme->bar_bg), LV_PART_MAIN);
    ui_button_bar_refresh();
}
