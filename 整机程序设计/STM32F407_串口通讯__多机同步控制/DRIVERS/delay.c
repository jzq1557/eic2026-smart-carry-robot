#include "delay.h"

/**
	* SystemCoreClock/1000000     1us中断一次
	* SystemCoreClock/100000			10us中断一次
	* SystemCoreClock/1000				1ms中断一次
	*/
void delay_us(__IO uint16_t us)
{
	uint16_t i;

	SysTick_Config(SystemCoreClock / 1000000);		/* 1us中断一次 */

	for(i=0; i < us; i++)
	{
		while(!((SysTick -> CTRL) & (1 << 16)));		/* 当计数器的值减为0，CTRL的bit16会置1，读取该位将自动清零 */
	}	 
	 
	SysTick -> CTRL &= ~SysTick_CTRL_ENABLE_Msk;	/* 关闭SysTick定时器 */
}

/**
	* SystemCoreClock/1000000     1us中断一次
	* SystemCoreClock/100000			10us中断一次
	* SystemCoreClock/1000				1ms中断一次
	*/
void delay_ms(__IO uint16_t ms)
{
	uint16_t i;

	SysTick_Config(SystemCoreClock / 1000);				/* 1ms中断一次 */

	for(i=0; i < ms; i++)
	{
		while(!((SysTick -> CTRL) & (1 << 16)));		/* 当计数器的值减为0，CTRL的bit16会置1，读取该位将自动清零 */
	}	 
	 
	SysTick -> CTRL &= ~SysTick_CTRL_ENABLE_Msk;	/* 关闭SysTick定时器 */
}
