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

/* ============ 2b. Demo2 换向圈数 ============ */
/* 轴每转 STEPS_PER_REV=3200 步,5 圈=16000 步后换下一个轮 */
#define DEMO2_REVS_BEFORE_FLIP  5

/* ============ 3. 舵机角度(270° 舵机) ============ */
#define SERVO_GRIP_OPEN       0        /* 爪子张开                    */
#define SERVO_GRIP_CLOSE      95       /* 爪子夹紧物块                */
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
} CarCommand;
/* ★协议修复说明:原代码 0x04 用大端(高字节在前)、0x0C 用小端,
 * 两处打架。重写后全部统一大端;树莓派端 0x0C 打包需同步修改! */

/* ============ 6. 演示循环参数 ============ */
#define DEMO_DISTANCE_CM       15       /* 每次移动距离(cm),可改      */
#define DEMO_ROTATE_DEG        90       /* 每次旋转角度(°),可改      */
#define DEMO_PAUSE_MS          500      /* 每段停顿时长(ms):0.5 秒   */

#endif
