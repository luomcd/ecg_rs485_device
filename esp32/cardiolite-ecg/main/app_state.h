/**
 * @file app_state.h
 * @brief 全局界面状态机（S6）
 *
 * 状态与语义严格按需求 v1.2 §8：
 *   - 回放四态：idle / playing / paused / ended（§8.1）
 *   - **冻结是叠加标志，不是独立状态**：只停绘制，不停数据管线（§8.3）
 *   - 清屏 = 清波形缓冲 + R 峰队列 + BPM 归 -- + 重画网格 + 回放位置归零（§8.4）
 *
 * 第一阶段明确不做（需求 §9）：「采集」「记录」无实时数据源，
 * 采集按钮恒灰，第九个按钮只跑回放状态机。
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    APP_PLAY_IDLE = 0,   /* 有文件、未开始 */
    APP_PLAYING,         /* 回放中 */
    APP_PAUSED,          /* 暂停 */
    APP_PLAY_ENDED,      /* 播完 */
} app_play_t;

typedef struct {
    app_play_t play;
    bool       frozen;      /* 冻结叠加标志 */
    bool       file_ok;     /* SD 上有 ecg.bin */
    uint32_t   file_sec;    /* 文件时长（秒），显示在回放按钮副行 */

    /* 最近一帧的 STA（0xFF = 未知，用于「未知」而非「诊断/关/I」，见需求 §8.5） */
    uint8_t    sta;
    bool       has_sta;
} app_state_t;

/** @brief 初始化（在 SD 探测完成后调用） */
void app_state_init(bool file_ok, uint32_t file_sec);

/** @brief 只读访问 */
const app_state_t *app_state_ro(void);

/**
 * @brief 动作：冻结 ↔ 解冻。
 * @return 状态是否有变化（UI 需要刷新）
 */
bool app_action_freeze(void);

/**
 * @brief 动作：清屏（只清视觉：波形缓冲 / R 峰队列 / BPM）。
 * @note  实际清缓冲由调用方调 ui_waveform_clear()；本函数只改状态。
 * @note  ★T212（2026-11-06 方案 B）：**不改 `play`、不归零回放位置**。
 *        旧版把 `play` 置 IDLE，导致按钮文字与实际播放状态分家。
 */
bool app_action_clear(void);

/**
 * @brief 动作：第九个按钮（回放/暂停/播放/重播）。
 *   idle    → playing（从头开始）
 *   playing → paused
 *   paused  → playing
 *   ended   → playing（清缓冲后从头）
 * @return 状态是否有变化
 */
bool app_action_replay(void);

/** @brief 由 UI 定时器检测管线已停（EOF）后置为 ended */
bool app_set_ended(void);

/** @brief 更新 STA（每帧解析后可调用，用于模式/陷波/通道按钮的显示） */
void app_state_set_sta(uint8_t sta, bool has_sta);

/** @brief 更新「SD 是否有文件」（删除 ecg.bin 后置 false） */
void app_state_set_file_ok(bool ok);
