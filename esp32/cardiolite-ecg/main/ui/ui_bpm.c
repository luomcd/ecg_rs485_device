/**
 * @file ui_bpm.c
 * @brief BPM 浮层实现（S7）
 *
 * 【为什么不在画布上画数字】
 * 与角标同理（见 ui_waveform.c 文件头第 2 条）：canvas 上的文字每次重绘都要
 * 查字库、渲染字形、做 alpha 混合；而 label 只在文本变化时重绘。
 * 本模块只在**文本变化时**才 set_text，14 FPS 下绝大多数帧什么都不做。
 */

#include "ui_bpm.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"

#include "app_config.h"
#include "ecg_pipeline.h"
#include "ui_waveform.h"

static const char *TAG = "ui_bpm";

static lv_obj_t         *s_box;
static lv_obj_t         *s_lbl_val;
static lv_obj_t         *s_lbl_unit;
static char              s_shown[16];       /* 当前显示的文本（判重用） */

/* ============================================================================
 * 配色
 * ========================================================================== */

static void apply_theme(const ui_theme_t *t)
{
    lv_obj_set_style_bg_color(s_box, lv_color_hex(t->bpm_bg), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_box, (lv_opa_t)t->bpm_bg_opa, LV_PART_MAIN);
    lv_obj_set_style_border_color(s_box, lv_color_hex(t->bpm_border), LV_PART_MAIN);

    lv_obj_set_style_text_color(s_lbl_val,  lv_color_hex(t->bpm_text), LV_PART_MAIN);
    lv_obj_set_style_text_color(s_lbl_unit, lv_color_hex(t->bpm_text), LV_PART_MAIN);
}

/* ============================================================================
 * 文本更新（只在变化时才动 label）
 * ========================================================================== */

static void set_text(const char *txt)
{
    if (strcmp(s_shown, txt) == 0) {
        return;                             /* 没变就不刷，省一次字形重绘 */
    }
    snprintf(s_shown, sizeof(s_shown), "%s", txt);
    lv_label_set_text(s_lbl_val, s_shown);
    /* 只在文本变化时打一行 —— 40 s 也就十几行，出问题时能直接看出
     * "什么时候从 -- 变成 60"（稳定窗是否按预期开门） */
    ESP_LOGI(TAG, "bpm -> %s", s_shown);
}

/**
 * 把心率快照翻译成屏上文本。
 *
 * 判定顺序（需求 §5.3 + §8.3 + 方案 D）：
 *   ① 冻结   ⇒ 保持冻结时刻的值，**本函数根本不该被调用**
 *   ② 稳定窗未满（本 epoch 已喂 < 3250 样本）⇒ `--`
 *      原因：参考实现的阈值跟踪要 6.15 s 才收敛，之前检出的是噪声峰
 *   ③ bpm=0  ⇒ `--`（检测器还没凑够 RR）
 *   ④ 3 s 无新峰 ⇒ `--`（信号断了）
 *   ⑤ 其余   ⇒ 四舍五入显示
 *
 * ⚠ 第 ④ 条在**暂停时不判**：暂停是用户主动行为，不是信号丢失，
 *   和"冻结保持数值"同理。否则一暂停 3 s 数字就自己变 `--`，看着像故障。
 */
static const char *resolve_text(const ecg_pipeline_hr_t *hr, char *buf, size_t n)
{
    bool valid = (hr->fed >= APP_BPM_SETTLE_SAMPLES) && (hr->bpm > 0.0f);

    if (valid && !ecg_pipeline_is_paused()) {
        const int64_t idle_us = esp_timer_get_time() - hr->last_peak_us;
        if (hr->last_peak_us <= 0 ||
            idle_us > (int64_t)APP_BPM_IDLE_TIMEOUT_MS * 1000) {
            valid = false;                  /* 需求 §5.3：3 s 无新峰归 -- */
        }
    }

    if (!valid) {
        snprintf(buf, n, "--");
    } else {
        snprintf(buf, n, "%d", (int)(hr->bpm + 0.5f));
    }
    return buf;
}

static void on_bpm_tick(lv_timer_t *t)
{
    (void)t;

    /* 冻结：保持冻结时刻的值（需求 §8.3「冻结期间 BPM 保持冻结时刻的值」） */
    if (ui_waveform_is_frozen()) {
        return;
    }

    ecg_pipeline_hr_t hr;
    if (!ecg_pipeline_get_hr(&hr)) {
        return;                             /* 读到写撕裂，下一个周期再来 */
    }

    char buf[16];
    set_text(resolve_text(&hr, buf, sizeof(buf)));
}

/* ============================================================================
 * 对外接口
 * ========================================================================== */

esp_err_t ui_bpm_create(lv_obj_t *scr)
{
    if (scr == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    s_box = lv_obj_create(scr);
    if (s_box == NULL) {
        return ESP_ERR_NO_MEM;
    }
    lv_obj_set_size(s_box, APP_BPM_W, APP_BPM_H);
    /* 右下、紧贴按钮栏上方（用绝对坐标，与 app_config.h 的 APP_BPM_Y 同一真源） */
    lv_obj_set_pos(s_box, APP_LCD_H_RES - APP_BPM_MARGIN_R - APP_BPM_W, APP_BPM_Y);
    lv_obj_set_style_radius(s_box, 5, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_box, 1, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_box, 0, LV_PART_MAIN);
    lv_obj_clear_flag(s_box, LV_OBJ_FLAG_SCROLLABLE);
    /* 浮层不接收触摸：它的作用只是显示。S8 会把「长按 BPM 浮层 2 s 切配色」
     * 挂到这里，届时再放开 CLICKABLE。 */
    lv_obj_clear_flag(s_box, LV_OBJ_FLAG_CLICKABLE);

    /* 数字：Montserrat 30（需求 §3.2）。纯数字，不进中文字库。
     * 右对齐到固定位置 —— 位数变化时右侧不动，读数不会左右跳。 */
    s_lbl_val = lv_label_create(s_box);
    lv_obj_set_style_text_font(s_lbl_val, &lv_font_montserrat_30, LV_PART_MAIN);
    lv_label_set_text(s_lbl_val, "--");
    lv_obj_align(s_lbl_val, LV_ALIGN_RIGHT_MID, -58, 0);

    /* 单位：需求写 11 px，但**工程里没有启用 Montserrat 11**（只有 12/14/16/20/24/30）。
     * 为 1 px 去改 sdkconfig 会触发 ccache 全 miss（40~50 min 全量重编），
     * 所以用最接近的 12 px。已在 S7 报告登记为已知偏差。 */
    s_lbl_unit = lv_label_create(s_box);
    lv_obj_set_style_text_font(s_lbl_unit, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_label_set_text(s_lbl_unit, "bpm");
    lv_obj_align(s_lbl_unit, LV_ALIGN_RIGHT_MID, -14, 8);

    snprintf(s_shown, sizeof(s_shown), "%s", "--");
    apply_theme(&UI_THEME_DARK);            /* 与画布/按钮栏的默认一致，见 ui_waveform.c */

    lv_timer_create(on_bpm_tick, APP_BPM_UPDATE_MS, NULL);

    ESP_LOGI(TAG, "bpm overlay ready: %dx%d @ bottom-right(x=%d,y=%d), 按钮栏顶=%d, font 30/12, tick %d ms",
             APP_BPM_W, APP_BPM_H, APP_LCD_H_RES - APP_BPM_MARGIN_R - APP_BPM_W, APP_BPM_Y,
             APP_BTN_BAR_Y, APP_BPM_UPDATE_MS);
    return ESP_OK;
}

void ui_bpm_set_theme(const ui_theme_t *theme)
{
    if (theme == NULL || s_box == NULL) {
        return;
    }
    apply_theme(theme);
}

void ui_bpm_reset(void)
{
    if (s_box == NULL) {
        return;
    }
    snprintf(s_shown, sizeof(s_shown), "%s", "--");
    lv_label_set_text(s_lbl_val, "--");
}
