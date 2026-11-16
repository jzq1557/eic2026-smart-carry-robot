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

#endif
