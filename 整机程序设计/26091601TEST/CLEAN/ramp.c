/**********************************************************************
 * ramp.c —— 通用梯形加减速引擎实现
 *
 * 原代码的 MSD_Move / MSD_Move1 / MSD_Move2 + 三个中断 + 三个计数器
 * 在这里合并为:一个 Ramp_Start + 一个 Ramp_OnTick,轴当参数传。
 *
 * 修复的老 bug:
 *  ① 点火即写真实首步间隔(消灭原版 ARR=100 那个"意外小步")
 *  ② STOP 分支不再把垃圾 new_step_delay 写回任务单
 *  ③ 等待条件改为状态机 STOP(WaitDone),不再依赖里程恰好相等
 *********************************************************************/
#include "system_init.h"
#include "ramp.h"
#include <math.h>

/* ---------- AVR446 常量(与原工程一致) ---------- */
/* 旧的 control.h 里定义了同名宏,通过 system_init.h 间接引入 —— 先抹掉重定 */
#undef T1_FREQ
#undef SPR
#undef ALPHA
#undef A_T_x100
#undef T1_FREQ_148
#undef A_SQ
#undef A_x20000

#define T1_FREQ      1000000UL                    /* 定时器计数频率 1MHz  */
#define SPR          3200UL                       /* 每圈脉冲数           */
#define ALPHA        (2.0 * 3.14159 / SPR)        /* 步距角 rad           */
#define A_T_x100     ((uint32_t)(ALPHA * T1_FREQ * 100))
#define T1_FREQ_148  ((uint32_t)((T1_FREQ * 0.676) / 100))
#define A_SQ         ((uint32_t)(ALPHA * 2 * 10000000000.0))
#define A_x20000     ((uint32_t)(ALPHA * 20000))

/* 平滑减速参数(2026-09-09 修订):减速段终点速度 ≈ 最高速 ÷ RAMP_DECEL_RATIO,
 * 速度随减速步数近似线性下降(不拖超长低速尾巴,也不像旧版把刹车压到最后1cm)。
 * R 越大末端越慢(更柔但尾部偏长、低速易"一顿一顿");建议 4~6,默认 5。 */
#define RAMP_DECEL_RATIO  3

/* ---------- 三根轴的实体 ---------- */
StepAxis axis_chassis = { TIM3, 1, RAMP_STOP };   /* 底盘:4 通道       */
StepAxis axis_lift    = { TIM2, 0, RAMP_STOP };   /* 升降:CCR3/4       */
StepAxis axis_tray    = { TIM4, 0, RAMP_STOP };   /* 载物台:CCR3/4     */

/* 把间隔写入定时器(ARR + 对应通道的 50% CCR) */
static void set_period(StepAxis *a, uint32_t delay)
{
    uint32_t half = delay >> 1;
    if (a->use_4ch) {
        a->tim->CCR1 = half;
        a->tim->CCR2 = half;
    }
    a->tim->CCR3 = half;
    a->tim->CCR4 = half;
    a->tim->ARR  = delay;
}

/*--------------------------------------------------------------------
 * Ramp_Start:填任务单 + 扣扳机(非阻塞)
 * steps  要走的步数(方向已由上层的 DIR 引脚决定,这里只取绝对值)
 * accel/decel/speed 单位同原工程(内部 ×100 定点)
 *-------------------------------------------------------------------*/
void Ramp_Start(StepAxis *a, int32_t steps,
                uint32_t accel, uint32_t decel, uint32_t speed)
{
    uint32_t max_s_lim;   /* 便签1:加满速需要的步数           */
    uint32_t accel_lim;   /* 便签2:距离允许的最大加速步数     */
    int8_t   sign = (steps >= 0) ? +1 : -1;

    if (steps < 0) steps = -steps;
    if (steps == 0) return;

    /* 把方向记进里程计的加减号(替代原来的 srd.dir + CW/CCW) */
    a->position_sign = sign;          /* 见下方结构体扩展说明 */

    if (steps == 1) {                 /* 特殊通道:只走一步 */
        a->decel_len  = 1;            /* 单步:减速段=1,直接走完停 */
        a->decel_tick = 0;
        a->decel_d0   = 1000;
        a->state      = RAMP_DECEL;
        a->step_delay = 1000;
        set_period(a, a->step_delay); /* ★修复①:直接用真实间隔点火 */
        a->tim->CNT = 0;
        TIM_Cmd(a->tim, ENABLE);
        return;
    }

    accel *= 100;  decel *= 100;  speed *= 100;

    a->min_delay  = A_T_x100 / speed;
    a->step_delay = (uint32_t)((T1_FREQ_148 * sqrt((double)A_SQ / accel)) / 100);

    max_s_lim = (uint32_t)((unsigned long)speed * speed / (((unsigned long)A_x20000 * accel) / 100));
    if (max_s_lim == 0) max_s_lim = 1;

    accel_lim = (uint32_t)((unsigned long)steps * decel / (accel + decel));
    if (accel_lim == 0) accel_lim = 1;

    if (accel_lim <= max_s_lim)                       /* 三角形 */
        a->decel_val = (int32_t)accel_lim - steps;
    else                                              /* 梯形   */
        a->decel_val = -(int32_t)((unsigned long)max_s_lim * accel / decel);
    if (a->decel_val == 0) a->decel_val = -1;

    a->decel_start = steps + a->decel_val;

    if (a->step_delay <= a->min_delay) {              /* 起步已达上限 */
        a->step_delay = a->min_delay;
        a->state      = RAMP_RUN;
        a->last_accel_delay = a->min_delay;           /* ★补上原版缺的赋值 */
    } else {
        a->state = RAMP_ACCEL;
    }

    a->accel_count = 0;
    a->step_count  = 0;
    a->rest        = 0;

    set_period(a, a->step_delay);     /* ★修复①:首步就是任务单间隔 */
    a->tim->CNT = 0;
    TIM_Cmd(a->tim, ENABLE);
}

/*--------------------------------------------------------------------
 * Ramp_OnTick:中断共用主体 —— 每个脉冲结束时被硬件叫一次
 *-------------------------------------------------------------------*/
void Ramp_OnTick(StepAxis *a)
{
    uint32_t new_delay = a->step_delay;   /* ★修复②:默认保持,杜绝垃圾值 */

    if (TIM_GetITStatus(a->tim, TIM_IT_Update) == RESET) return;
    TIM_ClearITPendingBit(a->tim, TIM_IT_Update);

    set_period(a, a->step_delay);         /* 下一轮周期先锁定 */

    switch (a->state) {

    case RAMP_STOP:
        a->step_count = 0;
        a->rest       = 0;
        TIM_Cmd(a->tim, DISABLE);         /* 断电,中断从此不再来 */
        return;                           /* STOP 不更新 step_delay */

    case RAMP_ACCEL:
        a->position += a->position_sign;  /* 里程计(签名版 ±1) */
        a->step_count++;
        a->accel_count++;
        new_delay = a->step_delay
                  - ((2 * a->step_delay + a->rest) / (4 * a->accel_count + 1));
        a->rest   = (int32_t)((2 * a->step_delay + a->rest)
                              % (4 * a->accel_count + 1));
        if (a->step_count >= a->decel_start) {        /* 撞到刹车点(三角形) */
            a->decel_len  = (uint32_t)(-a->decel_val); /* 减速总步数 B */
            if (a->decel_len == 0) a->decel_len = 1;
            a->decel_tick = 0;
            a->decel_d0   = new_delay;                 /* 刹车点当前间隔 */
            a->state      = RAMP_DECEL;
        } else if (new_delay <= a->min_delay) {       /* 撞到天花板(梯形) */
            a->last_accel_delay = new_delay;
            new_delay  = a->min_delay;
            a->rest    = 0;
            a->state   = RAMP_RUN;
        }
        break;

    case RAMP_RUN:
        a->position += a->position_sign;
        a->step_count++;
        new_delay = a->min_delay;
        if (a->step_count >= a->decel_start) {
            a->decel_len  = (uint32_t)(-a->decel_val);
            if (a->decel_len == 0) a->decel_len = 1;
            a->decel_tick = 0;
            a->decel_d0   = new_delay;                /* = min_delay(平顶) */
            a->state      = RAMP_DECEL;
        }
        break;

    case RAMP_DECEL:
        a->position += a->position_sign;
        a->step_count++;
        a->decel_tick++;
        {   /* ★平滑减速:让速度随步数近似线性下降(频率 ≈ f0·(1 - (1-1/R)·k/B)),
             * 减速全程可见,末端保留约 1/R 速度后停车 → 不抖不拖,也不高速直停 */
            uint32_t B = a->decel_len;
            uint32_t k = a->decel_tick;
            uint32_t R = RAMP_DECEL_RATIO;
            uint32_t lo = a->decel_d0;
            uint64_t den, num, d;
            if (B == 0) { B = 1; a->decel_len = 1; }
            if (k > B) k = B;
            if (lo == 0) lo = a->step_delay;
            if (lo == 0) lo = 1;
            den = (uint64_t)R * B - (uint64_t)(R - 1) * k;   /* ≥ B > 0 */
            num = (uint64_t)lo * R * B;
            d = num / den;
            if (d < 1) d = 1;
            a->step_delay = (uint32_t)d;
            new_delay = a->step_delay;
            if (k >= B) a->state = RAMP_STOP;   /* 走满减速段,下拍 STOP 断电 */
        }
        break;
    }

    a->step_delay = new_delay;
}

void Ramp_WaitDone(StepAxis *a)
{
    while (a->state != RAMP_STOP) { /* 等状态机收工,比数里程更稳 */ }
}

uint8_t Ramp_IsIdle(StepAxis *a) { return (a->state == RAMP_STOP); }

/*--------------------------------------------------------------------
 * 三个真中断:每个只有一行,把轴递给共用主体
 * (函数名必须与启动文件向量表一字不差 —— "对暗号")
 *-------------------------------------------------------------------*/
void TIM3_IRQHandler(void) { Ramp_OnTick(&axis_chassis); }
void TIM2_IRQHandler(void) { Ramp_OnTick(&axis_lift);    }
void TIM4_IRQHandler(void) { Ramp_OnTick(&axis_tray);    }
