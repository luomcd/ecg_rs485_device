/**
 * @file ecg_batch.h
 * @brief R 峰检测器的**离线批处理**入口（MIT-BIH / 心率阶梯验证用，CLI 测试模式）
 *
 * 为什么要它：PC 端没有可用的宿主 C 编译器（见 MEMORY.md 环境段的五条失败记录），
 * 而"验证固件里的 QRS 算法"最硬的证据就是**让固件自己跑**：
 *   同一份 `ecg_rpeak.c`、同样的 `float`、同样的 `-O2`，没有二次移植。
 *
 * 做法：从 SD 卡读一个文件清单，逐个文件**全速**跑检测器（不按 500 Hz 节拍，
 * 能跑多快跑多快），把每个峰索引打到串口。上位机用 `tools/mitbih_eval.py`
 * 直接算 Se / +P / DER。
 *
 * ⚠ 这是**测试模式功能**，由 CLI 的 `mit-run-all`/`ladder-run-all` 写 SD 标记文件
 *   `/sdcard/batch_pending.flag` 触发（重启后 LVGL 之前自动跑，跑完删标记）。
 *   清单文件不存在时 `ecg_batch_run_manifest` 返回 ESP_ERR_NOT_FOUND，不影响正常启动。
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

/**
 * @brief 按清单批量跑 R 峰检测。
 *
 * 清单格式（纯文本，一行一个路径，`#` 开头为注释）：
 * ```
 * /sdcard/100_500.bin
 * /sdcard/101_500.bin
 * ```
 *
 * 串口输出格式（便于机器解析）：
 * ```
 * BATCH-BEGIN total=<n>
 * FILE <name> frames=<f> samples=<s> bad=<b> peaks=<p> bpm=<bpm*10>
 * P <idx> <idx> ...          (每行最多 16 个)
 * PENV <name>
 * ...
 * BATCH-END ok=<n> fail=<m> elapsed_ms=<ms>
 * ```
 *
 * ⚠ 输出**分两路**：
 *   · 串口 = **进度提示**（`BATCH-BEGIN` / 每文件一行 `FILE ... peaks=.. bpm=..` / `BATCH-END`），
 *     不刷 `P` 峰序列（量大且重启会丢）。
 *   · SD 卡结果文件 `/sdcard/<清单名去 .txt>_result.txt` = **完整对账数据**
 *     （含全部 `P` 峰索引），对账以它为准。
 *
 * @param manifest 清单路径
 * @return ESP_OK 表示清单存在且至少跑了一个文件
 */
esp_err_t ecg_batch_run_manifest(const char *manifest);
