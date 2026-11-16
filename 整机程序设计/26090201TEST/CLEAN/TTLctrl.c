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
#include "claw.h"           /* Grip_Open 放料用 */

/* ============ USART3 初始化(PC10 TX / PC11 RX,部分重映射) ============ */
static void ttlctrl_usart3_init(void)
{
	GPIO_InitTypeDef g;
	USART_InitTypeDef u;
	NVIC_InitTypeDef nvic;

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

	/* RX 中断:解析驱动器"到位返回"帧([地址] 0xFD 0x9F 0x6B) */
	nvic.NVIC_IRQChannel                   = USART3_IRQn;
	nvic.NVIC_IRQChannelPreemptionPriority = 1;
	nvic.NVIC_IRQChannelSubPriority        = 1;
	nvic.NVIC_IRQChannelCmd                = ENABLE;
	NVIC_Init(&nvic);

	USART_Cmd(USART3, ENABLE);
	USART_ITConfig(USART3, USART_IT_RXNE, ENABLE);
}

/* ============ 到位返回帧解析(USART3 RX 中断) ============
 * 驱动器 Response 菜单开启 Reached 后,电机到位会返回:
 *     [地址] 0xFD 0x9F 0x6B
 * 中断里滑动窗口匹配该模式,置位到位标志并回调 */
#define TTL_REACH_FRAME_LEN   4

static volatile uint8_t ttl_reached[8];          /* ttl_reached[addr] = 到位标志 */
static void (*ttl_reached_hook)(uint8_t addr);   /* 到位回调(中断上下文执行) */
static uint8_t ttl_rx_buf[TTL_REACH_FRAME_LEN];  /* 滑动窗口 */

void USART3_IRQHandler(void)
{
	if (USART_GetITStatus(USART3, USART_IT_RXNE) != RESET)
	{
		uint8_t b = (uint8_t)USART_ReceiveData(USART3);
		uint8_t i;

		/* 滑窗:新字节入队,检查 [addr][0xFD][0x9F][0x6B] */
		for (i = 1; i < TTL_REACH_FRAME_LEN; i++)
			ttl_rx_buf[i - 1] = ttl_rx_buf[i];
		ttl_rx_buf[TTL_REACH_FRAME_LEN - 1] = b;

		if (ttl_rx_buf[1] == 0xFD && ttl_rx_buf[2] == 0x9F && ttl_rx_buf[3] == 0x6B)
		{
			uint8_t addr = ttl_rx_buf[0];
			if (addr >= 1 && addr <= 7)
			{
				ttl_reached[addr] = 1;
				if (ttl_reached_hook) ttl_reached_hook(addr);
			}
		}
		USART_ClearITPendingBit(USART3, USART_IT_RXNE);
	}
}

void TTLctrl_SetReachedHook(void (*hook)(uint8_t addr))
{
	ttl_reached_hook = hook;
}

/* 清到位标志(发命令前调用,避免旧标志干扰本次等待) */
void TTLctrl_ClearReached(uint8_t addr)
{
	if (addr < 1 || addr > 7) return;
	ttl_reached[addr] = 0;
}

uint8_t TTLctrl_IsReached(uint8_t addr)
{
	if (addr < 1 || addr > 7) return 0;
	return ttl_reached[addr];
}

uint8_t TTLctrl_WaitReached(uint8_t addr, uint32_t timeout_ms)
{
	uint32_t waited = 0;
	if (addr < 1 || addr > 7) return 0;
	if (timeout_ms == 0) timeout_ms = 10000;   /* 默认 10s */

	TTLctrl_ClearReached(addr);                /* 清旧标志,等本次到位 */

	while (!ttl_reached[addr])
	{
		delay_ms(10);
		waited += 10;
		if (waited >= timeout_ms) return 0;    /* 超时 */
	}
	return 1;
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
	TTLctrl_Motor23(1, 80, 4000);
	TTLctrl_WaitReached(2, 0);      
	TTLctrl_Motor1(1, 300, 2000);
	TTLctrl_WaitReached(1, 0);      
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

/* ============ 组合动作组 ============
 * 动作组:抬起龙门架 → 伸缩臂向后 → base轴顺时针半圈(慢速)
 * 内部按"速度×脉冲数"估算耗时,顺序等待每步完成(阻塞) */
void TTLctrl_WaitDone(uint16_t vel, uint32_t clk)
{
	/* 耗时(ms)≈ clk 脉冲 / (vel RPM × 3200 脉冲/圈 / 60) ,再加加减速余量 400ms */
	uint32_t ms = (uint32_t)((uint64_t)clk * 60000UL / ((uint64_t)vel * TTL_CLK_PER_REV)) + 400UL;
	delay_ms(ms);
}

void TTLctrl_Act_RaiseRetractCw(void)
{
	/* ① 抬起龙门架:id2/3 反向(dir=1=抬起),1 圈 */
	TTLctrl_Motor23(1, 100, TTL_CLK_PER_REV);
	TTLctrl_WaitDone(100, TTL_CLK_PER_REV);

	/* ② 伸缩臂向后:id1 反转(dir=1=向后),1 圈 */
	TTLctrl_Motor1(1, 100, TTL_CLK_PER_REV);
	TTLctrl_WaitDone(100, TTL_CLK_PER_REV);

	/* ③ base 轴顺时针:id4 反转(dir=1=顺时针),慢速 50RPM、半圈 1600 */
	TTLctrl_Motor4(1, 50, TTL_CLK_PER_REV / 2);
	TTLctrl_WaitDone(50, TTL_CLK_PER_REV / 2);
}

/* ============ 放料动作组(瞄准圆心后调用) ============
 * 龙门架向下(dir=0=下移)放料高度 → 到位停留 → 开爪 → 抬升回原位
 * 每步等到位(WaitReached)或耗时(WaitDone)兜底,整组阻塞完成 */
void TTLctrl_Act_Place(void)
{
	/* ① 龙门架下放(夹爪带着物料到标靶上方) */
	TTLctrl_Motor23(0, TTL_PLACE_LOWER_VEL, TTL_PLACE_LOWER_CLK);
	TTLctrl_WaitReached(2, 0);               /* 等到位,10s 超时兜底 */

	/* ② 到位停留,让夹爪稳定 */
	delay_ms(TTL_PLACE_HOLD_MS);

	/* ③ 开爪放料 */
	Grip_Open();
	delay_ms(TTL_PLACE_OPEN_DELAY);

	/* ④ 龙门架抬升回原位 */
	TTLctrl_Motor23(1, TTL_PLACE_RAISE_VEL, TTL_PLACE_RAISE_CLK);
	TTLctrl_WaitReached(2, 0);
}

/* ============ 去车载物架夹料动作组 ★手调区(协议 0x16) ============
 * 调用时机: 瞄准完成、机械在靶心位之后。
 * 约定: 本动作组结束时,机械必须回到"观察位"(与瞄准起点一致:
 *       base/伸缩臂相对起点偏移 = 0),以便放置段用 aim_offset.txt
 *       里的记录偏移精确复现回靶心。
 * ★函数体由你在车上实测后填写(参照上面 Act_Place / Debug_ManualSeq 的写法)。
 *   示例顺序: 抬升→base转物架方向→伸缩到物架→下放→夹爪闭合→抬升→回观察位 */
void TTLctrl_Act_GrabFromVehicle(void)
{
	
	 TTLctrl_Motor23(1, 80, 3400);  TTLctrl_WaitReached(2, 0);  // 龙门架抬起
	 TTLctrl_Motor4 (1, 200, 1800);  TTLctrl_WaitReached(4, 0);  // base 转向物架
	 TTLctrl_Motor1 (1, 300, 6300);  TTLctrl_WaitReached(1, 0);  // 伸缩臂伸向物架
	 TTLctrl_Motor23(0, 80, 800);  TTLctrl_WaitReached(2, 0);  // 龙门架下放到料位
	 Grip_Close();                 delay_ms(600);               // 夹爪闭合抓料
	 TTLctrl_Motor23(1, 80, 800);  TTLctrl_WaitReached(2, 0);  // 抬起
	 TTLctrl_Motor1 (0, 300, 6300);  TTLctrl_WaitReached(1, 0);  // 伸缩回观察位
	 TTLctrl_Motor4 (0, 20, 1800);  TTLctrl_WaitReached(4, 0);  // base 回观察位
}
