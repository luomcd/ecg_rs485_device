#include "main.h"
#include "stdarg.h"
#include "stdlib.h"
#include "stdio.h"
#include "stdbool.h"
#include "usbd_cdc_if.h"
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
    CDC_Transmit_FS(( uint8_t * )&buff[0], len); 
    HAL_Delay(5);
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
    
    while(1)
    {
        vcom_recv_analysis();
       
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