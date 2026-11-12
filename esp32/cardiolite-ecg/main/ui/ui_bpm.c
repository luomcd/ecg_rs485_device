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
#include "app_state.h"
#include "ecg_pipeline.h"
#include "ui_waveform.h"

static const char *TAG = "ui_bpm";

/* ★T232-C2：导联状态图标（定义在文件后部，tick 里要用 ⇒ 前置声明） */
static void ico_sync(void);

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

    /* ★T232-C2：导联图标**先于冻结判断**同步 —— 脱落是事实，冻结只是画面停更，
     *   冻结期间仍然必须能看到"导联掉了"。 */
    ico_sync();

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

/* ============================================================================
 * ★T232-C2（2026-11-11）：BPM 左侧的「导联线状态」图标
 *
 * 需求（猛哥 18:05）：**导联正常 = 导联线图标；脱落 = 图标打红叉**。
 *
 * 为什么用 LVGL 图元画、而不是用符号/图片：
 *   · 本项目**从未启用 LV_SYMBOL_***（全工程零引用），字体不一定带符号；
 *   · 用图片要加 RGB565 素材 + 解码，对 34×34 的小图标不划算；
 *   · 图元绘制的开销与 label 同级（静态图形，只在状态变化时改颜色/显隐）。
 *
 * 状态来源：`app_state_ro()->sta` 的 **bit4**（= 任意输入脱落，T232 §13.1）。
 *   未收到 STA（has_sta=false）时画成暗灰 —— 表示"不知道"，不要伪装成正常。
 *
 * 位置：BPM 盒 160×50，数字右对齐在 -58、单位在 -14 ⇒ 左侧 ~66 px 是空的，
 *       图标占 x=6..40，不会压到读数。
 * ========================================================================== */
static lv_obj_t *s_ico_wire;    /* 导联线（折线） */
static lv_obj_t *s_ico_dot;     /* 电极（小圆点） */
static lv_obj_t *s_ico_x1;      /* 红叉 ① */
static lv_obj_t *s_ico_x2;      /* 红叉 ② */
static bool      s_ico_lod;     /* 当前是否显示"脱落"态 */
static bool      s_ico_valid;   /* 是否已收到过 STA */

/* ⚠ 必须是静态生命周期：lv_line 只保存指针，不拷贝点数组 */
static lv_point_t PTS_WIRE[] = {{2, 26}, {10, 26}, {10, 8}, {22, 8}, {22, 18}, {30, 18}};

/* 叉的大小（猛哥 18:10：原来的 3..31 太大，把整条导联线全盖住了）。
 * 现值 14×14，中点 (16,17) = 导联线包围盒中心，线宽 3→2；
 * 缩到线的 1/2 后，叉只压住中段，两端折线仍露在外面 ⇒ 一眼能看出"是导联掉了"。 */
static lv_point_t PTS_X1[]   = {{9, 10}, {23, 24}};
static lv_point_t PTS_X2[]   = {{23, 10}, {9, 24}};

#define ICO_C_NORMAL   0xC8CDD2u   /* 亮灰：正常 */
#define ICO_C_LOD      0xFF5252u   /* 红：脱落 */
#define ICO_C_DIM      0x5A5F63u   /* 暗灰：还没有 STA（状态未知） */

static void ico_apply(void)
{
    const uint32_t c = (!s_ico_valid) ? ICO_C_DIM : (s_ico_lod ? ICO_C_LOD : ICO_C_NORMAL);

    lv_obj_set_style_line_color(s_ico_wire, lv_color_hex(c), LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_ico_dot,    lv_color_hex(c), LV_PART_MAIN);

    if (s_ico_valid && s_ico_lod) {
        lv_obj_clear_flag(s_ico_x1, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_ico_x2, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_ico_x1, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_ico_x2, LV_OBJ_FLAG_HIDDEN);
    }
}

static void ico_create(lv_obj_t *parent)
{
    s_ico_wire = lv_line_create(parent);
    lv_line_set_points(s_ico_wire, PTS_WIRE, (uint16_t)(sizeof(PTS_WIRE) / sizeof(PTS_WIRE[0])));
    lv_obj_set_style_line_width(s_ico_wire, 2, LV_PART_MAIN);
    lv_obj_set_pos(s_ico_wire, 4, 4);

    s_ico_dot = lv_obj_create(parent);
    lv_obj_set_size(s_ico_dot, 6, 6);
    lv_obj_set_style_radius(s_ico_dot, 3, LV_PART_MAIN);   /* 圆点 */
    lv_obj_set_style_border_width(s_ico_dot, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_ico_dot, 0, LV_PART_MAIN);
    lv_obj_clear_flag(s_ico_dot, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_clear_flag(s_ico_dot, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(s_ico_dot, 27, 19);

    s_ico_x1 = lv_line_create(parent);
    lv_line_set_points(s_ico_x1, PTS_X1, 2);
    lv_obj_set_style_line_width(s_ico_x1, 2, LV_PART_MAIN);
    lv_obj_set_style_line_color(s_ico_x1, lv_color_hex(ICO_C_LOD), LV_PART_MAIN);
    lv_obj_set_pos(s_ico_x1, 4, 4);

    s_ico_x2 = lv_line_create(parent);
    lv_line_set_points(s_ico_x2, PTS_X2, 2);
    lv_obj_set_style_line_width(s_ico_x2, 2, LV_PART_MAIN);
    lv_obj_set_style_line_color(s_ico_x2, lv_color_hex(ICO_C_LOD), LV_PART_MAIN);
    lv_obj_set_pos(s_ico_x2, 4, 4);

    s_ico_lod = false;
    s_ico_valid = false;
    ico_apply();
}

/** 每 tick 同步一次图标状态（只有变化才动样式 ⇒ 零成本） */
static void ico_sync(void)
{
    const app_state_t *st = app_state_ro();
    const bool valid = (st != NULL) && st->has_sta;
    const bool lod   = valid && ((st->sta & 0x10u) != 0u);   /* bit4 = 任意输入脱落 */

    if (valid == s_ico_valid && lod == s_ico_lod) {
        return;
    }
    s_ico_valid = valid;
    s_ico_lod   = lod;
    ico_apply();
    ESP_LOGI(TAG, "lead icon -> %s", !valid ? "unknown" : (lod ? "LEAD-OFF" : "ok"));
}

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
    apply_theme(&UI_THEME_DARK);

    ico_create(s_box);                      /* ★T232-C2：导联状态图标（在最左） */            /* 与画布/按钮栏的默认一致，见 ui_waveform.c */

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
