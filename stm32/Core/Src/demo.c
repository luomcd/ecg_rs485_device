#include "main.h"
#include "stdarg.h"
#include "stdlib.h"
#include "stdio.h"
#include "stdbool.h"
#include "usbd_cdc_if.h"
#include "comm_port.h"
#include "ads1293.h"
#include "queue.h"
#include "ecg_crc8.h"

extern TIM_HandleTypeDef  htim2;
extern void vcom_recv_analysis(void);

volatile bool      _bdispascii = 0;
volatile bool      _bconvint = 0;
volatile bool      _bconvstart = 0;
volatile uint8_t   _popoutcnt = 25;
volatile uint32_t  _pre_timestamp = 0;
volatile uint32_t  _cur_timestamp = 0;
volatile uint32_t  _ecgrawidx = 0;
#define timeout    1000

void printf_msg(const char *format,...)
{
    //int ret = 0;
    //int counter = 0;
    va_list args; 
    char buff[256];
    uint16_t len;
    va_start(args, format);
    len = vsprintf((char*)buff, format, args); 
    va_end(args);
#if 0    
    while(1){
      ret = CDC_Transmit_FS(( uint8_t * )&buff[0], len); 
      if(ret == USBD_OK || ++counter>3) break;
      //HAL_Delay(1);
    }
#else
    /* ★T198：改走通道抽象层。`comm_send()` **内部拷贝** ⇒ 这里的栈缓冲 `buff[256]`
     *   可以照旧留在栈上（异步发送不会读到已复用的栈，技能 §4.48/§4.126）。 */
    (void)comm_send((const uint8_t *)&buff[0], len);
#if (ECG_COMM_PORT == ECG_COMM_PORT_USB)
    /* 只有 USB 模式需要这个延时：CDC 单缓冲、无流控，连发时后一帧会覆盖前一帧的
     * 在途缓冲（`CDC_Transmit_FS` 返回 BUSY 即丢）。UART5 模式由 `comm_send()`
     * 自己的忙判 + 丢帧计数接管，**不能再加 5 ms 阻塞** —— 回显一多就把主循环拖垮。 */
    HAL_Delay(5);
#endif
#endif    
}



void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
   if(GPIO_Pin == DRDY_Pin)
   {
       _cur_timestamp = __HAL_TIM_GET_COUNTER(&htim2);
       _ecgrawidx++;
       _bconvint = true;
   }
}

void demo_ads1293_start(void)
{
    _bconvstart = true;
    _pre_timestamp = 0;
    _cur_timestamp = 0;
    _bconvint = 0;
    _ecgrawidx = 0;
    HAL_TIM_Base_Start(&htim2);    
}

void demo_ads1293_stop(void)
{
    _bconvstart = false;
    _pre_timestamp = 0;
    _cur_timestamp = 0;
    _bconvint = 0;
    HAL_TIM_Base_Stop(&htim2);    
}
void demo_ads1293(void)
{

    /* 上电初始化一次 —— 不置 TRCENA，CYCCNT 永远是 0（最常见的坑） */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL   |= DWT_CTRL_CYCCNTENA_Msk;


    printf_msg("demo_ads1290:%d\r\n",sizeof(double));

    // f32_le_t data;
    
    //  while(1){
    //     data.f32 = 3.1415926f;
    //     printf_msg("f32_le_t: %02x %02x %02x %02x\r\n",data.u8[0],data.u8[1],data.u8[2],data.u8[3]);
    //     data.u32 = 50;
    //     printf_msg("u32: %02x %02x %02x %02x\r\n",data.u8[0],data.u8[1],data.u8[2],data.u8[3]);
    //     //printf_msg("demo_ads1290:%d\r\n",sizeof(double));
    //     printf_msg("crc_chk: %d\r\n",ecg_crc8_selftest());
    //     HAL_Delay(2000);
    //  }
    HAL_GPIO_WritePin(ADS_CS_GPIO_Port, ADS_CS_Pin, GPIO_PIN_SET);
    
    ads1293_raw_queueinit();

    /* ★T207(2026-11-04)：上电就把默认表 36 条写进 AFE，让寄存器从一上电就是**已知态**。
     *   🛑 没有这一步时：ADS1293 停在 POR 默认值（它不吃 MCU 的 NRST ⇒ 只有整板断电才回 POR）
     *      ⇒ 上位机 `ConfigValidator.CanStart`（H 组 11 条全一致）恒 false
     *      ⇒ `Form1.cs:1050` 直接弹「禁止 Start」、一条下行都不发
     *      ⇒ 现场表现就是「上电后必须先勾实验写点同步，才能正常 START」。
     *   ⚠ 这一步**只此一处**：命令路径 `0x01`/`0x03` 各自仍内建写表，互不依赖。
     *   ⚠ 返回 0 = 全表回读一致；非 0 = 首个坏项下标+1（诊断见 `ads1293_wt_fail_*()`）。
     *     上电无帧流 ⇒ 这里打一行回显不丢任何样本。 */
    {
#if ECG_BOOT_AFE_INIT                       /* ★T207 开关，0 = 阴性对照（旧行为） */
        int afe_boot = ads1293_boot_init();
        /* ★T207b（11-04 实测）：上电阶段 `comm_send()` 是**异步 + 忙判丢弃**
         *   （`comm_port.c:196` `s_tx_busy != 0` ⇒ `comm_tx_drops++` 后直接 return）。
         *   `comm_init()` 末尾那条 42 B 横幅在 115200 下要发 **≈3.65 ms**，
         *   而本函数紧跟着就在这 3.65 ms 内打印 ⇒ **整行被静默丢掉**。
         *   实测现象：复位后只能看到 `[uart5] ready ...`，看不到 `afe boot init:0`。
         *   ⇒ 打印前先等 TX 空闲（上电阶段无帧流，忙等上限 20 ms 零代价）。 */
        {
            uint32_t wait_start = HAL_GetTick();
            while (comm_tx_busy() && (HAL_GetTick() - wait_start) < 20u) {
                /* 等横幅发完；超时也照打（宁可丢这行，不可卡住启动）*/
            }
        }
        printf_msg("afe boot init:%d\r\n", afe_boot);
#endif
    }
    
    while(1)
    {
        vcom_recv_analysis();
        ads1293_lod_poll();      /* ★T232：LOD 低频轮询（内部 20 ms 自节流，未到周期立即返回） */
       
        bool bconvstart = _bconvstart;
        bool bconvint = _bconvint;
       
        if(bconvstart)
        {
            if(bconvint){
              _bconvint = false;
              ads1293_raw_enqueue(_ecgrawidx);
            }
            ads1293_raw_popoutput();
        }

        
    }
}