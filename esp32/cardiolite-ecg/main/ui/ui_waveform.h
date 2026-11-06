/**
 * @file ui_waveform.h
 * @brief 双画布心电图绘制（S4）
 *
 * 布局（需求 v1.2 §3.1）：
 *   画布 1  y = 0   ~ 215
 *   画布 2  y = 216 ~ 431
 *   可绘制区 752 × 200，居中于 800 × 216 的画布内（上下各留 8 px、左右各留 24 px）
 *
 * 绘制策略（需求 §4.5 的接口优先级 ①）：
 *   每帧全量重绘「底色 + 网格 + 基线 + 标尺 + 角标 + 波形」到 RGB565 缓冲，再 invalidate。
 *   直接写缓冲指针，**不走 lv_canvas_set_px()** —— 后者每像素一次函数调用 + 边界检查，
 *   2500 个波形点 + 175 条网格线会慢一个量级。
 *   若实测帧率 < 15 FPS，再退到优先级 ②（脏矩形 / 网格层单独缓存）。
 *
 * 中文文案：S4 阶段**尚无中文字库**（S6 才生成），角标与覆盖文案暂用英文，
 *   字库到位后替换为需求原定的中文。
 */
#pragma once

#include "esp_err.h"
#include "lvgl.h"
#include "ui_theme.h"

/**
 * @brief 在屏幕上创建两个画布并启动 20 FPS 刷新定时器。
 *
 * ⚠ 调用前必须已 `ecg_pipeline_detach_consumer()` —— 绘制回调会接管环缓消费。
 */
esp_err_t ui_waveform_create(lv_obj_t *scr);

/** @brief 切换配色（S8 用；S4 只有浅色） */
void ui_waveform_set_theme(const ui_theme_t *theme);

/** @brief 设置增益（mV/LSB 倍数：2.5 / 5 / 10 / 20），S5 用 */
void ui_waveform_set_gain(float gain);

/** @brief 设置走纸（mm/s：12.5 / 25 / 50），S5 用 */
void ui_waveform_set_speed(float mm_per_s);

/** @brief 当前走纸（mm/s） */
float ui_waveform_get_speed(void);

/** @brief 当前增益（mV/LSB 倍数） */
float ui_waveform_get_gain(void);

/** @brief 实测帧率 × 10（如 187 表示 18.7 FPS） */
uint32_t ui_waveform_fps_x10(void);

/** @brief 最近一次重绘耗时（微秒） */
uint32_t ui_waveform_render_us(void);

/**
 * @brief 冻结 / 解冻。
 *
 * 语义（需求 §8.3）：**冻结只停绘制，不停数据管线** ——
 * 冻结期间样本继续进环形缓冲、继续被取进显示缓冲，只是不重绘画布。
 * 这样解冻后立刻显示的是**最新**数据，不是冻结时刻的旧数据。
 */
void ui_waveform_set_frozen(bool frozen);

/** @brief 清波形缓冲（需求 §8.4 清屏语义的一部分） */
void ui_waveform_clear(void);

/** @brief 当前是否冻结 */
bool ui_waveform_is_frozen(void);

/**
 * @brief ★T216：R 峰打点显示开关（默认开，CLI `rpeak on|off`）。
 *
 * 语义：**只停绘制，不停检测** —— 关掉后 `sync_rpeaks()` 照收、BPM 照算，
 * 只是不在波形上画圆点。重新打开时只画**当前可见窗口内**的峰，不会补画历史点。
 * @note 内部会失效画布并立即重绘一帧；调用方需持有 LVGL 锁。
 */
void ui_waveform_set_rp_show(bool on);

/** @brief 当前是否画 R 峰标记 */
bool ui_waveform_get_rp_show(void);

/**
 * @brief 双通道显示模式开关（画布 2 从"无数据"改为画 CH2）。
 *
 * ⚠ 需配合 LEN=103 的数据文件（如 test-dualch.bin）才看得出效果；
 *   若当前文件是单通道（LEN=53），CH2 恒为 0，画布 2 是一条基线。
 *
 * 切换时会清空两路显示缓冲 —— 因为切换前的 CH1 样本没有配对的 CH2，
 * 留着会让两路时间轴错位。
 */
void ui_waveform_set_dual_channel(bool dual);

/** @brief 当前是否双通道显示模式 */
bool ui_waveform_is_dual_channel(void);
