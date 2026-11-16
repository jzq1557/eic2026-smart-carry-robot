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
#include "TTLctrl.h"

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

    case CMD_GRAB_TRAY:   Claw_GrabFromTray();  break;
    case CMD_PUT_LAYER1:  Claw_PutLayer1();     break;

    case CMD_LIFT_GOTO:                          /* buf[1] = 预设位编号 */
        switch (buf[1]) {
        case 1: Wrist_Front(); Lift_GoTo(LIFT_CAMERA);    break;
        case 2: Lift_GoTo(LIFT_BLOCK_GET);                break;
        /* 3/4 靶心识别位:补高度常量后同样一行 */
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
        }
        Chassis_WaitDone();                      /* 双守门:两轴都收工 */
        Lift_WaitDone();
        break;
    }

    default: break;
    }
}

/**********************************************************************
上电即跑 Demo 版

原五幕结构的第 3/4/5 幕已注释,通电后初始化+归位即进入循环。
 *********************************************************************/
int main(void)
{
    /* ── 第 1 幕:上电初始化 ── */
    system_Init();          /* 时钟/串口/LED/按键/灯带(原模块,零改动) */
    Chassis_Init();
    Claw_Init();
    MOTOR5_Init();          /* 载物台(轴已由 ramp.c 接管) */

    /* ── 第 2 幕:机械归位 ── */
    Grip_Open();
    //Wrist_Front();
    //delay_ms(1000);         /* 舵机无反馈,时间估算到位 */
    //Lift_GoTo(LIFT_TOP);
    //Wrist_Back();

    /* ── 第 3 幕:等树莓派上线 —— */
    // while (1) {
    //     if (Serial1_GetRxFlag() && Serial_RXPacket[0] == PKT_HANDSHAKE_UP) {
    //         ws2812_AllOpen(255, 255, 0);
    //         delay_ms(200);
    //         break;
    //     }
    // }

    /* ── 第 4 幕:等裁判发令 —— */
    // while (1) {
    //     if (clickKEY1()) {
    //         delay_ms(20);
    //         UART_SendPacket2UP(PKT_ACK_DONE);
    //         break;
    //     }
    // }

    /* ── 第 5 幕:TTL 串口闭环电机控制(PC10/PC11) ── */
    TTLctrl_Init();     /* USART3 初始化 + 使能 id1~4(阻塞:含 2s 上电延时) */
	Grip_Open();
	delay_ms(200);
	Grip_Close();
    delay_ms(200);
	

    /* 此后即可按需非阻塞调用(发完立即返回,不等运动完成):
     *   TTLctrl_Motor1( TTL_DIR_CW, 100, TTL_CLK_PER_REV);  // id1 滑台前伸一圈
     *   TTLctrl_Motor23(TTL_DIR_CW, 100, TTL_CLK_PER_REV);  // id2/3 龙门架同步下移一圈
     *   TTLctrl_Motor4( TTL_DIR_CW, 100, TTL_CLK_PER_REV);  // id4 base 逆时针一圈
     */

    /* 原主循环(树莓派协议)
    // while (1) {
    //     if (Serial1_GetRxFlag()) {
    //         Protocol_Handle(Serial_RXPacket);
    //         UART_SendPacket2UP(PKT_ACK_DONE);
    //     }
    // }
    */
    while (1) { }
}
