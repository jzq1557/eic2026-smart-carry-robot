/**********************************************************************
 * protocol.c —— 命令分发层(原 uart_handle 的重写)
 *
 * 改进:
 *  ① switch 里只做"取参 + 调用",每个动作一行,细节全在各模块
 *  ② 16 位参数统一大端拼接(修复 0x0C 与 0x04 打架)
 *  ③ 阻塞语义由被调函数自己保证 —— 本层不再散落 while 守门
 *********************************************************************/
#include "system_init.h"
#include "car_config.h"
#include "chassis.h"
#include "claw.h"
#include "ramp.h"

/* 大端拼 16 位:高字节在前(全协议唯一标准) */
static uint16_t be16(uint8_t hi, uint8_t lo) { return ((uint16_t)hi << 8) | lo; }

void Protocol_Handle(const uint8_t *buf)     /* buf = 剥壳后的 8 字节 */
{
    switch ((CarCommand)buf[0])
    {
    case CMD_MOVE_X_CM:   Chassis_MoveX_cm((int8_t)buf[1]);   break;
    case CMD_MOVE_Y_CM:   Chassis_MoveY_cm((int8_t)buf[2]);   break;
    case CMD_ROTATE_DEG:  Chassis_Rotate_deg((int8_t)buf[3]); break;
    case CMD_MOVE_X_MM:   Chassis_MoveX_mm((int8_t)buf[1]);   break;
    case CMD_MOVE_Y_MM:   Chassis_MoveY_mm((int8_t)buf[2]);   break;

    case CMD_SHOW_QRCODE: {
        uint16_t c1 = be16(buf[4], buf[5]);
        uint16_t c2 = be16(buf[6], buf[7]);
        uint8_t i;
        for (i = 0; i < 5; i++) {            /* 连发 5 次防丢包 */
            u2_printf("t3.txt=\"%d+%d\"", c1, c2);
            delay_ms(10);
        }
        break;
    }

    case CMD_GRAB_TRAY:   Claw_GrabFromTray();   break;
    case CMD_PUT_LAYER1:  Claw_PutLayer1();      break;
    case CMD_GRAB_GROUND: Claw_GrabFromGround(); break;   /* v16 */
    case CMD_PUT_LAYER2:  Claw_PutLayer2();      break;   /* v16 */
    case CMD_FINAL:       Claw_Final();          break;   /* v16 */

    case CMD_LIFT_GOTO:                          /* buf[1] = 预设位编号 */
        switch (buf[1]) {
        case 1: Wrist_Front(); Lift_GoTo(LIFT_CAMERA);    break;
        case 2: Lift_GoTo(LIFT_BLOCK_GET);                break;
        case 3: Lift_GoTo(LIFT_SEE_CIRCLE);               break;   /* v16 识别圆环位 */
        case 4: Lift_GoTo(LIFT_SEE_BLOCK);                break;   /* v16 识别物块位 */
        }
        break;

    case CMD_LIFT_MOVE:                          /* ★统一大端(需树莓派同步改) */
        Lift_MoveBy((int16_t)be16(buf[1], buf[2]));
        break;

    case CMD_COMBO: {                            /* 边跑边升:双轴并行 */
        int8_t move_cm = (int8_t)buf[2];
        Chassis_MoveX_cm_Async(move_cm);         /* 底盘先跑,不等   */
        delay_ms(100);                           /* 错峰,防电流浪涌 */
        switch (buf[1]) {
        case 1: Wrist_Front(); Lift_GoTo_Async(LIFT_CAMERA);    break;
        case 2: Lift_GoTo_Async(LIFT_BLOCK_GET);                break;
        case 3: Lift_GoTo_Async(LIFT_SEE_CIRCLE);               break;   /* v16 */
        case 4: Lift_GoTo_Async(LIFT_SEE_BLOCK);                break;   /* v16 */
        case 5:                                                          /* v16 离开原料区:载物姿态 */
        case 6: Lift_GoTo_Async(LIFT_TOP);                      break;   /* v16 离开粗加工区:同上 */
        }
        Chassis_WaitDone();                      /* 双守门:两轴都收工 */
        Lift_WaitDone();
        break;
    }

    default: break;
    }
}

/**********************************************************************
 * main.c —— 五幕结构(与原版逻辑一致,只是每幕都能读懂了)
 *********************************************************************/
int main(void)
{
    /* ── 第 1 幕:上电初始化 ── */
    system_Init();          /* 时钟/串口/LED/按键/灯带(原模块,零改动) */
    Chassis_Init();
    Claw_Init();
    MOTOR5_Init();          /* 载物台(轴已由 ramp.c 接管) */

    /* ── 第 2 幕:机械归位(v7:先盲升归顶,舵机动作全部在最高点做,
     *    夹爪旋转扫过的空间里没有任何障碍,保护3D打印件) ── */
    Lift_Home();            /* ★上电先盲升到顶,从任意位置找回参考点 */
    Grip_Open();
    Wrist_Front();
    delay_ms(1000);         /* 舵机无反馈,时间估算到位 */
    Wrist_Back();

    /* ── 第 3 幕(v13):等树莓派上线,握手即发车 ──
     * 收到 0xFD 后:
     *   ① 灯带亮黄(若接了灯带)
     *   ② 腕关节"点头"一下 —— 无灯带时的可见确认:下行链路通
     *   ③ 立即回执 FF 01 FE —— 上位机 wait_for_start_cmd 收到即放行
     * 原第 4 幕"等 KEY1 按键"删除:扩展板按键为外接件,现场无按键,
     * 发车时机由树莓派运行脚本的时刻决定。 */
    while (1) {
        if (Serial1_GetRxFlag() && Serial_RXPacket[0] == PKT_HANDSHAKE_UP) {
            ws2812_AllOpen(255, 255, 0);
            /* 腕关节点头:从朝后位小幅摆出再回位,安全且可见 */
            SERVO3_CONTROL(SERVO_WRIST_BACK + 30);
            delay_ms(300);
            Wrist_Back();
            delay_ms(200);
            UART_SendPacket2UP(PKT_ACK_DONE);
            break;
        }
    }

    /* ── 第 5 幕:主循环 —— 收包 → 执行 → 回执,小车的余生 ── */
    while (1) {
        if (Serial1_GetRxFlag()) {
            Protocol_Handle(Serial_RXPacket);
            UART_SendPacket2UP(PKT_ACK_DONE);
        }
    }
}
