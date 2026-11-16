/**********************************************************************
 * claw.c
 *********************************************************************/
#include "system_init.h"
#include "claw.h"
#include "car_config.h"
#include "ramp.h"

/* 升降轴当前绝对高度(步)。上电归零位 = LIFT_HOME */
static int32_t lift_height_now = LIFT_HOME;

/* 升降方向引脚 PA4(沿用原 Motor1_DIR) */
static void lift_dir(uint8_t up)
{
    if (up) GPIO_SetBits  (GPIOA, GPIO_Pin_4);
    else    GPIO_ResetBits(GPIOA, GPIO_Pin_4);
}

void Claw_Init(void)
{
    MOTOR1_Init();      /* 升降步进,硬件配置零改动 */
    Servo2_Init();      /* 爪子张合 PA2 TIM5_CH3   */
    Servo3_Init();      /* 腕关节   PA3 TIM5_CH4   */
    lift_height_now = LIFT_HOME;
}

/* ============ 舵机原语 ============ */
/* ★TIM6(1s)会周期把 servo_angle2/3 写回舵机:改角度必须同步更新全局变量,
 * 否则 1 秒后夹爪/腕关节会被旧值拉回(定义在 time_count.c,旧版 control.c 已移出编译) */
extern uint8_t servo_angle2;   /* 爪子: 0=张开 95=夹紧 */
extern uint8_t servo_angle3;   /* 腕关节: 240=朝前 60=朝后 */

/* 发两遍防首帧丢失(沿用原工程的可靠性技巧) */
static void servo_send_twice(void (*ctrl)(uint8_t), uint8_t angle)
{
    ctrl(angle);  delay_ms(25);
    ctrl(angle);  delay_ms(25);
}
void Grip_Open (void) { servo_angle2 = SERVO_GRIP_OPEN;  servo_send_twice(SERVO2_CONTROL, SERVO_GRIP_OPEN ); }
void Grip_Close(void) { servo_angle2 = SERVO_GRIP_CLOSE; servo_send_twice(SERVO2_CONTROL, SERVO_GRIP_CLOSE); }
/* 爪子设任意角度(0~255°):瞄准等场景用 0° 完全张开避免遮挡摄像头。
 * 与 Grip_Open/Close 一样同步更新 servo_angle2,防 TIM6 每秒拉回旧值 */
void Grip_Angle(uint8_t angle)
{
    servo_angle2 = angle;
    servo_send_twice(SERVO2_CONTROL, angle);
}
void Wrist_Front(void){ servo_angle3 = SERVO_WRIST_FRONT; SERVO3_CONTROL(SERVO_WRIST_FRONT); }
void Wrist_Back (void){ servo_angle3 = SERVO_WRIST_BACK;  SERVO3_CONTROL(SERVO_WRIST_BACK ); }

/* ============ 升降:绝对高度制 ============ */
void Lift_GoTo_Async(int32_t target)
{
    int32_t delta = target - lift_height_now;   /* 自动算路程与方向 */
    if (delta == 0) return;

    /* 软件限位:目标越界直接拒绝(替代原版的 0~220mm 检查) */
    if (target < LIFT_HOME || target > LIFT_TOP) return;

    lift_dir(delta > 0);
    axis_lift.position = 0;
    Ramp_Start(&axis_lift, (delta > 0 ? delta : -delta), PROFILE_LIFT_NORMAL);
    lift_height_now = target;                   /* 账本即时更新 */
}
void Lift_WaitDone(void) { Ramp_WaitDone(&axis_lift); }
void Lift_GoTo(int32_t target)
{
    Lift_GoTo_Async(target);
    Lift_WaitDone();
}

/* ============ 载物台 ============ */
void Tray_Turn120(void)
{
    axis_tray.position = 0;
    Ramp_Start(&axis_tray, TRAY_STEPS_120DEG, PROFILE_TRAY);
    Ramp_WaitDone(&axis_tray);
}

/* ============ 组合动作(编舞层)============ */
/* 每个动作 = 原语的顺序脚本。原版散落各处的 claw_get_block 等
 * 在这里用"绝对高度 + 命名原语"重排,一眼可读。 */

void Claw_GrabFromTray(void)        /* 协议 0x07:从转盘抓块 */
{
    Wrist_Front();
    Lift_GoTo(LIFT_BLOCK_GET);      /* 降到转盘抓取高度 */
    Grip_Close();                   /* 夹住             */
    delay_ms(200);
    Lift_GoTo(LIFT_TOP);            /* 提起             */
    Wrist_Back();                   /* 转向载物台       */
    delay_ms(400);                  /* 舵机无反馈,时间估算到位 */
    Lift_GoTo(LIFT_BLOCK_DOWN);     /* 降到载物台       */
    Grip_Open();                    /* 放下             */
    Lift_GoTo(LIFT_TOP);
    Wrist_Front();
    delay_ms(200);
    Tray_Turn120();                 /* 载物台换下一格   */
}

void Claw_PutLayer1(void)           /* 协议 0x09:放地面第一层 */
{
    Wrist_Back();
    delay_ms(400);
    Lift_GoTo(LIFT_BLOCK_DOWN);
    Grip_Close();
    Lift_GoTo(LIFT_TOP);
    Wrist_Front();
    delay_ms(400);
    Lift_GoTo(LIFT_PUT_LAYER1);
    Grip_Open();
    Lift_GoTo(LIFT_TOP);
    /* 其余动作(0x08/0x0A/码垛…)按同样套路,
     * 对照原 control_claw.c 的高度账本逐个搬运即可 */
}

/* 相对移动(协议 0x0C 用):正=向上,负=向下,内部自动限位 */
void Lift_MoveBy(int16_t delta_steps)
{
    Lift_GoTo(lift_height_now + delta_steps);
}
