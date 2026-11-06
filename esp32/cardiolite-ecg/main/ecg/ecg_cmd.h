/**
 * @file ecg_cmd.h
 * @brief 下行命令层（T206 §3 / T208）—— 只做「拼帧 + 发」，不做状态机。
 *
 * 帧格式（与 STM32 固件 `vcomcmd.c:606 vcom_bin_dispatch()` 逐字节对齐，勿改）：
 *
 *     AA 55 | TYPE=0x80 | LEN(2,LE) | CMD | ARG(0~N) | CRC8        总长 = 6 + LEN
 *     LEN = 1(CMD) + N(ARG)
 *     CRC8：poly=0x07 / init=0x00 / 不反射，覆盖 TYPE + LEN + CMD + ARG
 *
 * 固件侧的三道硬校验（抄错就静默丢弃，不会有任何回显）：
 *   ① `len >= 7`（`vcomcmd.c:613`，最小合法帧 = LEN=1 ⇒ 6+1 = 7 B）
 *   ② `6 + LEN == 实收长度`（`:629`）
 *   ③ CRC 对上（`:637`，**坏 CRC 静默丢**）
 *
 * ★本层发出去的六帧（供抓总线/示波器或串口助手 HEX 模式比对，**不提供原样发接口**）：
 *     START       AA 55 80 01 00 01 5D
 *     STOP        AA 55 80 01 00 02 54
 *     SNAPSHOT    AA 55 80 01 00 10 2A
 *     SET_MODE 0  AA 55 80 02 00 04 00 EF      SET_MODE 1  AA 55 80 02 00 04 01 E8
 *     SET_NOTCH 0 AA 55 80 02 00 05 00 FA      SET_NOTCH 1 AA 55 80 02 00 05 01 FD
 *     SET_CH2 0   AA 55 80 02 00 06 00 C5      SET_CH2 1   AA 55 80 02 00 06 01 C2
 *   （★T209：后六条 CRC 已实算；并用 START/STOP/SNAP 三帧反验过 CRC 实现本身。）
 *   （猛哥 11-04 17:44 拍板：原样发与 `485-send start` 等价，属多余入口，已删。）
 *
 * 🛑 设计纪律（猛哥 2026-11-04）：
 *   ① **只发二进制，不发 ASCII** —— ESP32 界面没有 ASCII 面，也不需要。
 *   ② **只在 START 之前发命令** —— 那时总线上没有上行帧流，半双工不冲突（T206 §3.4）。
 *      RUN 态下发的成功率实测约 50%（T207 §7.4），所以本层不做 RUN 态重发。
 *      ⚠ 但 `mode`/`notch` 的**回执只在 RUN 态出现**（STA 跟随）⇒ 这两条是
 *      "必须 RUN 才看得见结果、而 RUN 又最容易丢"的一对，别把它当矛盾：
 *      发失败就重发一次（二者都幂等），验收看 STA 有没有变。
 *   ③ **不缓存"我以为的状态"** —— 真值一律看上行 `STA`（`app_state.sta`）或帧流有无。
 *   ④ 不做 `0x10` 探针 / `0x03` RESET / 寄存器回读 —— 那是 CLI 调试面的活；
 *      若要回读，**必须先给 `ecg_pipeline.c` 加 TYPE 过滤**，否则快照帧会被当 36 个样本
 *      （T206 §9.1b，不是推测，已核到行号）。★T208 已加过滤，本条前置条件已满足。
 *   ⑤ **不臆造"命令生效了"** —— `mode`/`notch` 在 STOP 态发出去**本来就无回执**，
 *      返回值只说"交给 UART 了"，不等于 STM32 收到（见 `ecg_cmd_send` 的 retval 注释）。
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"

/** 下行命令帧的 TYPE（上行显示帧是 0x01、快照是 0x03） */
#define ECG_CMD_TYPE        0x80u

/* 采集启停（T208）。START 之前唯一必须发的就是这两条。 */
#define ECG_CMD_START       0x01u   /* 无状态门；🛑 **不幂等**（重发会重走采集链初始化） */
#define ECG_CMD_STOP        0x02u   /* 无状态门；幂等 */
/* ★T208（11-05）：读快照。STM32 侧 `vcomcmd.c:646 case 0x10u` ——
 *   **仅在 STOP 态**回一帧 80 B 快照（`AA 55 03 4A ... REQID=0x10`）；
 *   RUN 态静默（协议规定，避免与 DRDY 抢 SPI 时基、避免与显示帧混流）。
 *   是「下行通不通」最干净的判据：回执是**只由命令生成**的帧，帧流伪造不出来。 */
#define ECG_CMD_SNAPSHOT    0x10u

/* ★T209（11-05）：带 ARG 的三条采集端参数命令。
 *
 * 三条的固件侧口径（`vcomcmd.c:706/727/739`）—— **两道门，各不相同**：
 *   ┌──────────┬────────┬──────────┬────────────────────────────────────────┐
 *   │ CMD      │ 状态门 │ 长度门   │ 回执（ACK）                            │
 *   ├──────────┼────────┼──────────┼────────────────────────────────────────┤
 *   │ 0x04 MODE│ 不加   │ LEN==2   │ 下一帧 STA.bit0 跟随；STOP 态**无回执** │
 *   │ 0x05 NOTCH│ 不加  │ LEN==2   │ 下一帧 STA.bit1 跟随；STOP 态**无回执** │
 *   │ 0x06 CH2 │ **仅STOP** │ LEN==2 │ ★**回一帧 80 B 快照**（REQID=0x06）    │
 *   └──────────┴────────┴──────────┴────────────────────────────────────────┘
 * ⇒ 验收方式**不同**，别混：
 *   - `mode` / `notch`：**必须先 `485-send start`**，然后看 `485-stat` 的 STA 行
 *     或屏上按钮文字（RUN 态下一帧即跟随，约 46 ms）。
 *   - `ch2`：**必须在 STOP 态**发（RUN 态被状态门静默丢掉），回执立刻可见。
 *
 * 🛑 `LEN` 必须是 **2**（= 1 CMD + 1 ARG）。固件 `argLen != 2u ⇒ return`，
 *    LEN=1 的帧会被**静默丢弃**（不报错、不回执），现场极易误判"命令没到"。
 * 🛑 ARG 只有 1 字节，语义由内核二次容错（`set_mode` 内 `mon = mon ? 1 : 0`）。 */
#define ECG_CMD_SET_MODE     0x04u   /* ARG: 0=诊断 1=监护 */
#define ECG_CMD_SET_NOTCH    0x05u   /* ARG: 0=关   1=开   */
#define ECG_CMD_SET_CHANNEL  0x06u   /* ARG: 0=仅I  非0=I&II（★仅 STOP 态生效） */

/** 单帧 ARG 上限：固件 `VCOM_BIN_MAX_LEN = 58`（`vcomcmd.c:604`），故 LEN ≤ 59 */
#define ECG_CMD_MAX_ARG     32u

/**
 * @brief 发一条下行命令（自动拼帧头与 CRC8）。
 *
 * @param cmd     命令码（见 `ECG_CMD_*`）
 * @param arg     参数区，可为 NULL（arg_len = 0）
 * @param arg_len 参数字节数，≤ `ECG_CMD_MAX_ARG`
 *
 * @retval ESP_OK               已交给 UART（⚠ 不代表 STM32 收到了）
 * @retval ESP_ERR_INVALID_ARG  参数越界
 * @retval ESP_ERR_INVALID_STATE 485 未初始化（还没 `485-start`）
 * @retval ESP_FAIL             底层写失败
 */
esp_err_t ecg_cmd_send(uint8_t cmd, const uint8_t *arg, uint8_t arg_len);

/**
 * @brief `0x01 START`。
 *
 * 🛑 **只点一次**：`ads1293_start()` 首步停流并重走采集链 ⇒ 重发会出现一次基线瞬态
 *    （`ads1293.c:843` 注释）。UI 侧靠「点完置灰」保证，不靠本函数去重。
 *
 * 隐式 ACK = **显示帧流"起"**（协议 §5.1）⇒ 固件**不回快照**，别等快照超时。
 */
esp_err_t ecg_cmd_start(void);

/** @brief `0x02 STOP`。幂等，隐式 ACK = 显示帧流"停"。 */
esp_err_t ecg_cmd_stop(void);

/**
 * @brief `0x10 REQ_SNAPSHOT` —— 请求 36 条寄存器快照。
 *
 * **仅在 STM32 处于 STOP 态**才有回执：回一帧 80 B `AA 55 03 4A ...`
 * （TYPE=0x03, LEN=74, REQID=0x10）。RUN 态静默，**不是丢包**（协议 §5.1）。
 *
 * 回执落在 `ecg_pipeline` 的 `snapshots` 计数上（`485-stat` 可见），
 * 不进波形 —— 为此本次同时给 `ecg_pipeline.c` 补了 TYPE 过滤。
 *
 * @note 幂等、无副作用（只读 36 条寄存器，不改配置、不动采集状态）。
 */
esp_err_t ecg_cmd_snapshot(void);

/**
 * @brief `0x04 SET_MODE` —— 诊断/监护（滤波档）切换。
 *
 * @param on 0 = 诊断（ECG 判据用的档）、1 = 监护。
 *
 * **不加状态门**（STOP 态也能改；`ads1293_start()` 里的 `filter_chain_init`
 * 会把 mode 写回默认 ⇒ 只在 START 之后改才有持久意义）。
 *
 * ACK = 下一帧**显示帧 STA.bit0** 跟随 ⇒ 回执**不在本函数返回时出现**，
 *   要等一帧（≤ 46 ms）。STOP 态无帧流 ⇒ **无任何回执**，不是丢包。
 * ⚠ 侧效应：换节系数 + 对该节 DC 预置 ⇒ 波形一次 **~10 µV 台阶**，
 *   是设计不是缺陷（T91 策略 B / T92b）。
 */
esp_err_t ecg_cmd_set_mode(uint8_t on);

/**
 * @brief `0x05 SET_NOTCH` —— 50 Hz 陷波开/关。
 *
 * @param on 0 = 关（跳链首 NOTCH 节）、1 = 开。
 *
 * 状态门/长度门/ACK 口径**与 `ecg_cmd_set_mode()` 完全一致**（都不加门，看 STA.bit1）。
 * ⚠ 侧效应：`off→on` 会 DC 预置陷波节（T48b）⇒ ~10 µV 台阶；`on→off` 只跳节、
 *   **不清状态**（切换开销最小、数值最稳）。
 */
esp_err_t ecg_cmd_set_notch(uint8_t on);

/**
 * @brief `0x06 SET_CHANNEL` —— 通道 II（I&II 双通道）使能。
 *
 * @param ch2 0 = 仅通道 I（LEN=53）、非 0 = I&II（LEN=103）。
 *
 * 🛑 **唯一带状态门的参数命令：仅 STOP 态生效**（`vcomcmd.c:755`）。RUN 态
 *   `START_CON=1` 锁着配置寄存器，改不进去 ⇒ 固件**静默丢弃**（一个字节都不回）。
 *   ⇒ 正确顺序：`485-send stop` → `485-send ch2 1` → `485-send start`。
 *
 * ACK = **一帧 80 B 快照**（`AA 55 03 4A .. REQID=0x06`），落在 `snapshots` 计数上；
 *   另有后续帧长 53/103 与 STA.bit2 跟随。三者任一可判。
 */
esp_err_t ecg_cmd_set_channel(uint8_t ch2);
