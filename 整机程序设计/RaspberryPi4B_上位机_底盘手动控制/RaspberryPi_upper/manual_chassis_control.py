#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Raspberry Pi 4B / Python 3.7
STM32F103ZET6 chassis manual control, using the ORIGINAL project protocol.

Original frame format:
    0xFF + 8-byte payload + 0xFE
ACK from STM32:
    0xFF 0x01 0xFE

Original active firmware command mapping:
    payload[0] = 0x01 : X displacement in cm, payload[1] = int8
                         positive = forward, negative = backward
    payload[0] = 0x03 : rotate in degrees, payload[3] = int8
                         positive = left/CCW, negative = right/CW

No STM32 motor pin, timer, PWM or direction mapping is changed.
"""

import argparse
import sys
import time
import serial

PKT_HEAD = 0xFF
PKT_TAIL = 0xFE
PKT_HANDSHAKE_UP = 0xFD
PKT_ACK_DONE = 0x01

CMD_MOVE_X_CM = 0x01
CMD_ROTATE_DEG = 0x03


def int8_to_u8(value):
    value = int(value)
    if value < -128 or value > 127:
        raise ValueError("int8 value must be in [-128, 127]")
    return value & 0xFF


def make_frame(command=0, p1=0, p2=0, p3=0, p4=0, p5=0, p6=0, p7=0):
    """Build FF + 8 payload bytes + FE, exactly matching uart.c."""
    return bytes([
        PKT_HEAD,
        command & 0xFF,
        p1 & 0xFF,
        p2 & 0xFF,
        p3 & 0xFF,
        p4 & 0xFF,
        p5 & 0xFF,
        p6 & 0xFF,
        p7 & 0xFF,
        PKT_TAIL,
    ])


def wait_ack(ser, timeout_s=60.0):
    """Wait for FF 01 FE; tolerates stray bytes and partial reads."""
    deadline = time.time() + timeout_s
    state = 0
    while time.time() < deadline:
        b = ser.read(1)
        if not b:
            continue
        v = b[0]
        if state == 0:
            if v == PKT_HEAD:
                state = 1
        elif state == 1:
            if v == PKT_ACK_DONE:
                state = 2
            elif v == PKT_HEAD:
                state = 1
            else:
                state = 0
        else:
            if v == PKT_TAIL:
                return True
            state = 1 if v == PKT_HEAD else 0
    return False


class ChassisController(object):
    def __init__(self, port, baudrate=115200, ack_timeout=60.0):
        self.port = port
        self.baudrate = int(baudrate)
        self.ack_timeout = float(ack_timeout)
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.10,
            write_timeout=1.0,
        )
        time.sleep(0.20)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        print("串口已打开: {} @ {} 8N1".format(self.port, self.baudrate))

    @staticmethod
    def _hex(data):
        return " ".join("{:02X}".format(x) for x in data)

    def _send_and_wait(self, frame, name):
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()
        print("TX [{}]: {}".format(name, self._hex(frame)))
        if not wait_ack(self.ser, self.ack_timeout):
            raise RuntimeError(
                "等待 STM32 回执 FF 01 FE 超时。请检查 PD5/PD6 交叉接线、GND 共地、115200，"
                "以及 STM32 是否已进入主循环。"
            )
        print("RX [ACK]: FF 01 FE")

    def handshake(self):
        frame = make_frame(PKT_HANDSHAKE_UP)
        self._send_and_wait(frame, "HANDSHAKE")

    def forward(self, cm):
        cm = abs(int(cm))
        if cm < 1 or cm > 127:
            raise ValueError("前进距离需为 1~127 cm")
        frame = make_frame(CMD_MOVE_X_CM, p1=int8_to_u8(cm))
        self._send_and_wait(frame, "FORWARD {}cm".format(cm))

    def backward(self, cm):
        cm = abs(int(cm))
        if cm < 1 or cm > 128:
            raise ValueError("后退距离需为 1~128 cm")
        frame = make_frame(CMD_MOVE_X_CM, p1=int8_to_u8(-cm))
        self._send_and_wait(frame, "BACKWARD {}cm".format(cm))

    def turn_left(self, degree):
        degree = abs(int(degree))
        if degree < 1 or degree > 127:
            raise ValueError("左转角度需为 1~127 degree")
        frame = make_frame(CMD_ROTATE_DEG, p3=int8_to_u8(degree))
        self._send_and_wait(frame, "LEFT {}deg".format(degree))

    def turn_right(self, degree):
        degree = abs(int(degree))
        if degree < 1 or degree > 128:
            raise ValueError("右转角度需为 1~128 degree")
        frame = make_frame(CMD_ROTATE_DEG, p3=int8_to_u8(-degree))
        self._send_and_wait(frame, "RIGHT {}deg".format(degree))

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()


def interactive(ctrl, distance_cm, turn_degree):
    print("")
    print("========== 底盘手动控制 ==========")
    print("W = 前进 {} cm".format(distance_cm))
    print("S = 后退 {} cm".format(distance_cm))
    print("A = 左转 {} 度".format(turn_degree))
    print("D = 右转 {} 度".format(turn_degree))
    print("H = 重新握手")
    print("Q = 退出")
    print("==================================")
    while True:
        key = input("命令 W/S/A/D/H/Q > ").strip().lower()
        try:
            if key == "w":
                ctrl.forward(distance_cm)
            elif key == "s":
                ctrl.backward(distance_cm)
            elif key == "a":
                ctrl.turn_left(turn_degree)
            elif key == "d":
                ctrl.turn_right(turn_degree)
            elif key == "h":
                ctrl.handshake()
            elif key == "q":
                return
            elif key == "":
                continue
            else:
                print("无效命令，只能输入 W/S/A/D/H/Q")
        except Exception as exc:
            print("ERROR: {}".format(exc))


def main():
    parser = argparse.ArgumentParser(description="STM32F103ZET6 底盘 W/S/A/D 手动控制")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="树莓派串口，例如 /dev/ttyUSB0 或 /dev/serial0")
    parser.add_argument("--baud", type=int, default=115200, help="原工程波特率，默认 115200")
    parser.add_argument("--distance", type=int, default=20, help="W/S 每次移动距离 cm，默认 20")
    parser.add_argument("--turn", type=int, default=90, help="A/D 每次转向角度，默认 90")
    parser.add_argument("--ack-timeout", type=float, default=60.0, help="动作完成回执超时秒数")
    parser.add_argument("--skip-handshake", action="store_true", help="不发送上线握手，直接进入控制")
    args = parser.parse_args()

    ctrl = None
    try:
        ctrl = ChassisController(args.port, args.baud, args.ack_timeout)
        if not args.skip_handshake:
            print("发送上线握手...")
            ctrl.handshake()
            print("握手成功，STM32 已进入命令模式。")
        interactive(ctrl, args.distance, args.turn)
    except serial.SerialException as exc:
        print("串口错误: {}".format(exc))
        return 2
    except KeyboardInterrupt:
        print("\n用户退出")
    finally:
        if ctrl is not None:
            ctrl.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
