/**********************************************************************
 * chassis.h / 底盘运动模块
 *********************************************************************/
#ifndef CHASSIS_H
#define CHASSIS_H
#include <stdint.h>

void Chassis_Init(void);                 /* 调用原 MOTOR3/4/7/8_Init      */

/* 全部为阻塞接口:函数返回 = 动作完成(对应协议的同步应答模型) */
void Chassis_MoveX_cm(int8_t cm);        /* 正=前进 负=后退               */
void Chassis_MoveY_cm(int8_t cm);        /* 正=左移 负=右移               */
void Chassis_MoveX_mm(int8_t mm);
void Chassis_MoveY_mm(int8_t mm);
void Chassis_Rotate_deg(int8_t deg);     /* 正=逆时针                     */

/* 非阻塞版(0x0D 复合动作用):启动后立即返回 */
void Chassis_MoveX_cm_Async(int8_t cm);
void Chassis_WaitDone(void);

#endif
