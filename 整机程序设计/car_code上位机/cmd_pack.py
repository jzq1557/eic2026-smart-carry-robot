#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cmd_pack.py —— 下位机串口帧打包(按固件 car_config.h 规则, 纯 Python)
====================================================================
规则(与 STM32F103ZET6 固件 26090501TEST CLEAN/car_config.h 对齐):
  帧      : 0xFF + 命令 + 7 字节参数 + 0xFE   (共 10 字节, 无校验)
  单字节  : int8 补码(旧代码曾把上限写成 128 → 越界崩溃, 这里显式限 -128..127)
  16 位   : 大端高字节在前(0x0C 升降距离等, 旧版小端 bug 已废弃)
  32 位   : 大端高字节在前(0x10~0x12 clk 脉冲数)
命令字(见 CarCommand 枚举):
  0x01 X_cm(buf[1]) 0x02 Y_cm(buf[2]) 0x03 旋转(buf[3]) 0x04 显示QR(buf[4..7] 两个be16)
  0x05 X_mm(buf[1]) 0x06 Y_mm(buf[2]) 0x07 转盘抓 0x08 地面抓 0x09 放1层 0x0A 放2层
  0x0B 升降预设位(buf[1]) 0x0C 升降相对距离(buf[1..2] be16) 0x0D 复合(buf[1]子模式 buf[2]距离)
  0x10~0x12 TTL电机(dir, vel16大端, clk32大端)  0x13 放料组  0x14 夹爪0开/1闭
  0x15 夹爪角度  0x16 去物架夹料组
用法:
  frame = pack_move_x_cm(-10)      # -> FF 01 F6 00 00 00 00 00 00 FE
  frame = pack_ttl(0x12, 0, 200, 3200)
回归: python test_cmd_pack.py
"""
from __future__ import annotations

__all__ = [
    "CMD_", "pack_cmd", "pack_move_x_cm", "pack_move_y_cm", "pack_rotate_deg",
    "pack_move_x_mm", "pack_move_y_mm", "pack_show_qr", "pack_lift_goto",
    "pack_lift_move", "pack_combo", "pack_grip", "pack_grip_angle",
    "pack_ttl", "pack_grab_vehicle", "PACK_PLACE", "PACK_GRAB_FROM_VEHICLE",
    "PACK_GRAB_TRAY", "PACK_PUT_LAYER1",
]

# ---- 命令字(与 car_config.h CarCommand 一致) ----
CMD_ = {
    "MOVE_X_CM": 0x01, "MOVE_Y_CM": 0x02, "ROTATE_DEG": 0x03,
    "SHOW_QR": 0x04, "MOVE_X_MM": 0x05, "MOVE_Y_MM": 0x06,
    "GRAB_TRAY": 0x07, "GRAB_GROUND": 0x08, "PUT_LAYER1": 0x09,
    "PUT_LAYER2": 0x0A, "LIFT_GOTO": 0x0B, "LIFT_MOVE": 0x0C, "COMBO": 0x0D,
    "TTL_MOTOR1": 0x10, "TTL_MOTOR23": 0x11, "TTL_MOTOR4": 0x12,
    "TTL_PLACE": 0x13, "TTL_GRIP": 0x14, "TTL_GRIP_ANG": 0x15,
    "TTL_GRAB_VEH": 0x16,
}
HEAD, TAIL = 0xFF, 0xFE


class PackError(ValueError):
    """打包参数越界/非法。"""


def _i8(v: int) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise PackError("int8 参数非数字: %r" % (v,))
    if not (-128 <= v <= 127):
        raise PackError("int8 越界: %d(允许 -128..127, 旧代码 128 上限是 bug)" % v)
    return v & 0xFF


def _be16(v: int) -> tuple:
    """16 位大端两字节(支持负数补码)。"""
    v = int(v)
    if not (-32768 <= v <= 65535):
        raise PackError("16位越界: %d" % v)
    v &= 0xFFFF
    return (v >> 8) & 0xFF, v & 0xFF


def _be32(v: int) -> tuple:
    v = int(v)
    if not (0 <= v <= 0xFFFFFFFF):
        raise PackError("32位无符号越界: %d" % v)
    return ((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def pack_cmd(mode: int, b1=0, b2=0, b3=0, b4=0, b5=0, b6=0, b7=0) -> bytes:
    """任意命令帧: 0xFF + 命令 + 7 字节参数 + 0xFE。"""
    if not (0x01 <= mode <= 0x16):
        raise PackError("命令字越界: 0x%02X" % mode)
    payload = (int(b1) & 0xFF, int(b2) & 0xFF, int(b3) & 0xFF,
               int(b4) & 0xFF, int(b5) & 0xFF, int(b6) & 0xFF, int(b7) & 0xFF)
    return bytes([HEAD, mode]) + bytes(payload) + bytes([TAIL])


# ---------------- 底盘/显示(旧协议) ----------------
def pack_move_x_cm(cm: int) -> bytes:   return pack_cmd(CMD_["MOVE_X_CM"], _i8(cm))
def pack_move_y_cm(cm: int) -> bytes:   return pack_cmd(CMD_["MOVE_Y_CM"], 0, _i8(cm))
def pack_rotate_deg(deg: int) -> bytes: return pack_cmd(CMD_["ROTATE_DEG"], 0, 0, _i8(deg))
def pack_move_x_mm(mm: int) -> bytes:   return pack_cmd(CMD_["MOVE_X_MM"], _i8(mm))
def pack_move_y_mm(mm: int) -> bytes:   return pack_cmd(CMD_["MOVE_Y_MM"], 0, _i8(mm))
def pack_show_qr(c1: int, c2: int) -> bytes:
    """0x04: 两个 16 位大端放 buf[4..7](显示任务码两组的旧接口)。"""
    a, b = _be16(c1)
    c, d = _be16(c2)
    return pack_cmd(CMD_["SHOW_QR"], 0, 0, 0, a, b, c, d)
def pack_lift_goto(preset: int) -> bytes:
    return pack_cmd(CMD_["LIFT_GOTO"], int(preset) & 0xFF)
def pack_lift_move(steps: int) -> bytes:
    """0x0C 升降相对距离(步), ★16位大端(旧版小端 bug 已废弃)。"""
    a, b = _be16(steps)
    return pack_cmd(CMD_["LIFT_MOVE"], a, b)
def pack_combo(sub_mode: int, dist_cm: int) -> bytes:
    return pack_cmd(CMD_["COMBO"], int(sub_mode) & 0xFF, _i8(dist_cm))
def pack_grab_tray() -> bytes:      return pack_cmd(CMD_["GRAB_TRAY"])
def pack_put_layer1() -> bytes:     return pack_cmd(CMD_["PUT_LAYER1"])

# ---------------- TTL 闭环电机(新协议) ----------------
def pack_ttl(mode: int, dir_: int, vel: int, clk: int) -> bytes:
    """0x10/0x11/0x12: buf[1]=dir, buf[2..3]=vel 16大端, buf[4..7]=clk 32大端。"""
    if mode not in (CMD_["TTL_MOTOR1"], CMD_["TTL_MOTOR23"], CMD_["TTL_MOTOR4"]):
        raise PackError("非 TTL 电机命令: 0x%02X" % mode)
    d = 0 if dir_ == 0 else 1
    vh, vl = _be16(vel)
    k0, k1, k2, k3 = _be32(clk)
    return pack_cmd(mode, d, vh, vl, k0, k1, k2, k3)


# ---------------- 夹爪/动作组 ----------------
def pack_grip(open_: bool = True) -> bytes:
    return pack_cmd(CMD_["TTL_GRIP"], 0 if open_ else 1)

def pack_grip_angle(deg: int) -> bytes:
    if not (0 <= int(deg) <= 255):
        raise PackError("夹爪角度越界: %d" % deg)
    return pack_cmd(CMD_["TTL_GRIP_ANG"], int(deg))

PACK_PLACE = pack_cmd(CMD_["TTL_PLACE"])             # 0x13 放料动作组
PACK_GRAB_FROM_VEHICLE = pack_cmd(CMD_["TTL_GRAB_VEH"])  # 0x16 夹料动作组(slot=0→固件按1号位)


def pack_grab_vehicle(slot: int = 1) -> bytes:
    """0x16 去车载物架夹料: buf[1]=物架位 1/2/3(固件 1=base正转1700 / 2=base不动 /
    3=base反转1700); 0 会被固件当 1 处理, 这里统一按 1 打包以免歧义。"""
    s = int(slot)
    if s == 0:
        s = 1
    if s not in (1, 2, 3):
        raise PackError("物架位只能是 1/2/3(收到 %r)" % slot)
    return pack_cmd(CMD_["TTL_GRAB_VEH"], s)
