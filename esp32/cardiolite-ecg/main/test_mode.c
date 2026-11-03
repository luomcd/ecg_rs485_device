/**
 * @file test_mode.c
 * @brief CLI 测试模式状态机实现 + SD 标记文件读写
 */

#include "test_mode.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"

static const char *TAG = "test_mode";

/* 批处理标记文件（S2 解法 A）：内容 = 清单路径，如 "batch_mit.txt" */
#define TEST_BATCH_FLAG "/sdcard/batch_pending.flag"

/* 清单路径最大长度（含 '\0'），"batch_mit.txt" / "batch_ladder.txt" 都远小于此 */
#define TEST_BATCH_MANIFEST_MAX 64

/* ---- 三态状态机 ---- */
static test_mode_t s_mode = TEST_NORMAL;

test_mode_t test_mode_get(void)
{
    return s_mode;
}

void test_mode_set(test_mode_t m)
{
    if (s_mode == m) {
        return;
    }
    const test_mode_t prev = s_mode;
    s_mode = m;
    ESP_LOGI(TAG, "mode %s -> %s", test_mode_name(prev), test_mode_name(m));
}

const char *test_mode_name(test_mode_t m)
{
    switch (m) {
    case TEST_MIT:
        return "MIT";
    case TEST_LADDER:
        return "LADDER";
    case TEST_NORMAL:
    default:
        return "NORMAL";
    }
}

/* ---- SD 标记文件读写（S2 用；S1 编译进固件但尚未有命令触发）---- */

void test_mode_set_batch(const char *manifest)
{
    if (manifest == NULL || manifest[0] == '\0') {
        ESP_LOGE(TAG, "set_batch: 清单路径为空");
        return;
    }

    FILE *f = fopen(TEST_BATCH_FLAG, "w");
    if (f == NULL) {
        ESP_LOGE(TAG, "set_batch: 打开 %s 失败（SD 未挂载？）", TEST_BATCH_FLAG);
        return;
    }
    /* 写清单路径 + 换行（避免部分写入时内容不完整，虽无 CRC 但足够用） */
    fprintf(f, "%s\n", manifest);
    fclose(f);
    ESP_LOGI(TAG, "已登记批处理: %s", manifest);
}

bool test_mode_batch_pending(void)
{
    FILE *f = fopen(TEST_BATCH_FLAG, "r");
    if (f == NULL) {
        return false;
    }
    /* 只要文件存在且能打开，就算有待跑项；内容读空也返回 false */
    char buf[TEST_BATCH_MANIFEST_MAX];
    const bool ok = (fgets(buf, sizeof(buf), f) != NULL && buf[0] != '\0' && buf[0] != '\n');
    fclose(f);
    return ok;
}

const char *test_mode_get_batch(void)
{
    static char s_manifest[TEST_BATCH_MANIFEST_MAX];

    FILE *f = fopen(TEST_BATCH_FLAG, "r");
    if (f == NULL) {
        return NULL;
    }
    if (fgets(s_manifest, sizeof(s_manifest), f) == NULL) {
        fclose(f);
        return NULL;
    }
    fclose(f);

    /* 去掉尾部换行与空白 */
    size_t len = strlen(s_manifest);
    while (len > 0 && (s_manifest[len - 1] == '\n' || s_manifest[len - 1] == '\r' ||
                       s_manifest[len - 1] == ' ')) {
        s_manifest[--len] = '\0';
    }
    if (len == 0) {
        return NULL;
    }
    return s_manifest;
}

void test_mode_clear_batch(void)
{
    if (remove(TEST_BATCH_FLAG) == 0) {
        ESP_LOGI(TAG, "已清除批处理标记 %s", TEST_BATCH_FLAG);
    } else {
        ESP_LOGW(TAG, "清除批处理标记失败（可能本就不存在）");
    }
}
