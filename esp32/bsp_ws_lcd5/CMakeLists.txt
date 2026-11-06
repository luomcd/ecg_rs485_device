# BSP for Waveshare ESP32-S3-Touch-LCD-5 (800x480 RGB565 + GT911 + CH422G)
#
# Standalone component: any project can reuse it via
#   set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../bsp_ws_lcd5")
# and then `REQUIRES bsp_ws_lcd5`.
#
# Public deps are required because include/waveshare_rgb_lcd_port.h exposes
# esp_lcd_* and esp_lv_adapter_* types in its function signatures.

idf_component_register(
    SRCS "waveshare_rgb_lcd_port.c"
    INCLUDE_DIRS "include"
    REQUIRES
        esp_lcd
        esp_driver_gpio
        esp_driver_i2c
        esp_rom
        log
        esp_lcd_touch
        esp_lcd_touch_gt911
        esp_lvgl_adapter
)
