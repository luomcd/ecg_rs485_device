#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_rgb.h"
#include "esp_lcd_touch.h"
#include "esp_lv_adapter.h"

#ifdef __cplusplus
extern "C" {
#endif

#define EXAMPLE_USE_1024_600_LCD    0
#define EXAMPLE_USE_TOUCH           1

#if EXAMPLE_USE_1024_600_LCD
#define EXAMPLE_LCD_H_RES           1024
#define EXAMPLE_LCD_V_RES           600
#define EXAMPLE_LCD_PIXEL_CLOCK_HZ  (21 * 1000 * 1000)
#else
#define EXAMPLE_LCD_H_RES           800
#define EXAMPLE_LCD_V_RES           480
/* pclk 保持 16 MHz —— 2026-10-23 实测过 24 MHz，**反而更慢，不要改**。
 *
 * 实测（S4 画布场景，752×200 canvas，20 FPS 请求）：
 *     16 MHz ⇒ 面板读 PSRAM 32 MB/s，render 25.4 ms，**14.0 FPS**
 *     24 MHz ⇒ 面板读 PSRAM 48 MB/s，render 30.5 ms，**11.6 FPS**  ← 更差
 *
 * 为什么提 pclk 反而变慢：**PSRAM 带宽是面板扫描与 CPU 绘图共享的**。
 * RGB 面板通过 LCD_CAM 从 PSRAM 持续读 framebuffer，pclk 越高抢得越凶；
 * CPU 侧要读写 canvas buffer（同一块 PSRAM），带宽被挤压后自己的绘制反而变慢。
 *
 * ⇒ 帧率的瓶颈是 PSRAM 带宽，不是面板扫描速率。要靠减少每帧 PSRAM 访问量来优化，
 *   不是靠提 pclk。（详见 test_logs/CardioLite_S4_画布绘制测试报告_20261023.md §6） */
#define EXAMPLE_LCD_PIXEL_CLOCK_HZ  (16 * 1000 * 1000)
#endif

#define I2C_MASTER_SCL_IO           9
#define I2C_MASTER_SDA_IO           8
#define I2C_MASTER_NUM              0
#define I2C_MASTER_FREQ_HZ          400000
#define I2C_MASTER_TIMEOUT_MS       1000

/* NOTE(ESP-IDF v6.x): bits-per-pixel is no longer a separate field; the RGB panel config
 * now takes an explicit lcd_color_format_t. Kept here only for reference. */
#define EXAMPLE_RGB_BIT_PER_PIXEL   16
#define EXAMPLE_RGB_DATA_WIDTH      16
#define EXAMPLE_RGB_BOUNCE_LINES    10
#define EXAMPLE_RGB_BOUNCE_BUFFER_SIZE (EXAMPLE_LCD_H_RES * EXAMPLE_RGB_BOUNCE_LINES)

#define EXAMPLE_LCD_IO_RGB_DISP     (-1)
#define EXAMPLE_LCD_IO_RGB_VSYNC    (GPIO_NUM_3)
#define EXAMPLE_LCD_IO_RGB_HSYNC    (GPIO_NUM_46)
#define EXAMPLE_LCD_IO_RGB_DE       (GPIO_NUM_5)
#define EXAMPLE_LCD_IO_RGB_PCLK     (GPIO_NUM_7)
#define EXAMPLE_LCD_IO_RGB_DATA0    (GPIO_NUM_14)
#define EXAMPLE_LCD_IO_RGB_DATA1    (GPIO_NUM_38)
#define EXAMPLE_LCD_IO_RGB_DATA2    (GPIO_NUM_18)
#define EXAMPLE_LCD_IO_RGB_DATA3    (GPIO_NUM_17)
#define EXAMPLE_LCD_IO_RGB_DATA4    (GPIO_NUM_10)
#define EXAMPLE_LCD_IO_RGB_DATA5    (GPIO_NUM_39)
#define EXAMPLE_LCD_IO_RGB_DATA6    (GPIO_NUM_0)
#define EXAMPLE_LCD_IO_RGB_DATA7    (GPIO_NUM_45)
#define EXAMPLE_LCD_IO_RGB_DATA8    (GPIO_NUM_48)
#define EXAMPLE_LCD_IO_RGB_DATA9    (GPIO_NUM_47)
#define EXAMPLE_LCD_IO_RGB_DATA10   (GPIO_NUM_21)
#define EXAMPLE_LCD_IO_RGB_DATA11   (GPIO_NUM_1)
#define EXAMPLE_LCD_IO_RGB_DATA12   (GPIO_NUM_2)
#define EXAMPLE_LCD_IO_RGB_DATA13   (GPIO_NUM_42)
#define EXAMPLE_LCD_IO_RGB_DATA14   (GPIO_NUM_41)
#define EXAMPLE_LCD_IO_RGB_DATA15   (GPIO_NUM_40)

#define EXAMPLE_TOUCH_RESET_GPIO    GPIO_NUM_4
#define EXAMPLE_TOUCH_INT_GPIO      (-1)
#define EXAMPLE_TOUCH_RST_GPIO      (-1)

esp_err_t waveshare_esp32_s3_rgb_lcd_init(esp_lv_adapter_tear_avoid_mode_t tear_mode,
                                          esp_lv_adapter_rotation_t rotation,
                                          esp_lcd_panel_handle_t *panel_handle,
                                          esp_lcd_touch_handle_t *touch_handle);

esp_err_t waveshare_rgb_lcd_backlight_on(void);

/* ============================================================================
 * CH422G 扩展 IO 位操作（2026-10-23 为 SD 片选新增）
 *
 * 位定义（原理图确认）：
 *   bit1 EXIO1 = CTP_RST   稳态 1
 *   bit2 EXIO2 = LCD_BL    稳态 1
 *   bit3 EXIO3 = LCD_RST   稳态 1
 *   bit4 EXIO4 = SD_CS     1=释放，0=选中
 *   bit5 EXIO5 = USB_SEL
 *   bit0        未知，实测恒 0
 *
 * 稳态值 0x1E（三复位释放 + SD 未选中）= backlight_on() 写入值。
 *
 * ⚠ 0x38 输出寄存器**硬件不可回读**（Waveshare 官方驱动的 read_output_reg()
 *   完全没有 I2C 读动作，只返回软件缓存），所以本模块内部维护影子副本。
 *   业务代码**禁止裸写 0x0E / 0x1E**，一律走下面的位操作 API。
 * ========================================================================== */

/**
 * @brief 读-改-写 CH422G 的 0x38 输出寄存器。
 * @param clr_mask 要清零的位（掩码中为 1 的位被清 0）
 * @param set_mask 要置位的位（掩码中为 1 的位被置 1）
 * @note  set_mask 优先于 clr_mask。返回值来自真实 I2C 写，失败时影子副本不变。
 */
esp_err_t waveshare_ch422g_set_out_bits(uint8_t clr_mask, uint8_t set_mask);

/**
 * @brief 控制 SD 卡片选（EXIO4 / bit4）。
 * @param select true = 选中（写 0x0E）；false = 释放（写 0x1E）
 */
esp_err_t waveshare_ch422g_sd_cs(bool select);

/**
 * @brief 取 CH422G 输出寄存器影子副本（仅诊断/日志用，不代表硬件真值）。
 */
uint8_t waveshare_ch422g_get_out_shadow(void);

#ifdef __cplusplus
}
#endif
