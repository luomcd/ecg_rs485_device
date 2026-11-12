/* ============================================================================
 * comm_port.c —— 上位机通信通道抽象层实现（T198 / 2026-11-03）
 * 头文件里有完整的背景、硬件通路与速率账，这里只写实现要点。
 * ==========================================================================*/

#include "main.h"
#include "comm_port.h"

/* ---- 记账变量（两种模式都有，ST-LINK watch 可读） ------------------------- */
volatile uint32_t comm_tx_frames = 0;
volatile uint32_t comm_tx_drops  = 0;
volatile uint32_t comm_rx_events = 0;
volatile uint32_t comm_rx_loss   = 0;


/* ==========================================================================
 * 模式 A：USB OTG FS（CDC 虚拟串口）—— 历史基线，零行为变化
 * ==========================================================================*/
#if (ECG_COMM_PORT == ECG_COMM_PORT_USB)

#include "usbd_cdc_if.h"

void comm_init(void)
{
    /* USB 由 main.c 的 MX_USB_DEVICE_Init() 负责；本层无可做之事。 */
}

uint8_t comm_send(const uint8_t *buf, uint16_t len)
{
    /* 🛑 不在此处拷贝：CDC 的调用点历史语义就是"把栈缓冲指针交出去"，
     *    且 `printf_msg()` 的 `HAL_Delay(5)` 保证了发送窗口远小于 50 ms 帧间隔。
     *    改成本层拷贝反而会在 USB 模式下多一份 512 B 静态 RAM。 */
    uint8_t r = CDC_Transmit_FS((uint8_t *)buf, len);
    if (r == USBD_OK) {
        comm_tx_frames++;
    } else {
        comm_tx_drops++;
    }
    return r;
}

uint8_t comm_tx_busy(void)
{
    return 0;   /* USB 侧忙不忙由 CDC_Transmit_FS 的返回值表达 */
}


/* ==========================================================================
 * 模式 B：UART5 @ PC12(TX) / PD2(RX)，AF8，115200 8N1
 * ==========================================================================*/
#else

/* ---- 缓冲尺寸 ------------------------------------------------------------ */
/* TX 576 B：覆盖最长一帧 —— ASCII-2 的 `buff[ASCII2_BUFF_LEN=512]` 上界 511 B。
 * 二进制档只有 59 B（ch2 双通道 109 B）。留 64 B 余量。 */
#define COMM_TX_MAX   576u
/* RX 128 B：下行命令实测最大 —— 二进制快照请求 6+? B、ASCII 命令含 CRLF 约 20 B。
 * 上位机 `SerialPort.Write` 整帧一次写出 ⇒ 一次 IDLE 事件收全整帧。 */
#define COMM_RX_MAX   128u

UART_HandleTypeDef huart5;

static uint8_t           s_tx_buf[COMM_TX_MAX];
static volatile uint8_t  s_tx_busy = 0;

/* RX 收落点（外设直接写这里） + 发布区（主循环读这里）
 * 🛑 必须两级：`VCOM_RxBuffer` 会在**下一次事件**到来时被覆盖，若发布区就是收落点，
 *    主循环正读到一半就会被撕。乒乓两片让"读到一半又来了命令"退化为读到旧命令。 */
static uint8_t           s_rx_work[COMM_RX_MAX];
static uint8_t           s_rx_pub[2][COMM_RX_MAX];
static volatile uint8_t  s_rx_pub_idx = 0;

/* 接收侧既有接口（`vcomcmd.c:110`）：只存指针/长度，不做拷贝。 */
extern void vcom_recv(uint8_t *buf, uint32_t *len);


/* ---- MSP：UART5 的时钟与引脚 -------------------------------------------- */
/* CubeMX 从未生成过 UART 的 MSP（`.ioc` 里 `Mcu.IPNb=7` 无任何 UART）⇒ 本函数
 * 在整个工程里**原本不存在**，在此首次定义，不会与 `stm32l4xx_hal_msp.c` 冲突。 */
void HAL_UART_MspInit(UART_HandleTypeDef *huart)
{
    GPIO_InitTypeDef g = {0};

    if (huart->Instance != UART5) {
        return;
    }

    __HAL_RCC_UART5_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();   /* PC12 = TX（MX_GPIO_Init 已开，重复开无副作用） */
    __HAL_RCC_GPIOD_CLK_ENABLE();   /* PD2  = RX（MX_GPIO_Init **未**开，必须在此开） */

    /* PC12 → UART5_TX，AF8。推挽输出，无上下拉。
     * ★T245（2026-11-12）已实测完边沿档位对坏帧率的影响（结论留档，别重复做）：
     *     `LOW`       → 6.32%（拐点：边沿一慢，坏点从"帧首+3/帧尾−2"扩散到 +8~+23 一大片）
     *     `HIGH`      → 1.12%（原设置）
     *     `VERY_HIGH` → 1.01%（与 HIGH 持平 ⇒ **加档没有收益**）
     * ⇒ 链路卡在**物理层余量边缘**：慢边沿直接崩，快边沿不再改善
     *   ⇒ 瓶颈不在 STM32 的驱动能力（否则加档会变好），而在线材/终端/共模。
     * 🛑 保持 `HIGH`：它是"够快"与"不过冲"的既有平衡点，且上板历史数据都基于它。 */
    g.Pin       = GPIO_PIN_12;
    g.Mode      = GPIO_MODE_AF_PP;
    g.Pull      = GPIO_NOPULL;
    g.Speed     = GPIO_SPEED_FREQ_HIGH;
    g.Alternate = GPIO_AF8_UART5;
    HAL_GPIO_Init(GPIOC, &g);

    /* PD2 → UART5_RX，AF8。
     * ★T202(2026-11-03)：Pull 必须 **NOPULL**，不能用 PULLUP。
     *
     * 实测（调试器直读 GPIOD 寄存器）：同样把 PD2 配成 AF8、
     *   PULLUP ⇒ IDR.bit2 = 0   （UART 永远读到持续低电平，一个字节都收不到）
     *   NOPULL ⇒ IDR.bit2 = 1   （正常空闲高）
     * 原因：模组内部 `RXD-USB_DP ≡ PA12`（USB D+）与本脚同网络，PA12 的 ESD 二极管以
     * `VDDUSB` 为参考端；40 kΩ 内部上拉与它凑出一个 ~0.7 V 的分压点，刚好低于
     * STM32 输入高阈值（0.4×1.8 V = 0.72 V）⇒ 被误判为低。
     * 外部线路本身是高阻（断电量 CN9.2 对 GND > 3 MΩ），并不是外部下拉造成的。
     *
     * 参照工程 `stm32ch3020demo-iar`（CubeMX 生成、同 UART5/同 PD2/AF8）用的正是
     * `GPIO_NOPULL`，能正常收发 —— 与本结论一致。
     * 🛑 别再加回上拉：那会让 RX 在空闲时停在 0.7 V，整条接收链路失效。 */
    g.Pin       = GPIO_PIN_2;
    g.Mode      = GPIO_MODE_AF_PP;
    g.Pull      = GPIO_NOPULL;
    g.Speed     = GPIO_SPEED_FREQ_HIGH;
    g.Alternate = GPIO_AF8_UART5;
    HAL_GPIO_Init(GPIOD, &g);

    /* 🛑 优先级必须**低于** DRDY（`EXTI1_IRQn` 是 0，见 `main.c:289`）。
     *    给 1：UART 中断打不断 DRDY 采样，反之可以。若给 0 同级，串口长帧会推迟
     *    DRDY 响应 ⇒ 采样时刻抖动 ⇒ 时间轴上的 dt 列出现假跳变。 */
    HAL_NVIC_SetPriority(UART5_IRQn, 1, 0);
    HAL_NVIC_EnableIRQ(UART5_IRQn);
}


/* ---- 初始化 ------------------------------------------------------------- */
void comm_init(void)
{
    huart5.Instance                    = UART5;
    /* ★T246（2026-11-12）**临时实验**：115200 → 57600，测"降波特率能不能把残余坏帧压到 0"。
     * 依据：T244/T245 已定死是物理层余量边缘（终端关掉后 1.12%→0.037%），
     *   降速把位宽从 8.68 µs 加倍到 17.4 µs ⇒ 采样窗口余量翻倍。
     * 带宽账：帧长 109 B × 20 fps = 2.18 kB/s；57600 8N1 = 5.76 kB/s ⇒ 占用 **37.8%**
     *   （115200 时 18.9%）⇒ 仍有一倍余量，**帧率与波形内容不受影响**（帧率只由 500/25 决定）。
     *   ⚠ 单帧发送耗时 9.5 ms → 18.9 ms（占 50 ms 帧周期的 38%），显示延迟 +9.5 ms，人眼不可见。
     * 🛑 实验完改回 115200，并把 banner 字符串一起改回（易漏）。 */
    huart5.Init.BaudRate               = 57600;
    huart5.Init.WordLength             = UART_WORDLENGTH_8B;
    huart5.Init.StopBits               = UART_STOPBITS_1;
    huart5.Init.Parity                 = UART_PARITY_NONE;
    huart5.Init.Mode                   = UART_MODE_TX_RX;
    huart5.Init.HwFlowCtl              = UART_HWCONTROL_NONE;
    huart5.Init.OverSampling           = UART_OVERSAMPLING_16;
    huart5.Init.OneBitSampling         = UART_ONE_BIT_SAMPLE_DISABLE;
    huart5.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;

    if (HAL_UART_Init(&huart5) != HAL_OK) {
        comm_rx_loss++;      /* 初始化失败：后续 receive 会一直失败，计数器留痕 */
        return;
    }

    /* 开 IDLE 接收。用 IDLE 而不是"逐字节 IT + 自己拼帧"：
     * 上位机 `SerialPort.Write()` 是**整帧一次写出** ⇒ 一次 IDLE = 一条完整命令，
     * 语义与 USB CDC 的"一包一次回调"完全一致 ⇒ `vcom_recv_analysis()` 零改动。 */
    (void)HAL_UARTEx_ReceiveToIdle_IT(&huart5, s_rx_work, (uint16_t)COMM_RX_MAX);

#if (ECG_UART5_BOOT_BANNER != 0)
    /* 一次性横幅（非周期心跳）：上板第一步就靠它证 TX 通路。
     * ⚠ 放在 init 末尾 —— 此时 s_tx_busy=0、中断已开，可以安全走 comm_send。 */
    {
        static const char banner[] =
            "\r\n[uart5] ready 57600 8N1  PC12=TX PD2=RX\r\n";
        (void)comm_send((const uint8_t *)banner, (uint16_t)(sizeof(banner) - 1u));
    }
#endif
}


/* ---- 发送 --------------------------------------------------------------- */
uint8_t comm_send(const uint8_t *buf, uint16_t len)
{
    if ((buf == NULL) || (len == 0u) || (len > COMM_TX_MAX)) {
        comm_tx_drops++;
        return 1u;
    }

    /* 🛑 忙判必须在**拷贝之前**：若先拷再判，这一帧的字节已经把上一帧的在途缓冲
     *    覆盖掉了一半，判出来"忙"也没意义了（技能 §4.129 同款陷阱）。 */
    if (s_tx_busy != 0u) {
        comm_tx_drops++;
        return 1u;
    }

    for (uint16_t i = 0u; i < len; i++) {
        s_tx_buf[i] = buf[i];
    }

    s_tx_busy = 1u;
    if (HAL_UART_Transmit_IT(&huart5, s_tx_buf, len) != HAL_OK) {
        s_tx_busy = 0u;
        comm_tx_drops++;
        return 1u;
    }

    comm_tx_frames++;
    return 0u;
}

uint8_t comm_tx_busy(void)
{
    return s_tx_busy;
}


/* ---- 回调 --------------------------------------------------------------- */

/* HAL 保证：TxCplt 时不持有任何在途引用 ⇒ 此刻 s_tx_buf 才可复用。 */
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == UART5) {
        s_tx_busy = 0u;
    }
}

/* IDLE（部分收）与"缓冲收满"两条路径都会进这里 —— 见 `hal_uart.c:2592` / `:2524`。
 * ⚠ 进来时 HAL 已把 `RxState` 复位成 READY（`hal_uart.c:2575`）⇒ 可在回调内重挂。 */
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size)
{
    if (huart->Instance != UART5) {
        return;
    }

    if ((Size > 0u) && (Size <= COMM_RX_MAX)) {
        uint8_t idx = (uint8_t)(s_rx_pub_idx ^ 1u);
        uint32_t len = (uint32_t)Size;

        for (uint16_t i = 0u; i < Size; i++) {
            s_rx_pub[idx][i] = s_rx_work[i];
        }
        s_rx_pub_idx = idx;

        /* 复用既有入口：它只做 `VCOM_RxData=1; VCOM_RxBuffer=buf; VCOM_RxLength=len;`
         * ⇒ 与 USB 模式走的是**同一条**命令解析链，`vcomcmd.c` 零改动。 */
        vcom_recv(s_rx_pub[idx], &len);
        comm_rx_events++;
    }

    /* 重挂接收。失败（例如上一次还没释放）就记账 —— 不静默。 */
    if (HAL_UARTEx_ReceiveToIdle_IT(&huart5, s_rx_work, (uint16_t)COMM_RX_MAX) != HAL_OK) {
        comm_rx_loss++;
    }
}

/* ★T201(2026-11-03)：错误恢复 —— 上板实测暴露的真缺陷。
 *
 * HAL 的处理顺序是：先 `UART_EndRxTransfer()`（`hal_uart.c` 里只要 errorcode 含
 * NE|FE|ORE 就终止接收），**再**调本回调。若不在这里重挂，一次噪声、一个 break
 * 就让接收永久失聪。板上第一次实测的现象就是：TX 完全正常（横幅逐字正确），
 * 但 `comm_rx_events` 恒 0、`test notch` 无任何回显。
 *
 * 触发场景很常见：适配器 TXD 在串口未打开 / DTR 未置位时输出低电平，STM32 侧
 * 持续读到 break ⇒ FE ⇒ 接收终止。这是**必须容忍**的工况，不是异常。 */
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance != UART5) {
        return;
    }

    comm_rx_loss++;

    /* 清掉累计错误标志：不清的话重挂后第一条中断就又被同一个标志打回。 */
    __HAL_UART_CLEAR_FLAG(huart, UART_CLEAR_OREF | UART_CLEAR_FEF |
                                UART_CLEAR_NEF  | UART_CLEAR_PEF);

    /* `UART_EndRxTransfer()` 正常会把 RxState 复位成 READY；但不同 HAL 修订版在
     * ORE 分支上行为不完全一致，这里强制拉回，避免重挂被 `HAL_BUSY` 拒掉。 */
    huart->RxState = HAL_UART_STATE_READY;

    if (HAL_UARTEx_ReceiveToIdle_IT(&huart5, s_rx_work, (uint16_t)COMM_RX_MAX) != HAL_OK) {
        comm_rx_loss++;
    }
}

#endif /* ECG_COMM_PORT */
