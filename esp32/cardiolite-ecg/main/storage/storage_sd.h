/**
 * @file storage_sd.h
 * @brief SD 卡挂载与文件访问（S1）
 *
 * 关键事实（详见实施计划 §4.6 / §12.5.1）：
 *   - SD 的 CS 不是 GPIO，接在 CH422G 的 EXIO4(bit4)。⇒ sdspi 的 gpio_cs 必须
 *     用 SDSPI_SLOT_NO_CS，片选靠 I2C 写 0x38。
 *   - SDSPI_DEVICE_CONFIG_DEFAULT() 的 gpio_cs 默认是 GPIO_NUM_13，
 *     **必须显式覆盖**，否则会去驱动 GPIO13（正好是 SD 的 MISO）。
 *   - 0x38 输出寄存器硬件不可回读，位操作走 BSP 的影子副本 API。
 */
#pragma once

#include <stdbool.h>
#include <stdio.h>

#include "esp_err.h"

/**
 * @brief 挂载 SD 卡到 /sdcard（FAT32）。
 *
 * 内部顺序：CH422G 选中 SD（0x0E）→ SPI2 初始化 → sdspi 挂载。
 * format_if_mount_failed 固定为 false：挂载失败**不会**格式化，
 * 不能因为一次接触不良就把卡里的数据洗掉。
 */
esp_err_t storage_sd_mount(void);

/** @brief 卸载并释放 SPI 总线（S1 自检用；正常运行时不需要） */
esp_err_t storage_sd_unmount(void);

/** @brief 是否已挂载 */
bool storage_sd_is_mounted(void);

/**
 * @brief S1 验收用：打开 /sdcard/ecg.bin，打印大小与首帧帧头。
 *
 * 校验：文件存在、可读、前两字节为 AA 55、LEN 字段落在 52/53/103 之一。
 * 不解析后续帧（那是 S2 的事）。
 */
esp_err_t storage_sd_probe_ecg_file(void);

/**
 * @brief 最近一次挂载/探测的结果摘要（供 UI 显示，最长 128 字节）。
 *
 * 形如 `mounted | ecg.bin 105964 B | LEN=53 59B/frame x1796`
 * 或   `NOT MOUNTED | mount failed 0x105`。
 */
const char *storage_sd_last_summary(void);

/**
 * @brief 删除 /sdcard/ecg.bin（长按「回放」确认后调用）。
 * @return 0 = 成功；非 0 = 失败（errno）
 * @note 只删这一个文件，不格式化、不碰卡上其他内容。
 */
int storage_sd_delete_ecg_file(void);

/* ============================================================================
 * 文件扫描：按首帧 LEN 把卡上的 .bin 分成「单通道」和「双通道」两类
 *
 * 为什么需要：需求 §2.2 规定数据文件名固定为 ecg.bin，所以同一时刻卡上只有一个
 * 主数据文件。但双通道（LEN=103）与单通道（LEN=53）的帧结构不同，
 * 切「通道 I / II」时要能各取所需 —— 于是启动时扫一遍目录、按 LEN 分类。
 * ========================================================================== */

#define SD_PATH_MAX 80

typedef struct {
    char     single[SD_PATH_MAX];   /* 第一个 LEN=52/53 的文件（优先 ecg.bin）；空串 = 没有 */
    char     dual[SD_PATH_MAX];     /* **最大**的 LEN=103 文件（时长最长）；空串 = 没有 */
    uint32_t single_sec;            /* 单通道文件时长（秒），0 = 未知 */
    uint32_t dual_sec;              /* 双通道文件时长（秒），0 = 未知 */
} storage_sd_files_t;

/** @brief 扫描 /sdcard 下的 .bin，按首帧 LEN 分类（挂载后调用一次） */
esp_err_t storage_sd_scan_files(void);

/** @brief 取扫描结果（永不返回 NULL） */
const storage_sd_files_t *storage_sd_get_files(void);
