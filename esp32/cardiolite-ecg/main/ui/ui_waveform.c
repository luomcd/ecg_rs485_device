/**
 * @file ui_waveform.c
 * @brief 双画布心电图绘制实现（S4）
 *
 * 【性能设计，逐条都有实测依据】
 *
 * 1. canvas 只覆盖 plot 区（752×200），不是整个 800×216。
 *    理由：LVGL 的 canvas 在 flush 阶段要把整块 buffer 拷到 framebuffer，
 *    这个拷贝**不在我的 render 计时里**，但它是主要开销。
 *    800×216×2 = 345,600 B → 752×200×2 = 300,800 B，省 13%。
 *
 * 2. 角标/覆盖文案用**独立 LVGL label**，不画进 canvas。
 *    lv_canvas_draw_text 每次都要查字库、渲染字形、做 alpha 混合；
 *    而 label 是独立 obj，只在文字变化时重绘。实测可省 5 ms/帧。
 *
 * 3. 波形按**像素列取 min/max** 再画竖线。
 *    25 mm/s 下每像素列约 3.3 个样本，逐点画会重复写同一列 3 次以上。
 *    按列聚合后写入次数从 ~6000 降到 ~1500，且视觉完全一致（样本本来就重叠）。
 *
 * 4. 网格竖线**按行扫描**而非按列：后者每次写跨 1504 B stride，PSRAM 必然 cache miss。
 */

#include "ui_waveform.h"

#include <math.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "app_config.h"
#include "ecg_pipeline.h"

static const char *TAG = "ui_wave";

/* ============================================================================
 * 布局：canvas 只做 plot 区，角标在 canvas 外
 *
 *   屏幕 y=0   ┌────────────────────────────┐
 *              │ 角标（独立 label，y=1）      │
 *   屏幕 y=8   │  ┌──────────────────────┐  │  ← canvas 起点(24,8)
 *              │  │  plot 752×200        │  │
 *   屏幕 y=208 │  └──────────────────────┘  │
 *   屏幕 y=216 └────────────────────────────┘ 画布 1 结束
 * ========================================================================== */

#define CW      APP_PLOT_W          /* 752 —— canvas 宽 */
#define CH      APP_PLOT_H          /* 200 —— canvas 高 */
#define CX0     ((APP_CANVAS_W - APP_PLOT_W) / 2)   /* 24 */
#define CY0     ((APP_CANVAS_H - APP_PLOT_H) / 2)   /* 8 */

#define BASELINE_Y  (CH / 2)        /* 100，canvas 内坐标 */

/* 每帧周期 50 ms ⇒ 20 FPS */
#define FRAME_MS    50

/* ADC int16 → mV 的比例（µV/LSB）。
 *
 * ⚠⚠ 这是**显示流 int16 域**，不是固件 raw code 域。两个系数差 1.36 倍，混用会错 36%：
 *
 *     ECG_LSB_OUT_UV    = 0.1526 µV/LSB   ← 显示流 int16（±5 mV ↔ ±32767）**本工程用这个**
 *     ECG_UV_PER_CODE   = 0.1122 µV/code  ← 固件 raw code 域（853 Hz 原始 AD 码）
 *
 * 依据：`ads1293_demo/Filter/ecg_out.h:55-67`
 *     #define ECG_LSB_OUT_UV    0.1526f  // ±5 mV <-> int16 ±32767 (5mV/32767)
 *     #define ECG_UV_PER_CODE   0.1122f  // AECG100 1mV 实测校准
 *     int16 定标：out = clamp(round(y_codes × ECG_UV_PER_CODE / ECG_LSB_OUT_UV), ±32767)
 *
 * 而 `ecg.bin` 的帧（T101 v2.2 显示帧，LEN=53/103）装的正是这个 int16 显示流。
 *
 * 三个独立验证（2026-10-23）：
 *     ① 需求 §2.4 的 RMS 列：843.2 × 0.1526 = 128.7 µV（文档写 129）✓
 *                             4195.0 × 0.1526 = 640.2 µV（文档写 640）✓
 *     ② 主文件峰峰 6580 × 0.1526/1000 = 1.004 mV ⇒ 20.1 px @ gain5
 *        ✓ 符合需求「QRS 1~2 mV ⇒ 20~40 px」
 *     ③ 源码注释「1 mV p-p 只占 ±3277 LSB(10%)」：6553 LSB 峰峰 / 2 = 3277 ✓
 */
#define UV_PER_LSB      0.1526f

/* ============================================================================
 * 状态
 * ========================================================================== */

static lv_obj_t         *s_canvas[2];
static uint16_t         *s_buf[2];
static lv_obj_t         *s_lbl_c1;      /* 画布 1 左侧角标 */
static lv_obj_t         *s_lbl_c1r;     /* 画布 1 右侧角标 */
/* ★T242（2026-11-12）：画布 2 的覆盖文案 `s_lbl_c2`
 *   （原内容 `"Single-channel file (Lead I) -- canvas 2 no data"`）**已删除**。
 *   原因：STM32 侧 `T239 步2` 起 CH2 是**真实第二路数据**、且 `_bch2` 上电默认 on
 *   ⇒ 屏上双画布常态就有数据，那条"无数据"提示只在单通道回退档才有意义，
 *   常态显示反而是**误导**（猛哥 2026-11-12 明确要求去掉）。
 *   ⚠ 不要"改成条件显示"再放回来：单通道时画布 2 是**空白网格**，本身已足够自明。
 *   ⚠ 若将来要加画布 2 的角标（如 `CH2 = IN3-IN1`），**新建** label，别复活这个。 */
static const ui_theme_t *s_theme;

static int32_t   s_disp[APP_RING_SAMPLES];      /* CH1 显示缓冲 */
static int32_t   s_disp2[APP_RING_SAMPLES];     /* CH2 显示缓冲（双通道模式用） */
static uint32_t  s_disp_wr;
static uint32_t  s_disp_cnt;
static uint32_t  s_disp2_wr;
static uint32_t  s_disp2_cnt;
/* ★T242（2026-11-12）：**上电默认双通道**（原为 false）。
 * 依据（猛哥 2026-11-12 拍板）：STM32 侧 `T239 步2` 起 `_bch2` 上电初值 = 1
 *   ⇒ 上行帧本来就是 109 B 双通道；ESP32 若还从单通道起步，屏上会先只画 CH1，
 *     直到有人敲 `485-send ch2 1` 才出第二路 —— 与"芯片侧已经给了两路"这个事实不符。
 * ⇒ 两边默认对齐，`485-send ch2 0|1` 负责后续开关（`cli_console.c:2054` 同时改
 *   STM32 的 `0x06` 与本变量，**两边一起动**，不会一个改一个不改）。
 * ⚠ 单通道回退时（`ch2 0`）：STM32 改发 59 B，`fr.n_ch2 = 0` ⇒ 环缓里 ch2 恒 0；
 *   本变量同时被置 false ⇒ 画布 2 只剩空白网格，不会留下一条"假平线"。
 * ⚠ 不要改成"永远 true"：那样 `ch2 0` 之后画布 2 会画一条贴基线的平线，看着像有数据。
 * ⚠ **已知边界**：若有人从**别的通道**（WinApp / STM32 ASCII `test ch2 off`）把 STM32 改成
 *   单通道，而 ESP32 没被告知，则本变量仍 true、收到 59 B 帧 ⇒ 画布 2 是一条平线。
 *   补一句 `485-send ch2 1` 即可（它会同时把 STM32 与本地都置成双通道）。
 *   ⇒ 不做"按帧长自动跟随"：那需要在 pipeline 任务里调 LVGL（非线程安全），
 *     收益只是省一条命令，不值这个风险。 */
static bool      s_dual = true;                 /* 双通道显示模式（上电默认开） */

static uint16_t  s_c_bg, s_c_gmin, s_c_gmaj, s_c_base, s_c_ch1, s_c_ch2, s_c_ruler;
static uint16_t  s_c_rpeak;                     /* R 峰标记（S7） */

/* ---- S7：R 峰标记的时间轴对齐 ----
 *
 * 用一个**永不重置**的绝对样本号把两边拴在一起：
 *   生产者  s_push_seq      每次成功 push +1，跨换文件/重播/清屏一直累加
 *   本模块  s_pulled_total  每次成功 pop  += n，同样一直累加
 *   峰的年龄  ago = s_pulled_total - 1 - peak_abs
 *
 * 试过并被实测否决的两条路（别回头走）：
 *   ① epoch 内相对索引：换文件/清屏时归零，而 UI 下一帧才发现 ⇒ 永久错位一帧；
 *   ② 发布"相对年龄"让 UI 每帧累加：峰是在一帧取数批次**中途**检出的，
 *      "检出后到本帧取数结束"这段取走的样本数每拍不同（0~37），补不进去
 *      ⇒ 每个点有 ±25 样本（±7.5 px）随机误差，表现为"有的点打正、有的落在下降支"。
 *
 * ⚠ `s_pulled_total` **绝对不许在清屏/换文件时归零** —— 一归零就回到方案①的坑。
 *   清屏只需把已存的峰列表清掉。
 *
 * epoch 只用于"换文件/重播/清屏时把陈旧标记立即作废"。 */
static uint32_t  s_peak_abs[APP_RPEAK_QUEUE_DEPTH];   /* 峰的全局绝对样本号 */
static uint32_t  s_peak_n;
static bool      s_rp_show = true;                    /* ★T216：R 峰打点显示开关（默认开） */
static uint32_t  s_epoch;                       /* 已对齐到哪个 epoch（0 = 未对齐） */
static uint32_t  s_pull_n;                      /* 本帧取走的样本数（诊断用） */
static uint32_t  s_pulled_total;                /* 累计取走样本数（**永不重置**） */

/* 画点诊断开关：置 1 时每 20 帧打一行「每帧画出几个点 / 已存几个 / ago 序列」。
 * 2026-10-26 用它查清"有的 R 波没有红点"（马克笔标记漏画）—— 平时置 0。 */
#define RP_MARK_DEBUG 0
static uint32_t  s_mark_drawn;

static float     s_gain       = APP_GAIN_5;
static float     s_speed      = APP_SPEED_25_MM_S;
static float     s_code_to_px = 0.0f;
static bool      s_frozen     = false;      /* 冻结：只停绘制，管线继续（需求 §8.3） */

static int16_t   s_gx[130];
static uint8_t   s_gx_major[130];
static int       s_gx_n;
static int16_t   s_gy[60];
static uint8_t   s_gy_major[60];
static int       s_gy_n;

static volatile uint32_t s_frames;
static volatile uint32_t s_fps_x10;
static volatile uint32_t s_render_us;
static int64_t           s_t0_us;

/* 帧间隔极值（µs）：平均 fps 看不出"顿挫"，要看每帧间隔的**极差**。
 * 一个 13.6 FPS 但间隔恒为 73 ms 的画面，观感远好于 平均值相同但 40/110 ms 交替。 */
static int64_t           s_dt_min_us = 999999;
static int64_t           s_dt_max_us = 0;
static int64_t           s_last_frame_us = 0;

/* 波形列聚合缓冲（每列 min/max y）。放静态区，避免在 LVGL 任务栈上开 1.5 KB */
static int16_t   s_col_min[APP_PLOT_W];
static int16_t   s_col_max[APP_PLOT_W];

/* ============================================================================
 * 低层绘制
 * ========================================================================== */

static inline uint16_t c565(uint32_t hex)
{
    return lv_color_hex(hex).full;
}

static inline void put_px(uint16_t *b, int x, int y, uint16_t c)
{
    if ((unsigned)x < (unsigned)CW && (unsigned)y < (unsigned)CH) {
        b[y * CW + x] = c;
    }
}

static void canvas_fill(uint16_t *b, uint16_t color)
{
    uint32_t *p = (uint32_t *)b;
    const uint32_t c2 = ((uint32_t)color << 16) | (uint32_t)color;
    const size_t n = (size_t)CW * CH / 2;
    for (size_t i = 0; i < n; i++) {
        p[i] = c2;
    }
}

/* 实心圆点（R 峰标记）。半径 2.6 px ⇒ 7×7 邻域内 21 个像素，
 * 直接逐像素判 dx²+dy² ≤ r² 即可，比跑一遍 Bresenham 圆快也更好读。 */
static void draw_dot(uint16_t *b, int cx, int cy, float r, uint16_t color)
{
    const int   ri = (int)(r + 0.5f);
    const float r2 = r * r;
    for (int dy = -ri; dy <= ri; dy++) {
        for (int dx = -ri; dx <= ri; dx++) {
            if ((float)(dx * dx + dy * dy) <= r2) {
                put_px(b, cx + dx, cy + dy, color);
            }
        }
    }
}

static void build_grid(void)
{
    s_gx_n = 0;
    for (int mm = 0; mm <= 125 && s_gx_n < (int)(sizeof(s_gx) / sizeof(s_gx[0])); mm++) {
        const int x = (int)((float)mm * APP_MMX_PX_PER_MM + 0.5f);
        if (x > CW - 1) {
            break;
        }
        s_gx[s_gx_n]       = (int16_t)x;
        s_gx_major[s_gx_n] = (mm % 5 == 0) ? 1u : 0u;
        s_gx_n++;
    }

    s_gy_n = 0;
    for (int mm = 0; mm <= 50 && s_gy_n < (int)(sizeof(s_gy) / sizeof(s_gy[0])); mm++) {
        const int y = (int)((float)mm * APP_MMY_PX_PER_MM + 0.5f);
        if (y > CH - 1) {
            break;
        }
        s_gy[s_gy_n]       = (int16_t)y;
        s_gy_major[s_gy_n] = (mm % 5 == 0) ? 1u : 0u;
        s_gy_n++;
    }

    ESP_LOGI(TAG, "grid %dx%d canvas: %d vertical (%d major) x %d horizontal (%d major)",
             CW, CH, s_gx_n, s_gx_n / 5 + 1, s_gy_n, s_gy_n / 5 + 1);
}

static void draw_grid(uint16_t *b)
{
    /* 竖线：按行扫描（行内连续，cache 友好） */
    for (int y = 0; y < CH; y++) {
        uint16_t *row = b + y * CW;
        for (int i = 0; i < s_gx_n; i++) {
            row[s_gx[i]] = s_gx_major[i] ? s_c_gmaj : s_c_gmin;
        }
    }
    /* 横线：整行连续写 */
    for (int i = 0; i < s_gy_n; i++) {
        const uint16_t c = s_gy_major[i] ? s_c_gmaj : s_c_gmin;
        uint16_t *row = b + s_gy[i] * CW;
        for (int x = 0; x < CW; x++) {
            row[x] = c;
        }
    }
}

static void draw_baseline(uint16_t *b)
{
    uint16_t *row = b + BASELINE_Y * CW;
    for (int x = 0; x < CW; x++) {
        row[x] = s_c_base;
    }
}

/* 左上角 1 mV 定标标尺：低-高-低 方波，高度 = 增益 × mmY（随增益变，不得写死） */
static void draw_ruler(uint16_t *b)
{
    const int h   = (int)(s_gain * APP_MMY_PX_PER_MM + 0.5f);   /* 20 px @ gain5 */
    const int x0  = 12;
    const int yb  = 14 + h;
    const int seg = 9;

    for (int x = x0; x <= x0 + 3 * seg; x++) {
        /* 两段低电平 */
        if (x <= x0 + seg || x >= x0 + 2 * seg) {
            put_px(b, x, yb, s_c_ruler);
        } else {
            put_px(b, x, yb - h, s_c_ruler);
        }
    }
    for (int y = yb - h; y <= yb; y++) {
        put_px(b, x0 + seg,     y, s_c_ruler);
        put_px(b, x0 + 2 * seg, y, s_c_ruler);
    }
}

/* ============================================================================
 * 波形：按像素列取 min/max
 * ========================================================================== */

static void pull_samples(void)
{
    ecg_ringbuf_t *rb = ecg_pipeline_ringbuf();
    s_pull_n = 0;
    if (rb == NULL) {
        return;
    }
    /* 双通道模式时同时取 ch2；单通道时传 NULL（环缓里 ch2 恒 0，不必取） */
    static int32_t tmp1[256];
    static int32_t tmp2[256];

    size_t n;
    while ((n = ecg_ring_pop(rb, tmp1, s_dual ? tmp2 : NULL,
                             sizeof(tmp1) / sizeof(tmp1[0]))) > 0) {
        for (size_t i = 0; i < n; i++) {
            s_disp[s_disp_wr] = tmp1[i];
            s_disp_wr = (s_disp_wr + 1u) % APP_RING_SAMPLES;
            if (s_disp_cnt < APP_RING_SAMPLES) {
                s_disp_cnt++;
            }
            if (s_dual) {
                s_disp2[s_disp2_wr] = tmp2[i];
                s_disp2_wr = (s_disp2_wr + 1u) % APP_RING_SAMPLES;
                if (s_disp2_cnt < APP_RING_SAMPLES) {
                    s_disp2_cnt++;
                }
            }
        }
        /* S7：累计取走样本数（永不重置）。R 峰年龄 = 它 - 1 - 峰的绝对样本号 */
        s_pull_n      += (uint32_t)n;
        s_pulled_total += (uint32_t)n;
    }
}

/* 通用波形绘制：b = 目标画布缓冲；disp/wr/cnt = 样本环；color = 波形色。
 * 参数化是为了画布 2 能复用同一套绘制逻辑画 CH2。 */
static void draw_wave_buf(uint16_t *b, const int32_t *disp,
                          uint32_t wr, uint32_t cnt, uint16_t color)
{
    const uint32_t n = cnt;
    if (n < 2) {
        return;
    }

    const float pps = APP_PX_PER_SAMPLE(s_speed);       /* 0.3008 @ 25 mm/s */
    uint32_t vis = (uint32_t)((float)CW / pps);
    if (vis > n) {
        vis = n;
    }
    if (vis < 2) {
        return;
    }

    const uint32_t start = (wr + APP_RING_SAMPLES - vis) % APP_RING_SAMPLES;

    /* 最新样本贴右边界（需求 §4.1） */
    float xf = (float)(CW - 1) - (float)(vis - 1) * pps;

    for (int i = 0; i < CW; i++) {
        s_col_min[i] = 32767;
        s_col_max[i] = -32768;
    }

    for (uint32_t k = 0; k < vis; k++) {
        const uint32_t idx = (start + k) % APP_RING_SAMPLES;
        const float    vpx = (float)disp[idx] * s_code_to_px;
        const int      y   = BASELINE_Y - (int)lroundf(vpx);
        const int      x   = (int)lroundf(xf);

        if ((unsigned)x < (unsigned)CW) {
            if (y < s_col_min[x]) {
                s_col_min[x] = (int16_t)y;
            }
            if (y > s_col_max[x]) {
                s_col_max[x] = (int16_t)y;
            }
        }
        xf += pps;
    }

    /* 按列画竖线；并补上列间连接（某列的最高点高于右邻列的最低点时会有断点） */
    int prev_bottom = -1;
    for (int x = 0; x < CW; x++) {
        if (s_col_min[x] > s_col_max[x]) {
            continue;                           /* 该列无样本 */
        }
        int y0 = s_col_min[x];
        int y1 = s_col_max[x];

        if (prev_bottom >= 0 && y1 < prev_bottom - 1) {
            y1 = prev_bottom - 1;               /* 向下补齐，避免断线 */
        } else if (prev_bottom >= 0 && y0 > prev_bottom + 1) {
            y0 = prev_bottom + 1;
        }

        if (y0 < 0) {
            y0 = 0;
        }
        if (y1 > CH - 1) {
            y1 = CH - 1;
        }
        uint16_t *col = b + x;
        for (int y = y0; y <= y1; y++) {
            col[y * CW] = color;
        }
        prev_bottom = s_col_max[x];
    }
}

/* 画布刷新：把 **BPM 浮层遮住的那块矩形扣掉**再 invalidate。
 *
 * 为什么能省：浮层是画布**之上**的不透明对象，它盖住的那 160×50 像素
 * 画布照样重画进缓冲，但 flush 上去也看不见 —— 白刷。
 *
 * 📌 2026-10-26 浮层从右上移到**右下（按钮栏上方）** ⇒ 它现在落在**画布 2** 的区域
 *   （y 378..427，画布 2 是 224..423）。所以本函数对**两块画布通用**，
 *   由调用方把画布顶边传进来。单通道时画布 2 每帧本来就 not invalidate，
 *   交集为空/无需处理；双通道时才是真正省 time 的地方。
 *
 * 拆成「左 / 下 / 右」三块是为了通用（浮层右边缘 789 > 画布右边缘 775，
 * 所以实际只有「左 + 下」两块，右侧那块为空）。
 *
 * ⚠ LVGL 8.4 只暴露私有的 `_lv_inv_area()`（`src/core/lv_refr.h:72`），
 *   没有公开的 `lv_inv_area`。可接受：它只是"喂矩形给刷新器"，签名稳定；
 *   升级 v9 时改名重编会**编译期报错**，不会静默失效。 */
static void invalidate_canvas_excl_overlay(lv_obj_t *cv, int cy_top)
{
    const int cx1 = CX0,            cy1 = cy_top;
    const int cx2 = CX0 + CW - 1,   cy2 = cy_top + CH - 1;
    const int ox1 = APP_LCD_H_RES - APP_BPM_MARGIN_R - APP_BPM_W;
    const int oy1 = APP_BPM_Y;
    const int ox2 = APP_LCD_H_RES - APP_BPM_MARGIN_R - 1;
    const int oy2 = APP_BPM_Y + APP_BPM_H - 1;

    /* 交集（手写，不用私有的 _lv_area_intersect） */
    const int ix1 = (cx1 > ox1) ? cx1 : ox1;
    const int iy1 = (cy1 > oy1) ? cy1 : oy1;
    const int ix2 = (cx2 < ox2) ? cx2 : ox2;
    const int iy2 = (cy2 < oy2) ? cy2 : oy2;

#if !APP_INV_EXCL_OVERLAY
    (void)ox1; (void)oy1; (void)ox2; (void)oy2;
    (void)ix1; (void)iy1; (void)ix2; (void)iy2;
    lv_obj_invalidate(cv);                  /* 对照模式：整块刷，不做扣除 */
    return;
#endif

    lv_disp_t *disp = lv_obj_get_disp(cv);
    if (disp == NULL || ix1 > ix2 || iy1 > iy2) {
        lv_obj_invalidate(cv);              /* 不相交：照旧整块刷 */
        return;
    }

    lv_area_t r;
    if (ix1 > cx1) {                        /* ① 浮层左侧 */
        r.x1 = (lv_coord_t)cx1; r.y1 = (lv_coord_t)cy1;
        r.x2 = (lv_coord_t)(ix1 - 1); r.y2 = (lv_coord_t)cy2;
        _lv_inv_area(disp, &r);
    }
    if (iy2 < cy2) {                        /* ② 浮层下方 */
        r.x1 = (lv_coord_t)ix1; r.y1 = (lv_coord_t)(iy2 + 1);
        r.x2 = (lv_coord_t)ix2; r.y2 = (lv_coord_t)cy2;
        _lv_inv_area(disp, &r);
    }
    if (ix2 < cx2) {                        /* ③ 浮层右侧 */
        r.x1 = (lv_coord_t)(ix2 + 1); r.y1 = (lv_coord_t)cy1;
        r.x2 = (lv_coord_t)cx2;       r.y2 = (lv_coord_t)cy2;
        _lv_inv_area(disp, &r);
    }
}

/* ============================================================================
 * S7：R 峰标记
 * ========================================================================== */

/* 每帧同步 R 峰标记：① epoch 变了整体作废 ② 收新峰 ③ 丢掉滑出显示缓冲的。
 * 成本 = 一次 seqlock 读 + 最多 64 次比较。 */
static void sync_rpeaks(void)
{
    /* ① epoch 变了（换文件 / 重播 / 清屏）：旧峰与新波形无关，立即作废 */
    ecg_pipeline_hr_t hr;
    if (ecg_pipeline_get_hr(&hr)) {
        if (s_epoch == 0) {
            s_epoch = hr.epoch;
        } else if (hr.epoch != s_epoch) {
            s_epoch  = hr.epoch;
            s_peak_n = 0;
            ecg_pipeline_pop_rpeaks(NULL, 0);      /* 丢掉队列里的陈旧条目 */
            ESP_LOGI(TAG, "rpeak epoch -> %u, markers cleared", (unsigned)s_epoch);
            return;
        }
    }

    /* ② 收新峰 */
    uint32_t buf[16];
    uint32_t got;
    while ((got = ecg_pipeline_pop_rpeaks(buf, 16)) > 0) {
        for (uint32_t i = 0; i < got; i++) {
            if (s_peak_n < APP_RPEAK_QUEUE_DEPTH) {
                s_peak_abs[s_peak_n++] = buf[i];
            } else {
                /* 满了就整体左移，保住最近的峰 */
                memmove(&s_peak_abs[0], &s_peak_abs[1],
                        sizeof(s_peak_abs[0]) * (APP_RPEAK_QUEUE_DEPTH - 1));
                s_peak_abs[APP_RPEAK_QUEUE_DEPTH - 1] = buf[i];
            }
        }
    }

    /* ③ 只丢"老到画不出来"的。ago<0 表示该样本还没被取走，**必须留着**，
     *    下一帧就会变成 0 —— 早先把它一起裁掉就是"有的 R 波没有红点"的直接原因。 */
    uint32_t keep = 0;
    for (uint32_t i = 0; i < s_peak_n; i++) {
        const int32_t ago = (int32_t)(s_pulled_total - 1u - s_peak_abs[i]);
        if (ago < (int32_t)APP_RING_SAMPLES) {
            s_peak_abs[keep++] = s_peak_abs[i];
        }
    }
    s_peak_n = keep;
}

/* 在画布 1 上画 R 峰圆点（需求 §3.4：半径 2.6 px，峰上 10 px） */
static void draw_rp_marks(uint16_t *b, const int32_t *disp, uint32_t wr, uint32_t cnt)
{
    /* ★T216：打点显示开关。**只影响绘制，不影响检测** ——
     * 关掉后 BPM 照算，只是不在波形上画点（语义：打点是视觉叠加，不是算法开关）。
     * 关掉的瞬间屏上旧点不会立刻消失，下一次整屏重绘（≤200 ms）就不再画。 */
    if (!s_rp_show || s_peak_n == 0 || cnt < 2) {
        return;
    }

    const float pps = APP_PX_PER_SAMPLE(s_speed);
    uint32_t    vis = (uint32_t)((float)CW / pps);
    if (vis > cnt) {
        vis = cnt;
    }
    if (vis < 2) {
        return;
    }

    const uint32_t newest = (wr + APP_RING_SAMPLES - 1) % APP_RING_SAMPLES;

    for (uint32_t i = 0; i < s_peak_n; i++) {
        const int32_t ago = (int32_t)(s_pulled_total - 1u - s_peak_abs[i]);
        if (ago < 0 || ago >= (int32_t)vis) {
            continue;                       /* 峰不在当前可见窗口内 */
        }

        /* 兜底：在 ±HALF 样本内取局部极值，把点钉到"看得见的那个尖"上。
         * 定位本身实测 0~1 样本，这一步只防"差一两个样本落到斜坡上"。 */
        uint32_t pos  = (newest + APP_RING_SAMPLES - (uint32_t)ago) % APP_RING_SAMPLES;
        int32_t  best = disp[pos];
        int32_t  rel  = 0;
        for (int32_t k = -APP_RPEAK_SNAP_HALF; k <= APP_RPEAK_SNAP_HALF; k++) {
            const int32_t a = ago - k;
            if (a < 0 || a >= (int32_t)vis) {
                continue;
            }
            const uint32_t p = (newest + APP_RING_SAMPLES - (uint32_t)a) % APP_RING_SAMPLES;
            const int32_t  v = disp[p];
            const int32_t  av = (v < 0) ? -v : v;
            const int32_t  ab = (best < 0) ? -best : best;
            if (av > ab) {
                best = v;
                rel  = k;
            }
        }

        const int y = BASELINE_Y - (int)lroundf((float)best * s_code_to_px);
        const int x = (int)lroundf((float)(CW - 1) - (float)(ago - rel) * pps);

        draw_dot(b, x, y - APP_RPEAK_DOT_LIFT, APP_RPEAK_DOT_R, s_c_rpeak);
        s_mark_drawn++;
    }

#if RP_MARK_DEBUG
    /* 诊断：每 20 帧打一行，看「各点年龄间隔」是否严格等于 RR（60 bpm ⇒ 500） */
    static uint32_t dbg_frames;
    if (++dbg_frames >= 20) {
        ecg_pipeline_hr_t h;
        char ages[96];
        int  p = 0;
        for (uint32_t i = 0; i < s_peak_n && i < 8; i++) {
            const int32_t ago = (int32_t)(s_pulled_total - 1u - s_peak_abs[i]);
            p += snprintf(ages + p, sizeof(ages) - (size_t)p, "%d ", (int)ago);
        }
        if (ecg_pipeline_get_hr(&h)) {
            ESP_LOGI(TAG, "rp: drawn=%u stored=%u pull_n=%u hr_peaks=%u epoch=%u ago=[%s]",
                     (unsigned)s_mark_drawn, (unsigned)s_peak_n, (unsigned)s_pull_n,
                     (unsigned)h.peak_total, (unsigned)h.epoch, ages);
        }
        s_mark_drawn = 0;
        dbg_frames   = 0;
    }
#endif
}


/* ============================================================================
 * 每帧
 * ========================================================================== */

static void on_frame(lv_timer_t *timer)
{
    (void)timer;

    const int64_t t_start = esp_timer_get_time();

    /* 帧间隔统计（首帧跳过） */
    if (s_last_frame_us != 0) {
        const int64_t dt = t_start - s_last_frame_us;
        if (dt < s_dt_min_us) s_dt_min_us = dt;
        if (dt > s_dt_max_us) s_dt_max_us = dt;
    }
    s_last_frame_us = t_start;

    /* 取样本：**冻结时也要取**，否则环缓会满、解冻后看到的是旧数据（需求 §8.3） */
    pull_samples();

    /* S7：对齐峰索引（冻结时也要做，否则解冻后标记会滞后一帧对齐） */
    sync_rpeaks();

    if (s_frozen) {
        /* 冻结：不重绘，保留最后一帧画面 */
        return;
    }

    /* ---- 画布 1：CH1 ---- */
    uint16_t *b1 = s_buf[0];
    canvas_fill(b1, s_c_bg);
    draw_grid(b1);
    draw_baseline(b1);
    draw_ruler(b1);
    draw_wave_buf(b1, s_disp, s_disp_wr, s_disp_cnt, s_c_ch1);
    draw_rp_marks(b1, s_disp, s_disp_wr, s_disp_cnt);
    invalidate_canvas_excl_overlay(s_canvas[0], APP_CANVAS1_Y + CY0);

    /* ---- 画布 2：双通道模式才画 CH2；否则保持静态「无数据」---- */
    if (s_dual) {
        uint16_t *b2 = s_buf[1];
        canvas_fill(b2, s_c_bg);
        draw_grid(b2);
        draw_baseline(b2);
        draw_wave_buf(b2, s_disp2, s_disp2_wr, s_disp2_cnt, s_c_ch2);
        invalidate_canvas_excl_overlay(s_canvas[1], APP_CANVAS2_Y + CY0);
    }

    s_render_us = (uint32_t)(esp_timer_get_time() - t_start);
    s_frames++;

    const int64_t dt = t_start - s_t0_us;
    if (dt >= 2000000LL) {
        s_fps_x10 = (uint32_t)((uint64_t)s_frames * 10000000ULL / (uint64_t)dt);
        ESP_LOGI(TAG, "fps=%u.%u  render=%u us  dt=[%d,%d] ms  samples=%u  gain=%.1f  speed=%.1f",
                 s_fps_x10 / 10, s_fps_x10 % 10, (unsigned)s_render_us,
                 (int)(s_dt_min_us / 1000), (int)(s_dt_max_us / 1000),
                 (unsigned)s_disp_cnt, (double)s_gain, (double)s_speed);
        s_frames    = 0;
        s_t0_us     = t_start;
        s_dt_min_us = 999999;
        s_dt_max_us = 0;
    }
}

/* ============================================================================
 * 对外接口
 * ========================================================================== */

static void apply_theme(const ui_theme_t *t)
{
    s_theme   = t;
    s_c_bg    = c565(t->canvas_bg);
    s_c_gmin  = c565(t->grid_minor);
    s_c_gmaj  = c565(t->grid_major);
    s_c_base  = c565(t->baseline);
    s_c_ch1   = c565(t->wave_ch1);
    s_c_ch2   = c565(t->wave_ch2);
    s_c_ruler = c565(t->ruler);
    s_c_rpeak = c565(t->rpeak);
}

static void update_scale(void)
{
    const float px_per_mv = s_gain * APP_MMY_PX_PER_MM;      /* 20 @ gain5 */
    s_code_to_px = (UV_PER_LSB / 1000.0f) * px_per_mv;
}

/* 画布 2 的静态内容（第一阶段无数据） */
static void paint_canvas2_static(void)
{
    canvas_fill(s_buf[1], s_c_bg);
    draw_grid(s_buf[1]);
    draw_baseline(s_buf[1]);
    lv_obj_invalidate(s_canvas[1]);
}

esp_err_t ui_waveform_create(lv_obj_t *scr)
{
    if (scr == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    /* 上电默认 **深色 B 套**（2026-10-23 拍板；S8 会改为从 NVS 读取）。
     * 与 ui_button_bar 的默认必须一致，否则上电瞬间画布与按钮栏不同色。 */
    apply_theme(&UI_THEME_DARK);
    update_scale();
    build_grid();

    const size_t bytes = (size_t)CW * CH * sizeof(uint16_t);

    for (int i = 0; i < 2; i++) {
        s_buf[i] = (uint16_t *)heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (s_buf[i] == NULL) {
            ESP_LOGE(TAG, "alloc canvas %d failed (%u B)", i, (unsigned)bytes);
            return ESP_ERR_NO_MEM;
        }
        s_canvas[i] = lv_canvas_create(scr);
        lv_canvas_set_buffer(s_canvas[i], s_buf[i], CW, CH, LV_IMG_CF_TRUE_COLOR);
        lv_obj_set_pos(s_canvas[i], CX0,
                       ((i == 0) ? APP_CANVAS1_Y : APP_CANVAS2_Y) + CY0);
    }

    /* ---- 角标 / 覆盖文案：独立 label，不画进 canvas（省 flush 与字形渲染）---- */
    s_lbl_c1 = lv_label_create(scr);
    lv_obj_set_style_text_font(s_lbl_c1, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_obj_set_style_text_color(s_lbl_c1, lv_color_hex(s_theme->text), LV_PART_MAIN);
    lv_label_set_text(s_lbl_c1, "Lead I   CH1 = IN2-IN1");
    lv_obj_set_pos(s_lbl_c1, CX0, APP_CANVAS1_Y + 1);

    s_lbl_c1r = lv_label_create(scr);
    lv_obj_set_style_text_font(s_lbl_c1r, &lv_font_montserrat_12, LV_PART_MAIN);
    lv_obj_set_style_text_color(s_lbl_c1r, lv_color_hex(s_theme->text), LV_PART_MAIN);
    lv_label_set_text(s_lbl_c1r, "5.0 s / 500.0 Hz");
    lv_obj_align(s_lbl_c1r, LV_ALIGN_TOP_MID, 0, APP_CANVAS1_Y + 1);

    /* ★T242：画布 2 **不再有覆盖文案**（原 `"Single-channel file (Lead I) -- canvas 2 no data"`）。
     *   本工程已常态双通道（STM32 `_bch2` 上电 = 1，CH2 有真实数据）⇒ 该提示恒为假。
     *   单通道回退时画布 2 保留**空白网格**（见下面 `paint_canvas2_static()`），足以自明。 */

    /* ---- 画布 2 静态一次 ---- */
    paint_canvas2_static();

    /* ---- 画布 1 先铺一次，避免首帧白屏 ---- */
    canvas_fill(s_buf[0], s_c_bg);
    draw_grid(s_buf[0]);
    draw_baseline(s_buf[0]);
    draw_ruler(s_buf[0]);

    s_t0_us = esp_timer_get_time();
    lv_timer_create(on_frame, FRAME_MS, NULL);

    ESP_LOGI(TAG, "waveform ready: canvas %dx%d @(%d,%d), %u KiB each, total %u KiB PSRAM",
             CW, CH, CX0, CY0, (unsigned)(bytes / 1024), (unsigned)(bytes * 2 / 1024));
    return ESP_OK;
}

void ui_waveform_set_theme(const ui_theme_t *theme)
{
    if (theme == NULL) {
        return;
    }
    apply_theme(theme);

    lv_obj_set_style_text_color(s_lbl_c1,  lv_color_hex(s_theme->text), LV_PART_MAIN);
    lv_obj_set_style_text_color(s_lbl_c1r, lv_color_hex(s_theme->text), LV_PART_MAIN);
    paint_canvas2_static();
}

void ui_waveform_set_gain(float gain)
{
    if (gain <= 0.0f) {
        return;
    }
    s_gain = gain;
    update_scale();
}

void ui_waveform_set_speed(float mm_per_s)
{
    if (mm_per_s <= 0.0f) {
        return;
    }
    s_speed = mm_per_s;

    /* 右侧角标跟着变：可见窗口 = plotW / (mmX × 走纸)
     *   12.5 mm/s → 10.0 s
     *   25   mm/s →  5.0 s
     *   50   mm/s →  2.5 s
     * ⚠ 用定点整数拼字符串，不用 %f —— LVGL 的 LV_SPRINTF_USE_FLOAT 默认关闭。 */
    if (s_lbl_c1r != NULL) {
        const float span_s = (float)CW / (APP_MMX_PX_PER_MM * mm_per_s);
        const int   ds     = (int)(span_s * 10.0f + 0.5f);      /* 0.1 s 单位 */
        lv_label_set_text_fmt(s_lbl_c1r, "%d.%d s / 500.0 Hz", ds / 10, ds % 10);
    }
}

float ui_waveform_get_speed(void)
{
    return s_speed;
}

float ui_waveform_get_gain(void)
{
    return s_gain;
}

uint32_t ui_waveform_fps_x10(void)
{
    return s_fps_x10;
}

uint32_t ui_waveform_render_us(void)
{
    return s_render_us;
}

void ui_waveform_set_frozen(bool frozen)
{
    s_frozen = frozen;
    ESP_LOGI(TAG, "frozen = %d（只停绘制，管线与取样本继续）", (int)frozen);
    if (!frozen) {
        /* 解冻后立刻按最新数据重画一帧，避免等到下个 50 ms 周期才有画面 */
        invalidate_canvas_excl_overlay(s_canvas[0], APP_CANVAS1_Y + CY0);
    }
}

bool ui_waveform_is_frozen(void)
{
    return s_frozen;
}

/* ★T216：R 峰打点显示开关（CLI `rpeak on|off`）。
 * 🛑 只切绘制，不碰检测器 —— `sync_rpeaks()` 照常收峰、BPM 照常算。
 * 关掉后旧点不会立刻消失，下一次整屏重绘（≤200 ms）就不画了；
 * 重新打开时也只会画**当前可见窗口内**的峰（`draw_rp_marks()` 里按 `ago` 过滤，
 * 窗口外的一律跳过）⇒ 不会冒出十几秒前的历史点。 */
void ui_waveform_set_rp_show(bool on)
{
    if (s_rp_show == on) {
        return;
    }
    s_rp_show = on;
    ESP_LOGI(TAG, "rpeak marks -> %s（只停绘制，检测与 BPM 不变）", on ? "on" : "off");
    /* 立刻重画一帧：否则「关掉」后旧点要等下一个刷新周期才消失 */
    invalidate_canvas_excl_overlay(s_canvas[0], APP_CANVAS1_Y + CY0);
}

bool ui_waveform_get_rp_show(void)
{
    return s_rp_show;
}

void ui_waveform_clear(void)
{
    /* 需求 §8.4：清波形缓冲。环缓里已有的样本不再补画（从下一个样本重新开始铺） */
    s_disp_wr   = 0;
    s_disp_cnt  = 0;
    s_disp2_wr  = 0;
    s_disp2_cnt = 0;

    /* S7：R 峰标记也要清（需求 §8.4「清空波形缓冲 + R 峰队列 + BPM 归 --」）。
     * ⚠ **只清峰列表，不要动 `s_pulled_total`** —— 它是永不重置的绝对计数，
     *   一旦归零就与生产者的 `s_push_seq` 错开，所有点会整体偏移。 */
    s_peak_n = 0;

    ESP_LOGI(TAG, "display buffer cleared (both channels) + rpeak markers");
}

void ui_waveform_set_dual_channel(bool dual)
{
    if (s_dual == dual) {
        return;
    }
    s_dual = dual;

    /* 切换时清空两路缓冲：切换前的 CH1 样本没有配对的 CH2，留着会时间轴错位 */
    ui_waveform_clear();

    /* ★T242：原这里负责显隐 `s_lbl_c2`（"canvas 2 no data" 提示），该 label 已删除。
     * 只需在**回单通道**时把画布 2 重刷成空白网格 —— 否则会留下上一次双通道的 CH2 轨迹，
     * 看着像"单通道却还在画第二路"。双通道方向不用动：`on_frame()` 下一帧就会覆盖。 */
    if (!dual) {
        paint_canvas2_static();
    }

    ESP_LOGI(TAG, "dual channel = %d%s", (int)dual,
             dual ? "（画布 2 开始画 CH2）" : "");
}

bool ui_waveform_is_dual_channel(void)
{
    return s_dual;
}
