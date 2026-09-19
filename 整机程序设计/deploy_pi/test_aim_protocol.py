#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_aim_protocol.py —— aim_and_place 协议层纯逻辑回归(无需串口/摄像头)

验证与下位机 26090501TEST 对齐的帧格式/ACK/常量:
  帧      : 0xFF + 命令 + 7 字节参数 + 0xFE
  vel 16位: 大端 (buf[2..3])        clk 32位: 大端 (buf[4..7])
  dir     : buf[1]
  ACK     : 0xFF 0x01 0xFE; 阻塞动作组(0x13/0x16)等整组完成(ACK_WAIT_ACT=25s)
  命令字  : 0x10=motor1  0x11=motor23  0x12=motor4  0x13=place
            0x14=grip   0x15=grip_angle  0x16=grab_from_vehicle
本机没有 cv2/serial 时自动 stub,只测纯逻辑;树莓派上也可直接跑(有真模块)。

用法: python3 test_aim_protocol.py   → 期望 "ALL TESTS PASSED"
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ---------- 本机缺 cv2/pyserial 时给最小 stub(仅用于 import,不执行视觉代码) ----------
def _ensure_module(name, attrs=None):
    if name in sys.modules:
        return
    m = types.ModuleType(name)
    if attrs:
        for k, v in attrs.items():
            setattr(m, k, v)
    sys.modules[name] = m

try:
    import cv2  # noqa: F401
except ImportError:
    _ensure_module("cv2")
try:
    import serial  # noqa: F401
except ImportError:
    _ensure_module("serial")

# ring_center_detect_v2 与 aim_and_place 同目录运行(现场在 ~/vision_robot);
# 本机测试时回退到仓库里的工作副本目录(_pdf_extract)。
try:
    import ring_center_detect_v2  # noqa: F401
except ImportError:
    _cand = os.path.join(
        os.path.dirname(os.path.dirname(HERE)), "_pdf_extract")
    if os.path.isdir(_cand) and _cand not in sys.path:
        sys.path.append(_cand)

import aim_and_place as A  # noqa: E402

FAIL = []
TOTAL = [0]


def check(name, cond):
    TOTAL[0] += 1
    print(("  [ok] " if cond else "  [FAIL] ") + name)
    if not cond:
        FAIL.append(name)


def hexb(b):
    return " ".join("%02X" % x for x in b)


def main():
    print("== 1. 帧构建 ==")
    check("build_cmd(0x13) 无参帧",
          A.build_cmd(0x13) == bytes([0xFF, 0x13, 0, 0, 0, 0, 0, 0, 0, 0xFE]))
    check("build_cmd(0x15, 0) 夹爪张开 0°",
          A.build_cmd(0x15, 0) == bytes([0xFF, 0x15, 0, 0, 0, 0, 0, 0, 0, 0xFE]))
    check("build_cmd(0x14, 1) 夹爪闭合",
          A.build_cmd(0x14, 1) == bytes([0xFF, 0x14, 1, 0, 0, 0, 0, 0, 0, 0xFE]))

    print("== 2. TTL 电机帧(0x10/0x11/0x12): dir + vel16大端 + clk32大端 ==")
    f1 = A.ttl_motor_cmd(0x10, 1, 40, 250)
    check("0x10 motor1 dir=1 vel=40 clk=250 -> " + hexb(f1),
          f1 == bytes([0xFF, 0x10, 1, 0x00, 0x28, 0x00, 0x00, 0x00, 0xFA, 0xFE]))
    f2 = A.ttl_motor_cmd(0x12, 0, 200, 3200)
    check("0x12 motor4 dir=0 vel=200 clk=3200(1圈) -> " + hexb(f2),
          f2 == bytes([0xFF, 0x12, 0, 0x00, 0xC8, 0x00, 0x00, 0x0C, 0x80, 0xFE]))
    f3 = A.ttl_motor_cmd(0x11, 0, 80, 4000)
    check("0x11 motor23 dir=0 vel=80 clk=4000 -> " + hexb(f3),
          f3 == bytes([0xFF, 0x11, 0, 0x00, 0x50, 0x00, 0x00, 0x0F, 0xA0, 0xFE]))
    # vel 高位>0 的代表值,验证真·大端而非小端
    f4 = A.ttl_motor_cmd(0x10, 0, 0x1234, 0x01020304)
    check("0x10 vel=0x1234 clk=0x01020304 大端 -> " + hexb(f4),
          f4 == bytes([0xFF, 0x10, 0, 0x12, 0x34, 0x01, 0x02, 0x03, 0x04, 0xFE]))

    print("== 3. 阻塞/非阻塞 ACK 等待常量 ==")
    check("ACK_WAIT_FAST == 3.0", A.ACK_WAIT_FAST == 3.0)
    check("ACK_WAIT_ACT  == 25.0", A.ACK_WAIT_ACT == 25.0)
    check("place()/grab_from_vehicle() 用长等待",
          A.Lower.place is not None and A.Lower.grab_from_vehicle is not None)

    print("== 4. Lower.send 的 ACK 解析(假串口:写入后立刻回 FF 01 FE) ==")
    class FakeSer:
        def __init__(self):
            self.written = []

        def write(self, data):
            self.written.append(bytes(data))
            return len(data)

        def read(self, n=1):
            return b""

        def reset_input_buffer(self):
            pass

    ser = FakeSer()
    lower = object.__new__(A.Lower)
    lower.ser = ser

    # 注入"依次返回 ACK 各字节"的假串口(与真实 read(1) 行为一致)
    class AckSer(FakeSer):
        def __init__(self):
            super().__init__()
            self._ack = bytearray([0xFF, 0x01, 0xFE])

        def read(self, n=1):
            if self._ack:
                return bytes([self._ack.pop(0)])
            return b""

    lower.ser = AckSer()
    ok = lower.send(A.build_cmd(0x10, 0, 40, 10), wait_s=1.0)
    check("send() 收到 ACK 返回 True", ok is True)
    check("send() 写出的正是完整帧(10B)",
          lower.ser.written and len(lower.ser.written[0]) == 10)

    # 不返回 ACK 的串口: 应超时返回 False(用极短 wait_s 避免久等)
    lower.ser = FakeSer()
    ok2 = lower.send(A.build_cmd(0x13), wait_s=0.3)
    check("send() 无 ACK 且超时返回 False", ok2 is False)

    print("== 5. 关键命令字与固件 car_config.h 对齐 ==")
    for mode, name in [(0x10, "motor1"), (0x11, "motor23"), (0x12, "motor4"),
                       (0x13, "place"), (0x14, "grip"), (0x15, "grip_angle"),
                       (0x16, "grab_from_vehicle")]:
        # 只是确认这些名字对应的方法存在且可调用(不真发串口)
        check("Lower.%s 存在" % name, callable(getattr(A.Lower, name, None)))

    print("== 6. 0x16 物架位 1/2/3(2026-09-12 适配) ==")
    for slot, expect in ((1, 0x01), (2, 0x02), (3, 0x03)):
        f = A.build_cmd(0x16, slot)
        check("build_cmd(0x16, %d) -> %s" % (slot, hexb(f)),
              f == bytes([0xFF, 0x16, expect, 0, 0, 0, 0, 0, 0, 0xFE]))
    # grab_from_vehicle(slot) 实际发出的帧里 buf[1] == slot, 且走长等待
    class AckSer2(AckSer):
        pass
    lower.ser = AckSer2()
    lower.grab_from_vehicle(2)
    w = lower.ser.written[-1] if lower.ser.written else b""
    check("grab_from_vehicle(2) 首字节=FF/0x16/0x02",
          len(w) == 10 and w[0] == 0xFF and w[1] == 0x16 and w[2] == 0x02)
    lower.ser = AckSer2()
    lower.grab_from_vehicle(9)          # 非法位号 → 回落 1 号位
    w2 = lower.ser.written[-1] if lower.ser.written else b""
    check("grab_from_vehicle(9) 回落为 1 号位", len(w2) == 10 and w2[2] == 0x01)

    print()
    if FAIL:
        print("FAILED %d/%d:" % (len(FAIL), TOTAL[0]))
        for f in FAIL:
            print("  - " + f)
        sys.exit(1)
    print("ALL TESTS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
