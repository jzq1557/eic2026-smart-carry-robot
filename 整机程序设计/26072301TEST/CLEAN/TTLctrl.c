/**********************************************************************
 * TTLctrl.c —— TTL 串口控制四路 Emm_V5.0 闭环电机(原 Demo3 更名重构)
 *
 * 重构要点(相对 demo3):
 *   - 往复演示逻辑移除,改为"三组独立、参数化、非阻塞"控制函数:
 *       TTLctrl_Motor1   单轴(id1 伸缩滑台)
 *       TTLctrl_Motor23  打包(id2+id3 龙门架,镜像互反,同步启停)
 *       TTLctrl_Motor4   单轴(id4 base轴)
 *   - 每个函数:方向(dir)/速度(vel)/距离(clk)全参数化
 *   - 发完串口命令立即返回,不等待执行完毕(非阻塞)
 *********************************************************************/
#include "system_init.h"
#include "car_config.h"
#include "Emm_V5.h"
#include "TTLctrl.h"

/* ============ USART3 初始化(PC10 TX / PC11 RX,部分重映射) ============ */
static void ttlctrl_usart3_init(void)
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

void TTLctrl_Init(void)
{
	ttlctrl_usart3_init();

	/* 上电延时:等四路 Emm_V5.0 驱动器初始化完毕(test 例程用 2s) */
	delay_ms(2000);

	/* 四路电机使能(关闭多机同步标志,立即生效) */
	Emm_V5_En_Control(1, true, false);
	Emm_V5_En_Control(2, true, false);
	Emm_V5_En_Control(3, true, false);
	Emm_V5_En_Control(4, true, false);
	delay_ms(100);
}

/* ============ 单轴非阻塞控制 ============
 * snF=false → 命令立即执行,发完即返回,不等待 */
static void ttlctrl_move_single(uint8_t addr, uint8_t dir, uint16_t vel, uint32_t clk)
{
	Emm_V5_Pos_Control(addr, dir, vel, TTLCTRL_ACC_DEFAULT, clk, 0, false);
}

/* id1 伸缩滑台:0=正转(前伸), 1=反转(后缩) */
void TTLctrl_Motor1(uint8_t dir, uint16_t vel, uint32_t clk)
{
	ttlctrl_move_single(1, dir, vel, clk);
}

/* id2/id3 龙门架(打包):
 *   dir 传龙门架运动方向,内部 id2=dir、id3=反向(镜像互反);
 *   两轴命令带 snF=true 暂存,再对广播地址 0 同步触发 → 同时启停;
 *   发完立即返回,不等待运动完成 */
void TTLctrl_Motor23(uint8_t dir, uint16_t vel, uint32_t clk)
{
	Emm_V5_Pos_Control(2, dir,      vel, TTLCTRL_ACC_DEFAULT, clk, 0, true);
	delay_ms(10);                                   /* 防粘包 */
	Emm_V5_Pos_Control(3, (uint8_t)!dir, vel, TTLCTRL_ACC_DEFAULT, clk, 0, true);
	delay_ms(10);                                   /* 防粘包 */

	Emm_V5_Synchronous_motion(0);                   /* 广播地址 0 触发,两轴同时起步 */
}

/* id4 base轴:0=正转(逆时针), 1=反转(顺时针) */
void TTLctrl_Motor4(uint8_t dir, uint16_t vel, uint32_t clk)
{
	ttlctrl_move_single(4, dir, vel, clk);
}
