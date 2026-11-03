/**
 * @file ecg_ringbuf.c
 * @brief SPSC 环形缓冲实现（S3）
 *
 * 内存序说明：
 *   生产者：写完 ch1/ch2[w] 之后，用 __ATOMIC_RELEASE 发布 wr。
 *           消费者用 __ATOMIC_ACQUIRE 读 wr，保证能看到样本写入。
 *   指针推进本身是 32 位对齐读改写，Xtensa 上单条指令即可，不需要临界区。
 *   真正需要的只是编译器与 CPU 不把「数据写」重排到「指针发布」之后。
 */

#include "ecg_ringbuf.h"

#include <string.h>

#include "esp_attr.h"

void ecg_ring_init(ecg_ringbuf_t *rb, uint8_t channels)
{
    if (rb == NULL) {
        return;
    }
    memset(rb, 0, sizeof(*rb));
    rb->channels = (channels >= 2) ? 2u : 1u;
}

bool ecg_ring_push(ecg_ringbuf_t *rb, int32_t ch1, int32_t ch2)
{
    const uint32_t w = __atomic_load_n(&rb->wr, __ATOMIC_RELAXED);
    const uint32_t r = __atomic_load_n(&rb->rd, __ATOMIC_ACQUIRE);

    /* 留一个空位区分「空」与「满」 */
    const uint32_t next = (w + 1u == ECG_RING_CAPACITY) ? 0u : (w + 1u);

    if (next == r) {
        /* 满：丢弃新样本并计数。
         * 10 s 的缓冲正常不会满；满了说明消费端卡了 10 s，属于系统级异常。 */
        __atomic_add_fetch(&rb->dropped, 1u, __ATOMIC_RELAXED);
        return false;
    }

    rb->ch1[w] = ch1;
    rb->ch2[w] = ch2;

    /* 样本写完，再发布写指针（RELEASE 保证上面的写对消费者可见） */
    __atomic_store_n(&rb->wr, next, __ATOMIC_RELEASE);
    __atomic_add_fetch(&rb->pushed, 1u, __ATOMIC_RELAXED);
    return true;
}

size_t ecg_ring_pop(ecg_ringbuf_t *rb, int32_t *ch1_out, int32_t *ch2_out, size_t max)
{
    if (rb == NULL || ch1_out == NULL) {
        return 0;
    }

    uint32_t r = __atomic_load_n(&rb->rd, __ATOMIC_RELAXED);
    const uint32_t w = __atomic_load_n(&rb->wr, __ATOMIC_ACQUIRE);

    size_t n = 0;
    while (r != w && n < max) {
        ch1_out[n] = rb->ch1[r];
        if (ch2_out != NULL) {
            ch2_out[n] = rb->ch2[r];
        }
        r = (r + 1u == ECG_RING_CAPACITY) ? 0u : (r + 1u);
        n++;
    }

    if (n > 0) {
        /* 数据读完，再发布读指针 */
        __atomic_store_n(&rb->rd, r, __ATOMIC_RELEASE);
    }
    return n;
}

size_t ecg_ring_count(const ecg_ringbuf_t *rb)
{
    if (rb == NULL) {
        return 0;
    }
    const uint32_t w = __atomic_load_n(&rb->wr, __ATOMIC_ACQUIRE);
    const uint32_t r = __atomic_load_n(&rb->rd, __ATOMIC_ACQUIRE);
    return (w >= r) ? (size_t)(w - r) : (size_t)(ECG_RING_CAPACITY - r + w);
}

size_t ecg_ring_free(const ecg_ringbuf_t *rb)
{
    /* 容量减 1 可用（保留一个空位） */
    const size_t used = ecg_ring_count(rb);
    return (used >= ECG_RING_CAPACITY) ? 0u : (ECG_RING_CAPACITY - 1u - used);
}

void ecg_ring_stats(const ecg_ringbuf_t *rb,
                    uint32_t *pushed, uint32_t *dropped, uint32_t *overwritten)
{
    if (rb == NULL) {
        return;
    }
    if (pushed != NULL) {
        *pushed = __atomic_load_n(&rb->pushed, __ATOMIC_RELAXED);
    }
    if (dropped != NULL) {
        *dropped = __atomic_load_n(&rb->dropped, __ATOMIC_RELAXED);
    }
    if (overwritten != NULL) {
        *overwritten = __atomic_load_n(&rb->overwritten, __ATOMIC_RELAXED);
    }
}

void ecg_ring_reset(ecg_ringbuf_t *rb)
{
    if (rb == NULL) {
        return;
    }
    /* 只在生产者已停止时调用 */
    __atomic_store_n(&rb->wr, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&rb->rd, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&rb->pushed, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&rb->dropped, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&rb->overwritten, 0u, __ATOMIC_RELEASE);
}
