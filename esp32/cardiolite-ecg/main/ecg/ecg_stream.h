/**
 * @file ecg_stream.h
 * @brief T101 v2.2 帧的**字节流**解析（S9a）—— 供 UART/RS485 这类无文件接口使用。
 *
 * 与 `ecg_frame_read()`（FILE* 接口）的关系：
 *   - 布局、CRC、LEN 白名单、样本合成**全部复用** `ecg_frame_parse_raw()`，
 *     两条数据源同一份口径（复制两份迟早打架）；
 *   - 区别只在"怎么拿到字节"：本模块是 feed 式状态机，适配 UART 的到达式数据。
 *
 * 上电垃圾天然容忍：ROM bootloader 会往 IO43 打几十毫秒日志（软件关不掉），
 * 这些字节在状态机里被当作"非同步字节"滑过，与文件解析的逐字节再同步语义一致。
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "ecg_frame.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    /* ---- 统计（对账用） ---- */
    uint32_t frames;       /* 凑满并通过校验流程的帧数（CRC 错的也算，见 crc_errors） */
    uint32_t crc_errors;   /* CRC 不匹配的帧数 */
    uint32_t resync_bytes; /* 为找同步字而丢弃的字节数（含假帧头丢弃） */
    uint32_t bad_len;      /* 同步字对上但 LEN 离谱、被当假帧头丢弃的次数 */
    uint32_t seq_gaps;     /* SEQ 非 +1 的次数（丢帧指示） */
    uint16_t last_seq;
    bool     has_seq;

    /* ---- 状态机内部 ---- */
    uint8_t  raw[ECG_HDR_LEN + ECG_LEN_SANITY_MAX + 1];
    uint16_t got;    /* 本帧已收字节数 */
    uint16_t need;   /* 本帧总长（收到帧头后确定） */
    uint8_t  state;  /* 0=找 AA / 1=等 55 / 2=收帧体 */
} ecg_stream_t;

/** @brief 初始化（清零统计与状态机） */
void ecg_stream_init(ecg_stream_t *st);

/**
 * @brief 喂一个字节。
 * @param[out] out 凑满一帧时写入帧视图（CRC 错也会写，看 out->crc_ok）
 * @return true = 凑满一帧；false = 还没凑满
 */
bool ecg_stream_feed(ecg_stream_t *st, uint8_t byte, ecg_frame_t *out);

/** @brief 清统计（不动状态机；每轮测试开始时用） */
void ecg_stream_reset_stat(ecg_stream_t *st);

#ifdef __cplusplus
}
#endif
