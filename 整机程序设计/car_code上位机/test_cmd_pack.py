#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_cmd_pack.py —— 串口帧打包回归(逐字节对齐固件 car_config.h 规则)
用法: python test_cmd_pack.py   → 期望 all OK
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cmd_pack import (PackError, pack_cmd, pack_move_x_cm, pack_move_y_cm,  # noqa: E402
                      pack_rotate_deg, pack_move_x_mm, pack_move_y_mm,
                      pack_show_qr, pack_lift_goto, pack_lift_move, pack_combo,
                      pack_grip, pack_grip_angle, pack_ttl, pack_grab_tray,
                      pack_put_layer1, pack_grab_vehicle,
                      PACK_PLACE, PACK_GRAB_FROM_VEHICLE)


def H(b):  # noqa: N802
    return b.hex(" ").upper()


class TestPack(unittest.TestCase):
    def test_len_head_tail(self):
        for f in (pack_move_x_cm(10), pack_show_qr(156, 123),
                  pack_ttl(0x10, 1, 40, 250), PACK_PLACE):
            self.assertEqual(len(f), 10)
            self.assertEqual(f[0], 0xFF)
            self.assertEqual(f[-1], 0xFE)

    def test_move_x_cm_positive_negative(self):
        self.assertEqual(H(pack_move_x_cm(10)), "FF 01 0A 00 00 00 00 00 00 FE")
        self.assertEqual(H(pack_move_x_cm(-10)), "FF 01 F6 00 00 00 00 00 00 FE")
        self.assertEqual(H(pack_move_x_cm(127)), "FF 01 7F 00 00 00 00 00 00 FE")

    def test_int8_boundaries(self):
        pack_move_x_cm(-128)                      # 合法边界
        for bad in (128, -129, "abc"):
            with self.assertRaises(PackError):
                pack_move_x_cm(bad)
        with self.assertRaises(PackError):        # 旧代码 128 钳位 bug 不复现
            pack_move_x_cm(128)

    def test_y_and_rotate_field_positions(self):
        # 0x02 距离在 buf[2]; 0x03 角度在 buf[3]
        self.assertEqual(H(pack_move_y_cm(-5)), "FF 02 00 FB 00 00 00 00 00 FE")
        self.assertEqual(H(pack_rotate_deg(-90)), "FF 03 00 00 A6 00 00 00 00 FE")
        self.assertEqual(H(pack_move_x_mm(12)), "FF 05 0C 00 00 00 00 00 00 FE")

    def test_show_qr_big_endian(self):
        # c1=156(0x009C) c2=123(0x007B), 大端放 buf[4..7]
        self.assertEqual(H(pack_show_qr(156, 123)),
                         "FF 04 00 00 00 00 9C 00 7B FE")

    def test_lift_move_be16(self):
        # 0x0C 升降 -1234 步 → 补码 0xFB2E 高字节在前(旧小端 bug 已废弃)
        self.assertEqual(H(pack_lift_move(-1234)), "FF 0C FB 2E 00 00 00 00 00 FE")
        self.assertEqual(H(pack_lift_goto(2)), "FF 0B 02 00 00 00 00 00 00 FE")

    def test_ttl_layout(self):
        # 0x12 base: dir0, vel200(0xC8), clk3200(0x00000C80) → 与 deploy 回归一致
        self.assertEqual(H(pack_ttl(0x12, 0, 200, 3200)),
                         "FF 12 00 00 C8 00 00 0C 80 FE")
        self.assertEqual(H(pack_ttl(0x10, 1, 40, 250)),
                         "FF 10 01 00 28 00 00 00 FA FE")
        with self.assertRaises(PackError):
            pack_ttl(0x13, 0, 1, 1)               # 非 TTL 电机命令

    def test_grip_and_action_groups(self):
        self.assertEqual(H(pack_grip(True)), "FF 14 00 00 00 00 00 00 00 FE")
        self.assertEqual(H(pack_grip(False)), "FF 14 01 00 00 00 00 00 00 FE")
        self.assertEqual(H(pack_grip_angle(0)), "FF 15 00 00 00 00 00 00 00 FE")
        self.assertEqual(H(pack_grip_angle(180)), "FF 15 B4 00 00 00 00 00 00 FE")
        self.assertEqual(H(PACK_PLACE), "FF 13 00 00 00 00 00 00 00 FE")
        self.assertEqual(H(PACK_GRAB_FROM_VEHICLE), "FF 16 00 00 00 00 00 00 00 FE")
        with self.assertRaises(PackError):
            pack_grip_angle(256)
        self.assertEqual(H(pack_grab_tray()), "FF 07 00 00 00 00 00 00 00 FE")
        self.assertEqual(H(pack_put_layer1()), "FF 09 00 00 00 00 00 00 00 FE")

    def test_grab_vehicle_slot(self):
        # 0x16 物架位 1/2/3(固件按位走不同动作组); 0 归一为 1
        self.assertEqual(H(pack_grab_vehicle(1)), "FF 16 01 00 00 00 00 00 00 FE")
        self.assertEqual(H(pack_grab_vehicle(2)), "FF 16 02 00 00 00 00 00 00 FE")
        self.assertEqual(H(pack_grab_vehicle(3)), "FF 16 03 00 00 00 00 00 00 FE")
        self.assertEqual(H(pack_grab_vehicle(0)), "FF 16 01 00 00 00 00 00 00 FE")
        for bad in (4, -1, 99):
            with self.assertRaises(PackError):
                pack_grab_vehicle(bad)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestPack)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    ok = result.wasSuccessful()
    print("ALL TESTS PASSED" if ok else "TESTS FAILED")
    sys.exit(0 if ok else 1)
