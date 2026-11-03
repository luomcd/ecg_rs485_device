/**
 * @file ui_button_bar.h
 * @brief 九按钮栏 + 状态刷新（S6）
 *
 * 布局（需求 v1.2 §3.3）：y = 432，高 48，内边距 4、间隙 3、按钮 85×40（flex 均分）。
 * 每个按钮两行：主行 13 px（动态）、副行 10 px（固定），均用中文子集字库。
 *
 * 灰显规则（需求 §8.2 / §8.5，第一阶段全部处于"回放"语境）：
 *   - 采集：**恒灰**（无实时数据源）
 *   - 模式 / 陷波 / 通道：**恒灰**（三参数属采集端命令 0x04/0x05/0x06，回放无采集端可改）
 *   - 回放/记录：SD 无文件时灰
 *   - 冻结 / 清屏 / 增益 / 走纸：有文件即可点
 */
#pragma once

#include "esp_err.h"
#include "lvgl.h"
#include "ui_theme.h"

typedef enum {
    BTN_CAPTURE = 0,   /* 采集（恒灰） */
    BTN_FREEZE,        /* 冻结 / 继续 */
    BTN_CLEAR,         /* 清屏 */
    BTN_GAIN,          /* 增益循环 */
    BTN_SPEED,         /* 走纸循环 */
    BTN_MODE,          /* 模式（恒灰） */
    BTN_NOTCH,         /* 陷波（恒灰） */
    BTN_CHANNEL,       /* 通道（恒灰） */
    BTN_REPLAY,        /* 回放 / 暂停 / 播放 / 重播 */
    BTN_COUNT
} btn_id_t;

/** @brief 创建九按钮栏（在 scr 上） */
esp_err_t ui_button_bar_create(lv_obj_t *scr);

/**
 * @brief 按当前 app_state 刷新所有按钮的文字、灰显、选中态。
 *
 * 状态变化后必须调一次。调用方需持有 LVGL 锁。
 */
void ui_button_bar_refresh(void);

/** @brief 切换配色（S8 用；S6 只切浅色/深色两套） */
void ui_button_bar_set_theme(const ui_theme_t *theme);
