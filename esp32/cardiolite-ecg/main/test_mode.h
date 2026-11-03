/**
 * @file test_mode.h
 * @brief CLI 测试模式状态机 —— 三态 g_test_mode + SD 标记文件读写
 *
 * 模式（需求文档 §1）：
 *   TEST_NORMAL  正常模式，UI 按钮可用
 *   TEST_MIT     MIT-BIH 测试模式，UI 按钮禁用（UI 仅作波形展示）
 *   TEST_LADDER  心率阶梯测试模式，UI 按钮禁用
 *
 * 状态是全局可变量 g_test_mode，由 CLI 命令通过 test_mode_set() 切换；
 * UI 事件回调入口在进入动作前读 test_mode_get()，非 NORMAL 直接 return。
 *
 * 批处理登记（S2，解法 A）：SD 标记文件 /sdcard/batch_pending.flag，内容=清单路径，
 * 重启后 LVGL 之前读标记跑批处理，跑完删标记。标记走 SD 文件而非 NVS（零新增基础设施）。
 */

#ifndef TEST_MODE_H
#define TEST_MODE_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    TEST_NORMAL = 0,   /* 正常模式 */
    TEST_MIT,          /* MIT-BIH 测试模式 */
    TEST_LADDER,       /* 心率阶梯测试模式 */
} test_mode_t;

/* 当前模式 */
test_mode_t test_mode_get(void);

/* 切换模式（幂等；同一模式重复设置不产生副作用） */
void test_mode_set(test_mode_t m);

/* 模式名的字符串形式（供 status 命令打印） */
const char *test_mode_name(test_mode_t m);

/* ============================================================================
 * 批处理登记（S2 用；S1 先只提供状态机，SD 标记文件函数 S2 再补）
 * ========================================================================== */

/* 登记一次批处理：写 /sdcard/batch_pending.flag，内容 = manifest（如 "batch_mit.txt"） */
void test_mode_set_batch(const char *manifest);

/* 是否有待跑的批处理（标记文件存在且内容非空） */
bool test_mode_batch_pending(void);

/* 读标记里的清单路径（无则返回 NULL，指向静态缓冲，勿 free） */
const char *test_mode_get_batch(void);

/* 清除批处理登记（remove 标记文件） */
void test_mode_clear_batch(void);

#ifdef __cplusplus
}
#endif

#endif /* TEST_MODE_H */
