#include "main.h"
#include "usbd_cdc_if.h"
#include "comm_port.h"   /* ★T198：通道抽象层 */
#include "ads1293.h"
#include "queue.h"
#include "ecg_filter.h"
#include "ecg_out.h"
#include "ecg_dc_block.h"
#include "ecg_crc8.h"

/* ★T232（2026-11-11）：LOD 首次启用（台架阶段）。
 * 开关值 → regs_leaderIII 的 #if 分支：0x06=0x00(DC 工作) / 0x07=0x07(IN1~IN3) / 0x08=0x01(8 nA)。
 * 🛑 不用 T215 当年的 0x07=0x0F / 0x08=0xFF：
 *    · 0x0F 含 IN4 —— IN4 被 RLD 放大器输出驱动（0x0C=0x04 ⇒ SELRLD=100 内部路由到 IN4，本设计有意为之：RLD 经 IN4→AECG N1 形成 LOD 回流；令 RLDOUT/pin9 闲置），非电极输入，禁开；
 *    · 0xFF = 2.04 µA/路，三路合计 6.12 µA（±25% 最坏 7.65 µA），患者辅助电流余量太小；改 8 nA 起步。
 * 见 docs/T232 §15.3。 */
#define ECG_LOD_EN            1 

#if 0
#define  ASCII_TYPE           0
#if ASCII_TYPE
    #define  MAXPOP_QLEN      5
#else
    #define  MAXPOP_QLEN      25
#endif
#endif
#define  MAXPOP_QLEN      25
#define  timeout          1000

extern void printf_msg(const char *format,...);
extern void demo_ads1293_start(void);
extern void demo_ads1293_stop(void);

extern SPI_HandleTypeDef  hspi3;
extern TIM_HandleTypeDef  htim2;
extern   bool      _bdispascii;
extern   uint8_t   _popoutcnt;
//ecg_filter_f32 f32;
ecg_filter_f64  f64;
ecg_dc_block    g_dc;
ecg_out_f32     out;
uint32_t        ecgfilteridx;
uint32_t        gdtover;
uint16_t        seQidx;

/* ---- 853 原始流输出开关（2026-09-20 拍板；T54）--------------------------------
 * 0（默认）= 500 Hz 重采样输出，与既有上位机一致；
 * 1        = 每个 853 样本直接入队，行率 = DRDY 853.2423 Hz。
 * 只经 ads1293_set_raw853()/ads1293_raw853_is_on() 访问，命令入口见
 * Core/Src/vcomcmd.c 的 `test raw853 [on|off]`。 */
static volatile uint32_t _braw853 = 0u;

/* ---- 双通道(CH1+CH2)输出开关（★G8 2026-09-23；T125 拍板）-----------------------
 * 0（默认）= 单通道 59 B 帧，**逐位等同 G7 定稿** ⇒ 它就是回归基线的守护者；
 * 1        = 双通道 109 B 帧：载荷 `[CH1×N][CH2×N]` **块布局**（N = _popoutcnt = 25）。
 * ★ 本步（G8）CH2 的 N 个点 = **CH1 的 N 个点**（打包时直接复制，不为 CH2 做任何
 *   滤波/重采样）⇒ 上位机两个画布应**逐位完全相同**。这正是本步最硬的判据：
 *   块布局、LEN、CRC 覆盖长度、CH2 段偏移 四处只要错一处，两画布立刻不等。
 * ⚠ CH2 的**真值**（芯片 0x3A-0x3C）本步只走 ASCII 列⑦（ecgraw2），不进二进制帧。
 * ⚠ 与 STA bit2 联动（见 ads1293_status_byte：开 ⇒ bit2=1 即 I&II）。
 * ⚠ 只读访问器在下面 ads1293_ch2_is_on()；命令入口 `test ch2 [on|off]`。 */
static volatile uint32_t _bch2 = 0u;

/* ---- ★T163（2026-09-29）：ASCII 精简档 `ascii-2` 的状态位 ------------------------
 * 依据：猛哥 2026-09-29 对本项目 T162 §八 五问的拍板 ——
 *   ① 走 **α**：新增 `test ascii-2`，`ascii-1`（7 列）**一字不改**；
 *   ② 列③（500 域序号）与列⑥（dtcycles）**改开关**；
 *   ③ 列①（853 域序号）**默认不输出**，「需要的时候加上，可以用命令来做」；
 *   ④ ASCII 专用批次数**可新增，但要用命令调**；
 *   ⑤ 列⑤（链后值）由 `%.02f` 改**定点整数**（"看分析数据的需要"）。
 *
 * ★ 列序恒为 **[①] ② [③] ⑤ [⑥]**（与 `ascii-1` 的相对顺序一致），
 *   关掉的列**整列消失、不留占位** ⇒ 列数随开关变。⇒ **组合信息由 T161 方案 B 的
 *   sidecar 记录**；串口助手手动用时靠 `test asciistat` 回显自证（同 raw853 范式）。
 *
 * ★★ 定点标度 = **0.1 code/LSB**（**不是**原议的 0.01；标度由"实测极值 × 18 B 恒定"定）：
 *   全库扫描 98 份 txt / **808,421 行**，列⑤ 实测极值 **−282,502.24 ~ +270,099.41 code**
 *   （`1mv-square-notch-620k(in)-2026-09-20 134612.txt`；⚠ 该扫描读的是 **ascii-1 的 `%.02f` 列
 *   ＝ `filterraw` 原值**，本就是 code ⇒ 折成 ascii-2 本列（×10）即 **±2,825,022**）。
 *     · 0.01 ⇒ |max| = 28,250,224 → **8 位数字 ＋ 符号 = 9 字符** ⇒ 行长不恒定 18 B；
 *     · 0.1  ⇒ |max| =  2,825,022 → **7 位数字 ＋ 符号 = 8 字符** ⇒ **严格 18 B** ✓。
 *   配合下面的 ±9,999,999 钳制（= **±999,999.9 code ≈ ±112.2 mV**，覆盖余量 **3.54×**；
 *   ⚠ 余量按 **±2,825,022** 算，不是按 ±282,502）⇒ `%08d` **恒为 8 字符**。
 *
 *   ⚠ **2026-09-30 订正（单位标签）**：本注原写「0.1 µV / LSB」与「±999,999.9 µV ≈ ±1 V」
 *     —— **标签错**（差 1/0.1122 ≈ **8.9×**），**但余量 3.54× 是对的**（见上）。
 *     ⑤ 列 ＝ `filterraw × 10`，而 `filterraw` 是 **code 域**，源码级依据：
 *       `ads1293.c:1300` `filterraw = ecg_restore_process(ecgraw)`（该函数自陈"返回滤波输出(code 域)"）
 *       `ads1293.c:1483` 又对它直接调 `ecg_out_codes_to_lsb()` ⇒ 传给 out 的是 codes。
 *     ⇒ **真单位 = 0.1 code**；判读换算 `µV = 本列 ÷ 10 × ECG_UV_PER_CODE`（`Filter/ecg_out.h:56` = 0.1122，
 *       即 0.1 code ≈ 0.0112 µV / LSB —— 远细于最细判据 a1 = 37.7 µV）。
 *
 * ★ 列①/②的宽度同样有实测背书：`ecgraw` 全库峰值 **9,024,748**（7 位）、**0 行为负**，
 *   唯一 1 行超 7 位（9 位）是**粘包伪迹**（同行列① 跳 41、列③ 跳 24）⇒ `%08d` 足够。
 *
 * 🛑 **批次必须钳制**（`_asciipopcnt`）：`enqueue()` 在**队满时仍 memcpy**
 *   （`Core/Src/queue.c:135`，覆盖最旧项、只记 `ovf_item`）⇒ 批次不得 ≥ 25
 *   （`EcgQueueFilter` 容量 = `MAX_FILTERECG_NUM` = 25），且
 *   `批次 × 行上界 ≤ sizeof(buff)-1`。两条都在 `ads1293_ascii2_popcap()` 里算。 */
static volatile uint8_t  _bdispver     = 1u;  /* 1 = ascii-1（7 列，逐字不变）；2 = ascii-2 */
static volatile uint8_t  _asciiraw     = 0u;  /* 列① 853 样本序号（默认关） */
static volatile uint8_t  _asciiidx     = 0u;  /* 列③ 500 域序号  （默认关） */
static volatile uint8_t  _asciicpu     = 0u;  /* 列⑥ dtcycles    （默认关） */
static volatile uint8_t  _asciipopcnt  = 16u; /* ascii-2 批次行数（默认 16） */
static volatile uint32_t _ascii2_rowmax = 19u;/* 单行字节**上界**（初值 = 默认组合 ②⑤） */

#define ASCII2_POPMIN   4u    /* 低于 4 则 CDC 调用次数反而不划算 */
#define ASCII2_POPMAX   24u   /* ≤ 队列容量 25 − 1（留一格，防 enqueue 覆盖最旧项） */
#define ASCII2_FIX_OVF  9999999   /* 定点 ±9,999,999（**0.1 code** 单位 ⇒ ±999,999.9 code ≈ ±112.2 mV）⇒ %08d 恒 8 字符 */

/* ★T163：CDC 组行缓冲容量。**唯一真源** —— `ads1293_raw_popoutput()` 里的
 *   `char buff[ASCII2_BUFF_LEN]` 与 `ads1293_ascii2_popcap()` 都引用它
 *   ⇒ 改一处即全局同步，不会漂移。原为字面 `512`，值不变（ascii-1 缓冲行为逐字不变）。
 *   🛑 本宏**必须定义在这里**（所有使用点之前）：`ads1293_ascii2_popcap()` 在
 *      `ads1293_raw_popoutput()` **之前**，定义若跟着函数走 ⇒ `Error[Pe020]
 *      identifier "ASCII2_BUFF_LEN" is undefined`（**本轮实踩**，见 T163 报告 §六）。 */
#define ASCII2_BUFF_LEN  512u



#define MAX_RAWECG_NUM 		MAXPOP_QLEN //MAXPOP_QLEN+10 //3
#define MAX_FILTERECG_NUM 	MAXPOP_QLEN //MAXPOP_QLEN+10
queue_t EcgQueueRaw;
uint8_t EcgQueueRawBuf[sizeof(ecg_data_t)*MAX_RAWECG_NUM];
queue_t EcgQueueFilter;
uint8_t EcgQueueFilterBuf[sizeof(ecg_filter_t)*MAX_FILTERECG_NUM];

int ads1293_write_reg(uint8_t reg, uint8_t val)
{
    int ret = 0;
    uint8_t tx_buf[2] = {reg&0x7F,val};
    HAL_GPIO_WritePin(ADS_CS_GPIO_Port, ADS_CS_Pin, GPIO_PIN_RESET);
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    ret = HAL_SPI_Transmit(&hspi3,tx_buf,2,timeout);
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    HAL_GPIO_WritePin(ADS_CS_GPIO_Port, ADS_CS_Pin, GPIO_PIN_SET);
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    return ret;
}

int ads1293_write_reginc(uint8_t reg, uint8_t* val, uint8_t len)
{
    uint8_t tx_buf[16] = {reg&0x7F,0x00};
    int ret = 0;
    memcpy(&tx_buf[1], val, len);
    HAL_GPIO_WritePin(ADS_CS_GPIO_Port, ADS_CS_Pin, GPIO_PIN_RESET);
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    ret = HAL_SPI_Transmit(&hspi3,tx_buf,len+1,timeout);
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    HAL_GPIO_WritePin(ADS_CS_GPIO_Port, ADS_CS_Pin, GPIO_PIN_SET);    
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    return ret;
}

int ads1293_read_reg(uint8_t reg, uint8_t* val)
{
    int ret = 0;
    uint8_t tx_buf[2] = {reg|0x80,0x00};
    uint8_t rx_buf[2] = {0x00,0x00};
    HAL_GPIO_WritePin(ADS_CS_GPIO_Port, ADS_CS_Pin, GPIO_PIN_RESET);
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    ret = HAL_SPI_TransmitReceive(&hspi3, tx_buf ,rx_buf, 2, timeout);
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    HAL_GPIO_WritePin(ADS_CS_GPIO_Port, ADS_CS_Pin, GPIO_PIN_SET);
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    *val = rx_buf[1];
    return ret;    
}

int ads1293_read_reginc(uint8_t reg, uint8_t* val, uint8_t len)
{
    int ret = 0;
    uint8_t tx_buf[16+1] =   {reg|0x80,0x00};
    uint8_t rx_buf[16+1] = {0x00};
    HAL_GPIO_WritePin(ADS_CS_GPIO_Port, ADS_CS_Pin, GPIO_PIN_RESET);
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    ret = HAL_SPI_TransmitReceive(&hspi3, tx_buf ,rx_buf, len+1, timeout);
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    HAL_GPIO_WritePin(ADS_CS_GPIO_Port, ADS_CS_Pin, GPIO_PIN_SET);
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    __NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();__NOP();
    memcpy(val,&rx_buf[1],len);
    return ret;    
}

void ads1293_config_test(void)
{
    int     ret = 0;
    uint8_t rddefault = 0;
    uint8_t rdval = 0;
    ret = ads1293_read_reg(TI_ADS1293_CONFIG_REG, &rddefault);
    if(ret!=HAL_OK){
        printf_msg("rd err: %02x %02x\r\n",ret, rddefault);
        return;
    }
    printf_msg("reg0x%02x=%02x\r\n",TI_ADS1293_CONFIG_REG, rddefault);
    ret = ads1293_write_reg(TI_ADS1293_CONFIG_REG, 0x00);
    if(ret==HAL_OK){
       ads1293_read_reg(TI_ADS1293_CONFIG_REG, &rdval); 
       if(rdval == 0x00){
          printf_msg("config rd&wt ok\r\n"); 
          return;
       }
    }
}

void ads1293_readregs_test(void)
{
   uint8_t inc = 0;
   uint8_t buff[16] = {0};
   
   int ret = ads1293_read_reginc(0x00,buff,16);
   for(inc = 0; inc<16; inc++) {
    printf_msg("%02x ",buff[inc]);
   }
   printf_msg("ret = %d\r\n",ret);
   
   ret = ads1293_read_reginc(0x10,buff,16);
   for(inc = 0; inc<16; inc++) {
    printf_msg("%02x ",buff[inc]);
   }
   printf_msg("ret = %d\r\n",ret);   

   ret = ads1293_read_reginc(0x20,buff,16);
   for(inc = 0; inc<16; inc++) {
    printf_msg("%02x ",buff[inc]);
   }
   printf_msg("ret = %d\r\n",ret);     
}

void ads1293_autoinc_test(void)
{
    uint8_t auto_val[] = {0x02, 0x02, 0x02, 0x02, 0x00, 0x00, 0x08, 0x00, 0x00};
    uint8_t rd_val[16] = {0};
    uint8_t regval = 0x00;
    uint8_t inc = 0;
    int     ret = 0;
    //printf_msg("reg0x%02x:",TI_ADS1293_R2_RATE_REG);

    for(inc = 0; inc<9; inc++)
    {
       ret = ads1293_read_reg(TI_ADS1293_R2_RATE_REG+inc, &regval);
       
       if(ret != HAL_OK) break;
       printf_msg("0x%02x ",regval);
    }
    if(ret != HAL_OK) {
        printf_msg("read reg err :%d\r\n", ret);
        return;
    }
  
    ret = ads1293_write_reginc(TI_ADS1293_R2_RATE_REG, auto_val, 9);
    printf_msg("\r\nwrite regs autoinc! ret = 0x%02x\r\n", ret);

    ret = ads1293_read_reginc(TI_ADS1293_R2_RATE_REG, rd_val, 9);
    printf_msg("read regs autoinc! ret = 0x%02x\r\n", ret);

    for(inc = 0; inc<9; inc++){
        printf_msg("0x%02x ",rd_val[inc]);

    }

    
    printf_msg("\r\nreread reg:\r\n");
    
    for(inc = 0; inc<9; inc++)
    {
       ret = ads1293_read_reg(TI_ADS1293_R2_RATE_REG+inc, &regval);
       if(ret != HAL_OK) break;
       printf_msg("0x%02x ",regval);
    }    
    if(ret != HAL_OK) {
        printf_msg("\r\nread reg err :%d\r\n", ret);
        return;
    }
    printf_msg("\r\ntest end\r\n");
 
}
#if 0
static uint8_t regs_afe[][2] = 
               {
                  {TI_ADS1293_CONFIG_REG, 0x00},\
                  {TI_ADS1293_FLEX_CH1_CN_REG, 0x0A},\
                  {TI_ADS1293_FLEX_CH2_CN_REG, 0x1A},\
                  {TI_ADS1293_FLEX_CH3_CN_REG, 0},\
                  {TI_ADS1293_FLEX_PACE_CN_REG, 0},\
                  {TI_ADS1293_FLEX_VBAT_CN_REG, 0},\
                  {TI_ADS1293_LOD_CN_REG, 0x08},\
                  {TI_ADS1293_LOD_EN_REG, 0},\
                  {TI_ADS1293_LOD_CURRENT_REG, 0},\
                  {TI_ADS1293_LOD_AC_CN_REG, 0},\
                  {TI_ADS1293_CMDET_EN_REG, 0x07},\
                  {TI_ADS1293_CMDET_CN_REG, 0},\
                 
                  {TI_ADS1293_RLD_CN_REG, 0x04},\
                  {TI_ADS1293_WILSON_EN1_REG, 0},\
                  {TI_ADS1293_WILSON_EN2_REG, 0},\
                  {TI_ADS1293_WILSON_EN3_REG, 0},\
                  {TI_ADS1293_WILSON_CN_REG, 0},\
                  {TI_ADS1293_REF_CN_REG, 0},\
                  {TI_ADS1293_OSC_CN_REG, 0x04},\
                  {TI_ADS1293_AFE_RES_REG, 0},\
                  {TI_ADS1293_AFE_SHDN_CN_REG, 0},\
                  {TI_ADS1293_AFE_FAULT_CN_REG, 0},\
                  {TI_ADS1293_AFE_DITHER_EN_REG, 0},\
                  {TI_ADS1293_AFE_PACE_CN_REG, 0x05},\

                  {TI_ADS1293_R2_RATE_REG, 0x02},\
                  {TI_ADS1293_R3_RATE1_REG, 0x02},\
                  {TI_ADS1293_R3_RATE2_REG, 0x02},\
                  {TI_ADS1293_R3_RATE3_REG, 0x02},\
                  {TI_ADS1293_P_DRATE_REG, 0},\
                  {TI_ADS1293_DIS_EFILTER_REG, 0},\
                  {TI_ADS1293_DRDYB_SRC_REG, 0x08},\
                  {TI_ADS1293_SYNCOUTB_SRC_REG, 0},\
                  {TI_ADS1293_MASK_DRDYB_REG, 0},\
                  {TI_ADS1293_MASK_ERR_REG, 0},\
                  {TI_ADS1293_ALARM_FILTER_REG, 0x33},\
                  {TI_ADS1293_CH_CNFG_REG, 0x30},\
                      
                                       
    };  

#endif


const uint8_t regs_leaderIII[][2] = 
{
    {0x00, 0x00},
    {0x01, 0x11},
    {0x02, 0x19},
    {0x03, 0x00},
    {0x04, 0x00},
    {0x05, 0x00},
#if ECG_LOD_EN   
    {0x06, 0x00},   /* LOD_CN：SHDN_LOD=0 工作 / SELAC_LOD=0 DC 模式 / ACLVL=00 */
    {0x07, ECG_LOD_MASK},   /* LOD_EN：IN1+IN2+IN3（掩码唯一定义在 ads1293.h；★不含 IN4 —— 它被 RLD 输出驱动） */
    {0x08, 0x01},   /* LOD_CURRENT：8 nA 起步（不是 0xFF=2.04 µA） */
#else
    {0x06, 0x08},
    {0x07, 0x00},
    {0x08, 0x00},
#endif
    {0x09, 0x00},
    {0x0a, 0x07},
  //  {0x0a, 0x03},
    {0x0b, 0x00},
    {0x0c, 0x04},   /* RLD_CN：SELRLD=100 ⇒ RLD 输出内部路由到 IN4（★本设计有意：经 IN4→AECG N1 提供 LOD 回流，RLDOUT/pin9 闲置） */
    {0x0d, 0x00},
    {0x0e, 0x00},
    {0x0f, 0x00},
    {0x10, 0x00},
    {0x11, 0x00},
    {0x12, 0x04},
    {0x13, 0x00},
    {0x14, 0x00},
    {0x15, 0x00},
    {0x16, 0x00},
    {0x17, 0x05},

    {0x21, 0x02},
    {0x22, 0x02},
    {0x23, 0x02},
    {0x24, 0x02},
    {0x25, 0x00},
    {0x26, 0x00},
    {0x27, 0x08},
    {0x28, 0x00},
    {0x29, 0x00},
    {0x2a, 0x00},

    {0x2e, 0x33},
    {0x2f, 0x30},//{0x2f, 0x30},
};


const uint8_t regs_shorted[][2] = 
{
    {0x00, 0x00},
    {0x01, 0x0a},
    {0x02, 0x00},
    {0x03, 0x00},
    {0x04, 0x00},
    {0x05, 0x00},
    {0x06, 0x00},
    {0x07, 0x0f},
    {0x08, 0xff},
    {0x09, 0x00},
    {0x0a, 0x00},
    {0x0b, 0x00},
    {0x0c, 0x00},
    {0x0d, 0x00},
    {0x0e, 0x00},
    {0x0f, 0x00},
    {0x10, 0x00},
    {0x11, 0x00},
    {0x12, 0x04},
    {0x13, 0x00},
    {0x14, 0x00},
    {0x15, 0x00},
    {0x16, 0x00},
    {0x17, 0x05},
    
    {0x21, 0x02},
    {0x22, 0x02},
    {0x23, 0x02},
    {0x24, 0x02},
    {0x25, 0x00},
    {0x26, 0x00},
    {0x27, 0x08},
    {0x28, 0x00},
    {0x29, 0x00},
    {0x2a, 0x00},

    {0x2e, 0x33},
    {0x2f, 0x10},
};


#define ARRAY_SIZE(array)	(sizeof(array)/sizeof(array[0]))

/* ---- G5(2026-09-23)：滤波链软件复位内核（待拍②：抽共用内核）---------------------
 * 抽出来的理由：conversion(true) 与 ads1293_reset_afe() 都要把滤波链清回初值；
 * 若各写一遍，日后改一处必漏另一处。⚠ 本函数**只做初值**，不含启停与 CONFIG 写。 */
/* ============================================================================
 * ★T232-2-2 / 2-3（2026-11-11）：LOD 锁存状态机（低频读 + 软件锁存 + 非对称防抖）
 *
 * 为什么不能逐样本读：手册 §8.3.24 —— 寄存器 0x18~0x1E 的错误位是
 *   **锁存 + 读后清零**（"will only return to zero after being read"）。
 *   原实现把 `0x18` 放在 DRDY 每样本路径里读（旧 `ads1293_raw_enqueue`），
 *   ⇒ 锁存被逐样本消耗 ⇒ 单样本读到 0 不能证明"没脱落"，上层也拿不到"曾经发生"。
 *
 * 本实现：
 *   · **低频读**：`ads1293_lod_poll()` 每 20 ms 读一次（主循环调用，内部按 tick 自节流）；
 *   · **软件锁存**：读到非零就按位记住（`s_lod_latched |= raw`）；
 *   · **非对称防抖**：连续 N 次非零才置位（60 ms），连续 M 次全零才清除（500 ms），M > N；
 *   · 清除只由"**持续**无故障"驱动，**不由单次读到 0** 驱动；
 *   · SPI 读失败时**保持现状**（不清不置）。
 *
 * 🛑 芯片硬件层 ALARM_FILTER 只有 µs 级（409.6 kHz × 1~16 次 ≈ 2.4~39 µs），
 *    对人体/运动伪迹等于没有 ⇒ 软件防抖必需（T233 P1-2）。
 * ========================================================================== */
#define LOD_POLL_MS      20u    /* 读取周期 */
#define LOD_ASSERT_CNT    3u    /* 连续 3 次(≥60 ms) 非零 ⇒ 置位 */
#define LOD_CLEAR_CNT    25u    /* 连续 25 次(≥500 ms) 全零 ⇒ 清除（> assert，非对称） */

static uint8_t  s_lod_raw;
static uint8_t  s_lod_latched;
static uint8_t  s_lod_stable;
static uint16_t s_lod_assert_cnt;
static uint16_t s_lod_clear_cnt;
static uint32_t s_lod_last_ms;

static void ads1293_lod_state_reset(void)
{
    s_lod_raw = 0u;
    s_lod_latched = 0u;
    s_lod_stable = 0u;
    s_lod_assert_cnt = 0u;
    s_lod_clear_cnt = 0u;
    s_lod_last_ms = 0u;
}

/** @brief 每 20 ms 调一次（主循环里调，内部自节流）。 */
void ads1293_lod_poll(void)
{
    const uint32_t now = HAL_GetTick();
    if ((uint32_t)(now - s_lod_last_ms) < LOD_POLL_MS) {
        return;
    }
    s_lod_last_ms = now;

    uint8_t raw = 0u;
    if (ads1293_read_reg(TI_ADS1293_ERROR_LOD_REG, &raw) != HAL_OK) {
        return;                 /* SPI 失败：保持现状 */
    }
    s_lod_raw = raw;

    if (raw != 0u) {
        s_lod_latched |= (uint8_t)(raw & ECG_LOD_MASK);   /* ★读后清零 ⇒ 必须自己锁存；只留已启用输入 */
        s_lod_clear_cnt = 0u;
        if (s_lod_assert_cnt < LOD_ASSERT_CNT) {
            s_lod_assert_cnt++;
        }
        if (s_lod_assert_cnt >= LOD_ASSERT_CNT) {
            s_lod_stable = s_lod_latched;
        }
    } else {
        s_lod_assert_cnt = 0u;
        if (s_lod_clear_cnt < LOD_CLEAR_CNT) {
            s_lod_clear_cnt++;
        }
        if (s_lod_clear_cnt >= LOD_CLEAR_CNT) {
            s_lod_latched = 0u; /* ★只有"持续无故障"才清锁存 */
            s_lod_stable  = 0u;
        }
    }
}

/** @brief 防抖后的脱落位（bit0=IN1 … bit2=IN3）。 */
uint8_t ads1293_lod_stable(void)
{
    return s_lod_stable;
}

/** @brief 最近一次原始读值（诊断用）。 */
uint8_t ads1293_lod_raw(void)
{
    return s_lod_raw;
}

static void ads1293_filter_chain_init(void)
{
    //ecg_filter_f32_init(&f32);
    /* ⚠ T84(2026-09-21)：dc_block 的截止频率**不再是本文件的硬编码**，改为按档宏
     *   `ECG_DC_BLOCK_FC_HZ_F`（定义在 Filter/ecg_filter_coeffs.h 的各档分支里）。
     *
     *   为什么（别再改回字面量）：本模块是一阶高通，它与 BP 段里的 HP 节**共同**构成
     *   低端极点预算 Σf —— 判据 a 的 a1（脉冲后偏移）满足
     *       a1 ≈ A · T · 2π · Σf ，  预算 Σf ≤ 0.05306 Hz（3 mV/100 ms 激励）
     *   若把 fc 写死在这里，Σf 的"两半"就劈在两个文件里：改档时 BP 那半跟着变、
     *   这半不变 ⇒ **档与 Σf 脱钩**，且改一行常数会**静默改变其它档**的组合。
     *   按档宏化后一档一行，Σf 在 coeffs.h 里可读可查（T83 §9 / T84）。
     *
     *   各档取值：A+B（当前投产档）= 0.02（Σf=0.02，37.7% 预算）；其余档 = 0.05。
     *   ⚠ 本值同时决定 **620 kΩ 建场/恢复时间**（τ = 1/(2π·f_min)），与 a1 是两个指标。 */
    ecg_dc_block_init(&g_dc, ECG_DC_BLOCK_FC_HZ_F);
    ecg_filter_f64_init(&f64);

    /* ★T232：每次（重新）起流把 LOD 状态机清零 —— 脱落状态属于"本次连接"，不跨段沿用 */
    ads1293_lod_state_reset();
    ecg_out_f32_init(&out);
    ecg_restore_start();   /* ★T146：复位 fast_restore_snippet.c 的 5 个 static + 装载 START 静默窗。
                            *   它虽是独立编译单元，但在 dc_block **上游** ⇒ 残值同样跨 STOP 存活，
                            *   属"链"的一部分。
                            *   🛑 必须写在本函数内、与上面三行同批：命令路径 0x01 /
                            *   ASCII `test afe start` / reset_afe 三条 START 入口都经这里，
                            *   写到别处必漏一条。 */
    ecgfilteridx = 0;
    seQidx = 0;
    gdtover = 0;
    //filterdisp = 0.0;
}

/* ---- G5(2026-09-23)：conversion 的无回显内核（待拍①：拆薄封装）--------------------
 * 🛑 为什么必须拆：ads1293_conversion() 末尾那句 printf_msg("config reg:...") 是
 *    **无条件**执行的（在 if/else 之外），而 printf_msg 内含 HAL_Delay(5)
 *    （Core/Src/demo.c:41）；样本周期只有 1172 µs ⇒ **一句回显 = 丢 4.27 个 DRDY 样本**。
 *    §5b 的 ads1293_start() 要调 conversion 两次 ⇒ 光回显就丢 ≈8.5 个样本，
 *    而 G6 的判据恰是"上电直接 START ⇒ 波形正确"。
 * ⇒ **命令路径（0x01/0x02/0x03）一律走本函数**（无 delay、无回显）；
 *    ads1293_conversion() 保留原行为给 ASCII 人工调试（人工需要看得见过程）。 */
static void ads1293_conversion_run(bool bstart)
{
    if(bstart){
        ads1293_filter_chain_init();
        demo_ads1293_start();
        ads1293_write_reg(TI_ADS1293_CONFIG_REG, 0x01);
    }
    else{
        demo_ads1293_stop();
        ads1293_write_reg(TI_ADS1293_CONFIG_REG, 0x02);
        queue_reset(&EcgQueueRaw);
    }
}

void ads1293_conversion(bool bstart)
{
    uint8_t rdval = 0;
    //ads1293_read_reg(TI_ADS1293_CONFIG_REG, &rdval);
    ads1293_conversion_run(bstart);
    ads1293_read_reg(TI_ADS1293_CONFIG_REG, &rdval);
    printf_msg("config reg:0x%02x \r\n",rdval);
}

/* ★G8(2026-09-23)：一次事务读 CH1+CH2（原名 ads1293_ecg_ch1，只读 3 B）。
 *   0x37/0x38/0x39 = CH1_ECG_H/M/L，0x3A/0x3B/0x3C = CH2_ECG_H/M/L ⇒ **地址连续**，
 *   一发 read_reginc(...,6) 取全 6 B（其内部 tx_buf[17]/rx_buf[17] ⇒ len ≤ 16 安全），
 *   比"再发一次 SPI 事务"省 —— 本函数在 DRDY 采样热路径上（主循环，非中断）。
 * ⚠ 符号处理照搬 CH1（下面两行被注释的符号扩展是**故意保留**的：24-bit 码不做 int32
 *   符号扩展 ⇒ 下游按 24-bit 量纲处理；改它等于改标定）。
 * ⚠ CH1/CH2 的输入路由不同（leaderIII 表 {0x01,0x11} vs {0x02,0x19}）⇒ 两路读到的
 *   不是同一信号源。G8 阶段 ch2 **只写不读**（唯一出口＝ ASCII 列⑦）。 */
int ads1293_ecg_ch(int32_t* rdval, int32_t* rdval2)
{
    uint8_t rdbuf[6] = {0};
    int ret = ads1293_read_reginc(TI_ADS1293_DATA_CH1_ECG_H_REG,rdbuf,6);
    int32_t ecg_raw =
      ((int32_t)rdbuf[0] << 16)
    | ((int32_t)rdbuf[1] << 8)
    |  (int32_t)rdbuf[2];
    int32_t ecg_raw2 =
      ((int32_t)rdbuf[3] << 16)
    | ((int32_t)rdbuf[4] << 8)
    |  (int32_t)rdbuf[5];

    // if (ecg_raw & 0x00800000)
    // {
    //   ecg_raw |= 0xFF000000;
    // } 
    *rdval = ecg_raw;
    if (rdval2 != 0) {
        *rdval2 = ecg_raw2;   /* ★G8：CH2 原始码；调用方传 NULL 即退回旧行为 */
    }
    return ret;
}


void ads1293_afe_test(void)
{
    uint8_t rdval = 0;
    uint8_t ind = 0;
    int ret = 0;
#if 0 
    //printf_msg("regs_shorted\r\n");
    for(ind = 0; ind < ARRAY_SIZE(regs_shorted); ind++)
    {
        ads1293_write_reg(regs_shorted[ind][0], regs_shorted[ind][1]);
    }
    //chk regval
    for(ind = 0; ind < ARRAY_SIZE(regs_shorted); ind++)
    {
        ads1293_read_reg(regs_shorted[ind][0],&rdval);
        printf_msg("0x%02x ",rdval);
        if(rdval != regs_shorted[ind][1]) {
            printf_msg("\r\nchk regval err! count=%d\r\n",ind);
            break;
        }
    }
    if( ind == ARRAY_SIZE(regs_shorted)){
        printf_msg("\r\nchk regval OK! count=%d\r\n",ind);
    }
#else
//regs_leaderIII
    //printf_msg("\r\nregs_leaderIII\r\n");
    for(ind = 0; ind < ARRAY_SIZE(regs_leaderIII); ind++)
    {
        ret = ads1293_write_reg(regs_leaderIII[ind][0], regs_leaderIII[ind][1]);
        if(ret != HAL_OK)
        {
            printf_msg("\r\nregs_leaderIII read err:%d %d\r\n",ret, --ind);
            return;
        }
        HAL_Delay(1);
    }
    printf_msg("\r\nregs_leaderIII wrtie ok\r\n");
    HAL_Delay(10);
    for(ind = 0; ind < ARRAY_SIZE(regs_leaderIII); ind++)
    {
        ads1293_read_reg(regs_leaderIII[ind][0],&rdval);
        printf_msg("0x%02x ",rdval);
        if(rdval != regs_leaderIII[ind][1]) {
            printf_msg("\r\nchk regval err! count=%d\r\n",ind);
            return;//break;
        }
    }
    if( ind == ARRAY_SIZE(regs_leaderIII)){
        printf_msg("\r\nchk regval OK! count=%d\r\n",ind);
    }    
#endif    
}


/* ---- G5(2026-09-23)：写表内核（协议 §5.1；G5 与 G6 共用）-------------------------
 * 与 ads1293_afe_test() 的区别：**只写 + 逐条回读校验，不含任何 delay / 回显**。
 * 🛑 为什么不能照搬 afe_test：它每条 HAL_Delay(1)×36 = 36 ms、回读段每条一句
 *    printf_msg（内含 HAL_Delay(5)）×36 = 180 ms、另两句 = 10 ms ⇒ **≈236 ms**。
 *    那 236 ms 几乎全是人为拖的（写表本身只有 36 次 SPI 往返 ≈ µs~ms）；
 *    命令路径若照搬，一次 START/RESET 就丢几十个 DRDY 样本（T106 §9e.2-3）。
 * 返回：0 = 全表回读一致；>0 = 首个不一致项下标+1（1-based，便于与 afe_test 的 count 对齐）。 */
/* ★T170-A（2026-10-08）：写表失败详情留痕（**只记不报**）。
 *   缘由：`0x01 START` 写表失败 ⇒ **不起流且不回显**（见 vcomcmd.c 的 0x01 段）⇒
 *     上位机只能靠 1.5 s 超时识别，**连是第几条不一致都看不到**（T170 §0）。
 *   ⇒ 此处只**记**；由 vcomcmd.c 在失败时读出去回显（那一刻已停流 ⇒ 回显无丢样本顾虑）。
 *   ⚠ 只在失败路径写入 ⇒ 成功路径**零副作用、零延时、零输出**。
 *   got 哨兵：0xFD = 写失败、0xFE = 读失败。 */
static uint8_t _wt_fail_reg  = 0xFFu;
static uint8_t _wt_fail_want = 0x00u;
static uint8_t _wt_fail_got  = 0xFEu;

uint8_t ads1293_wt_fail_reg (void) { return _wt_fail_reg;  }
uint8_t ads1293_wt_fail_want(void) { return _wt_fail_want; }
uint8_t ads1293_wt_fail_got (void) { return _wt_fail_got;  }

int ads1293_write_table(const uint8_t t[][2], uint32_t n)
{
    uint8_t rdval = 0;
    for (uint32_t i = 0; i < n; i++) {
        int ok = 0;
        rdval = 0xFEu;                        /* ★T170-A：默认哨兵＝“读没成功” */
        for (int attempt = 0; attempt < 2 && !ok; attempt++) {
            if (attempt) HAL_Delay(1);            /* 失败才短延迟重试一次（最坏 36 ms）*/
            if (ads1293_write_reg(t[i][0], t[i][1]) != HAL_OK) { rdval = 0xFDu; continue; }
            if (ads1293_read_reg(t[i][0], &rdval)   != HAL_OK) { rdval = 0xFEu; continue; }
            ok = (rdval == t[i][1]);
        }
        if (!ok) {
            /* ★T170-A：失败留痕（供上层回显；**不改返回值语义**） */
            _wt_fail_reg  = t[i][0];
            _wt_fail_want = t[i][1];
            _wt_fail_got  = rdval;
            return (int)(i + 1);
        }
    }
    return 0;
}


/* ---- G10(2026-09-23)：SET_REGS 白名单判据（协议 v2.2 §5.1 下行 0x11）-------------
 * 两重判据，**任一不满足即拒**：
 *   ① **必须在表内** —— 参与读写的就是 `regs_leaderIII` 那 36 条；表外地址（如
 *      `0x30` 数据、`0x18–0x1E` 错误状态）一律拒 ⇒ 上位机误发也踩不到芯片的
 *      隐陷阱寄存器（写 `0x50` DATA_LOOP 之类会让数据流"看起来还在动、内容全错"）。
 *   ② **不在硬拒 7 条** —— H2 频率链（`0x13` `0x21`-`0x24`）＋ H3 参考与时钟
 *      （`0x11` `0x12`）：改了就**静默**毁掉 fs / µV 定标 / 全套滤波系数
 *      （协议 §6.2），现象上完全看不出来 ⇒ 必须由固件拦死。
 *
 * 🛑🛑 **为什么固件只拒 7 条，而协议 §6.2 的 H 硬闸有 11 条 —— 差的 4 条必须放行**：
 *   差的是 **H1 电极映射 `0x01`/`0x02`**、**H4 读回通道 `0x2F`**、**H5 DRDY 源 `0x27`**。
 *   理由：G9 要写 `0x02` 切导联（I ↔ I&II）、`0x2F` 决定芯片读回哪几路 ⇒
 *   **固件若硬拒这 4 条，G9 的功能当场没有了**。
 *   它们改错的后果改由**上位机「拦 Start / 红色醒目」**兜住（协议 §6.1 的"不一致时的
 *   动作"列）⏳ **待补**（本步只管固件侧：T127 拍板②＝"写进注释，后续补全"）。
 *   ⇒ 分工不是矛盾，是**两层把关**：
 *      **固件守"不可恢复的静默失效"**（改了连现象都没有）；
 *      **上位机守"可识别的口径错误"**（改了波形会明显不对，人看得见）。
 *   🛑 将来若有人"补全"成硬拒这 4 条 ⇒ **G9 功能立刻坏掉 —— 别补**。 */
static int _reg_writable(uint8_t reg, uint8_t allow_protected)
{
    uint32_t i;
    int in = 0;

    for (i = 0u; i < (uint32_t)ARRAY_SIZE(regs_leaderIII); i++) {
        if (regs_leaderIII[i][0] == reg) { in = 1; break; }
    }
    /* ① 表外地址 ⇒ 拒。
     * ★T141(2026-09-24)：本道门**与 `allow_protected` 无关** —— 实验写（0x12）不是
     *   "想写哪就写哪"，它只放开 ② 里那 7 条**表内**项；表外地址两种模式都仍拒。 */
    if (!in) { return 0; }

    /* ★T141(2026-09-24)：实验写旁路 —— `allow_protected != 0` ⇒ 跳过 ② 的硬拒集，
     *   直接放行全部 36 条（只由 0x12 SET_REGS_FORCE 传入 1）。 */
    if (allow_protected) {
        return 1;
    }

    switch (reg) {                         /* ② 硬拒 7 条（H2 频率链 + H3 参考/时钟）*/
      case 0x11u: case 0x12u:                          /* REF_CN / OSC_CN：定标、fs */
      case 0x13u:                                      /* AFE_RES：fs */
      case 0x21u: case 0x22u: case 0x23u: case 0x24u:   /* R2 / R3 抽率：fs + 系数 */
        return 0;
      default:
        break;
    }
    return 1;
}

/* ---- G10(2026-09-23)：SET_REGS 内核（协议 §5.1 下行 0x11；与 G5/G6 共用同一纪律）--
 * ★T141(2026-09-24)：本内核**同时服务 0x11 SET_REGS 与 0x12 SET_REGS_FORCE（实验写）**
 *   —— 两条命令**逐字同构**，唯一差别是 `allow_protected`（0 / 1）与回填的 `reqid`。
 *   ⚠ 预扫判据带 `allow_protected` ⇒ 差异里混进硬拒项时**必须整批**改走 0x12，
 *     不能拆成"能写的发 0x11、硬拒的发 0x12"两帧 —— 那两帧各回一帧快照，
 *     上位机会多出一次"未登记的 ACK"。
 * items : `(REG,VAL)×n` **交错数组**（`items[2k]` = REG、`items[2k+1]` = VAL）；
 * n     : 项数（分派层传 `(argLen-1)/2`；分派层的 `VCOM_BIN_MAX_LEN` 门已保证 n ≤ 28）。
 * 四条纪律：
 *   ① **白名单预扫在写任何一条之前**（零副作用；见 `_reg_writable` 上方注释）。
 *   ② 逐条 `write_reg` + `read_reg` 回读比对（与 `ads1293_write_table` 同款写法：
 *      失败才 `HAL_Delay(1)` 重试一次，最坏 28 ms）。
 *   ③ 无 printf_msg、无多余 delay（命令路径铁律）。
 *   ④ **成功才发快照**（★T141：`reqid` 回填**触发本次写入的命令码** 0x11 / 0x12）；失败不发 ⇒ 上位机 1.0 s 超时。
 * 返回：0 = 全一致；>0 = 首个问题项 1-based 下标（被拒 / 写失败 / 回读不一致）。
 * 🛑 与 `ads1293_write_table` 的区别：那个是**写全表 36 条**（START/RESET 用）；
 *   这个是**写上位机送来的子集**，且多一道白名单。⚠ 两者返回语义**故意保持一致**，
 *   便于分派层共用同一套判词。 */
int ads1293_set_regs_ex(const volatile uint8_t *items, uint32_t n,
                        uint8_t allow_protected, uint8_t reqid)
{
    uint32_t i;
    uint8_t  rdval = 0;

    /* ③ 内核自守：项数上限 = (VCOM_BIN_MAX_LEN − 1) / 2 = 28。
     *    数值来源见 Core/Src/vcomcmd.c 的 `VCOM_BIN_MAX_LEN`；分派层已由该门保证，
     *    这里只是给"将来别的调用方"兜底（本函数在头文件里公开）。 */
    if (n == 0u || n > 28u) {
        return 1;
    }

    /* ① 白名单预扫（零副作用）：任一项被拒 ⇒ 立即返回，**一条都不写**。
     *   ★T141：判据带 `allow_protected` ⇒ 0x12 时只对"表外地址"生效。 */
    for (i = 0u; i < n; i++) {
        if (!_reg_writable(items[2u * i], allow_protected)) {
            return (int)(i + 1u);
        }
    }

    /* ② 逐条写 + 回读校验（重试 1 次，与 ads1293_write_table 同款）*/
    for (i = 0u; i < n; i++) {
        uint8_t reg = items[2u * i];
        uint8_t val = items[2u * i + 1u];
        int ok = 0;
        for (int attempt = 0; attempt < 2 && !ok; attempt++) {
            if (attempt) HAL_Delay(1);
            if (ads1293_write_reg(reg, val)   != HAL_OK) continue;
            if (ads1293_read_reg(reg, &rdval) != HAL_OK) continue;
            ok = (rdval == val);
        }
        if (!ok) {
            return (int)(i + 1u);
        }
    }

    /* ④ 隐式 ACK：上行 0x03 快照帧（REQID 回填**触发命令码** 0x11 / 0x12）
     *    —— 🛑 只在全部成功时发 */
    (void)ads1293_send_snapshot(reqid, NULL);
    return 0;
}

/* ---- ★T141(2026-09-24)：0x11 SET_REGS 的**对外契约保持不变** ——
 * 只是一层薄封装：把 `allow_protected` 钉为 0（老口径：拒 7 条）、`reqid` 钉为 0x11。
 * 🛑 刻意**不复制**一份函数体：两条命令的「预扫 → 逐条写+回读 → 发快照」链条
 *    **只能有一份实现**，复制出去将来改一处必忘另一处（劈叉）。
 * ⇒ 判据「0x11 路径的语句集合未变」以"执行路径完全一致"为准（本封装不引入任何新语句）。 */
int ads1293_set_regs(const volatile uint8_t *items, uint32_t n)
{
    return ads1293_set_regs_ex(items, n, 0u, 0x11u);
}

/* ---- G5(2026-09-23)：RESET 原子序列（协议 §5.1 下行 0x03）------------------------
 * 触发：上位机 Reset 按钮 / 串口发 `AA 55 80 01 00 03 53`（7 B）。
 * 🛑 三条纪律：
 *   ① 全程无 printf_msg（命令路径铁律，理由见 ads1293_conversion_run 注释）；
 *   ② 序列**必须停在 STOP** —— 若自动 START，START_CON=1 会立刻锁 0x13 / 0x21–0x24，
 *      之后再写这些寄存器会**静默失败** ⇒ 恢复到 STOP 才是一张干净的表；
 *   ③ **不加 RUN 状态门**（T106 §9e.6 待拍⑤）：主判据正是"运行中点 Reset ⇒ 帧停"，
 *      必须允许 RUN 态进入。⚠ 与 0x10 REQ_SNAPSHOT（**必须**加门）口径**相反**，别抄错。
 * 返回：0 = 写表全一致（已发快照）；>0 = 首个不一致项下标+1（**不发快照**，待拍③ ⇒
 *       上位机等满 1.0 s 超时，与 G4 的 RUN 态门同一种可识别表现）。 */
int ads1293_reset_afe(void)
{
    int bad;

    /* ① 停流 + 进 STANDBY（解锁受保护寄存器）+ 清原始队列。
     *    ⚠ conversion_run(false) 内部只 reset 了 EcgQueueRaw ⇒ 按 P7 / 待拍③补 Filter 队列 */
    ads1293_conversion_run(false);
    queue_reset(&EcgQueueFilter);

    /* ② 滤波链软件复位 —— 与 conversion(true) 共用同一内核（待拍②）*/
    ads1293_filter_chain_init();

    /* ③ 写全表 + 逐条回读校验（无 delay、无回显）*/
    bad = ads1293_write_table(regs_leaderIII, ARRAY_SIZE(regs_leaderIII));

    /* ④ 🛑 停在 STOP（待拍④：不顺带 START）。
     *    ⚠ 不能省这一步：表内 0x00(CONFIG) 的写入值是 **0x00 = STANDBY**
     *    （`regs_leaderIII[0]` 就是 {0x00, 0x00}）⇒ 只写表会停在 STANDBY；
     *    「STOP 态」的状态码 **0x02** 是本步显式写的，与老路径 `conversion(false)`
     *    里的写法逐字一致（T116 离线验证 §3 已断言表首项确实是 (0x00, 0x00)）。 */
    (void)ads1293_write_reg(TI_ADS1293_CONFIG_REG, 0x02);

    /* ⑤ 上行 0x03 回执（REQID 回填 0x03）：**只在写表成功时发**（待拍③）*/
    if (bad == 0) {
        (void)ads1293_send_snapshot(0x03u, NULL);
    }
    return bad;
}

/* ---- T207(2026-11-04)：上电把默认表写进 AFE（"上电从未配置"陷阱的正面修法）-------
 * 触发：`demo_ads1293()`（`Core/Src/demo.c`）在 `ads1293_raw_queueinit()` 之后调一次。
 *
 * 🛑 为什么必须有它：ADS1293 **不吃 MCU 的 NRST** —— `Core/Inc/main.h:60-67` 里只有
 *    `ADS_CLK/MISO/MOSI/CS` 四个脚，全库检索 `ADS_RESET`/`RST_Pin` 零命中
 *    ⇒ **只有整板断电**才让芯片回到 POR 默认值。旧固件上电从不写表 ⇒ 快照里
 *    H 组（`0x01 0x02 0x27 0x2F` ＋ `0x11 0x12 0x13 0x21~0x24`）永远是 POR 值 ≠ 金标
 *    ⇒ 上位机 `ConfigValidator.CanStart`（`ConfigValidator.cs:53`）= false
 *    ⇒ `Form1.cs:1050` 直接弹「禁止 Start」、**一条下行都不发** ⇒ 现场表现就是
 *    「上电后必须先勾实验写点同步，才能正常 START」。本函数在上电时把这个缺口补掉。
 *
 * 🛑 四条纪律（与 `ads1293_start()` / `ads1293_reset_afe()` 同口径）：
 *    ① 全程走 `ads1293_conversion_run(false)`（无 delay / 无回显），🛑 **不许用**
 *       `ads1293_conversion()`（内含 `HAL_Delay(5)` 的 printf_msg）；
 *    ② 🛑 **不发 0x03 快照** —— 上电阶段上位机还没发任何命令，多一帧就是
 *       「未登记的 ACK」（与 `ads1293_reset_afe()` 第⑤步的唯一区别，故意不去共用）；
 *    ③ **停在 STOP（`CONFIG=0x02`）**，不替用户 START：起流仍是 `0x01` 的独占职责；
 *    ④ 表首项 `regs_leaderIII[0]` 是 `{0x00,0x00}` = STANDBY ⇒ 写完**必须显式补 0x02**，
 *       否则芯片停在 STANDBY（"不在跑"照成立，但下次 START 的口径对不上）。
 *
 * ⚠ 与 `0x01 START` 的关系：**互不依赖**。`ads1293_start()` 第③步照样写全表 36 条
 *    （P1 依据：宁可重复写，不依赖上电那一次是否成功）⇒ 本函数失败也不会让 START 坏掉，
 *    只是快照/校验环节仍然不一致。
 * 返回：0 = 全表回读一致；>0 = 首个不一致项下标+1（细节读 `ads1293_wt_fail_*()`）。 */
int ads1293_boot_init(void)
{
    int bad;

    ads1293_conversion_run(false);      /* ① 停流 + CONFIG=0x02（STANDBY，解锁受保护寄存器） */
    bad = ads1293_write_table(regs_leaderIII, ARRAY_SIZE(regs_leaderIII));  /* ② 写全表 + 逐条回读 */
    (void)ads1293_write_reg(TI_ADS1293_CONFIG_REG, 0x02);                   /* ③ 停回 STOP */

    /* ④ 首轮失败 ⇒ 隔 10 ms 整表重来一次。理由：上电瞬间 AFE 的电源/时钟可能还没
     *    稳定（本板 LDO 起振 + 芯片内部 POR 释放时序），属于"再给一次机会"，
     *    **不是掩盖问题** —— 最终成败仍由返回值与 `_wt_fail_*` 留痕如实上报。 */
    if (bad != 0) {
        HAL_Delay(10);
        bad = ads1293_write_table(regs_leaderIII, ARRAY_SIZE(regs_leaderIII));
        (void)ads1293_write_reg(TI_ADS1293_CONFIG_REG, 0x02);
    }
    return bad;
}

/* ---- G6(2026-09-23)：START（协议 §5.1 下行 0x01）--------------------------------
 * 触发：上位机 Start 按钮 / 串口发 `AA 55 80 01 00 01 5D`（7 B）。
 * 🛑 四条纪律：
 *   ① **必须内建写表**（P1 依据）：`Core/Src/main.c:99` 只调 `demo_ads1293()`，
 *      而真正"只初始化队列"的是 `Core/Src/demo.c:99` 的 `ads1293_raw_queueinit()`
 *      ⇒ 上电 AFE **从未被配置**（"两个 99"陷阱）。若只写 CONFIG=0x01，**波形是错的
 *      且静默**（DRDY 照产、显示帧照发，只是数据不对）⇒ 这正是本步的主判据。
 *   ② 全程走 `ads1293_conversion_run()`（无 delay / 无回显）；🛑 **不许用**
 *      `ads1293_conversion()` —— 它末尾那句 printf_msg 是 if/else 之外的**无条件**
 *      语句、内含 HAL_Delay(5)（`Core/Src/demo.c:41`）⇒ 每调一次丢 ≈4.27 个 DRDY 样本。
 *      （G5 待拍① 拆薄封装正是为这一步；§5b 的伪码写在该决策之前，已过时。）
 *   ③ 写表失败 ⇒ **不起流**（T106 §9g.6 待拍②，fail-safe：宁可不跑，不跑错）。
 *      上位机可识别：`Form1.cs:895-905` 的 IsStreaming = 最近 1.5 s 内收到过显示帧
 *      ⇒ 不起流时按钮 1.5 s 后自动回「▶ Start」，不是静默失败。
 *   ④ 🛑 **不加状态门**（待拍③）：本函数首步就停流 ⇒ 重复 START 是**安全的重启**。
 *      ⚠ 与 0x10 REQ_SNAPSHOT（**必须**加门）口径**相反**，与 0x03 RESET 同口径。
 * ⚠ 待拍①（**不抽共用内核**）：本函数 ①② 与 `ads1293_reset_afe()` 的前 3 步形似而
 *   实不同 —— reset 停在 STOP、本函数一定要起流。**故意不抽**，两处各留 3 行，
 *   防日后"顺手让 start 也停在 STOP"（那会让 START 变成一个大号的 RESET）。
 * ⚠ P8：`conversion_run(true)` → `filter_chain_init()` 会把 notch/mode 复位成默认
 *   （ECG_NOTCH_DEFAULT_ON=1 / ECG_MODE_DEFAULT=DIAG，见 Filter/ecg_filter.c:675-676）
 *   ⇒ START 后 STA 必为 bit0=0 / bit1=1。上位机按"START 后手动补发 SET_MODE/SET_NOTCH"
 *   设计（`Form1.cs:923-924` 注释与本条同源）；而 `0x04/0x05` 要等 **G7** 才认
 *   ⇒ G6 阶段点「mon」/「notch off」是**静默无效**（不是坏了，别当 bug 记）。
 * 返回：0 = 写表全一致且已起流；>0 = 首个不一致项下标+1（**未起流**，停在 STOP）。 */
int ads1293_start(void)
{
    int bad;

    ads1293_conversion_run(false);      /* ① 停流 + CONFIG=0x02(STANDBY，解锁受保护寄存器) + 清 Raw */
    queue_reset(&EcgQueueFilter);       /* ② 补 P7：_run(false) 内部只清了 EcgQueueRaw */
    bad = ads1293_write_table(regs_leaderIII, ARRAY_SIZE(regs_leaderIII));   /* ③ 写表 + 逐条回读 */

    if (bad == 0) {
        ads1293_conversion_run(true);   /* ④ 写表 OK ⇒ 滤波链 init + CONFIG=0x01 + 起流 */
    } else {
        (void)ads1293_write_reg(TI_ADS1293_CONFIG_REG, 0x02);   /* ④' 失败 ⇒ 停回 STOP，不起流 */
    }
    return bad;
}

/* ---- G6(2026-09-23)：STOP（协议 §5.1 下行 0x02）---------------------------------
 * 触发：上位机 Stop 按钮 / 串口发 `AA 55 80 01 00 02 54`（7 B）。隐式 ACK = 显示帧流停。
 * 🛑 为什么不直接复用 `ads1293_conversion_run(false)`：它一是 `static`（本文件私有的
 *   "唯一命令内核"，G5 刚断言过这层约束不许放开），二是**只清 Raw 队列**（P7）。
 *   本函数是"二进制 STOP"的**公开入口** ⇒ 顺手把 Filter 队列也清掉（与 reset_afe/start 同口径）。
 * ⚠ P7 缺口（ASCII `test afe stop` 路径同样只清 Raw）**不在本步修**（T106 §9g.6 待拍④）：
 *   若把 `queue_reset(&EcgQueueFilter)` 塞进 `conversion_run(false)`，会改动 G5 已断言的
 *   语句集合并连带改变 `0x03 RESET` 的行为 ⇒ 记为**遗留**，单独立小补丁。
 * 🛑 不加状态门（待拍③）：幂等，STOP 态再发一次无副作用。 */
void ads1293_stop(void)
{
    ads1293_conversion_run(false);
    queue_reset(&EcgQueueFilter);
}

/* ============ G3(2026-09-23)：寄存器快照帧（协议 v2.2 §3.2，Type 0x03）=============
 * 用途：① 上位机连上后要一份全表快照做配置校验；② Reset/SetChannel/SetRegs 的回执
 *       （G4 的 REQ_SNAPSHOT、G5 的 Reset、G9/G10 全部复用本函数）。
 *
 * 帧格式（与上位机 `Core/Protocol.cs` 的 `ParseSnapshot()`、`Config/expected_regs.json`
 * 同一口径，逐字节对齐见 T106 §4-G3 的自检项）：
 *     AA 55 | 0x03 | LEN(2B LE) | REQID(1) | COUNT(1) | (REG,VAL)×COUNT | CRC8
 *     LEN   = PAYLOAD 字节数 = REQID(1) + COUNT(1) + 2×COUNT = 2 + 2×COUNT
 *     总长  = 6 + LEN = 8 + 2×COUNT ；COUNT=36 ⇒ LEN=74=0x4A ⇒ **80 B**
 *     CRC8  覆盖 `buff[2]` 起的 **5 + 2×COUNT** 字节（TYPE+LEN+REQID+COUNT+items）
 *           上位机侧等价式：`Slice(2, 3 + LEN)` = 3 + 74 = 77 ✓ 逐字节相同
 *
 * 🛑 两处口径不许改：
 *   1) 表内 36 条**按 REG 升序**输出（协议 §3.2）。`regs_leaderIII` 本身就是升序表
 *      （0x00-0x0F / 0x10-0x17 / 0x21-0x2A / 0x2E-0x2F）⇒ 顺序直取，不需排序。
 *   2) VAL 必须是**芯片回读值**，不是写入值（协议 §5.2）—— 这样"写没写进去"才能被
 *      上位机看见。⚠ 唯一会随状态变的表内项是 **0x00 CONFIG 的 START_CON 位**
 *      （STOP 0x00／RUN 0x01／`conversion(false)` 后 0x02）⇒ 比对时 0x00 单列。
 *
 * ⚠ 为什么分 3 段读：`ads1293_read_reginc()` 的 `tx_buf[16+1]` 决定**单次上限 16 字节**
 *   （本文件 :172），**不能一次读 36**。故按 0x00/0x10/0x20 各读 16 字节，再按表挑取。
 *   表内最大地址 0x2F ⇒ 3 段刚好覆盖（数据/状态寄存器 0x18-0x1E、0x30-0x3F 不传）。
 * ⚠ 时序：3 次 SPI 事务 ≈ 数十 µs；RUN 态调用会与 DRDY 抢时基（这也是协议把 0x03 帧
 *   定成"只在 STOP 态发"的原因）。本函数**不含状态门**，门由调用方按场景加：
 *   ASCII 调试通道不加（见 vcomcmd.c 的 `test snapshot`），二进制下行通道（G4）必须加。
 */
#define ADS1293_SNAP_MAX_ITEMS   60u   /* 协议 §3.2：COUNT 上限 60（装不完时分多帧）*/
#define ADS1293_SNAP_SEGS        3u    /* 0x00 / 0x10 / 0x20 三段，每段 16 字节 */

/* 读表内 36 条 ⇒ 打包成 (REG,VAL)×N（不含帧头/CRC）。
 * ⚠ 调用方须保证 items 至少 `2 × ARRAY_SIZE(regs_leaderIII)` = 72 字节。
 * 返回 0 = 成功；-1 = SPI 读失败（此时不输出半张表，避免上位机误判为"配置被改"）。*/
int ads1293_snapshot(uint8_t *items, uint8_t *count)
{
    uint8_t seg[ADS1293_SNAP_SEGS][16];
    uint8_t idx = 0;

    if (count != NULL) {
        *count = 0u;
    }

    for (idx = 0u; idx < ADS1293_SNAP_SEGS; idx++) {
        if (ads1293_read_reginc((uint8_t)(idx << 4), &seg[idx][0], 16u) != HAL_OK) {
            return -1;
        }
    }

    for (idx = 0u; idx < (uint8_t)ARRAY_SIZE(regs_leaderIII); idx++) {
        uint8_t reg = regs_leaderIII[idx][0];
        uint8_t si  = (uint8_t)(reg >> 4);          /* 高 4 位选段 */
        uint8_t val = 0xFFu;                        /* 0xFF = 不该出现的地址（表内只到 0x2F）*/
        if (si < ADS1293_SNAP_SEGS) {
            val = seg[si][reg & 0x0Fu];             /* 低 4 位选段内偏移 */
        }
        items[idx * 2u]      = reg;
        items[idx * 2u + 1u] = val;
    }
    if (count != NULL) {
        *count = (uint8_t)ARRAY_SIZE(regs_leaderIII);
    }
    return 0;
}

/* 上面 + 帧头/CRC ⇒ 直接 CDC 上行一整帧 0x03。
 * reqid : 触发本帧的下行命令码（0x00 上电主动上报／0x03 Reset／0x06 SetChannel
 *         ／0x10 ReqSnapshot／0x11 SetRegs／0x12 SetRegsForce；ASCII 调试通道用 0x00）
 * count : 出参 = 实际条目数（全表 36），失败时清 0
 * 返回   : 0 = 帧已交给 USB；-1 = 未发（SPI 失败／表长超协议上限）；1 = CDC 忙（帧可能丢）
 * ⚠ 单缓冲无流控（CDC_Transmit_FS 返回 USBD_BUSY 即丢帧）⇒ STOP 态（无显示帧竞争）
 *   必然成功；RUN 态调用可能返回 1，重发即可。
 *
 * 🛑🛑 帧缓冲**必须是 static，不能是栈局部**（2026-09-23 G3 自纠，差点上板翻车）：
 *   `CDC_Transmit_FS()` **不做拷贝** —— 它只把指针交给 USB 栈
 *   （`usbd_cdc_if.c:290` `USBD_CDC_SetTxBuffer(&hUsbDeviceFS, Buf, Len)`），
 *   数据由**后续 IN 事务**在中断里从该地址搬进 PMA，FS 下每帧（1 ms）搬一个 64 B 包
 *   ⇒ **80 B 要跨 2 个包 ≈ 2 ms**，这期间源缓冲必须保持有效。
 *   若用栈局部：本函数一返回，栈帧即被下一次调用复用 —— 而 ASCII 通道紧接着就调
 *   `printf_msg()`（其 `char buff[256]` 正在同一片栈区，`demo.c:23-42`，且它的
 *   `HAL_Delay(5)` 在 vsprintf **之后**才执行，救不了我们）⇒ **第 2 个包（尾 16 B）
 *   几乎必然被 ASCII 回显覆盖**，上板现象＝"收到 80 B 但 CRC 错/尾部乱"。
 *   ⚠ 既有显示帧（`ads1293_raw_popoutput` 的 `char buff[512]`）同源，但它的帧里还有
 *   一个 800 B 的 `ecgpopout[]` 垫在栈上，实测未被下一次浅调用踩到 ⇒ 属既有行为，
 *   本次**不动它**（另立待核实项，见 T106 §4-G3 备注）。*/
int ads1293_send_snapshot(uint8_t reqid, uint8_t *count)
{
    /* ★ static：跨 USB 异步发送窗口保持有效。.bss 128 B（RAM +128，只此一处）。
     *   单线程（主循环）调用 ⇒ 无重入问题。 */
    static uint8_t buff[8u + 2u * ADS1293_SNAP_MAX_ITEMS];   /* 128 B，足够（8+2×60）*/
    uint8_t  n     = 0u;
    uint16_t len16 = 0u;
    uint8_t  cret  = 0u;

    if (count != NULL) {
        *count = 0u;
    }
    /* 防御：表长超过协议 COUNT 上限时按协议应拆多帧，本步不支持 ⇒ 宁可不发也不越界 */
    if (ARRAY_SIZE(regs_leaderIII) > (size_t)ADS1293_SNAP_MAX_ITEMS) {
        return -1;
    }

    /* ★ 直接把 items 打包在 buff[7] 起：省一次 120 B 拷贝、少一个 120 B 中间数组。
     *   ads1293_snapshot 最多写 2×60 = 120 B ⇒ 7+120 = 127 < 128 ✓ */
    if (ads1293_snapshot(&buff[7], &n) != 0) {
        return -1;
    }

    len16   = (uint16_t)(2u + 2u * (uint16_t)n);
    buff[0] = 0xAA;
    buff[1] = 0x55;
    buff[2] = 0x03;                                    /* TYPE：寄存器快照帧 */
    buff[3] = (uint8_t)(len16 & 0xFFu);                /* LEN 低字节 */
    buff[4] = (uint8_t)((len16 >> 8) & 0xFFu);         /* LEN 高字节 */
    buff[5] = reqid;                                   /* REQID */
    buff[6] = n;                                       /* COUNT */
    buff[7u + 2u * n] = ecg_crc8((const uint8_t *)&buff[2], (uint32_t)(5u + 2u * n));
    /* ★T198：改走 comm_send。返回 0=已受理（与 USBD_OK 同为 0）⇒ 下面
     *   `cret == USBD_OK` 的判据语义不变。 */
    cret = comm_send((const uint8_t *)&buff[0], (uint16_t)(8u + 2u * n));

    if (count != NULL) {
        *count = n;
    }
    return (cret == USBD_OK) ? 0 : 1;
}

void ads1293_raw_queueinit(void)
{
    int ret = queue_init(&EcgQueueRaw, EcgQueueRawBuf, sizeof(ecg_data_t), sizeof(ecg_data_t) * MAX_RAWECG_NUM);
    printf_msg("ecg raw queue init = %d\r\n", ret);    
    ret = queue_init(&EcgQueueFilter, EcgQueueFilterBuf, sizeof(ecg_filter_t), sizeof(ecg_filter_t) * MAX_FILTERECG_NUM);
    printf_msg("ecg filter queue init = %d\r\n", ret);        
}

void ads1293_raw_enqueue(uint32_t idx)
{
    ecg_data_t ecgraw;
    /* ★T232-2-3：**不再逐样本读 0x18**（每次读都会清锁存）。
     * 改用低频轮询 + 软件锁存后的稳定态；ASCII 列⑦的 lod 语义变为"防抖后的脱落位"。 */
    ecgraw.lod = s_lod_stable;
    ads1293_read_reg(TI_ADS1293_ERROR_STATUS_REG, &ecgraw.errsta);
    ads1293_ecg_ch(&ecgraw.ecgraw, &ecgraw.ecgraw2);
    ecgraw.idx = idx;
    enqueue(&EcgQueueRaw, &ecgraw);    
}

/* ---- 853 原始流输出开关（2026-09-20 拍板；命令入口 `test raw853 [on|off]`）----
 * on : 每个 853 样本直接入队，输出行率 500 → 853.2423 Hz（见 ads1293_raw_popoutput）。
 * off: 恢复 500 Hz 重采样输出（上电默认）。
 *
 * ★★ 切回 off 时必须 ecg_out_f32_init(&out)，这是本开关唯一的隐藏陷阱：
 *    853 模式期间 ecg_resample_f32_process() 一次都不被调用 ⇒ 其内部状态
 *      rs.y_prev = 仍停留在**切入 853 之前**的那个滤波样本（插值区间起点）
 *      rs.acc_us = 同样冻结
 *    若不复位，切回后第一个满足 acc>=Tout 的输出点会拿这个「隔着数十万个样本」
 *    的 y_prev 去做 y_prev + (y-y_prev)*frac 插值 ⇒ **单个大幅尖峰**，
 *    而且它落在状态切换点上，正是我们做低频判据时要看的区域。
 *    复位把 first 置 1 ⇒ 下一输入样本直接原样输出（*out = y），干净无尖峰。
 *
 * 同时 queue_reset(&EcgQueueFilter)：丢掉切换瞬间可能混着两种行率的残留行，
 * 使 txt 里不会出现「同一文件内 500 行与 853 行交错」的不可解析片段。
 * ⚠ 本函数不清 EcgQueueRaw，也不动滤波链节点状态（切换不产生滤波器瞬态）。
 * ⚠ 与 `test afe start` 的关系：start 走 ecg_filter_f64_init()，只复位滤波链、
 *   不碰 _braw853 ⇒ 本开关是独立的，建议在 start 之后再设（与 notch 同一注意点）。 */
void ads1293_set_raw853(uint32_t on)
{
    if (on) {
        _braw853 = 1u;
    } else {
        _braw853 = 0u;
        ecg_out_f32_init(&out);   /* ★ 复位重采样器，避免切回后的插值尖峰 */
    }
    queue_reset(&EcgQueueFilter);
}

uint32_t ads1293_raw853_is_on(void)
{
    return _braw853;
}

/* ---- CH2 输出开关（★G8；命令 `test ch2 [on|off]`）------------------------------
 * on : 二进制显示帧切 **双通道 109 B**；off: 回 59 B 单通道（上电默认）。
 * 🛑 切换**不碰任何滤波节点状态** ⇒ 无瞬态（与 raw853 不同，无需复位重采样器）。
 * ⚠ 不改变 `_popoutcnt` —— 帧长由 LEN 公式给（猛哥 2026-09-20 明确：减压别动它）。 */
void ads1293_set_ch2(uint32_t on)
{
    _bch2 = (on != 0u) ? 1u : 0u;
}

uint32_t ads1293_ch2_is_on(void)
{
    return _bch2;
}

/* ---- ★T163：ASCII 精简档 `ascii-2` 的访问器 -----------------------------------
 * 全部只经这里访问（同 raw853 / ch2 的既有风格），`demo.c` 一行未动。 */

uint32_t ads1293_dispver(void)
{
    return _bdispver;
}

/* 1 = ascii-1（7 列，回归基线）；2 = ascii-2（精简档）。
 * ⚠ `ascii-0` / `ascii-1` 两条既有路径必须显式置 1，否则切到 ascii-2 后
 *   再发 `test ascii-1` 会**仍走精简分支** ⇒ 既有 41 个「7 列」硬门脚本当场失效。 */
void ads1293_set_dispver(uint32_t ver)
{
    _bdispver = (ver == 2u) ? 2u : 1u;
}

/* 单行字节**上界**（不是典型值）。各列按「**可证的最大字符数**」取：
 *   ① idxraw    `%06u` —— ★**6 位**（猛哥 2026-09-29 补充拍板："ascii 模式仅用于测试、
 *               不用于产品；测试时间一般都是短时间抓取数据用，所以序号都不需要 8 位，
 *               6 位就够"）。容量可算：**999,999 / 853.2423 Hz ≈ 19.5 min**，
 *               且 `_ecgrawidx` 在 `demo_ads1293_start()` 复位 ⇒ **每次 START 重新计时**。
 *   ② ecgraw    int32 ADC 码，实测峰值 9,024,748（7 位）⇒ **9**（留进位余量）
 *   ③ idxfilter `%06d` —— 同上 6 位；容量 **999,999 / 500 Hz ≈ 33.3 min**，
 *               `ecgfilteridx` 在公共 START 复位点（三条 START 入口都经）清零 ⇒ 同样重计。
 *   ⑤ 定点      **已被钳在 ±9,999,999** ⇒ **8（可证）**
 *   ⑥ dtcycles  `%05u` 实测 1905~2040（4 位）⇒ 上界取 **6**（余量 ~490×）
 *   另加 `\n` 1 ＋ 列间分隔各 1。
 * 默认（②⑤）→ 1 + 9 + 1 + 8 = **19**；全开 → **40**（6 位口径比 7/10 位口径省 12）。
 * ⚠ **典型值**（＝设计工况内恒定的那个宽度，报表里的"18 B"用它）：
 *   默认 **18**、＋③ 25、＋① 25、＋⑥ 24、＋①③ 32、＋①⑥ 31、＋③⑥ 31、全开 38。 */
uint32_t ads1293_ascii2_rowmax(void)
{
    uint32_t w = 1u;                              /* '\n' */
    if (_asciiraw) w += 6u + 1u;                  /* ① ＋ 分隔（6 位口径） */
    w += 9u;                                      /* ②（首列，无前导分隔） */
    if (_asciiidx) w += 1u + 6u;                  /* 分隔 ＋ ③（6 位口径） */
    w += 1u + 8u;                                 /* 分隔 ＋ ⑤（必出，已钳 ⇒ 可证 8） */
    if (_asciicpu) w += 1u + 6u;                  /* 分隔 ＋ ⑥ */
    return w;
}

/* ★T163c：**典型行长**（＝设计工况内每行实际多少字节，含 `\n`）。
 *   ⚠ 与 `ads1293_ascii2_rowmax()` 的区别：rowmax 是**上界**（② 按 9 位、⑥ 按 6 位算，
 *     用于"缓冲容量 ÷ 批次"的账），本函数是**真值**（② 按实测 8 位、⑥ 按实测 5 位）。
 *     两者只在"数值可能变宽"的列上有差 ⇒ 默认档 **上界 19／真值 18**。
 *   ⇒ **回显 `linesz=` 必须用它**（2026-09-29 T163c 修：原先在 `vcomcmd.c` 里写死 `18u`，
 *     而 `test ascii-2` **不重置列开关** ⇒ 先 `ascii-raw on` 再发 `ascii-2`
 *     实际已是 25 B 却仍报 18 ⇒ **回显失真**）。
 * 公式（与产出分支逐项对应）：1(`\n`) ＋ 7*① ＋ 8(②) ＋ 7*③ ＋ 9(分隔+⑤) ＋ 6(分隔+⑥)
 *   = **18 + 7*raw + 7*idx + 6*cpu**
 *   八组合对表（与 T163 报告 §三 的行长表逐项一致）：
 *     ②⑤ **18**／①②⑤ 25／②③⑤ 25／②⑤⑥ 24／①②③⑤ 32／①②⑤⑥ 31／②③⑤⑥ 31／全开 **38**。 */
uint32_t ads1293_ascii2_linesz(void)
{
    uint32_t w = 18u;                                 /* 默认：\n ＋ ②(8) ＋ 分隔 ＋ ⑤(8) */
    if (_asciiraw) w += 7u;                           /* ① 6 位 ＋ 其分隔 */
    if (_asciiidx) w += 7u;                           /* 其分隔 ＋ ③ 6 位 */
    if (_asciicpu) w += 6u;                           /* 其分隔 ＋ ⑥ 5 位 */
    return w;
}

/* 🛑 批次的**唯一合法上界**：同时受「队列容量」与「缓冲容量」夹逼。
 *   队列：`EcgQueueFilter` 容量 25，且 enqueue 满时覆盖最旧项 ⇒ 至多 24；
 *   缓冲：`(ASCII2_BUFF_LEN - 1) / 行上界` —— 默认（19）时 26→钳到 24，
 *        单开任一开关（26）时 **19**，双开（33）时 15，全开（40）时 12。
 *   ⇒ **默认批次 16 在"默认"与"单开任一开关"四种常用配置下都可用**（含丢包体检与
 *     CPU 实测这两种最常用组合）；只有同时开≥2 个列序时才会被下调（回显报生效值）。
 *     （★6 位口径把"单开"的 popcap 从 17 提到 **19**、双开 12→15、全开 9→12。）
 * 取 `ASCII2_POPMIN` 兜底（避免算出 0 行）。 */
uint32_t ads1293_ascii2_popcap(void)
{
    uint32_t cap = (uint32_t)((ASCII2_BUFF_LEN - 1u) / _ascii2_rowmax);
    if (cap > ASCII2_POPMAX) cap = ASCII2_POPMAX;
    if (cap < ASCII2_POPMIN) cap = ASCII2_POPMIN;
    return cap;
}

uint32_t ads1293_ascii2_popcnt(void)
{
    return _asciipopcnt;
}

/* 设定批次：**钳制到 [POPMIN, popcap()]**，并返回**实际生效值**
 * （调用方必须回显返回的生效值 —— 否则"设了 16 实际跑 8"会变成静默差异）。 */
uint32_t ads1293_set_ascii2_popcnt(uint32_t n)
{
    uint32_t cap = ads1293_ascii2_popcap();
    if (n > cap) n = cap;
    if (n < ASCII2_POPMIN) n = ASCII2_POPMIN;
    _asciipopcnt = (uint8_t)n;
    return (uint32_t)_asciipopcnt;
}

/* 开关变化 ⇒ 行上界变 ⇒ **批次必须重新钳制**（否则可能越界）。
 * 顺序不可颠倒：先更新 `_ascii2_rowmax`，再钳批次（popcap 读的就是它）。 */
static void ads1293_ascii2_recalc(void)
{
    _ascii2_rowmax = ads1293_ascii2_rowmax();
    (void)ads1293_set_ascii2_popcnt(_asciipopcnt);
}

/* 列开关：which 1 = 列①（853 序号）／2 = 列③（500 域序号）／3 = 列⑥（dtcycles） */
uint32_t ads1293_set_ascii2_col(uint32_t which, uint32_t on)
{
    uint8_t v = (on != 0u) ? 1u : 0u;
    if (which == 1u)      _asciiraw = v;
    else if (which == 2u) _asciiidx = v;
    else if (which == 3u) _asciicpu = v;
    else return 0xFFFFFFFFu;   /* 非法列号 ⇒ 不碰任何状态，由调用方报错 */
    ads1293_ascii2_recalc();
    return 0u;
}

uint32_t ads1293_ascii2_col(uint32_t which)
{
    if (which == 1u) return _asciiraw;
    if (which == 2u) return _asciiidx;
    if (which == 3u) return _asciicpu;
    return 0xFFFFFFFFu;
}

/* ★G2(2026-09-23)：STA 状态字节（协议 v2.2 §4）。
 *
 *   bit0 mode    0 = diag ／ 1 = mon    ← ecg_filter_f64_mode_is_mon(&f64)
 *   bit1 notch   0 = off  ／ 1 = on     ← ecg_filter_f64_notch_is_on(&f64)
 *   bit2 channel 0 = I    ／ 1 = I&II   ← ads1293_ch2_is_on()（★G8 起随 `test ch2` 变化）
 *   bit3 raw853  0 = off  ／ 1 = on     ← ads1293_raw853_is_on()
 *   bit4 LOD     0 = 正常 ／ 1 = 脱落    ← ★T232 起恒有效（ECG_LOD_EN=1，旧注释"恒 0"已作废）
 *   bit5~7 保留 0
 *
 * 🛑 硬约束（协议 §4）：**必须运行期读取，不得用编译期宏**。
 *   反例＝`profile:` 回显打的是 ECG_PROFILE_NAME 宏，T92b 已栽过（diag/mon 两态
 *   回显逐字相同、判不出档，教训注释在 vcomcmd.c:227-231）。
 * 访问器均已在位（mode_is_mon 见 Filter/ecg_filter.c，raw853_is_on 见上）⇒ 本函数零新增依赖。
 * ⚠ G9 落 SET_CHANNEL(0x06) 时，只需把 bit2 的数据源由 `_bch2` 换成协议驱动的
 *   channel 标志（本函数结构不动）；bit4 LOD 位同理。 */
uint8_t ads1293_status_byte(void)
{
    uint8_t sta = 0u;

    if (ecg_filter_f64_mode_is_mon(&f64)) {
        sta |= 0x01u;   /* bit0 mode  */
    }
    if (ecg_filter_f64_notch_is_on(&f64)) {
        sta |= 0x02u;   /* bit1 notch */
    }
    if (ads1293_ch2_is_on()) {
        sta |= 0x04u;   /* bit2 channel：0 = I ／ 1 = I&II（★G8 起随 `test ch2` 变化） */
    }
    if (ads1293_raw853_is_on()) {
        sta |= 0x08u;   /* bit3 raw853 */
    }
    /* ★T232 bit4 = **任意启用输入脱落**（完整性提示，屏上图标用；猛哥 11-11 17:24 定语义）。
     * ★任务3 bit5 = **CH1_INVALID**（门控用；判据由 ECG_LOD_GATE_POLICY 决定，见 ads1293.h）。
     *   两者分开的意义：拔 IN3 时 bit4=1（出红叉）但 bit5=0（不停测）。
     *   数据源都是防抖后的 `s_lod_stable`，不再是原始值。 */
    if ((s_lod_stable & ECG_LOD_MASK)      != 0u) {
        sta |= 0x10u;   /* bit4：任意启用输入脱落 */
    }
    if ((s_lod_stable & ECG_LOD_GATE_CRIT) != 0u) {
        sta |= 0x20u;   /* bit5：CH1_INVALID（POLICY=0 看 IN1|IN2／=1 看全部） */
    }

    return sta;
}

// /* float -> 4 字节小端写入 buf[0..3]（调试帧 y_code 用） */
// static inline void put_f32_le(uint8_t *buf, float v)
// {
//     f32_le_t u;
//     u.f32 = v;
//     buf[0] = u.u8[0];   /* LSB */
//     buf[1] = u.u8[1];
//     buf[2] = u.u8[2];
//     buf[3] = u.u8[3];   /* MSB */
// }

// /* 4 字节小端 -> float（PC 回读/调试用） */
// static inline float get_f32_le(const uint8_t *buf)
// {
//     f32_le_t u;
//     u.u8[0] = buf[0];
//     u.u8[1] = buf[1];
//     u.u8[2] = buf[2];
//     u.u8[3] = buf[3];
//     return u.f32;
// }

/*
AA 55 | TYPE(1B) | LEN(2B, u16 小端) | PAYLOAD(LEN 字节) | CRC8(1B)
*/

// static inline void pack_dbg_rec(uint8_t *p, uint32_t seq_raw, uint32_t raw24,
//                                 uint32_t seq_dsp, int16_t dsp, float y_code)
// {
//     f32_le_t y;
//     y.f32 = y_code;
//     p[0] = (uint8_t)(seq_raw  >>  0);  p[1] = (uint8_t)(seq_raw  >>  8);   /* u32 小端 */
//     p[2] = (uint8_t)(seq_raw  >> 16);  p[3] = (uint8_t)(seq_raw  >> 24);
//     p[4] = (uint8_t)(raw24    >>  0);  p[5] = (uint8_t)(raw24    >>  8);
//     p[6] = (uint8_t)(raw24    >> 16);  p[7] = (uint8_t)(raw24    >> 24);
//     p[8] = (uint8_t)(seq_dsp  >>  0);  p[9] = (uint8_t)(seq_dsp  >>  8);
//     p[10]= (uint8_t)(seq_dsp  >> 16);  p[11]= (uint8_t)(seq_dsp  >> 24);
//     p[12]= (uint8_t)(dsp      >>  0);  p[13]= (uint8_t)(dsp      >>  8);   /* i16 小端 */
//     p[14]= y.u8[0];  p[15]= y.u8[1];  p[16]= y.u8[2];  p[17]= y.u8[3];    /* f32 小端 */
// }




#if 0


void ads1293_raw_popoutput(void)
{
    ecg_data_t ecgdata[MAXPOP_QLEN];
    char buff[256];
    int len = queue_len(&EcgQueueRaw);
    if(len>=MAXPOP_QLEN){
        queue_front_n(&EcgQueueRaw, &ecgdata[0], MAXPOP_QLEN, sizeof(ecgdata));
        queue_pop_n(&EcgQueueRaw, MAXPOP_QLEN); 
       
//        printf_msg("\n%07d %07d\n%07d %07d\n%07d %07d",\
//                    ecgdata[0].idx,\
//                    ecgdata[0].ecgraw,\
//                    ecgdata[1].idx,\
//                    ecgdata[1].ecgraw,\
//                    ecgdata[2].idx,\
//                    ecgdata[2].ecgraw);

#if 0        
        len = sprintf(buff,"\n%07d %07d\n%07d %07d\n%07d %07d",\
                    ecgdata[0].idx,\
                    ecgdata[0].ecgraw,\
                    ecgdata[1].idx,\
                    ecgdata[1].ecgraw,\
                    ecgdata[2].idx,\
                    ecgdata[2].ecgraw);
        CDC_Transmit_FS(( uint8_t * )&buff[0], len); 
#else
        len = 0;
        for(int idx=0; idx<MAXPOP_QLEN; idx++){
            len += sprintf(&buff[len],"\n%05d %08d %03d %03d",\
                                             ecgdata[idx].idx,\
                                             ecgdata[idx].ecgraw,\
                                             ecgdata[idx].lod,\
                                             ecgdata[idx].errsta \
                                          );
        }
        CDC_Transmit_FS(( uint8_t * )&buff[0], len);
#endif     
       
    }
    
}
#else

uint8_t  vlod = 0x01;
uint8_t  verrsta = 0x01;

/* ★T163：CDC 组行缓冲（容量宏 `ASCII2_BUFF_LEN` 已在本文件**顶部**定义 —— 它被
 *   本函数**之前**的 `ads1293_ascii2_popcap()` 用到，不能跟在这里）。 */
void ads1293_raw_popoutput(void)
{
    int len = 0;
    int ret = 0;  
    //uint8_t crc8 = 0;
    char buff[ASCII2_BUFF_LEN];
    
    ecg_data_t    ecgdata;
    ecg_filter_t  ecgfilter;
    ecg_filter_t  ecgpopout[MAXPOP_QLEN];
    if(queue_len(&EcgQueueRaw)>=1)
    {
        queue_front_n(&EcgQueueRaw, &ecgdata.lod, 1, sizeof(ecgdata));
        queue_pop_n(&EcgQueueRaw, 1);   
        ecgfilter.idxraw = ecgdata.idx;
        ecgfilter.ecgraw = ecgdata.ecgraw;
        /* ★G8：CH2 原始码随 Filter 队列带出（ASCII 列⑦ 的唯一数据源）。
         *   ⚠ 必须在下面 `_braw853` 分支**之前** —— 853 与 500 两条路径都从这里过，
         *     放进分支里会漏掉一路。本步 ecgraw2 **只出不进**：不进滤波链、不参与重采样。 */
        ecgfilter.ecgraw2 = ecgdata.ecgraw2;
        vlod = ecgdata.lod;
        verrsta = ecgdata.errsta;
        #if 1
            uint32_t t0 = DWT->CYCCNT;
            ecgfilter.filterraw = ecg_restore_process(ecgfilter.ecgraw);
            ecgfilter.dtcycles = DWT->CYCCNT - t0;
            //if (ecgfilter.dtcycles > 12000u) gdtover++;   /* 12000 cyc = 150 us，超出即记一次 */
        #else
            float  x_dc = ecg_dc_block_process(&g_dc, (float)ecgdata.ecgraw);
            ecgfilter.filterraw    = ecg_filter_f64_process(&f64, (double)x_dc);
        #endif

        if (_braw853) {
            /* 853 原始流模式（`test raw853 on`）：不重采样，每个 853 样本直接入队
             * ⇒ 输出行率 = DRDY = 853.2423 Hz（原为 500 Hz）。
             * 用途：离线复核 250~426 Hz 段真实能量 ＋ 低频三判据的「无插值伪影」版本。
             * 列③/列④ 在本模式下不产生，固定 0（2026-09-20 拍板）：
             *   列③ idxfilter 由下面的 ++ 在 else 分支里维护 ⇒ 本模式必须清零，
             *   否则会输出上一次 500 模式留下的陈值（脏列）；
             *   列④ dsrateraw 同为插值产物 ⇒ 同理置 0。
             * ⚠ 本模式下 ecg_resample_f32_process() 不被调用 ⇒ 其 rs.y_prev / rs.acc_us
             *   停留在切换前的值；切回 off 时必须由 ads1293_set_raw853(0) 复位。 */
            ecgfilter.idxfilter = 0u;
            ecgfilter.dsrateraw = 0;
            enqueue(&EcgQueueFilter, &ecgfilter);   //853hz
        } else {
            //降采样
            if (ecg_out_f32_process(&out, ecgfilter.filterraw, &ecgfilter.dsrateraw)) {
                ecgfilter.idxfilter = ++ecgfilteridx;
                enqueue(&EcgQueueFilter, &ecgfilter);  //500hz
            }
        }
        return;
    }

    /* ★T163：ASCII 精简档（`ascii-2`）走**自己的批次数** `_asciipopcnt`。
     *   🛑 绝不改 `_popoutcnt` —— 它锁着二进制帧长（猛哥 2026-09-20 明确"减压别动它"）。
     *   `ascii-0` / `ascii-1` 两条既有路径 ⇒ `npop == _popoutcnt` ⇒ **逐字不变**。 */
    uint8_t npop = (_bdispascii && (_bdispver == 2u)) ? _asciipopcnt : _popoutcnt;

    if(queue_len(&EcgQueueFilter)>=npop) //MAXPOP_QLEN)
    {
        queue_front_n(&EcgQueueFilter, &ecgpopout[0], npop, sizeof(ecgpopout));
        queue_pop_n(&EcgQueueFilter, npop);
        len = 0;
  
        if(_bdispascii) {
            for(int idx=0; idx<npop; idx++){

            if(_bdispver == 2u) {
                /* ================= ★T163：ascii-2 精简行 =================
                 * 列序恒为 [①] ② [③] ⑤ [⑥]，关掉的列整列消失。
                 * 默认（三开关全关）= `\n` + %08d + " " + %08d = **18 B 恒定**。
                 * ⚠ 每行的三段/五段 sprintf 都是**整数**格式化 —— 相比 ascii-1 里那次
                 *   `%.02f` 双精度格式化，净 CPU 是降的（整数 %d 不走 vfp 打印路径）。
                 *   但"降多少"本轮**无法上板实测**（dtcycles 只包 ecg_restore_process），
                 *   ⇒ 只作定性声明，见 T163 报告 §诚实边界。 */
                int32_t fq;
                float   fv;

                /* 硬护栏：批次已在 `ads1293_ascii2_popcap()` 按「上界×批次 ≤ 缓冲」钳过，
                 * 这里再挡一道。万一 %d 宽度异常增长，**宁可少发一行也不越界**。 */
                if(len + (int)_ascii2_rowmax > (int)ASCII2_BUFF_LEN - 1) {
                    break;
                }
                len += sprintf(&buff[len], "\n");
                if(_asciiraw) {
                    /* ★6 位口径（猛哥 2026-09-29："仅测试用、短时抓取，序号 6 位够"）。
                     *   `%06u` = 最小宽度 6 ⇒ 设计工况内（单次 START 后 < 17.5 min
                     *   的 853 抓取）恒 6 字符。超时会变 7 位 ⇒ 由上方护栏兜底；
                     *   若真发生，说明抓取时长已超出本档设计用途。 */
                    len += sprintf(&buff[len], "%06u ", (unsigned)ecgpopout[idx].idxraw);
                }
                len += sprintf(&buff[len], "%08d", (int)ecgpopout[idx].ecgraw);
                if(_asciiidx) {
                    len += sprintf(&buff[len], " %06d", (int)ecgpopout[idx].idxfilter);
                }
                /* ⑤ 链后值 ⇒ **0.1 code 定点整数**（×10 后四舍五入，不用 libm）。
                 *   ⚠ 2026-09-30 订正：本注原写「0.1 µV」**是错的**（差 1/0.1122 ≈ 8.9×）。
                 *     真单位依据：`Filter/ecg_filter.h:223`「去直流后的值 -> 原始 ADS1293
                 *     24-bit 码」＋`Filter/fast_restore_snippet.c:126`「返回滤波输出(code 域)」。
                 *     判读换算：µV = 本列 ÷ 10 × ECG_UV_PER_CODE（`Filter/ecg_out.h:56`
                 *     = 0.1122）⇒ 照旧注把 code 当 µV 读，3.1 mV 的心电会被读成 27.9 mV。
                 *   钳到 ±ASCII2_FIX_OVF ⇒ `%08d` **恒 8 字符**（浮动宽度会破坏"恒定 18 B"，
                 *   也会让"批次×上界"的账失效）。实测极值 **±282,502 code**（＝ 本列 **±2,825,022**，
                 *   来源＝全库扫描 ascii-1 的 `%.02f` 列）⇒ 余量 **3.54×**
                 *   （上限 9999999 = 999,999.9 code ≈ 112.2 mV；⚠ 余量按 ±2,825,022 算）。 */
                fv = ecgpopout[idx].filterraw;
                fq = (fv >= 0.0f) ? (int32_t)(fv * 10.0f + 0.5f)
                                  : (int32_t)(fv * 10.0f - 0.5f);
                if(fq >  ASCII2_FIX_OVF) fq =  ASCII2_FIX_OVF;
                if(fq < -ASCII2_FIX_OVF) fq = -ASCII2_FIX_OVF;
                len += sprintf(&buff[len], " %08d", (int)fq);
                if(_asciicpu) {
                    len += sprintf(&buff[len], " %05u", (unsigned)ecgpopout[idx].dtcycles);
                }
                continue;
            }

                /* ⚠ **2026-09-29（T163）订正**：本注释块原本声称「列① 自 2026-09-20 起
                 *   改为 853 样本序号（`%07u` 配 `idxraw`）」—— **与代码不符**（本注写的是历史意图，不是现状）：
                 *   自 **T88**（猛哥改）起，列① 的实参被换成**字面 `0`**，真正的
                 *   `ecgpopout[idx].idxraw` 被留在该实参后面、**以行内注释的形式注释掉**
                 *   （见下面 `ret = sprintf(...)` 的第 2 行实参处）⇒
                 *   **本档的列① 恒 0、毫无判据力**，「853 域丢包判据」实际**断链**
                 *   （`T88 §7#1` 已记为遗留）。
                 *   ⇒ 🛑 **本档（ascii-1）的列① 不要当判据用**；
                 *     需要 853/500 域丢行判据时改用 **`ascii-2` ＋ `test ascii-raw on`**
                 *     （见本文件 T163 的 ascii-2 分支，那里 `%06u` 真的接了 `idxraw`）。
                 *   ⇒ 本条同时是 T162 §七「文档漂移 #2」的现场订正：
                 *     旧索引里写的 `ads1293.c:679-681` 早已漂移，**这里是唯一真源**。
                 *   ⚠ 别在本注释里再写出注释起止符本身 —— ICCARM 会报
                 *     `Warning[Pe009]: nested comment is not allowed`（**本轮实踩**），
                 *     而 gcc `-Wall -Wextra` 当时**没报** ⇒ 两种编译器的嵌套注释判据不同，
                 *     语法门禁只跑 gcc 会漏（教训见 T163 报告 §六）。 */
                //ret = sprintf(&buff[len],"\n%07u %07d %06d %05d %.02f %05d %x",
                /* ★G8：列⑦ 由 `gdtover` 换成 CH2 原始码 `ecgraw2`（猛哥 2026-09-23 拍板）。
                 *   为何可换：`gdtover` 的溢出门限自 2026-09-20 起就注释掉了（见本文件
                 *   `//if (ecgfilter.dtcycles > 12000u) gdtover++;`）⇒ 它**恒 0**，
                 *   这一列一直在输出零信息。换掉它零信息损失。
                 *   列数不变（仍 7 列）⇒ 列①②③④⑤⑥ 位置不动，txt 解析零影响。
                 *   列⑥ dtcycles **保留**（同次拍板）：门限虽停用，数值仍在计，是 G9
                 *   切 109 B 帧后评估 CPU 占用的**现成读数**。
                 *   格式 %x → %07d：ecgraw2 是 int32 十进制码，用 %x 会打出十六进制乱码。
                 *   ⚠ 列⑦ 的实参显式写 `(int)`：本工具链 int32_t 是 `long int`，
                 *     直接传会触发 `-Wformat`（"expects int but argument has long int"）；
                 *     原列⑦ `%x` 配 uint32_t 也带着同一类告警 ⇒ 本步顺手消掉，
                 *     加转换后**输出文本逐字不变**（32 位平台 int 与 int32_t 同宽）。 */
                ret = sprintf(&buff[len],"\n%d %07d %06d %05d %.02f %05d %07d",\
                                                0,/* (unsigned)ecgpopout[idx].idxraw, */ \
                                                ecgpopout[idx].ecgraw,\
                                                ecgpopout[idx].idxfilter,\
                                                ecgpopout[idx].dsrateraw,\
                                                (double)ecgpopout[idx].filterraw,\
                                                ecgpopout[idx].dtcycles,\
                                                (int)ecgpopout[idx].ecgraw2
                                            );
                if(ret < 0) {
                    printf_msg("sprintf error at index %d\n", idx);
                    return;
                }
                len += ret;
            }
            (void)comm_send((const uint8_t *)&buff[0], (uint16_t)len);
        }
        else
        {
            buff[0] = 0xAA;
            buff[1] = 0x55;
            buff[2] = 0x01; //type
            /* ★G2(2026-09-23)：帧内加 1 字节 STA（状态自证），**58 → 59 B**。
             *   协议口径（与上位机 Core/Protocol.cs 一致）：
             *     AA 55 | TYPE | LEN(2B LE) | SEQ(2) | STA(1) | 样本(2×_popoutcnt) | CRC8
             *     LEN   = PAYLOAD 字节数 = SEQ(2) + STA(1) + 样本(2×_popoutcnt)
             *     总长  = 2(前导) + 1(TYPE) + 2(LEN) + LEN + 1(CRC) = **6 + LEN**
             *   _popoutcnt = 25 ⇒ LEN = 53 = 0x35 ⇒ 总长 59 B，帧头 `AA 55 01 35 00`。
             *   ★ STA 由 ads1293_status_byte() **运行期读取**（协议 §4 硬约束 1；宏回显判不出档，
             *     T92b 已栽过）。
             *   ⚠ 加 STA 后三处偏移必须同步右移 1（本文件内已一并改）：
             *       CRC 覆盖  ：`5+2N` → `6+2N`（TYPE+LEN+SEQ+STA+样本）
             *       样本起始  ：`buff[7]` → `buff[8]`
             *       发送长度  ：`7+2N+1` → `8+2N+1`
             *   ⚠ `test ascii-1` 走 ASCII sprintf 分支、**不产生二进制帧** ⇒ 本步必须在
             *     `test ascii-0`（二进制档）下抓包验收（T107 已就 G1 的同类判据自纠一次）。
             *   ★ 上板验收：抓包 59 B ＋ 帧头 `AA 55 01 35 00` ＋ CRC 全对；且 STA 位能被
             *     现成命令驱动 —— `test mode diag|mon` ⇒ bit0、`test notch on|off` ⇒ bit1、
             *     `test raw853 on|off` ⇒ bit3（**上位机状态栏「工况」随之变化**，见 T106 §4-G2）。 */
            /* ★G8：双通道开关 ⇒ nch ∈ {1,2}。**四处长度耦合必须同步**（漏一处 CRC 就错）：
             *   ① LEN      = 3 + 2·nch·N      ② CRC 覆盖 = 6 + 2·nch·N
             *   ③ 发送长度  = 8 + 2·nch·N + 1   ④ CH2 段偏移 = 8 + 2·N（块布局，紧跟 CH1 段）
             *   nch = 1 时四式逐字还原 G7 定稿（LEN=53 ⇒ 59 B）⇒ **回归基线不变**。 */
            uint16_t nch = (uint16_t)(_bch2 ? 2u : 1u);
            uint16_t len16 = (uint16_t)(3 + 2 * nch * (uint16_t)_popoutcnt);
            buff[3] = (uint8_t)(len16 & 0xFF);        //len
            buff[4] = (uint8_t)((len16 >> 8) & 0xFF); //len
            seQidx++;
            buff[5] = (uint8_t)(seQidx & 0xFF);
            buff[6] = (uint8_t)(((uint16_t)seQidx >> 8) & 0xFF);
            buff[7] = ads1293_status_byte();          //★ STA（协议 §4）
            for(int idx=0; idx<_popoutcnt; idx++)
            {
                /* 🛑 853 模式下 dsrateraw 恒 0（本模式不产生重采样输出点）⇒ 若照旧直接
                 * 取它，整段二进制裸流会**全是 0**（有文件、有帧、有 CRC，全是零值——
                 * 最难发现的一类坑）。故 853 分支现场补一次定标，口径与 500 档完全一致：
                 *   codes × ECG_UV_PER_CODE / ECG_LSB_OUT_UV，再 clamp 到 ±32767。
                 * 帧长/类型/CRC 不随采样率变（只由 _popoutcnt 决定）⇒ 帧结构无需改。 */
                int16_t v = _braw853
                          ? ecg_out_codes_to_lsb((float)ecgpopout[idx].filterraw)
                          : ecgpopout[idx].dsrateraw;
                buff[8+idx*2+0] = (uint8_t)(v & 0xFF);
                buff[8+idx*2+1] = (uint8_t)(((uint16_t)v >> 8) & 0xFF);
                if (_bch2) {
                    /* CH2 ≡ CH1（G8 拍板）：CH2 不做滤波/重采样，直接复制 CH1 的 N 个点
                     * ⇒ 上位机两画布逐位相同 = 块布局与切分是否正确的**唯一硬判据**。 */
                    buff[8+_popoutcnt*2+idx*2+0] = buff[8+idx*2+0];
                    buff[8+_popoutcnt*2+idx*2+1] = buff[8+idx*2+1];
                }
            }
            buff[8+nch*_popoutcnt*2] = ecg_crc8((const uint8_t*)&buff[2], (uint32_t)(6+nch*_popoutcnt*2));
            (void)comm_send((const uint8_t*)&buff[0], (uint16_t)(8+nch*_popoutcnt*2+1));
        }
    }
}
#endif
