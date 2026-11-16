#include "uart.h"
#include "sys.h"
#include <stdio.h>
#include "stdarg.h"    
#include "string.h" 

/**********************************************************************
 * v12:双串口协议兼容(基于v11)
 *
 * 背景:验证可运行的旧代码走 USART1 —— 说明核心板自带的USB转串口
 *      (CH340,内接PA9/PA10)是真实通路;扩展板"串口2"座(H7,
 *      PD5/PD6)是另一条通路。为免再猜,两条全部支持:
 * 改动:① USART1 与 USART2 中断各带一套收包状态机,
 *        写同一个 Serial_RXPacket / Serial_Flag
 *      ② 回执 UART_SendPacket2UP 同时从两个口发出
 *      ③ u2_printf 保持置空(无串口屏;若发文本会污染协议流)
 * 接线二选一:
 *   A. USB线: 树莓派USB ↔ 核心板"USB转串口"口(确认PA9/PA10跳线帽在位)
 *   B. TTL小板: TX→板上RX2, RX→板上TX2, GND共地, 5V不接
 *********************************************************************/

uint8_t Serial_Flag;

uint8_t Serial_TXPacket[3];
uint8_t Serial_RXPacket[8];

uint8_t USART2_TX_BUF[100];


// PA9    TX
// PA10   RX
/********************************
函数功能 : 串口1的初始化(v11:保留,不再用于协议)
*********************************/
void UART1_Init(void)
{
	GPIO_InitTypeDef GPIO_InitStructure;
	NVIC_InitTypeDef NVIC_InitStructure;
	USART_InitTypeDef USART_InitStructure;
	
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA,ENABLE);
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_USART1,ENABLE);
	
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_9;
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF_PP;
	GPIO_Init(GPIOA, &GPIO_InitStructure);

	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_10;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IN_FLOATING;
	GPIO_Init(GPIOA, &GPIO_InitStructure); 
	
	NVIC_InitStructure.NVIC_IRQChannel = USART1_IRQn;
	NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority=0 ;
	NVIC_InitStructure.NVIC_IRQChannelSubPriority = 0;
	NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;
	NVIC_Init(&NVIC_InitStructure);	
	
	USART_InitStructure.USART_BaudRate = 115200;
	USART_InitStructure.USART_WordLength = USART_WordLength_8b;
	USART_InitStructure.USART_StopBits = USART_StopBits_1;
	USART_InitStructure.USART_Parity = USART_Parity_No;
	USART_InitStructure.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
	USART_InitStructure.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;	
  USART_Init(USART1, &USART_InitStructure); 
	
  USART_ITConfig(USART1, USART_IT_RXNE, ENABLE);
	
  USART_Cmd(USART1, ENABLE);
}

void USART1_SendBits(uint8_t data)
{
	USART1->DR = data;
	while(USART_GetFlagStatus(USART1,USART_FLAG_TXE) == RESET);
}

void UART1_SendArray(uint8_t *array,uint8_t length)
{
	uint8_t i;
	for(i=0;i<length;i++)
	{
		USART1_SendBits(array[i]);
	}
}

void UART1_SendString(uint8_t *String1)
{
	uint8_t i;
	for(i=0;String1[i]!='\0';i++)
	{
		USART1_SendBits(String1[i]);		
	}
}

void UART1_SendNum(uint32_t Number,uint8_t Length)
{
	uint8_t i;
	for(i=0;i<Length;i++)
	{
		USART1_SendBits(Number/Serial_Pow(10,Length-i-1)%10 + '0');
	}
}

uint32_t Serial_Pow(uint32_t x,uint32_t y)
{
	uint32_t Result=1;
	while(y--)
	{
		Result *= x;
	}
	return Result;
}

/********************************
函数功能 : 发送一个数据包 帧头0xff 帧尾0xfe
v11:改走串口2(树莓派实际连接的口)
*********************************/
void UART_SendPacket(uint8_t *Serial_TXPacket)
{
	uint8_t i;
	USART1_SendBits(0xFF);
	for(i=0;i<3;i++) USART1_SendBits(Serial_TXPacket[i]);
	USART1_SendBits(0xFE);
	USART2_SendBits(0xFF);
	for(i=0;i<3;i++) USART2_SendBits(Serial_TXPacket[i]);
	USART2_SendBits(0xFE);
}
/********************************
函数功能 : 向上位机发单字节回执包
v11:改走串口2
*********************************/
void UART_SendPacket2UP(uint8_t data)
{
	/* v12:两个口都发,树莓派插哪个都能收到 */
	USART1_SendBits(0xFF);
	USART1_SendBits(data);
	USART1_SendBits(0xFE);
	USART2_SendBits(0xFF);
	USART2_SendBits(data);
	USART2_SendBits(0xFE);
}
/********************************
串口1中断:v12 —— 协议收包状态机(与串口2共用缓冲与标志)
帧格式:0xFF + 8字节数据 + 0xFE
*********************************/
void USART1_IRQHandler(void)
{
	static uint8_t RX1State=0;
	static uint8_t pRx1Packet=0;
	if(USART_GetITStatus(USART1,USART_IT_RXNE) == SET)
	{
		uint8_t Rx1Data = USART_ReceiveData(USART1);
		if(RX1State == 0)
		{
			if(Rx1Data == 0xFF)
			{
					RX1State =1;
					pRx1Packet=0;
			}
		}
		else if(RX1State == 1)
		{
				Serial_RXPacket[pRx1Packet] = Rx1Data;
				pRx1Packet++;
				if(pRx1Packet>7)
				{
					RX1State=2;
				}
		}
		else if(RX1State == 2)
		{
			if(Rx1Data == 0xFE)
			{
					RX1State = 0;
					Serial_Flag = 1;
			}
			else
			{
					RX1State = 0;   /* 帧尾错,丢弃整包 */
			}
		}
		USART_ClearITPendingBit(USART1,USART_IT_RXNE);
	}
}

uint8_t Serial1_GetRxFlag(void)
{
	if(Serial_Flag == 1)
	{
		Serial_Flag=0;
		return 1;
	}
	return 0;
}

/******************************************************************************
PD5  TX2
PD6  RX2   —— v11:树莓派协议口
******************************************************************************/
void UART2_Init(void)
{
	GPIO_InitTypeDef GPIO_InitStructure;
	NVIC_InitTypeDef NVIC_InitStructure;
	USART_InitTypeDef USART_InitStructure;
	
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOD,ENABLE);
	RCC_APB1PeriphClockCmd(RCC_APB1Periph_USART2,ENABLE);
	RCC_APB2PeriphClockCmd(RCC_APB2Periph_AFIO,ENABLE);
	
	GPIO_PinRemapConfig(GPIO_Remap_USART2,ENABLE);
	
	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_5;
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF_PP;
	GPIO_Init(GPIOD, &GPIO_InitStructure);

	GPIO_InitStructure.GPIO_Pin = GPIO_Pin_6;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_IN_FLOATING;
	GPIO_Init(GPIOD, &GPIO_InitStructure); 
	
	NVIC_InitStructure.NVIC_IRQChannel = USART2_IRQn;
	NVIC_InitStructure.NVIC_IRQChannelPreemptionPriority=0 ;
	NVIC_InitStructure.NVIC_IRQChannelSubPriority = 0;
	NVIC_InitStructure.NVIC_IRQChannelCmd = ENABLE;
	NVIC_Init(&NVIC_InitStructure);	
	
	USART_InitStructure.USART_BaudRate = 115200;
	USART_InitStructure.USART_WordLength = USART_WordLength_8b;
	USART_InitStructure.USART_StopBits = USART_StopBits_1;
	USART_InitStructure.USART_Parity = USART_Parity_No;
	USART_InitStructure.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
	USART_InitStructure.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;	
  USART_Init(USART2, &USART_InitStructure); 
	
  USART_ITConfig(USART2, USART_IT_RXNE, ENABLE);
	
  USART_Cmd(USART2, ENABLE);
}

void USART2_SendBits(uint8_t data)
{
	USART2->DR = data;
	while(USART_GetFlagStatus(USART2,USART_FLAG_TXE) == RESET);
}


int fputc(int ch, FILE *f)
{
	USART2_SendBits(ch);
	
	return ch;
}
/********************************
串口2中断:v11 —— 协议收包状态机(原在串口1)
帧格式:0xFF + 8字节数据 + 0xFE
*********************************/
void USART2_IRQHandler(void)
{
	static uint8_t RX2State=0;
	static uint8_t pRx2Packet=0;
	if(USART_GetITStatus(USART2,USART_IT_RXNE) == SET)
	{
		uint8_t Rx2Data = USART_ReceiveData(USART2);
		if(RX2State == 0)
		{
			if(Rx2Data == 0xFF)
			{
					RX2State =1;
					pRx2Packet=0;
			}
		}
		else if(RX2State == 1)
		{
				Serial_RXPacket[pRx2Packet] = Rx2Data;
				pRx2Packet++;
				if(pRx2Packet>7)
				{
					RX2State=2;
				}
		}
		else if(RX2State == 2)
		{
			if(Rx2Data == 0xFE)
			{
					RX2State = 0;
					Serial_Flag = 1;
			}
			else
			{
					RX2State = 0;   /* 帧尾错,丢弃整包重新找头 */
			}
		}
		USART_ClearITPendingBit(USART2,USART_IT_RXNE);
	}
}


/********************************
v11:无串口屏,置空。原实现向串口2发屏幕文本,
现在串口2是树莓派协议口,发文本会污染ACK数据流导致上位机死等。
*********************************/
void u2_printf(char* fmt,...)  
{  
	(void)fmt;   /* 什么都不发 */
}
