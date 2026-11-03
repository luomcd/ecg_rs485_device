/*
 * ecg_crc8.h —— 协议 v1.1 帧校验（CRC-8）
 * ---------------------------------------------------------------------------
 * 口径（必须与 PC 端 parse_v11_frames.py 完全一致，改任何一项都会导致整条
 * 链路校验失败）：
 *
 *   poly    = 0x07   (x^8 + x^2 + x + 1)
 *   init    = 0x00
 *   refin   = false  (不反射输入)
 *   refout  = false  (不反射输出)
 *   xorout  = 0x00
 *   → 即标准 CRC-8（别名 CRC-8/SMBUS）
 *
 * 计算范围：TYPE + LEN + PAYLOAD
 *   ┌────────┬──────┬────────┬───────────────┬───────┐
 *   │ AA 55  │ TYPE │ LEN LE │  PAYLOAD      │ CRC8  │
 *   └────────┴──────┴────────┴───────────────┴───────┘
 *     ↑ 不含帧头                          ↑ 不含 CRC 自身
 *
 *   C 实现里等价于：ecg_crc8(&frame[2], len_of_type_len_payload);
 *
 * 自测向量（写完固件先单步核对这三个，对上了就不会错）：
 *   ecg_crc8("123456789", 9)              == 0xF4   ← 标准 check 值
 *   ecg_crc8("\x00", 1)                   == 0x00
 *   ecg_crc8("\x01", 1)                   == 0x07
 *
 * 用法（拼帧时二遍扫描，帧只有 50~360 字节，开销可忽略）：
 *   p[0] = 0xAA; p[1] = 0x55;
 *   p[2] = type;
 *   put_u16_le(&p[3], payload_len);
 *   ... 填 payload ...
 *   n = 5 + payload_len;                       // 帧头2 + 型1 + 长度2 + 负载
 *   p[n] = ecg_crc8(&p[2], 3 + payload_len);   // 覆盖 TYPE+LEN+PAYLOAD
 *   uart_send(p, n + 1);
 *
 * 也可边写边算（增量版，省一遍扫描）：
 *   uint8_t c = ecg_crc8_update(0x00, type);
 *   c = ecg_crc8_update(c, len_lo);  c = ecg_crc8_update(c, len_hi);
 *   ... 每写一个负载字节就 update 一次 ...
 *   p[n] = c;
 * ---------------------------------------------------------------------------
 */
#ifndef ECG_CRC8_H
#define ECG_CRC8_H

#include <stdint.h>

/* 增量：把 1 个字节并入已有 CRC 值（初值传 0x00） */
uint8_t ecg_crc8_update(uint8_t crc, uint8_t byte);

/* 一次性计算 len 个字节的 CRC8 */
uint8_t ecg_crc8(const uint8_t *data, uint32_t len);

/* 上电自检：用 "123456789"→0xF4 等 3 个标准向量兜底校验实现本身。
 * 返回 0 = 通过，-1 = 失败（说明本文件被改坏了，需立即排查）。 */
int ecg_crc8_selftest(void);

#endif /* ECG_CRC8_H */
