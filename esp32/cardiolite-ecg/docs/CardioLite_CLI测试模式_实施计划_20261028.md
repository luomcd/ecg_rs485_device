# CardioLite CLI 测试模式 —— 分步实施计划

**日期**：2026-10-28
**上游需求**：`CardioLite_CLI测试模式_需求文档_20261028.md`（已全部定案）
**原则**：一次改少、逐条验收，每步编译 + 冒烟 + 对账验证后再进下一步。

---

## 0. 关键前提（已核实）

| 项 | 结论 |
|---|---|
| 固件有 CLI 吗 | ❌ 无，需从零加 `esp_console` |
| 登记标记用什么 | ✅ **SD 卡标记文件** `/sdcard/batch_pending.flag`（猛哥提议，比 NVS 更简单，复用现有文件读写，零新增基础设施） |
| `ecg_rpeak` 可重入吗 | ✅ 纯实例化（全收 `ecg_rpeak_t*` 指针，无全局可变状态），回放/批处理不互扰 |
| 回放路径已有 R 峰检测 | ✅ `producer_task` 已在跑 `ecg_rpeak_process`，只缺 printf 输出 |
| 全速批处理位置 | 现有 `main.c` LVGL 之前（`S7_BATCH` 段），**SD 已挂载**，串口干净 |
| console 输出通道 | USB Serial JTAG（COM35），`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y` |

---

## 1. 新增文件与改动文件总览

| 文件 | 动作 | 内容 |
|---|---|---|
| `main/cli_console.c` / `.h` | **新增** | CLI 框架 + 命令注册 + REPL 启动 |
| `main/test_mode.c` / `.h` | **新增** | 模式状态机 `g_test_mode` + SD 卡标记文件读写 |
| `main/ecg/ecg_pipeline.c` | 改 | 回放路径补峰序列 printf + 文件内相对号 + 倍速 |
| `main/main.c` | 改 | LVGL 之前读标记文件跑批处理；起 REPL；接 UI 禁用 |
| `main/ui/*` | 改 | 事件回调入口加 `if (g_test_mode != NORMAL) return;` |
| `main/CMakeLists.txt` | 改 | 加 `esp_console` `linenoise` `argtable3` 依赖（**不加 nvs_flash**，标记走 SD 文件） |

---

## 2. 分步实施

### S1：CLI 框架 + 模式状态机 + 基础命令 + UI 禁用

**目标**：上电进 UI + CLI，能敲命令、切模式、按钮随模式禁用/恢复。

**改动**：
1. `main/test_mode.h` 定义：
   ```c
   typedef enum { TEST_NORMAL = 0, TEST_MIT, TEST_LADDER } test_mode_t;
   test_mode_t test_mode_get(void);
   void        test_mode_set(test_mode_t m);
   ```
2. `main/cli_console.c`：
   - `esp_console_repl_config_t` + `esp_console_new_repl_stdio()` + `esp_console_start_repl()`（照官方 basic 示例）。
   - 注册命令：`test-mit` / `test-ladder` / `test-exit` / `status` / `help` / `scan`（`scan` 本期先列 SD 根目录 `.bin`）。
   - `esp_console_register_help_command()`。
3. `main/main.c`：
   - `app_main` 末尾（LVGL 初始化完成后）调 `cli_console_start()`。
   - 注意：REPL 用**独立任务**，不阻塞 app_main。
4. UI 事件回调入口加禁用判断。

**验证**：
- 编译通过（`tools/idf.sh`）。
- 上电：UI 正常显示，串口出现 `esp32>` 提示符。
- `status` → `mode: NORMAL, ui-buttons: enabled`。
- `test-mit` → 提示进入，UI 按钮失效（触摸无响应）。
- `test-exit` → 按钮恢复。

**风险**：低。唯一注意 REPL 任务优先级别设太高（默认即可），否则抢 LVGL。

---

### S2：全速批处理命令（解法 A：SD 标记文件 + 重启跑）

**目标**：`mit-run-all` / `ladder-run-all` 写标记文件（记录要跑的清单），重启后 LVGL 之前自动跑批处理。

**改动**：
1. `main/test_mode.c` 增加标记文件读写（复用现有 `fopen`/`remove`，零新基础设施）：
   ```c
   #define TEST_BATCH_FLAG "/sdcard/batch_pending.flag"
   void test_mode_set_batch(const char *manifest); // 写标记文件（内容=清单路径，如 "batch_mit.txt"）
   bool test_mode_batch_pending(void);            // 标记文件是否存在
   const char *test_mode_get_batch(void);         // 读标记里的清单路径（无则 NULL）
   void test_mode_clear_batch(void);              // remove 标记文件
   ```
2. `main/cli_console.c` 注册 `mit-run-all` / `ladder-run-all`：
   - `mit-run-all` 回调 → `test_mode_set_batch("batch_mit.txt")`。
   - `ladder-run-all` 回调 → `test_mode_set_batch("batch_ladder.txt")`。
   - 都 `printf("已登记，请重启\n")`。
3. `main/main.c` LVGL 之前（现有 `S7_BATCH` 段，SD 已挂载）：
   - `const char *m = test_mode_get_batch(); if (m)` → `ecg_batch_run_manifest(m)` → 跑完 `test_mode_clear_batch()`。
   - 清单路径拼成 `/sdcard/<m>`。
   - 保留现有「清单不存在则跳过」逻辑。
4. 清单分两份（`make_sdcard_batch.py` 或 CLI `*-set` 命令产出）：
   - `batch_mit.txt`：只含 `mit_*_500.bin`。
   - `batch_ladder.txt`：只含 `sr_*bpm.bin`。

**验证**：
- 敲 `mit-run-all` → 提示「已登记，请重启」，SD 卡上 `batch_pending.flag` 内容为 `batch_mit.txt`。
- 重启 → LVGL 之前黑屏跑 `batch_mit.txt`（只 MIT），串口出 `BATCH-BEGIN`/`FILE`/`P`/`BATCH-END`。
- 跑完自动进 UI，标记文件已删除（不会重复跑）。
- `ecg_batch_eval.py` 对账出 Se/+P。

**风险**：低。标记文件读写是固件已大量使用的套路，无新坑。唯一注意：标记文件检查必须在 SD 挂载成功之后（现有 `S7_BATCH` 段已在 `storage_sd_mount()` 内，天然满足）。

---

### S3：回放命令（峰序列 printf + 文件内相对号 + 倍速）

**目标**：`mit-run <rec>` / `ladder-run <bpm>` 回放 SD 文件，显示波形 + 输出峰序列可对账。

**改动**：
1. `main/ecg/ecg_pipeline.c` 的 `producer_task`：
   - 增加「文件内相对样本号」计数器 `s_file_idx`（每文件 `ecg_pipeline_start` 时归零，每喂一个样本 ++）。
   - 检出峰时，除 `rp_q_push` 外，`printf` 峰的文件内相对号（小缓冲，满 8 个 flush）。
   - 复用现有 `s_rp` 的 `ecg_rpeak_sample_index` 逻辑，转成文件内相对号。
2. 倍速：`period` 按命令参数缩放（`--speed 2/4/8`）。
3. `main/cli_console.c` 注册 `mit-run <rec>` / `ladder-run <bpm>`（argtable 解析参数 + 可选 `--speed`）。

**验证（关键，逐位比对）**：
- `mit-run 105` 回放，屏幕显示波形，串口边跑边出 `P <idx>`。
- 跑完，`P` 序列交给 `ecg_batch_eval.py` 对账，结果 **必须 = S2 批处理跑同一条的结果（逐位一致）**。
- 这一步是峰号口径（文件内相对号）的正确性验证，不一致就说明相对号转换有 bug。

**风险**：🟡 中。峰号口径转换是唯一风险点，靠「逐位比对」兜底。

---

### S4：SD 扫描 + 写清单命令

**目标**：`mit-list` / `ladder-list` / `mit-set` / `ladder-set` 完整可用。

**改动**：
1. `main/cli_console.c`：
   - `mit-list`：扫描 SD 卡 `s7_batch/` 下 `mit_*_500.bin`，列记录号。
   - `ladder-list`：扫 `sr_*bpm.bin`，列 bpm。
   - `mit-set <rec1,rec2>`：把指定记录写进 `/sdcard/batch_mit.txt`。
   - `ladder-set <bpm1,bpm2>`：把指定 bpm 写进 `/sdcard/batch_ladder.txt`。
   - `scan`：升级为扫全卡 `.bin`（复用已有 `ecg_scan_file` 或新写目录遍历）。

**验证**：
- `mit-list` 列出 105/108 等。
- `mit-set 105,108` 后，`cat /sdcard/batch_mit.txt`（或 `mit-run-all` 登记重启跑）验证清单已更新。
- `ladder-set 060,090` 后，`ladder-run-all` 只跑这两档。

**风险**：低。目录遍历用 `opendir/readdir`（POSIX，IDF 支持）。

---

## 3. 依赖顺序与建议节奏

```
S1（骨架）→ S2（批处理，解法A）→ S3（回放，峰号口径）→ S4（扫描/清单）
```

- S1 是地基，必须先跑通「能敲命令」。
- S2 和 S3 相互独立（批处理走 `ecg_batch`，回放走 `ecg_pipeline`），可交换顺序。
- S3 的「逐位比对」依赖 S2 的批处理结果作基准，所以建议 S2 在 S3 前。
- S4 是增强，最后做。

---

## 4. 验收总标准

1. 上电：UI 正常 + CLI 提示符可用。
2. `test-mit`/`test-ladder` 切模式，UI 按钮禁用/恢复正确。
3. `mit-run 105` 回放显示波形 + 输出峰序列，对账 Se/+P 与批处理逐位一致。
4. `mit-run-all` 登记 + 重启跑，`ecg_batch_eval.py` 出 Se/+P，与现在批处理结果一致。
5. 实时采集命令（不带参数）返回「通道未实现」提示，不崩。

---

## 5. 纪律（沿用 firmware-change-verify）

- 每步改完：编译 → 冒烟 → 对账验证，通过才进下一步。
- 改前归档 baseline（`tools/_mem_archive.py --backup`）。
- 每步完成后更新 `NOW.md` 和当日日报。
