# CardioLite ECG 显示终端 —— 正式 UI 工程
#
# 板卡: Waveshare ESP32-S3-Touch-LCD-5 (800x480 SKU)
# 平台: ESP-IDF v6.1.0 + LVGL 8.4.0 + esp_lvgl_adapter 0.5.3
# 板级驱动: ../bsp_ws_lcd5（共享组件，与 lcd-bringup 共用同一份）
#
# 构建:  ../tools/idf.sh -C C:/work2026/CardioLite/cardiolite-ecg build
# 烧录:  由猛哥在 VS Code 里执行（本项目只编不烧）
#
# 注意: 必须走 tools/idf.sh —— Git Bash 的 MSYSTEM 会让 idf.py 静默空跑。

cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../bsp_ws_lcd5")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# 压制厂家 BSP 与 esp_lcd 里的 attribute 警告（与 lcd-bringup 一致）
add_compile_options("-Wno-attributes")

project(cardiolite_terminal)
