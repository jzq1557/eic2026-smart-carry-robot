/**********************************************************************
 * TTLctrl.h —— TTL 串口控制四路 Emm_V5.0 闭环电机(原 Demo3 更名)
 *
 * 通道:USART3 部分重映射 PC10(TX)/PC11(RX),115200
 * 电机:ID 1=伸缩滑台  2/3=龙门架(镜像)  4=base轴
 *
 * ★ 全部为"非阻塞"控制:发出 TTL 串口命令后立即返回,
 *   不等待电机执行完毕(运动由驱动器自行完成)。
 *   距离、速度、方向均为函数参数,调用时按需调整。
 *
 * 单位约定:
 *   dir   : 0=CW(正转)  其余=CCW(反转)
 *   vel   : RPM,范围 0-5000
 *   clk   : 脉冲数,3200 = 1 圈(16 细分)
 *********************************************************************/
#ifndef __TTLCTRL_H
#define __TTLCTRL_H

#include <stdint.h>

/* 方向常量(与 Emm_V5 协议一致) */
#define TTL_DIR_CW    0
#define TTL_DIR_CCW   1

/* 一圈脉冲数(16 细分),换算用 */
#define TTL_CLK_PER_REV   3200

void TTLctrl_Init(void);                /* USART3 初始化 + 上电延时 + 使能四路电机 */

/* ---- 非阻塞控制函数(发完即返回) ---- */

/* id1 伸缩滑台:dir 传方向(0=正转→前伸),vel 速度,clk 距离 */
void TTLctrl_Motor1(uint8_t dir, uint16_t vel, uint32_t clk);

/* id2/id3 龙门架(打包):dir 传龙门架运动方向,内部自动令
 *   id2=dir、id3=反向(镜像互反),两轴经多机同步"同时启停" */
void TTLctrl_Motor23(uint8_t dir, uint16_t vel, uint32_t clk);

/* id4 base轴:dir 传方向(0=正转→逆时针),vel 速度,clk 距离 */
void TTLctrl_Motor4(uint8_t dir, uint16_t vel, uint32_t clk);

/* ---- 组合动作组(基于非阻塞原语,内部按序等待完成) ---- */

/* 动作组:抬起龙门架 → 伸缩臂向后 → base轴顺时针半圈(慢速)
 *   - 抬起龙门架:Motor23 反向(dir=1),速度/圈数可调
 *   - 伸缩臂向后:Motor1 反转(dir=1)
 *   - base 顺时针:Motor4 反转(dir=1),慢速(默认 50RPM)、半圈(1600 脉冲)
 * 内部用 TTLctrl_WaitDone 按耗时估算等待每步完成,阻塞直至整组结束 */
void TTLctrl_Act_RaiseRetractCw(void);

/* 放料动作组(瞄准圆心后调用):
 *   龙门架下放(TTL_PLACE_LOWER_*) → 开爪 → 抬升回原位
 *   内部阻塞等待每步到位,整组完成后返回 */
void TTLctrl_Act_Place(void);

/* 去车载物架夹料动作组(协议 0x16,★手调区):
 *   从靶心位出发去车上物架抓料,结束时须回到观察位(与瞄准起点一致)
 *   函数体现在为 2026-09-03 填的手调占位参数(3400/1800/6300/800…),
 *   上车实测后定稿;测试动作组前务必先架空/抬离执行机构 */
void TTLctrl_Act_GrabFromVehicle(void);

/* 按"速度×脉冲数"估算单轴运动完成耗时(ms),用于非阻塞命令后的等待 */
void TTLctrl_WaitDone(uint16_t vel, uint32_t clk);

/* ---- 到位交互(驱动器 Response 菜单需开启 Reached 到位返回) ----
 * 电机运动到位后,驱动器自动回帧:[地址] 0xFD 0x9F 0x6B,
 * USART3 RX 中断解析到后置位到位标志。可用于"动作完成后再延时"的精确同步。 */

/* 注册到位回调:某电机到位时在中断上下文中调用 hook(addr)。
 * ★ 回调内只宜置标志/极短操作,勿做长延时(在中断里执行) */
void TTLctrl_SetReachedHook(void (*hook)(uint8_t addr));

/* 阻塞等待 addr 电机到位(超时 timeout_ms,0 表示默认 10s)。
 * 返回 1 = 已到位, 0 = 超时 */
uint8_t TTLctrl_WaitReached(uint8_t addr, uint32_t timeout_ms);

/* 非阻塞查询 addr 电机是否已到位(1=是,0=否),查询后不清标志 */
uint8_t TTLctrl_IsReached(uint8_t addr);

/* 清除到位标志(发命令前调用,避免旧标志干扰本次等待) */
void TTLctrl_ClearReached(uint8_t addr);

#endif
