#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_serial_exec.py —— 串口执行适配器回归(假串口, 无需 pyserial)
用法: python test_serial_exec.py   → 期望 all OK
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from serial_exec import SerialLink, LinkError, ACK_DONE  # noqa: E402
from cmd_pack import pack_move_x_cm, pack_ttl             # noqa: E402


class FakeSer:
    """行为同 pyserial: write 记录; read(1) 每次吐一个字节。"""

    def __init__(self, ack=ACK_DONE, delay_writes=0):
        self.ack = bytearray(ack)
        self.writes = []
        self.n_reads = 0
        self.delay_writes = delay_writes  # 前 N 次 write 不喂 ACK(模拟超时重发)

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes.append(bytes(data))

    def read(self, n=1):
        self.n_reads += 1
        # 若 write 次数还没到 delay 阈值则先不返回 ACK 字节
        if len(self.writes) <= self.delay_writes and not self.ack_drained():
            return b""
        if self.ack:
            return bytes([self.ack.pop(0)])
        return b""

    def ack_drained(self):
        return len(self.ack) == 0


class TestSerialExec(unittest.TestCase):
    def test_import_without_pyserial_ok(self):
        # 模块 import 不触发 pyserial(本机无 pyserial 也能跑)
        self.assertIsNotNone(SerialLink)

    def test_send_ack_ok(self):
        ser = FakeSer()
        link = SerialLink(ser=ser)
        frame = pack_move_x_cm(10)
        self.assertTrue(link.send(frame, wait_s=0.5))
        self.assertEqual(ser.writes[0], frame)
        self.assertEqual(len(ser.writes), 1)

    def test_send_no_ack_false(self):
        ser = FakeSer(ack=b"")
        link = SerialLink(ser=ser)
        self.assertFalse(link.send(pack_move_x_cm(10), wait_s=0.2))
        self.assertEqual(len(ser.writes), 1)

    def test_retry_resend_until_ack(self):
        # 前 2 次 write 无回执, 第 3 次才回执; retries=2 → True
        ser = FakeSer(delay_writes=2)
        link = SerialLink(ser=ser)
        self.assertTrue(link.send(pack_ttl(0x12, 0, 200, 3200),
                                  wait_s=0.3, retries=2))
        self.assertEqual(len(ser.writes), 3)
        # 三帧内容相同
        self.assertTrue(all(w == ser.writes[0] for w in ser.writes))

    def test_retry_exhausted_false(self):
        ser = FakeSer(ack=b"", delay_writes=99)
        link = SerialLink(ser=ser)
        self.assertFalse(link.send(pack_move_x_cm(1), wait_s=0.2, retries=2))
        self.assertEqual(len(ser.writes), 3)

    def test_invalid_frame_raises(self):
        link = SerialLink(ser=FakeSer())
        with self.assertRaises(LinkError):
            link.send(b"\xFF\x01")                    # 短帧
        with self.assertRaises(LinkError):
            link.send(bytes([0xFF] + [0] * 8 + [0x00]))  # 尾不是 0xFE
        self.assertEqual(len(link._ser.writes), 0)     # 非法帧不发

    def test_open_requires_port_or_ser(self):
        link = SerialLink()
        with self.assertRaises(LinkError):
            link.open()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestSerialExec)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    ok = result.wasSuccessful()
    print("ALL TESTS PASSED" if ok else "TESTS FAILED")
    sys.exit(0 if ok else 1)
