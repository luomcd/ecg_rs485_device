#include "waveshare_rgb_lcd_port.h"

#include "freertos/FreeRTOS.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"   /* ESP-IDF v6.x: legacy driver/i2c.h is EOL, removed in v7.0 */
#include "esp_lcd_panel_io.h"
#include "esp_lcd_touch_gt911.h"
#include "esp_log.h"
#include "esp_rom_sys.h"

static const char *TAG = "lcd_port";

/* CH422G exposes two I2C addresses: 0x24 = system/mode register, 0x38 = I/O output register.
 * The new i2c_master driver works per-device, so one handle per address is required. */
#define CH422G_MODE_ADDR  0x24
#define CH422G_OUT_ADDR   0x38

static i2c_master_bus_handle_t s_i2c_bus = NULL;
static i2c_master_dev_handle_t s_ch422g_mode_dev = NULL;
static i2c_master_dev_handle_t s_ch422g_out_dev = NULL;

static esp_err_t i2c_add_dev(uint8_t addr, i2c_master_dev_handle_t *out)
{
    const i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = addr,
        .scl_speed_hz = I2C_MASTER_FREQ_HZ,
    };
    return i2c_master_bus_add_device(s_i2c_bus, &dev_cfg, out);
}

static esp_err_t i2c_master_init_if_needed(void)
{
    if (s_i2c_bus != NULL) {
        return ESP_OK;
    }

    const i2c_master_bus_config_t bus_cfg = {
        .i2c_port = I2C_MASTER_NUM,
        .sda_io_num = I2C_MASTER_SDA_IO,
        .scl_io_num = I2C_MASTER_SCL_IO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };

    esp_err_t ret = i2c_new_master_bus(&bus_cfg, &s_i2c_bus);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2c_new_master_bus failed: %s", esp_err_to_name(ret));
        return ret;
    }

    ret = i2c_add_dev(CH422G_MODE_ADDR, &s_ch422g_mode_dev);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "add dev 0x%02x failed: %s", CH422G_MODE_ADDR, esp_err_to_name(ret));
        return ret;
    }

    ret = i2c_add_dev(CH422G_OUT_ADDR, &s_ch422g_out_dev);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "add dev 0x%02x failed: %s", CH422G_OUT_ADDR, esp_err_to_name(ret));
        return ret;
    }

    return ESP_OK;
}

/* 0x38 输出寄存器的软件影子副本。
 *
 * 为什么必须有：CH422G 的输出寄存器**硬件不可回读** —— Waveshare 官方驱动
 * esp_io_expander_ch422g.c:216-223 的 read_output_reg() 一行 I2C 都没有，
 * 只是返回 ch422g->regs.wr_io 这个软件缓存。所以「先读回来改一位」做不到，
 * 只能自己记住上次写了什么。
 *
 * 初值 0x1E = backlight_on() 写入的稳态值（bit1 CTP_RST | bit2 LCD_BL |
 * bit3 LCD_RST | bit4 SD_CS=释放）。所有对 0x38 的写都会经 ch422g_write()
 * 自动同步，包括 touch_reset 的 0x2C/0x2E 两步。 */
static uint8_t s_out_shadow = 0x1E;

static esp_err_t ch422g_write(i2c_master_dev_handle_t dev, uint8_t value)
{
    esp_err_t ret = i2c_master_transmit(dev, &value, 1, I2C_MASTER_TIMEOUT_MS);
    if (ret == ESP_OK && dev == s_ch422g_out_dev) {
        s_out_shadow = value;   /* 只有写 0x38 才更新影子；写 0x24(mode) 不动 */
    }
    return ret;
}

static esp_err_t ch422g_init_for_output(void)
{
    return ch422g_write(s_ch422g_mode_dev, 0x01);
}

static esp_err_t touch_reset_gpio_init(void)
{
    const gpio_config_t io_conf = {
        .pin_bit_mask = 1ULL << EXAMPLE_TOUCH_RESET_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };

    return gpio_config(&io_conf);
}

static esp_err_t waveshare_touch_reset(void)
{
    ESP_ERROR_CHECK(ch422g_init_for_output());
    ESP_ERROR_CHECK(touch_reset_gpio_init());

    ESP_ERROR_CHECK(ch422g_write(s_ch422g_out_dev, 0x2C));
    esp_rom_delay_us(100 * 1000);
    gpio_set_level(EXAMPLE_TOUCH_RESET_GPIO, 0);
    esp_rom_delay_us(100 * 1000);
    ESP_ERROR_CHECK(ch422g_write(s_ch422g_out_dev, 0x2E));
    esp_rom_delay_us(200 * 1000);

    return ESP_OK;
}

esp_err_t waveshare_rgb_lcd_backlight_on(void)
{
    ESP_ERROR_CHECK(i2c_master_init_if_needed());
    ESP_ERROR_CHECK(ch422g_init_for_output());
    return ch422g_write(s_ch422g_out_dev, 0x1E);
}

/* ============================================================================
 * CH422G 位操作 API —— 供 SD 片选等上层功能使用
 * ========================================================================== */

/* 位掩码，与 waveshare_rgb_lcd_port.h 的注释表对应 */
#define CH422G_BIT_CTP_RST  (1u << 1)
#define CH422G_BIT_LCD_BL   (1u << 2)
#define CH422G_BIT_LCD_RST  (1u << 3)
#define CH422G_BIT_SD_CS    (1u << 4)
#define CH422G_BIT_USB_SEL  (1u << 5)

#define CH422G_OUT_STEADY   0x1E    /* 三复位释放 + SD_CS 释放 */

esp_err_t waveshare_ch422g_set_out_bits(uint8_t clr_mask, uint8_t set_mask)
{
    esp_err_t ret = i2c_master_init_if_needed();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "set_out_bits: i2c init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    /* 写 0x38 之前必须先确认 0x24 的输出模式（厂家序列就是 0x24<-0x01）。
     * 这一步幂等，重复写无副作用。 */
    ret = ch422g_init_for_output();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "set_out_bits: mode init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    const uint8_t value = (uint8_t)((s_out_shadow & (uint8_t)~clr_mask) | set_mask);

    ret = ch422g_write(s_ch422g_out_dev, value);
    if (ret == ESP_OK) {
        ESP_LOGD(TAG, "ch422g out: 0x%02X -> 0x%02X (clr=0x%02X set=0x%02X)",
                 s_out_shadow, value, clr_mask, set_mask);
    } else {
        ESP_LOGE(TAG, "set_out_bits: write 0x%02X failed: %s", value, esp_err_to_name(ret));
    }
    return ret;
}

esp_err_t waveshare_ch422g_sd_cs(bool select)
{
    /* SD_CS = bit4：选中 = 清 bit4（0x1E -> 0x0E）；释放 = 置 bit4（0x0E -> 0x1E）。
     * ⚠ 必须以 0x1E 为基准做位运算，不能从 0x2E 派生（0x2E 的 bit5=1，
     *   会带上 USB_SEL）。 */
    return select
           ? waveshare_ch422g_set_out_bits((uint8_t)CH422G_BIT_SD_CS, 0)
           : waveshare_ch422g_set_out_bits(0, (uint8_t)CH422G_BIT_SD_CS);
}

uint8_t waveshare_ch422g_get_out_shadow(void)
{
    return s_out_shadow;
}

esp_err_t waveshare_esp32_s3_rgb_lcd_init(esp_lv_adapter_tear_avoid_mode_t tear_mode,
                                          esp_lv_adapter_rotation_t rotation,
                                          esp_lcd_panel_handle_t *panel_handle,
                                          esp_lcd_touch_handle_t *touch_handle)
{
    if (panel_handle == NULL || touch_handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    *panel_handle = NULL;
    *touch_handle = NULL;

    const uint8_t num_fbs = esp_lv_adapter_get_required_frame_buffer_count(tear_mode, rotation);

    ESP_LOGI(TAG, "Creating RGB panel, frame buffers: %u", num_fbs);
    esp_lcd_rgb_panel_config_t panel_config = {
        .clk_src = LCD_CLK_SRC_DEFAULT,
        .timings = {
            .pclk_hz = EXAMPLE_LCD_PIXEL_CLOCK_HZ,
            .h_res = EXAMPLE_LCD_H_RES,
            .v_res = EXAMPLE_LCD_V_RES,
#if EXAMPLE_USE_1024_600_LCD
            .hsync_back_porch = 145,
            .hsync_front_porch = 170,
            .hsync_pulse_width = 30,
            .vsync_back_porch = 23,
            .vsync_front_porch = 12,
            .vsync_pulse_width = 2,
#else
            .hsync_pulse_width = 4,
            .hsync_back_porch = 8,
            .hsync_front_porch = 8,
            .vsync_pulse_width = 4,
            .vsync_back_porch = 8,
            .vsync_front_porch = 8,
#endif
            .flags = {
                .pclk_active_neg = 1,
            },
        },
        .data_width = EXAMPLE_RGB_DATA_WIDTH,
        .in_color_format = LCD_COLOR_FMT_RGB565,   /* v6.x: replaces bits_per_pixel; out_color_format follows in_color_format */
        .num_fbs = num_fbs,
        .bounce_buffer_size_px = EXAMPLE_RGB_BOUNCE_BUFFER_SIZE,
        .dma_burst_size = 64,                      /* v6.x: replaces sram_trans_align=4 / psram_trans_align=64 */
        .hsync_gpio_num = EXAMPLE_LCD_IO_RGB_HSYNC,
        .vsync_gpio_num = EXAMPLE_LCD_IO_RGB_VSYNC,
        .de_gpio_num = EXAMPLE_LCD_IO_RGB_DE,
        .pclk_gpio_num = EXAMPLE_LCD_IO_RGB_PCLK,
        .disp_gpio_num = EXAMPLE_LCD_IO_RGB_DISP,
        .data_gpio_nums = {
            EXAMPLE_LCD_IO_RGB_DATA0,
            EXAMPLE_LCD_IO_RGB_DATA1,
            EXAMPLE_LCD_IO_RGB_DATA2,
            EXAMPLE_LCD_IO_RGB_DATA3,
            EXAMPLE_LCD_IO_RGB_DATA4,
            EXAMPLE_LCD_IO_RGB_DATA5,
            EXAMPLE_LCD_IO_RGB_DATA6,
            EXAMPLE_LCD_IO_RGB_DATA7,
            EXAMPLE_LCD_IO_RGB_DATA8,
            EXAMPLE_LCD_IO_RGB_DATA9,
            EXAMPLE_LCD_IO_RGB_DATA10,
            EXAMPLE_LCD_IO_RGB_DATA11,
            EXAMPLE_LCD_IO_RGB_DATA12,
            EXAMPLE_LCD_IO_RGB_DATA13,
            EXAMPLE_LCD_IO_RGB_DATA14,
            EXAMPLE_LCD_IO_RGB_DATA15,
        },
        .flags = {
            .fb_in_psram = 1,
        },
    };

    ESP_ERROR_CHECK(esp_lcd_new_rgb_panel(&panel_config, panel_handle));
    ESP_ERROR_CHECK(esp_lcd_panel_init(*panel_handle));

#if EXAMPLE_USE_TOUCH
    ESP_ERROR_CHECK(i2c_master_init_if_needed());
    ESP_ERROR_CHECK(waveshare_touch_reset());

    esp_lcd_panel_io_handle_t tp_io_handle = NULL;
    esp_lcd_panel_io_i2c_config_t tp_io_config = ESP_LCD_TOUCH_IO_I2C_GT911_CONFIG();
    /**
     * v6.x signature: esp_lcd_new_panel_io_i2c(i2c_master_bus_handle_t bus, const esp_lcd_panel_io_i2c_config_t *, esp_lcd_panel_io_handle_t *)
     * The old (esp_lcd_i2c_bus_handle_t)port-number form no longer exists.
     */
    tp_io_config.scl_speed_hz = I2C_MASTER_FREQ_HZ;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_i2c(s_i2c_bus, &tp_io_config, &tp_io_handle));

    const esp_lcd_touch_config_t tp_cfg = {
        .x_max = EXAMPLE_LCD_H_RES,
        .y_max = EXAMPLE_LCD_V_RES,
        .rst_gpio_num = EXAMPLE_TOUCH_RST_GPIO,
        .int_gpio_num = EXAMPLE_TOUCH_INT_GPIO,
        .levels = {
            .reset = 0,
            .interrupt = 0,
        },
        .flags = {
            .swap_xy = 0,
            .mirror_x = 0,
            .mirror_y = 0,
        },
    };

    ESP_LOGI(TAG, "Creating GT911 touch controller");
    ESP_ERROR_CHECK(esp_lcd_touch_new_i2c_gt911(tp_io_handle, &tp_cfg, touch_handle));
#endif

    return ESP_OK;
}
