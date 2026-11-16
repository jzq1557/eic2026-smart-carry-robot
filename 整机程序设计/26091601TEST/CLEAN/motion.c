/**
  ******************************************************************************
  * @file    motion.c
  * @brief   校赛扩展：动作组解释器实现（步骤表由上位机 0x21 下发，0x24 触发执行）
  *
  *  下位机在这里**只做解释**：速度/脉冲/加速度全来自上位机 motion_profile.json，
  *  本文件不含任何"业务数字"。
  *
  *  ★钩子用的是本工程**实际的**函数名（已核对 TTLctrl.h / delay.h，2026-09-16）：
  *      TTLctrl_Motor1/Motor23/Motor4(dir, vel, clk)  —— 非阻塞发命令
  *      TTLctrl_WaitReached(addr, timeout_ms)         —— 等到位（addr: 2/3 都看 id2）
  *      Grip_Close() / Grip_Open()
  *      delay_ms(u16)
  *    换工程时若函数名不同，只改下面 extern 与 Motion_Run 里那几行。
  ******************************************************************************
  */
#include "motion.h"
#include "params.h"
#include "sys.h"
#include "delay.h"

/* ---- 与现有固件对接的钩子（名字已按 TTLctrl.h / delay.h 核对）---- */
extern void    TTLctrl_Motor1(uint8_t dir, uint16_t vel, uint32_t clk);
extern void    TTLctrl_Motor23(uint8_t dir, uint16_t vel, uint32_t clk);
extern void    TTLctrl_Motor4(uint8_t dir, uint16_t vel, uint32_t clk);
extern uint8_t TTLctrl_WaitReached(uint8_t addr, uint32_t timeout_ms);
extern void    TTLctrl_ClearReached(uint8_t addr);
extern void    Grip_Close(void);
extern void    Grip_Open(void);
extern void    delay_ms(u16 nms);

typedef struct {
    uint8_t  kind;
    uint8_t  axis;
    uint8_t  dir;
    uint8_t  vel;
    uint32_t pulses;
} MotionStep;

static MotionStep s_steps[MOTION_MAX_GROUPS][MOTION_MAX_STEPS];
static uint8_t    s_n[MOTION_MAX_GROUPS];        /* 每组已提交步骤数 */

void Motion_Init(void)
{
    uint8_t g, i;
    for (g = 0; g < MOTION_MAX_GROUPS; g++) {
        s_n[g] = 0u;
        for (i = 0; i < MOTION_MAX_STEPS; i++) {
            s_steps[g][i].kind = 0u;
            s_steps[g][i].axis = 0u;
            s_steps[g][i].dir = 0u;
            s_steps[g][i].vel = 0u;
            s_steps[g][i].pulses = 0u;
        }
    }
}

void Motion_Clear(uint8_t gid)
{
    if (gid < MOTION_MAX_GROUPS) { s_n[gid] = 0u; }
}

uint8_t Motion_SetHead(uint8_t gid, uint8_t idx, uint8_t kind,
                       uint8_t axis, uint8_t dir, uint8_t vel)
{
    if (gid >= MOTION_MAX_GROUPS || idx >= MOTION_MAX_STEPS) { return 0u; }
    s_steps[gid][idx].kind = kind;
    s_steps[gid][idx].axis = axis;
    s_steps[gid][idx].dir  = dir;
    s_steps[gid][idx].vel  = vel;
    return 1u;
}

uint8_t Motion_SetPulses(uint8_t gid, uint8_t idx, uint32_t pulses)
{
    if (gid >= MOTION_MAX_GROUPS || idx >= MOTION_MAX_STEPS) { return 0u; }
    s_steps[gid][idx].pulses = pulses;
    return 1u;
}

uint8_t Motion_Commit(uint8_t gid, uint8_t n)
{
    if (gid >= MOTION_MAX_GROUPS || n > MOTION_MAX_STEPS) { return 0u; }
    s_n[gid] = n;
    return 1u;
}

/* 把一步的轴号翻译成实际电机调用；axis 2/3 都走龙门架打包函数 */
static void RunAxisStep(uint8_t axis, uint8_t dir, uint32_t pulses, uint16_t vel)
{
    if (axis == 1u) {
        TTLctrl_Motor1(dir, vel, pulses);
    } else if (axis == 4u) {
        TTLctrl_Motor4(dir, vel, pulses);
    } else {                               /* 2 / 3 = 龙门架（内部镜像互反） */
        TTLctrl_Motor23(dir, vel, pulses);
    }
    /* 等这一步真正到位再走下一步 —— 龙门架看 id2 的到位帧。
     * 运动到位帧要求驱动器 Response 菜单里开了 "Reached" 回传；
     * 超时也不再等(整组继续)，避免一个轴卡住把整组拖死。 */
    TTLctrl_WaitReached((axis == 1u) ? 1u : ((axis == 4u) ? 4u : 2u),
                        MOTION_STEP_TIMEOUT_MS);
}

/**
  * @brief  执行整组（阻塞：整组跑完才返回，随后由协议层回 ACK）
  * @param  slot  兼容原 0x16 的"物架位"语义，保留给"按位微调"用；当前不影响步骤执行
  * @retval 1=执行完 0=组不存在/空组
  */
uint8_t Motion_Run(uint8_t gid, uint8_t slot)
{
    uint8_t i, n;
    uint16_t vel;

    (void)slot;
    if (gid >= MOTION_MAX_GROUPS) { return 0u; }
    n = s_n[gid];
    if (n == 0u) { return 0u; }

    for (i = 0u; i < n; i++) {
        MotionStep *st = &s_steps[gid][i];
        vel = (st->vel != 0u) ? (uint16_t)st->vel
                              : (uint16_t)Params_U32(P_TTL_DEFAULT_VEL);
        switch (st->kind) {
        case STEP_AXIS:
            TTLctrl_ClearReached((st->axis == 1u) ? 1u : ((st->axis == 4u) ? 4u : 2u));
            RunAxisStep(st->axis, st->dir, st->pulses, vel);
            break;
        case STEP_GRIP_CLOSE:
            Grip_Close();
            delay_ms(300);                 /* 爪子闭合需要时间，给个稳妥间隔 */
            break;
        case STEP_GRIP_OPEN:
            Grip_Open();
            delay_ms(300);
            break;
        case STEP_WAIT:
            delay_ms((u16)(st->pulses > 60000u ? 60000u : st->pulses));
            break;
        default:
            break;
        }
    }
    return 1u;
}
