# -*- coding: utf-8 -*-
"""
aim.py —— 瞄准控制器（把 aim_and_place.py 的控制律搬到校赛架构里）
=====================================================================
控制律（与现场验证过的 aim_and_place v4 一致）：
    停稳 → 单帧检测 → 阶段A 只用 base(轴4) 收敛画面 x
                    → 阶段B 只用伸缩(轴1) 收敛画面 y
                    → 阶段X2 再用 base 复校 x
    单拍限幅 AIM_OUT_MAX_PX（防冲过头）→ 收敛后把"观察位→靶心"的净偏移记下来。

两种用法（对应校赛的六次瞄准）：
    · G 抓取：`grab()`        —— 瞄准 → 直接夹（顺带用颜色确认这一位是不是该取的料）
    · P 放置：`place_mission()` —— 瞄准记录偏移 → 归位 → 去物架夹料(0x16) → 回放偏移 → 放料(0x13)

坐标系约定：K_PX2STEP_* 是"脉冲 → 画面像素位移"，正转(dir=0)让画面内容向 +x/+y 移动
    base: 200 脉冲 → dx = +51.9px   → K = 0.2593
    arm : 200 脉冲 → dy = + 8.3px   → K = 0.0413
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import school_config as SC                                       # noqa: E402

AXIS_BASE = 4          # base 轴（左右转，修画面 x）
AXIS_ARM = 1           # 伸缩轴（修画面 y）
DIR_FWD, DIR_REV = 0, 1


class Aim:
    def __init__(self, vision, lower, cfg=None, log=print, dry_run=False, settle=None):
        self.v = vision
        self.l = lower
        self.cfg = cfg or SC
        self.log = log
        self.dry = dry_run
        self.settle_s = SC.AIM_SETTLE_S if settle is None else settle
        if dry_run:
            self.settle_s = min(self.settle_s, 0.01)

    # ------------------------------------------------------------------
    def locate(self):
        frame = self.v.grab() if hasattr(self.v, "grab") else None
        loc = self.v.locate(frame)
        if loc.get("found"):
            loc["frame"] = frame
        return loc

    def _target_px(self, loc):
        """目标点 = 画面中心 + 夹爪/光轴不共轴补偿。"""
        shp = loc.get("shape") or (384, 512, 3)
        h, w = shp[0], shp[1]
        return w / 2.0 + SC.CAM_GRIP_OFFSET_X, h / 2.0 + SC.CAM_GRIP_OFFSET_Y

    @staticmethod
    def _steps_for(delta_px, k, out_max_px):
        """把像素误差换成脉冲（含符号 + 单拍限幅）。返回 (dir, pulses)。"""
        if k <= 0:
            return DIR_FWD, 0
        want = -delta_px / k                     # 画面要往 -delta 方向移动
        limit = out_max_px / k
        want = max(-limit, min(limit, want))
        pulses = int(round(abs(want)))
        if pulses <= 0:
            return None, 0
        return (DIR_FWD if want > 0 else DIR_REV), pulses

    def _apply(self, axis, d, pulses, k, sign_px):
        """下发一次 TTL 并累加偏移；dry-run 时同时"移动"仿真目标。"""
        vel = self._vel_of(axis)
        self.l.ttl(axis, d, vel, pulses)
        signed = pulses if d == DIR_FWD else -pulses
        moved_px = signed * k * sign_px
        if hasattr(self.v, "nudge"):             # 仿真闭环：画面里目标反向移动
            if axis == AXIS_BASE:
                self.v.nudge(moved_px, 0)
            else:
                self.v.nudge(0, moved_px)
        time.sleep(self.settle_s)

    def _vel_of(self, axis):
        try:
            prof = SC.load_profile()
            return int((prof.get("params") or {}).get(
                "ttl_default_vel", 40) if axis != AXIS_BASE else
                (prof.get("params") or {}).get("axes_home_speed", 40))
        except Exception:
            return 40

    # ------------------------------------------------------------------
    def converge(self, tag="", timeout=None, want_color=None, tol=None):
        """分轴收敛到画面中心。返回 (ok, offset_dict)。
        offset = {"base": 累计脉冲(带符号), "arm": 累计脉冲(带符号)}，回放时照抄。"""
        timeout = timeout or SC.T_AIM
        tol = SC.AIM_TOL_PX if tol is None else tol
        t0, ok_run, stable, off = time.time(), 0, 0, {"base": 0, "arm": 0}
        plan = [("base", "x"), ("arm", "y"), ("base", "x")]     # X1 → Y → X2
        it = 0
        while time.time() - t0 < timeout and it < SC.AIM_MAX_ITER:
            it += 1
            loc = self.locate()
            if not loc.get("found"):
                # 丢目标：先等一拍（运动残留 / 自动曝光调整），仍找不到就小步试探把目标"找回来"。
                # ★这里曾经崩过（2026-09-16 现场）：`_apply` 需要 (axis, d, pulses, k, sign_px) 五个参数，
                #   旧代码只传了三个 → TypeError: missing 2 required positional arguments。
                #   根因是 dry-run 仿真视觉一直 found=True，**这条分支从没被跑到**；
                #   现在回归里加了 t6_lost_target() 专项覆盖。
                self.log("  [%s#%d] 未找到目标 → 小步试探（重试上限 %d）" % (tag, it, SC.AIM_RETRY))
                if it > SC.AIM_RETRY:
                    self.log("  [%s] 连续丢目标，放弃本次瞄准" % tag)
                    break
                time.sleep(self.settle_s)
                probe = int(SC.AIM_PROBE_PX / SC.K_PX2STEP_ARM)
                self._apply(AXIS_ARM, DIR_REV, probe, SC.K_PX2STEP_ARM, +1)
                off["arm"] -= probe
                continue
            if want_color is not None and hasattr(self.v, "find"):
                c = self.v.find(loc.get("frame"), want_color)
                if not c.get("found"):
                    self.log("  [%s#%d] 颜色确认失败（期望 %s）" % (tag, it, want_color))
                    return False, off
            tx, ty = self._target_px(loc)
            dx, dy = loc["cx"] - tx, loc["cy"] - ty
            self.log("  [%s#%d] c=(%.0f,%.0f) dx=%+.1f dy=%+.1f r=%.0f digit=%s"
                     % (tag, it, loc["cx"], loc["cy"], dx, dy, loc.get("r", 0), loc.get("digit")))
            if abs(dx) <= tol and abs(dy) <= tol:
                stable += 1
                if stable >= 2:
                    self.log("  [%s] 收敛 ✓  offset=%s" % (tag, off))
                    return True, off
                time.sleep(self.settle_s)
                continue
            stable = 0
            axis, comp = plan[min(ok_run, len(plan) - 1)]
            if comp == "x":
                d, pulses = self._steps_for(dx, SC.K_PX2STEP_BASE, SC.AIM_OUT_MAX_PX)
                if d is not None:
                    self._apply(AXIS_BASE, d, pulses, SC.K_PX2STEP_BASE, +1)
                    off["base"] += pulses if d == DIR_FWD else -pulses
            else:
                d, pulses = self._steps_for(dy, SC.K_PX2STEP_ARM, SC.AIM_OUT_MAX_PX)
                if d is not None:
                    self._apply(AXIS_ARM, d, pulses, SC.K_PX2STEP_ARM, +1)
                    off["arm"] += pulses if d == DIR_FWD else -pulses
            ok_run = (ok_run + 1) % len(plan)
        self.log("  [%s] !! 未收敛（%.1fs，%d 次迭代），offset=%s" % (tag, time.time() - t0, it, off))
        return False, off

    def replay(self, off, sign=+1, tag=""):
        """按记录的偏移回放（sign=-1 回观察位，+1 回靶心）。"""
        for axis, key, k in ((AXIS_BASE, "base", SC.K_PX2STEP_BASE),
                             (AXIS_ARM, "arm", SC.K_PX2STEP_ARM)):
            pulses = int(off.get(key, 0)) * int(sign)
            if pulses == 0:
                continue
            d = DIR_FWD if pulses > 0 else DIR_REV
            self.log("  [replay%s] %s %s %d 脉冲" % (tag, "base" if axis == AXIS_BASE else "arm",
                                                    "正转" if d == DIR_FWD else "反转", abs(pulses)))
            self._apply(axis, d, abs(pulses), k, +1)      # 走 _apply：dry-run 时也会动仿真目标
        
    # ------------------------------------------------------------------
    # 校赛的两种瞄准用法
    # ------------------------------------------------------------------
    def grab(self, expect_color=None, tag="G"):
        """G：瞄准 → 直接夹。（取料时物料就在夹爪可及处，瞄上即夹）"""
        ok, off = self.converge(tag=tag, want_color=expect_color)
        if not ok:
            self.log("  [%s] 瞄准失败，放弃这次抓取" % tag)
            return False, off
        self.l.run_group("PICK_DOWN")          # 下探 + 夹（步骤来自下发）
        self.l.run_group("PICK_UP")
        return True, off

    def place_mission(self, slot, tag="P"):
        """P：瞄准记录偏移 → 归位 → 去物架夹料 → 回放偏移 → 放料。
        与现场验证过的 `aim_and_place.py --mission` 完全同构。"""
        ok, off = self.converge(tag=tag)
        if not ok:
            self.log("  [%s] 瞄准失败，放弃这次放置" % tag)
            return False
        self.replay(off, sign=-1, tag="-home")           # 回观察位
        self.log("  [%s] 去车载物架 %d 号位夹料 (0x16)" % (tag, slot))
        if not self.l.run_group("BUILTIN_GRAB_VEH", slot):
            self.log("  [%s] !! 夹料无回执" % tag)
            return False
        self.replay(off, sign=+1, tag="-back")           # 回靶心
        self.log("  [%s] 放料 (0x13)" % tag)
        if not self.l.run_group("BUILTIN_PLACE"):
            self.log("  [%s] !! 放料无回执" % tag)
            return False
        return True
