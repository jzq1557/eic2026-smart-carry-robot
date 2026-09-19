#include "servo.h"

/**********************************************************************
 * 修复说明(2026-08):
 *   原版 PSC=7200-1、ARR=200-1 → PWM 最小分辨率 100us ≈ 13.5°/格,
 *   且换算 0.074*angle+5 强制截断小数,导致:
 *     写120°实际输出≈108°(夹爪过冲12°→打滑、齿轮错位)
 *     写210°实际输出≈203°、240°→230°、60°→54°
 *   现改为 PSC=72-1(1MHz计数,1us/格)、ARR=20000-1(20ms周期50Hz),
 *   分辨率提升到 0.135°/格;换算按 270°舵机 500~2500us 精确映射并四舍五入:
 *     脉宽us = 500 + angle × (2000/270) = 500 + 7.4074×angle
 *   config.h 中的角度值(120/210/240/60)无需改动,此后所写即所得。
 *********************************************************************/

#define SERVO_PSC        (72-1)      /* 72MHz/72 = 1MHz,1us一格          */
#define SERVO_ARR        (20000-1)   /* 20000us = 20ms,标准舵机50Hz周期  */
#define SERVO_US_PER_DEG 7.4074f     /* 2000us / 270°                     */
#define SERVO_MIN_US     500.0f      /* 0°对应脉宽                        */

/* 角度→CCR(单位us),四舍五入 */
static uint16_t servo_angle_to_ccr(uint8_t angle)
{
	return (uint16_t)(SERVO_US_PER_DEG * angle + SERVO_MIN_US + 0.5f);
}

//TIM5_CH2    PA1    servo1  Y

/********************************
函数功能 : 舵机1的初始化
输入参数 : 无
输出参数 ；无
*********************************/
void Servo1_Init(void)
{
	GPIO_InitTypeDef GPIO_InitStructure;
	TIM_OCInitTypeDef TIM_OCInitStructure;
	TIM_TimeBaseInitTypeDef TIM_TimeBaseInitStructure;
	
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA,ENABLE);
	RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM5,ENABLE);
	
	GPIO_InitStructure.GPIO_Mode=GPIO_Mode_AF_PP;
	GPIO_InitStructure.GPIO_Pin=GPIO_Pin_1;
	GPIO_InitStructure.GPIO_Speed=GPIO_Speed_50MHz;
	GPIO_Init(GPIOA,&GPIO_InitStructure);

	TIM_TimeBaseInitStructure.TIM_ClockDivision=TIM_CKD_DIV1;
	TIM_TimeBaseInitStructure.TIM_CounterMode=TIM_CounterMode_Up;
	TIM_TimeBaseInitStructure.TIM_Period=SERVO_ARR;
	TIM_TimeBaseInitStructure.TIM_Prescaler=SERVO_PSC;
	TIM_TimeBaseInitStructure.TIM_RepetitionCounter=0;
	TIM_TimeBaseInit(TIM5,&TIM_TimeBaseInitStructure);
	
	TIM_OCStructInit(&TIM_OCInitStructure);
	TIM_OCInitStructure.TIM_OCMode=TIM_OCMode_PWM1;
	TIM_OCInitStructure.TIM_OCPolarity=TIM_OCPolarity_High;
	TIM_OCInitStructure.TIM_OutputState=TIM_OutputState_Enable;
	TIM_OCInitStructure.TIM_Pulse=0;
	TIM_OC2Init(TIM5,&TIM_OCInitStructure);
	
	TIM_OC2PreloadConfig(TIM5,ENABLE);
	TIM_ARRPreloadConfig(TIM5,ENABLE);
	
	TIM_Cmd(TIM5,ENABLE);
}
//TIM5_CH3    PA2    servo2  Y
/********************************
函数功能 : 舵机2的初始化(爪子)
输入参数 : 无
输出参数 ；无
*********************************/
void Servo2_Init(void)
{
	GPIO_InitTypeDef GPIO_InitStructure;
	TIM_OCInitTypeDef TIM_OCInitStructure;
	TIM_TimeBaseInitTypeDef TIM_TimeBaseInitStructure;
	
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA,ENABLE);
	RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM5,ENABLE);
	
	GPIO_InitStructure.GPIO_Mode=GPIO_Mode_AF_PP;
	GPIO_InitStructure.GPIO_Pin=GPIO_Pin_2;
	GPIO_InitStructure.GPIO_Speed=GPIO_Speed_50MHz;
	GPIO_Init(GPIOA,&GPIO_InitStructure);
	
	TIM_TimeBaseInitStructure.TIM_ClockDivision=TIM_CKD_DIV1;
	TIM_TimeBaseInitStructure.TIM_CounterMode=TIM_CounterMode_Up;
	TIM_TimeBaseInitStructure.TIM_Period=SERVO_ARR;
	TIM_TimeBaseInitStructure.TIM_Prescaler=SERVO_PSC;
	TIM_TimeBaseInitStructure.TIM_RepetitionCounter=0;
	TIM_TimeBaseInit(TIM5,&TIM_TimeBaseInitStructure);
	
	TIM_OCStructInit(&TIM_OCInitStructure);
	TIM_OCInitStructure.TIM_OCMode=TIM_OCMode_PWM1;
	TIM_OCInitStructure.TIM_OCPolarity=TIM_OCPolarity_High;
	TIM_OCInitStructure.TIM_OutputState=TIM_OutputState_Enable;
	TIM_OCInitStructure.TIM_Pulse=0;
	TIM_OC3Init(TIM5,&TIM_OCInitStructure);
	
	TIM_OC3PreloadConfig(TIM5,ENABLE);
	TIM_ARRPreloadConfig(TIM5,ENABLE);
	
	TIM_Cmd(TIM5,ENABLE);
}
//TIM5_CH4    PA3    servo3  Y
/********************************
函数功能 : 舵机3的初始化(腕关节)
输入参数 : 无
输出参数 ；无
*********************************/
void Servo3_Init(void)
{
	GPIO_InitTypeDef GPIO_InitStructure;
	TIM_OCInitTypeDef TIM_OCInitStructure;
	TIM_TimeBaseInitTypeDef TIM_TimeBaseInitStructure;
	
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA,ENABLE);
	RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM5,ENABLE);
	
	GPIO_InitStructure.GPIO_Mode=GPIO_Mode_AF_PP;
	GPIO_InitStructure.GPIO_Pin=GPIO_Pin_3;
	GPIO_InitStructure.GPIO_Speed=GPIO_Speed_50MHz;
	GPIO_Init(GPIOA,&GPIO_InitStructure);
	
	TIM_TimeBaseInitStructure.TIM_ClockDivision=TIM_CKD_DIV1;
	TIM_TimeBaseInitStructure.TIM_CounterMode=TIM_CounterMode_Up;
	TIM_TimeBaseInitStructure.TIM_Period=SERVO_ARR;
	TIM_TimeBaseInitStructure.TIM_Prescaler=SERVO_PSC;
	TIM_TimeBaseInitStructure.TIM_RepetitionCounter=0;
	TIM_TimeBaseInit(TIM5,&TIM_TimeBaseInitStructure);
	
	TIM_OCStructInit(&TIM_OCInitStructure);
	TIM_OCInitStructure.TIM_OCMode=TIM_OCMode_PWM1;
	TIM_OCInitStructure.TIM_OCPolarity=TIM_OCPolarity_High;
	TIM_OCInitStructure.TIM_OutputState=TIM_OutputState_Enable;
	TIM_OCInitStructure.TIM_Pulse=0;
	TIM_OC4Init(TIM5,&TIM_OCInitStructure);
	
	TIM_OC4PreloadConfig(TIM5,ENABLE);
	TIM_ARRPreloadConfig(TIM5,ENABLE);
	
	TIM_Cmd(TIM5,ENABLE);
}

/********************************
函数功能 : 舵机1的控制函数
输入参数 : 角度(0~270)
输出参数 ；无
*********************************/
void SERVO1_CONTROL(uint8_t angle)
{
	TIM_SetCompare2(TIM5, servo_angle_to_ccr(angle));
}
/********************************
函数功能 : 舵机2的控制函数(爪子)
输入参数 : 角度(0~270)
输出参数 ；无
*********************************/
void SERVO2_CONTROL(uint8_t angle)
{
	TIM_SetCompare3(TIM5, servo_angle_to_ccr(angle));
}
/********************************
函数功能 : 舵机3的控制函数(腕关节)
输入参数 : 角度(0~270)
输出参数 ；无
*********************************/
void SERVO3_CONTROL(uint8_t angle)
{
	TIM_SetCompare4(TIM5, servo_angle_to_ccr(angle));
}
