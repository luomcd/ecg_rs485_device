/**
 * @file storage_sd.c
 * @brief SD 卡挂载与 ecg.bin 首帧探测（S1）
 */

#include "storage_sd.h"

#include <dirent.h>
#include <errno.h>
#include <inttypes.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>

#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "driver/sdspi_host.h"
#include "driver/spi_master.h"
#include "sdmmc_cmd.h"

#include "app_config.h"
#include "waveshare_rgb_lcd_port.h"

static const char *TAG = "storage_sd";

static sdmmc_card_t *s_card      = NULL;
static bool          s_spi_inited = false;

/* UI 显示用的结果摘要（storage_sd_last_summary 返回） */
static char s_summary[128] = "SD: not initialized";

#define SUMMARY_FMT(...) do { \
        snprintf(s_summary, sizeof(s_summary), __VA_ARGS__); \
    } while (0)

/* ============================================================================
 * 挂载 / 卸载
 * ========================================================================== */

esp_err_t storage_sd_mount(void)
{
    if (s_card != NULL) {
        ESP_LOGW(TAG, "already mounted");
        return ESP_OK;
    }

    /* ---- 1) 先用 CH422G 把 SD 片选拉低（0x1E -> 0x0E）----
     *
     * 必须在任何 SPI 通信之前。本板 SD_CS 是常低拓扑：选中之后整个挂载
     * 生命周期都保持低电平，SPI2 上只有 SD 一个从设备，所以不需要逐次切换。
     * ⚠ 副作用：将来若要在 SPI2 上挂第二颗器件，必须先写回 0x1E 释放 SD。 */
    esp_err_t ret = waveshare_ch422g_sd_cs(true);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "CH422G select SD failed: %s", esp_err_to_name(ret));
        return ret;
    }
    ESP_LOGI(TAG, "CH422G out = 0x%02X (SD_CS selected, expect 0x0E)",
             waveshare_ch422g_get_out_shadow());

    /* ---- 2) SPI2 总线 ----
     * RGB 面板走 LCD_CAM 外设、触摸走 I2C，所以 SPI2 完全空闲，可独占。 */
    if (!s_spi_inited) {
        const spi_bus_config_t bus_cfg = {
            .mosi_io_num     = APP_SD_PIN_MOSI,
            .miso_io_num     = APP_SD_PIN_MISO,
            .sclk_io_num     = APP_SD_PIN_CLK,
            .quadwp_io_num   = -1,
            .quadhd_io_num   = -1,
            .max_transfer_sz = 4000,
        };
        ret = spi_bus_initialize(SPI2_HOST, &bus_cfg, SDSPI_DEFAULT_DMA);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "spi_bus_initialize(SPI2) failed: %s", esp_err_to_name(ret));
            return ret;
        }
        s_spi_inited = true;
        ESP_LOGI(TAG, "SPI2 initialized: MOSI=%d MISO=%d CLK=%d max=%d kHz",
                 APP_SD_PIN_MOSI, APP_SD_PIN_MISO, APP_SD_PIN_CLK, APP_SD_FREQ_KHZ);
    }

    /* ---- 3) sdspi 挂载 ---- */
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot         = SPI2_HOST;
    host.max_freq_khz = APP_SD_FREQ_KHZ;

    sdspi_device_config_t slot = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot.host_id = SPI2_HOST;
    /* ★ 必须显式覆盖：SDSPI_DEVICE_CONFIG_DEFAULT() 里 gpio_cs 默认是
     *   GPIO_NUM_13，而 GPIO13 正是本板 SD 的 MISO —— 放着不管会去驱动它。
     *   SDSPI_SLOT_NO_CS = GPIO_NUM_NC(-1)，表示片选不由 GPIO 控制。 */
    slot.gpio_cs = SDSPI_SLOT_NO_CS;

    const esp_vfs_fat_mount_config_t mount_cfg = {
        /* 🛑 固定 false：挂载失败不格式化。不能因为一次接触不良就把卡里的
         *    录制数据洗掉。真要格式化由人显式操作。 */
        .format_if_mount_failed = false,
        .max_files              = 5,
        .allocation_unit_size   = 16 * 1024,
    };

    ret = esp_vfs_fat_sdspi_mount(APP_SD_MOUNT_POINT, &host, &slot, &mount_cfg, &s_card);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "esp_vfs_fat_sdspi_mount failed: %s (0x%x)",
                 esp_err_to_name(ret), ret);
        ESP_LOGE(TAG, "排查顺序：① 卡是否为 FAT32（exFAT 不被支持，FF_FS_EXFAT=0）");
        ESP_LOGE(TAG, "          ② 卡是否插到位  ③ 把 APP_SD_FREQ_KHZ 降到 10000 再试");
        SUMMARY_FMT("SD: MOUNT FAILED (%s)", esp_err_to_name(ret));
        return ret;
    }

    ESP_LOGI(TAG, "mounted at %s", APP_SD_MOUNT_POINT);
    sdmmc_card_print_info(stdout, s_card);
    SUMMARY_FMT("SD: mounted (%s), no file yet",
                s_card->is_mmc ? "MMC" : "SD");
    return ESP_OK;
}

esp_err_t storage_sd_unmount(void)
{
    if (s_card != NULL) {
        esp_err_t ret = esp_vfs_fat_sdcard_unmount(APP_SD_MOUNT_POINT, s_card);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "unmount failed: %s", esp_err_to_name(ret));
        }
        s_card = NULL;
    }
    if (s_spi_inited) {
        spi_bus_free(SPI2_HOST);
        s_spi_inited = false;
    }
    /* 释放片选，回到稳态 0x1E */
    waveshare_ch422g_sd_cs(false);
    ESP_LOGI(TAG, "unmounted, CH422G out = 0x%02X",
             waveshare_ch422g_get_out_shadow());
    return ESP_OK;
}

bool storage_sd_is_mounted(void)
{
    return s_card != NULL;
}

/* ============================================================================
 * S1 验收：打开 ecg.bin，打印大小与首帧帧头
 * ========================================================================== */

esp_err_t storage_sd_probe_ecg_file(void)
{
    if (s_card == NULL) {
        ESP_LOGE(TAG, "not mounted");
        return ESP_ERR_INVALID_STATE;
    }

    /* ---- 文件大小 ---- */
    struct stat st;
    if (stat(APP_SD_FILE_PATH, &st) != 0) {
        ESP_LOGE(TAG, "stat(%s) failed, errno=%d", APP_SD_FILE_PATH, errno);
        SUMMARY_FMT("SD: mounted but %s missing", APP_SD_FILE_PATH);
        return ESP_ERR_NOT_FOUND;
    }
    const long size = (long)st.st_size;
    ESP_LOGI(TAG, "ecg.bin size = %ld B (%ld KiB)", size, size / 1024);

    /* ---- 首帧帧头 ---- */
    FILE *f = fopen(APP_SD_FILE_PATH, "rb");
    if (f == NULL) {
        ESP_LOGE(TAG, "fopen failed, errno=%d", errno);
        SUMMARY_FMT("SD: ecg.bin exists but cannot open (errno=%d)", errno);
        return ESP_FAIL;
    }

    uint8_t hdr[8] = {0};
    const size_t n = fread(hdr, 1, sizeof(hdr), f);
    fclose(f);

    if (n < ECG_FRAME_OVERHEAD) {
        ESP_LOGE(TAG, "read only %u B, need >= %d", (unsigned)n, ECG_FRAME_OVERHEAD);
        SUMMARY_FMT("SD: ecg.bin too short (%u B)", (unsigned)n);
        return ESP_ERR_INVALID_SIZE;
    }

    ESP_LOGI(TAG, "first 8 bytes: %02X %02X %02X %02X %02X %02X %02X %02X",
             hdr[0], hdr[1], hdr[2], hdr[3], hdr[4], hdr[5], hdr[6], hdr[7]);

    /* ---- 同步字 ---- */
    if (hdr[0] != ECG_SYNC0 || hdr[1] != ECG_SYNC1) {
        ESP_LOGE(TAG, "sync mismatch: expect %02X %02X, got %02X %02X",
                 ECG_SYNC0, ECG_SYNC1, hdr[0], hdr[1]);
        SUMMARY_FMT("SD: ecg.bin sync ERR (%02X %02X)", hdr[0], hdr[1]);
        return ESP_ERR_INVALID_RESPONSE;
    }

    /* ---- LEN（小端）与派生量 ---- */
    const uint16_t len = (uint16_t)(hdr[3] | (hdr[4] << 8));
    const uint16_t frame_total = (uint16_t)(ECG_FRAME_OVERHEAD + len);
    /* LEN 奇数带 STA ⇒ N=(LEN-3)/2；LEN 偶数（v1.1 旧帧）⇒ N=(LEN-2)/2 */
    const uint16_t samples = (len % 2) ? (uint16_t)((len - 3) / 2)
                                       : (uint16_t)((len - 2) / 2);

    ESP_LOGI(TAG, "first frame: TYPE=0x%02X LEN=%u  frame_total=%u B  N=%u samples",
             hdr[2], len, frame_total, samples);

    const bool len_ok = (len == ECG_LEN_V11 || len == ECG_LEN_V22 || len == ECG_LEN_DUAL);
    if (!len_ok) {
        ESP_LOGW(TAG, "LEN=%u 不在已知三型（52/53/103）之内 —— 仍是合法帧长，但需确认数据来源",
                 len);
    }

    /* ---- 帧数与大小自洽（凭 LEN 推算，不做全量解析；那是 S2） ---- */
    const long frames_est = (frame_total > 0) ? (size / frame_total) : 0;
    const long tail       = (frame_total > 0) ? (size % frame_total) : 0;
    if (frame_total > 0 && size > 0) {
        ESP_LOGI(TAG, "size consistency: %ld B / %u B = %ld frames, tail %ld B",
                 size, frame_total, frames_est, tail);
        if (tail != 0) {
            ESP_LOGW(TAG, "有 %ld B 尾字节，若 %ld 之后混入其他 LEN 型则属正常（S2 会逐帧解析）",
                     tail, frames_est);
        }
    }

    SUMMARY_FMT("SD: ecg.bin %ld B | LEN=%u, %u B/frame x%ld",
                size, len, frame_total, frames_est);

    ESP_LOGI(TAG, "S1 probe OK");
    return ESP_OK;
}

const char *storage_sd_last_summary(void)
{
    return s_summary;
}

int storage_sd_delete_ecg_file(void)
{
    if (s_card == NULL) {
        ESP_LOGE(TAG, "delete: SD not mounted");
        return -1;
    }
    errno = 0;
    const int rc = remove(APP_SD_FILE_PATH);
    if (rc == 0) {
        ESP_LOGW(TAG, "deleted %s", APP_SD_FILE_PATH);
        SUMMARY_FMT("SD: ecg.bin deleted");
    } else {
        ESP_LOGE(TAG, "remove(%s) failed, errno=%d", APP_SD_FILE_PATH, errno);
    }
    return rc;
}

/* ============================================================================
 * 文件扫描：按首帧 LEN 分类
 * ========================================================================== */

static storage_sd_files_t s_files;
static long                s_dual_size;     /* 已选中的双通道文件大小（用于"选最大"） */

esp_err_t storage_sd_scan_files(void)
{
    memset(&s_files, 0, sizeof(s_files));
    s_dual_size = 0;

    if (s_card == NULL) {
        ESP_LOGE(TAG, "scan: SD not mounted");
        return ESP_ERR_INVALID_STATE;
    }

    DIR *dir = opendir(APP_SD_MOUNT_POINT);
    if (dir == NULL) {
        ESP_LOGE(TAG, "opendir(%s) failed, errno=%d", APP_SD_MOUNT_POINT, errno);
        return ESP_FAIL;
    }

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        const char *name = ent->d_name;
        const size_t nlen = strlen(name);
        if (nlen < 5) {
            continue;
        }
        if (strcasecmp(name + nlen - 4, ".bin") != 0) {
            continue;
        }

        char full[SD_PATH_MAX];
        snprintf(full, sizeof(full), "%s/%s", APP_SD_MOUNT_POINT, name);

        FILE *f = fopen(full, "rb");
        if (f == NULL) {
            continue;
        }
        uint8_t hdr[ECG_FRAME_OVERHEAD] = {0};
        const size_t got = fread(hdr, 1, sizeof(hdr), f);
        fclose(f);

        if (got < ECG_FRAME_OVERHEAD) {
            continue;
        }
        if (hdr[0] != ECG_SYNC0 || hdr[1] != ECG_SYNC1) {
            continue;
        }

        const uint16_t flen = (uint16_t)(hdr[3] | ((uint16_t)hdr[4] << 8));

        if (flen == ECG_LEN_DUAL) {
            /* 双通道文件**选最大的那个** —— 时长最长，演示/长跑效果最好。
             * 典型场景：卡上同时有 3.5 s 的原始样本和 5 min 的合成文件，应该用后者。 */
            struct stat st;
            const long sz = (stat(full, &st) == 0) ? (long)st.st_size : 0;
            if (s_files.dual[0] == '\0' || sz > s_dual_size) {
                snprintf(s_files.dual, SD_PATH_MAX, "%s", full);
                s_dual_size = sz;
                /* 时长 = 帧数 × 25 样本/通道 ÷ 500 Hz；帧总长 = 6 + LEN */
                const long frames = (sz > 0) ? (sz / (6 + flen)) : 0;
                s_files.dual_sec = (uint32_t)(frames * 25 / APP_SAMPLE_RATE_HZ);
                ESP_LOGI(TAG, "  dual  : %s (LEN=%u, %ld B, %u s)",
                         name, flen, sz, (unsigned)s_files.dual_sec);
            }
        } else {
            /* 单通道：ecg.bin 优先；没有 ecg.bin 时先到先得 */
            const bool is_main = (strcmp(name, APP_SD_FILE_PATH + 8) == 0);
            if (s_files.single[0] == '\0' || is_main) {
                snprintf(s_files.single, SD_PATH_MAX, "%s", full);
                struct stat st1;
                const long sz1 = (stat(full, &st1) == 0) ? (long)st1.st_size : 0;
                const long fr1 = (sz1 > 0) ? (sz1 / (6 + flen)) : 0;
                s_files.single_sec = (uint32_t)(fr1 * 25 / APP_SAMPLE_RATE_HZ);
                ESP_LOGI(TAG, "  single: %s (LEN=%u, %ld B, %u s)%s",
                         name, flen, sz1, (unsigned)s_files.single_sec,
                         is_main ? " [main]" : "");
            }
        }
    }

    closedir(dir);

    ESP_LOGI(TAG, "scan result: single=%s, dual=%s",
             s_files.single[0] ? s_files.single : "(none)",
             s_files.dual[0]   ? s_files.dual   : "(none)");
    return ESP_OK;
}

const storage_sd_files_t *storage_sd_get_files(void)
{
    return &s_files;
}
