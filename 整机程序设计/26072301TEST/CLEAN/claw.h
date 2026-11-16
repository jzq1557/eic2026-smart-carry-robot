/**********************************************************************
 * claw.h / claw.c —— 抓取机构:两只舵机 + 一根升降轴 + 载物台
 *
 * 改进:
 *  ① 升降从"相对位移手工记账"改为"绝对高度自动换算"
 *     —— 报目的地,不报路程,永不算错账
 *  ② 所有角度/高度用命名常量(car_config.h)
 *********************************************************************/
#ifndef CLAW_H
#define CLAW_H
#include <stdint.h>

void Claw_Init(void);

/* --- 舵机原语(瞬发,不阻塞;机械到位约需 200~500ms) --- */
void Grip_Open(void);
void Grip_Close(void);
void Wrist_Front(void);
void Wrist_Back(void);

/* --- 升降(阻塞;单位:绝对高度"步",用 LIFT_xxx 常量) --- */
void Lift_GoTo(int32_t target_height);
void Lift_GoTo_Async(int32_t target_height);   /* 复合动作用 */
void Lift_WaitDone(void);
void Lift_MoveBy(int16_t delta_steps);

/* --- 载物台 --- */
void Tray_Turn120(void);

/* --- 组合动作(对应协议 0x07~0x0A) --- */
void Claw_GrabFromTray(void);
void Claw_PutLayer1(void);

#endif
