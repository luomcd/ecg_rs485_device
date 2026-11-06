idf_component_register(
    SRCS
        "main.c"
        "app_state.c"
        "cli_console.c"
        "test_mode.c"
        "storage/storage_sd.c"
        "rs485_port.c"
        "ecg/ecg_frame.c"
        "ecg/ecg_stream.c"
        "ecg/ecg_ringbuf.c"
        "ecg/ecg_pipeline.c"
        "ecg/ecg_rpeak.c"
        "ecg/ecg_batch.c"
        "ecg/ecg_cmd.c"
        "ecg/rp_selftest_data.c"
        "ui/ui_theme.c"
        "ui/ui_waveform.c"
        "ui/ui_bpm.c"
        "ui/ui_button_bar.c"
        # 中文子集字库（tools/gen_fonts.sh 生成；改字符集后要重新生成）
        # 单行按钮只用 13 px，其余尺寸先不编（省 flash 与编译时间）。
        # 需要 10/12/14 px 时把对应行取消注释 —— 三个文件都已生成好，在 ui/font/ 下。
        "ui/font/font_zh_13.c"
        # "ui/font/font_zh_10.c"
        # "ui/font/font_zh_12.c"
        # "ui/font/font_zh_14.c"
    INCLUDE_DIRS
        "."
        "storage"
        "ecg"
        "ui"
    REQUIRES
        bsp_ws_lcd5
        lvgl
        esp_lvgl_adapter
        esp_lcd
        esp_psram
        esp_timer
        # S1：SD 卡（SPI 模式 + FAT）
        fatfs
        vfs
        sdmmc
        esp_driver_sdspi
        esp_driver_spi
        # S1（CLI）：console（内建 linenoise 行编辑 + argtable3 参数解析）
        # + UART/USB-JTAG 驱动（stdio REPL 走串口需要）。
        # 不加 nvs_flash —— 批处理标记走 SD 文件，不用 NVS。
        console
        esp_driver_uart
        esp_driver_usb_serial_jtag
)

# LVGL 自身的 printf 格式告警（32 位 lv_coord_t 与 %d 混用）不修正，只压制。
idf_build_get_property(build_components BUILD_COMPONENTS)
if("lvgl" IN_LIST build_components)
    idf_component_get_property(lvgl_lib lvgl COMPONENT_LIB)
elseif("lvgl__lvgl" IN_LIST build_components)
    idf_component_get_property(lvgl_lib lvgl__lvgl COMPONENT_LIB)
endif()

if(lvgl_lib)
    target_compile_options(${lvgl_lib} PRIVATE -Wno-format)
endif()
