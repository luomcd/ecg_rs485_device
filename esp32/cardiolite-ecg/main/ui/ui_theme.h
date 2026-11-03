/**
 * @file ui_theme.h
 * @brief 配色表（S4 画布 / S6 按钮 / S8 两套切换）
 *
 * 规矩：**绘制代码禁止内联颜色字面量**。所有颜色从这里取。
 * 理由：S8 要在两套配色间切换，内联的字面量届时无处替换。
 *
 * 色值来源：需求 v1.2 §3.3（按钮四态）+ §3.4（两套配色）。
 * 用 uint32_t 存 0xRRGGBB，使用方初始化时转成 RGB565 缓存，
 * 避免每帧对每个像素都调一次 lv_color_hex()。
 */
#pragma once

#include <stdint.h>

typedef struct {
    const char *name;

    /* ---- 画布 ---- */
    uint32_t canvas_bg;
    uint32_t grid_minor;
    uint32_t grid_major;
    uint32_t baseline;

    /* ---- 波形 ---- */
    uint32_t wave_ch1;
    uint32_t wave_ch2;

    /* ---- 文字与标尺 ---- */
    uint32_t text;
    uint32_t ruler;

    /* ---- R 峰标记 / BPM 浮层（需求 §3.2 / §3.4）---- */
    uint32_t rpeak;           /* R 峰标记圆点 */
    uint32_t bpm_bg;          /* BPM 浮层底板 */
    uint32_t bpm_border;
    uint32_t bpm_text;
    uint8_t  bpm_bg_opa;      /* 0~255；需求给的是 0.9（浅色）/ 0.94（深色）的 alpha */

    /* ---- 按钮栏（需求 §3.3 / §3.4）---- */
    uint32_t bar_bg;          /* 按钮栏底色 */

    uint32_t btn_bg;          /* 未选中 */
    uint32_t btn_text;
    uint32_t btn_border;

    uint32_t btn_sel_bg;      /* 选中态 */
    uint32_t btn_sel_text;
    uint32_t btn_sel_border;

    uint32_t btn_dis_bg;      /* 灰显 */
    uint32_t btn_dis_text;

    uint32_t warn_bg;         /* 运行 / 警示 */
} ui_theme_t;

/** A 套：浅色纸（默认）。心电图机形态，网格是读数参照。 */
extern const ui_theme_t UI_THEME_LIGHT;

/**
 * B 套：深色屏（黑底白波）。监护仪形态，波形对比度最高。
 *
 * ⚠ 深色的网格**不是简单反色**（需求 §3.4 的红色警告）：
 *   浅色下网格是"略暗于背景"的辅助线；深色若照搬，网格会因亮度差过大而抢波形重心。
 *   所以小格压到几乎不可见（#1C222A）、大格勉强可辨（#2A323C），只保留大格与基线参与读数。
 *
 * ⚠ 深色的「灰显」色需求 §3.4 未给，本文按"比按钮底色更暗、字更灰"推断，
 *   实测观感不对再调（已在 S6 报告登记）。
 */
extern const ui_theme_t UI_THEME_DARK;
