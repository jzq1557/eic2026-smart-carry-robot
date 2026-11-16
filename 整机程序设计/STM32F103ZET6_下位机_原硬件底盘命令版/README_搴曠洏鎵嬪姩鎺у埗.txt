树莓派4B(Python 3.7) -> STM32F103ZET6 底盘手动控制
===================================================

一、严格沿用原工程硬件配置
1. STM32 串口2：USART2，完全按原工程重映射到 PD5(TX2)、PD6(RX2)
2. 波特率：115200，8位数据位，1位停止位，无校验(8N1)
3. 底盘四电机：原工程 MOTOR3/MOTOR4/MOTOR7/MOTOR8，不改任何引脚
4. 原协议：0xFF + 8字节payload + 0xFE
5. 动作完成回执：FF 01 FE

二、接线（走 USART2 / USB-TTL 时）
树莓派/USB-TTL TX -> STM32 PD6(RX2)
树莓派/USB-TTL RX -> STM32 PD5(TX2)
GND -> GND
不要把两个TX直连，也不要把5V电源线乱接。

三、四个动作直接复用原下位机命令
前进：CMD_MOVE_X_CM = 0x01，buf[1]为正数cm
后退：CMD_MOVE_X_CM = 0x01，buf[1]为负数cm的int8补码
左转：CMD_ROTATE_DEG = 0x03，buf[3]为正角度
右转：CMD_ROTATE_DEG = 0x03，buf[3]为负角度的int8补码

以20cm、90度为例，完整10字节帧：
前进20cm：FF 01 14 00 00 00 00 00 00 FE
后退20cm：FF 01 EC 00 00 00 00 00 00 FE
左转90度：FF 03 00 00 5A 00 00 00 00 FE
右转90度：FF 03 00 00 A6 00 00 00 00 FE
上线握手：FF FD 00 00 00 00 00 00 00 FE

四、STM32 下位机
这份工程当前 Keil 项目实际编译的是 CLEAN/chassis.c + CLEAN/protocol_and_main.c。
下位机已经具备上述四种动作，因此没有去改底层硬件控制代码：
- CLEAN/protocol_and_main.c
  CMD_MOVE_X_CM  -> Chassis_MoveX_cm((int8_t)buf[1])
  CMD_ROTATE_DEG -> Chassis_Rotate_deg((int8_t)buf[3])
- CLEAN/chassis.c
  正X=前进，负X=后退；正角=逆时针/左转，负角=顺时针/右转。

五、树莓派使用
进入 RaspberryPi_upper 目录：
  cd RaspberryPi_upper

确认串口：
  ls -l /dev/ttyUSB* /dev/serial* 2>/dev/null

如果未安装pyserial：
  python3 -m pip install pyserial

运行：
  python3 manual_chassis_control.py --port /dev/ttyUSB0

默认：
W 前进20cm
S 后退20cm
A 左转90度
D 右转90度
H 重新握手
Q 退出

自定义每次10cm、转45度：
  python3 manual_chassis_control.py --port /dev/ttyUSB0 --distance 10 --turn 45

六、重要说明
原上位机 models/MoveControl.py 中 self.serial.write(self.send_buffer) 传的是list；
Python3/pyserial更稳妥的写法是 bytes(self.send_buffer)。本发布包已只做这一兼容修复，
协议字段没有变化。

七、烧录前必须 Rebuild
上传工程中的旧 Template.hex 时间早于 CLEAN/protocol_and_main.c，因此发布包已删除旧 HEX。
请用 Keil5 打开 STM32_lower_car2/USER/Template.uvprojx，执行 Rebuild，确认 0 Error，
再使用新生成的 OBJ/Template.hex 烧录。
