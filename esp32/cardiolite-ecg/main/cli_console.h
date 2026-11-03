/**
 * @file cli_console.h
 * @brief CLI 测试模式命令台 —— esp_console REPL + 命令注册
 *
 * S1 只起 REPL 骨架 + 模式切换 + 基础命令；S2/S3/S4 逐步加批处理/回放/扫描命令。
 */

#ifndef CLI_CONSOLE_H
#define CLI_CONSOLE_H

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * 启动 CLI 命令台（REPL）。
 *
 * 注意：esp_console_new_repl_stdio() 内部会自建独立的 console_repl 任务，
 * esp_console_start_repl() 只是给它发 notify 信号、立即返回，**不阻塞调用者**。
 * 因此本函数必须在 app_main 末尾（LVGL 与页面都建好之后）调用，返回后 app_main 正常结束。
 */
esp_err_t cli_console_start(void);

#ifdef __cplusplus
}
#endif

#endif /* CLI_CONSOLE_H */
