/**
 * @file ecg_ringbuf.h
 * @brief 单产单消（SPSC）环形缓冲 —— S3
 *
 * 用途：Core 1 的数据管线写入、Core 0 的绘制读取，两者之间唯一的通道。
 *
 * 设计约束（实施计划 §4.2 / §6 S3）：
 *   - 容量 5000 样本/通道 = 10 s @ 500 Hz（最长窗口是 12.5 mm/s 下的 10 s）
 *   - 样本用 int32_t 而不是 int16_t：后续滤波/求导/积分的中间结果会超出 int16 范围
 *   - 40 KB，放内部 RAM（PSRAM 有 80 MHz 但有 cache miss，内部 RAM 确定性更好）
 *   - 生产者只写 wr、只读 rd；消费者只写 rd、只读 wr —— 严格单写者，零锁零 CAS
 *
 * 溢出策略：**丢弃新样本 + 计数 + 报警**，而不是覆盖最旧。
 *   为什么偏离「丢最旧」：
 *     ① 覆盖最旧需要生产者写 rd，破坏 SPSC 的单写者前提，引入竞态；
 *     ② 10 s 的缓冲正常绝不会满。一旦满，说明消费端卡了 10 s —— 那是 **bug**，
 *        应该暴露而不是静默掩盖（与 S2 报告 §6.3「静默容错」原则一致）；
 *     ③ 从显示语义看，丢旧会造成波形时间轴跳变，丢新只是延迟。
 *   若将来确认需要覆盖式，再单独做一版并接受那点竞态。
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* 每通道样本容量：10 s @ 500 Hz */
#define ECG_RING_CAPACITY   5000

typedef struct {
    int32_t ch1[ECG_RING_CAPACITY];
    int32_t ch2[ECG_RING_CAPACITY];

    volatile uint32_t wr;           /* 写指针，生产者独占推进 */
    volatile uint32_t rd;           /* 读指针，消费者独占推进 */
    volatile uint32_t pushed;       /* 累计推入样本数 */
    volatile uint32_t dropped;      /* 因满而丢弃的新样本数 */
    volatile uint32_t overwritten;  /* 保留字段（当前策略不覆盖旧数据，恒 0） */

    uint8_t channels;               /* 1 = 单通道；2 = 双通道 */
} ecg_ringbuf_t;

/** @brief 初始化（清零指针与统计） */
void ecg_ring_init(ecg_ringbuf_t *rb, uint8_t channels);

/**
 * @brief 推入一个样本（仅生产者调用）。
 * @return true = 已写入；false = 缓冲满，样本被丢弃（已计入 dropped）
 */
bool ecg_ring_push(ecg_ringbuf_t *rb, int32_t ch1, int32_t ch2);

/**
 * @brief 批量取出（仅消费者调用）。
 * @param max 本次最多取多少个
 * @return 实际取出个数
 */
size_t ecg_ring_pop(ecg_ringbuf_t *rb, int32_t *ch1_out, int32_t *ch2_out, size_t max);

/** @brief 当前可读样本数 */
size_t ecg_ring_count(const ecg_ringbuf_t *rb);

/** @brief 当前剩余空间（保留 1 个空位用于区分空/满） */
size_t ecg_ring_free(const ecg_ringbuf_t *rb);

/** @brief 取统计快照 */
void ecg_ring_stats(const ecg_ringbuf_t *rb,
                    uint32_t *pushed, uint32_t *dropped, uint32_t *overwritten);

/** @brief 清空（生产者必须已停止） */
void ecg_ring_reset(ecg_ringbuf_t *rb);
