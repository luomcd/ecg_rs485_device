/**
 * @file ecg_pipeline.h
 * @brief 数据管线：Core 1 按 500 Hz 节拍把帧样本推入环形缓冲（S3）
 *
 * 分工（实施计划 §4.2）：
 *   Core 1 = 生产者：读 SD → 帧解析 → 逐样本按 2 ms 节拍推入环缓
 *   Core 0 = 消费者：S3 阶段是临时 drain 任务（只取不画），S4 起由 ui_waveform 取代
 *
 * 为什么逐样本推、而不是整帧一次性推：
 *   一帧 25 样本 = 50 ms 的数据量。整帧推会让环缓呈"每 50 ms 跳一下"的锯齿，
 *   绘制端看到的时间轴不均匀。逐样本按 2 ms 节拍推，环缓增长是线性的，
 *   与"实时采集"的到达模式一致 —— 这样 S4 的绘制逻辑不必为两种模式分别处理。
 *
 * 优先级：LVGL 任务 = 6（esp_lv_adapter.h:103），所以管线取 **5**、消费者取 3。
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "ecg_ringbuf.h"

typedef struct {
    uint32_t pushed;        /* 累计推入环缓的样本数 */
    uint32_t popped;        /* 累计被消费者取走的样本数 */
    uint32_t dropped;       /* 因环缓满而丢弃的样本数 */
    uint32_t loops;         /* 文件重放次数（loop 模式） */
    uint32_t elapsed_ms;    /* 已运行时長 */
    size_t   ring_level;    /* 环缓当前水位（样本数） */
    size_t   heap_int_free; /* 内部 RAM 剩余字节 */
    uint32_t frames_read;   /* 已解析帧数 */
} ecg_pipeline_stats_t;

/**
 * @brief 启动数据管线。
 * @param path  数据文件路径（如 "/sdcard/ecg.bin"）
 * @param loop  true = 读到 EOF 后从头重放（长跑测试用）；false = 读完即停
 */
esp_err_t ecg_pipeline_start(const char *path, bool loop);

/** @brief 停止并回收任务（会等待任务退出） */
void ecg_pipeline_stop(void);

/** @brief 是否在运行 */
bool ecg_pipeline_running(void);

/**
 * @brief 卸下 S3 的临时 drain 消费者，把环缓的消费权交给 UI（S4）。
 *
 * 为什么需要：S3 的 drain 任务只是为了验证 SPSC 正确性（只取不画）。
 * S4 起由 ui_waveform 的绘制回调消费环缓 —— 两者**不能并存**，否则会互相抢样本，
 * 表现为波形缺帧和"pushed 有而画面上没画出来"。
 *
 * 调用后 drain 任务退出，生产者继续推样本。
 */
void ecg_pipeline_detach_consumer(void);

/* ---- S6 回放控制（对应界面上的暂停 / 继续 / 重播）---- */

/** @brief 暂停投喂（生产者保持任务存活，节拍基准在恢复时重置） */
void ecg_pipeline_pause(void);

/** @brief 恢复投喂 */
void ecg_pipeline_resume(void);

/** @brief 是否处于暂停 */
bool ecg_pipeline_is_paused(void);

/** @brief 是否已读到文件末尾（loop=false 时置位，restart 后清除） */
bool ecg_pipeline_eof(void);

/**
 * @brief 从头重放。
 * @note  生产者任务在 EOF 后**不退出**，停在循环里等本请求，
 *        所以重播不需要销毁重建任务（避免栈反复分配）。
 */
void ecg_pipeline_restart(void);

/**
 * @brief 换数据文件（切「通道 I / II」时用）。
 *
 * 生产者会打开新文件并从头播放；**打不开就保持原文件不变**，不会把管线搞死。
 * 用于单通道（LEN=53）与双通道（LEN=103）数据的切换。
 */
void ecg_pipeline_set_file(const char *path);

/** @brief 取统计快照 */
void ecg_pipeline_get_stats(ecg_pipeline_stats_t *out);

/** @brief 取环形缓冲句柄（S4 绘制端要读它） */
ecg_ringbuf_t *ecg_pipeline_ringbuf(void);

/* ============================================================================
 * S7：心率 / R 峰检测 —— 跨核发布（需求 §6.2）
 *
 * R 峰检测跑在**生产者任务内**（Core 1，与读 SD / 帧解析同核），状态量任务私有、
 * 无锁。UI 侧（Core 0）只读结果，两种通道：
 *   - BPM      → 单变量 + 版本号（seqlock，防读到撕裂值）
 *   - R 峰索引 → 定长 SPSC 环（深度 APP_RPEAK_QUEUE_DEPTH = 64）
 *
 * ⚠ **索引口径**：`epoch` 是检测器的「代数」，换文件 / 重播 / 清屏 / 环缓丢样本
 *   都会 +1 并把 `sample_index` 归零。UI 必须跟着把「已取样本计数」归零，
 *   否则峰索引与显示缓冲的时间轴会错位（表现为标记画在错误的 x 上）。
 *   检测器索引 == 生产者推入序号；UI 取走的样本序号与之等价的前提是**不丢样本**
 *   （正常运行时 dropped 恒 0，S3/S4 实测），丢样本时本模块会主动重置对齐。
 * ========================================================================== */

typedef struct {
    float    bpm;           /* 0 = 尚不可用（UI 显示 `--`，不得显示 0 —— 0 是有效读数） */
    uint32_t peak_total;    /* 本 epoch 累计检出峰数 */
    int64_t  last_peak_us;  /* 最近一次检出的 esp_timer 时刻；0 = 还没有峰 */
    uint32_t epoch;         /* 检测器代数，见上 */
    uint32_t fed;           /* 本 epoch 已喂入检测器的样本数（稳定窗用，见 app_config.h） */
} ecg_pipeline_hr_t;

/**
 * @brief 读心率快照（Core 0 调用）。
 * @return false = 本次读到写撕裂，调用方应沿用上一次的值（下一轮自然会成功）
 */
bool ecg_pipeline_get_hr(ecg_pipeline_hr_t *out);

/**
 * @brief 取走待绘制的 R 峰（Core 0 调用）。
 * @param abs_idx 输出缓冲，**每个元素是该峰的「全局绝对样本号」**——与生产者
 *                `s_push_seq` 同一空间，**永不重置**，跨换文件/重播/清屏一直累加。
 *                UI 用自己同样永不重置的「已取样本计数」去减即可得到年龄。
 *                ⚠ 不要换成 epoch 内相对索引（归零时刻不同步）或"相对年龄"（有随机误差），
 *                两种都被实测否决，原因见 `rp_q_push()` 的注释。
 * @param max  本最多取多少个
 * @return 实际取出个数
 */
uint32_t ecg_pipeline_pop_rpeaks(uint32_t *abs_idx, uint32_t max);

/**
 * @brief 请求重置检测器（清屏时调用；实际重置发生在生产者下一次循环，≤2 ms）。
 * @note  换文件 / 重播已经隐含重置，不必额外调用。
 */
void ecg_pipeline_rpeak_reset(void);

/* ============================================================================
 * S3：回放对账输出 + 倍速
 *
 * 目的：`mit-run <rec>` 回放时，边跑边把峰序列打到串口（格式同批处理的 `P <idx>`），
 *      跑完打一行 `FILE <name> frames=.. samples=.. peaks=.. bpm=..` 收尾，
 *      于是可以直接用 `tools/ecg_batch_eval.py` 对账 —— 结果必须与 S2 批处理跑同一条
 *      **逐位一致**（这是峰号口径正确性的唯一判据）。
 *
 * 🛑 **峰号口径**：输出的必须是**文件内相对样本号**（0 起，与 `_ann500.txt` 同一坐标系）。
 *   回放内部用的是「全局绝对号」（`s_push_seq`，永不归零）。转换基准是 `s_file_base_seq`
 *   = 当前文件第一个样本的全局号，换文件 / 重播时刷新。
 *   ⚠ **不能用 `ecg_rpeak_sample_index()`（epoch 内索引）**：UI 清屏会 reset 检测器把它
 *   归零，但文件还在继续播 ⇒ 之后再拿它当文件内号就整体偏移。改用 push 序号基准无此问题。
 * ========================================================================== */

/**
 * @brief 开/关回放峰序列输出（`P <相对号>` + 收尾 `FILE` 行）。
 * @param on true = 开（`mit-run`/`ladder-run` 调用）；false = 关（收尾时自动关）
 */
void ecg_pipeline_set_rpeak_dump(bool on);

/**
 * @brief 设置回放倍速。
 * @param speed 1 / 2 / 4 / 8（其他值按 1 处理）
 *
 * 实现：不是改 `period`（tick=1000 Hz ⇒ 最小 1 ms，4x 需要 0.5 ms 做不到），
 * 而是用「微秒累计器」：每喂一个样本累加 `2000/speed` µs，累计够 1 ms 才
 * `vTaskDelayUntil` 一次。1x 时累加 2000 µs ⇒ 每次 delay 2 tick，与改动前**完全等价**。
 *
 * ⚠ 倍速**不影响算法结果**（样本仍逐个按原顺序喂检测器，与批处理同一序列），
 *   但 4x/8x 会让环缓填充变快、UI 消费跟不上 ⇒ 可能丢样本。丢样本时回放少喂检测器，
 *   峰序列就不再与批处理逐位一致。所以 `FILE` 行带 `dropped=`，**对账要求 dropped=0**。
 */
void ecg_pipeline_set_speed(int speed);

/**
 * @brief 把回放峰序列同时**落盘**一份（`mit-run` 单条分析用）。
 * @param path 目标路径（如 `/sdcard/mit_105_result.txt`）；NULL 或 "" = 不落盘
 *
 * 内容与串口输出**完全一致**：`P <相对号> ...` 行 + `FILE ..` 收尾行 + `# dropped=..`，
 * 所以 `tools/ecg_batch_eval.py` / `tools/s3_verify_replay.py` 可直接吃这个文件。
 *
 * 🛑 **实现约束**：不能边跑边写 SD 卡。生产者任务跑 2 ms 硬节拍，SD 卡一次写几十 ms
 *   会把节拍打崩（环缓空、波形卡顿）。所以先攒进 32 KB 静态缓冲，**EOF 时一次性落盘**。
 *   代价：中途 `run-stop` 不落盘（缓冲里是半截数据，而且没有 FILE 行没法对账）。
 *
 * @note 换文件 / 重播会清空缓冲（上一段结果作废）。
 */
void ecg_pipeline_set_dump_file(const char *path);

/**
 * @brief ★T216：把攒着的峰序列**立即落盘**（实时记录用）。
 *
 * 为什么需要：上面那条约束——"先攒进 32 KB 缓冲、EOF 时一次性落盘"——只对
 * **FILE 源**成立。485 实时源**永远不会 EOF** ⇒ 不主动 flush 就永远不落盘。
 *
 * 语义：
 *   - 先把攒了一半的行（不足 16 峰）成行，再一次性 `fopen/write/close`；
 *   - 落完把缓冲清空（`s_dump_fill = 0`），避免重复写；
 *   - **缓冲为空时直接返回，不做任何写操作** —— 否则 `fopen(path,"w")` 会用空内容
 *     覆盖掉之前已经落好的文件（FILE 源 EOF 已落盘、之后又调一次这种情况）。
 *
 * @note 会阻塞数十 ms（SD 写）。实时源下由记录任务调用，不在生产者节拍里。
 */
void ecg_pipeline_flush_dump(void);

/* ============================================================================
 * S9a：RS485 实时数据源（真机路线：ADS1293+STM32 → 485 → ESP32）
 *
 * 与 FILE 源的差别只有"数据从哪来"：
 *   - FILE 源：fread + ecg_frame_read，按 2 ms 节拍逐样本推；
 *   - 485 源：uart_read_bytes + ecg_stream_feed（字节流状态机），**收到整帧即推**
 *     （PC/STM32 按实时速率发，数据自带时序，不再自打节拍）。
 * push 之后的代码**完全共用**（环缓 / R 峰绝对样本号 / BPM 发布 / UI 取样绘制），
 * 这是"485 显示与 SD 回放等价"的结构保证。
 *
 * ⚠ 两源互斥：start_rs485 会先停掉正在跑的 FILE 源（含暂停态——暂停时 s_running
 *   仍为 true，直接再 start 会撞 "already running"）。
 * ⚠ 485 源**永不置 EOF**（`ecg_pipeline_eof()` 恒 false），否则 UI 会把实时流
 *   误判成"播完"。
 * ========================================================================== */

typedef struct {
    bool     active;        /* 当前是否为 485 源且在跑 */
    uint32_t frames;        /* 解析成功的帧数 */
    uint32_t crc_errors;    /* CRC 错帧数 */
    uint32_t resync_bytes;  /* 为找同步字丢弃的字节数（含上电 ROM 垃圾） */
    uint32_t bad_len;       /* 假帧头（LEN 离谱）丢弃次数 */
    uint32_t seq_gaps;      /* SEQ 非 +1 次数（丢帧指示） */
    uint32_t snapshots;     /* ★T208：收到的快照帧（TYPE=0x03）数；>0 即证明下行通 */
} ecg_pipeline_stream_stat_t;

/**
 * @brief 启动 485 数据源（先停 FILE 源，再装 UART1、按 APP_485_* 引脚收发）。
 * @param baud 波特率（0 = APP_485_BAUD_DEFAULT = 115200）
 */
esp_err_t ecg_pipeline_start_rs485(uint32_t baud);

/** @brief 取 485 流统计（CLI `485-stat` 用；与生产者并发读，轻量竞争可接受） */
void ecg_pipeline_get_stream_stat(ecg_pipeline_stream_stat_t *out);

/** @brief 当前数据源是否为 485 */
bool ecg_pipeline_src_is_rs485(void);
