#include "time_count.h"

void TIM6_COUNT_Init(void)  // 1s��ʱ���ж�
{
	TIM_TimeBaseInitTypeDef TIM_TimeBaseStructure;
	NVIC_InitTypeDef NVIC_InitStructure;
	
	RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM6,ENABLE);
	// 72 000 000/ (7200)
	TIM_TimeBaseStructure.TIM_ClockDivision=TIM_CKD_DIV1;
	TIM_TimeBaseStructure.TIM_CounterMode=TIM_CounterMode_Up;
	TIM_TimeBaseStructure.TIM_Period=7200-1;
	TIM_TimeBaseStructure.TIM_Prescaler=10000-1;
	TIM_TimeBaseInit(TIM6,&TIM_TimeBaseStructure);
	
	NVIC_InitStructure.NVIC_IRQChannel=TIM6_IRQn;
	NVIC_InitStructure.NVIC_IRQChannelCmd=ENABLE;
	NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority=2;
	NVIC_InitStructure.NVIC_IRQChannelSubPriority=2;
	NVIC_Init(&NVIC_InitStructure);
	
	TIM_ITConfig(TIM6,TIM_IT_Update,ENABLE);
	
	TIM_ClearITPendingBit(TIM6,TIM_IT_Update);
	
	TIM_Cmd(TIM6,ENABLE);
}

void TIM6_IRQHandler(void)
{
	static uint8_t flag = 0;
	if(TIM_GetITStatus(TIM6,TIM_IT_Update))
	{
		flag=!flag;
//		LED0=flag;
//	SERVO2_CONTROL(servo_angle2);  /* v8删除:全局变量无人更新,每秒把夹爪拽回0度,是打滑真凶 */
//	SERVO3_CONTROL(servo_angle3);  /* v8删除:同上,TIM5硬件PWM本就持续输出,无需软件刷新 */
	}
	TIM_ClearITPendingBit(TIM6,TIM_IT_Update);
}
