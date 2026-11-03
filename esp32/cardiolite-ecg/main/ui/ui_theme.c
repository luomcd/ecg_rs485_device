/**
 * @file ui_theme.c
 * @brief 配色表实例 —— 色值见需求 v1.2 §3.3（按钮）/ §3.4（两套）
 */

#include "ui_theme.h"

/* ---------- A 套：浅色纸（默认） ---------- */
const ui_theme_t UI_THEME_LIGHT = {
    .name        = "light",

    /* 画布 */
    .canvas_bg   = 0xF5F5F5,
    .grid_minor  = 0xE2E2E2,
    .grid_major  = 0xC6C6C6,
    .baseline    = 0xA8A8A8,

    /* 波形 */
    .wave_ch1    = 0x1B5E20,   /* 导联 I：深绿 */
    .wave_ch2    = 0x0D47A1,   /* 导联 II：深蓝 */

    /* 文字与标尺 */
    .text        = 0x5B5B5B,
    .ruler       = 0x3A3A3A,

    /* R 峰标记 / BPM 浮层（§3.2 / §3.4 A 套） */
    .rpeak       = 0xC62828,
    .bpm_bg      = 0xFFFFFF,
    .bpm_border  = 0xB8C4D4,
    .bpm_text    = 0x2C2C2A,
    /* 需求 §3.2 写 0.9，实测改为不透明：浮层在画布范围内，每帧都要重绘，
     * α 混合 160×50 = 8000 px 要 2.3 ms，帧率 14.1 → 13.7（跌破 ≥14 判据）。
     * 浮层底色 #FFFFFF 与画布底 #F5F5F5 本来几乎同色，视觉无差别。 */
    .bpm_bg_opa  = 255,        /* 需求 0.9，实测改不透明，见上 */

    /* 按钮（§3.3） */
    .bar_bg          = 0xEFEEEB,
    .btn_bg          = 0xE4E3DE,
    .btn_text        = 0x2C2C2A,
    .btn_border      = 0xC2C1BB,
    .btn_sel_bg      = 0x4078BE,
    .btn_sel_text    = 0xFFFFFF,
    .btn_sel_border  = 0x285A96,
    .btn_dis_bg      = 0xEFEEEB,
    .btn_dis_text    = 0xB4B2A9,
    .warn_bg         = 0xC0392B,
};

/* ---------- B 套：深色屏 ---------- */
const ui_theme_t UI_THEME_DARK = {
    .name        = "dark",

    /* 画布 */
    .canvas_bg   = 0x0F1216,
    .grid_minor  = 0x1C222A,   /* 几乎不可见，只提供极弱的纹理 */
    .grid_major  = 0x2A323C,   /* 勉强可辨 */
    .baseline    = 0x3C4652,

    /* 波形 */
    .wave_ch1    = 0xFFFFFF,
    .wave_ch2    = 0x6FA8DC,

    /* 文字与标尺 */
    .text        = 0x8A949E,
    .ruler       = 0x9FA8B2,

    /* R 峰标记 / BPM 浮层（§3.4 B 套） */
    .rpeak       = 0xFF5252,
    .bpm_bg      = 0x0F1318,
    .bpm_border  = 0x39424E,   /* 需求 B 套未给边框色，取按钮边框同值 */
    .bpm_text    = 0xF2F6FA,
    /* 需求 §3.4 写 0.94，同 A 套理由改不透明（#0F1318 vs 画布 #0F1216 几乎同色） */
    .bpm_bg_opa  = 255,

    /* 按钮（§3.4 的 B 套） */
    .bar_bg          = 0x151A20,
    .btn_bg          = 0x242B34,
    .btn_text        = 0xD8DEE6,
    .btn_border      = 0x39424E,
    .btn_sel_bg      = 0x2F6FB5,
    .btn_sel_text    = 0xFFFFFF,
    .btn_sel_border  = 0x4A8AD0,
    /* 深色的灰显需求未给，按"更暗的底 + 更灰的字"推断 */
    .btn_dis_bg      = 0x1A1F26,
    .btn_dis_text    = 0x4A525C,
    .warn_bg         = 0xB3352A,
};
