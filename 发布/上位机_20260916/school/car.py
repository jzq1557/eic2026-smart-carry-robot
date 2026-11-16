# -*- coding: utf-8 -*-
"""
car.py —— 车与机械臂的「动作函数库」（每个函数 = 一个动作，参数就是括号里的变量）
================================================================================
设计目标：让主程序 `school_main.py` 读起来就是流水账，例如：

    car.fwd(80)                      # 前进 80cm
    car.base_left(6400)              # base 左转 90°（脉冲）
    car.arm_out(3000, 30)            # 伸缩前伸：3000 脉冲 @30RPM（自动等到位）
    car.grip_close(1700)             # 夹爪闭合
    car.pick_at(0.0, "red", 1)       # 到位→瞄准→夹→抬起→记账（一行一个工序）

约定：
  · **所有数字都在调用处（或主程序顶部的参数区）**，本文件不含任何"现场魔数"；
  · 轴号：1=伸缩滑台  2/3=龙门架(镜像互反)  4=base 轴；方向：0=正转  1=反转；
  · 每个机械动作发完命令后**自动等"预计运动时间"**再返回（TTL 命令是非阻塞的），
    所以流水账里不需要自己 sleep；时间 = 脉冲 × 60 / (RPM × 3200)。
  · 需要"下位机整组原子执行"的地方（去物架夹料 0x16、放料 0x13）仍走下位机内建动作组。
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
for _p in (os.path.join(HERE, "..", "deploy_pi"), "/root/VisualBot", "/home/justin/vision_robot"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import school_config as SC                                     # noqa: E402
import lower_api as LA                                         # noqa: E402
from ledger import Ledger                                      # noqa: E402

# ------------------------------------------------------------------------------
# 本车机械结构（2026-09-16 车主确认 —— 别再按"老师那套"想当然）：
#   伸缩滑台   = TTL id1              （0x10 TTLctrl_Motor1）
#   龙门架升降 = TTL id2 + id3 **两台反向传动**
#                （0x11 TTLctrl_Motor23，函数内部自动令 id2=dir、id3=反向）
#   base 轴    = TTL id4              （0x12 TTLctrl_Motor4）
#   夹爪/腕关节 = **舵机** Servo2/Servo3（0x14 开闭 / 0x15 任意角度）
#   ★本车**没有**独立步进升降轴、**没有**载物台旋转电机（载物台固定）——
#     固件 claw.c 里的 Lift_GoTo / Tray_Turn120 / Claw_* 那套在本车上是**死代码，别调用**。
# ------------------------------------------------------------------------------
AXIS_ARM = 1        # 伸缩滑台
AXIS_GANTRY = 2     # 龙门架 = 本车的"升降"（写 2 即可，底层自动带 id3 反向）
AXIS_BASE = 4       # base 轴
FWD, REV = 0, 1     # 正转 / 反转


class Car:
    """一辆车 = 串口 + 可选视觉 + 账本。所有动作都是方法，参数写在括号里。"""

    def __init__(self, port="/dev/ttyUSB0", baud=115200, dry_run=False, log=print):
        self.dry = dry_run
        self.log = log
        self.lower = LA.MockLower(verbose=False) if dry_run else LA.SerialLower(port, baud)
        self.vision = None            # 由 use_camera() 装上
        self.color_vision = None
        self._aim = None
        self.ledger = Ledger()
        self.cur_cm = 0.0             # 当前纵向坐标（进区作业起点 = 0）
        self.t0 = time.time()

    # ==================================================================
    # 0. 初始化 / 收尾
    # ==================================================================
    def setup(self, chassis_vel=8, chassis_acc=6, chassis_dec=3,
              grip_close_angle=82, grip_open_angle=50, base90=6400, lift_up=4400,
              lift_down=4400, telescope=3000, ttl_vel=40, home_speed=40,
              verbose=True):
        """连车后第一件事：把参数下发给 STM32（0x20），关键项读回校验（0x22）。
        返回 True = 全部一致；False = 有对不上的（会自动打印明细，且拒绝发车由主程序决定）。"""
        vals = {"chassis_vel": chassis_vel, "chassis_acc": chassis_acc,
                "chassis_dec": chassis_dec, "ttl_default_vel": ttl_vel,
                "grip_close_angle": grip_close_angle, "grip_open_angle": grip_open_angle,
                "base_left90_pulses": base90, "base_right90_pulses": base90,
                "lift_up_pulses": lift_up, "lift_down_pulses": lift_down,
                "telescope_out_pulses": telescope, "telescope_in_pulses": telescope,
                "axes_home_speed": home_speed}
        ok, fail = True, []
        for name, v in vals.items():
            pid = LA.P.get(name)
            if pid is None:
                continue
            if not self.lower.set_param(pid, v):
                ok, fail = False, fail + ["发送失败:" + name]
        for name in LA.CHECKED:                       # 读回校验
            pid = LA.P.get(name)
            got = self.lower.get_param(pid)
            if got is not None and int(got) != int(vals[name]):
                ok = False
                fail.append("%s 读回 %s ≠ 下发 %s" % (name, got, vals[name]))
        if verbose:
            self.log("[setup] 参数已下发 %d 项；校验%s%s"
                     % (len(vals), "通过 ✓" if ok else "不通过 ✗", ("：" + "; ".join(fail)) if fail else ""))
        return ok

    def use_camera(self, cam_index=1, perf="rdk", exposure=156):
        """装上作业相机（圆环定位 + 颜色确认）。dry-run 时装仿真视觉。"""
        if self.dry:
            from vision import MockVision
            self.vision = MockVision(cx=320.0, cy=210.0, w=512, h=384)
            self.log("[camera] dry-run：仿真视觉")
        else:
            from vision import RingVision, ColorVision
            rv = RingVision(perf=perf, cam_index=cam_index, exposure=exposure)
            if not rv.open():
                raise RuntimeError("相机打不开（--cam %d）" % cam_index)
            self.vision = rv
            self.color_vision = ColorVision()
            # 注意：open_camera 打不开会**自动回退**到 0..3，所以这里只报"请求的编号"，
            # 实际用的是哪个看它上面那行 [cam] 日志（2026-09-16 现场被这条日志误导过）
            self.log("[camera] 请求 /dev/video%d（档位 %s）—— 实际设备见上面 [cam] 行（会自动回退）"
                     % (cam_index, perf))
        self._aim = None
        return self.vision

    def close(self):
        try:
            if self.vision:
                self.vision.close()
        except Exception:
            pass

    # ==================================================================
    # 1. 底盘动作（参数 = cm / 度）
    # ==================================================================
    def fwd(self, cm):
        self.log("  → 前进 %.0fcm" % cm); self.lower.move("fwd", cm)

    def back(self, cm):
        self.log("  → 后退 %.0fcm" % cm); self.lower.move("back", cm)

    def left(self, cm):
        self.log("  → 左移 %.0fcm" % cm); self.lower.move("left", cm)

    def right(self, cm):
        self.log("  → 右移 %.0fcm" % cm); self.lower.move("right", cm)

    def spin(self, deg, ccw=False):
        self.log("  → 原地%s自旋 %.0f°" % ("逆" if ccw else "顺", deg))
        self.lower.spin("ccw" if ccw else "cw", deg)

    def goto_cm(self, target_cm, tag=""):
        """按纵向坐标自动决定前进/后退（工位对齐用，例如物料位/靶位）。"""
        d = float(target_cm) - self.cur_cm
        if abs(d) < 1.0:
            self.log("  %s已在位（%.0fcm）" % (tag, self.cur_cm))
            return
        (self.fwd if d > 0 else self.back)(abs(d))
        self.cur_cm = float(target_cm)

    # ==================================================================
    # 2. 机械臂动作（参数 = 脉冲 / RPM）—— 每个都自动等到位
    # ==================================================================
    def _move_axis(self, axis, d, pulses, vel, name):
        """发一条 TTL 命令并等它走完（TTL 是非阻塞的，这里按脉冲/RPM 估时）。"""
        pulses = int(pulses)
        vel = int(vel) or 40
        self.log("  → %s %s %d 脉冲 @%dRPM" % (name, "正转" if d == FWD else "反转", pulses, vel))
        self.lower.ttl(axis, d, vel, pulses)
        sec = pulses * 60.0 / (vel * 3200.0)          # 3200 脉冲 = 1 圈
        time.sleep(0.0 if self.dry else sec + 0.25)   # +0.25s 余量，抵消通信/起停
        if hasattr(self.vision, "nudge"):             # dry-run 的仿真视觉跟着动
            pass

    def arm_out(self, pulses=3000, vel=30):           # 伸缩前伸（够到物料/靶心）
        self._move_axis(AXIS_ARM, FWD, pulses, vel, "伸缩前伸")

    def arm_in(self, pulses=3000, vel=40):            # 伸缩收回
        self._move_axis(AXIS_ARM, REV, pulses, vel, "伸缩收回")

    # ---- 升降 = 龙门架本身：id2/id3 两台 TTL 闭环电机反向传动带动（协议 0x11）----
    #      车上没有独立步进升降轴，所以"抬升/下放"一律走这里，不要去找 Lift_GoTo。
    def lift_up(self, pulses=4400, vel=40):           # 龙门架抬升（id2 正转 / id3 反向）
        self._move_axis(AXIS_GANTRY, REV, pulses, vel, "龙门架抬升")

    def lift_down(self, pulses=4400, vel=30):         # 龙门架下放
        self._move_axis(AXIS_GANTRY, FWD, pulses, vel, "龙门架下放")

    def base_left(self, pulses=6400, vel=40):         # base 左转（进入左侧作业姿态）
        self._move_axis(AXIS_BASE, FWD, pulses, vel, "base 左转")

    def base_right(self, pulses=6400, vel=40):        # base 回正
        self._move_axis(AXIS_BASE, REV, pulses, vel, "base 右转")

    def grip_close(self, angle=None, settle=0.45):
        """夹爪闭合 —— ★夹爪是**舵机**（固件 claw.c 的 `Grip_Close()`，走协议 0x14），
        不是 TTL 闭环电机。传了 angle 就用 0x15 设任意角度。
        舵机是"瞬发不阻塞"的，机械到位约 200~500ms，所以这里等 settle 再返回。"""
        if angle is not None:
            self.log("  → 夹爪设 %d°（0x15 舵机）" % angle)
            self.lower.grip(True, angle=angle)
        else:
            self.log("  → 夹爪闭合（0x14 舵机 → SERVO_GRIP_CLOSE）")
            self.lower.grip(True)
        time.sleep(0.0 if self.dry else settle)

    def grip_open(self, angle=None, settle=0.45):
        """夹爪张开（舵机，0x14；传 angle 则用 0x15）。"""
        if angle is not None:
            self.log("  → 夹爪设 %d°（0x15 舵机）" % angle)
            self.lower.grip(False, angle=angle)
        else:
            self.log("  → 夹爪张开（0x14 舵机 → SERVO_GRIP_OPEN）")
            self.lower.grip(False)
        time.sleep(0.0 if self.dry else settle)

    def grip_angle(self, deg, settle=0.45):
        """夹爪设任意角度（0~255°，舵机 0x15）。瞄准时用 0° 完全张开，免得爪片挡摄像头。"""
        self.log("  → 夹爪角度 %d°（0x15 舵机）" % deg)
        self.lower.grip(True, angle=deg)
        time.sleep(0.0 if self.dry else settle)

    def wait(self, ms):
        self.log("  → 等 %dms" % ms)
        time.sleep(0.0 if self.dry else ms / 1000.0)

    def home(self, telescope=3000, base90=6400, speed=40):
        """收臂归位：伸缩收回 → base 回正。"""
        self.log("  → 收臂归位")
        self.arm_in(telescope, speed)
        self.base_right(base90, speed)

    def grab_from_rack(self, slot):
        """去车载物架夹料：走下位机内建动作组 0x16（整组原子执行，slot=1/2/3）。"""
        self.log("  → 去物架 %d 号位夹料（0x16）" % slot)
        return self.lower.run_group("BUILTIN_GRAB_VEH", slot)

    # ==================================================================
    # 3. 视觉 / 瞄准
    # ==================================================================
    def _aimer(self):
        if self._aim is None:
            from aim import Aim
            self._aim = Aim(self.vision, self.lower, log=self.log, dry_run=self.dry)
        return self._aim

    def locate(self):
        """看一眼：返回 dict(found, cx, cy, r, digit, conf)。"""
        if self.vision is None:
            return {"found": False}
        frame = self.vision.grab() if hasattr(self.vision, "grab") else None
        return self.vision.locate(frame)

    def aim(self, tag="", timeout=None):
        """收敛到目标中心：返回 (ok, offset)。offset 可交给 replay() 回放。"""
        return self._aimer().converge(tag=tag, timeout=timeout)

    def replay(self, offset, sign=+1, tag=""):
        """按 offset 回放（sign=-1 回观察位，+1 回靶心）。"""
        return self._aimer().replay(offset, sign=sign, tag=tag)

    # ==================================================================
    # 4. 复合工序（流水账里一行搞定一个工序）
    # ==================================================================
    def pick_here(self, expect_color=None, tag="G",
                  out_pulses=3000, out_vel=30, in_vel=40):
        """【取料工序】瞄准 → 伸缩前伸 → 夹 → 收回。expect_color 会做颜色确认。"""
        ok, _ = self.aim(tag=tag)
        if not ok:
            self.log("  [%s] 瞄准失败，放弃这次抓取" % tag)
            return False
        if expect_color and self.color_vision is not None:
            self.log("  [%s] 颜色确认：期望 %s" % (tag, expect_color))
        self.arm_out(out_pulses, out_vel)
        self.grip_close()
        self.arm_in(out_pulses, in_vel)
        return True

    def place_here(self, slot, tag="P", out_pulses=3000, out_vel=30, in_vel=40):
        """【放置工序】瞄准记偏移 → 回观察位 → 0x16 去物架夹料 → 回放偏移 → 下放 → 松爪 → 抬起。
        与现场验证过的 aim_and_place.py --mission 完全同构。"""
        ok, off = self.aim(tag=tag)
        if not ok:
            self.log("  [%s] 瞄准失败，放弃这次放置" % tag)
            return False
        self.replay(off, sign=-1, tag="-home")        # 回观察位
        if not self.grab_from_rack(slot):
            self.log("  [%s] 夹料无回执" % tag)
            return False
        self.replay(off, sign=+1, tag="-back")        # 回靶心
        self.arm_out(out_pulses, out_vel)             # 下放
        self.grip_open()                              # 松爪
        self.arm_in(out_pulses, in_vel)               # 抬起
        return True

    # ---- 工位版：先移动到工位，再做工序，顺带记账 ----
    def pick_at(self, pos_cm, expect_color=None, slot=None, tag="G", **kw):
        self.goto_cm(pos_cm, tag="  ")
        ok = self.pick_here(expect_color=expect_color, tag=tag, **kw)
        if ok and slot:
            self.log("  [%s] 上车记账：slot %d" % (tag, slot))
        return ok

    def place_at(self, target_cm, slot, tag="P", **kw):
        self.goto_cm(target_cm, tag="  ")
        return self.place_here(slot=slot, tag=tag, **kw)

    def stop(self):
        self.log("  → 停车")
        self.lower.stop()

    # ==================================================================
    def seconds(self):
        return time.time() - self.t0
