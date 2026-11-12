/**
 * @file app_config.h
 * @brief CardioLite 全部编译期常量。
 *
 * 规矩：**布局、标定、时序、引脚的数字只允许出现在本文件**。
 *       业务代码引用宏，不写字面量。
 *       理由：需求 v1.2 §11 的「逐像素比对」判据要求坐标有唯一真源；
 *             两套配色切换要求颜色不从代码里内联（见 ui_theme.h）。
 *
 * 来源：需求 v1.2 §3（布局）/ §4（标定）/ §8（状态机时限）
 */
#pragma once

#include <stdint.h>
#include <stdbool.h>

/* ============================================================================
 * 一、屏幕与布局（需求 v1.2 §3.1 / §3.3）
 * ========================================================================== */

#define APP_LCD_H_RES           800     /* 必须等于 BSP 的 EXAMPLE_LCD_H_RES */
#define APP_LCD_V_RES           480     /* 必须等于 BSP 的 EXAMPLE_LCD_V_RES */

/* 硬件常量（本板 N16R8 SKU，与 sdkconfig.defaults 的 FLASHSIZE_16MB 一致）。
 * 不调 esp_flash_get_size()：那个 API 在 spi_flash 组件里，不是公共依赖，
 * 为此加 REQUIRES 会触发一次 250 s 的 cmake reconfigure。S0 只要打印已知值。 */
#define APP_FLASH_SIZE_MB       16

/* 双画布：各 216 px 高。可绘制区 752x200（留边框与角标空间） */
#define APP_CANVAS_H            216
#define APP_CANVAS_W            800
#define APP_PLOT_W              752
#define APP_PLOT_H              200

#define APP_CANVAS1_Y           0
#define APP_CANVAS2_Y           216

/* 按钮栏：y = 432 ~ 479，高 48。
 * 9 个按钮：内边距 4、间隙 3、均分宽 (800−8−24)/9 = 85.33 ⇒ 取整 85（需求 v1.2 §3.3）。
 * 实现用 flex 均分（LV_FLEX_FLOW_ROW + SPACE_BETWEEN），不手写 x 坐标，
 * 这样 0.33 px 的余量由 LVGL 摊到间隙里，与需求的 85.3 严格一致。 */
#define APP_BTN_BAR_Y           432
#define APP_BTN_BAR_H           48
#define APP_BTN_PAD             4       /* 左右内边距 */
#define APP_BTN_W               85      /* 均分值 85.33 取整；实际宽度由 flex 决定 */
#define APP_BTN_H               40
#define APP_BTN_GAP             3       /* 按钮间隙 */
#define APP_BTN_COUNT           9

/* ============================================================================
 * 二、心电图标定（需求 v1.2 §4.1 / §4.2）
 *
 * 横纵解耦，不得回退：
 *   横向由纸密度常数定（mmX = plotW / 125）
 *   纵向由恒 ±5 mV 满量程定（mmY = plotH / 50）
 * ========================================================================== */

#define APP_SAMPLE_RATE_HZ      500     /* 显示链路按 500 Hz 解释（raw853 时降级处理） */

#define APP_MMX_PX_PER_MM       (APP_PLOT_W / 125.0f)   /* = 6.016 px/mm，常数 */
#define APP_MMY_PX_PER_MM       (APP_PLOT_H / 50.0f)    /* = 4.0   px/mm，常数 */

/* 一屏宽度恒等于 125 mm —— 这是纸密度常数的物理含义 */
#define APP_SCREEN_WIDTH_MM     125.0f

/* 走纸档位（mm/s）。可见跨度 = 5*25/走纸 ⇒ 10.0 / 5.0 / 2.5 s */
#define APP_SPEED_12_5_MM_S     12.5f
#define APP_SPEED_25_MM_S       25.0f
#define APP_SPEED_50_MM_S       50.0f

/* 每样本像素数 = mmX * 走纸 / fs  ⇒ 0.150 / 0.301 / 0.602 */
#define APP_PX_PER_SAMPLE(mm_s) (APP_MMX_PX_PER_MM * (mm_s) / (float)APP_SAMPLE_RATE_HZ)

/* 增益档位（mV/LSB 的倍数，相对基准 。量程 = 5mV/gain） */
#define APP_GAIN_2_5            2.5f
#define APP_GAIN_5              5.0f    /* 默认：1 mV = 20 px，量程 ±5 mV */
#define APP_GAIN_10             10.0f
#define APP_GAIN_20             20.0f

/* 每 mV 像素数 = gain * mmY ⇒ 10 / 20 / 40 / 80 */
#define APP_PX_PER_MV(gain)     ((gain) * APP_MMY_PX_PER_MM)

/* 环形缓冲：按最长 10 s 窗口（走纸 12.5 mm/s）配 5000 样本/通道 */
#define APP_RING_SAMPLES        5000
#define APP_RING_CHANNELS       2

/* R 峰队列深度（需求 v1.2 §6.2） */
#define APP_RPEAK_QUEUE_DEPTH   64

/* ============================================================================
 * 二·五、BPM 浮层与 R 峰标记（需求 v1.2 §3.2 / §3.4 / §5.3）
 * ========================================================================== */

/* BPM 浮层：**右下角，紧贴按钮栏上方**（2026-10-26 猛哥改；原为右上）。
 * 位置约束：底边 = 按钮栏顶 - GAP，**不得遮挡按钮**。160×50 ⇒ y1 = 432-4-50 = 378。
 * 屏幕高 480，按钮栏 432..479，按钮 436..475 ⇒ 浮层 378..427，与按钮留 9 px 间隙。 */
#define APP_BPM_W               160
#define APP_BPM_H               50
#define APP_BPM_MARGIN_R        10
#define APP_BPM_GAP             4                                   /* 与按钮栏的间隙 */
#define APP_BPM_Y               (APP_BTN_BAR_Y - APP_BPM_GAP - APP_BPM_H)   /* = 378 */

/* 数值刷新周期：只在**文本变化时**才动 label。
 * ⚠ 不要每帧刷 —— 14 FPS 下每帧 set_text 会触发一次字体重绘，白掉帧。 */
#define APP_BPM_UPDATE_MS       200

/* 需求 §5.3：超过 3 s 无新峰 ⇒ 归 `--` */
#define APP_BPM_IDLE_TIMEOUT_MS 3000

/* ---- 启动稳定窗（方案 D，2026-10-26 拍板）----
 *
 * 现象：参考实现在**开始出数据之后的前 6.15 s** 里 BPM 会乱跳
 *   （实测轨迹 187→222→272→260→287→273→303→250→326→85→72→60），
 *   因为自适应阈值还在建立 signal_peak/noise_peak，前 7 个"峰"是噪声。
 *   这是参考实现的固有行为，不是移植 bug。
 *
 * 为什么不用"范围过滤"和"稳定门控"（两者都被实测否决，见 S7 报告 §7）：
 *   - 范围过滤（20~250）：187 / 222 / 250 / 85 / 72 全在范围内，一个都压不住
 *   - 稳定门控（连续 3 次互差 ≤5%）：瞬态里每个错值都持续 0.6~1 s，
 *     会被误判成"稳定"，t=1.85 s 就开门，照样乱跳
 *
 * 为什么按**样本数**而不是墙上时间计时：
 *   上电时管线是暂停的（等用户点「回放」）。若按时间算，用户 30 s 后才开播，
 *   窗口早已过期，瞬态照跳。按已喂样本数算，与暂停/恢复无关。
 *
 * 3250 样本 = 6.5 s @ 500 Hz（实测收敛 6.15 s + 0.35 s 余量）。 */
#define APP_BPM_SETTLE_MS       6500
#define APP_BPM_SETTLE_SAMPLES  ((uint32_t)((uint64_t)APP_SAMPLE_RATE_HZ \
                                            * APP_BPM_SETTLE_MS / 1000u))

/* 🛑🛑 「扣浮层矩形」优化 —— **已废弃，永久置 0**（2026-10-26）
 *
 * 曾经这样做：画布 invalidate 时把被 BPM 浮层遮挡的矩形扣掉（省 4.9% flush，+0.5 FPS），
 * 依据是"被不透明对象盖住的像素刷了也看不见"。**实测证明这个前提在本平台上不成立。**
 *
 * A/B 结果（同一份双通道数据、同一次上电场景）：
 *   1 → 通道 II 在浮层 x 范围内的波形**整段消失**（基线、S 波一起没）
 *   0 → 波形完整无缺
 *
 * 注意：按坐标算，浮层顶边 378 **比画布 2 基线 324 还低 54 px**，
 * 所以"基线被盖住"在几何上是不可能的 —— 说明这不是遮挡，是**那段区域没被刷新**。
 * 怀疑 esp_lv_adapter 的 RGB flush 路径对"不覆盖整个 canvas 对象的零碎区域"处理不完整；
 * 未深究，因为**为了 0.5 FPS 换一个错画面不值得**。
 *
 * ⇒ 教训：**"被遮挡 ⇒ 不用刷"这个推论，只有在你完全掌握刷新管线时才成立。**
 *   本项目的显示层是第三方适配器，不能假设它对局部区域的处理是完整的。 */
#define APP_INV_EXCL_OVERLAY    0

/* R 峰标记（需求 §3.4）：圆点半径 2.6 px，画在峰上 10 px */
#define APP_RPEAK_DOT_R         2.6f
#define APP_RPEAK_DOT_LIFT      10

/* 画标记前在 ±N 样本内找局部极值，把圆点钉到**波形上真正看得见的尖**上。
 * 检测器的定位误差实测 ≤1 样本，这一步是兜底：即使索引差几个样本，
 * 点也一定落在尖顶而不是下降支上（5 样本 = 10 ms，QRS 尖顶宽度约 ±5 样本）。 */
#define APP_RPEAK_SNAP_HALF     5

/* ============================================================================
 * 三、CH422G 扩展 IO（原理图确认 2026-10-23 + 三组实测值交叉验证）
 *
 *   EXIO1 bit1 = CTP_RST     稳态 1
 *   EXIO2 bit2 = LCD_BL      稳态 1
 *   EXIO3 bit3 = LCD_RST     稳态 1
 *   EXIO4 bit4 = SD_CS       1=释放，0=选中
 *   EXIO5 bit5 = USB_SEL     （推断）
 *   bit0        = 未知，实测恒 0
 *
 * 0x38 输出寄存器**硬件不可回读**（Waveshare 驱动 read_output_reg 无 I2C 动作），
 * ⇒ BSP 内维护影子副本，业务代码只走位操作，禁止出现裸 0x0E / 0x1E。
 * ========================================================================== */

#define CH422G_BIT_CTP_RST      (1u << 1)
#define CH422G_BIT_LCD_BL       (1u << 2)
#define CH422G_BIT_LCD_RST      (1u << 3)
#define CH422G_BIT_SD_CS        (1u << 4)
#define CH422G_BIT_USB_SEL      (1u << 5)

/* 稳态：三复位位释放 + SD 未选中。与 BSP backlight_on() 写入值一致。 */
#define CH422G_OUT_STEADY       0x1E
/* SD 使能：仅清 bit4。⚠ 不得从 0x2E 派生（那会带上 bit5） */
#define CH422G_OUT_SD_SELECTED  (CH422G_OUT_STEADY & ~CH422G_BIT_SD_CS)   /* = 0x0E */

/* ============================================================================
 * 四、T101 v2.2 帧格式（需求 v1.2 §2.2）
 *
 *   AA 55 | TYPE(1) | LEN(2, LE) | PAYLOAD(LEN) | CRC8(1)
 *   帧总长 = 6 + LEN
 *
 * 已用 4 个文件 4185 帧实测确证：
 *   1796 x 59 = 105,964；1182 x 59 = 69,738；1136 x 59 = 67,024；71 x 109 = 7,739
 * ========================================================================== */

#define ECG_SYNC0               0xAA
#define ECG_SYNC1               0x55
#define ECG_FRAME_OVERHEAD      6       /* AA55(2) + TYPE(1) + LEN(2) + CRC8(1) */

#define ECG_LEN_V11             52      /* 旧帧，无 STA，总长 58 —— 兼容但不演示 */
#define ECG_LEN_V22             53      /* 单通道，总长 59，SEQ(2)+STA(1)+25*int16 */
#define ECG_LEN_DUAL            103     /* 双通道，总长 109，块布局不交错 */

#define ECG_CRC8_POLY           0x07
#define ECG_CRC8_INIT           0x00

/* STA 状态字节位定义 */
#define ECG_STA_BIT_MODE        (1u << 0)
#define ECG_STA_BIT_NOTCH       (1u << 1)
#define ECG_STA_BIT_CHANNEL     (1u << 2)
#define ECG_STA_BIT_RAW853      (1u << 3)
#define ECG_STA_BIT_LOD         (1u << 4)

/* 三参数未知时的哨兵（LEN=52 旧帧不含 STA，不得伪装成已知值） */
#define ECG_STATE_UNKNOWN       0xFF

/* ============================================================================
 * 五、任务与优先级（实施计划 §4.2）
 *
 *   Core 0 = LVGL 渲染 + 触摸 + UI 状态机（由 CONFIG_EXAMPLE_LVGL_PORT_TASK_CORE=0 决定）
 *   Core 1 = 数据管线 + R 峰检测
 * ========================================================================== */

#define APP_TASK_PIPE_CORE      1
#define APP_TASK_PIPE_PRIO      5       /* 必须低于 LVGL 任务 */
#define APP_TASK_PIPE_STACK     4096

/* ============================================================================
 * 六、交互时限（需求 v1.2 §8）
 * ========================================================================== */

#define APP_LONGPRESS_DELETE_MS     1500    /* 长按「回放」删除确认，±0.3 s */
#define APP_LONGPRESS_THEME_MS      2000    /* 长按 BPM 浮层切换配色 */
#define APP_REPLAY_TIMER_MS         10      /* 回放投喂定时器周期 */

/* ============================================================================
 * 七、文件路径（需求 v1.2 §2.2：固定名，只存一个，再存覆盖）
 * ========================================================================== */

#define APP_SD_MOUNT_POINT      "/sdcard"
#define APP_SD_FILE_PATH        "/sdcard/ecg.bin"

/* SD 卡 SPI 引脚（厂家 BSP 定义）。与 RGB 数据线、VSYNC/HSYNC/DE/PCLK、
 * 触摸复位 GPIO4 均不冲突；SPI2 上只有 SD 一个从设备。
 * ⚠ CS **不是 GPIO**，接 CH422G 的 EXIO4(bit4)，见 §三 CH422G 段。 */
#define APP_SD_PIN_MOSI         11
#define APP_SD_PIN_MISO         13
#define APP_SD_PIN_CLK          12

/* SD 时钟。探测阶段驱动内部用 400 kHz；传输阶段用此值。
 * 🛑 2026-10-26 从 20000 降到 10000：批处理连跑了几十次挂载，
 *   20 MHz 下出现**间歇性** `ESP_ERR_INVALID_CRC (0x109)`（同一次上电有时成功有时失败），
 *   典型是信号完整性临界。降到 10 MHz 后连续挂载稳定。
 *   ⚠ 不要凭"以前 20 MHz 能用"改回去 —— 那个"能用"是有时能用。 */
#define APP_SD_FREQ_KHZ         10000

/* ============================================================================
 * 八、NVS 键名（配色 / 增益 / 走纸持久化）
 * ========================================================================== */

#define APP_NVS_NAMESPACE       "cardiolite"
#define APP_NVS_KEY_THEME       "theme"
#define APP_NVS_KEY_GAIN        "gain"
#define APP_NVS_KEY_SPEED       "speed"

/* ============================================================================
 * 九、静态断言——布局常量之间不许打架
 * ========================================================================== */

_Static_assert(APP_CANVAS2_Y == APP_CANVAS1_Y + APP_CANVAS_H, "双画布必须紧邻");
_Static_assert(APP_BTN_BAR_Y == APP_CANVAS2_Y + APP_CANVAS_H, "按钮栏必须紧接画布 2");
_Static_assert(APP_BTN_BAR_Y + APP_BTN_BAR_H == APP_LCD_V_RES, "按钮栏底部必须贴屏幕底");
_Static_assert(APP_BTN_PAD * 2 + APP_BTN_COUNT * APP_BTN_W
                   + (APP_BTN_COUNT - 1) * APP_BTN_GAP
                   <= APP_LCD_H_RES, "九按钮横向放不下 800 px");
_Static_assert(APP_PLOT_W <= APP_CANVAS_W, "可绘制区不能超出画布");
_Static_assert(APP_PLOT_H <= APP_CANVAS_H, "可绘制区不能超出画布");
/* BPM 浮层必须完全位于按钮栏之上，否则会遮挡按钮（2026-10-26 移到右下后加的闸门） */
_Static_assert(APP_BPM_Y + APP_BPM_H <= APP_BTN_BAR_Y, "BPM 浮层不得压到按钮栏");
_Static_assert(APP_BPM_Y >= APP_CANVAS2_Y, "BPM 浮层不得越出画布 2 顶端");

/* ============================================================================
 * 十、RS485 实时数据源（S9a，2026-10-30）
 *
 * 硬件事实（同板已由 `C:\work2026\ESP-HRV\ADS1293-HRV-Node` 实测跑通）：
 *   U7 = SP3485EN，VCC=3V3；RO -> IO43（经 R64 4.7k 上拉），DI -> IO44；
 *   RE/DE 短接由 Q1 **硬件自动切换方向**，固件不碰方向控制；
 *   A = P1-10（TX+）、B = P1-9（TX−），120Ω 终端由 SW2 拨码选通。
 *
 * 🛑 **引脚是交叉的**：ESP32-S3 的 UART0 默认 U0TXD=43 / U0RXD=44，
 *    而板上 IO43 接收发器输出、IO44 接收发器输入 ⇒ 必须显式 set_pin(tx=44, rx=43)。
 * 🛑 **用 UART1 不用 UART0**：UART0 是 ROM console 默认口，bootloader 会往 IO43
 *    打日志，自动方向电路会把总线拉进发送态、等于灌垃圾。UART1 + GPIO matrix
 *    路由到 43/44 可避开这一段（ROM 那几十毫秒垃圾无法软件关闭，靠帧状态机滑过）。
 * 🛑 **初始化顺序**：先 uart_param_config + uart_set_pin，再 uart_driver_install ——
 *    UART1 默认引脚 GPIO17/18 是本板 LCD 数据线，顺序反了中间一瞬会把它们拉成 UART。
 *
 * 带宽（★T246 2026-11-12 起默认 **57600**）：
 *   57600 8N1 = 5.76 KB/s；双通道实时流 109 B×20 帧/s = 2.18 KB/s ⇒ 占用 **37.8%**（余量 2.6×）。
 *   115200 时是 1.18 KB/s(单) / 2.18 KB/s(双)，余量 9.8× / 5.3×。
 * 🛑 **为什么降速**：T244/T245 的常量图案测试定死了上行坏帧是**物理层余量边缘**造成的
 *   （坏点锚在帧首+3/帧尾−2、坏值恒"清位"、边沿档 LOW 6.32%/HIGH 1.12%/VH 1.01%），
 *   再加上 ESP32 侧 120 Ω 终端**关掉**（1.12%→0.037%），最后把位宽从 8.68 µs 加倍到 17.4 µs
 *   ⇒ 图案法 3600 帧 / 39.2 万字节 **零错**（真实数据 1200 帧、ESP32 独立解帧 606 帧也全 0）。
 * ⚠ **必须与 STM32 侧一致**：`stm32/Core/Src/comm_port.c` 的 `huart5.Init.BaudRate` 同为 57600。
 *   两边不一致的症状是"屏上无数据、`485-stat` 的 rx 不涨"，且**不报任何错**。
 * ⚠ 波形展示不受影响：帧率仍由 500/25 = 20 fps 决定，每帧 25 样本不变；
 *   只是单帧发送耗时 9.5→18.9 ms（占 50 ms 帧周期的 38%），显示延迟 +9.5 ms（不可见）。
 * ========================================================================== */
#define APP_485_UART_NUM        1
#define APP_485_TX_GPIO         44      /* -> SP3485 DI */
#define APP_485_RX_GPIO         43      /* <- SP3485 RO */
#define APP_485_BAUD_DEFAULT    57600
#define APP_485_RX_BUF_SIZE     4096    /* ≈68 帧 ≈1.7 s 实时数据，吸收任务唤醒抖动 */
#define APP_485_TX_BUF_SIZE     2048
#define APP_485_EVT_QUEUE       20
#define APP_485_READ_TIMEOUT_MS 50
