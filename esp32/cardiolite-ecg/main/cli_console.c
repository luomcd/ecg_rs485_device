/**
 * @file cli_console.c
 * @brief CLI 测试模式命令台实现
 *
 * 命令集（共 22 条，按阶段分组）：
 *
 *   S1 骨架/状态
 *     status        打印当前模式与 UI 按钮状态
 *     test-mit      进入 MIT-BIH 测试模式（禁用 UI 按钮）
 *     test-ladder   进入心率阶梯测试模式（禁用 UI 按钮）
 *     test-exit     退出测试模式，回到 NORMAL（恢复 UI 按钮）
 *     log           开/关串口日志级别（off/error/warn/info/debug/verbose）
 *     restart       软重启（登记批处理后用它触发）
 *     help          内建命令帮助（esp_console_register_help_command）
 *
 *   S2 全速批处理
 *     mit-run-all     登记跑 /sdcard/batch_mit.txt（重启后跑）
 *     ladder-run-all  登记跑 /sdcard/batch_ladder.txt（重启后跑）
 *
 *   S3 回放对账
 *     mit-run <rec> [gain] [--speed N]      单条回放（有波形，峰序列落盘 SD）
 *     ladder-run <bpm> [gain] [--speed N]   单档阶梯回放
 *     play <file> [gain] [--speed N]        ★T210 通用回放：文件名原样，
 *                                           不写结果文件、不进测试模式（纯看波形）
 *     run-stop                              停止回放并收尾落盘
 *     result-dump <stem>                    按需回传结果文件到串口
 *
 *   S4 SD 扫描 / 写清单
 *     scan           递归列全卡 *.bin（含大小）
 *     mit-list       列 MIT 记录（扫根目录与 s7_batch/）
 *     ladder-list    列心率阶梯档位
 *     mit-set <csv>|all      写 /sdcard/batch_mit.txt
 *     ladder-set <csv>|all   写 /sdcard/batch_ladder.txt
 *
 *   S9a 485 实时源（真机路线第一段：PC/STM32 → RS485 → ESP32）
 *     485-start [baud]  启动 485 源（停掉当前 SD 回放；默认 115200）
 *     485-stop          停止 485 源，回到上电默认（SD 回放暂停态）
 *     485-stat          链路/解帧/环缓/心率统计（对账用）
 *     485-send start|stop|snap|mode|notch|ch2
 *                    ★T208/T209：本工程唯一的二进制下行入口
 *                    （0x01/0x02/0x10/0x04/0x05/0x06）
 */

#include "cli_console.h"

#include <ctype.h>
#include <dirent.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "esp_console.h"
#include "esp_heap_caps.h"      /* ★T216：mem 命令读内部 RAM / PSRAM 真实容量 */
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "argtable3/argtable3.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_lv_adapter.h"

#include "app_config.h"
#include "test_mode.h"
#include "app_state.h"          /* ★T209：STA 回读（验证 0x04/0x05 是否生效） */
#include "storage/storage_sd.h"
#include "ecg/ecg_cmd.h"
#include "ecg/ecg_frame.h"      /* ★T209：ecg_sta_to_str() */
#include "ecg/ecg_pipeline.h"
#include "rs485_port.h"
#include "ui/ui_waveform.h"
#include "ui/ui_button_bar.h"

static const char *TAG = "cli";

#define PROMPT_STR "cardiolite"

/* ============================================================================
 * 命令实现
 * ========================================================================== */

/* ---- status ---- */
static int cmd_status(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    const test_mode_t m = test_mode_get();
    const bool ui_enabled = (m == TEST_NORMAL);

    printf("mode: %s\n", test_mode_name(m));
    printf("ui-buttons: %s\n", ui_enabled ? "enabled" : "disabled");

    return 0;
}

/* ---- test-mit ---- */
static int cmd_test_mit(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    test_mode_set(TEST_MIT);
    printf("进入 MIT-BIH 测试模式，UI 按钮已禁用（UI 仅作波形展示）。\n");
    printf("退出请用 test-exit。\n");

    return 0;
}

/* ---- test-ladder ---- */
static int cmd_test_ladder(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    test_mode_set(TEST_LADDER);
    printf("进入心率阶梯测试模式，UI 按钮已禁用（UI 仅作波形展示）。\n");
    printf("退出请用 test-exit。\n");

    return 0;
}

/* ---- test-exit ---- */
static int cmd_test_exit(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    test_mode_set(TEST_NORMAL);
    printf("已退出测试模式，UI 按钮恢复。\n");

    return 0;
}

/* ---- mit-run-all / ladder-run-all：登记全速批处理（解法 A）----
 * 写 SD 标记文件 `/sdcard/batch_pending.flag`（内容=清单文件名），
 * 用户重启后 LVGL 之前自动跑对应清单，跑完删标记。
 * 清单分两份：`batch_mit.txt`（只 MIT）/ `batch_ladder.txt`（只阶梯）。
 *
 * 🛑 登记前必须**先数清单行数**：清单不存在或为空时照样写标记，重启后就会
 *    `BATCH-END ok=0` 一条都不跑 —— 这是"静默少跑"的典型来源。数出来一并报给用户，
 *    别再硬写档数（原来 `ladder-run-all` 固定打"8 档"，清单只有 2 条时完全误导）。 */
static int cli_count_manifest(const char *path)
{
    FILE *f = fopen(path, "r");
    if (f == NULL) {
        return -1;
    }
    int n = 0;
    char line[160];
    while (fgets(line, sizeof(line), f) != NULL) {
        if (line[0] != '#' && line[0] != '\n' && line[0] != '\r') {
            n++;
        }
    }
    fclose(f);
    return n;
}

static int cmd_mit_run_all(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    if (!storage_sd_is_mounted()) {
        printf("SD 卡未挂载，无法登记。\n");
        return 1;
    }
    const int n = cli_count_manifest("/sdcard/batch_mit.txt");
    if (n <= 0) {
        printf("🛑 /sdcard/batch_mit.txt 不存在或为空 —— 未登记（否则重启后一条都不跑）。\n");
        printf("   先用 mit-set all 或 mit-set <a,b,c> 写清单。\n");
        return 1;
    }
    test_mode_set_batch("batch_mit.txt");
    printf("已登记：重启后将全速跑 /sdcard/batch_mit.txt（%d 条 MIT 记录）。\n", n);
    printf("请重启（restart 命令或断电重上电）。\n");

    return 0;
}

static int cmd_ladder_run_all(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    if (!storage_sd_is_mounted()) {
        printf("SD 卡未挂载，无法登记。\n");
        return 1;
    }
    const int n = cli_count_manifest("/sdcard/batch_ladder.txt");
    if (n <= 0) {
        printf("🛑 /sdcard/batch_ladder.txt 不存在或为空 —— 未登记（否则重启后一条都不跑）。\n");
        printf("   先用 ladder-set all 或 ladder-set <a,b,c> 写清单。\n");
        return 1;
    }
    test_mode_set_batch("batch_ladder.txt");
    printf("已登记：重启后将全速跑 /sdcard/batch_ladder.txt（%d 条）。\n", n);
    printf("请重启（restart 命令或断电重上电）。\n");

    return 0;
}

/* ============================================================================
 * S4：SD 扫描 + 写清单
 *
 * 为什么需要：清单原本只能由 PC 端 `tools/make_sdcard_batch.py` 生成。拔卡改了文件、
 * 或只想跑其中几条，就得回 PC 重做一遍 —— 而板子上明明能自己看、自己写。
 *
 * 目录约定：载荷既可能在 `/sdcard/` 根，也可能在 `/sdcard/s7_batch/`（PC 工具的 SD_SUB）。
 * **两处都扫、都认**，顺序固定「根 → s7_batch」，与 `mit-run` 的探测顺序一致；同名去重时根优先。
 *
 * 清单格式：`#` 注释行跳过，其余**每行一个绝对路径**（`ecg_batch.c:run_one()` 直接 `fopen` 该行）。
 *
 * 🛑 写入策略是 **fail-fast**：任何一个记录号解析不到文件，就一个字节都不写。
 *    半份清单会让 `mit-run-all` **静默少跑记录**，比直接报错危险得多。
 * 🛑 写完必回读自检（数非注释行 == n）：SD 卡写失败在某些接触不良下不报错，只写半截。
 * ==========================================================================*/

#define CLI_SCAN_DIR_N   2
#define CLI_LIST_MAX     64          /* MIT 48 + 阶梯 8，留余量 */
#define CLI_MANIFEST_MAX 3072        /* 64 条 × 33 B + 两行注释，够用 */

static const char *const kScanDirs[CLI_SCAN_DIR_N] = { "/sdcard/", "/sdcard/s7_batch/" };

/* 一条"可跑单元"：值（记录号 / bpm）+ 它在哪个目录 */
typedef struct {
    int          val;
    unsigned char dir;
} cli_item_t;

static bool cli_file_exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0;
}

/* 收集形如 `前缀 + 3位数字 + 后缀` 的文件。两目录都扫，按 val 去重（先到先得＝根优先）。 */
static int cli_collect(cli_item_t *out, int max, const char *prefix, const char *suffix)
{
    const size_t lp = strlen(prefix);
    const size_t ls = strlen(suffix);
    int n = 0;

    for (int d = 0; d < CLI_SCAN_DIR_N; d++) {
        DIR *dp = opendir(kScanDirs[d]);
        if (dp == NULL) {
            continue;
        }
        struct dirent *ent;
        while ((ent = readdir(dp)) != NULL) {
            const char *nm = ent->d_name;
            if (strlen(nm) != lp + 3 + ls || strncmp(nm, prefix, lp) != 0 ||
                strncmp(nm + lp + 3, suffix, ls) != 0) {
                continue;
            }
            const char *g = nm + lp;
            if (!isdigit((unsigned char)g[0]) || !isdigit((unsigned char)g[1]) ||
                !isdigit((unsigned char)g[2])) {
                continue;
            }
            const int val = (g[0] - '0') * 100 + (g[1] - '0') * 10 + (g[2] - '0');

            bool dup = false;
            for (int k = 0; k < n; k++) {
                if (out[k].val == val) {
                    dup = true;
                    break;
                }
            }
            if (dup || n >= max) {
                continue;
            }
            out[n].val = val;
            out[n].dir = (unsigned char)d;
            n++;
        }
        closedir(dp);
    }

    /* 插入排序（n ≤ 64，不必上 qsort） */
    for (int i = 1; i < n; i++) {
        const cli_item_t key = out[i];
        int j = i - 1;
        while (j >= 0 && out[j].val > key.val) {
            out[j + 1] = out[j];
            j--;
        }
        out[j + 1] = key;
    }
    return n;
}

/* 把 argv[1..] 用逗号拼起来 —— 让 `mit-set 105,108`、`mit-set 105 108`、
 * `mit-set 105, 108` 三种写法都能用（shell 会按空格拆参数）。 */
static int cli_join_args(int argc, char **argv, char *buf, size_t cap)
{
    size_t off = 0;
    buf[0] = '\0';
    for (int i = 1; i < argc; i++) {
        const int w = snprintf(buf + off, cap - off, "%s%s", (i == 1) ? "" : ",", argv[i]);
        if (w < 0 || off + (size_t)w >= cap) {
            return -1;
        }
        off += (size_t)w;
    }
    return (int)off;
}

/* 解析 CSV → 校验每个都真在卡上 → 填 items。返回条数，<0 表示失败（调用方不得写盘）。 */
static int cli_parse_csv(char *csv, cli_item_t *out, int max,
                         const char *prefix, const char *suffix)
{
    int n = 0;
    char *save = NULL;
    for (char *tok = strtok_r(csv, ",", &save); tok != NULL;
         tok = strtok_r(NULL, ",", &save)) {
        while (*tok == ' ' || *tok == '\t') {
            tok++;
        }
        int len = (int)strlen(tok);
        while (len > 0 && (tok[len - 1] == ' ' || tok[len - 1] == '\t')) {
            tok[--len] = '\0';
        }
        if (len == 0 || len > 3) {
            printf("非法项「%s」：应为 1~3 位数字。\n", tok);
            return -1;
        }
        for (int i = 0; i < len; i++) {
            if (!isdigit((unsigned char)tok[i])) {
                printf("非法项「%s」：含非数字字符。\n", tok);
                return -1;
            }
        }
        if (n >= max) {
            printf("一次最多写 %d 条。\n", max);
            return -1;
        }

        const int val = atoi(tok);
        bool found = false;
        for (int d = 0; d < CLI_SCAN_DIR_N && !found; d++) {
            char full[80];
            snprintf(full, sizeof(full), "%s%s%03d%s", kScanDirs[d], prefix, val, suffix);
            if (cli_file_exists(full)) {
                out[n].val = val;
                out[n].dir = (unsigned char)d;
                n++;
                found = true;
            }
        }
        if (!found) {
            printf("SD 卡上没有 %s%03d%s —— 已扫 /sdcard/ 与 /sdcard/s7_batch/。\n",
                   prefix, val, suffix);
            return -1;
        }
    }
    return n;
}

/* 写清单并回读自检。返回 0 成功。 */
static int cli_write_manifest(const char *path, const char *header,
                              const char *prefix, const char *suffix,
                              const cli_item_t *items, int n)
{
    static char man[CLI_MANIFEST_MAX];

    int off = snprintf(man, sizeof(man), "%s", header);
    for (int i = 0; i < n; i++) {
        const int w = snprintf(man + off, sizeof(man) - (size_t)off, "%s%s%03d%s\n",
                               kScanDirs[items[i].dir], prefix, items[i].val, suffix);
        if (w < 0 || off + w >= (int)sizeof(man)) {
            printf("清单超出内部缓冲（%d 条太长）。\n", n);
            return -1;
        }
        off += w;
    }

    FILE *f = fopen(path, "w");
    if (f == NULL) {
        printf("写 %s 失败（SD 卡只读或路径不存在？）。\n", path);
        return -1;
    }
    const size_t wr = fwrite(man, 1, (size_t)off, f);
    fclose(f);
    if (wr != (size_t)off) {
        printf("写入不完整：%u / %d B。\n", (unsigned)wr, off);
        return -1;
    }

    /* 回读自检：数非注释、非空行 */
    int back = 0;
    f = fopen(path, "r");
    if (f == NULL) {
        printf("写入后回读失败（%s）。\n", path);
        return -1;
    }
    char line[128];
    while (fgets(line, sizeof(line), f) != NULL) {
        if (line[0] != '#' && line[0] != '\n' && line[0] != '\r') {
            back++;
        }
    }
    fclose(f);
    if (back != n) {
        printf("🛑 回读自检不符：写入 %d 条，读回 %d 条。清单不可信，请重写。\n", n, back);
        return -1;
    }

    printf("已写 %s：%d 条 / %d B（回读自检 %d/%d ✓）\n", path, n, off, back, n);
    return 0;
}

/* ---- mit-list / ladder-list ---- */
static int cli_do_list(const char *prefix, const char *suffix, const char *title)
{
    if (!storage_sd_is_mounted()) {
        printf("SD 卡未挂载。\n");
        return 1;
    }

    static cli_item_t items[CLI_LIST_MAX];
    const int n = cli_collect(items, CLI_LIST_MAX, prefix, suffix);
    if (n == 0) {
        printf("没找到 %s*%s —— 已扫 /sdcard/ 与 /sdcard/s7_batch/。\n", prefix, suffix);
        return 1;
    }

    int per_dir[CLI_SCAN_DIR_N] = { 0, 0 };
    printf("%s 共 %d 个：\n", title, n);
    for (int i = 0; i < n; i++) {
        per_dir[items[i].dir]++;
        printf("%03d%c", items[i].val, ((i % 10) == 9 || i == n - 1) ? '\n' : ' ');
    }
    for (int d = 0; d < CLI_SCAN_DIR_N; d++) {
        if (per_dir[d] > 0) {
            printf("  来源 %-20s %d 个\n", kScanDirs[d], per_dir[d]);
        }
    }
    return 0;
}

static int cmd_mit_list(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    return cli_do_list("mit_", "_500.bin", "MIT 记录");
}

static int cmd_ladder_list(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    return cli_do_list("sr_", "bpm.bin", "心率阶梯");
}

/* ---- mit-set / ladder-set ---- */
static int cli_do_set(int argc, char **argv, const char *prefix, const char *suffix,
                      const char *manifest, const char *header)
{
    if (!storage_sd_is_mounted()) {
        printf("SD 卡未挂载。\n");
        return 1;
    }
    if (argc < 2) {
        printf("用法: %s <a,b,c> | %s all\n", argv[0], argv[0]);
        return 1;
    }

    static cli_item_t items[CLI_LIST_MAX];
    int n;

    if (argc == 2 && strcmp(argv[1], "all") == 0) {
        n = cli_collect(items, CLI_LIST_MAX, prefix, suffix);
        if (n == 0) {
            printf("卡上没有 %s*%s，无法生成 all 清单。\n", prefix, suffix);
            return 1;
        }
        printf("all 模式：按卡上实际文件生成 %d 条。\n", n);
    } else {
        char csv[256];
        if (cli_join_args(argc, argv, csv, sizeof(csv)) < 0) {
            printf("参数太长（上限 256 字符）。\n");
            return 1;
        }
        n = cli_parse_csv(csv, items, CLI_LIST_MAX, prefix, suffix);
        if (n <= 0) {
            printf("🛑 未写入任何内容（上面有失败项）。清单保持原样。\n");
            return 1;
        }
    }

    if (cli_write_manifest(manifest, header, prefix, suffix, items, n) != 0) {
        return 1;
    }
    printf("下一步：%s-run-all 登记清单，再 restart 重启批处理。\n",
           strncmp(prefix, "mit_", 4) == 0 ? "mit" : "ladder");
    return 0;
}

static int cmd_mit_set(int argc, char **argv)
{
    return cli_do_set(argc, argv, "mit_", "_500.bin", "/sdcard/batch_mit.txt",
                      "# CardioLite MIT batch manifest (written by mit-set)\n"
                      "# one absolute .bin path per line\n");
}

static int cmd_ladder_set(int argc, char **argv)
{
    return cli_do_set(argc, argv, "sr_", "bpm.bin", "/sdcard/batch_ladder.txt",
                      "# CardioLite ladder batch manifest (written by ladder-set)\n"
                      "# one absolute .bin path per line\n");
}

/* ---- scan：递归列全卡 *.bin（S4 升级；原来是只列根目录一层）----
 * ⚠ 不用 d_type 判目录：ESP32 的 dirent 里 d_type 恒 DT_UNKNOWN（不可靠），
 *   与 storage_sd.c 一致，改用 stat + S_ISDIR。
 * 🛑 **必须「先抄名字 → closedir → 再 stat」**。实测（2026-10-29）：
 *    DIR 流开着时 stat 要 383 ms/次（64 次 = 24.5 s），关了流降到 ~25 ms/次 ——
 *    同样 2 次 stat 在 `mit-set` 里只花几十毫秒，差别就在「流是否还开着」。
 *    两阶段写法把 `scan` 从 25 s 压到 4 s。
 * 🛑 **只有 `.bin` 才 stat**，其余条目用 opendir 试探判目录（~0.6 ms vs ~25 ms）。
 *    `/sdcard/overlays/` 有上百个 png 资源，逐个 stat 纯属白扔时间。 */
/* 安全的路径拼接：放不下就返回 false（调用方跳过）。
 * 🛑 不用 snprintf("%s%s")：fatfs 的 d_name 最长 255 B，编译器会判「可能截断」并
 *    以 -Werror=format-truncation 直接卡构建；而这里显式算长度，既无告警也真的不会截断。 */
static bool cli_join_path(char *out, size_t cap, const char *a, const char *b, const char *c)
{
    const size_t la = strlen(a);
    const size_t lb = strlen(b);
    const size_t lc = (c != NULL) ? strlen(c) : 0;
    if (la + lb + lc + 1 > cap) {
        return false;
    }
    memcpy(out, a, la);
    memcpy(out + la, b, lb);
    if (lc > 0) {
        memcpy(out + la + lb, c, lc);
    }
    out[la + lb + lc] = '\0';
    return true;
}

/* --- scan 的耗时拆分（S4 实测用）---
 * 起因：`scan` 首次实测 25 s，`mit-list`（同样两次 opendir+readdir）只要 0.1 s。
 * 我先后猜过「printf 逐行刷串口慢」和「stat 本身慢」，两次都被数据否掉；最后靠这段
 * 计时一次测准：`耗时 24879 ms = 目录 289 + stat 24538 + 输出 37`。
 * ⇒ **瓶颈是 stat，而且只在 DIR 流打开时慢**（`mit-set` 里 2 次独立 stat 只花几毫秒，
 *   这里 64 次却 383 ms/次）。修法就是下面这个两阶段：先抄名字、关流，再 stat。
 * 计时永久保留 —— 以后换卡、换 FAT 配置都能一眼看出瓶颈在哪。 */
static int64_t s_t_dir;      /* opendir + readdir + closedir */
static int64_t s_t_stat;     /* stat() */
static int64_t s_t_out;      /* printf + fflush */
static int     s_dtype_unknown;  /* readdir 没给有效 d_type 的条目数（验证 d_type 能否用） */

#define CLI_SCAN_ENTRY_MAX 96        /* 单目录最多抄 96 条（实测 s7_batch 56 条）*/
#define CLI_SCAN_SUB_MAX   4         /* 单目录最多下钻 4 个子目录 */

typedef struct {
    char full[96];               /* 绝对路径，给 stat 用 */
    char rel[64];                /* 相对 /sdcard/ 的显示名 */
    unsigned char dtype;         /* readdir 给的 d_type（DT_UNKNOWN 表示不可信） */
} cli_scan_ent_t;

/* 🛑 必须是**单一份**、且只在「本目录全部抄完、流已关闭」后才使用：
 *    递归时子目录会复用同一块，所以父层不能在自己遍历期间还留着指针。 */
static cli_scan_ent_t s_ents[CLI_SCAN_ENTRY_MAX];

/* 待下钻的子目录清单，**按深度分片**（0=根，1=s7_batch，2=第三层只打印不展开）。
 * 🛑 不能放栈上：`console_repl` 任务栈只有 4 KB，一帧 640 B 的两层递归直接把任务打崩
 *    （实测 `***ERROR*** A stack overflow in task console_repl`）。
 * 🛑 也不能用单份 static：父层还在遍历子目录清单时，子层会把同一块覆盖掉。 */
static char s_sub[3][CLI_SCAN_SUB_MAX][96];
static char s_subrel[3][CLI_SCAN_SUB_MAX][64];

static int scan_walk(const char *dir, const char *rel, int depth, int *count)
{
    /* ---- 阶段 1：只 readdir 抄名字，抄完立刻 closedir ---- */
    int m = 0;
    bool over = false;

    int64_t t0 = esp_timer_get_time();
    DIR *dp = opendir(dir);
    s_t_dir += esp_timer_get_time() - t0;
    if (dp == NULL) {
        printf("打开 %s 失败。\n", dir);
        return -1;
    }

    for (;;) {
        t0 = esp_timer_get_time();
        struct dirent *ent = readdir(dp);
        s_t_dir += esp_timer_get_time() - t0;
        if (ent == NULL) {
            break;
        }

        const char *nm = ent->d_name;
        if (strcmp(nm, ".") == 0 || strcmp(nm, "..") == 0) {
            continue;
        }
        if (m >= CLI_SCAN_ENTRY_MAX) {
            over = true;
            break;
        }

        /* 🛑 d_type 可信时，**确定既不是目录、也不是 .bin** 的条目当场丢掉。
         *    `overlays/` 是树莓派启动分区的几百个 .dtbo，全抄进来会把 96 条上限挤爆，
         *    结果连 .bin 都列不全（实测就是这么漏的）。 */
        const size_t nl = strlen(nm);
        const bool maybe_bin = (nl > 4) && (strcasecmp(nm + nl - 4, ".bin") == 0);
        const unsigned char dt = (unsigned char)ent->d_type;
        if (dt != DT_UNKNOWN && dt != DT_DIR && !maybe_bin) {
            continue;
        }

        if (!cli_join_path(s_ents[m].full, sizeof(s_ents[m].full), dir, nm, NULL)) {
            continue;                       /* 路径过长：FAT 下不会出现，跳过即可 */
        }
        if (rel[0] != '\0') {
            if (!cli_join_path(s_ents[m].rel, sizeof(s_ents[m].rel), rel, "/", nm)) {
                continue;
            }
        } else if (!cli_join_path(s_ents[m].rel, sizeof(s_ents[m].rel), "", nm, NULL)) {
            continue;
        }
        s_ents[m].dtype = (unsigned char)ent->d_type;
        m++;
    }

    t0 = esp_timer_get_time();
    closedir(dp);
    s_t_dir += esp_timer_get_time() - t0;

    /* ---- 阶段 2：流已关闭，这时 stat 才是毫秒级 ---- */
    char (*subdir)[96] = s_sub[depth];              /* 指向本深度的分片，不在栈上 */
    char (*subrel)[64] = s_subrel[depth];
    int  nsub = 0;

    for (int i = 0; i < m; i++) {
        const char *nm = strrchr(s_ents[i].rel, '/');
        nm = (nm != NULL) ? (nm + 1) : s_ents[i].rel;
        const size_t ln = strlen(nm);
        const bool is_bin = (ln > 4) && (strcasecmp(nm + ln - 4, ".bin") == 0);

        bool is_dir = false;
        struct stat st;
        memset(&st, 0, sizeof(st));

        if (s_ents[i].dtype == DT_DIR) {
            is_dir = true;                  /* readdir 已经说清，省掉一次查找 */
        } else if (s_ents[i].dtype == DT_UNKNOWN) {
            /* 这版 IDF 的 fatfs 可能不给 d_type ⇒ 退回「探测」：
             * 非 .bin 条目只关心它是不是目录，用 opendir 试比 stat 语义更直接。 */
            s_dtype_unknown++;
            if (!is_bin) {
                t0 = esp_timer_get_time();
                DIR *probe = opendir(s_ents[i].full);
                if (probe != NULL) {
                    closedir(probe);
                    is_dir = true;
                }
                s_t_dir += esp_timer_get_time() - t0;
                if (!is_dir) {
                    continue;               /* 普通非 .bin 文件：不列 */
                }
            }
        }

        /* 要显示大小就得 stat；顺带兜住「名字带 .bin 的目录」这种边角 */
        if (is_bin && !is_dir) {
            t0 = esp_timer_get_time();
            const int src = stat(s_ents[i].full, &st);
            s_t_stat += esp_timer_get_time() - t0;
            if (src != 0) {
                continue;
            }
            if (S_ISDIR(st.st_mode)) {
                is_dir = true;
            }
        }

        if (is_dir) {
            if (depth < 2 && nsub < CLI_SCAN_SUB_MAX) {
                if (cli_join_path(subdir[nsub], sizeof(subdir[nsub]), s_ents[i].full, "/", NULL) &&
                    cli_join_path(subrel[nsub], sizeof(subrel[nsub]), s_ents[i].rel, "", NULL)) {
                    nsub++;
                }
            } else if (depth >= 2) {
                printf("%-44s   <目录，未展开>\n", s_ents[i].rel);
            } else {
                printf("%-44s   <子目录过多，未展开>\n", s_ents[i].rel);
            }
            continue;
        }

        /* 走到这里既不是目录 —— 那只有 .bin 才值得列。
         * 🛑 这一道不能省：`d_type` 可信时前面根本不会走 DT_UNKNOWN 分支，
         *    少了它会把所有普通文件都当成 .bin 打出来（实测 215 条里有 150 条是 .dtbo）。 */
        if (!is_bin) {
            continue;
        }

        t0 = esp_timer_get_time();
        printf("%-44s %8u KB\n", s_ents[i].rel, (unsigned)(st.st_size / 1024));
        fflush(stdout);
        s_t_out += esp_timer_get_time() - t0;
        (*count)++;
    }

    if (over) {
        printf("⚠ %s 条目超过 %d，后面的没列（也不计入总数）\n", dir, CLI_SCAN_ENTRY_MAX);
    }

    /* ---- 阶段 3：本层名字已用完，才下钻（子层写的是自己深度的分片）---- */
    for (int i = 0; i < nsub; i++) {
        scan_walk(s_sub[depth][i], s_subrel[depth][i], depth + 1, count);
    }
    return 0;
}

static int cmd_scan(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    if (!storage_sd_is_mounted()) {
        printf("SD 卡未挂载。\n");
        return 1;
    }

    /* 这一行带 fflush，是判断「输出是否攒到最后一起冲」的探针：
     * 若它立刻到，说明 printf 是逐次生效的，后面 25 s 全在干活；
     * 若它也在 23 s 后才到，说明 stdout 是块缓冲，字节数 < 4 KB 就一次都不刷。 */
    printf("扫描 /sdcard/ ...\n");
    fflush(stdout);
    const int64_t t_all = esp_timer_get_time();

    s_t_dir = s_t_stat = s_t_out = 0;
    s_dtype_unknown = 0;
    int n = 0;
    scan_walk("/sdcard/", "", 0, &n);
    const int64_t total = esp_timer_get_time() - t_all;

    if (n == 0) {
        printf("（全卡无 .bin 文件）\n");
    } else {
        printf("共 %d 个 .bin 文件\n", n);
    }
    printf("耗时 %d ms = 目录 %d + stat %d + 输出 %d（其余 %d）\n",
           (int)(total / 1000), (int)(s_t_dir / 1000), (int)(s_t_stat / 1000),
           (int)(s_t_out / 1000), (int)((total - s_t_dir - s_t_stat - s_t_out) / 1000));
    printf("d_type 不可用条目 %d 个（0 = 全部由 readdir 判型，无需逐个查找）\n",
           s_dtype_unknown);
    fflush(stdout);
    return 0;
}

/* ---- S3：回放对账（mit-run / ladder-run）----
 *
 * 与 S2 批处理的关系：**两种跑法，各干各的**（需求 §1 方案 A）。
 *   `mit-run <rec>`   → 走 ecg_pipeline 按 500 Hz 节拍回放（屏幕有波形），
 *                       边跑边把峰序列打到串口，跑完 `FILE` 行收尾 → 可直接对账。
 *   `mit-run-all`     → 走 ecg_batch 全速黑屏批处理（S2）。
 *
 * 🛑 **峰号必须是「文件内相对样本号」**（与 `_ann500.txt` 同坐标系），否则
 *   `ecg_batch_eval.py` 对不上。管线的全局绝对号 → 相对号转换在
 *   `ecg_pipeline_set_rpeak_dump()` 内部完成（见 ecg_pipeline.h 的口径说明）。
 */

/* ---- 回放增益（S3+）----
 *
 * 增益是**垂直方向**（mV → px），与 `--speed`（水平方向，时间快进）是两个维度。
 * 四档与 UI 的 GAIN 按钮完全一致（`k_gain_v[]`）：2.5 / 5 / 10 / 20 mm/mV，默认 5。
 * `APP_PX_PER_MV(g) = g × APP_MMY_PX_PER_MM`（4 px/mm）⇒ 默认 1 mV = 20 px。
 *
 * 两种输入写法：
 *   `10`  → 直接给 mm/mV
 *   `2x`  → 相对基准 5 mm/mV 的倍数（2x = 10、4x = 20、0.5x = 2.5、1x = 5）
 * 只接受四档：超出的值 UI 按钮会显示不出对应档位，也破坏测试可比性。
 */
static bool parse_gain_arg(const char *s, float *out)
{
    if (s == NULL || out == NULL || *s == '\0') {
        return false;
    }
    char  *end = NULL;
    const double v = strtod(s, &end);
    if (end == s || v <= 0.0) {
        return false;
    }
    if (*end == 'x' || *end == 'X') {
        if (*(end + 1) != '\0') {
            return false;
        }
        *out = (float)((double)APP_GAIN_5 * v);
        return true;
    }
    if (*end != '\0') {
        return false;
    }
    *out = (float)v;
    return true;
}

static bool gain_is_supported(float g)
{
    return (fabsf(g - APP_GAIN_2_5) < 0.01f)
        || (fabsf(g - APP_GAIN_5)   < 0.01f)
        || (fabsf(g - APP_GAIN_10)  < 0.01f)
        || (fabsf(g - APP_GAIN_20)  < 0.01f);
}

/** 应用增益（LVGL 非线程安全，必须持锁；并刷新按钮栏的档位高亮） */
static void apply_gain(float g)
{
    if (esp_lv_adapter_lock(-1) == ESP_OK) {
        ui_waveform_set_gain(g);
        ui_button_bar_refresh();
        esp_lv_adapter_unlock();
    } else {
        printf("⚠ 取 LVGL 锁失败，增益未应用。\n");
    }
}

static const char *gain_usage(void)
{
    return "增益档位: 2.5 / 5 / 10 / 20 mm/mV（默认 5），也可写 0.5x / 1x / 2x / 4x";
}

/**
 * 启动一次回放。
 * @param fname SD 根目录下的文件名（`mit_105_500.bin` / `sr_060bpm.bin` / `alt-120bpm.bin`）
 * @param speed 倍速 1/2/4/8
 * @param gain  增益 mm/mV；<=0 表示不改（沿用 UI 当前档位）
 * @param mode  进入哪个测试模式（禁用 UI 按钮，防与回放打架）
 * @param dump_result ★T210：是否落盘峰序列 + 写结果文件。
 *        `mit-run`/`ladder-run` 传 **true**（对账要用，行为与 T209 之前逐字一致）；
 *        `play` 传 **false**（纯看波形：不写 SD、不占 SD 写带宽、不进测试模式）。
 * @return 0 = 已开始回放
 *
 * 🛑 加这个参数是**纯签名扩展**：两个既有调用点都传 true，分支内代码一行未改，
 *   所以 MIT/阶梯两条路径的落盘内容、顺序、时机全部不变（T210 验收要求）。
 */
static int start_replay(const char *fname, int speed, float gain, test_mode_t mode,
                        bool dump_result)
{
    if (!storage_sd_is_mounted()) {
        printf("SD 卡未挂载，无法回放。\n");
        return 1;
    }

    char path[96];
    bool found = false;

    /* 🛑 载荷不一定在根目录：`tools/make_sdcard_batch.py` 的 SD_SUB = "s7_batch"
     * ⇒ 实际文件是 `/sdcard/s7_batch/mit_105_500.bin`。两处都试，谁在用都能跑，
     * 也兼容需求文档里写的「SD 卡 mit_105_500.bin」字面布局。 */
    static const char *const kDirs[] = { "/sdcard/", "/sdcard/s7_batch/" };
    for (size_t i = 0; i < sizeof(kDirs) / sizeof(kDirs[0]); i++) {
        snprintf(path, sizeof(path), "%s%s", kDirs[i], fname);
        FILE *f = fopen(path, "rb");
        if (f != NULL) {
            fclose(f);
            found = true;
            break;
        }
    }
    if (!found) {
        printf("找不到 %s\n", fname);
        printf("已试过 /sdcard/ 与 /sdcard/s7_batch/。可用 scan 看根目录 .bin。\n");
        return 1;
    }
    printf("数据文件: %s\n", path);

    const bool running = ecg_pipeline_running();

    /* 1) 先停住投喂：保证下面的 BATCH-BEGIN 一定打在第一个 P 之前，序列不乱 */
    ecg_pipeline_pause();
    vTaskDelay(pdMS_TO_TICKS(10));

    /* 2) 序列头（与批处理同格式，ecg_batch_eval.py 直接可吃）
     * ★T210：只在 `dump_result` 时打。纯回放（play）不打 —— `BATCH-BEGIN` 是
     *   给批处理/对账脚本认的序列头，纯看波形时打出来会把下游解析器骗进来。 */
    if (dump_result) {
        printf("BATCH-BEGIN total=1\n");
        printf("# CardioLite S3 replay: fs=%d Hz, file=%s, speed=%dx, "
               "峰号=文件内相对样本号\n", APP_SAMPLE_RATE_HZ, fname, speed);
    }

    /* 3) 开 dump + 设倍速 + 定落盘文件。必须在样本开始喂之前生效。
     *
     * ★T210：整块由 `dump_result` 控制。play=false 时必须**显式关**，
     *   不能"什么都不做" —— dump 开关是跨调用残留的静态状态，上一次
     *   `mit-run` 打开过它就还是开的，纯回放会莫名其妙往 SD 写一份峰序列。 */
    char result_path[96];
    result_path[0] = '\0';

    /* 顺序与 T209 之前完全一致：先 speed、再 dump。别调换。 */
    ecg_pipeline_set_speed(speed);

    if (dump_result) {
        /* 结果文件名由数据文件名派生：
         *   mit_105_500.bin -> mit_105_result.txt
         *   sr_060bpm.bin   -> sr_060bpm_result.txt
         * 去 `.bin` 再去 `_500` 尾巴（记录号才是唯一标识）。 */
        char stem[64];
        snprintf(stem, sizeof(stem), "%.*s", (int)sizeof(stem) - 1, fname);
        char *dot = strrchr(stem, '.');
        if (dot != NULL) {
            *dot = '\0';
        }
        const size_t sl = strlen(stem);
        if (sl > 4 && strcmp(stem + sl - 4, "_500") == 0) {
            stem[sl - 4] = '\0';
        }
        snprintf(result_path, sizeof(result_path), "/sdcard/%s_result.txt", stem);

        ecg_pipeline_set_dump_file(result_path);   /* 峰序列同时落盘一份 */
        ecg_pipeline_set_rpeak_dump(true);
    } else {
        ecg_pipeline_set_dump_file(NULL);           /* 清掉上一次的路径 */
        ecg_pipeline_set_rpeak_dump(false);
    }
    /* ★T219：`PROGRESS` 只在**回放对账**（dump_result）时才有意义。
     * 485 实时源下 `s_dump_name` 是上次回放的文件名、`samples` 是开机累计，
     * 两个字段都会误导 ⇒ 这里按本次调用的语义显式设门。 */
    ecg_pipeline_set_prog(dump_result);

    /* 4) 启动 / 换文件。set_file 内部会把 s_paused 清掉从而自动恢复播放。 */
    if (running) {
        ecg_pipeline_set_file(path);
    } else {
        const esp_err_t e = ecg_pipeline_start(path, false);
        if (e != ESP_OK) {
            printf("启动管线失败: %s\n", esp_err_to_name(e));
            ecg_pipeline_set_rpeak_dump(false);
            return 1;
        }
        ecg_pipeline_detach_consumer();   /* 环缓交给 UI 绘制 */
    }

    /* 5) 增益（只影响绘制，不影响检测与峰序列 —— 对账结论不受它影响） */
    if (gain > 0.0f) {
        apply_gain(gain);
        printf("增益: %.2f mm/mV（1 mV = %.0f px）\n",
               (double)gain, (double)APP_PX_PER_MV(gain));
    } else {
        printf("增益: 沿用当前档位 %.2f mm/mV\n", (double)ui_waveform_get_gain());
    }

    test_mode_set(mode);

    if (dump_result) {
        printf("回放中：波形在屏上；串口只打进度（每 5 万样本一行），完整峰序列写 SD 卡。\n");
        printf("结果文件: %s（跑到 EOF 才有；中途 run-stop 不落盘）\n", result_path);
        printf("对账：把该文件拷到 PC，python tools/s3_verify_replay.py <文件>\n");
    } else {
        printf("纯回放中：波形在屏上；不写 SD、不进测试模式（UI 按钮保持可用）。\n");
        printf("改增益/走纸/冻结照常用屏上按钮。\n");
    }
    printf("中途停止用 run-stop。\n");
    return 0;
}

/* mit-run <rec> [gain] [--speed N] */
static struct {
    struct arg_str *rec;
    struct arg_str *gain;
    struct arg_int *speed;
    struct arg_end *end;
} s_mit_run_args;

static int cmd_mit_run(int argc, char **argv)
{
    const int nerrors = arg_parse(argc, argv, (void **)&s_mit_run_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, s_mit_run_args.end, argv[0]);
        printf("用法: mit-run <rec> [gain] [--speed 1|2|4|8]    例: mit-run 105 2x\n");
        printf("%s\n", gain_usage());
        return 1;
    }
    if (s_mit_run_args.rec->count == 0) {
        printf("用法: mit-run <rec> [gain] [--speed 1|2|4|8]    例: mit-run 105 2x\n");
        printf("%s\n", gain_usage());
        return 1;
    }

    const char *rec   = s_mit_run_args.rec->sval[0];
    const int   speed = (s_mit_run_args.speed->count > 0)
                        ? s_mit_run_args.speed->ival[0] : 1;

    float gain = 0.0f;      /* 0 = 不改，沿用 UI 当前档位 */
    if (s_mit_run_args.gain->count > 0) {
        const char *gs = s_mit_run_args.gain->sval[0];
        if (!parse_gain_arg(gs, &gain)) {
            printf("增益写法不认识: %s\n", gs);
            printf("%s\n", gain_usage());
            return 1;
        }
        if (!gain_is_supported(gain)) {
            printf("增益 %.2f mm/mV 不在档位内。\n", (double)gain);
            printf("%s\n", gain_usage());
            return 1;
        }
    }

    char fname[64];
    snprintf(fname, sizeof(fname), "mit_%s_500.bin", rec);
    return start_replay(fname, speed, gain, TEST_MIT, true);
}

/* ladder-run <bpm> [gain] [--speed N] */
static struct {
    struct arg_int *bpm;
    struct arg_str *gain;
    struct arg_int *speed;
    struct arg_end *end;
} s_ladder_run_args;

static int cmd_ladder_run(int argc, char **argv)
{
    const int nerrors = arg_parse(argc, argv, (void **)&s_ladder_run_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, s_ladder_run_args.end, argv[0]);
        printf("用法: ladder-run <bpm> [gain] [--speed 1|2|4|8]    例: ladder-run 60 2x\n");
        printf("%s\n", gain_usage());
        return 1;
    }
    if (s_ladder_run_args.bpm->count == 0) {
        printf("用法: ladder-run <bpm> [gain] [--speed 1|2|4|8]    例: ladder-run 60 2x\n");
        printf("%s\n", gain_usage());
        return 1;
    }

    const int bpm   = s_ladder_run_args.bpm->ival[0];
    const int speed = (s_ladder_run_args.speed->count > 0)
                      ? s_ladder_run_args.speed->ival[0] : 1;
    if (bpm <= 0 || bpm > 999) {
        printf("bpm 超出范围（1~999）。\n");
        return 1;
    }

    float gain = 0.0f;
    if (s_ladder_run_args.gain->count > 0) {
        const char *gs = s_ladder_run_args.gain->sval[0];
        if (!parse_gain_arg(gs, &gain)) {
            printf("增益写法不认识: %s\n", gs);
            printf("%s\n", gain_usage());
            return 1;
        }
        if (!gain_is_supported(gain)) {
            printf("增益 %.2f mm/mV 不在档位内。\n", (double)gain);
            printf("%s\n", gain_usage());
            return 1;
        }
    }

    /* 阶梯文件命名是三位补零：sr_060bpm.bin */
    char fname[64];
    snprintf(fname, sizeof(fname), "sr_%03dbpm.bin", bpm);
    return start_replay(fname, speed, gain, TEST_LADDER, true);
}

/* play <file> [gain] [--speed N] —— ★T210：通用回放入口（不猜命名规则）
 *
 * 为什么单独立一条，而不是给 mit-run 加个"任意名字"开关：
 *   `mit-run <rec>` 的 rec 是**记录号**，固件把它硬拼成 `mit_<rec>_500.bin`
 *   （`:1002` 上一行）。根目录那些 `alt-120bpm.bin` / `test-short.bin` /
 *   `ecg_dual_5min.bin` 既不符合 MIT 规则也不符合阶梯规则 ⇒ **一条命令都用不了**，
 *   只能点屏上「回放」按钮，而按钮只会选 `ecg.bin`(single) 与最大的 dual 文件。
 *   `play` 就是补这个缺口：**文件名原样贴进去**，不做任何拼装。
 *
 * 与 mit-run / ladder-run 的**三处刻意差异**（都是为了不动到 MIT 那条链）：
 *   ① `TEST_NORMAL` —— 不进测试模式，UI 按钮照常可用（MIT 路径仍是 TEST_MIT/LADDER）；
 *   ② `dump_result = false` —— 不写 `*_result.txt`、不占 SD 写带宽（MIT 路径仍写）；
 *   ③ 文件名**不补 `_500`、不派生 stem** —— 原样就用。
 *   ⇒ 两条 MIT 路径的落盘内容/顺序/时机逐字不变，`s3_verify_replay.py` 那套对账不受影响。
 *
 * 例：
 *   play alt-120bpm.bin
 *   play test-short 10x --speed 1        # 后缀可省，增益/倍速照 mit-run 的写法
 *   play ecg_dual_5min.bin               # LEN=103 双通道
 */
static struct {
    struct arg_str *file;
    struct arg_str *gain;
    struct arg_int *speed;
    struct arg_end *end;
} s_play_args;

static int cmd_play(int argc, char **argv)
{
    const int nerrors = arg_parse(argc, argv, (void **)&s_play_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, s_play_args.end, argv[0]);
        printf("用法: play <文件> [gain] [--speed 1|2|4|8]\n");
        printf("  例: play alt-120bpm.bin / play test-short --speed 4 / play ecg_dual_5min\n");
        printf("%s\n", gain_usage());
        return 1;
    }
    if (s_play_args.file->count == 0) {
        printf("用法: play <文件> [gain] [--speed 1|2|4|8]\n");
        return 1;
    }

    const int speed = (s_play_args.speed->count > 0)
                      ? s_play_args.speed->ival[0] : 1;

    float gain = 0.0f;
    if (s_play_args.gain->count > 0) {
        const char *gs = s_play_args.gain->sval[0];
        if (!parse_gain_arg(gs, &gain)) {
            printf("增益写法不认识: %s\n", gs);
            printf("%s\n", gain_usage());
            return 1;
        }
        if (!gain_is_supported(gain)) {
            printf("增益 %.2f mm/mV 不在档位内。\n", (double)gain);
            printf("%s\n", gain_usage());
            return 1;
        }
    }

    /* ---- 文件名归一：只做两件最小的事 ----
     *   ① 带路径就取最后一段（`/sdcard/ecg.bin` → `ecg.bin`）——
     *      因为 `start_replay()` 自己会拼 `/sdcard/` 与 `/sdcard/s7_batch/`，
     *      传入绝对路径会拼成 `/sdcard//sdcard/ecg.bin` 而找不到。
     *   ② 没有 `.bin` 后缀就补上（让人能偷懒少打 4 个字符）。
     * 🛑 除此之外**一个字节都不改** —— 不补 `_500`、不派生 stem，这是本条与 mit-run 的分界。 */
    const char *in = s_play_args.file->sval[0];
    const char *base = strrchr(in, '/');
    base = (base != NULL) ? (base + 1) : in;

    char fname[96];
    const size_t bl = strlen(base);
    const bool has_bin = (bl >= 4) &&
                         (strcasecmp(base + bl - 4, ".bin") == 0);
    snprintf(fname, sizeof(fname), "%s%s", base, has_bin ? "" : ".bin");

    return start_replay(fname, speed, gain, TEST_NORMAL, false);
}

/* run-stop：停止回放并关掉峰序列输出 */
static int cmd_run_stop(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    ecg_pipeline_set_rpeak_dump(false);
    ecg_pipeline_set_dump_file(NULL);   /* 半截数据不落盘（也没有 FILE 行，没法对账） */
    ecg_pipeline_pause();
    printf("回放已停止（峰序列输出与落盘均已关闭）。\n");
    return 0;
}

/* ---- result-dump <stem>：把 SD 卡上的回放结果文件回传到串口 ----
 *
 * 为什么需要它：回放过程串口只打进度（干净、不被 P 峰淹没），完整峰序列落盘在
 * `/sdcard/<stem>_result.txt`。但板子是 USB-JTAG CDC（不是 U 盘模式），PC 读不到卡内容，
 * 总不能每验一条就拔卡拷文件。于是提供这个**按需**回传：想看才拉，不看不打印。
 *
 * 体积：单条约 19 KB（`mit_105` = 18,587 B）；批处理汇总 771 KB（48 条 / 109,563 峰）。
 *
 * 🛑 **大文件回传的完整性靠 PC 侧，不靠串口自报**：USB-JTAG 突发输出会静默丢字节
 *   （2026-10-29 实测 771 KB 丢 5 行 `P` + 1 行被削掉前 3 字节，而 `# ---- END`
 *   自报的 771,619 B 与原文件分毫不差 ⇒ 那个数字证明不了传完整）。
 *   真因已定位在 **PC 接收端**（Windows 串口默认 RX 缓冲仅 4 KB + 接收循环里每块
 *   `flush()` 卡磁盘几十 ms ⇒ 驱动缓冲溢出）。`tools/s3_replay_capture.py` 改成
 *   `set_buffer_size(1 MB)` + 攒 64 KB 才 flush 后，同一份 771 KB 回传
 *   **逐行完全一致**（`tools/s3_dump_verify.py` 可复核）⇒ 固件无需节流。
 *
 * 输出格式与回放输出完全一致 ⇒ `tools/s3_verify_replay.py` / `tools/ecg_batch_eval.py` 直接吃。 */
static struct {
    struct arg_str *stem;
    struct arg_end *end;
} s_result_dump_args;

static int cmd_result_dump(int argc, char **argv)
{
    const int nerrors = arg_parse(argc, argv, (void **)&s_result_dump_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, s_result_dump_args.end, argv[0]);
        printf("用法: result-dump <stem>    例: result-dump mit_105\n");
        return 1;
    }
    if (s_result_dump_args.stem->count == 0) {
        printf("用法: result-dump <stem>    例: result-dump mit_105\n");
        return 1;
    }
    if (!storage_sd_is_mounted()) {
        printf("SD 卡未挂载。\n");
        return 1;
    }

    const char *stem = s_result_dump_args.stem->sval[0];
    char path[96];

    /* 三种写法（猛哥 2026-10-29：「result-dump mit_all 应该把 batch_mit_result.txt 也导出来」）
     *   ① 别名 mit_all / all / batch → 批处理 48 条的汇总结果 /sdcard/batch_mit_result.txt
     *      —— 必须单列：那个文件名是 `batch_mit_result.txt`，拼 `<stem>_result.txt` 推不出来。
     *   ② 带 `.txt`                   → 当完整文件名用（`result-dump batch_mit_result.txt`）
     *   ③ 其它（记录号）              → 拼 `<stem>_result.txt`，与 mit-run 落盘命名一致。 */
    if (strcmp(stem, "mit_all") == 0 || strcmp(stem, "all") == 0 || strcmp(stem, "batch") == 0) {
        snprintf(path, sizeof(path), "/sdcard/batch_mit_result.txt");
    } else if (strstr(stem, ".txt") != NULL) {
        snprintf(path, sizeof(path), "/sdcard/%.*s", (int)sizeof(path) - 9, stem);
    } else {
        snprintf(path, sizeof(path), "/sdcard/%s_result.txt", stem);
    }

    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        printf("找不到 %s\n", path);
        printf("写法: result-dump mit_105 | result-dump mit_all（批处理汇总）| result-dump xxx.txt\n");
        return 1;
    }

    printf("# ---- BEGIN %s ----\n", path);
    char   buf[512];
    size_t n;
    size_t total = 0;
    while ((n = fread(buf, 1, sizeof(buf) - 1, f)) > 0) {
        buf[n] = '\0';
        printf("%s", buf);
        total += n;
    }
    fclose(f);
    printf("\n# ---- END %s (%u B) ----\n", path, (unsigned)total);
    return 0;
}

/* ---- restart：软重启（跑批处理登记后用它触发重启）---- */
static int cmd_restart(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    printf("重启中...\n");
    esp_restart();
    return 0;
}

/* ---- rpeak：R 峰打点显示开关 + QRS 序列记录（★T216 / ★T217）----
 *
 *   rpeak                查询：打点开关 + 记录状态与剩余时间
 *   rpeak on|off         打点显示开关（**只停绘制，不停检测**，BPM 不受影响）
 *   rpeak rec            开始记录 QRS 峰序列，**固定 300 s（5 min）到点自动落盘**
 *   rpeak rec off        提前终止并立即落盘
 *
 * 🛑 时长为什么固定 300 s、且不给参数：32 KB 缓冲在 300 bpm 下 5 min 只占 33%，
 *   10 min 占 64%，15 min 就贴到 98% 的边；而**溢出是静默截断**（只置
 *   `s_dump_overflow`，不报错）⇒ 开放任意时长等于给用户一个"看起来记了、其实丢了"
 *   的坑。要记更久必须先走 T215 §2.6 方案甲（缓冲挪 PSRAM）。
 *
 * 🛑 打点是 **ESP32 本地**功能，不是 `485-send` —— STM32 侧全工程 grep
 *   `rpeak/RPEAK/R波` 零命中，R 峰是本地算的，做成下行命令那边也没人执行。
 *
 * 🛑 记录**只在 485 实时源下允许**：FILE 源的 `s_dump_path` 归 `mit-run` 用，
 *   实时记录若共用同一个文件路径会覆盖批处理结果。
 *
 * 🛑 记录内容目前只有峰号（`P <相对号>`，16 峰/行），**不含峰处波形值** ⇒
 *   能算 RR 间期 / RMSSD / 抖动 σ，但**判不了"点是否正好落在峰顶"**。后者要改
 *   格式，会动到 `ecg_batch_eval.py` 等批处理口径，另行定（T215 §2.6）。 */
#define RPEAK_REC_PATH   "/sdcard/rpeak_hrv.txt"
#define RPEAK_REC_SECS   300u      /* 固定 5 min，见上面容量说明 */

static volatile bool     s_rec_running = false;
static volatile bool     s_rec_cancel  = false;
static volatile uint32_t s_rec_end_ms  = 0;

/* 记录任务：每 100 ms 检查一次，到点或收到 cancel 就收尾落盘。
 * 🛑 不用 vTaskDelete 强杀 —— 杀在临界点会留下半截状态；用标志位让它自己退。 */
static void rpeak_rec_worker(void *arg)
{
    (void)arg;
    const uint32_t ticks = (RPEAK_REC_SECS * 1000u) / 100u;

    for (uint32_t i = 0; i < ticks; i++) {
        if (s_rec_cancel) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }

    const bool cancelled = s_rec_cancel;

    ecg_pipeline_set_rpeak_dump(false);
    ecg_pipeline_flush_dump();          /* 实时源没有 EOF ⇒ 必须主动落盘 */
    s_rec_running = false;
    s_rec_end_ms  = 0;

    /* ★T219：**必须在控制台打一行**。默认全局日志被压到 ERROR
     * （`main.c`：esp_log_level_set("*", ESP_LOG_ERROR)），只靠 ESP_LOGI
     * 的话「记完了」这件事用户根本看不见 —— 实测就有人问"到点没提示吗"。 */
    if (cancelled) {
        printf("\n[rpeak rec] 已提前停止，落盘完成 -> %s\n", RPEAK_REC_PATH);
    } else {
        printf("\n[rpeak rec] ★ %u s 到点，已自动落盘 -> %s\n",
               (unsigned)RPEAK_REC_SECS, RPEAK_REC_PATH);
    }
    printf("            取回：result-dump rpeak_hrv.txt   分析：py hrv_analyze.py rpeak_hrv.txt\n");

    ESP_LOGI(TAG, "rpeak rec done (%u s, cancelled=%d) -> " RPEAK_REC_PATH,
             (unsigned)RPEAK_REC_SECS, (int)cancelled);
    vTaskDelete(NULL);
}

static int cmd_rpeak(int argc, char **argv)
{
    /* ---- 查询 ---- */
    if (argc < 2) {
        printf("R 峰打点：%s\n", ui_waveform_get_rp_show() ? "on" : "off");
        if (s_rec_running) {
            const uint32_t now  = (uint32_t)(esp_timer_get_time() / 1000);
            const uint32_t left = (s_rec_end_ms > now) ? (s_rec_end_ms - now) : 0;
            printf("QRS 记录：on（剩 %lu s）-> %s\n",
                   (unsigned long)(left / 1000u), RPEAK_REC_PATH);
        } else {
            printf("QRS 记录：off\n");
        }
        printf("用法: rpeak on|off | rpeak rec | rpeak rec off\n");
        return 0;
    }

    /* ---- rpeak rec / rpeak rec off ---- */
    if (strcasecmp(argv[1], "rec") == 0) {
        if (argc >= 3 && strcasecmp(argv[2], "off") == 0) {
            if (!s_rec_running) {
                printf("当前没在记录\n");
                return 0;
            }
            s_rec_cancel = true;        /* 任务会在 ≤100 ms 内收尾落盘 */
            printf("QRS 记录停止中，正在落盘…\n");
            return 0;
        }
        if (argc >= 3) {
            printf("记录时长固定 %u s（5 min），不需要参数\n", (unsigned)RPEAK_REC_SECS);
            printf("用法: rpeak rec     （开始）| rpeak rec off（提前停）\n");
            return 1;
        }

        if (!ecg_pipeline_src_is_rs485()) {
            printf("⚠ 只在 485 实时源下记录（FILE 源会覆盖 mit-run 的批处理结果）。先 485-start。\n");
            return 1;
        }
        if (!storage_sd_is_mounted()) {
            printf("SD 卡未挂载，无法记录。\n");
            return 1;
        }
        if (s_rec_running) {
            printf("已在记录中（剩 %lu s），先 `rpeak rec off`。\n",
                   (unsigned long)((s_rec_end_ms > (uint32_t)(esp_timer_get_time() / 1000))
                                   ? ((s_rec_end_ms - (uint32_t)(esp_timer_get_time() / 1000)) / 1000u)
                                   : 0u));
            return 1;
        }

        ecg_pipeline_set_dump_file(RPEAK_REC_PATH);
        ecg_pipeline_set_rpeak_dump(true);
        /* ★T219：实时记录不打 PROGRESS（字段含义在 485 源下全是假的，见 ecg_pipeline.h） */
        ecg_pipeline_set_prog(false);

        s_rec_cancel  = false;
        s_rec_running = true;
        s_rec_end_ms  = (uint32_t)(esp_timer_get_time() / 1000) + RPEAK_REC_SECS * 1000u;

        if (xTaskCreate(rpeak_rec_worker, "rpeak_rec", 3072, NULL, 2, NULL) != pdPASS) {
            s_rec_running = false;
            s_rec_end_ms  = 0;
            ecg_pipeline_set_rpeak_dump(false);
            printf("创建记录任务失败（内存不足？）\n");
            return 1;
        }
        printf("QRS 记录开始：%u s（5 min）-> %s\n",
               (unsigned)RPEAK_REC_SECS, RPEAK_REC_PATH);
        printf("  到点自动落盘；提前停用 `rpeak rec off`；回传 `result-dump rpeak_hrv.txt`\n");
        return 0;
    }

    /* ---- rpeak on|off ---- */
    bool on;
    if (strcasecmp(argv[1], "on") == 0) {
        on = true;
    } else if (strcasecmp(argv[1], "off") == 0) {
        on = false;
    } else {
        printf("用法: rpeak on|off | rpeak rec | rpeak rec off\n");
        return 1;
    }

    /* 内部会失效画布并立刻重绘一帧 ⇒ 必须持 LVGL 锁 */
    if (esp_lv_adapter_lock(-1) != ESP_OK) {
        printf("拿不到 LVGL 锁，未执行\n");
        return 1;
    }
    ui_waveform_set_rp_show(on);
    esp_lv_adapter_unlock();

    printf("R 峰打点 -> %s（检测与 BPM 不受影响）\n", on ? "on" : "off");
    return 0;
}

/* ---- mem：内部 RAM / PSRAM 真实容量（★T216）----
 *
 * 为什么需要：`485-stat` 只打 `int_heap`（内部 RAM），PSRAM 一直没读数。
 * 评估「把 32 KB 峰序列缓冲挪到 PSRAM」这类方案时，必须知道**可用堆**到底多少 ——
 * 物理 8 MB ≠ 可用 8 MB：本工程开了 `SPIRAM_XIP_FROM_PSRAM` +
 * `FETCH_INSTRUCTIONS` + `RODATA`，固件的一部分代码/常量常驻 PSRAM。
 * 硬件实测（启动日志）：`octal_psram: density 0x03 (64 Mbit)` ⇒ 8 MB。 */
static int cmd_mem(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    const size_t i_tot  = heap_caps_get_total_size(MALLOC_CAP_INTERNAL);
    const size_t i_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    const size_t i_big  = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL);
    const size_t p_tot  = heap_caps_get_total_size(MALLOC_CAP_SPIRAM);
    const size_t p_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const size_t p_big  = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);

    printf("内部RAM  : 总 %lu KB   空闲 %lu KB   最大连续 %lu KB\n",
           (unsigned long)(i_tot / 1024), (unsigned long)(i_free / 1024),
           (unsigned long)(i_big / 1024));
    if (p_tot == 0) {
        printf("PSRAM    : ★未启用/未检测到（0 B）\n");
    } else {
        printf("PSRAM    : 总 %lu KB   空闲 %lu KB   最大连续 %lu KB\n",
               (unsigned long)(p_tot / 1024), (unsigned long)(p_free / 1024),
               (unsigned long)(p_big / 1024));
        printf("           （物理 8 MB，差值＝XIP 代码/只读数据常驻部分）\n");
    }
    return 0;
}

/* ---- ui-clear：执行一次清屏（与屏上「清屏」键同一段，★T212）----
 *
 * 为什么需要它：清屏只能点屏 ⇒ 自动化验收（`_t211_clear_probe.py --trigger cli`）
 * 触发不了，回归只能靠人手点。本命令把动作暴露给 CLI，脚本即可自己跑闭环。
 *
 * 🛑 只覆盖**动作层**（`ui_clear_apply()`），不覆盖 LVGL 事件分发
 *   （`on_btn_click` 的 `case BTN_CLEAR`）。事件路径改动过就必须人手点一次确认
 *   —— 别拿本命令的通过去证明"按钮也能用"。
 * 🛑 必须持 LVGL 锁：`ui_bpm_reset()` / `ui_button_bar_refresh()` 都会动 lv 对象；
 *   本函数跑在 console 任务里，不持锁直接改 LVGL 是未定义行为。 */
static int cmd_ui_clear(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    if (!app_state_ro()->file_ok) {
        printf("无数据文件 ⇒ 清屏无意义（与屏上灰键一致）\n");
        return 1;
    }
    if (esp_lv_adapter_lock(-1) != ESP_OK) {
        printf("拿不到 LVGL 锁，清屏未执行\n");
        return 1;
    }
    ui_clear_apply();
    esp_lv_adapter_unlock();

    printf("清屏已执行：只清视觉（波形 / R 峰 / BPM），不改播放状态、不归零位置\n");
    return 0;
}

/* ---- log：开/关串口日志级别 ----
 * 上电默认全局日志被压到 ERROR（见 main.c 的 app_main 开头），串口干净。
 * 调试时按需 `log info/debug/verbose` 打开，`log off` 关回全静默。 */
static int cmd_log(int argc, char **argv)
{
    if (argc != 2) {
        printf("用法: log <off|error|warn|info|debug|verbose>\n");
        printf("当前全局日志级别: %d（0=off 1=error 2=warn 3=info 4=debug 5=verbose）\n",
               (int)esp_log_level_get("*"));
        return 1;
    }

    esp_log_level_t lvl;
    if (strcmp(argv[1], "off") == 0) {
        lvl = ESP_LOG_NONE;
    } else if (strcmp(argv[1], "error") == 0) {
        lvl = ESP_LOG_ERROR;
    } else if (strcmp(argv[1], "warn") == 0) {
        lvl = ESP_LOG_WARN;
    } else if (strcmp(argv[1], "info") == 0) {
        lvl = ESP_LOG_INFO;
    } else if (strcmp(argv[1], "debug") == 0) {
        lvl = ESP_LOG_DEBUG;
    } else if (strcmp(argv[1], "verbose") == 0) {
        lvl = ESP_LOG_VERBOSE;
    } else {
        printf("未知级别: %s\n", argv[1]);
        return 1;
    }

    esp_log_level_set("*", lvl);
    printf("全局日志级别 -> %s\n", argv[1]);

    return 0;
}

/* ============================================================================
 * 命令注册
 * ========================================================================== */

/* ============================================================================
 * S9a：485 实时源
 *
 * 数据源切换的语义：
 *   485-start  → 停掉当前源（含 SD 回放的暂停态）→ 装 UART1 → 收到帧即推环缓
 *   485-stop   → 停 485 源，**回到上电默认**（SD 有文件则重启 FILE 源并暂停）
 *
 * UI 一行都不用改：绘制定时器不读播放状态，数据进了环缓下一帧就画出来
 * （见 ui_waveform.c:579 on_frame / :594 pull_samples）。
 * ========================================================================== */

static int cmd_485_start(int argc, char **argv)
{
    uint32_t baud = APP_485_BAUD_DEFAULT;
    if (argc >= 2) {
        char *endp = NULL;
        const long v = strtol(argv[1], &endp, 10);
        if (endp == argv[1] || v < 1200 || v > 3000000) {
            printf("波特率不合法: %s（允许 1200 ~ 3000000）\n", argv[1]);
            return 1;
        }
        baud = (uint32_t)v;
    }

    const esp_err_t err = ecg_pipeline_start_rs485(baud);
    if (err != ESP_OK) {
        printf("485 启动失败: %s\n", esp_err_to_name(err));
        return 1;
    }

    printf("485 源已启动：UART%d  TX=IO%d->DI  RX=IO%d<-RO  @%lu 8N1\n",
           APP_485_UART_NUM, APP_485_TX_GPIO, APP_485_RX_GPIO,
           (unsigned long)rs485_baud());
    printf("  差分线：A=P1-10(TX+)  B=P1-9(TX-)；方向由硬件自动切换\n");
    /* 🛑 旧文案已删（2026-11-04 18:08）：原来还有一行是 S9a「PC 发波形」时期写的
     *    「PC 侧：python tools/rs485_stream.py --port COM27」—— 现在**每一处都是错的**
     *    （COM27 早不存在、PC 也已不在链路上）。猛哥就是被它误判成"板上还在跑旧的
     *    PC 测试固件"。凡是会随架构失效的提示，宁可只留判据、不留地址。 */
    /* ★T209（11-05）：屏上「开始」按钮已**永久置灰**，采集启停只在 CLI 里做。 */
    printf("  STM32 侧：485-send start（发 0x01）／ 485-send stop（发 0x02）\n");
    printf("  ⚠ 屏上「开始」键恒灰，不是坏了 —— 没有 STOP 键，按钮文字一旦变「采集中」就回不去\n");
    printf("  STM32 停在 STOP 时 rx=0 属正常；485-stat 看对账（frames 在涨 = 真在出数据）\n");
    /* 仍刷一次：布局与其它键的灰显状态要靠它初始化（本函数可能早于首次 UI tick） */
    ui_button_bar_refresh();
    return 0;
}

static int cmd_485_stop(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    if (!ecg_pipeline_src_is_rs485()) {
        printf("当前不是 485 源（无操作）\n");
        return 0;
    }

    ecg_pipeline_stream_stat_t st;
    ecg_pipeline_get_stream_stat(&st);
    rs485_stat_t rs;
    rs485_get_stat(&rs);

    ecg_pipeline_stop();
    printf("485 源已停止：收 %lu B / %lu 帧，crc_err=%lu seq_gaps=%lu\n",
           (unsigned long)rs.rx_bytes, (unsigned long)st.frames,
           (unsigned long)st.crc_errors, (unsigned long)st.seq_gaps);

    /* 回到上电默认：SD 上还有文件就重启 FILE 源并暂停，等用户点「回放」。
     * 这样 UI 状态、BPM 归 `--` 的行为与重新上电完全一致，不留半截状态。 */
    if (storage_sd_is_mounted()) {
        const storage_sd_files_t *files = storage_sd_get_files();
        const char *path = (files->single[0] != '\0') ? files->single
                                                      : APP_SD_FILE_PATH;
        const esp_err_t e = ecg_pipeline_start(path, false);
        if (e == ESP_OK) {
            ecg_pipeline_detach_consumer();
            ecg_pipeline_pause();
            printf("已回到 SD 回放（%s，暂停态），点「回放」按钮继续\n", path);
        } else {
            printf("重启 SD 回放失败: %s\n", esp_err_to_name(e));
        }
    } else {
        printf("SD 未挂载，保持空闲（重新上电可恢复）\n");
    }

    /* ★T209：源已切走 ⇒ 刷一次按钮（「开始」键恒灰，此处只为其它键的灰显状态跟上） */
    ui_button_bar_refresh();
    return 0;
}

/* ---- 485-perf [secs]：窗口内实测帧率 / 丢帧 / 帧长（★T214）----
 *
 * 为什么需要它：`485-stat` 打的全是**开机以来的累计值**，要算速率得自己
 * 隔一段时间发两次、把数字相减、再掐表。三个问题：
 *   ① 分母靠人掐表 ⇒ 每次口径不同，换固件版本后数字无法对比；
 *   ② 30 min 长稳没法手工抄；
 *   ③ 手工采样容易把多帧打在同一时间戳上（CH340 的 read(4096) 实测阻塞
 *      3.49 s）—— 曾据此算出 46 fps 的假值，真值 19.6 fps。
 *
 * 本命令把口径钉死：分母一律用 **esp_timer 实测窗口时长**，
 * 不用命令行给的秒数、也不用采样周期。
 *
 * 🛑 判双通道只看 `帧长`：59 B/帧=单通道，109 B/帧=双通道（Δrx/Δframes）。
 *   屏上画布切了**不代表**下行生效（T209 的教训）。
 */
static int cmd_485_perf(int argc, char **argv)
{
    uint32_t secs = 10;
    if (argc >= 2) {
        char *endp = NULL;
        const long v = strtol(argv[1], &endp, 10);
        if (endp == argv[1] || v < 1 || v > 3600) {
            printf("用法: 485-perf [秒数 1~3600]，默认 10\n");
            return 1;
        }
        secs = (uint32_t)v;
    }

    if (!ecg_pipeline_src_is_rs485()) {
        printf("⚠ 当前不是 485 源（FILE 回放），以下统计不会增长。先 485-start。\n");
    }

    ecg_pipeline_stream_stat_t s0, s1;
    rs485_stat_t r0, r1;
    ecg_pipeline_stats_t p0, p1;
    memset(&s0, 0, sizeof s0); memset(&s1, 0, sizeof s1);
    memset(&r0, 0, sizeof r0); memset(&r1, 0, sizeof r1);
    memset(&p0, 0, sizeof p0); memset(&p1, 0, sizeof p1);

    ecg_pipeline_get_stream_stat(&s0);
    rs485_get_stat(&r0);
    ecg_pipeline_get_stats(&p0);

    const uint32_t t0 = (uint32_t)(esp_timer_get_time() / 1000);
    uint32_t ring_peak = p0.ring_level;

    printf("采样 %lu s …（期间别发别的命令，避免扰动）\n", (unsigned long)secs);
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(50));
        ecg_pipeline_stats_t p;
        ecg_pipeline_get_stats(&p);
        if (p.ring_level > ring_peak) {
            ring_peak = p.ring_level;
        }
        if ((uint32_t)(esp_timer_get_time() / 1000) - t0 >= secs * 1000u) {
            break;
        }
    }
    const uint32_t t1    = (uint32_t)(esp_timer_get_time() / 1000);
    const uint32_t dt_ms = t1 - t0;

    ecg_pipeline_get_stream_stat(&s1);
    rs485_get_stat(&r1);
    ecg_pipeline_get_stats(&p1);

    const uint32_t dframes = s1.frames - s0.frames;
    const uint32_t dbytes  = r1.rx_bytes - r0.rx_bytes;
    const uint32_t dpushed = p1.pushed - p0.pushed;
    const double   dt_s    = (double)dt_ms / 1000.0;
    const double   fps     = (dt_s > 0.0) ? (double)dframes / dt_s : 0.0;
    const double   fps_byt = (dt_s > 0.0) ? (double)dbytes / dt_s : 0.0;
    const double   frame_b = (dframes > 0u) ? (double)dbytes / (double)dframes : 0.0;
    const double   expect  = (double)APP_SAMPLE_RATE_HZ * dt_s;

    printf("\n==== 485-perf  实测窗口 %lu ms ====\n", (unsigned long)dt_ms);
    printf("帧      : +%lu 帧  →  %.2f fps        （判据 ≥19 fps）\n",
           (unsigned long)dframes, fps);
    printf("字节    : +%lu B   →  %.1f B/s   帧长 %.2f B/帧（59=单通道 / 109=双通道）\n",
           (unsigned long)dbytes, fps_byt, frame_b);
    printf("样本    : +%lu  →  %.1f /s   理论 %.0f（%d Hz × %.2f s）\n",
           (unsigned long)dpushed, (dt_s > 0.0) ? (double)dpushed / dt_s : 0.0,
           expect, APP_SAMPLE_RATE_HZ, dt_s);
    printf("错误    : crc_err +%lu  resync +%lu B  bad_len +%lu  seq_gaps +%lu\n",
           (unsigned long)(s1.crc_errors - s0.crc_errors),
           (unsigned long)(s1.resync_bytes - s0.resync_bytes),
           (unsigned long)(s1.bad_len - s0.bad_len),
           (unsigned long)(s1.seq_gaps - s0.seq_gaps));
    printf("链路    : parity +%lu  frame_err +%lu  ovf +%lu  chunks +%lu  tx +%lu B\n",
           (unsigned long)(r1.parity_err - r0.parity_err),
           (unsigned long)(r1.frame_err - r0.frame_err),
           (unsigned long)(r1.fifo_ovf - r0.fifo_ovf),
           (unsigned long)(r1.rx_chunks - r0.rx_chunks),
           (unsigned long)(r1.tx_bytes - r0.tx_bytes));
    printf("环缓    : peak %lu/%d   drop +%lu   水位末值 %lu\n",
           (unsigned long)ring_peak, ECG_RING_CAPACITY,
           (unsigned long)(p1.dropped - p0.dropped),
           (unsigned long)p1.ring_level);
    printf("内存    : int_heap %lu → %lu KB（Δ%+ld）\n",
           (unsigned long)(p0.heap_int_free / 1024), (unsigned long)(p1.heap_int_free / 1024),
           (long)((int32_t)(p1.heap_int_free / 1024) - (int32_t)(p0.heap_int_free / 1024)));

    /* ---- 判定：只打结论，不替人拍板 ---- */
    const bool ok_fps   = (fps >= 19.0);
    const bool ok_crc   = ((s1.crc_errors - s0.crc_errors) == 0u);
    const bool ok_ovf   = ((r1.fifo_ovf - r0.fifo_ovf) == 0u);
    const bool ok_gap   = ((s1.seq_gaps - s0.seq_gaps) <= (dframes / 1000u));
    const bool ok_drop  = ((p1.dropped - p0.dropped) == 0u);

    printf("\n判定    : 帧率 %s   crc %s   ovf %s   seq_gaps %s   drop %s\n",
           ok_fps  ? "✓" : "✗", ok_crc ? "✓" : "✗", ok_ovf ? "✓" : "✗",
           ok_gap  ? "✓" : "✗", ok_drop ? "✓" : "✗");
    printf("          %s\n", (ok_fps && ok_crc && ok_ovf && ok_gap && ok_drop)
                             ? "⇒ 本窗口 PASS" : "⇒ 本窗口 FAIL（见上面逐项）");
    if (dframes > 0u && frame_b > 100.0 && frame_b < 115.0) {
        printf("          双通道已生效（帧长 %.1f B/帧）\n", frame_b);
    }
    return 0;
}

static int cmd_485_stat(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    ecg_pipeline_stream_stat_t st;
    ecg_pipeline_get_stream_stat(&st);
    rs485_stat_t rs;
    rs485_get_stat(&rs);
    ecg_pipeline_stats_t ps;
    ecg_pipeline_get_stats(&ps);
    ecg_pipeline_hr_t hr;
    const bool hrok = ecg_pipeline_get_hr(&hr);

    printf("源        : %s%s\n", st.active ? "RS485" : "FILE（SD 回放）",
           ecg_pipeline_running() ? "" : " / 未运行");
    printf("UART      : %s  UART%d @%lu 8N1  TX=IO%d RX=IO%d\n",
           rs485_ready() ? "ready" : "未初始化", APP_485_UART_NUM,
           (unsigned long)rs485_baud(), APP_485_TX_GPIO, APP_485_RX_GPIO);
    printf("链路      : rx=%lu B  tx=%lu B  chunks=%lu  parity=%lu  frame=%lu  ovf=%lu\n",
           (unsigned long)rs.rx_bytes, (unsigned long)rs.tx_bytes,
           (unsigned long)rs.rx_chunks, (unsigned long)rs.parity_err,
           (unsigned long)rs.frame_err, (unsigned long)rs.fifo_ovf);
    printf("解帧      : frames=%lu  crc_err=%lu  resync=%lu B  bad_len=%lu  seq_gaps=%lu\n",
           (unsigned long)st.frames, (unsigned long)st.crc_errors,
           (unsigned long)st.resync_bytes, (unsigned long)st.bad_len,
           (unsigned long)st.seq_gaps);
    printf("快照      : snapshots=%lu（★>0 即证明下行通：0x10 回执只由命令生成）\n",
           (unsigned long)st.snapshots);
    /* ★T209（11-05）：STA 回读 —— `485-send mode/notch` 的**唯一验收口**。
     * 🛑 此前 `app_state_set_sta()` 全工程零调用 ⇒ 这里读到的永远是 unknown，
     *    屏上「诊断/陷波」也是恒 0x02 的假值。现在由 `ecg_pipeline.c` 在每帧
     *    显示帧上抛（生产者任务写，CLI/UI 读，8 位访问无需加锁）。 */
    {
        const app_state_t *as = app_state_ro();
        char stas[64];
        ecg_sta_to_str(as->sta, as->has_sta, stas, sizeof(stas));

        /* ★T213（11-06）：**帧龄 + 静态说明**。
         *
         * 🛑 `sta` 只在 485 生产者收到显示帧时刷新（`ecg_pipeline.c:436`），停流后
         *   **不会失效**、`has_sta` 也永远为真 ⇒ 单看这一行，分不出「正在通信」
         *   和「已经停了半小时」。而 `485-send mode/notch` 的唯一验收口就是它：
         *   下行丢包（当前成功率约 50%）时 STA 不变，会被误读成"命令生效"。
         *
         * ⇒ 三个判据一起给：
         *   ① 距上次更新多少秒（`app_state.sta_ms`，T213 新增）
         *   ② ≥2 s 直接标 ★陈旧（500 Hz 下每帧 25 样本＝50 ms，2 s 已漏 40 帧）
         *   ③ FILE 源下明确说"本行不刷新"（该源压根不写 sta）
         */
        const uint32_t now_ms = (uint32_t)(esp_timer_get_time() / 1000);
        const uint32_t age_ms = now_ms - as->sta_ms;   /* uint32 回绕相减仍正确 */
        const double   age_s  = (double)age_ms / 1000.0;

        if (!as->has_sta) {
            printf("STATUS    : %s  (raw=0x%02X ★从未收到带 STA 的帧)\n",
                   stas, (unsigned)as->sta);
        } else if (!ecg_pipeline_src_is_rs485()) {
            printf("STATUS    : %s  (raw=0x%02X, 距更新 %.1f s；"
                   "★当前是 FILE 源，本行不刷新)\n",
                   stas, (unsigned)as->sta, age_s);
        } else if (age_ms >= 2000u) {
            printf("STATUS    : %s  (raw=0x%02X, 距更新 %.1f s "
                   "★陈旧：STM32 已停流，这是最后一帧的值)\n",
                   stas, (unsigned)as->sta, age_s);
        } else {
            printf("STATUS    : %s  (raw=0x%02X, 距更新 %.1f s；停流时不刷新)\n",
                   stas, (unsigned)as->sta, age_s);
        }
    }
    printf("环缓      : pushed=%lu  dropped=%lu  水位=%u 帧=%lu\n",
           (unsigned long)ps.pushed, (unsigned long)ps.dropped,
           (unsigned)ps.ring_level, (unsigned long)ps.frames_read);
    printf("心率      : %s  bpm=%.1f  peaks=%lu  fed=%lu\n",
           (hrok && hr.bpm > 0.0f) ? "ok" : "未就绪（稳定窗内或未接到数据）",
           (hrok ? (double)hr.bpm : 0.0), (unsigned long)(hrok ? hr.peak_total : 0),
           (unsigned long)(hrok ? hr.fed : 0));
    return 0;
}

/* ---- 485-send（★T208 / ★T209）----
 *
 *   485-send start     发 `0x01`（整帧 AA 55 80 01 00 01 5D）—— ★不幂等，只发一帧
 *   485-send stop      发 `0x02`（整帧 AA 55 80 01 00 02 54）—— 幂等，无回执
 *   485-send snap      发 `0x10`（整帧 AA 55 80 01 00 10 2A）—— 仅 STOP 态回 80 B 快照
 *   485-send mode 0|1  发 `0x04`（AA 55 80 02 00 04 00 EF / ...01 E8）诊断 / 监护
 *   485-send notch 0|1 发 `0x05`（AA 55 80 02 00 05 00 FA / ...01 FD）陷波关 / 开
 *   485-send ch2 0|1   发 `0x06`（AA 55 80 02 00 06 00 C5 / ...01 C2）仅I / I&II
 *   （六条 CRC 均已按 poly=0x07/init=0x00/不反射 实算，并用 START/STOP/SNAP 三帧自检过）
 *
 * ★T209（11-05）起屏上「开始」按钮永久置灰，**采集启停只有这一条路**：
 *   ⚠ 且 `0x01`/`0x02` 都**不会**让按钮文字变化（那是 T208 方案，已撤）。
 *     要判"采集是否真在跑"，只看 `485-stat` 的 `frames` 在不在涨。
 *
 * 🛑 **不提供"原样发十六进制"**（猛哥 11-04 17:44 拍板，两条理由）：
 *   ① 它与 `start` 发的是**同一串字节** —— 多一条入口就多一处误用面；
 *   ② CRC 到底有没有在校验，不需要造坏帧去试：**用代码审 + 统计量**即可。
 *      硬件乱码会自己显形在 `485-stat` 上：
 *        `crc_err`（CRC 错帧）／`resync`（重同步跳过的字节）／`bad_len`（LEN 非法）
 *        ／`frame`+`parity`（UART 层帧错/校验错）。
 *      ⇒ 这些数一起涨＝硬件/接线问题；全为 0 而没波形＝别再怀疑 CRC。
 */
static int cmd_485_send(int argc, char **argv)
{
    if (argc < 2) {
        printf("用法: 485-send start|stop|snap|mode|notch|ch2\n");
        printf("  start     发 0x01 START（与屏上「开始」按钮同一条）\n");
        printf("  stop      发 0x02 STOP（本设计不设停止键，停采集用它）\n");
        printf("  snap      发 0x10 读快照（★STOP 态才有回执；看 485-stat 的 snapshots）\n");
        printf("  mode 0|1  发 0x04 SET_MODE 诊断(0)/监护(1)   ★回执看 STA.bit0\n");
        printf("  notch 0|1 发 0x05 SET_NOTCH 关(0)/开(1)      ★回执看 STA.bit1\n");
        printf("  ch2 0|1   发 0x06 SET_CHANNEL 仅I(0)/I&II(1)  ★仅 STOP 态，回 80 B 快照\n");
        return 1;
    }
    if (!rs485_ready()) {
        printf("485 未初始化，先执行 485-start\n");
        return 1;
    }

    if (strcmp(argv[1], "start") == 0) {
        const esp_err_t e = ecg_cmd_start();
        printf("START(0x01) -> %s\n", esp_err_to_name(e));
        return (e == ESP_OK) ? 0 : 1;
    }
    if (strcmp(argv[1], "stop") == 0) {
        const esp_err_t e = ecg_cmd_stop();
        printf("STOP(0x02) -> %s\n", esp_err_to_name(e));
        return (e == ESP_OK) ? 0 : 1;
    }
    if (strcmp(argv[1], "snap") == 0) {
        /* ★T208（11-05）：整帧 AA 55 80 01 00 10 2A。
         * 回执是 80 B 快照帧，落在 `485-stat` 的 snapshots 上（不进波形）。
         * 🛑 STOP 态才有回执 —— RUN 态静默是**协议规定**，不是丢包。 */
        ecg_pipeline_stream_stat_t st0, st1;
        ecg_pipeline_get_stream_stat(&st0);
        const esp_err_t e = ecg_cmd_snapshot();
        if (e != ESP_OK) {
            printf("SNAPSHOT(0x10) -> %s\n", esp_err_to_name(e));
            return 1;
        }
        /* 等 0.6 s 再取一次：STM32 回 80 B ≈ 6.9 ms，但要走完 UART+任务调度 */
        vTaskDelay(pdMS_TO_TICKS(600));
        ecg_pipeline_get_stream_stat(&st1);
        const uint32_t dn = st1.snapshots - st0.snapshots;
        printf("SNAPSHOT(0x10) -> OK   快照回执 %lu 帧\n", (unsigned long)dn);
        if (dn == 0u) {
            printf("  ⇒ 没有回执。两种可能，都别急着怪代码：\n");
            printf("     ① STM32 正在 RUN（协议规定 RUN 态静默）—— 先 485-send stop\n");
            printf("     ② 下行没到 STM32（线路/极性/UT-2211 接收侧）\n");
            printf("     看 485-stat：bad_len/crc_err 一起涨＝线上有问题\n");
            return 1;
        }
        return 0;
    }

    /* ---- ★T209（11-05）：三条带 ARG 的采集端参数命令 ----
     *
     *   mode 0|1   0x04 SET_MODE     0=诊断 1=监护
     *   notch 0|1  0x05 SET_NOTCH    0=关   1=开
     *   ch2 0|1    0x06 SET_CHANNEL  0=仅I  非0=I&II
     *
     * 三条**验收方式不同**（见 `ecg_cmd.h` 的口径表）：
     *   mode / notch → 回执只在**下一帧 STA** 里 ⇒ 必须 RUN 态才看得到；
     *   ch2          → 回一帧 **80 B 快照**（REQID=0x06）⇒ 必须 **STOP 态**才发得进去。
     * 🛑 参数只收 `0` / `1`：固件虽然有长度门（LEN 必须 == 2），但 ARG 值它
     *   不做范围校验（交给内核容错）⇒ 本层收紧，避免打出手误的档位。 */
    if ((strcmp(argv[1], "mode") == 0) ||
        (strcmp(argv[1], "notch") == 0) ||
        (strcmp(argv[1], "ch2") == 0)) {

        if (argc < 3) {
            printf("用法: 485-send %s 0|1\n", argv[1]);
            return 1;
        }
        if ((strcmp(argv[2], "0") != 0) && (strcmp(argv[2], "1") != 0)) {
            printf("参数只接受 0 或 1（收到 '%s'）\n", argv[2]);
            return 1;
        }
        const uint8_t on = (uint8_t)((argv[2][0] == '1') ? 1u : 0u);

        if (strcmp(argv[1], "ch2") == 0) {
            /* 🛑 **唯一带状态门的参数命令：仅 STOP 态生效**（`vcomcmd.c:755`）。
             *   RUN 态 `START_CON=1` 锁着配置寄存器 ⇒ 固件**静默丢弃**、一字节不回，
             *   现场极易误判成"命令没到"或"下行不通"。所以先看帧流在不在涨，
             *   直接给出该敲的下一步，别让人去猜。 */
            ecg_pipeline_stream_stat_t s0, s1;
            ecg_pipeline_get_stream_stat(&s0);

            const esp_err_t e = ecg_cmd_set_channel(on);
            if (e != ESP_OK) {
                printf("SET_CHANNEL(0x06) -> %s\n", esp_err_to_name(e));
                return 1;
            }

            vTaskDelay(pdMS_TO_TICKS(600));
            ecg_pipeline_get_stream_stat(&s1);
            const uint32_t dn = s1.snapshots - s0.snapshots;
            printf("SET_CHANNEL(0x06) arg=%u -> OK   快照回执 %lu 帧\n",
                   (unsigned)on, (unsigned long)dn);

            if (dn == 0u) {
                printf("  ⇒ 没有回执。按顺序排查（第 ① 条最常见）：\n");
                printf("     ① STM32 还在 RUN ⇒ 本条被状态门丢弃。先 485-send stop 再发\n");
                printf("     ② 下行没到 STM32（线路/UT-2211 接收侧）\n");
                printf("     顺序：485-send stop → 485-send ch2 %u → 485-send start\n",
                       (unsigned)on);
                return 1;
            }
            printf("  ⇒ 通道 II %s（后续帧长 %s、STA.bit2 跟随；run 起来后看 485-stat）\n",
                   on ? "已使能" : "已关闭", on ? "103（双通道）" : "53（单通道）");

            /* ★T209（11-05）：画布**必须跟着切**，这是"通道II 只有一条基线"的根治点。
             *
             * 🛑 真因（代码可证，不是猜测）：
             *   ① STM32 侧 `ads1293.c:1533-1538` 明写 **CH2 ≡ CH1** —— CH2 不做滤波/
             *      重采样，直接把 CH1 的 25 个点复制过去 ⇒ 只要真在发 LEN=103，
             *      两路**必然逐位相同**，物理上不可能出现"一路有波形、另一路是平线"。
             *   ② 那平线是哪来的？`ecg_pipeline.c:429`：
             *        `v2 = (k < fr.n_ch2) ? fr.ch2[k] : 0;`
             *      收到 LEN=53 单通道帧时 `n_ch2 == 0` ⇒ CH2 **整段填 0** ⇒ 画出来
             *      **正好是一条贴零的水平基线**。
             *   ③ 屏上那个「通道II」键（`ui_button_bar.c:201`）只调
             *      `ui_waveform_set_dual_channel()` 换画布，**一个字节都不发给 STM32**
             *      ⇒ STM32 照旧发 LEN=53 ⇒ 命中 ②。
             * ⇒ 结论：那个键"看起来使能了通道II"，实际只换了画布。
             *   本条命令才是真正切 STM32 的入口，所以两边必须一起切。 */
            ui_waveform_set_dual_channel(on != 0u);
            ui_waveform_clear();
            ui_button_bar_refresh();
            printf("  ⇒ 画布已切到%s\n", on ? "双通道（CH2 与 CH1 逐位相同 = 切分正确的硬判据）"
                                            : "单通道");
            return 0;
        }

        /* ---- mode / notch ---- */
        const bool is_mode = (strcmp(argv[1], "mode") == 0);
        const esp_err_t e = is_mode ? ecg_cmd_set_mode(on) : ecg_cmd_set_notch(on);
        if (e != ESP_OK) {
            printf("%s(0x%02X) -> %s\n", is_mode ? "SET_MODE" : "SET_NOTCH",
                   is_mode ? 0x04 : 0x05, esp_err_to_name(e));
            return 1;
        }

        /* 无快照回执 —— 真值只在**下一帧 STA**里（帧周期 = 25 样本 × 2 ms = 50 ms，
         * 300 ms 足够跨 6 帧）。STOP 态没有帧流 ⇒ 这里必然读到 unknown，
         * 那不是失败，是"还没开始采集"。 */
        vTaskDelay(pdMS_TO_TICKS(300));
        const app_state_t *as = app_state_ro();
        char stas[64];
        ecg_sta_to_str(as->sta, as->has_sta, stas, sizeof(stas));

        printf("%s(0x%02X) arg=%u -> OK   现 STA=%s\n",
               is_mode ? "SET_MODE" : "SET_NOTCH", is_mode ? 0x04 : 0x05,
               (unsigned)on, stas);
        if (!as->has_sta) {
            printf("  ⇒ 读到 unknown：**STM32 没在出帧**（STOP 态）—— 本条已生效但无回执，\n");
            printf("     485-send start 之后再看 485-stat 的 STA 行即可确认\n");
        }
        return 0;
    }

    printf("未知参数 '%s'（只支持 start / stop / snap / mode / notch / ch2）\n", argv[1]);
    return 1;
}

static void register_commands(void)
{
    /* ---- S3：argtable 初始化（必须在注册/执行前完成）----
     * 位置参数按注册顺序分配：第 1 个给 <rec>/<bpm>，第 2 个（可选）给 [gain]。
     * `arg_int0` 的最后一参是默认值 NULL ⇒ 不传就看 count==0 走默认。 */
    s_mit_run_args.rec   = arg_str1(NULL, NULL, "<rec>", "MIT 记录号，如 105（读 mit_105_500.bin）");
    s_mit_run_args.gain  = arg_str0(NULL, NULL, "[gain]", "增益 2.5/5/10/20 mm/mV 或 0.5x/1x/2x/4x");
    s_mit_run_args.speed = arg_int0(NULL, "speed", "<n>", "回放倍速 1/2/4/8（默认 1）");
    s_mit_run_args.end   = arg_end(3);

    s_ladder_run_args.bpm   = arg_int1(NULL, NULL, "<bpm>", "目标心率，如 60（读 sr_060bpm.bin）");
    s_ladder_run_args.gain  = arg_str0(NULL, NULL, "[gain]", "增益 2.5/5/10/20 mm/mV 或 0.5x/1x/2x/4x");
    s_ladder_run_args.speed = arg_int0(NULL, "speed", "<n>", "回放倍速 1/2/4/8（默认 1）");
    s_ladder_run_args.end   = arg_end(3);

    /* ★T210：play —— 文件名原样贴进去，不拼规则 */
    s_play_args.file  = arg_str1(NULL, NULL, "<file>",
                                 "SD 上的文件名，如 alt-120bpm.bin（.bin 可省）");
    s_play_args.gain  = arg_str0(NULL, NULL, "[gain]", "增益 2.5/5/10/20 mm/mV 或 0.5x/1x/2x/4x");
    s_play_args.speed = arg_int0(NULL, "speed", "<n>", "回放倍速 1/2/4/8（默认 1）");
    s_play_args.end   = arg_end(3);

    s_result_dump_args.stem = arg_str1(NULL, NULL, "<stem>", "结果文件名主干，如 mit_105");
    s_result_dump_args.end  = arg_end(1);

    const esp_console_cmd_t cmds[] = {
        {
            .command = "status",
            .help    = "打印当前测试模式与 UI 按钮状态",
            .hint    = NULL,
            .func    = &cmd_status,
        },
        {
            .command = "test-mit",
            .help    = "进入 MIT-BIH 测试模式（禁用 UI 按钮）",
            .hint    = NULL,
            .func    = &cmd_test_mit,
        },
        {
            .command = "test-ladder",
            .help    = "进入心率阶梯测试模式（禁用 UI 按钮）",
            .hint    = NULL,
            .func    = &cmd_test_ladder,
        },
        {
            .command = "test-exit",
            .help    = "退出测试模式，恢复 UI 按钮",
            .hint    = NULL,
            .func    = &cmd_test_exit,
        },
        {
            .command  = "mit-run",
            .help     = "回放单条 MIT 记录（波形 + 峰序列，可对账）",
            .hint     = "<rec> [gain] [--speed 1|2|4|8]",
            .func     = &cmd_mit_run,
            .argtable = (void **)&s_mit_run_args,
        },
        {
            .command  = "ladder-run",
            .help     = "回放单档心率阶梯（波形 + 峰序列，可对账）",
            .hint     = "<bpm> [gain] [--speed 1|2|4|8]",
            .func     = &cmd_ladder_run,
            .argtable = (void **)&s_ladder_run_args,
        },
        {
            .command = "run-stop",
            .help    = "停止回放并关闭峰序列输出/落盘",
            .hint    = NULL,
            .func    = &cmd_run_stop,
        },
        {   /* ★T210：通用回放，文件名原样（不拼 mit_/sr_ 规则，不写结果文件） */
            .command = "play",
            .help    = "回放 SD 上任意 .bin（纯看波形：不写结果文件、不进测试模式）",
            .hint    = "<file> [gain] [--speed 1|2|4|8]",
            .func    = &cmd_play,
        },
        {
            .command  = "result-dump",
            .help     = "把 SD 卡上的结果文件回传串口（单条对账/批处理汇总）",
            .hint     = "<stem>  mit_105 | mit_all（批处理汇总）| xxx.txt",
            .func     = &cmd_result_dump,
            .argtable = (void **)&s_result_dump_args,
        },
        {
            .command = "mit-run-all",
            .help    = "登记跑 batch_mit.txt 全部 MIT 记录（写 SD 标记，重启后跑）",
            .hint    = NULL,
            .func    = &cmd_mit_run_all,
        },
        {
            .command = "ladder-run-all",
            .help    = "登记跑 batch_ladder.txt 心率阶梯（写 SD 标记，重启后跑）",
            .hint    = NULL,
            .func    = &cmd_ladder_run_all,
        },
        {
            .command = "mit-list",
            .help    = "列 SD 卡上的 MIT 记录（扫 /sdcard/ 与 /sdcard/s7_batch/）",
            .hint    = NULL,
            .func    = &cmd_mit_list,
        },
        {
            .command = "ladder-list",
            .help    = "列 SD 卡上的心率阶梯档位",
            .hint    = NULL,
            .func    = &cmd_ladder_list,
        },
        {
            .command = "mit-set",
            .help    = "指定要跑的 MIT 记录，写 /sdcard/batch_mit.txt",
            .hint    = "<105,108> | all",
            .func    = &cmd_mit_set,
        },
        {
            .command = "ladder-set",
            .help    = "指定要跑的阶梯档位，写 /sdcard/batch_ladder.txt",
            .hint    = "<060,090> | all",
            .func    = &cmd_ladder_set,
        },
        {
            .command = "scan",
            .help    = "递归列全卡 *.bin（含大小）",
            .hint    = NULL,
            .func    = &cmd_scan,
        },
        {
            .command = "log",
            .help    = "开/关串口日志级别 (off/error/warn/info/debug/verbose)",
            .hint    = "<off|error|warn|info|debug|verbose>",
            .func    = &cmd_log,
        },
        {
            .command = "restart",
            .help    = "软重启（登记批处理后用它触发重启）",
            .hint    = NULL,
            .func    = &cmd_restart,
        },
        {
            .command = "ui-clear",
            .help    = "执行一次清屏（与屏上「清屏」键同一段；T212 只清视觉）",
            .hint    = NULL,
            .func    = &cmd_ui_clear,
        },
        {
            .command = "rpeak",
            .help    = "R 峰打点开关 / QRS 序列记录（本地功能，非下行命令）",
            .hint    = "[on|off] | rec（300 s 固定） | rec off",
            .func    = &cmd_rpeak,
        },
        {
            .command = "mem",
            .help    = "打印内部 RAM / PSRAM 的总量、空闲、最大连续块",
            .hint    = NULL,
            .func    = &cmd_mem,
        },
        /* ---- S9a：485 实时源 ---- */
        {
            .command = "485-start",
            .help    = "启动 RS485 实时数据源（停掉当前 SD 回放）",
            .hint    = "[baud]",
            .func    = &cmd_485_start,
        },
        {
            .command = "485-stop",
            .help    = "停止 RS485 源，回到上电默认（SD 回放暂停态）",
            .hint    = NULL,
            .func    = &cmd_485_stop,
        },
        {
            .command = "485-stat",
            .help    = "打印 485 链路/解帧/环缓/心率统计",
            .hint    = NULL,
            .func    = &cmd_485_stat,
        },
        {
            .command = "485-perf",
            .help    = "窗口内实测帧率/丢帧/帧长/内存（★性能测试统一口径）",
            .hint    = "[秒数，默认 10]",
            .func    = &cmd_485_perf,
        },
        {
            .command = "485-send",
            .help    = "下发命令：start/stop/snap/mode/notch/ch2（★本工程唯一发送入口）",
            .hint    = "start|stop|snap|mode 0|1|notch 0|1|ch2 0|1",
            .func    = &cmd_485_send,
        },
    };

    for (size_t i = 0; i < sizeof(cmds) / sizeof(cmds[0]); i++) {
        ESP_ERROR_CHECK(esp_console_cmd_register(&cmds[i]));
    }
}

/* ============================================================================
 * 对外接口
 * ========================================================================== */

esp_err_t cli_console_start(void)
{
    esp_console_repl_t *repl = NULL;
    esp_console_repl_config_t repl_config = ESP_CONSOLE_REPL_CONFIG_DEFAULT();

    repl_config.prompt = PROMPT_STR ">";
    /* 关历史文件存储（本工程 SD 卡用 SPI 模式，不另起 wear-levelling 分区） */
    repl_config.history_save_path = NULL;
    /* 命令最长 256 字节，足够 argtable 参数 + 记录号列表 */
    repl_config.max_cmdline_length = 256;

    esp_console_register_help_command();
    register_commands();

    /* 内部自建 console_repl 任务，start 只发 notify，非阻塞 */
    ESP_ERROR_CHECK(esp_console_new_repl_stdio(&repl_config, &repl));
    ESP_ERROR_CHECK(esp_console_start_repl(repl));

    ESP_LOGI(TAG, "CLI 命令台已启动（提示符 %s>），test-mit/test-ladder/test-exit/status/scan",
             PROMPT_STR);

    return ESP_OK;
}
