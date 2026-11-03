/**
 * @file ecg_frame.h
 * @brief T101 v2.2 帧解析（S2）
 *
 * 帧格式：
 *     AA 55 | TYPE(1) | LEN(2, LE) | PAYLOAD(LEN) | CRC8(1)
 *     帧总长 = 6 + LEN
 *     CRC8：多项式 0x07、初值 0x00，范围 = TYPE + LEN + PAYLOAD
 *
 * PAYLOAD 布局（由首帧实测反推并自洽验证，见 test_logs S2 报告 §3.2）：
 *     LEN=53  : SEQ(2,LE) + STA(1) + int16×25        （单通道，25 样本）
 *     LEN=103 : SEQ(2,LE) + STA(1) + int16×25(ch1) + int16×25(ch2)
 *                                                    （双通道，块布局**不交错**）
 *     LEN=52  : SEQ(2,LE) + int16×25                 （v1.1 旧帧，**无 STA**）
 *
 * 样本数：LEN 奇数 ⇒ N = (LEN-3)/2；LEN 偶数 ⇒ N = (LEN-2)/2
 *
 * 🛑 严禁按固定 58 字节切帧 —— 那只在 LEN=52 下成立。LEN=53 每帧 59 B，
 *    LEN=103 每帧 109 B，照抄必然整文件错位（参考工程 Core/ReplaySource.cs 的教训）。
 *
 * 已用 4 个文件 4185 帧实测确证（2026-10-23，PC 端 Python）：
 *     1796×59 = 105,964 ；1182×59 = 69,738 ；1136×59 = 67,024 ；71×109 = 7,739
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "esp_err.h"

/* ============================================================================
 * 常量
 * ========================================================================== */

#define ECG_SYNC0           0xAA
#define ECG_SYNC1           0x55

/* 帧头长度 = AA55(2) + TYPE(1) + LEN(2) = 5 字节
 * ⚠ 不要把下面的 ECG_FRAME_OVERHEAD 当帧头长度用！
 *   ECG_FRAME_OVERHEAD = 帧头 5 + CRC 1 = 6，是用来算「帧总长 = LEN + 6」的。
 *   2026-10-23 曾误用 6 去 fread 帧头，导致每帧多吞 1 字节、帧数正好减半。 */
#define ECG_HDR_LEN         5

/* 帧的固定开销（不含 PAYLOAD）：帧头 5 + CRC8 1 = 6 ⇒ 帧总长 = LEN + 6 */
#define ECG_FRAME_OVERHEAD  6

#define ECG_LEN_V11         52      /* ⚠ **旧帧**（v1.1 早期固件），无 STA，总长 58。
                                     * 2026-10-23 拍板：**摒弃，不参与验收**。
                                     * 解析器保留识别能力（万一读到老数据不崩），
                                     * 但三参数状态填 ECG_STATE_UNKNOWN，且不计入任何判据。
                                     * 手上已无此型样本，口径按协议 §3.1 表格（N=25）实现。 */
#define ECG_LEN_V22         53      /* 单通道，总长 59 */
#define ECG_LEN_DUAL        103     /* 双通道，总长 109，块布局不交错 */

#define ECG_CRC8_POLY       0x07
#define ECG_CRC8_INIT       0x00

/* LEN 上限：合法最大 103；给重同步时的容错留到 200 */
#define ECG_LEN_SANITY_MAX  200

/* STA 位定义 */
#define ECG_STA_BIT_MODE    (1u << 0)
#define ECG_STA_BIT_NOTCH   (1u << 1)
#define ECG_STA_BIT_CHANNEL (1u << 2)
#define ECG_STA_BIT_RAW853  (1u << 3)
#define ECG_STA_BIT_LOD     (1u << 4)

/* 无 STA 的旧帧，三参数状态未知时的哨兵（不得伪装成已知值） */
#define ECG_STATE_UNKNOWN   0xFF

/* 每通道最大样本数：LEN=103 时 N=(103-3)/2=50，所以 50 是上限 */
#define ECG_MAX_SAMPLES_PER_CH  50

/* ============================================================================
 * 单帧视图
 *
 * ⚠ 样本用**内嵌定长数组**而不是指向 raw_buf 的指针，原因：
 *   payload 里 int16 的起始偏移是 3（SEQ 2 + STA 1）或 2，都是**奇数**，
 *   把奇数地址强转成 int16_t* 在 Xtensa 上是未对齐访问 —— 会触发
 *   LoadStoreAlignment 异常或取到错误数据。所以解析时逐字节合成 le16，
 *   写进结构体里自然对齐的数组。
 *
 *   代价：每帧最多拷 200 B。500 Hz 下约 100 KB/s，可忽略。
 * ========================================================================== */

typedef struct {
    uint8_t  type;         /* TYPE 字段 */
    uint16_t len;          /* LEN 字段原值 */
    uint16_t frame_total;  /* 6 + len */
    uint16_t seq;          /* 采集端序号（逐帧 +1，可用于查丢帧） */
    bool     has_sta;      /* LEN 为奇数时 true */
    uint8_t  sta;          /* has_sta ? payload[2] : ECG_STATE_UNKNOWN */
    uint16_t n_ch1;        /* 通道 1 样本数 */
    uint16_t n_ch2;        /* 通道 2 样本数（单通道帧为 0） */
    int16_t  ch1[ECG_MAX_SAMPLES_PER_CH];
    int16_t  ch2[ECG_MAX_SAMPLES_PER_CH];
    uint8_t  crc_calc;     /* 计算出的 CRC8 */
    uint8_t  crc_recv;     /* 帧里携带的 CRC8 */
    bool     crc_ok;
    /* 本帧读取过程中为找同步字而额外前进的字节数。
     * 正常应为 0；非 0 说明发生了字节漂移。上层应累加进 sync_errors，
     * 否则漂移会被内部重同步吞掉、伪装成「解析顺利」。
     * （2026-10-23 就是因为这一点，帧数正好减半却没报 sync_err。）*/
    uint16_t resync_bytes;
} ecg_frame_t;

/* ============================================================================
 * 整文件扫描统计
 * ========================================================================== */

typedef struct {
    uint32_t frames;          /* 成功解析的帧数 */
    uint32_t crc_errors;      /* CRC 不匹配的帧数 */
    uint32_t sync_errors;     /* 同步字错位次数（重同步触发次数） */
    uint32_t other_len;       /* 长度不在 52/53/103 之内的帧数 */

    uint32_t cnt_len_52;
    uint32_t cnt_len_53;
    uint32_t cnt_len_103;

    uint32_t samples_ch1;     /* 通道 1 累计样本数 */
    uint32_t samples_ch2;     /* 通道 2 累计样本数 */

    uint32_t tail_bytes;      /* 文件尾部不足一帧的残余字节 */

    bool     first_sta_valid; /* 是否读到过带 STA 的帧 */
    uint8_t  first_sta;       /* 第一帧的 STA */
    uint16_t first_seq;       /* 第一帧的 SEQ */
    uint16_t last_seq;        /* 最后一帧的 SEQ */
    uint32_t seq_gaps;        /* SEQ 非 +1 的次数（丢帧指示） */

    long     file_size;       /* 文件字节数 */
    long     bytes_consumed;  /* 实际消费的字节数 */
} ecg_scan_stat_t;

/* ============================================================================
 * API
 * ========================================================================== */

/** @brief CRC8（多项式 0x07，初值 0x00，不反射） */
uint8_t ecg_crc8(const uint8_t *data, size_t len);

/**
 * @brief 从 FILE* 顺序读一帧（自动处理同步字错位后的重同步）。
 *
 * @param[in]  f          已打开的文件
 * @param[out] fr         帧视图（样本已逐字节合成进内嵌数组）
 * @param[in]  raw_buf    调用方提供的原始字节缓冲，至少 ECG_FRAME_OVERHEAD + ECG_LEN_SANITY_MAX + 1 字节
 * @param[in]  raw_size   raw_buf 大小
 *
 * @retval ESP_OK         读到一帧（无论 CRC 是否通过，看 fr->crc_ok）
 * @retval ESP_ERR_NOT_FOUND    正常 EOF
 * @retval ESP_ERR_INVALID_SIZE 文件尾部残余不足一帧
 * @retval ESP_ERR_INVALID_RESPONSE 帧头非法，已向后重同步 1 字节（调用方应计数并继续）
 */
esp_err_t ecg_frame_read(FILE *f, ecg_frame_t *fr, uint8_t *raw_buf, size_t raw_size);

/**
 * @brief 把一整帧原始字节（HDR 5 + PAYLOAD + CRC 1）解析成帧视图。
 *
 * S9a 起由 `ecg_frame_read`（文件）与 `ecg_stream_feed`（UART 流）共用，
 * 保证两条数据源**同一份布局/CRC 口径**——复制两份迟早打架。
 *
 * @param[out] fr  帧视图（样本逐字节合成进内嵌数组）
 * @param[in]  raw 至少 `ECG_FRAME_OVERHEAD + len` 字节；len 由 raw[3..4] 决定
 * @note 调用方负责确认 raw 里确实有一整帧且 len 合法（2..ECG_LEN_SANITY_MAX）。
 */
void ecg_frame_parse_raw(ecg_frame_t *fr, const uint8_t *raw);

/**
 * @brief 扫描整个文件并统计（S2 验收用）。
 *
 * 内部顺序读，不把整个文件载入内存 —— 文件可以任意大。
 */
esp_err_t ecg_scan_file(const char *path, ecg_scan_stat_t *stat);

/**
 * @brief 把 STA 字节拆成可读字符串（写进调用方缓冲）。
 *
 * 无 STA 时输出 "unknown"（不是 "诊断/关/I" —— 不能把未知伪装成已知）。
 */
void ecg_sta_to_str(uint8_t sta, bool has_sta, char *buf, size_t buf_size);
