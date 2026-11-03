/**
 * @file ui_bpm.h
 * @brief BPM 浮层（S7）—— 右上角心率数值
 *
 * 需求 v1.2 §3.2：
 *   位置  右上，距右 10 / 距顶 8，约 160×50
 *   数字  Montserrat 30；单位 `bpm` 小字
 *   无值  显示 `--`，**不得显示 0**（0 是有效读数）
 *
 * 为什么做成浮层而不是顶栏：480 px 全给两个画布。有顶栏时画布只有 190 px，
 * 1 mV 只值 17.6 px；无顶栏 216 px ⇒ 1 mV = 20 px（需求 §3.2 的取舍说明）。
 *
 * 取值口径（需求 §5.3）：
 *   - 检测器给出有效 BPM 才显示
 *   - **超过 3 s 无新峰 ⇒ 归 `--`**（信号断了不能留着旧数字骗人）
 *   - 冻结时保持冻结时刻的值（需求 §8.3）
 */
#pragma once

#include "esp_err.h"
#include "lvgl.h"
#include "ui_theme.h"

/**
 * @brief 创建 BPM 浮层。
 * @note  必须在 ui_waveform_create() **之后**调用 —— LVGL 的后建对象在上层，
 *        浮层要盖在画布之上。
 */
esp_err_t ui_bpm_create(lv_obj_t *scr);

/** @brief 切换配色（画布 / 按钮栏 / 浮层三处必须一起切） */
void ui_bpm_set_theme(const ui_theme_t *theme);

/** @brief 立刻归 `--`（清屏时调用，不等下一个刷新周期） */
void ui_bpm_reset(void);
