/**
 * @file ecg_batch.c
 * @brief R 峰检测器离线批处理（CLI 测试模式：SD 标记文件触发，全速跑）
 *
 * 设计要点：
 *   · **全速跑**：不按 500 Hz 节拍，读一帧喂一帧，能跑多快跑多快
 *     （SD 读 + 检测器算力决定，30 min 的 MIT 记录约几秒）
 *   · **不占大缓冲**：逐帧读，帧缓冲在静态区（约 460 B）
 *   · **峰索引边出边打**，不在 RAM 里攒（MIT 30 min 有 2000+ 拍）
 *   · 每读完一个文件 `reset` 检测器，保证文件之间不串状态
 *   · 清单不存在就返回 ESP_ERR_NOT_FOUND，`main.c` 据此静默跳过
 */

#include "ecg_batch.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"

#include "app_config.h"
#include "ecg_frame.h"
#include "ecg_rpeak.h"

static const char *TAG = "ecg_batch";

/* 帧缓冲放静态区：ECG_HDR_LEN + LEN 上限 + CRC */
static uint8_t    s_raw[ECG_HDR_LEN + ECG_LEN_SANITY_MAX + 1];
static ecg_frame_t s_fr;
static ecg_rpeak_t s_rp;

/* 结果文件：批处理输出**同时**写串口（进度展示）和 SD 卡（完整对账数据）。
 * 串口重启会丢一段（USB-JTAG 重连延迟），SD 卡文件才是权威记录。 */
static FILE *s_result = NULL;

/* 双路输出：写结果文件 + 打串口。结果文件没打开时退化为只打串口。 */
static void batch_out(const char *fmt, ...)
{
    va_list ap;

    if (s_result != NULL) {
        va_start(ap, fmt);
        vfprintf(s_result, fmt, ap);
        va_end(ap);
        fflush(s_result);
    }

    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}

/* 只写文件、不打串口：峰序列 `P xxx` 数据量巨大（48 条约 0.87 MB），
 * 串口刷它既慢又无意义（且重启会丢）。串口只留进度行（FILE/BATCH-*）。 */
static void file_out(const char *fmt, ...)
{
    if (s_result == NULL) {
        return;
    }
    va_list ap;
    va_start(ap, fmt);
    vfprintf(s_result, fmt, ap);
    va_end(ap);
    fflush(s_result);
}

/* 从一行里取文件名（去掉目录），便于上位机按名字认记录 */
static const char *base_name(const char *path)
{
    const char *p = strrchr(path, '/');
    return (p != NULL) ? p + 1 : path;
}

/* 从清单路径推导结果文件路径：/sdcard/batch_mit.txt -> /sdcard/batch_mit_result.txt
 * 输出写到静态缓冲（单次批处理期间唯一，无并发）。 */
static const char *result_path_of(const char *manifest)
{
    static char buf[64];
    const size_t len = strlen(manifest);

    /* 去掉 ".txt" 尾巴，拼 "_result.txt" */
    if (len >= 4 && strcmp(manifest + len - 4, ".txt") == 0) {
        snprintf(buf, sizeof(buf), "%.*s_result.txt", (int)(len - 4), manifest);
    } else {
        snprintf(buf, sizeof(buf), "%s_result.txt", manifest);
    }
    return buf;
}

/** 跑单个文件；返回峰值数，<0 表示打开失败 */
static int run_one(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        batch_out("FILE %s OPEN-FAILED\n", base_name(path));
        return -1;
    }

    ecg_rpeak_init(&s_rp, APP_SAMPLE_RATE_HZ);

    uint32_t frames = 0, samples = 0, bad = 0, peaks = 0;
    int      col    = 0;      /* 每行已打的峰数 */

    for (;;) {
        const esp_err_t ret = ecg_frame_read(f, &s_fr, s_raw, sizeof(s_raw));
        if (ret == ESP_ERR_INVALID_RESPONSE) {
            bad++;
            continue;                       /* 假帧头，已内部重同步 */
        }
        if (ret != ESP_OK) {
            break;                          /* EOF / 读错误 */
        }
        frames++;

        for (uint16_t i = 0; i < s_fr.n_ch1; i++) {
            int64_t pk = 0;
            if (ecg_rpeak_process(&s_rp, (float)s_fr.ch1[i], &pk)) {
                /* 峰序列只写 SD 卡结果文件，不打串口（串口只留进度行）。 */
                if (col == 0) {
                    file_out("P %ld", (long)pk);
                } else {
                    file_out(" %ld", (long)pk);
                    if (col == 15) {
                        file_out("\n");
                        col = -1;               /* 下一轮归零 */
                    }
                }
                col++;
                peaks++;
            }
        }
        samples += s_fr.n_ch1;
    }
    if (col > 0 && col != 16) {
        file_out("\n");      /* 峰序列收尾换行，只写文件 */
    }
    fclose(f);

    const uint32_t bpm10 = (uint32_t)(ecg_rpeak_bpm(&s_rp) * 10.0f + 0.5f);
    batch_out("FILE %s frames=%u samples=%u bad=%u peaks=%u bpm=%u.%u\n",
              base_name(path), (unsigned)frames, (unsigned)samples, (unsigned)bad,
              (unsigned)peaks, (unsigned)(bpm10 / 10), (unsigned)(bpm10 % 10));
    batch_out("PENV %s\n", base_name(path));

    ESP_LOGI(TAG, "%s: %u samples, %u peaks, %.1f s",
             base_name(path), (unsigned)samples, (unsigned)peaks,
             (double)samples / APP_SAMPLE_RATE_HZ);
    return (int)peaks;
}

esp_err_t ecg_batch_run_manifest(const char *manifest)
{
    FILE *mf = fopen(manifest, "r");
    if (mf == NULL) {
        ESP_LOGW(TAG, "no manifest %s -- batch skipped", manifest);
        return ESP_ERR_NOT_FOUND;
    }

    /* 打开结果文件：完整批处理输出落盘，串口丢数据时以此为权威记录。 */
    s_result = fopen(result_path_of(manifest), "w");
    if (s_result == NULL) {
        ESP_LOGW(TAG, "cannot open result file -- 结果只打串口");
    }

    const int64_t t0 = esp_timer_get_time();
    int total = 0, ok = 0, fail = 0;

    /* 先数一遍总条数，便于上位机知道该等几个 FILE 段 */
    char line[160];
    while (fgets(line, sizeof(line), mf) != NULL) {
        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r') {
            continue;
        }
        total++;
    }
    rewind(mf);
    batch_out("BATCH-BEGIN total=%d\n", total);
    batch_out("# CardioLite S7 batch: fs=%d Hz, 每行最多 16 个峰索引\n",
              APP_SAMPLE_RATE_HZ);

    while (fgets(line, sizeof(line), mf) != NULL) {
        /* 去行尾 */
        size_t n = strlen(line);
        while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r')) {
            line[--n] = '\0';
        }
        if (n == 0 || line[0] == '#') {
            continue;
        }
        if (run_one(line) >= 0) {
            ok++;
        } else {
            fail++;
        }
    }
    fclose(mf);

    const uint32_t ms = (uint32_t)((esp_timer_get_time() - t0) / 1000);
    batch_out("BATCH-END ok=%d fail=%d elapsed_ms=%u\n", ok, fail, (unsigned)ms);
    batch_out("# 用 tools/mitbih_eval.py 或 tools/ecg_batch_eval.py 解析本段输出\n");
    ESP_LOGI(TAG, "batch done: ok=%d fail=%d %u ms", ok, fail, (unsigned)ms);

    if (s_result != NULL) {
        fclose(s_result);
        s_result = NULL;
        ESP_LOGI(TAG, "结果已写入 %s", result_path_of(manifest));
    }

    return (ok > 0) ? ESP_OK : ESP_FAIL;
}
