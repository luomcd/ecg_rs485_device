/**
 * @file rs485_port.c
 * @brief RS485 传输层实现（S9a）—— 裁剪版，无后台任务、无行命令。
 *
 * 与 ESP-HRV 版本的关键差异（**改这里之前先读 app_config.h §十 的坑**）：
 *   1. 没有 rx_task：UART 由 producer 任务按需 rs485_read() 拉取。
 *      两个消费者会抢数据（ESP-HRV 的 CLI 与后台任务就踩过），这里只有一条读路径。
 *   2. 没有行命令：总线上只应有 T101 帧，不再回 PING/PONG/HELLO。
 *   3. 事件队列改为在 get_stat()/read() 时 poll —— 无后台任务时没人消费，
 *      20 深度很快堆满，溢出统计会丢。
 */

#include "rs485_port.h"

#include <string.h>

#include "driver/uart.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#include "app_config.h"

static const char *TAG = "rs485";

static bool          s_ready = false;
static uint32_t      s_baud  = APP_485_BAUD_DEFAULT;
static QueueHandle_t s_evt_q = NULL;
static rs485_stat_t  s_stat;

/* 把队列里积压的事件全部吃掉，累计到统计里。
 * 无后台任务时这是唯一消费点，read/get_stat 都要过一遍。 */
static void poll_events(void)
{
    if (s_evt_q == NULL) {
        return;
    }
    uart_event_t ev;
    while (xQueueReceive(s_evt_q, &ev, 0) == pdTRUE) {
        switch (ev.type) {
        case UART_FIFO_OVF:
        case UART_BUFFER_FULL:
            s_stat.fifo_ovf++;
            uart_flush_input(APP_485_UART_NUM);
            break;
        case UART_PARITY_ERR:
            s_stat.parity_err++;
            break;
        case UART_FRAME_ERR:
            s_stat.frame_err++;
            break;
        default:
            break;
        }
    }
}

esp_err_t rs485_init(uint32_t baud)
{
    if (s_ready) {
        return ESP_OK;
    }
    const uint32_t b = (baud == 0) ? APP_485_BAUD_DEFAULT : baud;

    const uart_config_t cfg = {
        .baud_rate  = (int)b,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    /* 🛑 顺序：先 param_config + set_pin，再 driver_install。
     * UART1 的默认引脚 GPIO17/18 是本板 LCD 数据线，若先 install 再 set_pin，
     * 中间那一瞬可能把 LCD 数据线拉成 UART 输出（ESP-HRV 实测记下的坑）。 */
    esp_err_t err = uart_param_config(APP_485_UART_NUM, &cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "param_config: %s", esp_err_to_name(err));
        return err;
    }

    /* 🛑 tx/rx 交叉：IO44 接收发器 DI、IO43 接收发器 RO，而 UART0 默认是 T43/R44。
     * 这里显式指定，别改成"看起来更顺"的对调。 */
    err = uart_set_pin(APP_485_UART_NUM, APP_485_TX_GPIO, APP_485_RX_GPIO,
                       UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "set_pin: %s", esp_err_to_name(err));
        return err;
    }

    if (s_evt_q == NULL) {
        s_evt_q = xQueueCreate(APP_485_EVT_QUEUE, sizeof(uart_event_t));
        if (s_evt_q == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }

    err = uart_driver_install(APP_485_UART_NUM, APP_485_RX_BUF_SIZE,
                              APP_485_TX_BUF_SIZE, APP_485_EVT_QUEUE, &s_evt_q, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "driver_install: %s", esp_err_to_name(err));
        return err;
    }

    s_baud  = b;
    s_ready = true;
    uart_flush(APP_485_UART_NUM);
    memset(&s_stat, 0, sizeof s_stat);

    ESP_LOGI(TAG, "UART%d ready: TX=IO%d RX=IO%d @%lu 8N1",
             APP_485_UART_NUM, APP_485_TX_GPIO, APP_485_RX_GPIO, (unsigned long)b);
    return ESP_OK;
}

bool rs485_ready(void)
{
    return s_ready;
}

esp_err_t rs485_set_baud(uint32_t baud)
{
    if (!s_ready || baud == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    const esp_err_t err = uart_set_baudrate(APP_485_UART_NUM, baud);
    if (err == ESP_OK) {
        s_baud = baud;
    }
    return err;
}

uint32_t rs485_baud(void)
{
    return s_baud;
}

esp_err_t rs485_send(const uint8_t *data, size_t len)
{
    if (!s_ready || data == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    const int n = uart_write_bytes(APP_485_UART_NUM, (const char *)data, len);
    if (n < 0) {
        return ESP_FAIL;
    }
    /* 等 TX 真正发完：自动方向电路在最后一个停止位后才切回接收，
     * 早返回会让调用方以为"发完了"立刻去读，容易读到半截。 */
    uart_wait_tx_done(APP_485_UART_NUM, pdMS_TO_TICKS(100));
    s_stat.tx_bytes += (uint32_t)n;
    return ESP_OK;
}

int rs485_read(uint8_t *buf, size_t max, uint32_t timeout_ms)
{
    if (!s_ready || buf == NULL || max == 0) {
        return -1;
    }
    const int n = uart_read_bytes(APP_485_UART_NUM, buf, max, pdMS_TO_TICKS(timeout_ms));
    if (n > 0) {
        s_stat.rx_bytes += (uint32_t)n;
        s_stat.rx_chunks++;
    }
    poll_events();
    return n;
}

void rs485_get_stat(rs485_stat_t *out)
{
    poll_events();
    if (out != NULL) {
        *out = s_stat;
    }
}

void rs485_reset_stat(void)
{
    memset(&s_stat, 0, sizeof s_stat);
}
