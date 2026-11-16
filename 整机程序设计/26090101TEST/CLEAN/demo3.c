/**********************************************************************
 * demo3.c —— Demo3:USART3 串口控制四路 Emm_V5.0 闭环电机往复运动
 *
 * 设计说明:
 *   - 发送底层在 Emm_V5.c 内(emm_send_cmd → USART3),初始化在本文件
 *   - 四路"同时启停"用 Emm_V5 多机同步:命令带 snF=true 暂存,
 *     再对广播地址 0 发一次同步触发(0xFF/0x66),四路几乎同时起步
 *   - "2、3 互反":镜像安装的龙门架两侧,命令方向相反、机械同时下移
 *   - 平滑曲线 = 位置模式 acc 参数(0 是直接启动,非 0 由驱动器内部加减速)
 *   - 机械对应:1=伸缩滑台 2/3=龙门架 4=base轴
 *********************************************************************/
#include "system_init.h"
#include "car_config.h"
#include "Emm_V5.h"
#include "demo3.h"

/* ============ USART3 初始化(PC10 TX / PC11 RX,部分重映射) ============ */
static void demo3_usart3_init(void)
{
	GPIO_InitTypeDef g;
	USART_InitTypeDef u;

	/* 时钟:GPIOC + AFIO(重映射开关) + USART3(APB1) */
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOC | RCC_APB2Periph_AFIO, ENABLE);
	RCC_APB1PeriphClockCmd(RCC_APB1Periph_USART3, ENABLE);

	/* USART3 部分重映射:TX=PC10, RX=PC11 */
	GPIO_PinRemapConfig(GPIO_PartialRemap_USART3, ENABLE);

	g.GPIO_Speed = GPIO_Speed_50MHz;
	g.GPIO_Mode  = GPIO_Mode_AF_PP;           /* PC10 TX */
	g.GPIO_Pin   = GPIO_Pin_10;
	GPIO_Init(GPIOC, &g);

	g.GPIO_Mode  = GPIO_Mode_IN_FLOATING;     /* PC11 RX */
	g.GPIO_Pin   = GPIO_Pin_11;
	GPIO_Init(GPIOC, &g);

	u.USART_BaudRate            = 115200;
	u.USART_WordLength          = USART_WordLength_8b;
	u.USART_StopBits            = USART_StopBits_1;
	u.USART_Parity              = USART_Parity_No;
	u.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
	u.USART_Mode                = USART_Mode_Tx | USART_Mode_Rx;
	USART_Init(USART3, &u);

	USART_Cmd(USART3, ENABLE);
}

void Demo3_Init(void)
{
	demo3_usart3_init();

	/* 上电延时:等四路 Emm_V5.0 驱动器初始化完毕(test 例程用 2s) */
	delay_ms(2000);

	/* 四路电机使能(关闭多机同步标志,立即生效) */
	Emm_V5_En_Control(1, true, false);
	Emm_V5_En_Control(2, true, false);
	Emm_V5_En_Control(3, true, false);
	Emm_V5_En_Control(4, true, false);
	delay_ms(100);
}

/* ============ 四路同时启停地走一段 ============
 * dir1/2/3/4:0=CW, 其余=CCW(Emm_V5 约定)
 * 所有命令带 snF=true(暂存),每条之间延时 10ms 防粘包(官方例程要求),
 * 最后对广播地址 0 发一次同步触发 → 四路同时起步 */
static void demo3_move(uint8_t dir1, uint8_t dir2, uint8_t dir3, uint8_t dir4)
{
	Emm_V5_Pos_Control(1, dir1, DEMO3_VEL_RPM, DEMO3_ACC, DEMO3_CLK, 0, true);
	delay_ms(10);                               /* 防粘包 */
	Emm_V5_Pos_Control(2, dir2, DEMO3_VEL_RPM, DEMO3_ACC, DEMO3_CLK, 0, true);
	delay_ms(10);                               /* 防粘包 */
	Emm_V5_Pos_Control(3, dir3, DEMO3_VEL_RPM, DEMO3_ACC, DEMO3_CLK, 0, true);
	delay_ms(10);                               /* 防粘包 */
	Emm_V5_Pos_Control(4, dir4, DEMO3_VEL_RPM, DEMO3_ACC, DEMO3_CLK, 0, true);
	delay_ms(10);                               /* 防粘包 */

	Emm_V5_Synchronous_motion(0);               /* 广播地址 0 触发多机同步 */
	delay_ms(10);

	/* 阻塞等待运动完成(运动时长按速度/脉冲数估算,含加减速余量) */
	delay_ms(DEMO3_MOVE_MS);
}

void Demo3_Run(void)
{
	while (1)
	{
		/* 正向段:1 正转、2 正转、3 反转、4 正转(2/3 互反,同时启停) */
		demo3_move(0, 0, 1, 0);
		delay_ms(DEMO3_PAUSE_MS);           /* 单向运动后停 1 秒 */

		/* 反向段:1 反转、2 反转、3 正转、4 反转(2/3 仍互反) */
		demo3_move(1, 1, 0, 1);
		delay_ms(DEMO3_PAUSE_MS);
	}
}
