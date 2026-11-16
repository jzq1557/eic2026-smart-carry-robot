/**
  ******************************************************************************
  * @file    motion.h
  * @brief   校赛扩展：**动作组解释器**（步骤表由上位机下发，下位机只按表执行）
  *          协议：0x21 建步（子类型 0=清空 1=步头 2=补脉冲 3=提交）/ 0x24 执行整组
  *          设计见 校赛开发.md §3.2 / §3.3 / §5.3
  ******************************************************************************
  */
#ifndef __MOTION_H
#define __MOTION_H

#include "stm32f10x.h"

#define MOTION_MAX_GROUPS   8u      /* 可下发的动作组数(上位机 motion_profile.json 的组数上限) */
#define MOTION_MAX_STEPS    12u     /* 每组最多步骤数(够覆盖 下探+夹+抬+转 这类组合) */

/* 单步等待到位的超时(ms)：龙门架大行程最慢，给足；超时也会继续下一步(不卡死整组) */
#define MOTION_STEP_TIMEOUT_MS  15000u

/* 步骤类型（与上位机 lower_api.STEP_KIND 一致，别只改一边） */
#define STEP_AXIS        1u         /* 走闭环电机：axis/dir/pulses/vel */
#define STEP_GRIP_CLOSE  2u
#define STEP_GRIP_OPEN   3u
#define STEP_WAIT        4u         /* pulses 当毫秒用 */

void    Motion_Init(void);
void    Motion_Clear(uint8_t gid);                                     /* 0x21 子类型 0 */
uint8_t Motion_SetHead(uint8_t gid, uint8_t idx, uint8_t kind,
                       uint8_t axis, uint8_t dir, uint8_t vel);        /* 0x21 子类型 1 */
uint8_t Motion_SetPulses(uint8_t gid, uint8_t idx, uint32_t pulses);   /* 0x21 子类型 2 */
uint8_t Motion_Commit(uint8_t gid, uint8_t n);                         /* 0x21 子类型 3 */
uint8_t Motion_Run(uint8_t gid, uint8_t slot);                         /* 0x24（阻塞到整组完成）*/

#endif /* __MOTION_H */
