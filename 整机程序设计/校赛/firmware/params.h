/**
  ******************************************************************************
  * @file    params.h
  * @brief   校赛扩展：**下位机参数表**（一切速度/脉冲/加速度都由上位机下发）
  *          设计见 校赛开发.md §3.2 / §5.3 —— 下位机只留"空函数接口 + 参数表"，
  *          不再有 4400/1700/6400 这类散落的魔数。
  *          上电默认值只是"没有上位机时也能动"的兜底；上位机 sync 之后一律用下发的值。
  ******************************************************************************
  */
#ifndef __PARAMS_H
#define __PARAMS_H

#include "stm32f10x.h"

/* ---- 参数 ID（与上位机 lower_api.py 的 P{} 表一一对应，别只改一边）---- */
#define P_CHASSIS_VEL            0x0001u
#define P_CHASSIS_ACC            0x0002u
#define P_CHASSIS_DEC            0x0003u
#define P_TTL_DEFAULT_VEL        0x0010u
#define P_GRIP_CLOSE_ANGLE       0x0011u  /* 夹爪是舵机: 闭合角度 0~255 */
#define P_GRIP_OPEN_ANGLE        0x0012u  /* 夹爪是舵机: 张开角度 0~255 */
#define P_BASE_LEFT90_PULSES     0x0020u
#define P_BASE_RIGHT90_PULSES    0x0021u
#define P_LIFT_UP_PULSES         0x0022u  /* 龙门架抬升(id2/id3 反向传动) */
#define P_LIFT_DOWN_PULSES       0x0023u  /* 龙门架下放(id2/id3 反向传动) */
#define P_TELESCOPE_OUT_PULSES   0x0024u  /* 伸缩滑台(id1)前伸 */
#define P_TELESCOPE_IN_PULSES    0x0025u  /* 伸缩滑台(id1)收回 */
#define P_AXES_HOME_SPEED        0x0030u

/* 参数表容量（够用即可；新增参数只改 params.c 的默认表 + 这里不一定要加宏） */
#define PARAM_COUNT              16u

void     Params_Reset(void);                       /* 全部恢复默认 */
uint8_t  Params_Set(uint16_t id, uint32_t v);      /* 0x20：成功=1 */
uint8_t  Params_Get(uint16_t id, uint32_t *v);     /* 0x22：成功=1 */
uint32_t Params_U32(uint16_t id);                  /* 内部取参（找不到返回 0） */
void     Params_Save(void);                        /* 0x23：存 Flash（见 .c 里的说明） */
void     Params_Load(void);                        /* 上电时调用：尝试从 Flash 读回 */

#endif /* __PARAMS_H */
