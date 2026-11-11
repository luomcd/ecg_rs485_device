#ifndef _QUEUE_H_
#define _QUEUE_H_

#ifdef __cplusplus
extern "C" {
#endif

#include "stdarg.h"
#include "stdlib.h"
#include "stdint.h"
#include "stdio.h"
#include "stdbool.h"   
    
#define TI_ADS1293_CONFIG_REG                         (0x00)                   /* Main Configuration */

#define TI_ADS1293_FLEX_CH1_CN_REG                    (0x01)                   /* Flex Routing Swich Control for Channel 1 */
#define TI_ADS1293_FLEX_CH2_CN_REG                    (0x02)                   /* Flex Routing Swich Control for Channel 2 */
#define TI_ADS1293_FLEX_CH3_CN_REG                    (0x03)                   /* Flex Routing Swich Control for Channel 3 */
#define TI_ADS1293_FLEX_PACE_CN_REG                   (0x04)                   /* Flex Routing Swich Control for Pace Channel */
#define TI_ADS1293_FLEX_VBAT_CN_REG                   (0x05)                   /* Flex Routing Swich Control for Battery Monitoriing */

#define TI_ADS1293_LOD_CN_REG                         (0x06)                   /* Lead Off Detect Control */
#define TI_ADS1293_LOD_EN_REG                         (0x07)                   /* Lead Off Detect Enable */
#define TI_ADS1293_LOD_CURRENT_REG                    (0x08)                   /* Lead Off Detect Current */
#define TI_ADS1293_LOD_AC_CN_REG                      (0x09)                   /* AC Lead Off Detect Current */

#define TI_ADS1293_CMDET_EN_REG                       (0x0A)                   /* Common Mode Detect Enable */
#define TI_ADS1293_CMDET_CN_REG                       (0x0B)                   /* Commond Mode Detect Control */
#define TI_ADS1293_RLD_CN_REG                         (0x0C)                   /* Right Leg Drive Control */

#define TI_ADS1293_WILSON_EN1_REG                     (0x0D)                   /* Wilson Reference Input one Selection */
#define TI_ADS1293_WILSON_EN2_REG                     (0x0E)                   /* Wilson Reference Input two Selection */
#define TI_ADS1293_WILSON_EN3_REG                     (0x0F)                   /* Wilson Reference Input three Selection */
#define TI_ADS1293_WILSON_CN_REG                      (0x10)                   /* Wilson Reference Input Control */

#define TI_ADS1293_REF_CN_REG                         (0x11)                   /* Internal Reference Voltage Control */

#define TI_ADS1293_OSC_CN_REG                         (0x12)                   /* Clock Source and Output Clock Control */

#define TI_ADS1293_AFE_RES_REG                        (0x13)                   /* Analog Front-End Frequency and Resolution */
#define TI_ADS1293_AFE_SHDN_CN_REG                    (0x14)                   /* Analog Front-End Shutdown Control */
#define TI_ADS1293_AFE_FAULT_CN_REG                   (0x15)                   /* Analog Front-End Fault Detection Control */
#define TI_ADS1293_AFE_DITHER_EN_REG                  (0x16)                   /* Enable Dithering in Signma-Delta */
#define TI_ADS1293_AFE_PACE_CN_REG                    (0x17)                   /* Analog Pace Channel Output Routing Control */

#define TI_ADS1293_ERROR_LOD_REG                      (0x18)                   /* Lead Off Detect Error Status */
#define TI_ADS1293_ERROR_STATUS_REG                   (0x19)                   /* Other Error Status */
#define TI_ADS1293_ERROR_RANGE1_REG                   (0x1A)                   /* Channel 1 Amplifier Out of Range Status */
#define TI_ADS1293_ERROR_RANGE2_REG                   (0x1B)                   /* Channel 1 Amplifier Out of Range Status */
#define TI_ADS1293_ERROR_RANGE3_REG                   (0x1C)                   /* Channel 1 Amplifier Out of Range Status */
#define TI_ADS1293_ERROR_SYNC_REG                     (0x1D)                   /* Synchronization Error */


#define TI_ADS1293_R2_RATE_REG                        (0x21)                   /* R2 Decimation Rate */
#define TI_ADS1293_R3_RATE1_REG                       (0x22)                   /* R3 Decimation Rate for Channel 1 */
#define TI_ADS1293_R3_RATE2_REG                       (0x23)                   /* R3 Decimation Rate for Channel 2 */
#define TI_ADS1293_R3_RATE3_REG                       (0x24)                   /* R3 Decimation Rate for Channel 3 */
#define TI_ADS1293_P_DRATE_REG                        (0x25)                   /* 2x Pace Data Rate */
#define TI_ADS1293_DIS_EFILTER_REG                    (0x26)                   /* ECG Filter Disable */
#define TI_ADS1293_DRDYB_SRC_REG                      (0x27)                   /* Data Ready Pin Source */
#define TI_ADS1293_SYNCOUTB_SRC_REG                   (0x28)                   /* Sync Out Pin Source */
#define TI_ADS1293_MASK_DRDYB_REG                     (0x29)                   /* Optional Mask Control for DRDYB Output */
#define TI_ADS1293_MASK_ERR_REG                       (0x2A)                   /* Mask Error on ALARMB Pin */

#define TI_ADS1293_ALARM_FILTER_REG                   (0x2E)                   /* Digital Filter for Analog Alarm Signals */
#define TI_ADS1293_CH_CNFG_REG                        (0x2F)                   /* Configure Channel for Loop Read Back Mode */

#define TI_ADS1293_DATA_STATUS_REG                    (0x30)                   /* ECG and Pace Data Ready Status */
#define TI_ADS1293_DATA_CH1_PACE_H_REG                (0x31)                   /* Channel1 Pace Data High [15:8] */
#define TI_ADS1293_DATA_CH1_PACE_L_REG                (0x32)                   /* Channel1 Pace Data Low [7:0] */
#define TI_ADS1293_DATA_CH2_PACE_H_REG                (0x33)                   /* Channel2 Pace Data High [15:8] */
#define TI_ADS1293_DATA_CH2_PACE_L_REG                (0x34)                   /* Channel2 Pace Data Low [7:0] */
#define TI_ADS1293_DATA_CH3_PACE_H_REG                (0x35)                   /* Channel3 Pace Data High [15:8] */
#define TI_ADS1293_DATA_CH3_PACE_L_REG                (0x36)                   /* Channel3 Pace Data Low [7:0] */
#define TI_ADS1293_DATA_CH1_ECG_H_REG                 (0x37)                   /* Channel1 ECG Data High [23:16] */
#define TI_ADS1293_DATA_CH1_ECG_M_REG                 (0x38)                   /* Channel1 ECG Data Medium [15:8] */
#define TI_ADS1293_DATA_CH1_ECG_L_REG                 (0x39)                   /* Channel1 ECG Data Low [7:0] */
#define TI_ADS1293_DATA_CH2_ECG_H_REG                 (0x3A)                   /* Channel2 ECG Data High [23:16] */
#define TI_ADS1293_DATA_CH2_ECG_M_REG                 (0x3B)                   /* Channel2 ECG Data Medium [15:8] */
#define TI_ADS1293_DATA_CH2_ECG_L_REG                 (0x3C)                   /* Channel2 ECG Data Low [7:0] */
#define TI_ADS1293_DATA_CH3_ECG_H_REG                 (0x3D)                   /* Channel3 ECG Data High [23:16] */
#define TI_ADS1293_DATA_CH3_ECG_M_REG                 (0x3E)                   /* Channel3 ECG Data Medium [15:8] */
#define TI_ADS1293_DATA_CH3_ECG_L_REG                 (0x3F)                   /* Channel3 ECG Data Low [7:0] */

#define TI_ADS1293_REVID_REG                          (0x40)                   /* Revision ID */
#define TI_ADS1293_DATA_LOOP_REG                      (0x50)                   /* Loop Read Back Address */


// Useful definitions
#define ADS1293_READ_BIT                              (0x80)
#define ADS1293_WRITE_BIT                             (0x7F)




#if 0
typedef union {
	struct {
		uint32_t val:24;
		uint32_t:8;
	};
	uint8_t  rawbytes[4];
	uint32_t ecgraw;
        
} ecg_data_t;
#else
typedef struct {
    uint8_t  lod;
	uint8_t  errsta;
	int32_t  ecgraw;
    uint32_t idx;
    int32_t  ecgraw2;   /* ★G8：CH2 的 24-bit 原始码（本步只写不读；G9 起参与输出） */
} ecg_data_t;

#endif


typedef struct {
        uint32_t idxraw;
        uint16_t dtcycles;
        int32_t  ecgraw;
        uint32_t idxfilter;
        int16_t  dsrateraw;
        int32_t  ecgraw2;   /* ★G8：CH2 原始码。★**必须放在 double 之前**——此处正是编译器
                             *   既有的 6 B 对齐空洞（offset 18-23），占用后 sizeof 仍 **32 B**；
                             *   若追加到结构末尾，会被 double 顶到 offset 32 ⇒ sizeof 40 B
                             *   ⇒ 队列 +200 B（本步认可的账是 +100 B）。 */
        double   filterraw;
        
} ecg_filter_t;

typedef union {
    float    f32;
    uint32_t u32;
    uint8_t  u8[4];
} f32_le_t;

void ads1293_config_test(void);
void ads1293_autoinc_test(void);
void ads1293_readregs_test(void);
void ads1293_afe_test(void);
void ads1293_conversion(bool bstart);
void ads1293_raw_enqueue(uint32_t idx);
void ads1293_raw_queueinit(void);
void ads1293_raw_popoutput(void);

/* ---- 853 原始流输出开关（2026-09-20 拍板；命令 `test raw853 [on|off]`）---------
 * on : 每个 853 样本直接入队（输出行率 500 → 853.2423 Hz）；
 * off: 恢复 500 Hz 重采样输出，并复位重采样器相位（上电默认 off）。
 * 打开后的纪律见 vcomcmd.c 中 `test raw853` 处的注释块（时间轴 / 上位机 / 列义 / 丢包判据）。 */
void     ads1293_set_raw853(uint32_t on);
uint32_t ads1293_raw853_is_on(void);

/* ---- 双通道(CH1+CH2)输出开关（★G8 2026-09-23；命令 `test ch2 [on|off]`）--------
 * on : 二进制显示帧 59 B → **109 B**，载荷 `[CH1×N][CH2×N]` **块布局**，且 CH2 ≡ CH1；
 * off: 恢复单通道（上电默认）——逐字节等同 G7 定稿，是**回归基线**。
 * 只影响**二进制**帧长；ASCII 档的 CH2 走列⑦（`ecgraw2` 原始码），帧长不变。
 * 🛑 on 期间上位机（按 59 B 定长解析）必然错乱 ⇒ 必须先用串口助手抓包验收。
 * STA bit2（channel 0=I／1=I&II）随本开关联动，见 ads1293_status_byte()。 */
void     ads1293_set_ch2(uint32_t on);
uint32_t ads1293_ch2_is_on(void);
uint8_t  ads1293_status_byte(void);

/* ---- ★T232（2026-11-11）LOD 锁存状态机 ----------------------------------
 * 手册 §8.3.24：0x18~0x1E 的错误位是「锁存 + 读后清零」⇒ 逐样本读会把锁存吃掉。
 * 因此改为低频轮询 + 软件锁存 + 非对称防抖。
 *   ads1293_lod_poll()   主循环每轮调用（内部 20 ms 自节流）
 *   ads1293_lod_stable() 防抖后的脱落位：bit0=IN1 / bit1=IN2 / bit2=IN3
 *   ads1293_lod_raw()    最近一次原始读值（诊断用）
 * 对外语义（STATUS.bit4）= **任意输入脱落**（完整性提示），不等于"QRS 通道无效"。 */
void    ads1293_lod_poll(void);
uint8_t ads1293_lod_stable(void);
uint8_t ads1293_lod_raw(void);

/* ---- ★T163（2026-09-29）ASCII 精简档 `ascii-2`（命令 `test ascii-2`）----------
 * 依据猛哥 2026-09-29 对 T162 §八 五问的拍板（α 方案 + ③⑥改开关 + ①默认不输出
 * + 批次用命令调 + ⑤改定点）。`ascii-1`（7 列）**一字不改** ⇒ 41 个「7 列」硬门
 * 脚本与全部历史报告口径零回归。
 *
 * 列序恒为 **[①] ② [③] ⑤ [⑥]**（与 ascii-1 同相对顺序），关掉的列整列消失。
 *   · ② `ecgraw`   `%08d`（链前原始码，必出）
 *   · ⑤ 链后值     `%08d` = **0.1 code 定点整数**（必出；⚠ 2026-09-30 订正：原写「0.1 µV」**标签错**，
 *                  差 8.9×，见 `ads1293.c` 同款订正段。`filterraw` 是 **code 域**
 *                  ⇒ `µV = 本列 ÷ 10 × 0.1122`；实测极值 ±282,502 code ＝ 本列 ±2,825,022，
 *                  钳在 ±9,999,999 ⇒ 字段恒 8 字符，余量 3.54×）
 *   · ① `idxraw`   `%06u`（853 域丢包判据，**默认关**）
 *   · ③ `idxfilter` `%06d`（500 域丢包判据，**默认关**）
 *   · ⑥ `dtcycles` `%05u`（CPU 占用唯一实测手段，**默认关**）
 * 默认（三开关全关）= `\n%08d %08d` = **18 B 恒定**（原 7 列 45 B ⇒ −60%）。
 *
 * ★ **序号取 6 位口径**（猛哥 2026-09-29 补充拍板）：本档**仅用于测试、不用于产品**，
 *   测试都是**短时间抓取** ⇒ 序号不需要 8 位。容量可算、且两个序号都随 START 复位：
 *     · 列① 6 位 = 999,999 / 853.2423 Hz ≈ **19.5 min**（`_ecgrawidx` 随 START 清零）
 *     · 列③ 6 位 = 999,999 / 500 Hz      ≈ **33.3 min**（`ecgfilteridx` 随 START 清零）
 *   ⇒ 设计工况内字段宽度恒定；超时才会变宽，由运行期护栏兜底（属超出本档用途）。
 *
 * 🛑 批次 `_asciipopcnt` 受**双重钳制**：≤ 24（队列容量 25 − 1，因 `enqueue()` 满时
 *   会覆盖最旧项）且 ≤ `(512-1)/行上界`（默认 19⇒24、单开 26⇒19、双开 33⇒15、
 *   全开 40⇒12）。设定值可能被下调，**调用方必须回显
 *   `ads1293_set_ascii2_popcnt()` 的返回值**（实际生效值，不是入参）。
 * ⚠ 本档**不改变 `_popoutcnt`**：它锁着二进制帧长（猛哥 2026-09-20 明确）。 */
uint32_t ads1293_dispver(void);
void     ads1293_set_dispver(uint32_t ver);          /* 1 = ascii-1；2 = ascii-2 */
uint32_t ads1293_ascii2_rowmax(void);                /* 单行字节**上界**（默认 19；24 是 popcap 不是它） ★T163c 注释订正 */
uint32_t ads1293_ascii2_linesz(void);                /* 单行字节**真值**（随开关变；默认 18） ★T163c 新增 */
uint32_t ads1293_ascii2_popcap(void);                /* 批次合法上界 */
uint32_t ads1293_ascii2_popcnt(void);                /* 当前批次 */
uint32_t ads1293_set_ascii2_popcnt(uint32_t n);      /* 返回**实际生效值** */
uint32_t ads1293_set_ascii2_col(uint32_t which, uint32_t on); /* 1/2/3；非法⇒0xFFFFFFFF */
uint32_t ads1293_ascii2_col(uint32_t which);

/* ---- 寄存器快照帧（协议 v2.2 §3.2，Type 0x03；★G3 2026-09-23 落码）------------
 * ads1293_snapshot()      : 读表内 36 条寄存器 ⇒ (REG,VAL)×N（按 REG 升序，不含帧头）
 *                           items 至少 2×36=72 B；返回 0 / -1(SPI 失败)
 * ads1293_send_snapshot() : 上面 + 帧头/CRC ⇒ CDC 上行一整帧（COUNT=36 ⇒ **80 B**）
 *                           reqid = 触发它的下行 CMD（ASCII 调试用 0x00）
 *                           返回 0 已发／-1 未发／1 CDC 忙
 * 🛑 协议规定 0x03 帧只在 STOP 态发；状态门由调用方加（ASCII 通道不加、G4 必须加）。 */
int ads1293_snapshot(uint8_t *items, uint8_t *count);
int ads1293_send_snapshot(uint8_t reqid, uint8_t *count);

/* ---- 写表 / RESET（协议 v2.2 §5.1；★G5 2026-09-23 落码）-------------------------
 * ads1293_write_table() : 按表写 + **逐条回读校验**，🛑 不含任何 delay / 回显
 *                         （对比 ads1293_afe_test() 的 ≈236 ms —— 那几乎全是人为拖的）
 *                         返回 0 = 全一致；>0 = 首个不一致项下标+1
 * ads1293_reset_afe()   : RESET 的原子序列 = 停流/STANDBY → 清两队列 → 滤波链复位
 *                         → 写表 + 回读校验 → **停在 STOP** → 上行 0x03 回执
 *                         （0x03 快照只在写表成功时发 ⇒ 失败时上位机等 1.0 s 超时）
 *                         🛑 **不加 RUN 状态门**（与 0x10 口径相反）；返回同上。
 * 两者共用：G5 的 `0x03 RESET` 与 G6 的 `0x01 START`（§5b 的 ads1293_start）。 */
int ads1293_write_table(const uint8_t t[][2], uint32_t n);

/* ★T170-A（2026-10-08）：上一次 `ads1293_write_table()` **失败项**的详情（只读、诊断用）。
 *   reg 失败项寄存器 ｜ want 期望值 ｜ got 实读值
 *     （got：0xFD = 写失败、0xFE = 读失败、0xFF = 从未失败过）
 *   🛑 **只用于诊断回显，不得参与任何控制流**（T170 §5-A）。 */
uint8_t ads1293_wt_fail_reg(void);
uint8_t ads1293_wt_fail_want(void);
uint8_t ads1293_wt_fail_got(void);
int ads1293_reset_afe(void);

/* ★T207(2026-11-04)：**上电写默认表**（由 `demo_ads1293()` 调一次，唯一调用点）。
 *   动机：ADS1293 不吃 MCU 的 NRST（`main.h` 无 RESET 脚）⇒ 只有整板断电才回 POR
 *   ⇒ 旧固件上电寄存器恒≠金标 ⇒ 上位机 H 组硬闸恒 false ⇒「必须先同步才能 START」。
 *   行为：停流/STANDBY → 写全表 36 条 + 逐条回读 → **停在 STOP**；
 *         🛑 **不发 0x03 快照**（上电阶段无命令来源，多一帧＝未登记 ACK）。
 *   返回：0 = 全一致；>0 = 首个不一致项下标+1（详情 `ads1293_wt_fail_*()`）。
 *   ⚠ 与 `ads1293_start()` **互不依赖** —— START 仍自带写全表。 */
#ifndef ECG_BOOT_AFE_INIT
/* ★T207 开关：1 = 上电写默认表（默认）；0 = 回到旧行为（阴性对照用，只需改这一行）。 */
#define ECG_BOOT_AFE_INIT 1
#endif
int ads1293_boot_init(void);


/* ---- SET_REGS（协议 v2.2 §5.1 下行 0x11；★G10 2026-09-23 落码）--------------------
 * ads1293_set_regs() : 按 `(REG,VAL)×N` **逐条写 + 逐条回读校验**（复用 G5 写表内核纪律）。
 *   🛑 **白名单预扫在写第一条之前** ⇒ 被拒时**零副作用**（不会出现"前 k 条已落、
 *      第 k+1 条被拒"的半改状态，否则芯片处于未知配置且上位机收不到回执）。
 *   🛑 **无 printf_msg / 无 HAL_Delay**（命令路径铁律：printf_msg 内含 HAL_Delay(5)，
 *      而 DRDY 周期 1172 µs ⇒ 一次回显吞掉 4.27 个样本，见 vcomcmd.c 的 0x03 段注释）。
 *   🛑 **不自加状态门** —— 「仅 STOP」的门在**分派层**（口径同 0x03/0x10 的分工）。
 *   成功 ⇒ 上行 `0x03` 快照帧（REQID 回填 `0x11`，即协议 §5.1 给 0x11 的隐式 ACK）；
 *   失败 ⇒ **不发快照** ⇒ 上位机等满 1.0 s 超时（与 0x10 的 RUN 态门**同一种可识别表现**）。
 * 返回：0 = 全部写入且回读一致；>0 = 首个问题项 1-based 下标（被白名单拒 / 写失败 / 回读不一致）。
 * ⚠ `items` 的 volatile 限定只因调用方的源缓冲是 CDC 接收缓冲（`volatile uint8_t*`）；
 *   内核只把每个字节读一遍 ⇒ **无附加语义、无性能含义**。
 * ⚠ 帧长上限（策略 B，T127 拍板②）：`N ≤ 28`（= `VCOM_BIN_MAX_LEN(58) − 1` 的一半）
 *   ⇒ 帧总长 `7+2N ≤ 63 B` ⇒ **天然单包落地**。36 项**必须分 2 批**（28 + 8）下发。
 *   详见分派层注释与 `ads1293_set_regs()` 上方注释（含白名单分工）。 */
int ads1293_set_regs(const volatile uint8_t *items, uint32_t n);

/* ---- SET_REGS_FORCE（协议 v2.3 §5.1 下行 0x12「实验写」；★T141 2026-09-24 落码）----
 * 与 `ads1293_set_regs()` **逐字同构**，唯一差别是 `allow_protected`：
 *   `allow_protected == 0` ⇒ 老口径（`0x11`/`0x12`/`0x13`/`0x21`–`0x24` 共 7 条硬拒）；
 *   `allow_protected != 0` ⇒ **放行全部 36 条**（只放开这 7 条；**表外地址两种模式都仍拒**）。
 * 🛑 只在**确需改 fs / 内部参考等受保护项**时用（上位机侧 = 显式勾选「实验写」）。
 * ⚠ **一次性语义（per-frame）**：本函数**不记任何状态**，`allow_protected` 由调用方每帧独立传
 *   ⇒ 结构上就不存在"忘关开关 ⇒ 下一次普通同步静默改掉频率链"的路径。
 *   ⚠ 与 R2（T141 方案 §6）的"一次性、发完即回落"是**同一件事的两层保证**：
 *     上位机发完把勾选框清掉（UI 层），固件这边每帧独立传参（内核层）。
 * `reqid`：成功时回填到上行 `0x03` 快照帧（0x11 或 0x12，供上位机区分是哪条命令的 ACK）。
 * 其余纪律（预扫零副作用 / 无 printf_msg / 成功才发快照 / `N ≤ 28`）**全部相同**。
 * 返回：0 = 全一致；>0 = 首个问题项 1-based 下标。 */
int ads1293_set_regs_ex(const volatile uint8_t *items, uint32_t n,
                        uint8_t allow_protected, uint8_t reqid);

/* ---- START / STOP（协议 v2.2 §5.1；★G6 2026-09-23 落码）-------------------------
 * ads1293_start() : START 的唯一入口 = 停流/STANDBY → 清两队列 → **写全表**（P1）→
 *                   （成功才）滤波链复位 → CONFIG=0x01 → 起流
 *                   🛑 写表失败 ⇒ **不起流**、停在 STOP（fail-safe）
 *                   🛑 **不加状态门**（重复 START = 安全重启）；返回 0 / 首个不一致项下标+1
 * ads1293_stop()  : STOP 的公开入口（`ads1293_conversion_run` 是 static，必须另开）
 *                   = 停流 → STANDBY → **清两队列**（补 P7 的 Filter 队列）；幂等
 * ⚠ 两者都**不发快照** —— 协议 §5.1 的隐式 ACK 是"**显示帧流的起停**"，不是快照回执。 */
int  ads1293_start(void);
void ads1293_stop(void);

#ifdef __cplusplus
}
#endif
#endif //_QUEUE_H_