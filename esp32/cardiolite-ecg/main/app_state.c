/**
 * @file app_state.c
 * @brief 全局状态机实现（S6）
 */

#include "app_state.h"

#include <string.h>

#include "esp_log.h"

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
    /* 清屏语义（需求 §8.4）：波形缓冲由调用方清；这里把回放位置归零。
     * 冻结状态下清屏 = 解冻后从空白开始，所以顺手解冻。 */
    s_st.frozen = false;
    s_st.play   = APP_PLAY_IDLE;
    ESP_LOGI(TAG, "clear -> idle, unfrozen");
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
