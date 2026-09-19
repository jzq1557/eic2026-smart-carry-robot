/**********************************************************************
 * ramp.h —— 通用步进电机梯形加减速引擎(AVR446 算法)
 *
 * 设计:一套代码驱动任意多根"轴"。每根轴 = 一个定时器 + 一张任务单。
 *      本车三根轴:底盘(TIM3) / 升降(TIM2) / 载物台(TIM4)。
 *
 * 用法:
 *      Ramp_Start(&axis_chassis, 6500, 28, 28, 56);  // 非阻塞,填单开跑
 *      Ramp_WaitDone(&axis_chassis);                 // 需要等就调这句
 * 中断:
 *      TIMx_IRQHandler 里只写一行 Ramp_OnTick(&axis_xxx);
 *********************************************************************/
#ifndef RAMP_H
#define RAMP_H

#include "stm32f10x.h"

/* 速度曲线四状态 */
typedef enum { RAMP_STOP = 0, RAMP_ACCEL, RAMP_RUN, RAMP_DECEL } RampState;

/* 一根轴的全部家当(原来的 srd + stepPosition + distance 合体) */
typedef struct {
    TIM_TypeDef *tim;                  /* 这根轴挂在哪个定时器上      */
    uint8_t      use_4ch;              /* 1=写 CCR1~4(底盘) 0=只写 CCR3/4 */

    /* --- 任务单(MSD 填,中断读写) --- */
    volatile RampState state;
    volatile uint32_t  step_delay;     /* 下一步的间隔 us             */
    uint32_t           min_delay;      /* 天花板速度对应的最小间隔    */
    uint32_t           decel_start;    /* 第几步开始踩刹车            */
    int32_t            decel_val;      /* 减速步数(存负,兼做沙漏)  */
    int32_t            accel_count;    /* 加/减速计数 n               */

    /* --- 中断的私账 --- */
    uint32_t step_count;               /* 本次任务走了几步            */
    int32_t  rest;                     /* 整数除法找零口袋            */
    uint32_t last_accel_delay;         /* 加速末步间隔(平顶接力用)  */

    /* --- 平滑减速(2026-09-09 方案B:线性拉长间隔,末端慢速蠕停) --- */
    uint32_t decel_len;                /* 减速段总步数 B(>0)          */
    uint32_t decel_tick;               /* 减速段当前已走步数 k        */
    uint32_t decel_d0;                 /* 进入减速时的脉冲间隔(起点)  */

    /* --- 对外可见的里程计 --- */
    volatile int32_t position;         /* 有符号累计位置(原 stepPosition) */
    int8_t  position_sign;             /* 本次运动方向:+1 / -1            */
} StepAxis;

/* 本车三根轴,定义在 ramp.c */
extern StepAxis axis_chassis;   /* TIM3:底盘 4 轮  */
extern StepAxis axis_lift;      /* TIM2:爪子升降   */
extern StepAxis axis_tray;      /* TIM4:载物台旋转 */

/* 启动一段运动(非阻塞:填完任务单、开定时器就返回) */
void Ramp_Start(StepAxis *a, int32_t steps,
                uint32_t accel, uint32_t decel, uint32_t speed);

/* 阻塞等待该轴走完(等状态机回到 STOP,比数里程更稳) */
void Ramp_WaitDone(StepAxis *a);

/* 该轴是否空闲 */
uint8_t Ramp_IsIdle(StepAxis *a);

/* 中断服务的共用主体,由各 TIMx_IRQHandler 调用 */
void Ramp_OnTick(StepAxis *a);

#endif
