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


def _maybe_flip(d, which):
    """按 SC.AIM_FLIP_BASE / AIM_FLIP_ARM 翻转下发方向（2026-09-17 加）。

    ★为什么要这么一个开关：`_steps_for()` 的方向来自"K 为正 = 正转让画面内容向 +x/+y 移动"
      这个**约定**，而 K_PX2STEP_* 是 `calibrate_aim.py` 的结果**人工换算**出来的
      （那个脚本内部把画面逆时针转了 90°，换算是靠人看 dx/dy 推的），只保证了数值大小；
      方向符号一旦推反，症状就是"**一开始朝远离物料的方向转**"。
      更阴的是：base 转动会让物料在画面里**绕中心转圈**，符号反了不一定表现为发散，
      反而可能转半圈之后从另一侧绕回来、**最终收敛**（车主原话："总是会绕一圈再回来"）
      → 光看现象极难怀疑到是符号问题。所以留一个开关，并用 grab_demo 的 `c` 命令实测判定。
    """
    if bool(getattr(SC, "AIM_FLIP_" + str(which).upper(), False)):
        return DIR_REV if d == DIR_FWD else DIR_FWD
    return d


def _stage_limit(err_px, which="x"):
    """分段单拍限幅：**误差越大允许走的步子越大**。

    ★2026-09-18 车主"瞄准还是慢"：原来每拍一律最多修 SC.AIM_OUT_MAX_PX(12px)，
      初始偏差 120px 时要 11 拍 ≈9.4s。改成分段后（60/25/12）同样条件只要 5 拍 ≈4.2s。
    分段表 SC.AIM_LIMIT_STAGES = [(阈值px, 限幅px), …]（从大到小）；空/None = 旧行为。

    `which="arm"` 时各档再乘 SC.AIM_ARM_LIMIT_SCALE —— arm 的 K 只有 base 的 1/5.5，
    同样 45px 折算成脉冲是 base 的 5 倍多，步子更猛、更容易冲过中心
    （车主 2026-09-18："伸缩台前后方向调整时在中心前后晃动较大"）。
    """
    lim = None
    for stage in (getattr(SC, "AIM_LIMIT_STAGES", None) or []):
        try:
            thr, l = float(stage[0]), float(stage[1])
        except (TypeError, ValueError, IndexError):
            continue
        if err_px > thr:
            lim = l
            break
    if lim is None:
        lim = float(SC.AIM_OUT_MAX_PX)
    # ⚠️ 传进来的 which 是 "x"/"y"（comp），也可能直接写 "arm" —— 两种都要认。
    #    （曾经只判断 startswith("a")，"y" 匹配不上 → arm 缩放**静默失效**，2026-09-18 抓到）
    w = str(which).lower()
    if w == "y" or w.startswith("arm"):
        lim *= float(getattr(SC, "AIM_ARM_LIMIT_SCALE", 1.0) or 1.0)
    return lim


def _soften(err_px):
    """近中心**软化**：误差进入软化区后只走 SC.AIM_SOFT_GAIN 比例（治"中心来回摆"）。

    ★2026-09-18 车主："伸缩台前后方向调整时在中心前后晃动幅度较大"。
      根因是**每拍步长（12px）大于收敛容差（8px）**——一步就能跨过中心，下一拍再摆回来。
      进入软化区后只走一半，就跨不过去了，于是平稳贴合中心。
      离线仿真（起点 dy=+30、容差 8px、实际 K 比配置大 60%）：
        不软化 [30, 10.8, -6.5]（从 +10.8 一步跨到 -6.5，跨中心 17px）
        软化后 [30, 10.8,  2.2]（收敛，且**拍数不增加**）
    """
    zone = float(getattr(SC, "AIM_SOFT_ZONE_PX", 0.0) or 0.0)
    gain = float(getattr(SC, "AIM_SOFT_GAIN", 1.0) or 1.0)
    if zone > 0 and 0.0 < gain < 1.0 and abs(err_px) < zone:
        return err_px * gain
    return err_px


def _k_scaled(k, r_px):
    """按目标半径（≈距离）缩放 K —— "根据距离对修正幅度加权"。

    K = px/脉冲 本身随距离变化：离得近，同样脉冲造成的画面位移更大（K 更大）。
    用固定 K 会让近处"走不够"、远处"冲过头"。画面里**目标半径 r 就是距离的度量**，
    所以 K_eff = K × (r / K_R_REF)。★不需要额外标定 —— K_R_REF 就是当初标 K 时的 r。
    """
    ref = float(getattr(SC, "K_R_REF", 0.0) or 0.0)
    if not bool(getattr(SC, "K_R_SCALE", False)) or ref <= 0 or r_px <= 0:
        return k
    return k * (float(r_px) / ref)


class Aim:
    def __init__(self, vision, lower, cfg=None, log=print, dry_run=False, settle=None,
                 color_vision=None, target_mode="ring"):
        self.v = vision
        self.l = lower
        self.cfg = cfg or SC
        self.log = log
        self.dry = dry_run
        self.cv = color_vision          # ColorVision（取料时按"目标颜色圆块中心"瞄准）
        self.target_mode = target_mode  # "ring"=圆环中心（加工区放置）/ "color"=颜色圆块中心（原料区取料）
        self.settle_s = SC.AIM_SETTLE_S if settle is None else settle
        if dry_run:
            self.settle_s = min(self.settle_s, 0.01)
        # ★防震荡（2026-09-17 现场"在目标点周围大幅晃"）：记录每轴上一拍的误差符号，
        #   一旦符号来回翻转（= 上一拍冲过头了），就把下一拍的限幅逐次减半。
        self._last_err = {}
        self._flip = {}

    def _damp(self, comp, err):
        """返回本拍的限幅倍率：1 / 1/2 / … / 1/2^N（N = SC.AIM_DAMP_MAX_FLIP）。

        原理：同一轴的误差符号**反复翻转** = 上一拍冲过头了 → 步子减半；连续同号 = 步子恢复。
        于是"大幅晃"会很快变成"小幅微调"。好处是**不依赖 K 值标得多准** ——
        K 是"像素/脉冲"，它随相机到目标的距离变化（近处同脉冲位移更大），本来就做不到处处准。

        ★2026-09-18 车主"狠狠加修正阻尼" → 上限改成可配（SC.AIM_DAMP_MAX_FLIP）：
          原来是写死的 3（最多压到 1/8），现在是 4（1/16），嫌晃还能继续加到 5。
          该轴一旦长时间同号（说明方向对、离得远），阻尼立刻回到 1 → 不会白白变慢。
        """
        cap = max(0, int(getattr(
            SC,
            # arm（画面 y 轴）用单独、更狠的上限：它的 K 只有 base 的 1/5.5，
            # 同样的限幅要发 5 倍多脉冲，一旦 K 偏小就冲过头（车主报的"中心前后晃"）
            "AIM_DAMP_MAX_FLIP_ARM" if str(comp).lower().startswith("y")
            else "AIM_DAMP_MAX_FLIP",
            3) or 0))
        last = self._last_err.get(comp)
        if last is not None and abs(err) > 1e-6 and (err > 0) != (last > 0):
            self._flip[comp] = min(self._flip.get(comp, 0) + 1, cap)
        else:
            self._flip[comp] = 0
        self._last_err[comp] = err
        return 1.0 / (2 ** self._flip.get(comp, 0))

    # ------------------------------------------------------------------
    def locate(self, mode=None, color=None):
        """看一眼目标。

        · mode="ring" （默认）→ 圆环中心：`ring_center_detect_v2.RingDetector`（ring_stream_ui 同款）
        · mode="color"        → **目标颜色圆块的中心**（color_blob_demo 同款算法 + EMA 滤波）
        两种模式返回同一结构 {found, cx, cy, r, digit, conf, stale, shape, frame}，坐标系一致，
        所以下游的分轴收敛/限幅/回放偏移完全不用改。
        """
        mode = str(mode or self.target_mode or "ring").lower()
        frame = self.v.grab() if hasattr(self.v, "grab") else None
        if mode == "color" and self.cv is not None and color:
            fr = self.v.prepare(frame) if hasattr(self.v, "prepare") else frame
            loc = self.cv.track(fr, color)
            if loc.get("frame") is None:
                loc["frame"] = fr
            return loc
        loc = self.v.locate(frame)
        # ⚠️ 必须用 `is None`：loc["frame"] 是 numpy 数组，`not array` 会抛
        #    "The truth value of an array with more than one element is ambiguous"（2026-09-17 现场踩到）
        if loc.get("frame") is None:
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
        """瞄准微调的速度（RPM）。

        ★2026-09-17 改：以前只去 motion_profile.json 里找 ttl_default_vel / axes_home_speed，
          但**部署目录里经常没有那个文件**（/root/VisualBot2 就只有 color_profile.json），
          于是每次调用都抛 FileNotFoundError 被吞掉 → 一路兜底成 40 →
          "瞄准速度"实际上变成了谁也没法调的硬编码。
          现在优先读 SC.AIM_VEL_BASE / SC.AIM_VEL_ARM（默认 40，与旧行为完全一致），
          现场要治"摇摆"直接调这两个数即可；没配就退回原来的 profile 逻辑。
        """
        v = getattr(SC, "AIM_VEL_BASE" if axis == AXIS_BASE else "AIM_VEL_ARM", None)
        if v:
            return max(1, int(v))
        try:
            prof = SC.load_profile()
            return int((prof.get("params") or {}).get(
                "ttl_default_vel", 40) if axis != AXIS_BASE else
                (prof.get("params") or {}).get("axes_home_speed", 40))
        except Exception:
            return 40

    def _dump_fail(self, tag, loc, color):
        """丢目标放弃时存一帧诊断图（颜色模式：原图 + 该颜色的掩膜）→ `aim_fail/`。

        没有它的时候，现场只能猜"是阈值/门限不对，还是料根本不在画面里"；
        有了它看一眼就知道（2026-09-17 现场加，专治"未找到目标"这类扯皮）。
        """
        try:
            cv = getattr(self, "cv", None)
            fr = (loc or {}).get("frame")
            if cv is None or fr is None or not hasattr(cv, "save_debug"):
                return
            p = cv.save_debug(fr, color or "?", out_dir="aim_fail", tag=tag)
            if p:
                self.log("  [%s] 已存诊断帧：%s（同名 _mask.png 是该颜色的掩膜）" % (tag, p))
        except Exception:                                        # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    def converge(self, tag="", timeout=None, want_color=None, tol=None, mode=None, color=None):
        """分轴收敛到画面中心。返回 (ok, offset_dict)。
        offset = {"base": 累计脉冲(带符号), "arm": 累计脉冲(带符号)}，回放时照抄。
        · mode="color" + color="red"/"green"/"blue" → 按**目标颜色圆块中心**收敛（原料区取料）
        · mode="ring"（默认）                        → 按**圆环中心**收敛（加工区放置）
        """
        timeout = timeout or SC.T_AIM
        tol = SC.AIM_TOL_PX if tol is None else tol
        use_mode = str(mode or self.target_mode or "ring").lower()
        # ★开工预热（2026-09-17，车主现场反馈"**一开始的修正方向与正确方向相反**，走一段才绕回来"）：
        #   病根是**第一拍用的那一帧不可信**，两种情况都会命中：
        #     ① 上一个动作刚停，机械还在余振、画面/曝光没稳；
        #     ② ColorVision/BlobTracker 的 stale 保持：连续丢失 ≤ stale_max 帧时会把
        #        **上一次的中心**当作 found=True 返回（conf=0.5、raw=旧中心），
        #        第一拍据此算出的方向可能就是反的；等真实检测恢复才发现走错 → 看着像"绕一圈"。
        #
        # ★★2026-09-18 晚 车主："能不能简化一下这个过程，用时太长了" —— 确实如此：
        #   原判据是 `stale==0 且 帧间抖动≤3px`，而**抖动至少要两帧才算得出来**
        #   → 画面再稳，预热也至少跑 2 次 = 2×(settle + 取帧) ≈ 1.1~1.5s。
        #   现在把"抖动"降级为**可选**（SC.AIM_WARMUP_JITTER_PX，默认 0 = 不检查）：
        #   只要求"本帧是真检测（stale==0）" → **第 1 帧就放行**，实测 ≈0.55s。
        #   敢去掉抖动的理由：
        #     · 真正治"方向反"的是 stale 判据（挡住②里那种"拿上一轮中心顶替"），必须留；
        #     · 抖动那条原本顺带挡两件事 —— "机械还在余振"（由预热前那一次 settle 等待负责）
        #       和"EMA 还没爬到新位置"（只会让第一拍**走得少一点**，第二拍就纠正，
        #       **不会让方向反**）。这两样的代价都远小于白等一整帧。
        #   想恢复严格行为：把 AIM_WARMUP_JITTER_PX 填 3 即可（判据和日志都还在）。
        warm = int(getattr(SC, "AIM_WARMUP_FRAMES", 0) or 0)
        jit_max = float(getattr(SC, "AIM_WARMUP_JITTER_PX", 0.0) or 0.0)
        w_settle = getattr(SC, "AIM_WARMUP_SETTLE_S", None)
        w_settle = self.settle_s if w_settle is None else float(w_settle or 0.0)
        prev = None
        for _w in range(max(0, warm)):
            time.sleep(w_settle)
            wl = self.locate(mode=use_mode, color=color)
            okf = bool(wl.get("found")) and int(wl.get("stale", 0) or 0) == 0
            pos = (float(wl["cx"]), float(wl["cy"])) if wl.get("found") else None
            jit = (max(abs(pos[0] - prev[0]), abs(pos[1] - prev[1]))
                   if (prev is not None and pos is not None) else None)
            self.log("  [%s] 预热 %d/%d：found=%s stale=%s%s"
                     % (tag, _w + 1, warm, wl.get("found"), wl.get("stale"),
                        ("  帧间抖动=%.1fpx" % jit) if jit is not None else ""))
            if okf and (jit_max <= 0.0 or (jit is not None and jit <= jit_max)):
                if jit_max <= 0.0 or jit is None:
                    extra = "（抖动判据已关）"
                else:
                    extra = "，帧间抖动 %.1fpx ≤ %.1fpx" % (jit, jit_max)
                self.log("  [%s] 预热结束（第 %d 帧）：本帧真检测到%s" % (tag, _w + 1, extra))
                break
            prev = pos
        # ★★三阶段瞄准（车主 2026-09-18 定的流程）：
        #     ① X1：只用 base 收敛画面 **x**，连续 2 拍 |dx| ≤ tol 才算达标；
        #     ② Y ：只用伸缩收敛画面 **y**，同上；
        #     ③ X2：再用 base 把 **x 复校回中**（②动伸缩时会轻微扰动 x，所以补这一下）。
        #   ★与旧实现（plan = base,arm,base **每拍交替**）的区别：
        #     交替时每 3 拍只有 2 拍在修同一个轴 → 等效收敛速度只有 2/3，
        #     而且日志上 x/y 交替跳动、现场看不出卡在哪一步。
        #     分阶段后一个轴可以**连续修**，明显更快；每阶段只盯一个误差，好判断好调参。
        STAGES = (("x", AXIS_BASE), ("y", AXIS_ARM), ("x", AXIS_BASE))
        stage, stage_ok = 0, 0            # 当前阶段 / 本阶段"连续达标"计数
        stale_run = 0                     # ★连续多少拍只拿到"tracker 保持的旧中心"
        t0, off = time.time(), {"base": 0, "arm": 0}
        it = 0
        while time.time() - t0 < timeout and it < SC.AIM_MAX_ITER:
            it += 1
            # ★决策前连采几帧（SC.AIM_READ_FRAMES，1 = 关）：
            #   中值/EMA 的历史是按"每拍一帧"累积的，只读 1 帧时读到的是被旧值拖住的位置
            #   → 控制器误判"还没到位" → 多推 → 过冲。多采几帧能把滤波拉向**当前真实位置**。
            #   （相机缓冲积压是更狠的同类问题，那个在 vision.RingVision.grab() 里治：
            #     取帧前先用 cap.grab() 把旧帧抽干，见 SC.CAM_FLUSH_FRAMES。）
            nf = max(1, int(getattr(SC, "AIM_READ_FRAMES", 1) or 1))
            loc = None
            for _f in range(nf):
                loc = self.locate(mode=use_mode, color=color)
                if _f < nf - 1:
                    time.sleep(0.05)
            if not loc.get("found"):
                # 丢目标：先等一拍（运动残留 / 自动曝光调整），仍找不到就小步试探把目标"找回来"。
                # ★这里曾经崩过（2026-09-16 现场）：`_apply` 需要 (axis, d, pulses, k, sign_px) 五个参数，
                #   旧代码只传了三个 → TypeError: missing 2 required positional arguments。
                #   根因是 dry-run 仿真视觉一直 found=True，**这条分支从没被跑到**；
                #   现在回归里加了 t6_lost_target() 专项覆盖。
                self.log("  [%s#%d] 未找到目标（连续第 %d 次，上限 %d）" % (tag, it, it, SC.AIM_RETRY))
                if it > SC.AIM_RETRY:
                    self.log("  [%s] 连续丢目标，放弃本次瞄准" % tag)
                    self._dump_fail(tag, loc, color)
                    break
                # ★2026-09-17 修：以前这里"每丢一次就按 K 换算把伸缩臂后收一次"：
                #     probe = AIM_PROBE_PX / K_ARM = 40 / 0.0385 ≈ 1039 脉冲，还**单向**(DIR_REV)，
                #     3 次 ≈ 3100 脉冲 > 伸缩行程 3000 → **每次瞄准失败都把臂收到行程尽头**
                #     （现场日志 offset arm=-2904 就是它），下一个物料平移过来时臂已不在原位。
                #   现在：丢目标时**默认只等待、不动**（给视觉恢复的机会）；
                #   确实要试探时用 AIM_PROBE_PULSES 给一个**固定的小脉冲**、正反交替、并限累计。
                probe = int(getattr(SC, "AIM_PROBE_PULSES", 0) or 0)
                if probe <= 0:
                    time.sleep(self.settle_s)
                    continue
                cap = int(getattr(SC, "AIM_PROBE_TOTAL", 400) or 400)
                if abs(off["arm"]) + probe > cap:
                    self.log("  [%s] 试探累计已达上限 %d 脉冲 → 停止试探（改为等待/放弃）"
                             % (tag, cap))
                    time.sleep(self.settle_s)
                    continue
                d = DIR_FWD if (it % 2 == 0) else DIR_REV        # 正反交替，绝不单向跑掉
                self._apply(AXIS_ARM, d, probe, SC.K_PX2STEP_ARM, +1)
                off["arm"] += probe if d == DIR_FWD else -probe
                continue
            # ★★2026-09-18 晚 新增：「找到了」但其实是**旧中心顶替**时，绝不能拿来判断！
            #   `BlobTracker.update()` 在"连续丢失 ≤ stale_max 帧"时会把**上一次的中心**
            #   当作 found=True 返回（conf=0.5、stale=N、held=True）。上层若照单全收，
            #   就会拿着**上一轮的位置**算出"已经在画面中心附近"→ 三阶段各一拍"达标"
            #   → **零修正的假收敛**，一个动作都不发。
            #   现场事故（加工区三个靶位全部如此）根因是瞄准模式用错（见 place_demo.do_aim_target），
            #   但**这个防护是通用的**：只要视觉一时检测不到，就不该给出"瞄准好了"的结论。
            #   处理：这一拍既不判断也不动作，等真实检测回来；连续太多拍就明确放弃并存诊断帧。
            _st = int(loc.get("stale", 0) or 0)
            if _st > 0 or loc.get("held"):
                stale_run += 1
                self.log("  [%s#%d] 本帧是 tracker 保持的旧中心（stale=%d, held=%s）→ "
                         "不判断也不动作，等真实检测（连续第 %d 次）"
                         % (tag, it, _st, loc.get("held"), stale_run))
                if stale_run >= int(getattr(SC, "AIM_STALE_GIVEUP", 6) or 6):
                    self.log("  [%s] 连续 %d 拍都只有旧中心 → **放弃本次瞄准**"
                             "（视觉多半根本没看到目标，别硬凑）" % (tag, stale_run))
                    self._dump_fail(tag, loc, color)
                    break
                time.sleep(self.settle_s)
                continue
            stale_run = 0
            # 颜色确认只对"圆环模式"有意义：取料已改成按颜色质心定位（mode="color"），不必再确认一遍
            if use_mode != "color" and want_color is not None and hasattr(self.v, "find"):
                c = self.v.find(loc.get("frame"), want_color)
                if not c.get("found"):
                    self.log("  [%s#%d] 颜色确认失败（期望 %s）" % (tag, it, want_color))
                    return False, off
            tx, ty = self._target_px(loc)
            dx, dy = loc["cx"] - tx, loc["cy"] - ty
            # 本阶段用哪个轴、盯哪个误差
            comp, axis = STAGES[stage]
            err_now = dx if comp == "x" else dy
            self.log("  [%s#%d] 阶段%d/%d(%s) c=(%.0f,%.0f) dx=%+.1f dy=%+.1f r=%.0f"
                     % (tag, it, stage + 1, len(STAGES), comp,
                        loc["cx"], loc["cy"], dx, dy, loc.get("r", 0)))
            # ---- 本阶段只盯自己那个误差：达标 N 拍就进下一阶段 ----
            #   N = SC.AIM_STAGE_CONFIRM（默认 1）。**取 1 是刻意的**：
            #   每多确认一拍就是纯等待，三阶段三个边界 → 确认 2 拍要多花 6 拍
            #   （离线仿真：确认2拍=12拍，确认1拍=9拍，K 偏小时 13 vs 10）。
            #   万一某阶段被噪声误判提前切换也不用怕 —— **最后的 X2 阶段会兜底**，
            #   这正是"第三步再校一次 x"的意义。
            want_ok = max(1, int(getattr(SC, "AIM_STAGE_CONFIRM", 1) or 1))
            if abs(err_now) <= tol:
                stage_ok += 1
                if stage_ok >= want_ok:
                    if stage + 1 >= len(STAGES):
                        self.log("  [%s] 收敛 ✓（三阶段走完 X1→Y→X2）offset=%s" % (tag, off))
                        return True, off
                    self.log("  [%s] 阶段%d(%s) 达标 → 进入阶段%d(%s)"
                             % (tag, stage + 1, comp, stage + 2, STAGES[stage + 1][0]))
                    stage += 1
                    stage_ok = 0
                time.sleep(self.settle_s)
                continue
            stage_ok = 0
            # 本拍限幅 = **分段**限幅（误差大→步子大；arm 轴再乘缩放，见 _stage_limit）
            #            × 防震荡阻尼（误差符号来回翻 → 步子减半；arm 上限更狠）
            limit_px = _stage_limit(abs(err_now), comp) * self._damp(comp, err_now)
            # K 按**目标半径**加权（≈按距离）：近处 r 大 → 同脉冲位移大 → K 更大。
            # ★不用额外标定，K_R_REF 就是当初标 K 时的 r（现场日志里能看到）。
            r_now = float(loc.get("r") or 0.0)
            if comp == "x":
                kx = _k_scaled(SC.K_PX2STEP_BASE, r_now)
                d, pulses = self._steps_for(_soften(dx), kx, limit_px)
                if d is not None:
                    d = _maybe_flip(d, "BASE")
                    self._apply(AXIS_BASE, d, pulses, kx, +1)
                    off["base"] += pulses if d == DIR_FWD else -pulses
            else:
                ka = _k_scaled(SC.K_PX2STEP_ARM, r_now)
                d, pulses = self._steps_for(_soften(dy), ka, limit_px)
                if d is not None:
                    d = _maybe_flip(d, "ARM")
                    self._apply(AXIS_ARM, d, pulses, ka, +1)
                    off["arm"] += pulses if d == DIR_FWD else -pulses
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
