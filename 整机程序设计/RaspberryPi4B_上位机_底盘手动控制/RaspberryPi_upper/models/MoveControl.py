import serial
from .config import Mode, HeightMode
import time


# 输入一个十进制的[-128, 127]范围内的整数，获取它补码的十进制
def get_int8(num):
    assert num <= 127 and num >= -128, print("int8类型范围为[-128, 127]")
    if num >= 0:
        return int(num)
    else:
        return int(255 + num + 1)


# 输入一个[-32768, 32767]范围内的整数，返回大端序的(高字节, 低字节)
# ★ 对应固件 car_config.h 的协议修复说明：0x0C 与 0x04 全部统一为大端
def get_int16_be(num):
    num = int(num)
    assert -32768 <= num <= 32767, print("int16类型范围为[-32768, 32767]")
    if num < 0:
        num += 0x10000          # 补码
    return (num >> 8) & 0xFF, num & 0xFF


# 运动控制类，包含小车的距离控制，以及旋转控制
class MoveControl(object):
    def __init__(self, port, baudrate) -> None:
        self.port = port
        self.baudrate = baudrate
        self.serial = serial.Serial(port, baudrate)
        print("与stm32串口初始化成功")
        self.serial.flush()

        # 初始化数据列表 [头帧，模式，X方向，Y方向，角度，二维码1，二维码2，尾帧]
        self.send_buffer = [0xFF, 0, 0, 0, 0, 0, 0, 0, 0, 0xFE]

    # ★ 新增：每次发送前清空数据区(下标1~8)，防止上一条命令的残留字节
    #   混入本条命令(固件虽只读相关字段，但清零可彻底杜绝隐患)
    def __clear_payload(self):
        for i in range(1, 9):
            self.send_buffer[i] = 0

    # 生成串口数据、发送数据并等待回传结果
    def __send_serial_msg(self, mode, x_dis=None, y_dis=None, angel=None, qr_info=None):
        self.__clear_payload()

        if mode == Mode.x_distance:
            assert x_dis != None, print("未指定x方向移动距离")
            self.send_buffer[1] = mode.value
            self.send_buffer[2] = get_int8(x_dis)

        elif mode == Mode.y_distance:
            assert y_dis != None, print("未指定y方向移动距离")
            self.send_buffer[1] = mode.value
            self.send_buffer[3] = get_int8(y_dis)

        elif mode == Mode.rotate:
            assert angel != None, print("未设置角度")
            self.send_buffer[1] = mode.value
            self.send_buffer[4] = get_int8(angel)

        elif mode == Mode.qrcode:
            assert qr_info != None, print("未输入二维码信息")
            self.send_buffer[1] = mode.value

            task1, task2 = int(qr_info[:3]), int(qr_info[4:])
            # 大端：高字节在前(与固件 be16(buf[4],buf[5]) / be16(buf[6],buf[7]) 一致)
            self.send_buffer[5], self.send_buffer[6] = get_int16_be(task1)
            self.send_buffer[7], self.send_buffer[8] = get_int16_be(task2)

        elif mode == Mode.x_dis_mm:
            assert x_dis != None, print("未指定x方向移动距离，单位:毫米")
            self.send_buffer[1] = mode.value
            self.send_buffer[2] = get_int8(x_dis)

        elif mode == Mode.y_dim_mm:
            assert y_dis != None, print("未指定y方向移动距离，单位:毫米")
            self.send_buffer[1] = mode.value
            self.send_buffer[3] = get_int8(y_dis)

        # 从原料区抓取物料块
        elif mode == Mode.grub_1:
            self.send_buffer[1] = mode.value

        # 从地面抓取物料块
        # ⚠ 注意：当前v4固件 Protocol_Handle 未实现 0x08，小车会空操作但仍回ACK
        elif mode == Mode.grub_2:
            self.send_buffer[1] = mode.value

        # 将物料块放到地面上
        elif mode == Mode.down_1:
            self.send_buffer[1] = mode.value

        # 在精加工区，将物料块放到另一个物料块
        # ⚠ 注意：当前v4固件 Protocol_Handle 未实现 0x0A，小车会空操作但仍回ACK
        elif mode == Mode.down_2:
            self.send_buffer[1] = mode.value

        # 调整升降台的位置(预设位编号 1~4；当前v4固件仅实现 1/2)
        elif mode == Mode.adjust_height:
            self.send_buffer[1] = mode.value
            self.send_buffer[2] = x_dis.value

        # ★ 修复：0x0C 升降台手动移动
        #   固件 v4 已统一为大端：Lift_MoveBy((int16_t)be16(buf[1], buf[2]))
        #   x_dis 传入 int16 步数(负数=下降)，拆成大端两字节放 buf[1]/buf[2]
        #   即 send_buffer[2]=高字节, send_buffer[3]=低字节
        elif mode == Mode.adjust_height_manual:
            assert x_dis != None, print("未指定升降台移动步数")
            self.send_buffer[1] = mode.value
            self.send_buffer[2], self.send_buffer[3] = get_int16_be(x_dis)

        # 混合模式：buf[1]=子模式(当前v4固件仅实现 1/2)，buf[2]=X方向距离cm
        elif mode == Mode.mix_mode:
            self.send_buffer[1] = mode.value
            self.send_buffer[2] = y_dis.value
            self.send_buffer[3] = get_int8(x_dis)

        # ⚠ 注意：0x0E 未在v4固件命令枚举中定义，小车会空操作但仍回ACK
        elif mode == Mode.final:
            self.send_buffer[1] = mode.value

        else:
            raise ValueError("下位机模式选择错误，现有的模式有", [mode.name for mode in Mode])

        self.serial.write(bytes(self.send_buffer))

        self.__wait_for_movement_done()
        time.sleep(0.05)

    # 在下发动作后，比如前进，旋转等，下位机执行结束后会回传任务结束指令[0xFF, 0x01, 0xFE]
    def __wait_for_movement_done(self):
        while True:
            header = ord(self.serial.read())
            if header == self.send_buffer[0]:
                if ord(self.serial.read()) == 0x01:
                    return True
                else:
                    pass
            else:
                pass

    # 程序启动后：先发上线握手 [0xFF, 0xFD, ..., 0xFE]，
    # 下位机等按键KEY1按下后回传启动指令 [0xFF, 0x01, 0xFE]（原注释0x10有误，固件实际发0x01）
    def wait_for_start_cmd(self):
        self.serial.write(bytes([0xFF, 0xFD, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFE]))
        self.__wait_for_movement_done()
        time.sleep(0.05)

    # 向下位机发发送二维码信息
    def send_qr_info(self, qr_info):
        assert qr_info, print("二维码信息为空")
        self.__send_serial_msg(mode=Mode.qrcode, qr_info=qr_info)

    # 距离控制，支持x和y同时输入，先运行x再运行y
    def set_distance(self, x=0, y=0):
        x = 1e-9 if x == 0 else x
        y = 1e-9 if y == 0 else y
        max_one_time_dis = 1.25
        if abs(x) > max_one_time_dis or abs(y) > max_one_time_dis:
            Warning("单次距离设置最远为{0}，需多次调用".format(max_one_time_dis))

        times_x, times_y = int(abs(x) / max_one_time_dis), int(
            abs(y) // max_one_time_dis
        )
        rest_x, rest_y = (
            abs(x) - times_x * max_one_time_dis,
            abs(y) - times_y * max_one_time_dis,
        )
        factor_x, factor_y = abs(x) // x, abs(y) // y

        for _ in range(times_x):
            dis = 125 * factor_x
            self.__send_serial_msg(mode=Mode.x_distance, x_dis=dis)
        if rest_x > 0.001:
            dis = factor_x * rest_x / max_one_time_dis * 125
            self.__send_serial_msg(mode=Mode.x_distance, x_dis=dis)
        else:
            pass

        for _ in range(times_y):
            dis = 125 * factor_y
            self.__send_serial_msg(mode=Mode.y_distance, y_dis=dis)
        if rest_y > 0.001:
            dis = factor_y * rest_y / max_one_time_dis * 125
            self.__send_serial_msg(mode=Mode.y_distance, y_dis=dis)
        else:
            pass

    # 较为精细的距离控制
    def move_in_mm(self, x=0, y=0):
        x = 127 if x > 127 else int(x)      # 修复：int8上限为127，原代码写128会触发get_int8断言
        x = -128 if x < -128 else int(x)
        y = 127 if y > 127 else int(y)
        y = -128 if y < -128 else int(y)
        if x != 0:
            self.__send_serial_msg(mode=Mode.x_dis_mm, x_dis=x)
        if y != 0:
            self.__send_serial_msg(mode=Mode.y_dim_mm, y_dis=y)

    # 旋转，角度定义为[-128, 127]之间
    def rotate(self, angle):
        assert angle <= 127 and angle >= -128, print("角度应当在[-128, 127]之间")
        self.__send_serial_msg(mode=Mode.rotate, angel=angle)

    # 右转
    def turn_right(self):
        self.rotate(-89)

    # 左转
    def turn_left(self):
        self.rotate(89)

    def test(self, num):
        self.__clear_payload()
        self.send_buffer[1] = 3
        self.send_buffer[4] = num

        self.serial.write(bytes(self.send_buffer))

    # 从原料区抓取物料块
    def grubBlockFromRawArea(self):
        self.__send_serial_msg(mode=Mode.grub_1)

    # 从地面抓取物料块（⚠ v4固件暂未实现0x08，需在固件Protocol_Handle补case）
    def grubBlockFromGround(self):
        self.__send_serial_msg(mode=Mode.grub_2)

    # 将物料块放到圆环上
    def putBlockToCircle(self):
        self.__send_serial_msg(mode=Mode.down_1)

    # 精加工区，将物料块放到另一个物料块上（⚠ v4固件暂未实现0x0A）
    def putBlockToAnotherBlock(self):
        self.__send_serial_msg(mode=Mode.down_2)

    # 自定义（⚠ v4固件命令枚举中没有0x0E）
    def final(self):
        self.__send_serial_msg(mode=Mode.final)

    # 调整升降台到合适的位置    1.读取二维码  2.识别原料区  3.识别圆环   4.识别物料块圆心
    # ⚠ v4固件的 CMD_LIFT_GOTO 目前仅实现预设位 1/2
    def adjustHeight(self, target_height):
        heightConfigs = [mode for mode in HeightMode]
        if target_height not in heightConfigs:
            raise ValueError("小车高度模式中没有这个模式{0}".format(target_height))
        self.__send_serial_msg(mode=Mode.adjust_height, x_dis=target_height)

    """
        ★ 新增实现：升降台手动微调 (对应固件 0x0C CMD_LIFT_MOVE)
        输入参数：
            delta_steps: int16 步数，正数上升，负数下降
                         (固件 Lift_MoveBy(int16_t delta_steps)，
                          换算参考 car_config.h：LIFT_STEPS_PER_MM = 64 步/毫米)
        返回参数：
            无；等待下位机回执后结束
    """
    def adjustHeightManual(self, delta_steps: int):
        self.__send_serial_msg(mode=Mode.adjust_height_manual, x_dis=delta_steps)

    # 便捷封装：按毫米微调升降台(内部换算为步数，64步/毫米)
    def adjustHeightManual_mm(self, delta_mm: float):
        LIFT_STEPS_PER_MM = 85          # 与固件 car_config.h 保持一致
        self.adjustHeightManual(int(delta_mm * LIFT_STEPS_PER_MM))

    """
        混合模式: 支持在小车的移动过程中移动升降台，减少时间
        输入参数：
            height_mode: 期望的模式，支持单纯的升降台移动，以及夹取后的直接离开
                         (⚠ v4固件的 CMD_COMBO 目前仅实现子模式 1/2；
                          3~6 会导致底盘移动但升降台不动)
            move_dis: 小车移动距离（仅限X方向移动，单位:米）
        返回参数：
            无；当接收到下位机信号后，函数结束
    """
    def move_while_adjusting_height(self, mix_mode, move_dis: float):
        if abs(move_dis) > 1.25:
            raise ValueError('太远了，这个模式最远支持1.25m')
        dis = 100 * move_dis
        self.__send_serial_msg(mode=Mode.mix_mode, x_dis=dis, y_dis=mix_mode)

    def __del__(self):
        self.serial.close()
        print("程序结束，释放串口")
