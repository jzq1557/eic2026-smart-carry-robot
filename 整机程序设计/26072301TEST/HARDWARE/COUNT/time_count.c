#include "time_count.h"

/* 舵机角度全局变量(原定义于 control.c,现迁至此处以消除依赖)
 * CLEAN/claw.c 通过 SERVO2/3_CONTROL 直接写 PWM,此处供 TIM6 周期刷新 */
uint8_t servo_angle2 = 0;   /* 爪子: 0=张开, 95=夹紧                */
uint8_t servo_angle3 = 60;  /* 腕关节: 240=朝前, 60=朝后           */

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
		SERVO2_CONTROL(servo_angle2);
		SERVO3_CONTROL(servo_angle3);
	}
	TIM_ClearITPendingBit(TIM6,TIM_IT_Update);
}
