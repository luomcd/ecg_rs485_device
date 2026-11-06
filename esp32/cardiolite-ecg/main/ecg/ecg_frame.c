/**
 * @file ecg_frame.c
 * @brief T101 v2.2 帧解析实现（S2）
 */

#include "ecg_frame.h"

#include <string.h>

#include "esp_log.h"

static const char *TAG = "ecg_frame";

/* ============================================================================
 * 基础工具
 * ========================================================================== */

uint8_t ecg_crc8(const uint8_t *data, size_t len)
{
    uint8_t crc = ECG_CRC8_INIT;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ ECG_CRC8_POLY)
                               : (uint8_t)(crc << 1);
        }
    }
    return crc;
}

/* 小端 16 位读取。
 * ⚠ 不能写成 *(const int16_t*)p —— payload 里样本的偏移是奇数，
 *   Xtensa 上属于未对齐访问。逐字节合成是唯一安全的做法。 */
static inline int16_t le16(const uint8_t *p)
{
    return (int16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

/* ============================================================================
 * 读一帧
 * ========================================================================== */

esp_err_t ecg_frame_read(FILE *f, ecg_frame_t *fr, uint8_t *raw, size_t raw_size)
{
    if (f == NULL || fr == NULL || raw == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    /* 需要容纳：帧头 5 + PAYLOAD(上限) + CRC 1 */
    if (raw_size < (size_t)(ECG_HDR_LEN + ECG_LEN_SANITY_MAX + 1)) {
        return ESP_ERR_INVALID_SIZE;
    }

    /* ---- 找帧头：同步字错位时每轮只前进 1 字节，直到对齐或 EOF ----
     * ⚠ 帧头只有 ECG_HDR_LEN(5) 字节，不是 ECG_FRAME_OVERHEAD(6)。
     *   后者含 CRC，只用于算帧总长。 */
    fr->resync_bytes = 0;
    for (;;) {
        const size_t n = fread(raw, 1, ECG_HDR_LEN, f);
        if (n == 0) {
            return ESP_ERR_NOT_FOUND;            /* 正常 EOF */
        }
        if (n < ECG_HDR_LEN) {
            return ESP_ERR_INVALID_SIZE;         /* 尾部残字节 */
        }
        if (raw[0] == ECG_SYNC0 && raw[1] == ECG_SYNC1) {
            break;
        }
        /* 已读 5 字节但不同步 ⇒ 回退 4 字节，净前进 1 字节 */
        fseek(f, -(long)(ECG_HDR_LEN - 1), SEEK_CUR);
        fr->resync_bytes++;
        if (fr->resync_bytes > ECG_LEN_SANITY_MAX) {
            /* 连续 200 字节都找不到同步字 ⇒ 文件已损坏，别无限扫 */
            return ESP_ERR_INVALID_RESPONSE;
        }
    }

    const uint16_t len = (uint16_t)(raw[3] | ((uint16_t)raw[4] << 8));
    if (len < 2 || len > ECG_LEN_SANITY_MAX) {
        /* 同步字对但长度离谱 ⇒ 这是个假帧头，继续往后找 */
        fseek(f, -(long)(ECG_HDR_LEN - 1), SEEK_CUR);
        return ESP_ERR_INVALID_RESPONSE;
    }

    /* 帧总长 = 帧头 5 + PAYLOAD(len) + CRC 1 = len + 6 */
    const uint16_t total = (uint16_t)(ECG_HDR_LEN + len + 1u);

    /* 接着读 PAYLOAD(len) + CRC(1) */
    const size_t rest = fread(raw + ECG_HDR_LEN, 1, (size_t)len + 1u, f);
    if (rest < (size_t)len + 1u) {
        return ESP_ERR_INVALID_SIZE;             /* 帧不完整（截断） */
    }

    /* 布局解析 + CRC 交给公共函数（S9a 起流式解析器 ecg_stream 共用同一份口径，
     * 避免两套实现慢慢打架） */
    ecg_frame_parse_raw(fr, raw);
    return ESP_OK;
}

/* ============================================================================
 * 原始字节 → 帧视图（S9a 抽出，供 FILE 解析与 UART 流式解析共用）
 * ========================================================================== */

void ecg_frame_parse_raw(ecg_frame_t *fr, const uint8_t *raw)
{
    const uint16_t len   = (uint16_t)(raw[3] | ((uint16_t)raw[4] << 8));
    const uint16_t total = (uint16_t)(ECG_HDR_LEN + len + 1u);

    /* ---- 头字段 ---- */
    fr->type        = raw[2];
    fr->len         = len;
    fr->frame_total = total;

    /* ---- payload 布局 ----
     * 奇数 LEN：SEQ(2) + STA(1) + samples
     * 偶数 LEN：SEQ(2)         + samples   （v1.1 旧帧无 STA）*/
    const uint8_t *payload = raw + ECG_HDR_LEN;
    const bool     odd     = (len & 1u) != 0;
    const uint16_t hdr_len = odd ? 3u : 2u;

    fr->has_sta = odd;
    fr->seq     = (uint16_t)(payload[0] | ((uint16_t)payload[1] << 8));
    fr->sta     = odd ? payload[2] : (uint8_t)ECG_STATE_UNKNOWN;

    /* 🛑 T208 修复（11-05）：`len < hdr_len` 时无符号下溢。
     *   原式 `(uint16_t)((len - hdr_len) / 2u)` 在 len=1、hdr_len=3 时
     *   ⇒ 65534/2 = 32767 ⇒ 被下面 clamp 到 ECG_MAX_SAMPLES_PER_CH=50
     *   ⇒ **越界读 ~99 B 且 CRC 仍然通过**（T206 §9.1b 已核到行号）。
     *   触发源就在本链路上：ESP32 自己发的下行帧 LEN=1，半双工回环能收到。
     *   现在 len 不足头部长度时 n_total 记 0，不产生任何样本。 */
    const uint16_t n_total =
        (len > hdr_len) ? (uint16_t)((len - hdr_len) / 2u) : 0u;

    if (len == ECG_LEN_DUAL) {
        /* 双通道：块布局不交错，前一半 CH1，后一半 CH2 */
        fr->n_ch1 = (uint16_t)(n_total / 2u);
        fr->n_ch2 = (uint16_t)(n_total - fr->n_ch1);
    } else {
        fr->n_ch1 = n_total;
        fr->n_ch2 = 0;
    }

    /* ---- 样本：逐字节合成，保证对齐安全 ---- */
    const uint8_t *sp = payload + hdr_len;
    if (fr->n_ch1 > ECG_MAX_SAMPLES_PER_CH) {
        fr->n_ch1 = ECG_MAX_SAMPLES_PER_CH;
    }
    for (uint16_t i = 0; i < fr->n_ch1; i++) {
        fr->ch1[i] = le16(sp + (size_t)i * 2u);
    }
    if (fr->n_ch2 > ECG_MAX_SAMPLES_PER_CH) {
        fr->n_ch2 = ECG_MAX_SAMPLES_PER_CH;
    }
    for (uint16_t i = 0; i < fr->n_ch2; i++) {
        fr->ch2[i] = le16(sp + ((size_t)fr->n_ch1 + i) * 2u);
    }

    /* ---- CRC：范围 = TYPE + LEN(2) + PAYLOAD(len) ---- */
    fr->crc_calc = ecg_crc8(raw + 2, (size_t)(1u + 2u + len));
    fr->crc_recv = raw[total - 1];
    fr->crc_ok   = (fr->crc_calc == fr->crc_recv);
}

/* ============================================================================
 * 整文件扫描
 * ========================================================================== */

esp_err_t ecg_scan_file(const char *path, ecg_scan_stat_t *stat)
{
    if (path == NULL || stat == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(stat, 0, sizeof(*stat));

    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        ESP_LOGW(TAG, "open %s failed", path);
        return ESP_ERR_NOT_FOUND;
    }

    if (fseek(f, 0, SEEK_END) != 0) {
        fclose(f);
        return ESP_FAIL;
    }
    stat->file_size = ftell(f);
    fseek(f, 0, SEEK_SET);

    uint8_t     raw[ECG_FRAME_OVERHEAD + ECG_LEN_SANITY_MAX + 1];
    ecg_frame_t fr;
    uint16_t    prev_seq = 0;
    bool        first    = true;

    for (;;) {
        const long frame_start = ftell(f);

        const esp_err_t ret = ecg_frame_read(f, &fr, raw, sizeof(raw));
        if (ret == ESP_ERR_NOT_FOUND) {
            break;                                    /* 正常读完 */
        }
        if (ret == ESP_ERR_INVALID_SIZE) {
            /* 尾部残余：从本帧起点算剩余字节 */
            const long consumed = frame_start;
            stat->tail_bytes = (uint32_t)(stat->file_size - consumed);
            break;
        }
        if (ret == ESP_ERR_INVALID_RESPONSE) {
            stat->sync_errors++;
            if (stat->sync_errors > 4096) {           /* 防病态文件死循环 */
                ESP_LOGW(TAG, "%s: too many sync errors, abort", path);
                break;
            }
            continue;
        }
        if (ret != ESP_OK) {
            break;
        }

        /* ---- 统计 ---- */
        stat->frames++;
        /* 漂移不可静默：把本帧为找同步字额外前进的字节数计入 sync_errors。
         * 若这里持续非 0，说明帧长口径或读取偏移出了问题。 */
        stat->sync_errors += fr.resync_bytes;
        if (!fr.crc_ok) {
            stat->crc_errors++;
        }

        switch (fr.len) {
        case ECG_LEN_V11:  stat->cnt_len_52++;  break;
        case ECG_LEN_V22:  stat->cnt_len_53++;  break;
        case ECG_LEN_DUAL: stat->cnt_len_103++; break;
        default:           stat->other_len++;   break;
        }

        stat->samples_ch1 += fr.n_ch1;
        stat->samples_ch2 += fr.n_ch2;

        if (first) {
            first           = false;
            stat->first_seq = fr.seq;
            stat->last_seq     = fr.seq;
            if (fr.has_sta) {
                stat->first_sta_valid = true;
                stat->first_sta       = fr.sta;
            }
        } else if (fr.seq != (uint16_t)(prev_seq + 1u)) {
            stat->seq_gaps++;
        }
        prev_seq       = fr.seq;
        stat->last_seq = fr.seq;
    }

    stat->bytes_consumed = ftell(f);
    fclose(f);
    return ESP_OK;
}

/* ============================================================================
 * STA 可视化
 * ========================================================================== */

void ecg_sta_to_str(uint8_t sta, bool has_sta, char *buf, size_t buf_size)
{
    if (buf == NULL || buf_size == 0) {
        return;
    }
    if (!has_sta) {
        /* 不能把「未知」伪装成「诊断/关/I」 */
        snprintf(buf, buf_size, "unknown(no STA)");
        return;
    }
    snprintf(buf, buf_size, "%s notch=%s ch=%s raw853=%d lod=%d",
             /* bit0：0=diag（诊断）／1=mon（监护）
              * 依据协议 §5.1 `0x04 SET_MODE` 的 ARG 定义「1 B：0=diag／1=mon」，
              * 以及「下一帧 STA.bit0」的回读契约。
              * ⚠ 2026-10-23 初版把这一位写反了（bit0=1 判成 diag）。*/
             (sta & ECG_STA_BIT_MODE) ? "mon" : "diag",
             (sta & ECG_STA_BIT_NOTCH) ? "on" : "off",
             (sta & ECG_STA_BIT_CHANNEL) ? "I&II" : "I",
             (sta & ECG_STA_BIT_RAW853) ? 1 : 0,
             (sta & ECG_STA_BIT_LOD) ? 1 : 0);
}
