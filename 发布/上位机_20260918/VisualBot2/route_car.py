#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
route_car.py —— 「跑图专用」底盘动作库（VisualBot2）
================================================================================
只做**底盘**和**base 轴作业姿态**：

    fwd / back / left / right       前后左右平移（cm）
    spin(deg, ccw=False)            原地自旋（度）
    goto_cm(target)                 按纵向坐标自动前进/后退（工位对齐）
    base_left / base_right(脉冲)     base 轴(id4)左转/回正 —— 进料区、进加工区的作业姿态

**故意不含**：伸缩滑台、龙门架升降、夹爪 —— 跑图阶段碰都不碰，也就不可能撞、不会被机械臂
的毛病掩盖车身轨迹的问题。等路径调明白了，再由主工程去接这些动作。

协议（沿用固件）：`FF cmd b1..b7 FE`，10 字节、无校验、多字节大端
    0x01 前后(cm, int8 +前进)      0x02 左右(cm, int8 **负=右移**)    0x03 自旋(deg, int8 **负=顺时针**)
    0x04 显示二维码                 0x10/0x11/0x12  TTL 轴1/龙门/轴4（base 用 0x12）
凡方向符号都以固件 `chassis.c` 为准（本项目踩过两次方向反的坑，见 README 坑档案 #47）。
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# ★★sys.path 顺序至关重要（2026-09-18 踩到的大坑，车主发现"三阶段没整合进去"就是它）：
#   共享模块（car.py / vision.py …）会把**老工程 `/root/VisualBot`** 也塞进 sys.path，
#   而 `/root/VisualBot/校赛/` 下有**同名的旧版 aim.py / car.py / vision.py**。
#   原来这里是 `for p in (HERE, …): sys.path.insert(0, p)` —— 顺序插入，**后插的排最前**，
#   于是 `/root/VisualBot` 反超到 sys.path[0] → `import aim` 命中**旧版** →
#   三阶段瞄准 / 近中心软化 / arm 专项阻尼 / 实测 K 这些新改动**全部静默失效**
#   （现象：route 里瞄准日志退回老的 `digit=` 格式、收敛轨迹也是旧行为）。
#   实测抓到的证据：`>>> aim = /root/VisualBot/校赛/aim.py`。
#   → 改成**倒序插入**（后插的优先），并且先把自己从 sys.path 里清掉再插，
#     否则"已存在就跳过"会让顺序根本不变。
for _p in ("/root/VisualBot", "/root/VisualBot2", HERE):
    if not _p or not os.path.isdir(_p):
        continue
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import lower_api as LA                                          # noqa: E402

try:                                     # ★ 复用 car.Car 的瞄准 / 取料 / 放置 / 相机能力
    from car import Car
except Exception as _e:                                          # noqa: BLE001
    Car = None
    print("[warn] car.Car 不可用: %r（跑图将只能走位，不能做取料/放置）" % _e)

# base 轴 = TTL id4：0=正转(左转) 1=反转(回正)
BASE_FWD, BASE_REV = 0, 1


class RouteCar(Car if Car is not None else object):
    """跑图用车：**继承 car.Car**（底盘 + base + 瞄准 + 取料 + 放置 + 相机全都有），
    再补上跑图专用的走位、分段交互、任务码。

    ★★2026-09-18 车主："尝试着将 grab 放入跑图过程中"。
      原来 RouteCar 是**独立类**，只有底盘和 base —— 跑图时不做任何作业；
      要集成"瞄准+取料+放到物架"就得把 car.Car 那一整套
      （use_camera / aim / pick_here / replay / apply_step / 动作组）**再实现一遍**。
      改成继承之后这些**直接就有**，而且两边共用同一个 lower 对象
      （不会出现两个 SerialLower 抢同一个 /dev/stm32）。
    """

    def __init__(self, port="/dev/stm32", baud=115200, dry_run=False, log=print):
        # ★交给 Car.__init__ 建 lower / vision / color_vision / ledger / cur_cm / t0 —— 别再建一遍，
        #   否则会开出**第二个串口**，两个对象抢 /dev/stm32（2026-09-18 集成时改的）
        if Car is not None:
            super().__init__(port=port, baud=baud, dry_run=dry_run, log=log)
        else:
            self.dry, self.log = dry_run, log
            self.lower = LA.MockLower(verbose=False) if dry_run else LA.SerialLower(port, baud)
            self.cur_cm, self.t0 = 0.0, time.time()
        # ---- 跑图专用状态 ----
        self.auto = False            # True = 不停顿（--auto）；False = 每步停下等回车
        self.code = None             # 扫码读到的任务码（如 "123+321"）

    # ---------------- 初始化：底盘 + base + （作业时）夹爪 ----------------
    def setup(self, chassis_vel=8, chassis_acc=6, chassis_dec=3, base90=6400,
              ttl_vel=40, grip_close_angle=None, grip_open_angle=None, verbose=True):
        """连车后第一件事：把底盘档位 + base 90° 脉冲（+ 可选夹爪角度）下发 STM32（0x20）。

        ★2026-09-18 加夹爪角度：跑图开始做取料了，而"**瞄准前要把夹爪张到初始开度**"
          （车主："在瞄准物料的过程中夹爪要再次打开成初始化大小"）靠的是固件里的
          `SERVO_GRIP_OPEN` —— 不把参数区那个值下发下去，固件就用自己的默认值，
          可能和参数区不一致（爪子张不够 → 爪片还是挡镜头 → 瞄准还是不准）。
        """
        vals = {"chassis_vel": chassis_vel, "chassis_acc": chassis_acc,
                "chassis_dec": chassis_dec, "ttl_default_vel": ttl_vel,
                "base_left90_pulses": base90, "base_right90_pulses": base90}
        if grip_close_angle is not None:
            vals["grip_close_angle"] = int(grip_close_angle)
        if grip_open_angle is not None:
            vals["grip_open_angle"] = int(grip_open_angle)
        ok, fail = True, []
        for name, v in vals.items():
            pid = LA.P.get(name)
            if pid is None:
                continue
            if not self.lower.set_param(pid, v):
                ok, fail = False, fail + ["发送失败:" + name]
        for name in LA.CHECKED:                       # 读回校验（只校验底盘那几项）
            pid = LA.P.get(name)
            if pid is None or name not in vals:
                continue
            got = self.lower.get_param(pid)
            if got is not None and int(got) != int(vals[name]):
                ok = False
                fail.append("%s 读回 %s ≠ 下发 %s" % (name, got, vals[name]))
        if verbose:
            self.log("[setup] 底盘/base 参数已下发 %d 项；校验%s%s"
                     % (len(vals), "通过 ✓" if ok else "不通过 ✗",
                        ("：" + "; ".join(fail)) if fail else ""))
        return ok

    # ---------------- 底盘动作 ----------------
    def fwd(self, cm):
        self.log("  → 前进 %.1fcm" % cm)
        self.lower.move("fwd", cm)

    def back(self, cm):
        self.log("  → 后退 %.1fcm" % cm)
        self.lower.move("back", cm)

    def left(self, cm):
        self.log("  → 左移 %.1fcm" % cm)
        self.lower.move("left", cm)

    def right(self, cm):
        self.log("  → 右移 %.1fcm" % cm)
        self.lower.move("right", cm)

    def spin(self, deg, ccw=False):
        self.log("  → 原地%s自旋 %.1f°" % ("逆" if ccw else "顺", deg))
        self.lower.spin("ccw" if ccw else "cw", deg)

    def goto_cm(self, target_cm, tag=""):
        """按纵向坐标自动前进/后退（工位对齐）。"""
        d = float(target_cm) - self.cur_cm
        if abs(d) < 1.0:
            self.log("  %s已在位（%.1fcm）" % (tag, self.cur_cm))
            return
        (self.fwd if d > 0 else self.back)(abs(d))
        self.cur_cm = float(target_cm)

    def pause(self, msg="", auto=None):
        """分段调试用：停下等回车。

        `--auto`（`self.auto=True`）时不停；否则打印提示并等回车。
        输入 **q** 或 stdin 结束 → 抛 KeyboardInterrupt，主程序会停车并干净退出。
        """
        auto = self.auto if auto is None else auto
        if msg:
            self.log("   【%s】" % msg)
        if auto:
            return True
        try:
            ans = input("   按回车继续（q=退出）> ").strip().lower()
        except EOFError:
            self.log("   （stdin 结束 → 退出）")
            raise KeyboardInterrupt
        if ans == "q":
            self.log("   收到 q → 退出")
            raise KeyboardInterrupt
        return True

    # ---------------- base 轴（作业姿态；跑图要验证"车头/机械臂朝哪"）----------------
    def base_turn(self, pulses, cw=False, vel=40, tag=""):
        """base 轴转任意角度。

        · `cw=True`  → **顺时针**（固件 dir=1 = 反转；从上往下看车向右转）
        · `cw=False` → 逆时针（固件 dir=0 = 正转）
        ⚠️ 转 180° 时龙门架+机械臂会**扫过整片空间**，转之前先确认周围没人、没东西挡着。
        """
        d = BASE_REV if cw else BASE_FWD
        self.log("  → base %s转 %d 脉冲 @%dRPM%s"
                 % ("顺时" if cw else "逆时", int(pulses), int(vel),
                    ("（%s）" % tag) if tag else ""))
        self.lower.ttl(4, d, vel, int(pulses))
        self._wait_axis(int(pulses), vel)

    def base_cw(self, pulses, vel=40, tag=""):
        """base 顺时针转（扫码朝向、或反向作业姿态）。"""
        return self.base_turn(pulses, cw=True, vel=vel, tag=tag)

    def base_ccw(self, pulses, vel=40, tag=""):
        """base 逆时针转。"""
        return self.base_turn(pulses, cw=False, vel=vel, tag=tag)

    def base_left(self, pulses=6400, vel=40):
        """base 左转（= 逆时针）—— 进原料区/加工区后的作业姿态（车左侧朝向料区）。"""
        return self.base_turn(pulses, cw=False, vel=vel, tag="左转/作业姿态")

    def base_right(self, pulses=6400, vel=40):
        """base 右转（= 顺时针）—— 回正用。"""
        return self.base_turn(pulses, cw=True, vel=vel, tag="右转/回正")

    def _wait_axis(self, pulses, vel):
        """TTL 是非阻塞的，按“匀速段 + 加减速段”估时等它走完。

        ★2026-09-18：老公式只算匀速段（脉冲×60/(RPM×3200)），漏了驱动器的加减速段
          （固件 car_config.h TTLCTRL_ACC_DEFAULT=60，非 0 → 带加减速），速度调高后
          严重偏短 → 现场现象"伸缩台还没到位，龙门架和舵机动作就开始执行"。
          统一改走 Car.ttl_wait()（含加减速补偿，斜率见 school_config.TTL_ACC_RPM_PER_S），
          全工程只有一套估时规则。
        """
        self.ttl_wait(pulses, vel)

    # ---------------- 上电初始化（把机械臂从"摆放位"送到"起始位"）----------------
    def init_axes(self, steps, gid=7, timeout=60.0, verbose=True):
        """把上电初始化动作组**整组下发**给 STM32 执行（0x21 下发步骤 + 0x24 触发）。

        与 /root/VisualBot 用的是同一套动作（原来写死在固件 `TTLctrl_Init()` 里，
        2026-09-17 搬到上位机，由固件 `motion.c` 的动作组解释器逐条执行、每步等到位）。

        ⚠️ 初始化是**相对行程**（走固定脉冲数），只在机械臂处于【上电摆放位】时执行；
           臂已经在起始位时再跑一遍 = 继续往同方向走 → **顶死机械限位**。
           所以每次"STM32 刚上电"之后做一次，之后靠 `--no-init` 跳过。
        """
        steps = [s for s in (steps or []) if str(list(s)[0]).lower() != "break"]
        if not steps:
            return True
        if verbose:
            self.log("  → 上电初始化 %d 步（整组交下位机执行）：%s" % (len(steps), steps))
        return self.lower.run_steps(steps, gid=gid, timeout=timeout)

    # ---------------- 杂项 ----------------
    def show_qr(self, code):
        """把任务码打到车上显示屏（赛规要求显示）。"""
        try:
            self.lower.show_qr(code)
        except Exception as exc:                                     # noqa: BLE001
            self.log("  (上屏失败: %r)" % exc)

    def stop(self):
        self.log("  → 停车")
        self.lower.stop()

    def close(self):
        try:
            self.lower.close()
        except Exception:                                            # noqa: BLE001
            pass

    def seconds(self):
        return time.time() - self.t0
