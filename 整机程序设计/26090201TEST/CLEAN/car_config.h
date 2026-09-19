/**********************************************************************
 * car_config.h —— 全车唯一的"数字仓库"
 *
 * 原则:任何魔法数字都必须住在这里,并且带注释说明物理来源。
 *      改参数只改这一个文件。
 *********************************************************************/
#ifndef CAR_CONFIG_H
#define CAR_CONFIG_H

/* ============ 1. 机械换算(硬件焊死,勿改) ============ */
#define MOTOR_STEPS_PER_REV   200      /* 电机单圈整步数              */
#define DRIVER_MICROSTEP      16       /* 驱动器细分                  */
#define STEPS_PER_REV         (MOTOR_STEPS_PER_REV * DRIVER_MICROSTEP) /* 3200 */

#define WHEEL_DIAMETER_MM     78       /* 对角全向轮直径              */
/* 每 cm 脉冲数 = 3200 / (π×7.8cm) ≈ 130,实验标定后取整
 * ★注意:全向轮有效滚动直径可能异于麦轮,首次上电需实测微调 */
#define STEPS_PER_CM          130
#define STEPS_PER_MM          13       /* = STEPS_PER_CM / 10         */
#define STEPS_PER_DEGREE      51       /* 原地旋转 1° 的脉冲数,实验标定 */

#define LIFT_STEPS_PER_MM     64       /* 升降丝杆:3200/(8×2×π)≈64   */
#define TRAY_STEPS_120DEG     1067     /* 载物台转 1/3 圈 = 3200/3    */

/* ============ 2. 运动性格(三组调参,单位 rad/s 系) ============ */
/* 调参哲学:粗活快干、细活慢干、误差层层收敛                        */
/* 格式: accel, decel, speed —— decel<accel 则刹车比起步更缓       */
#define PROFILE_CHASSIS_FAST   6,  4, 12   /* 长距离平移:缓起缓停    */
#define PROFILE_CHASSIS_TURN   2,  2,  6   /* 原地旋转:准            */
#define PROFILE_CHASSIS_FINE   1,  1,  2   /* mm 精调:稳             */
#define PROFILE_CHASSIS_DEMO2  1,  1,  2   /* Demo2:0.2x FAST,极慢  */
#define PROFILE_LIFT_NORMAL    3,  3,  5   /* 升降常规                */
#define PROFILE_LIFT_GENTLE    2,  2,  4   /* 升降轻柔(带着物块时)  */
#define PROFILE_TRAY           1,  1,  2   /* 载物台                  */

/* ============ 2b. Demo2:八电机依次正转一圈循环 ============ */
/* 每个电机(MOTOR1~8)以固定脉冲频率正转一整圈后停止,依次执行,无限循环
 * 一圈 = STEPS_PER_REV = 3200 步;半周期 500us → 1kHz 脉冲,一圈约 3.2s */
#define DEMO2_HALF_PERIOD_US   500      /* 脉冲半周期(us),改小转更快     */
#define DEMO2_PAUSE_MS         500      /* 每电机转完后的停顿(ms)        */
#define DEMO2_REVS_BEFORE_FLIP  5       /* (废弃)旧"逐个反转"方案参数   */

/* ============ 2c. TTLctrl:USART3 串口四路闭环电机控制(原 Demo3) ============ */
/* 四路 Emm_V5.0 闭环电机 ID=1/2/3/4,经 PC10(TX)/PC11(RX) 115200 控制
 * 控制函数在 TTLctrl.c:方向/速度/距离全部参数化,非阻塞(发完即返回)
 * 机械对应:1=伸缩滑台 2/3=龙门架(镜像互反) 4=base轴 */
#define TTLCTRL_ACC_DEFAULT  60              /* 默认加减速 0-255,0=直接启动 */

/* ===== TTL 放料动作组参数(瞄准圆心后下放-开爪-抬升,实测标定) ===== */
#define TTL_PLACE_LOWER_VEL   50             /* 下放速度 RPM(慢)          */
#define TTL_PLACE_LOWER_CLK   6000           /* 下放距离(脉冲,实测标定)   */
#define TTL_PLACE_HOLD_MS     800            /* 到位后开爪前的停留(ms)    */
#define TTL_PLACE_OPEN_DELAY  600            /* 开爪后抬升前延时(爪张开)  */
#define TTL_PLACE_RAISE_VEL   80             /* 抬升速度 RPM              */
#define TTL_PLACE_RAISE_CLK   6000           /* 抬升距离(脉冲,回原高度)   */

/* ============ 3. 舵机角度(270° 舵机) ============ */
#define SERVO_GRIP_OPEN       57       /* 爪子张开                    */
#define SERVO_GRIP_CLOSE      120      /* 爪子夹紧物块                */
#define SERVO_WRIST_FRONT     240      /* 腕关节朝前(读码/抓取姿态) */
#define SERVO_WRIST_BACK      60       /* 腕关节朝后(载物台姿态)    */

/* ============ 4. 升降台绝对高度表(单位:步) ============ */
/* 原代码用相对位移手工记账,这里统一为绝对高度,由 Lift_GoTo 换算 */
#define LIFT_HOME             0        /* 最低点(上电归零位)        */
#define LIFT_TOP              12600    /* 最高点                      */
#define LIFT_CAMERA           9800     /* 摄像头读码高度              */
#define LIFT_BLOCK_GET        9600     /* 转盘抓取高度                */
#define LIFT_BLOCK_DOWN       1500     /* 放到载物台的高度            */
#define LIFT_PUT_LAYER1       0        /* 地面第一层放置              */
#define LIFT_PUT_LAYER2       5100     /* 码垛第二层放置              */

/* ============ 5. 串口协议 ============ */
#define PKT_HEAD              0xFF
#define PKT_TAIL              0xFE
#define PKT_HANDSHAKE_UP      0xFD     /* 树莓派上线信号              */
#define PKT_ACK_DONE          0x01     /* 动作完成回执                */

/* 命令字(数据包剥壳后 buf[0]) */
typedef enum {
    CMD_MOVE_X_CM   = 0x01,   /* buf[1] = int8 位移 cm               */
    CMD_MOVE_Y_CM   = 0x02,   /* buf[2] = int8 位移 cm               */
    CMD_ROTATE_DEG  = 0x03,   /* buf[3] = int8 角度                  */
    CMD_SHOW_QRCODE = 0x04,   /* buf[4..7] 两个 16 位数,均为大端     */
    CMD_MOVE_X_MM   = 0x05,
    CMD_MOVE_Y_MM   = 0x06,
    CMD_GRAB_TRAY   = 0x07,   /* 从转盘抓块                          */
    CMD_GRAB_GROUND = 0x08,   /* 从地面抓块                          */
    CMD_PUT_LAYER1  = 0x09,
    CMD_PUT_LAYER2  = 0x0A,
    CMD_LIFT_GOTO   = 0x0B,   /* buf[1] = 预设位编号                 */
    CMD_LIFT_MOVE   = 0x0C,   /* buf[1..2] 16 位距离,★统一为大端★  */
    CMD_COMBO       = 0x0D,   /* 复合动作:buf[1]=子模式 buf[2]=距离 */

    /* ===== TTL 闭环电机命令(0x10+,经 USART3→张大头驱动器) =====
     * 参数统一布局: buf[1]=dir(0=CW/其余=CCW)
     *              buf[2..3]=vel 16位大端(RPM)
     *              buf[4..7]=clk 32位大端(脉冲,3200=1圈)         */
    CMD_TTL_MOTOR1  = 0x10,   /* id1 伸缩滑台:dir/vel/clk           */
    CMD_TTL_MOTOR23 = 0x11,   /* id2/3 龙门架:dir(龙门方向)/vel/clk  */
    CMD_TTL_MOTOR4  = 0x12,   /* id4 base轴:dir/vel/clk             */
    CMD_TTL_PLACE   = 0x13,   /* 放料动作组(整组下放-开爪-抬升)     */
    CMD_TTL_GRIP    = 0x14,   /* 夹爪:buf[1]=0开 / 1闭               */
    CMD_TTL_GRIP_ANG = 0x15,  /* 夹爪设任意角度:buf[1]=0~255°        */
    CMD_TTL_GRAB_VEH = 0x16,  /* 去车载物架夹料动作组(手调,结束时回观察位) */
} CarCommand;
/* ★协议修复说明:原代码 0x04 用大端(高字节在前)、0x0C 用小端,
 * 两处打架。重写后全部统一大端;树莓派端 0x0C 打包需同步修改! */

/* ============ 6. 演示循环参数 ============ */
#define DEMO_DISTANCE_CM       15       /* 每次移动距离(cm),可改      */
#define DEMO_ROTATE_DEG        90       /* 每次旋转角度(°),可改      */
#define DEMO_PAUSE_MS          500      /* 每段停顿时长(ms):0.5 秒   */

#endif
