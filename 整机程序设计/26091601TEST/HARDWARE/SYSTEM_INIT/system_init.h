#ifndef _SYSTEM_INIT_H
#define _SYSTEM_INIT_H

#include "sys.h"
#include "delay.h"
#include "usart.h"
#include "led.h"
#include "key.h"
#include "motor.h"
#include "oled.h"
#include "uart.h"
/* #include "control.h"      已由 CLEAN/chassis+ramp 覆盖,移出编译 */
#include "servo.h"
#include "time_count.h"
/* #include "control_claw.h" 已由 CLEAN/claw 覆盖,移出编译       */
#include "ws2812.h"

void system_Init(void);





#endif
