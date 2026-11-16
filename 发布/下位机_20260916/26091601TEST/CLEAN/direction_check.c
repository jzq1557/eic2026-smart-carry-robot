/**********************************************************************
 * direction_check.c —— 底盘"程序方向 ↔ 实际车向"自动校准 demo
 *
 * 说明: 上电后(定义了 RUN_DIRECTION_DEMO 时)自动按序连续执行
 *   前进 → 后退 → 左移 → 右移 → 左旋 → 右旋, 每步之间有间隔便于观察,
 *   全程不需要按键。执行 N 轮(见 DCHK_CYCLES, 0=无限)后以长红灯结束。
 *
 * 硬件前提(与 chassis.c 一致): 四轮"对角全向轮"装在车四角(辊子45°),
 *   轮1..轮4 由 dir_pins[] 定义(DIR 线: PG7/PG8/PA8/PC1, 轮2/3 电平已 invert)。
 * 代码方向的"人类直觉约定"(chassis.c 方向表):
 *   前进 = Chassis_MoveX_cm(+),  后退 = Chassis_MoveX_cm(-)
 *   左移 = Chassis_MoveY_cm(+),  右移 = Chassis_MoveY_cm(-)   ← 依辊子安装方向
 *   左旋 = Chassis_Rotate_deg(+),右旋 = Chassis_Rotate_deg(-)
 *
 * 校正: 记录每步实际车向并对照 chassis.h 期望。反了/斜了的修正位置:
 *   - 单轮装反(斜走/画圈)→ chassis.c dir_pins[] 该轮 invert 0↔1
 *   - 前后/左右整体互换 → chassis.c DIR_FORWARD/BACK 或 DIR_LEFT/RIGHT 整行
 *   - 自旋方向整反      → DIR_CCW/DIR_CW
 *   - 若横移做不出来/斜对角走 → 轮子45°布局与方向表不符, 需按实际轮角重写方向表
 *
 * ⚠️ 安全: 首次请架空车轮; 步长/间隔见下方 DCHK_* 宏。
 **********************************************************************/
#include "system_init.h"        /* 含 ws2812.h / delay.h */
#include "chassis.h"
#include "direction_check.h"

/* ===== 参数 ===== */
#define DCHK_CM      15         /* 平移距离 cm             */
#define DCHK_DEG     90         /* 旋转角度 °              */
#define DCHK_GAP_MS  1500       /* 每步之间的观察间隔(ms)   */
#define DCHK_CYCLES  0          /* 循环轮数: 0=无限循环; N=跑 N 轮后停 */

static void led_blink(uint8_t r, uint8_t g, uint8_t b, uint16_t ms)
{
    ws2812_AllOpen(r, g, b);
    delay_ms(ms);
    ws2812_AllOpen(0, 0, 0);
    delay_ms(80);
}

static void step_done(uint16_t gap_ms)
{
    ws2812_AllOpen(0, 60, 200);          /* 蓝: 该步完成 */
    delay_ms(300);
    ws2812_AllOpen(0, 0, 0);
    delay_ms(gap_ms);                     /* 观察间隔 */
}

void Direction_Check_Run(void)
{
    uint32_t round = 0;

    for (;;)
    {
        round++;

        /* ① 前进 */
        ws2812_AllOpen(0, 200, 0);  Chassis_MoveX_cm( DCHK_CM);
        step_done(DCHK_GAP_MS);

        /* ② 后退 */
        ws2812_AllOpen(0, 200, 0);  Chassis_MoveX_cm(-DCHK_CM);
        step_done(DCHK_GAP_MS);

        /* ③ 左移 */
        ws2812_AllOpen(0, 200, 0);  Chassis_MoveY_cm( DCHK_CM);
        step_done(DCHK_GAP_MS);

        /* ④ 右移 */
        ws2812_AllOpen(0, 200, 0);  Chassis_MoveY_cm(-DCHK_CM);
        step_done(DCHK_GAP_MS);

        /* ⑤ 左旋(逆时针) */
        ws2812_AllOpen(0, 200, 0);  Chassis_Rotate_deg( DCHK_DEG);
        step_done(DCHK_GAP_MS);

        /* ⑥ 右旋(顺时针) */
        ws2812_AllOpen(0, 200, 0);  Chassis_Rotate_deg(-DCHK_DEG);
        step_done(DCHK_GAP_MS);

        /* 一轮结束: 黄灯闪一下 */
        ws2812_AllOpen(255, 180, 0);
        delay_ms(400);
        ws2812_AllOpen(0, 0, 0);
        delay_ms(DCHK_GAP_MS);

        if (DCHK_CYCLES > 0 && round >= DCHK_CYCLES)
        {
            led_blink(120, 0, 0, 2000);   /* 长红: 校准结束 */
            return;
        }
    }
}
