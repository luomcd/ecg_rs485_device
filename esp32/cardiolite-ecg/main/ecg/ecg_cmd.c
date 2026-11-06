/**
 * @file ecg_cmd.c
 * @brief 下行命令层实现（T208）—— 拼帧 + CRC + 交给 `rs485_send()`。
 *
 * 依赖的都是**现成的**件，本文件不新写协议：
 *   - CRC8：`ecg_crc8()`（`ecg_frame.c:18`，poly 0x07 / init 0x00 / 不反射）
 *   - 发送：`rs485_send()`（`rs485_port.c:138`，内部已 `uart_wait_tx_done`）
 *     485 模块是**自动方向**（最后停止位后才切回接收）⇒ 不需要 DE/RE 引脚。
 */

#include "ecg/ecg_cmd.h"

#include <string.h>

#include "esp_log.h"

#include "ecg/ecg_frame.h"     /* ecg_crc8() */
#include "rs485_port.h"

static const char *TAG = "ecg_cmd";

/* 帧头 3 B + LEN 2 B + (CMD+ARG) + CRC 1 B */
#define ECG_CMD_HDR_LEN   3u
#define ECG_CMD_LENLEN    2u
#define ECG_CMD_CRC_LEN   1u

esp_err_t ecg_cmd_send(uint8_t cmd, const uint8_t *arg, uint8_t arg_len)
{
    if (arg_len > ECG_CMD_MAX_ARG) {
        ESP_LOGE(TAG, "arg_len %u > %u", (unsigned)arg_len, (unsigned)ECG_CMD_MAX_ARG);
        return ESP_ERR_INVALID_ARG;
    }
    if ((arg_len > 0u) && (arg == NULL)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!rs485_ready()) {
        /* 🛑 不静默失败：现场最容易把它误判成"固件没反应"或"线路不通"，
         *    实际只是还没敲 `485-start`。 */
        ESP_LOGE(TAG, "485 未初始化（先执行 485-start）");
        return ESP_ERR_INVALID_STATE;
    }

    uint8_t buf[ECG_CMD_HDR_LEN + ECG_CMD_LENLEN + 1u + ECG_CMD_MAX_ARG + ECG_CMD_CRC_LEN];

    /* LEN = CMD(1) + ARG(N)；固件按 `6 + LEN == 实收长度` 对核（`vcomcmd.c:629`） */
    const uint8_t len = (uint8_t)(1u + arg_len);

    buf[0] = 0xAAu;
    buf[1] = 0x55u;
    buf[2] = (uint8_t)ECG_CMD_TYPE;
    buf[3] = (uint8_t)(len & 0xFFu);          /* LEN 低字节（LE） */
    buf[4] = (uint8_t)(len >> 8);             /* LEN 高字节（LE） */
    buf[5] = cmd;

    if (arg_len > 0u) {
        memcpy(&buf[6], arg, arg_len);
    }

    /* CRC 覆盖 TYPE + LEN(2) + PAYLOAD(len)，即从下标 2 起 (3 + len) 字节 */
    const size_t total = (size_t)(6u + len);
    buf[total - 1u] = ecg_crc8(&buf[2], (size_t)(3u + len));

    /* 自检：总长必须 ≥7（固件 `vcomcmd.c:613` 的下限），否则直接不发 */
    if (total < 7u) {
        ESP_LOGE(TAG, "帧长 %u < 7，固件会丢", (unsigned)total);
        return ESP_ERR_INVALID_SIZE;
    }

    if (rs485_send(buf, total) != ESP_OK) {
        ESP_LOGE(TAG, "rs485_send 失败（cmd=0x%02X）", cmd);
        return ESP_FAIL;
    }

    /* 只在 DEBUG 级别打，避免刷屏；要现场取证用 `485-send` 或抓总线 */
    ESP_LOGD(TAG, "tx cmd=0x%02X len=%u total=%u", cmd, (unsigned)len, (unsigned)total);
    return ESP_OK;
}

esp_err_t ecg_cmd_start(void)
{
    /* 🛑 只发一帧：`0x01` 不幂等（重发 ⇒ 重走采集链 ⇒ 基线瞬态），
     *    与"连发 3 次抗丢"的常规做法**相反** —— 那条只适用于幂等命令。
     *    依据：`ads1293.c:843`、T206 §8.2。 */
    const esp_err_t err = ecg_cmd_send(ECG_CMD_START, NULL, 0u);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "START 已发（隐式 ACK = 显示帧流起来；不回快照）");
    }
    return err;
}

esp_err_t ecg_cmd_stop(void)
{
    /* 幂等 ⇒ 可以连发，但本层只发一帧：调用方（UI/CLI）自己决定要不要重复。 */
    return ecg_cmd_send(ECG_CMD_STOP, NULL, 0u);
}

esp_err_t ecg_cmd_snapshot(void)
{
    /* ★T208（11-05）：读快照。整帧 AA 55 80 01 00 10 2A。
     * 🛑 只发一帧：STM32 侧这条**幂等且只读**（`vcomcmd.c:646` 只调
     *    `ads1293_send_snapshot()`，不改配置、不动流），但回执是 80 B，
     *    连发会让快照帧挤占上行带宽 —— 要看多次就由 CLI 侧分次发。 */
    const esp_err_t err = ecg_cmd_send(ECG_CMD_SNAPSHOT, NULL, 0u);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "SNAPSHOT(0x10) 已发（仅 STOP 态有回执；看 485-stat 的 snapshots）");
    }
    return err;
}

/* ---- ★T209（11-05）：三条带 ARG 的参数命令 ----
 *
 * 三条共用一个私有实现：都是「1 B ARG」、都走 `ecg_cmd_send(cmd, &arg, 1)`。
 * 🛑 LEN 由 `ecg_cmd_send` 自动算成 `1 + 1 = 2` —— 正好是固件的长度门要求，
 *   不要再手工传 arg_len=2（那会拼出 LEN=3，被 `argLen != 2u` 静默丢掉）。
 */
static esp_err_t send_arg1(uint8_t cmd, uint8_t arg, const char *name)
{
    const esp_err_t err = ecg_cmd_send(cmd, &arg, 1u);
    if (err == ESP_OK) {
        /* 只打 INFO（该级别默认关），真正给人看的是 CLI 侧的回读 */
        ESP_LOGI(TAG, "%s(0x%02X) 已发 arg=%u", name, cmd, (unsigned)arg);
    }
    return err;
}

esp_err_t ecg_cmd_set_mode(uint8_t on)
{
    /* 0=诊断 1=监护。固件 `ecg_filter_f64_set_mode()` 内部再归一到 0/1，
     * 传 2 也不会炸，但本层先收口，免得日志里出现一个不存在的档位。 */
    return send_arg1(ECG_CMD_SET_MODE, (uint8_t)(on ? 1u : 0u), "SET_MODE");
}

esp_err_t ecg_cmd_set_notch(uint8_t on)
{
    return send_arg1(ECG_CMD_SET_NOTCH, (uint8_t)(on ? 1u : 0u), "SET_NOTCH");
}

esp_err_t ecg_cmd_set_channel(uint8_t ch2)
{
    /* 固件 `!=0 => I&II`，与 ASCII `test ch2 on|off` 共享同一真值源（`_bch2`）。 */
    return send_arg1(ECG_CMD_SET_CHANNEL, (uint8_t)(ch2 ? 1u : 0u), "SET_CHANNEL");
}
