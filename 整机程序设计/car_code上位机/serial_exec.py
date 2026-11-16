#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serial_exec.py —— 串口执行适配器(全流程 Executor 的物理层, 纯 Python)
====================================================================
- 帧协议: 0xFF + 命令 + 7 字节参数 + 0xFE(10 字节, 见 cmd_pack.py)
- 回执  : 0xFF 0x01 0xFE(滑动窗解析; 阻塞动作组 0x13/0x16 整组完成才回执,
          须用长 wait_s, 见 deploy_pi/aim_and_place ACK_WAIT_ACT=25)
- pyserial **延迟导入**: 仅在带 port 打开时 import; 无 pyserial 也能 import 本模块
  并在测试中用假串口。
用法:
  link = SerialLink(port="/dev/ttyUSB0")   # 或 SerialLink(ser=fake) 测试注入
  ok = link.send(pack_move_x_cm(10), wait_s=3.0, retries=1)
回归: python test_serial_exec.py
"""
from __future__ import annotations

from typing import Optional

__all__ = ["SerialLink", "LinkError", "ACK_DONE"]


ACK_DONE = bytes([0xFF, 0x01, 0xFE])
FRAME_LEN = 10


class LinkError(Exception):
    """串口链路错误(打开失败/帧非法/缺 pyserial)。"""


class SerialLink:
    def __init__(self, port: Optional[str] = None, baud: int = 115200,
                 timeout: float = 0.5, ser=None):
        """
        port: 设备路径; ser: 预置串口对象(测试注入, 与 port 二选一)。
        打开前不 import pyserial。
        """
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._ser = ser
        self._owned = ser is None and port is not None

    # ---------- 打开/关闭 ----------
    def open(self):
        if self._ser is not None:
            return self._ser
        if not self.port:
            raise LinkError("SerialLink 需要 port(如 /dev/ttyUSB0)或注入 ser")
        try:
            import serial  # 延迟导入
        except ImportError:
            raise LinkError("未安装 pyserial(真机需 `pip3 install pyserial`; "
                            "纯逻辑测试请注入假 ser)")
        self._ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
        import time
        time.sleep(0.2)
        self._ser.reset_input_buffer()
        return self._ser

    def close(self):
        if self._ser is not None and self._owned:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---------- 收发 ----------
    def send(self, frame: bytes, wait_s: float = 3.0,
             retries: int = 0, ack: bytes = ACK_DONE) -> bool:
        """发一帧并等回执。wait_s: 非阻塞命令 3s; 0x13/0x16 动作组 25s。
        retries: 回执超时后的重发次数(整帧重发, 幂等命令才安全)。"""
        if len(frame) != FRAME_LEN or frame[0] != 0xFF or frame[-1] != 0xFE:
            raise LinkError("非法帧(应 FF+8B+FE, 10B): %s" % frame.hex(" "))
        ser = self.open()
        for attempt in range(retries + 1):
            ser.reset_input_buffer()
            ser.write(frame)
            if self._wait_ack(ser, wait_s, ack):
                return True
        return False

    @staticmethod
    def _wait_ack(ser, wait_s: float, ack: bytes) -> bool:
        import time
        t0 = time.time()
        buf = bytearray()
        while time.time() - t0 < wait_s:
            b = ser.read(1)
            if not b:
                continue
            buf.append(b[0])
            if len(buf) >= len(ack) and bytes(buf[-len(ack):]) == ack:
                return True
            if len(buf) > 32:
                buf = buf[-len(ack):]
        return False
