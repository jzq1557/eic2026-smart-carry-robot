/**********************************************************************
 * direction_check.h —— 底盘"程序方向 ↔ 实际车向"校准 demo
 *
 * 用法: 26090901TEST 上电后若定义了 RUN_DIRECTION_DEMO, 会先跑本 demo:
 *   - 自动顺序执行(无任何按键): 前进 → 后退 → 左移 → 右移 → 左旋 → 右旋
 *   - 每步之间自动停顿(DCHK_GAP_MS)便于观察; 按 DCHK_CYCLES 轮数循环
 * 观察并记录"实际车辆运动方向", 与 chassis.h 期望方向比对后修正
 * 方向表(DIR_*)或 dir_pins[] 的 invert 位(见 direction_check.c 顶部说明)。
 **********************************************************************/
#ifndef DIRECTION_CHECK_H
#define DIRECTION_CHECK_H

void Direction_Check_Run(void);   /* 阻塞执行(自动循环); 跑完 DCHK_CYCLES 轮后返回 */

#endif
