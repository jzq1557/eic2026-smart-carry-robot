/**********************************************************************
 * chassis.c —— 底盘:方向表 + 换算 + 启动
 *
 * 改进:原代码的 if(x<0){DIR1(0);DIR2(0)...} 意大利面,
 *       重写为"方向查表"——四种运动就是四行数据。
 *********************************************************************/
#include "system_init.h"
#include "chassis.h"
#include "car_config.h"
#include "ramp.h"

/* ============ 引脚层:每个轮子的 DIR 线 ============ */
/* 硬件事实(勿改):四电机镜像对称安装,左右两侧旋向天然相反。
 * 机械补偿做在这一层:wheel 2、3 的电平取反,
 * 从而上层的方向表可以用"人类直觉"书写(1=让车往该方向使劲)。 */
typedef struct { GPIO_TypeDef *port; uint16_t pin; uint8_t invert; } DirPin;

static const DirPin dir_pins[4] = {
    { GPIOG, GPIO_Pin_7, 0 },   /* 轮1 PG7                     */
    { GPIOG, GPIO_Pin_8, 1 },   /* 轮2 PG8 —— 镜像安装,反接  */
    { GPIOA, GPIO_Pin_8, 1 },   /* 轮3 PA8 —— 镜像安装,反接  */
    { GPIOC, GPIO_Pin_1, 0 },   /* 轮4 PC1                     */
};

static void set_wheel_dir(uint8_t i, uint8_t level)
{
    if (dir_pins[i].invert) level = !level;
    if (level) GPIO_SetBits  (dir_pins[i].port, dir_pins[i].pin);
    else       GPIO_ResetBits(dir_pins[i].port, dir_pins[i].pin);
}

/* ============ 方向表:麦克纳姆轮四种运动 ============ */
/*                          轮1 轮2 轮3 轮4                      */
static const uint8_t DIR_FORWARD [4] = { 1, 1, 1, 1 };
static const uint8_t DIR_BACKWARD[4] = { 0, 0, 0, 0 };
static const uint8_t DIR_LEFT    [4] = { 0, 1, 0, 1 };  /* 对角同向 */
static const uint8_t DIR_RIGHT   [4] = { 1, 0, 1, 0 };
static const uint8_t DIR_CCW     [4] = { 0, 1, 1, 0 };  /* 左右反向 */
static const uint8_t DIR_CW      [4] = { 1, 0, 0, 1 };

static void apply_dirs(const uint8_t d[4])
{
    uint8_t i;
    for (i = 0; i < 4; i++) set_wheel_dir(i, d[i]);
}

/* ============ 通用私有函数:定向 + 换算 + 开跑 ============ */
static void move_steps(const uint8_t d[4], int32_t steps,
                       uint32_t accel, uint32_t decel, uint32_t speed)
{
    apply_dirs(d);
    axis_chassis.position = 0;
    Ramp_Start(&axis_chassis, steps, accel, decel, speed);
}

/* ============ 对外接口 ============ */
void Chassis_Init(void)          /* 硬件配置沿用原工程,零改动 */
{
    MOTOR3_Init();  MOTOR4_Init();  MOTOR7_Init();  MOTOR8_Init();
}

void Chassis_MoveX_cm(int8_t cm)
{
    const uint8_t *d = (cm >= 0) ? DIR_FORWARD : DIR_BACKWARD;
    int32_t steps = (cm >= 0 ? cm : -cm) * STEPS_PER_CM;
    move_steps(d, steps, PROFILE_CHASSIS_FAST);
    Ramp_WaitDone(&axis_chassis);
}

void Chassis_MoveY_cm(int8_t cm)
{
    const uint8_t *d = (cm >= 0) ? DIR_LEFT : DIR_RIGHT;
    int32_t steps = (cm >= 0 ? cm : -cm) * STEPS_PER_CM;
    move_steps(d, steps, PROFILE_CHASSIS_FAST);
    Ramp_WaitDone(&axis_chassis);
}

void Chassis_MoveX_mm(int8_t mm)
{
    const uint8_t *d = (mm >= 0) ? DIR_FORWARD : DIR_BACKWARD;
    int32_t steps = (mm >= 0 ? mm : -mm) * STEPS_PER_MM;
    move_steps(d, steps, PROFILE_CHASSIS_FINE);
    Ramp_WaitDone(&axis_chassis);
}

void Chassis_MoveY_mm(int8_t mm)
{
    const uint8_t *d = (mm >= 0) ? DIR_LEFT : DIR_RIGHT;
    int32_t steps = (mm >= 0 ? mm : -mm) * STEPS_PER_MM;
    move_steps(d, steps, PROFILE_CHASSIS_FINE);
    Ramp_WaitDone(&axis_chassis);
}

void Chassis_Rotate_deg(int8_t deg)
{
    const uint8_t *d = (deg >= 0) ? DIR_CCW : DIR_CW;
    int32_t steps = (deg >= 0 ? deg : -deg) * STEPS_PER_DEGREE;
    /* ★修复:原版 MOTOR_Angle 把 int8×51 塞进 uint16,负角度会溢出成
     *  巨大正数;这里先取绝对值再乘,天然安全 */
    move_steps(d, steps, PROFILE_CHASSIS_TURN);
    Ramp_WaitDone(&axis_chassis);
}

/* ---- 非阻塞版(复合动作用) ---- */
void Chassis_MoveX_cm_Async(int8_t cm)
{
    const uint8_t *d = (cm >= 0) ? DIR_FORWARD : DIR_BACKWARD;
    int32_t steps = (cm >= 0 ? cm : -cm) * STEPS_PER_CM;
    move_steps(d, steps, PROFILE_CHASSIS_FAST);   /* 不等,直接返回 */
}

void Chassis_WaitDone(void) { Ramp_WaitDone(&axis_chassis); }
