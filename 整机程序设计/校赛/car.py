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
        # ★取料/放置动作组（由主程序参数区的 PICK_STEPS / PLACE_STEPS 注入；None = 用 DEFAULT_PICK_STEPS）
        #   这两个列表就是现场要反复调的"动作组"，配合 pick_debug.py 单步调试。
        self.pick_steps = None
        self.place_steps = None

    # ==================================================================
    # 0. 初始化 / 收尾
    # ==================================================================
    def setup(self, chassis_vel=8, chassis_acc=6, chassis_dec=3,
              grip_close_angle=82, grip_open_angle=10, base90=6400, lift_up=4400,
              lift_down=4400, telescope=3000, ttl_vel=80, home_speed=40,
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

    def use_camera(self, cam_index=0, perf="rdk", exposure=None, rotate="auto",
                   hub=None, width=0):
        """装上作业相机（圆环定位 + 颜色确认）。dry-run 时装仿真视觉。

        · `exposure=None`（默认）= **不动曝光**（相机自动）。RDK 上那台给 156 会过曝 ——
          2026-09-16 现场就是锁着 157 跑的，画面死白、环检测忽有忽无。要锁就显式传数值。
        · `rotate` = 画面方向： "auto"（默认，跟随 ring_calib.json）/ none / ccw / cw / 180。
        · `hub` = FrameHub，主程序读到的帧顺手喂给内嵌推流（监控台不再抢相机）。
        · `width` = 处理宽度（0 = 用性能档）。
        """
        if self.dry:
            from vision import MockVision
            self.vision = MockVision(cx=320.0, cy=210.0, w=512, h=384)
            self.vision.hub = hub
            self.log("[camera] dry-run：仿真视觉（方向 %s）" % rotate)
        else:
            from vision import RingVision, ColorVision
            cfg = {"proc_w": int(width)} if width else None
            rv = RingVision(cfg=cfg, perf=perf, cam_index=cam_index,
                            exposure=exposure, rotate=rotate, hub=hub)
            if not rv.open():
                raise RuntimeError("相机打不开（--cam %d）" % cam_index)
            self.vision = rv
            self.color_vision = ColorVision()
            # 注意：open_camera 打不开会**自动回退**到 0..3，所以这里只报"请求的编号"、
            # 实际用的那个看它上面那行 [cam] 日志（2026-09-16 现场被这条日志误导过）
            self.log("[camera] 请求 /dev/video%d（档位 %s）—— 实际设备见上面 [cam] 行（会自动回退）"
                     % (cam_index, perf))
            self.log("[camera] 画面方向 = %s%s；曝光 = %s"
                     % (rv.rotate_mode,
                        ("（来自 %s）" % os.path.basename(rv.calib_path)) if rv.calib_path else "（无档案，用参数/CALIB 默认）",
                        "不改（自动）" if exposure is None else ("锁 %.0f" % exposure)))
            if self.color_vision.profile_path:
                self.log("[camera] 颜色阈值来自 %s"
                         % os.path.basename(self.color_vision.profile_path))
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
    # ★2026-09-18 晚 加"耗时自检"（车主："经常在向右平移出启动区后进行长时间停止延时"）。
    #   背景：底盘命令是**同步**的 —— 固件 `Chassis_MoveX_cm()` 末尾 `Ramp_WaitDone()`
    #   要**等到位**才回 ACK，所以上位机 `lower.move()` 里的 `send(timeout=10.0)`
    #   正常只消耗实际运动时间（11cm 约 0.5~1.5s）。
    #   一旦这里打印出 >1.2s，就说明卡在"等 ACK"上，两种可能一眼可分：
    #     · 上一行有 `!! ACK 超时 cmd=0x01（10.0s）` → ACK 丢了/固件没回，白等满 10s；
    #     · 没有超时行但很慢 → 固件的到位判据（斜坡减速尾巴）拖得久。
    def _chassis_timed(self, label, fn, *args):
        t0 = time.time()
        fn(*args)
        dt = time.time() - t0
        if dt > 1.2:
            self.log("  ⏱ 【%s】花了 %.1fs —— 底盘命令偏慢！"
                     "（正常 11cm ≈1s；先看上一行有没有 `!! ACK 超时`）" % (label, dt))
        return dt

    def fwd(self, cm):
        self.log("  → 前进 %.0fcm" % cm)
        self._chassis_timed("前进%.0fcm" % cm, self.lower.move, "fwd", cm)

    def back(self, cm):
        self.log("  → 后退 %.0fcm" % cm)
        self._chassis_timed("后退%.0fcm" % cm, self.lower.move, "back", cm)

    def left(self, cm):
        self.log("  → 左移 %.0fcm" % cm)
        self._chassis_timed("左移%.0fcm" % cm, self.lower.move, "left", cm)

    def right(self, cm):
        self.log("  → 右移 %.0fcm" % cm)
        self._chassis_timed("右移%.0fcm" % cm, self.lower.move, "right", cm)

    # ==================================================================
    # 毫米级平移（★2026-09-29 新增）—— 给"视觉对中"这类微调用
    # ==================================================================
    # 为什么要有它：厘米版一路走到下位机是 `abs(int(cm))`，**0.5cm 会被截断成 0 → 车不动**，
    #   于是对中永远只能精确到 ±1cm；实车表现为"偏差 6 轮不变、一动不动"。
    # mm 版走协议 0x05/0x06（固件 chassis.c 的 Chassis_MoveX_mm，STEPS_PER_MM=10），
    #   能精确到 1mm。
    # ⚠️ 单次上限 ±127mm（协议字段是 int8），别拿它跑长距离 —— 长距离仍用上面的 cm 版。
    def fwd_mm(self, mm):
        self.log("  → 前进 %dmm" % mm)
        self._chassis_timed("前进%dmm" % mm, self.lower.move_mm, "fwd", mm)

    def back_mm(self, mm):
        self.log("  → 后退 %dmm" % mm)
        self._chassis_timed("后退%dmm" % mm, self.lower.move_mm, "back", mm)

    def left_mm(self, mm):
        self.log("  → 左移 %dmm" % mm)
        self._chassis_timed("左移%dmm" % mm, self.lower.move_mm, "left", mm)

    def right_mm(self, mm):
        self.log("  → 右移 %dmm" % mm)
        self._chassis_timed("右移%dmm" % mm, self.lower.move_mm, "right", mm)

    def spin(self, deg, ccw=False):
        self.log("  → 原地%s自旋 %.1f°" % ("逆" if ccw else "顺", deg))
        self._chassis_timed("自旋%.1f°" % deg, self.lower.spin,
                            "ccw" if ccw else "cw", deg)

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
    # 3200 脉冲 = 1 圈（16 细分 × 200 整步，见 Emm_V5 手册）
    TTL_PULSES_PER_REV = 3200.0

    def ttl_extra(self, axis):
        """按轴取「额外等待」（秒）—— 见 school_config.TTL_EXTRA_S。

        ★2026-09-18 现场："去载物台夹取时，龙门架还没有充分下放，夹爪舵机就闭合了"。
          匀速段公式没错，但**低转速 + 重负载**的龙门架（id2+id3 两台电机反向传动，
          抬整个龙门 + 载物台）实际加减速比统一斜率估的慢：
              ④ lift_down 300 @30RPM 只等 0.64s、⑤ lift_down 800 @30RPM 只等 0.95s
          若实际各要 ≈0.8s / ≈1.1s，⑥ 关爪时它还差一截 → 夹爪在半空闭合。
          这里按轴补一段固定裕量（加大 = 更保险，代价只是慢一点）。
        """
        tbl = getattr(SC, "TTL_EXTRA_S", None) or {}
        key = {AXIS_ARM: "arm", AXIS_GANTRY: "gantry", AXIS_BASE: "base"}.get(axis, "")
        try:
            return float(tbl.get(key, 0.0))
        except (TypeError, ValueError):
            return 0.0

    def ttl_time(self, pulses, vel, axis=None):
        """估一条 TTL 单轴命令的**真实**耗时（秒）：匀速段 + 加速段 + 减速段 + 该轴裕量。

        ★2026-09-18 车主现场反馈："去载物台夹取物料时，伸缩台还没到位，龙门架和
          舵机动作就开始执行了"。根因**不是**动作组顺序，而是这个估时：

          · 固件对 0x10/0x11/0x12 是**非阻塞**转发（TTLctrl.c「单轴非阻塞控制…
            发完即返回,不等待」）→ 上位机只能按时间估；
          · 老公式只算**匀速段**，而驱动器带加减速（car_config.h:
            TTLCTRL_ACC_DEFAULT = 60，非 0 → 不是"直接启动"）；
          · 速度调得越高，匀速段越短、加减速占比越大，误差被放大：
                6500 脉冲 @40RPM ：匀速 3.05s + 加减速 ~0.27s → 老公式 3.30s ✅ 够
                6500 脉冲 @200RPM：匀速 0.61s + 加减速 ~1.33s → 老公式 0.86s ❌ 少一半
            ⇒ 伸缩还在飞，龙门下放 + 关爪的命令已经发出去了。

        返回 (总时间, 匀速段, 加减速段)（轴裕量已并入总时间）。
        """
        pulses = abs(int(pulses))
        vel = max(1, int(vel))
        t_lin = pulses * 60.0 / (vel * self.TTL_PULSES_PER_REV)
        t_acc = 2.0 * vel / max(1.0, float(getattr(SC, "TTL_ACC_RPM_PER_S", 300.0)))
        t_ext = self.ttl_extra(axis) if axis is not None else 0.0
        return t_lin + t_acc + t_ext, t_lin, t_acc

    def ttl_wait(self, pulses, vel, axis=None):
        """按 ttl_time() 估的时长 sleep（含固定余量），返回实际等待秒数。

        给 RouteCar 之类"自己发 TTL 命令"的地方复用，保证全工程只有一套估时规则。
        """
        total = self.ttl_time(pulses, vel, axis=axis)[0] + \
            float(getattr(SC, "TTL_TIME_EXTRA_S", 0.25))
        time.sleep(0.0 if self.dry else total)
        return total

    def _move_axis(self, axis, d, pulses, vel, name):
        """发一条 TTL 命令并等它走完（TTL 是非阻塞的，这里按脉冲/RPM 估时）。"""
        pulses = int(pulses)
        vel = int(vel) or 40
        self.log("  → %s %s %d 脉冲 @%dRPM" % (name, "正转" if d == FWD else "反转", pulses, vel))
        self.lower.ttl(axis, d, vel, pulses)
        sec, t_lin, t_acc = self.ttl_time(pulses, vel, axis=axis)
        extra = float(getattr(SC, "TTL_TIME_EXTRA_S", 0.25))
        t_ext = self.ttl_extra(axis)
        if not self.dry:
            self.log("     等到位 ≈ %.2fs（匀速 %.2f + 加减速 %.2f + 轴裕量 %.2f + 余量 %.2f）"
                     % (sec + extra, t_lin, t_acc, t_ext, extra))
        time.sleep(0.0 if self.dry else sec + extra)
        if hasattr(self.vision, "nudge"):             # dry-run 的仿真视觉跟着动
            pass

    def arm_out(self, pulses=3000, vel=30):           # 伸缩前伸（够到物料/靶心）
        self._move_axis(AXIS_ARM, FWD, pulses, vel, "伸缩前伸")

    def arm_in(self, pulses=3000, vel=40):            # 伸缩收回
        self._move_axis(AXIS_ARM, REV, pulses, vel, "伸缩收回")

    # ---- 升降 = 龙门架本身：id2/id3 两台 TTL 闭环电机反向传动带动（协议 0x11）----
    #      车上没有独立步进升降轴，所以"抬升/下放"一律走这里，不要去找 Lift_GoTo。
    def lift_up(self, pulses=2400, vel=40):           # 龙门架抬升（id2 正转 / id3 反向）
        # ★2026-09-17: 默认值 4400→2400（起始位抬高 2000 后，物架上方绝对高度不变）
        self._move_axis(AXIS_GANTRY, REV, pulses, vel, "龙门架抬升")

    def lift_down(self, pulses=2400, vel=30):         # 龙门架下放（默认值同 lift_up）
        self._move_axis(AXIS_GANTRY, FWD, pulses, vel, "龙门架下放")

    def base_left(self, pulses=6400, vel=80):         # base 左转（进入左侧作业姿态）★40→80
        self._move_axis(AXIS_BASE, FWD, pulses, vel, "base 左转")

    def base_right(self, pulses=6400, vel=80):        # base 回正 ★40→80
        self._move_axis(AXIS_BASE, REV, pulses, vel, "base 右转")

    # ---- base 的"顺/逆时针"别名（与 route_car.RouteCar 同名同义，两边都能用）----
    #   ★2026-09-18 晚 加 `wait=False`：固件 TTL 命令本来就是**非阻塞**的，
    #     所以"先发 base（不等）→ 再底盘前进（阻塞）"= 两件事并行，
    #     用于"在去扫码区的路上就把 base 转 180°"（见 route_main.s2_scan）。
    def base_turn_dir(self, pulses, cw=False, vel=80, wait=True, tag=""):
        """base 按方向转。`cw=True` 顺时针（dir=REV）/ `False` 逆时针（dir=FWD）。
        `wait=False` → 只发命令、立刻返回**估算耗时（秒）**，供调用方自己补齐差额。"""
        d = REV if cw else FWD
        label = tag or ("base %s转" % ("顺时" if cw else "逆时"))
        if not wait:
            self.log("  → %s %d 脉冲 @%dRPM  ★只发不等（与底盘动作并行）"
                     % (label, int(pulses), int(vel)))
            return self.fire_axis(AXIS_BASE, d, int(pulses), int(vel), name=label)
        self._move_axis(AXIS_BASE, d, int(pulses), int(vel), label)
        return None

    def base_cw(self, pulses=6400, vel=80, wait=True, tag=""):
        """base **顺时针**转（= 右转/扫码朝向）。"""
        return self.base_turn_dir(pulses, cw=True, vel=vel, wait=wait, tag=tag)

    def base_ccw(self, pulses=6400, vel=80, wait=True, tag=""):
        """base **逆时针**转（= 左转/回正）。"""
        return self.base_turn_dir(pulses, cw=False, vel=vel, wait=wait, tag=tag)

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

    def home(self, telescope=0, base90=6400, speed=40):
        """收臂归位：伸缩收回（`telescope=0` 则**不动伸缩台**）→ base 回正。

        ★2026-09-17：默认把它改成 0 —— 本车地面取料/放料不用伸缩台，伸缩台一直停在原位；
          以前每次 home 都再"反转 3000 脉冲"会一路缩到底、顶死机械限位。
        """
        self.log("  → 收臂归位")
        if telescope and int(telescope) > 0:
            self.arm_in(int(telescope), speed)
        else:
            self.log("     （伸缩台不动：telescope=0）")
        self.base_right(base90, speed)

    def grab_from_rack(self, slot):
        """去车载物架夹料：走下位机内建动作组 0x16（整组原子执行，slot=1/2/3）。"""
        self.log("  → 去物架 %d 号位夹料（0x16）" % slot)
        return self.lower.run_group("BUILTIN_GRAB_VEH", slot)

    # ==================================================================
    # 2b. 动作组（步骤列表 → 下发/逐条执行/上电初始化）
    #     步骤写法（与固件 motion.c 一致，两种语义都支持）：
    #       ("axis", 轴号, 方向, 脉冲, 速度RPM)   1=伸缩 / 2=龙门 / 4=base；方向 0=正转 1=反转
    #       ("grip_close",) / ("grip_open",) / ("grip_angle", 角度) / ("wait", 毫秒)
    #       ("break", "说明")                     ← 断点：单步调试时在这里停下（整组下发时忽略）
    #     还支持"说人话"的动作名（调试器/动作组里更好读）：
    #       ("arm_out", 脉冲, RPM) ("arm_in", …) ("lift_up", …) ("lift_down", …)
    #       ("base_left", …) ("base_right", …)
    # ==================================================================
    _STEP_ALIAS = {
        "arm_out":    (AXIS_ARM, FWD, 3000, 30),
        "arm_in":     (AXIS_ARM, REV, 3000, 40),
        "lift_up":    (AXIS_GANTRY, REV, 2400, 40),   # ★2026-09-17: 4400→2400（起始位抬高 2000）
        "lift_down":  (AXIS_GANTRY, FWD, 2400, 30),
        "base_left":  (AXIS_BASE, FWD, 6400, 40),
        "base_right": (AXIS_BASE, REV, 6400, 40),
    }

    # ★取料/放置动作组的**兜底默认值**（主程序 school_main.py 的 PICK_STEPS/PLACE_STEPS 会覆盖它）
    #   ★2026-09-17 车主确认：**地面取料/放料不用伸缩台** —— 瞄准完直接下龙门架、开合夹爪。
    #     （伸缩台只在"去车上物架夹料 0x16"那类场合用，所以这里不再有 arm_out/arm_in）
    DEFAULT_PICK_STEPS = [
        ("grip_open",),                                    # 张爪
        ("lift_down", 2400, 30),                           # 龙门下探到料
        ("grip_close",),                                   # 夹住
        ("lift_up", 2400, 40),                             # 抬起
    ]
    DEFAULT_PLACE_STEPS = [
        ("lift_down", 2400, 30),                           # 龙门下放到地面
        ("grip_open",),                                    # 开爪放料
        ("lift_up", 2400, 40),                             # 抬起
    ]

    def step_to_axis(self, step):
        """把一步翻译成 (轴号, 方向, 脉冲, RPM)；不是轴动作就返回 None。"""
        st = list(step)
        kind = str(st[0]).strip().lower()
        if kind in self._STEP_ALIAS:
            axis, d, p_def, v_def = self._STEP_ALIAS[kind]
            pulses = int(st[1]) if len(st) > 1 else p_def
            vel = int(st[2]) if len(st) > 2 else v_def
            return axis, d, pulses, vel
        if kind == "axis":
            axis = int(st[1])
            d = int(st[2])
            pulses = int(st[3]) if len(st) > 3 else 0
            vel = int(st[4]) if len(st) > 4 else 40
            return axis, d, pulses, vel
        return None

    def run_steps(self, steps, gid=7, timeout=60.0):
        """【比赛模式】整组下发，由**下位机**逐条执行（通信少、时序准、每步等到位）。"""
        steps = [s for s in (steps or []) if str(s[0]).lower() != "break"]
        self.log("  → 动作组下发执行（%d 步，gid=%d）" % (len(steps), gid))
        return self.lower.run_steps(steps, gid=gid, timeout=timeout)

    def apply_step(self, step, tag=""):
        """【调试模式】**由上位机**执行单独一步（发一条命令并等到位）。
        返回 True=执行了 / False=执行失败 / None=不认识的步骤。"""
        st = list(step)
        kind = str(st[0]).strip().lower()
        if kind == "break":
            self.log("  ⏸ 断点：%s" % (st[1] if len(st) > 1 else ""))
            return True
        if kind == "wait":
            self.wait(int(st[1]))
            return True
        if kind in ("par", "parallel", "both"):
            # ★多轴并行（见 apply_parallel）：把组里的命令**先全部发出去**，再按最慢的一步统一等待。
            #   写法：("par", [子步1, 子步2, …])，子步就是平常那些 ("lift_up", 脉冲, 速度) /
            #   ("axis", 轴号, 方向, 脉冲, 速度) 等。
            sub = list(st[1]) if len(st) > 1 else []
            n_ok, _ok, _w = self.apply_parallel(sub)
            return True if n_ok else None
        if kind in ("grip_close", "close"):
            self.grip_close()
            return True
        if kind in ("grip_open", "open"):
            self.grip_open()
            return True
        if kind in ("grip_angle", "grip"):
            self.grip_angle(int(st[1]))
            return True
        ax = self.step_to_axis(st)
        if ax is None:
            self.log("  !! 未知步骤：%r" % (list(step),))
            return None
        axis, d, pulses, vel = ax
        name = {AXIS_ARM: "伸缩", AXIS_BASE: "base"}.get(axis, "龙门架")
        if kind in self._STEP_ALIAS:
            name = kind
        self._move_axis(axis, d, pulses, vel, name)
        return True

    # ------------------------------------------------------------------
    # ★多轴并行（2026-09-18 车主："龙门架、伸缩台、base 轴同时开启运动…
    #   等最后一个部分的动作做完了，再进行 base 大回转"）
    # ------------------------------------------------------------------
    def fire_axis(self, axis, d, pulses, vel, name=""):
        """**只发命令、不等**（多轴并行用）—— 返回这条命令的估算耗时（秒）。

        与 _move_axis 的唯一区别：发完立刻返回、不 sleep。
        之所以安全：固件的 0x10/0x11/0x12 本来就是**非阻塞**的（TTLctrl.c
        「发完即返回,不等待」），所以"发出去"和"走完"本来就是两件独立的事。
        """
        pulses = int(pulses)
        vel = int(vel) or 40
        if pulses <= 0:
            return 0.0
        label = name or {AXIS_ARM: "伸缩", AXIS_GANTRY: "龙门架",
                         AXIS_BASE: "base"}.get(axis, "轴%d" % axis)
        self.log("  → [并行] %s %s %d 脉冲 @%dRPM（先发不等）"
                 % (label, "正转" if d == FWD else "反转", pulses, vel))
        self.lower.ttl(axis, d, vel, pulses)
        return self.ttl_time(pulses, vel, axis=axis)[0] + \
            float(getattr(SC, "TTL_TIME_EXTRA_S", 0.25))

    def apply_parallel(self, steps, tag="[并行] "):
        """★多轴**同时**运动：先把所有命令发出去，再按**最慢那一步**统一等待。

        为什么能这么干：固件的 0x10/0x11/0x12 是**非阻塞**的，三条命令下给三个不同的
          驱动器，各自独立执行 → 天生就能同时动（这不是我们伪造出来的并行）。
        为什么"等最慢的"就够：每一步的耗时都算得出来（见 ttl_time：匀速 + 加减速 +
          该轴裕量），取最大值即可；最慢的那步走完时，快的早就停稳了。
        好处（车主 2026-09-18 的目标）：原本"回零(base+伸缩) → 再抬龙门架"要串行等两段，
          现在三轴一起动、只等一次 → 省掉较短那一段的全部时间。

        ⚠️ 只用于**不同轴**的组合。同一个轴上写两个动作会互相覆盖目标位置
           （TTL 位置命令是覆盖式的）；真写了会打告警，不会静默出错 ——
           那种情况请拆成两个 par 组，或退回串行。
        舵机步骤（grip_*）在组里会**立即发出**（它本来就是即时动作，不参与等待计算）。

        返回 (成功发出的步数, 是否全部发出, 实际等待秒数)。
        """
        steps = [list(s) for s in (steps or [])]
        waited, n_ok, seen = 0.0, 0, {}
        for st in steps:
            kind = str(st[0]).strip().lower()
            if kind in ("grip_close", "close", "grip_open", "open",
                        "grip_angle", "grip"):
                self.apply_step(st)                     # 舵机即时，直接发
                n_ok += 1
                continue
            ax = self.step_to_axis(st)
            if ax is None:
                self.log("  %s!! 并行组里有不认识的步骤：%r（跳过）" % (tag, st))
                continue
            axis, d, pulses, vel = ax
            if axis in seen:
                self.log("  %s!! 同一个轴在并行组里出现了两次（%r → %r），"
                         "后面的会覆盖前面的目标！请拆成两个 par 组"
                         % (tag, seen[axis], st))
            seen[axis] = st
            waited = max(waited, self.fire_axis(axis, d, pulses, vel))
            n_ok += 1
        if waited > 0:
            self.log("  %s各轴已同时启动 → 等最慢的一步 %.2fs（三者距离/速度不同，取最大值）"
                     % (tag, waited))
            time.sleep(0.0 if self.dry else waited)
        return n_ok, (n_ok == len(steps)), waited

    def run_steps_host(self, steps, stop_at_break=True):
        """【调试模式】上位机逐条执行整组；遇到 ("break",…) 时按 stop_at_break 决定是否停下。
        返回 (执行步数, 是否全成功)。调试器就是调它 + step_once。"""
        steps = list(steps or [])
        n_ok, ok = 0, True
        for i, st in enumerate(steps, 1):
            if str(st[0]).lower() == "break" and stop_at_break:
                self.log("  ⏸ [%d/%d] 断点：%s —— 停下" % (i, len(steps), st[1] if len(st) > 1 else ""))
                break
            self.log("  [%d/%d] %r" % (i, len(steps), list(st)))
            r = self.apply_step(st)
            if r is None:
                ok = False
            elif r:
                n_ok += 1
        return n_ok, ok

    def init_axes(self, steps, gid=7, mode="board"):
        """上电初始化：把机械臂从"上电摆放位"送到"起始位"（原固件 TTLctrl_Init 的三段运动）。

        mode="board" —— 整组下发，下位机 motion.c 逐条执行、每步等到位（推荐，used by 比赛）
        mode="host"  —— 上位机逐条执行（现场观察/排查时用，通信次数多但能看到每条日志）
        """
        steps = list(steps or [])
        if not steps:
            self.log("  !! 初始化动作组为空，跳过")
            return False
        self.log("  → 上电初始化 %d 步（%s 模式）" % (len(steps), mode))
        if str(mode).lower().startswith("host"):
            _, ok = self.run_steps_host(steps, stop_at_break=False)
            return ok
        return self.run_steps(steps, gid=gid, timeout=60.0)

    # ==================================================================
    # 3. 视觉 / 瞄准
    # ==================================================================
    def _aimer(self):
        if self._aim is None:
            from aim import Aim
            self._aim = Aim(self.vision, self.lower, log=self.log, dry_run=self.dry,
                            color_vision=self.color_vision)
        return self._aim

    def locate(self, mode="ring", color=None):
        """看一眼目标：mode="ring" = 圆环中心；mode="color" = 目标颜色圆块中心。"""
        if self.vision is None:
            return {"found": False}
        return self._aimer().locate(mode=mode, color=color)

    def aim(self, mode="ring", color=None, tag="", timeout=None):
        """收敛到目标中心：返回 (ok, offset)。offset 可交给 replay() 回放。

        · mode="color" + color="red"/"green"/"blue" → **原料区取料**：认目标颜色圆块中心
        · mode="ring"（默认）                        → **加工区放置**：认标靶圆环中心

        ★★2026-09-18 车主："在瞄准物料的过程中夹爪要再次打开成初始化大小"。
          为什么必须：夹爪半开/闭合时**爪片会伸进镜头视野**，把该颜色的色块切掉一块、
          甚至爪片反光自己被当成目标 → 质心偏移 → 瞄准等于在噪声上收敛
          （表现就是"越修越偏 / 中心附近抖"）。
          放在**这里**而不是 pick_here，是因为 grab_demo / route_main 都是
          「先单独 aim 拿到 offset → 再 pick_here(aim=False)」，
          放在 pick_here 的 if aim 分支里**根本不会被执行**（2026-09-18 实际验证发现）。
          走 0x14 → 固件 SERVO_GRIP_OPEN，该值由 setup() 的 grip_open_angle 下发。
          ⚠️ 只管瞄准阶段；取料/放置**动作组里**的 grip 步骤一律不动
             （车主："放置到载物架的动作组中保持当前程序的大小即可"）。
        """
        try:
            self.grip_open()          # 张开到初始开度，免得爪片挡镜头
        except Exception:             # 夹爪不在（比如纯圆环模式/无硬件）时不该挡住瞄准
            pass
        return self._aimer().converge(tag=tag, timeout=timeout, mode=mode, color=color)

    def replay(self, offset, sign=+1, tag=""):
        """按 offset 回放（sign=-1 回观察位，+1 回靶心）。"""
        return self._aimer().replay(offset, sign=sign, tag=tag)

    # ==================================================================
    # 4. 复合工序（流水账里一行搞定一个工序）
    # ==================================================================
    def pick_steps_of(self, steps=None):
        """取料动作组的来源优先级：显式传入 > car.pick_steps（主程序参数区）> DEFAULT_PICK_STEPS。"""
        return list(steps or self.pick_steps or self.DEFAULT_PICK_STEPS)

    def pick_here(self, expect_color=None, tag="G", steps=None,
                  out_pulses=3000, out_vel=30, in_vel=40, open_deg=None,
                  blind=None, blind_pulses=None, aim=True):
        """【取料工序】瞄准（可关）→ 执行**取料动作组**。

        · `steps`：动作组，形如
              [("grip_open",), ("arm_out",3000,30), ("lift_down",2400,30),
               ("grip_close",), ("lift_up",2400,40), ("arm_in",3000,40)]
          不给就用 `car.pick_steps`（= 主程序参数区的 `PICK_STEPS`）→ 再退回 DEFAULT_PICK_STEPS。
          ★**现场要调的"从地面夹取细节"就是这个列表**，配合 `pick_debug.py` 单步试。
        · `aim=False`：跳过瞄准，直接跑动作组（纯调动作 / 纯盲夹）。
        · `blind=True`：瞄准失败也照跑动作组（原料区物料位置固定时的兜底）。
        · `open_deg`：动作组里没写张爪步骤时，可在这里给个"预张爪角度"（None = 不预张）。
        · `out_pulses/out_vel/in_vel`：仅为兼容旧调用保留，动作组化后不再使用。
        """
        steps = self.pick_steps_of(steps)
        mode = "color" if (expect_color and self.color_vision is not None) else "ring"
        has_open = any(str(list(s)[0]).lower() in ("grip_open", "open", "grip_angle", "grip")
                       for s in steps)
        if open_deg is not None and not has_open:
            self.grip_angle(int(open_deg))            # 动作组里没张爪 → 这里补一下
        if aim:
            # 注：**瞄准前张爪**已经统一放在 car.aim() 里了（那才是所有瞄准的必经之路，
            #     因为 grab_demo / route_main 都是"先单独 aim 拿 offset → pick_here(aim=False)"，
            #     写在这里根本不会被执行）。这里不再重复一次，免得白白多花一次停稳时间。
            if mode == "color":
                self.log("  [%s] 瞄准目标 = %s 色圆块中心（颜色法）" % (tag, expect_color))
            elif expect_color:
                self.log("  [%s] !! 无颜色视觉（dry-run?），退回圆环瞄准" % tag)
            ok, _ = self.aim(mode=mode, color=expect_color, tag=tag)
            if not ok:
                if not blind:
                    self.log("  [%s] 瞄准失败且未开盲夹 → 放弃这次抓取" % tag)
                    return False
                self.log("  [%s] 瞄准失败 → **盲夹**：跳过瞄准直接跑取料动作组（物料位置固定）" % tag)
        else:
            self.log("  [%s] 跳过瞄准 → 直接跑取料动作组（%d 步）" % (tag, len(steps)))
        self.log("  [%s] 取料动作组 %d 步：%s" % (tag, len(steps), steps))
        n_ok, allok = self.run_steps_host(steps, stop_at_break=False)
        if not allok:
            self.log("  [%s] !! 动作组有步骤没执行成功（成功 %d/%d 步）" % (tag, n_ok, len(steps)))
        return allok

    def place_here(self, slot, tag="P", steps=None, out_pulses=3000, out_vel=30, in_vel=40):
        """【放置工序】瞄准记偏移 → 回观察位 → 0x16 去物架夹料 → 回放偏移 → 执行**放置动作组**。

        与现场验证过的 aim_and_place.py --mission 同构；圆环检测走 `ring_stream_ui` 同款算法
        （`ring_center_detect_v2.RingDetector`，参数来自 `ring_calib.json`）。
        ★2026-09-17：末段"下放/松爪/抬起"改成执行 `steps`（参数区 `PLACE_STEPS`）——
          **地面放料不用伸缩台**，直接下龙门架 → 开爪 → 抬龙门。
        · `out_pulses/out_vel/in_vel` 仅为兼容旧调用保留，动作组化后不再使用。
        """
        steps = list(steps or self.place_steps or self.DEFAULT_PLACE_STEPS)
        ok, off = self.aim(mode="ring", tag=tag)
        if not ok:
            self.log("  [%s] 瞄准失败，放弃这次放置" % tag)
            return False
        self.replay(off, sign=-1, tag="-home")        # 回观察位
        if not self.grab_from_rack(slot):
            self.log("  [%s] 夹料无回执" % tag)
            return False
        self.replay(off, sign=+1, tag="-back")        # 回靶心
        self.log("  [%s] 放置动作组 %d 步：%s" % (tag, len(steps), steps))
        n_ok, allok = self.run_steps_host(steps, stop_at_break=False)
        if not allok:
            self.log("  [%s] !! 动作组有步骤没执行成功（成功 %d/%d 步）" % (tag, n_ok, len(steps)))
        return allok

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
