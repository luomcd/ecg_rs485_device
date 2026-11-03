/**
 * @file ecg_stream.c
 * @brief T101 v2.2 帧字节流解析实现（S9a）
 */

#include "ecg_stream.h"

#include <string.h>

/* 状态机三态：找 AA → 等 55 → 收帧体（TYPE+LEN+PAYLOAD+CRC）。
 * 🛑 不要按"固定 58/59 字节切帧"的思路改：LEN=52/53/103 三种帧长共存，
 *   帧长只能由 LEN 字段决定（参考 ecg_frame.h 顶部的教训）。 */

void ecg_stream_init(ecg_stream_t *st)
{
    if (st == NULL) {
        return;
    }
    memset(st, 0, sizeof(*st));
    st->state = 0;
}

void ecg_stream_reset_stat(ecg_stream_t *st)
{
    if (st == NULL) {
        return;
    }
    const uint8_t  state = st->state;
    const uint16_t got   = st->got;
    const uint16_t need  = st->need;
    uint8_t  raw[sizeof(st->raw)];

    memcpy(raw, st->raw, sizeof raw);

    memset(st, 0, sizeof(*st));

    /* 状态机继续跑，只清统计 —— 否则复位瞬间会把半帧丢掉 */
    st->state = state;
    st->got   = got;
    st->need  = need;
    memcpy(st->raw, raw, sizeof raw);
}

bool ecg_stream_feed(ecg_stream_t *st, uint8_t byte, ecg_frame_t *out)
{
    if (st == NULL || out == NULL) {
        return false;
    }

    switch (st->state) {
    case 0: /* 找第一个同步字节 AA */
        if (byte == ECG_SYNC0) {
            st->raw[0] = byte;
            st->got    = 1;
            st->state  = 1;
        } else {
            st->resync_bytes++;
        }
        return false;

    case 1: /* 等 55 */
        if (byte == ECG_SYNC1) {
            st->raw[1] = byte;
            st->got    = 2;
            st->state  = 2;
            st->need   = 0;   /* 帧长未定，收到 5 字节头后再算 */
        } else if (byte == ECG_SYNC0) {
            /* AA AA ...：以新的 AA 重新起头，前一个 AA 计入重同步 */
            st->raw[0] = byte;
            st->got    = 1;
            st->resync_bytes++;
        } else {
            /* AA + 非 55：两个字节都不是同步字 */
            st->resync_bytes += 2;
            st->state = 0;
            st->got   = 0;
        }
        return false;

    default: /* 收帧体 */
        if (st->got >= sizeof(st->raw)) {
            /* 防御：理论上不该到这里（need 已限幅），真到了就重来 */
            st->resync_bytes += st->got;
            st->state = 0;
            st->got   = 0;
            return false;
        }
        st->raw[st->got++] = byte;

        if (st->need == 0 && st->got == ECG_HDR_LEN) {
            const uint16_t len = (uint16_t)(st->raw[3] | ((uint16_t)st->raw[4] << 8));
            if (len < 2 || len > ECG_LEN_SANITY_MAX) {
                /* 同步字对但长度离谱 ⇒ 假帧头，整段丢弃重新找头。
                 * （文件版是回退 4 字节净前进 1；流式版无法回退，直接丢 5 字节，
                 *   代价是极小概率漏掉嵌在假帧头里的真同步字，垃圾流场景可接受） */
                st->bad_len++;
                st->resync_bytes += st->got;
                st->state = 0;
                st->got   = 0;
                return false;
            }
            st->need = (uint16_t)(len + ECG_FRAME_OVERHEAD);
        }

        if (st->need != 0 && st->got >= st->need) {
            /* 一整帧到手：布局/CRC 复用文件版的同一份实现 */
            ecg_frame_parse_raw(out, st->raw);

            st->frames++;
            if (!out->crc_ok) {
                st->crc_errors++;
            }
            if (st->has_seq && out->seq != (uint16_t)(st->last_seq + 1u)) {
                st->seq_gaps++;
            }
            st->last_seq = out->seq;
            st->has_seq  = true;

            st->state = 0;
            st->got   = 0;
            st->need  = 0;
            return true;
        }
        return false;
    }
}
