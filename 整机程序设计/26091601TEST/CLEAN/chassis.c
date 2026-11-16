/**********************************************************************
 * chassis.c —— 底盘:方向表 + 换算 + 启动 + 演示循环
 *
 * 底盘类型:四轮对角全向轮(45° 辊子交替排列)
 *        FL1(/3)──FR2(\4)       辊子方向: FL/RR 为 \ , FR/RL 为 /
 *        │        │
 *        RL4(\8)──RR3(/7)
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
    { GPIOG, GPIO_Pin_8, 1 },   /* 轮2 PG8  				   */
    { GPIOA, GPIO_Pin_8, 1 },   /* 轮3 PA8  				   */
    { GPIOC, GPIO_Pin_1, 0 },   /* 轮4 PC1                     */
};

static void set_wheel_dir(uint8_t i, uint8_t level)
{
    if (dir_pins[i].invert) level = !level;
    if (level) GPIO_SetBits  (dir_pins[i].port, dir_pins[i].pin);
    else       GPIO_ResetBits(dir_pins[i].port, dir_pins[i].pin);
}

/* ============ 方向表:对角全向轮四种运动 ============ */
/* 运动学原理:每轮辊子与轮面成 45°,轮面推力沿45°分解。
 *  前进:四轮同转,侧向分量对消 → 直行
 *  左移:FL↺ + FR↻ + RL↻ + RR↺,前后分量对消 → 纯左移
 *  自旋:左侧两轮同向、右侧两轮反向 → 原地旋转            */
/*                          轮1 轮2 轮3 轮4                      */
static const uint8_t DIR_FORWARD [4] = { 0, 0, 0, 0 };
static const uint8_t DIR_BACKWARD[4] = { 1, 1, 1, 1 };
static const uint8_t DIR_LEFT    [4] = { 1, 0, 1, 0 };  /* 对角反向;2026-09-09实测互换 */
static const uint8_t DIR_RIGHT   [4] = { 0, 1, 0, 1 };
static const uint8_t DIR_CCW     [4] = { 1, 0, 0, 1 };  /* 左逆右顺;2026-09-09实测互换 */
static const uint8_t DIR_CW      [4] = { 0, 1, 1, 0 };

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
    /* ★换算走 ×100 版：每度 = 36.34 步（2026-09-29 实车标定，见 car_config.h）。
     *   +50 是**四舍五入**：90° → (90×3634 + 50)/100 = 3271 步；
     *   直接截断只会给 3270 步（白丢 0.6 步，慢转时就是可观的系统偏差）。
     *   先乘后除，整数运算不丢精度。 */
    int32_t steps = ((deg >= 0 ? deg : -deg) * STEPS_PER_DEGREE_X100 + 50) / 100;
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

/* ============ TEST ============ */
void Chassis_DemoCycle(void)
{
    while (1)
    {
        Chassis_MoveX_cm   ( DEMO_DISTANCE_CM);   /* 前进               */
        delay_ms(DEMO_PAUSE_MS);
        Chassis_MoveX_cm   (-DEMO_DISTANCE_CM);   /* 后退               */
        delay_ms(DEMO_PAUSE_MS);
        Chassis_MoveY_cm   ( DEMO_DISTANCE_CM);   /* 左移               */
        delay_ms(DEMO_PAUSE_MS);
        Chassis_MoveY_cm   (-DEMO_DISTANCE_CM);   /* 右移               */
        delay_ms(DEMO_PAUSE_MS);
        Chassis_Rotate_deg ( DEMO_ROTATE_DEG);    /* 左旋(CCW)          */
        delay_ms(DEMO_PAUSE_MS);
        Chassis_Rotate_deg (-DEMO_ROTATE_DEG);    /* 右旋(CW)           */
        delay_ms(DEMO_PAUSE_MS);
    }
}

/* ====================================================================
 * Demo2:八电机(MOTOR1~8)依次正转一圈 → 停止 → 下一个,无限循环
 *
 * 设计说明:
 *  ramp 引擎按"轴"驱动(一根轴 = 一个定时器的多通道同时输出),
 *  无法让同一定时器上的单个电机独立转。因此本 demo 绕开 ramp,
 *  直接用 GPIO 翻转 STEP 产生脉冲 —— 8 个电机全部独立可控。
 *  一圈 = STEPS_PER_REV = 3200 步,频率由 DEMO2_HALF_PERIOD_US 决定。
 * ==================================================================== */
typedef struct { GPIO_TypeDef *port; uint16_t pin; } MtrPin;

/* 8 个电机的 STEP/DIR 引脚表(与 motor.h / README.TXT 硬件事实一致) */
static const MtrPin mtr_step[8] = {
    { GPIOB, GPIO_Pin_10 },   /* M1 升降     PB10 TIM2_CH3 */
    { GPIOB, GPIO_Pin_11 },   /* M2 空置备用 PB11 TIM2_CH4 */
    { GPIOC, GPIO_Pin_6  },   /* M3 轮1      PC6  TIM3_CH1 */
    { GPIOC, GPIO_Pin_7  },   /* M4 轮2      PC7  TIM3_CH2 */
    { GPIOB, GPIO_Pin_8  },   /* M5 载物台   PB8  TIM4_CH3 */
    { GPIOB, GPIO_Pin_9  },   /* M6 空置备用 PB9  TIM4_CH4 */
    { GPIOC, GPIO_Pin_8  },   /* M7 轮3      PC8  TIM3_CH3 */
    { GPIOC, GPIO_Pin_9  },   /* M8 轮4      PC9  TIM3_CH4 */
};
static const MtrPin mtr_dir[8] = {
    { GPIOA, GPIO_Pin_4  },   /* M1 PA4  */
    { GPIOA, GPIO_Pin_5  },   /* M2 PA5  */
    { GPIOG, GPIO_Pin_7  },   /* M3 PG7  */
    { GPIOG, GPIO_Pin_8  },   /* M4 PG8  */
    { GPIOG, GPIO_Pin_13 },   /* M5 PG13 */
    { GPIOE, GPIO_Pin_6  },   /* M6 PE6  */
    { GPIOA, GPIO_Pin_8  },   /* M7 PA8  */
    { GPIOC, GPIO_Pin_1  },   /* M8 PC1  */
};

/* 把 STEP/DIR 全部配成推挽输出,并停掉三根轴定时器:
 *  M1~M8 的 STEP 引脚原本是定时器复用输出(AF_PP),demo 期间
 *  改回 GPIO 手动翻转;M2/M6 的 DIR(PA5/PE6)原工程从未初始化,一并配置。
 *  停 TIM2/3/4 防止残留 PWM / 中断干扰。 */
static void demo2_pin_init(void)
{
    GPIO_InitTypeDef g;
    uint8_t i;

    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_GPIOB |
                           RCC_APB2Periph_GPIOC | RCC_APB2Periph_GPIOE |
                           RCC_APB2Periph_GPIOG, ENABLE);

    g.GPIO_Mode  = GPIO_Mode_Out_PP;
    g.GPIO_Speed = GPIO_Speed_50MHz;
    for (i = 0; i < 8; i++) {
        g.GPIO_Pin = mtr_step[i].pin;  GPIO_Init(mtr_step[i].port, &g);
        g.GPIO_Pin = mtr_dir[i].pin;   GPIO_Init(mtr_dir[i].port, &g);
    }

    TIM_Cmd(TIM2, DISABLE);
    TIM_Cmd(TIM3, DISABLE);
    TIM_Cmd(TIM4, DISABLE);
}

/* 单个电机正转一圈:DIR 置高 + 3200 个 STEP 脉冲 */
static void demo2_spin_one(uint8_t idx)
{
    uint32_t n;
    GPIO_SetBits(mtr_dir[idx].port, mtr_dir[idx].pin);        /* 正转 */
    for (n = 0; n < STEPS_PER_REV; n++) {
        GPIO_SetBits(mtr_step[idx].port, mtr_step[idx].pin);
        delay_us(DEMO2_HALF_PERIOD_US);
        GPIO_ResetBits(mtr_step[idx].port, mtr_step[idx].pin);
        delay_us(DEMO2_HALF_PERIOD_US);
    }
}

void Chassis_DemoCycle_2(void)
{
    demo2_pin_init();
    while (1)
    {
        uint8_t i;
        for (i = 0; i < 8; i++) {
            demo2_spin_one(i);            /* 正转一圈       */
            delay_ms(DEMO2_PAUSE_MS);     /* 停止一段时间   */
        }
    }
}

