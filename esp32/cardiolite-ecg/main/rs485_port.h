/**
 * @file rs485_port.h
 * @brief RS485 传输层（S9a）—— 板载 SP3485EN，UART1 收发，方向硬件自动切换。
 *
 * 移植自 `C:\work2026\ESP-HRV\ADS1293-HRV-Node\main\rs485_port.c`（同板实测跑通），
 * **裁剪**掉测试工程专用的部分：
 *   - 行命令解析 / PING-PONG / 空闲提示 —— 那是给"裸串口助手"用的交互面，
 *     CardioLite 有自己的 CLI 与 UI，总线应保持干净（只有数据帧）；
 *   - 后台 rx_task —— ESP-HRV 用它持续消费；本工程由 producer 任务按需阻塞读，
 *     **两个消费者会抢数据**，所以这里只提供同步读接口；
 *   - rs485_selftest_internal / rs485_send_pattern —— 芯片内 loopback 在 S3 上
 *     实测收不到字节；pattern 是示波器场景，S9a 用不上（需要时从原工程取回）。
 *
 * 🛑 本电路不能 A/B 短接自环：RE 低有效且与 DE 短接，发送期间接收器关闭，
 *    自己发的自己收不到。外部验证必须有第二个节点（PC 的 USB-RS485 转换器）。
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t rx_bytes;    /* 收到的总字节 */
    uint32_t tx_bytes;    /* 发出的总字节 */
    uint32_t rx_chunks;   /* "读到一段数据"的次数 */
    uint32_t parity_err;  /* 校验错（8N1 下不应出现） */
    uint32_t frame_err;   /* 帧错（停止位不对） */
    uint32_t fifo_ovf;    /* 硬件 FIFO / 驱动缓冲溢出 */
} rs485_stat_t;

/**
 * @brief 初始化 UART1 并装驱动（**不创建后台任务**）。
 * @param baud 波特率（0 = 用 APP_485_BAUD_DEFAULT）
 * @note 顺序：param_config → set_pin → driver_install（见 app_config.h §十）。
 */
esp_err_t rs485_init(uint32_t baud);

/** @brief 是否已初始化成功 */
bool rs485_ready(void);

esp_err_t rs485_set_baud(uint32_t baud);
uint32_t  rs485_baud(void);

/** @brief 发送（等 TX 发完才返回；自动方向电路在最后停止位后才切回接收） */
esp_err_t rs485_send(const uint8_t *data, size_t len);

/**
 * @brief 阻塞读。
 * @return 实际读到的字节数（0 = 超时无数据），负数 = 错误
 */
int rs485_read(uint8_t *buf, size_t max, uint32_t timeout_ms);

/** @brief 取统计快照（顺带 poll 掉 UART 事件队列，否则 20 深度会积压） */
void rs485_get_stat(rs485_stat_t *out);

/** @brief 清统计（每轮测试开始时清零，便于对账） */
void rs485_reset_stat(void);

#ifdef __cplusplus
}
#endif
