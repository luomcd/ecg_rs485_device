#ifndef __COMM_PORT_H__
#define __COMM_PORT_H__

/* ============================================================================
 * comm_port.h —— 上位机通信通道抽象层（T198 / 2026-11-03）
 * ============================================================================
 * 背景
 * ----
 * 改造前：显示流与命令回显共 **5 处直调 `CDC_Transmit_FS()`**（`demo.c` 1 处、
 * `ads1293.c` 4 处，其中 2 处已被 `#if 0` 屏蔽 ⇒ 实际活码 4 处）。
 * 接收侧早已抽象（`vcom_recv()` 只存 `VCOM_RxBuffer/RxLength`）⇒ 本层只补发送侧
 * 与 UART5 的接收喂入。
 *
 * 🛑 为什么必须「编译期二选一」而不是运行期切换
 * ------------------------------------------------
 * SensorTile 模组（STEVAL-STLCS01V1）内部把 USB 与 UART5 的两对引脚**物理短接**：
 *   `RXD-USB_DP` ≡ `PA12` ≡ `PD2`（UART5_RX）
 *   `TXD-USB_DM` ≡ `PA11` ≡ `PC12`（UART5_TX）
 * ⇒ 若两套外设同时初始化，会在同一根网络上**两个推挽输出对着驱动** ⇒ 必须互斥。
 * 故本层用宏选择，且 **UART5 模式下 `MX_USB_DEVICE_Init()` 整个不调用**。
 *
 * 硬件通路（UART5 模式）
 * ----------------------
 *   PC12 (UART5_TX, AF8) → ST2378 (1.8V→3.3V) → CN9.1 → USB转串口 RXD
 *   PD2  (UART5_RX, AF8) ← ST2378              ← CN9.2 ← USB转串口 TXD
 *   ⚠ 底板丝印把这两根按「对端视角」命名（CN9.1 叫 UART_RX、CN9.2 叫 UART_TX）
 *     ⇒ **按信号方向接**：CN9.1→适配器 RX，CN9.2→适配器 TX。接反了表现为
 *     「完全没数据」，不会烧，但会白折腾，先核这两根。
 *   ⚠ 焊桥只焊 **SB11 + SB20**；同组两片全焊 = 把 DP/DM 短接成自环。
 *
 * 速率账（115200 8N1 = 11520 B/s）
 * --------------------------------
 *   二进制档 `_popoutcnt=25` ⇒ 帧长 59 B、行率 500 Hz / 25 = **20 帧/s**
 *   ⇒ **1180 B/s = 带宽 10.2 %** ⇒ 单帧占线 5.12 ms，帧间隔 50 ms，余量充足。
 *   ASCII-1 档（`_popoutcnt=5`）单帧约 235 B ⇒ 20 帧/s ⇒ 4.7 kB/s ⇒ 41 %（可用）。
 *   🛑 ASCII-2 大行数档会超带宽，本层不负责，见 T198 §5。
 * ==========================================================================*/

#include <stdint.h>

/* ---- 通道编号 ------------------------------------------------------------ */
#define ECG_COMM_PORT_USB    0   /* USB OTG FS（CDC 虚拟串口，历史基线） */
#define ECG_COMM_PORT_UART5  1   /* UART5 @ PC12/PD2（RS485 通路）      */

/* ---- ★ 唯一开关：改这一行即在两套通道之间切换 ---------------------------- */
/* 🛑 必须落在**源码**里，不要写进 eide.yml 的 defineList —— 改了 yml 而 EIDE 未重新
 *    生成 builder.params 时，构建会**静默沿用旧宏**（本项目已栽过一次，见技能 §2）。 */
#ifndef ECG_COMM_PORT
#define ECG_COMM_PORT   ECG_COMM_PORT_UART5
#endif

/* ---- 编译期断言：拼错的宏名在 #if 里按 0 处理，会静默落到 USB 档 ---------- */
#if (ECG_COMM_PORT != ECG_COMM_PORT_USB) && (ECG_COMM_PORT != ECG_COMM_PORT_UART5)
#error "ECG_COMM_PORT 只能是 ECG_COMM_PORT_USB(0) 或 ECG_COMM_PORT_UART5(1)"
#endif

/* ---- 开机横幅（一次，非周期心跳）---------------------------------------- */
/* 用途：上板第一步用它证 UART5_TX 通路（焊桥/电平/接线）通不通 ——
 *       打开串口终端按一下复位键就该看见这一行，看不到就是 TX 侧没通。
 * 上位机侧安全：`Core/Protocol.cs` 的解析器按 `AA 55` 重同步、旁路字节单独计数
 * ⇒ 混入 ASCII 文本是它的常态（运行期命令回显本来就是 ASCII）。
 * ⇒ 联调确认通路后可置 0，让线上只有二进制帧。 */
#ifndef ECG_UART5_BOOT_BANNER
#define ECG_UART5_BOOT_BANNER   1
#endif

/* ---- API ----------------------------------------------------------------- */

/**
 * @brief  初始化所选通道。必须在任何 `comm_send()` 之前调用一次。
 * @note   UART5 模式下：开 UART5 时钟、配 PC12/PD2 为 AF8、115200 8N1、
 *         使能 UART5 中断（优先级**低于** DRDY 的 EXTI1，后者是 0）；
 *         并使能 IDLE 接收（`HAL_UARTEx_ReceiveToIdle_IT`）。
 *         USB 模式下本函数为空 —— USB 由 `MX_USB_DEVICE_Init()` 负责。
 */
void comm_init(void);

/**
 * @brief  发送一帧（**内部拷贝**，调用方缓冲在函数返回后即可复用）。
 * @param  buf  数据指针，可为栈局部
 * @param  len  字节数
 * @retval 0 = 已受理；非 0 = 丢弃（上一帧未发完 / 超长 / 外设报错）
 * @note   🛑 之所以"内部拷贝"而不是"把指针交给外设"：
 *         `CDC_Transmit_FS` 与 `HAL_UART_Transmit_IT` 都**异步**取数据，
 *         函数返回时数据还没发完。历史上 `ads1293.c` 的主通路缓冲是**栈局部**
 *         `char buff[512]`，直接交指针会让外设读到已复用的栈（技能 §4.48/§4.126）。
 *         拷贝成本 59~500 B，相对 5.12 ms 的占线时间可忽略。
 */
uint8_t comm_send(const uint8_t *buf, uint16_t len);

/**
 * @brief  上一帧是否仍在发送中。给"取数据前先问忙不忙"的调用点预留。
 * @note   UART5 模式下有效；USB 模式下恒返回 0（由 CDC 自己回 BUSY）。
 */
uint8_t comm_tx_busy(void);

/* ---- 记账（ST-LINK watch 可读，无需上位机） ------------------------------- */
extern volatile uint32_t comm_tx_frames;   /* 成功受理的帧数 */
extern volatile uint32_t comm_tx_drops;    /* 丢弃的帧数（>0 说明带宽或打点太密） */
extern volatile uint32_t comm_rx_events;   /* UART5：IDLE/满包事件数（命令到达次数） */
extern volatile uint32_t comm_rx_loss;     /* UART5：溢出或重挂失败次数 */

#endif /* __COMM_PORT_H__ */
