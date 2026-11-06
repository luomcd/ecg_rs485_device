/**
 * @file app_state.c
 * @brief 全局状态机实现（S6）
 */

#include "app_state.h"

#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"      /* ★T213：STA 帧龄时基 */

static const char *TAG = "app_state";

static app_state_t s_st;

void app_state_init(bool file_ok, uint32_t file_sec)
{
    memset(&s_st, 0, sizeof(s_st));
    s_st.play     = APP_PLAY_IDLE;
    s_st.frozen   = false;
    s_st.file_ok  = file_ok;
    s_st.file_sec = file_sec;
    s_st.sta      = 0xFF;
    s_st.has_sta  = false;

    ESP_LOGI(TAG, "init: file_ok=%d, file_sec=%u", (int)file_ok, (unsigned)file_sec);
}

const app_state_t *app_state_ro(void)
{
    return &s_st;
}

bool app_action_freeze(void)
{
    /* 无文件时冻结无意义 */
    if (!s_st.file_ok) {
        return false;
    }
    s_st.frozen = !s_st.frozen;
    ESP_LOGI(TAG, "freeze -> %d", (int)s_st.frozen);
    return true;
}

bool app_action_clear(void)
{
    if (!s_st.file_ok) {
        return false;
    }
    /* ★T212（2026-11-06 猛哥拍板方案 B）：**清屏只清视觉，不改播放状态、不归零位置**。
     *
     * 🛑 旧版这里写 `s_st.play = APP_PLAY_IDLE`（配合「清屏 = 回放位置归零」的
     *   §8.4 旧口径）。它与「清屏不改变播放状态」**自相矛盾**，因为按钮文字由
     *   `play` 驱动（`ui_button_bar.c:505`）：
     *     ① 播放中点清屏 ⇒ 按钮从「暂停」跳成「回放」，而管线其实还在跑；
     *     ② 第二次点清屏时 `prev_play` 已残留为 IDLE ⇒ 走「非播放」分支补 pause
     *        ⇒ **同一个键，第一次是重播、第二次是暂停**（11-06 实测就是这个）。
     * ⇒ 现在 `play` 一律不动，按钮文字恒等于真实状态。
     *
     * §8.4 的「回放位置归零」已随本改动撤销（改为「清屏不倒退进度」）；
     *   要重新从头看，按「回放」键 —— 它自己会 restart（`app_state.c:58`）。
     * 冻结仍按旧口径解除：清屏后从空白开始画，不该还停在冻结。 */
    s_st.frozen = false;
    ESP_LOGI(TAG, "clear -> visual only, play=%d kept", (int)s_st.play);
    return true;
}

bool app_action_replay(void)
{
    if (!s_st.file_ok) {
        return false;
    }

    switch (s_st.play) {
    case APP_PLAY_IDLE:
    case APP_PLAY_ENDED:
        s_st.play   = APP_PLAYING;
        s_st.frozen = false;          /* 重新播放时解冻 */
        ESP_LOGI(TAG, "replay: %s -> playing",
                 (s_st.play == APP_PLAY_ENDED) ? "ended" : "idle");
        break;
    case APP_PLAYING:
        s_st.play = APP_PAUSED;
        ESP_LOGI(TAG, "replay: playing -> paused");
        break;
    case APP_PAUSED:
        s_st.play = APP_PLAYING;
        ESP_LOGI(TAG, "replay: paused -> playing");
        break;
    default:
        return false;
    }
    return true;
}

bool app_set_ended(void)
{
    if (s_st.play != APP_PLAYING) {
        return false;
    }
    s_st.play = APP_PLAY_ENDED;
    ESP_LOGI(TAG, "play -> ended (EOF)");
    return true;
}

void app_state_set_sta(uint8_t sta, bool has_sta)
{
    s_st.sta     = has_sta ? sta : 0xFF;
    s_st.has_sta = has_sta;
    /* ★T213：记下写入时刻，CLI 据此算帧龄（`485-stat` 的 STATUS 行）。
     * 放在这里而不是调用方：所有写入路径都必须盖时间戳，漏一处就又回到
     * 「停流后看不出陈旧」的老问题。 */
    s_st.sta_ms  = (uint32_t)(esp_timer_get_time() / 1000);
}

void app_state_set_file_ok(bool ok)
{
    s_st.file_ok = ok;
    if (!ok) {
        /* 文件没了，回放与冻结都无意义 */
        s_st.play   = APP_PLAY_IDLE;
        s_st.frozen = false;
    }
    ESP_LOGI(TAG, "file_ok -> %d", (int)ok);
}
