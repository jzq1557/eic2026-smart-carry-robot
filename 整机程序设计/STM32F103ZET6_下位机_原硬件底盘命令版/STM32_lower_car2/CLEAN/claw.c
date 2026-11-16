/**********************************************************************
 * claw.c
 *********************************************************************/
#include "system_init.h"
#include "claw.h"
#include "car_config.h"
#include "ramp.h"
/* 舵机当前角度(旧代码 time_count.c 有引用,保留全局变量) */
uint8_t servo_angle1;
uint8_t servo_angle2;
uint8_t servo_angle3;
/* 升降轴当前绝对高度(步)。上电归零位 = LIFT_HOME */
static int32_t lift_height_now = LIFT_HOME;

/* 升降方向引脚 PA4(沿用原 Motor1_DIR)
 * ★修复:极性由 car_config.h 的 LIFT_DIR_UP_LEVEL 决定。
 *   已验证的步进测试程序中 PA4=0 为上升,原固件写反导致
 *   "升到顶"实际是往下顶底、升降台看似不动。 */
static void lift_dir(uint8_t up)
{
    uint8_t level = up ? LIFT_DIR_UP_LEVEL : !LIFT_DIR_UP_LEVEL;
    if (level) GPIO_SetBits  (GPIOA, GPIO_Pin_4);
    else       GPIO_ResetBits(GPIOA, GPIO_Pin_4);
}

/* ★归零改为"盲升归顶"(v7):
 * 夹爪为3D打印悬臂件,盲降可能扫到载物台料盘;向上则永远远离障碍。
 * 从任意位置盲升超过全行程,必停在顶部机械限位,账本记为 LIFT_TOP。
 * 若起始已近顶部,仅在顶端低速打滑片刻,无伤害。 */
void Lift_Home(void)
{
    lift_dir(1);                                  /* 向上 */
    axis_lift.position = 0;
    Ramp_Start(&axis_lift, LIFT_HOMING_STEPS, PROFILE_LIFT_GENTLE);
    Ramp_WaitDone(&axis_lift);
    lift_height_now = LIFT_TOP;                   /* 到顶,账本=最高点 */
}

void Claw_Init(void)
{
    MOTOR1_Init();      /* 升降步进,硬件配置零改动 */
    Servo2_Init();      /* 爪子张合 PA2 TIM5_CH3   */
    Servo3_Init();      /* 腕关节   PA3 TIM5_CH4   */
    lift_height_now = LIFT_HOME;
}

/* ============ 舵机原语 ============ */
/* 发两遍防首帧丢失(沿用原工程的可靠性技巧) */
static void servo_send_twice(void (*ctrl)(uint8_t), uint8_t angle)
{
    ctrl(angle);  delay_ms(25);
    ctrl(angle);  delay_ms(25);
}
void Grip_Open (void) { servo_send_twice(SERVO2_CONTROL, SERVO_GRIP_OPEN ); }
void Grip_Close(void) { servo_send_twice(SERVO2_CONTROL, SERVO_GRIP_CLOSE); }
void Wrist_Front(void){ SERVO3_CONTROL(SERVO_WRIST_FRONT); }
void Wrist_Back (void){ SERVO3_CONTROL(SERVO_WRIST_BACK ); }

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
    Lift_GoTo(LIFT_TOP);            /* v18安全:转腕前必先升顶(已在顶=零代价) */
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
    Lift_GoTo(LIFT_TOP);            /* v18安全:转腕前必先升顶(已在顶=零代价) */
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
    Wrist_Back();                   /* v19:腕先回位(在顶部转,安全) */
    delay_ms(200);
    Tray_Turn120();                 /* v19:取空的盘转走,下一个有块的盘对位
                                       修复:原代码连续三次0x09都从同一格取块,
                                       第二三次必然抓空 */
}

void Claw_GrabFromGround(void)      /* v16 协议 0x08:从地面抓块,放回载物台 */
{
    Lift_GoTo(LIFT_TOP);            /* v18安全:转腕前必先升顶(已在顶=零代价) */
    Wrist_Front();
    delay_ms(400);
    Lift_GoTo(LIFT_PUT_LAYER1);     /* 降到地面高度            */
    Grip_Close();                   /* 夹住地面物块            */
    delay_ms(200);
    Lift_GoTo(LIFT_TOP);            /* 提起                    */
    Wrist_Back();                   /* 转向载物台              */
    delay_ms(400);
    Lift_GoTo(LIFT_BLOCK_DOWN);     /* 降到载物台放置高度      */
    Grip_Open();                    /* 放入料盘                */
    Lift_GoTo(LIFT_TOP);
    Wrist_Front();
    delay_ms(200);
    Tray_Turn120();                 /* 载物台换下一格          */
}

void Claw_PutLayer2(void)           /* v16 协议 0x0A:码垛第二层(叠在已放物块上) */
{
    Lift_GoTo(LIFT_TOP);            /* v18安全:转腕前必先升顶(已在顶=零代价) */
    Wrist_Back();
    delay_ms(400);
    Lift_GoTo(LIFT_BLOCK_DOWN);     /* 从载物台取块            */
    Grip_Close();
    delay_ms(200);
    Lift_GoTo(LIFT_TOP);
    Wrist_Front();
    delay_ms(400);
    Lift_GoTo(LIFT_PUT_LAYER2);     /* 降到第二层高度(6800,待实测) */
    Grip_Open();                    /* 叠放                    */
    Lift_GoTo(LIFT_TOP);
}

void Claw_Final(void)               /* v16 协议 0x0E:全流程结束,回收尾姿态 */
{
    Lift_GoTo(LIFT_TOP);            /* v18安全:转腕前必先升顶(已在顶=零代价) */
    Grip_Open();
    Wrist_Back();
    delay_ms(400);
    Lift_GoTo(LIFT_TOP);
}

/* 相对移动(协议 0x0C 用):正=向上,负=向下,内部自动限位 */
void Lift_MoveBy(int16_t delta_steps)
{
    Lift_GoTo(lift_height_now + delta_steps);
}
