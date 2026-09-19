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
//#include "direction_check.h"   /* 方向/线序校准 demo(默认关) */

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

    /* ===== TTL 闭环电机命令(0x10+,经 USART3→张大头驱动器) =====
     * 参数布局: buf[1]=dir  buf[2..3]=vel 16位大端  buf[4..7]=clk 32位大端
     * ★非阻塞:发完立即回执。瞄准闭环的"到位"由上位机视觉判定
     * (圆心居中),上位机按运动耗时 sleep 控制节奏,勿依赖本端等待。 */
    case CMD_TTL_MOTOR1: {
        uint8_t  dir = buf[1];
        uint16_t vel = be16(buf[2], buf[3]);
        uint32_t clk = ((uint32_t)buf[4] << 24) | ((uint32_t)buf[5] << 16)
                     | ((uint32_t)buf[6] << 8)  |  (uint32_t)buf[7];
        TTLctrl_Motor1(dir, vel, clk);
        break;
    }
    case CMD_TTL_MOTOR23: {
        uint8_t  dir = buf[1];
        uint16_t vel = be16(buf[2], buf[3]);
        uint32_t clk = ((uint32_t)buf[4] << 24) | ((uint32_t)buf[5] << 16)
                     | ((uint32_t)buf[6] << 8)  |  (uint32_t)buf[7];
        TTLctrl_Motor23(dir, vel, clk);
        break;
    }
    case CMD_TTL_MOTOR4: {
        uint8_t  dir = buf[1];
        uint16_t vel = be16(buf[2], buf[3]);
        uint32_t clk = ((uint32_t)buf[4] << 24) | ((uint32_t)buf[5] << 16)
                     | ((uint32_t)buf[6] << 8)  |  (uint32_t)buf[7];
        TTLctrl_Motor4(dir, vel, clk);
        break;
    }
    case CMD_TTL_PLACE:   TTLctrl_Act_Place();  break;   /* 整组放料(内部阻塞) */
    case CMD_TTL_GRIP:    /* buf[1]=0 开 / 1 闭 */
        if (buf[1] == 0) Grip_Open();
        else             Grip_Close();
        break;
    case CMD_TTL_GRIP_ANG:                        /* 夹爪任意角度:buf[1]=0~255° */
        Grip_Angle(buf[1]);
        break;
    case CMD_TTL_GRAB_VEH:                        /* 去物架夹料: buf[1]=物架位 1/2/3 */
        TTLctrl_Act_GrabFromVehicle(buf[1] ? buf[1] : 1);
        break;

    default: break;
    }
}

/**********************************************************************
 * ==================== 手动调试序列(夹取-放置-归位) ====================
 * 原 main 里的大段调试动作,打包成独立函数。
 * 默认 main 不调用(直接进协议循环);需要调试时把 main 里的
 *   #define RUN_DEBUG_SEQ 取消注释即可上电先跑一遍。
 *********************************************************************/
static void Debug_ManualSeq(void)
{
	/* ══ 夹取地上物料置于右 + 归位 ══
	 * (第一套开头先小抬 1200,与后两套不同,勿合并) */
	TTLctrl_Motor23(1, 80, 1200);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架(看 id2)到位 */

	Grip_Close();                   /* 到位后夹爪闭合 */
	delay_ms(500);

	TTLctrl_Motor23(1, 80, 6000);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架到位 */

	TTLctrl_Motor4(1, 200, 1800);
	TTLctrl_WaitReached(4, 0);      /* 等 base 轴到位 */

	TTLctrl_Motor1(1, 300, 8850);
	TTLctrl_WaitReached(1, 0);      /* 等伸缩臂真正到位 */

	TTLctrl_Motor23(0, 80, 200);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架(看 id2)到位 */

	delay_ms(500);                  /* ★ 到位后再延时 0.5s */
	Grip_Open();                    /* 到位+延时后才打开夹爪 */

	TTLctrl_Motor23(1, 80, 200);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架(看 id2)到位 */
	TTLctrl_Motor1(0, 300, 8850);
	TTLctrl_WaitReached(1, 0);      /* 等伸缩臂真正到位 */
	TTLctrl_Motor4(0, 200, 1800);
	TTLctrl_WaitReached(4, 0);      /* 等 base 轴到位 */
	TTLctrl_Motor23(0, 80, 6000);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架到位 */

	/* ══ 夹取地上物料置于中 + 归位 ══ */
	Grip_Close();                   /* 到位后夹爪闭合 */
	delay_ms(500);

	TTLctrl_Motor23(1, 80, 6000);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架到位 */

	TTLctrl_Motor1(1, 300, 8850);
	TTLctrl_WaitReached(1, 0);      /* 等伸缩臂真正到位 */

	TTLctrl_Motor23(0, 80, 200);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架(看 id2)到位 */

	delay_ms(500);                  /* ★ 到位后再延时 0.5s */
	Grip_Open();                    /* 到位+延时后才打开夹爪 */

	TTLctrl_Motor23(1, 80, 200);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架(看 id2)到位 */
	TTLctrl_Motor1(0, 300, 8850);
	TTLctrl_WaitReached(1, 0);      /* 等伸缩臂真正到位 */
	TTLctrl_Motor23(0, 80, 6000);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架到位 */

	/* ══ 夹取地上物料置于左 + 归位 ══ */
	Grip_Close();                   /* 到位后夹爪闭合 */
	delay_ms(500);

	TTLctrl_Motor23(1, 80, 6000);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架到位 */

	TTLctrl_Motor4(0, 200, 1800);
	TTLctrl_WaitReached(4, 0);      /* 等 base 轴到位 */

	TTLctrl_Motor1(1, 300, 8850);
	TTLctrl_WaitReached(1, 0);      /* 等伸缩臂真正到位 */

	TTLctrl_Motor23(0, 80, 200);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架(看 id2)到位 */

	delay_ms(500);                  /* ★ 到位后再延时 0.5s */
	Grip_Open();                    /* 到位+延时后才打开夹爪 */

	TTLctrl_Motor23(1, 80, 200);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架(看 id2)到位 */
	TTLctrl_Motor1(0, 300, 8850);
	TTLctrl_WaitReached(1, 0);      /* 等伸缩臂真正到位 */
	TTLctrl_Motor4(1, 200, 1800);
	TTLctrl_WaitReached(4, 0);      /* 等 base 轴到位 */
	TTLctrl_Motor23(0, 80, 6000);
	TTLctrl_WaitReached(2, 0);      /* 等龙门架到位 */
}

/* ★测试:右移20cm → 停500ms → 前进100cm → 停1000ms */
void MoveTest(void)
{
  Chassis_MoveY_cm(-10);   /* 右移 20cm  (负=右, 已实测校正) */
  delay_ms(500);           /* 停 500ms   */

  Chassis_MoveX_cm( 100);  /* 前进 100cm (正=前; 100 在 int8 ±127 内) */
  delay_ms(1000);          /* 停 1000ms  */
	delay_ms(10000);
}

/**********************************************************************
 上电流程:
  - 初始化 → TTLctrl_Init(使能四路闭环电机)
  - 默认:直接进入协议主循环,等树莓派发命令
  - 需要跑手动调试序列时:取消下一行注释
 *********************************************************************/
//#define RUN_DEBUG_SEQ
//#define RUN_DIRECTION_DEMO    /* ★方向校准:打开后上电自动连续跑六向demo(无按键;跑完DCHK_CYCLES轮或断电停止) */

int main(void)
{
    /* ── 第 1 幕:上电初始化 ── */
    system_Init();          /* 时钟/串口/LED/按键/灯带(原模块,零改动) */
    Chassis_Init();
    Claw_Init();
    MOTOR5_Init();          /* 载物台(轴已由 ramp.c 接管) */

    /* ── 第 2 幕:机械归位 ── */
    //Grip_Open();
		delay_ms(1000);

    /* ── 第 3 幕:TTL 串口闭环电机控制(PC10/PC11) ── */
    TTLctrl_Init();         /* USART3 初始化 + 使能 id1~4(阻塞:含 2s 上电延时) */

//#ifdef RUN_DEBUG_SEQ
    //Debug_ManualSeq();      /* 手动调试序列:夹取右/中/左各一遍(跑完进协议循环) */
//#endif

//#ifdef RUN_DIRECTION_DEMO
    //Direction_Check_Run();  /* ★方向校准:上电自动连续跑六向(无按键);跑完 DCHK_CYCLES 轮后才进入下方协议循环 */
//#endif

    /* ★正式瞄准-放料流程:树莓派发 0xFF 帧命令(见 aim_and_place.py),
     * 本循环实时响应:
     *   0x10/0x11/0x12 → TTLctrl_Motor1/23/4(非阻塞,视觉闭环节奏)
     *   0x13 → 放料动作组(下放-开爪-抬升)   0x14 → 夹爪开/闭
     * 回执 [0xFF 0x01 0xFE] = 命令已受理 */
    while (1)
    {
			//MoveTest(); 
        if (Serial1_GetRxFlag())
        {
            Protocol_Handle(Serial_RXPacket);
            UART_SendPacket2UP(PKT_ACK_DONE);
        }
		
    }
}
