#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_school_main_local.py —— 流水账主程序(car.py + school_main.py)的纯逻辑回归
================================================================================
本机可跑（不需要相机/串口/cv2）：dry-run 用 MockLower + MockVision。

覆盖：
  1. Car.setup() 参数下发（13 项）+ 关键项读回校验
  2. 底盘动作（里程参数真的进了命令行）
  3. 机械臂动作（脉冲/RPM 参数化，且能等到位）
  4. 取料工序 pick_here（瞄准收敛 + 夹 + 收回）
  5. 放置工序 place_here（瞄 → 回观察位 → 0x16 夹料 → 回放 → 下放 → 松爪）
  6. **整场流水账 run()**：账本 3/3、放置顺序按后段、动作顺序与参数一致
"""
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import school_config as SC                                       # noqa: E402
import lower_api as LA                                          # noqa: E402
import school_main as SM                                        # noqa: E402
from car import Car                                             # noqa: E402
from aim import Aim                                             # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


def silent(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


def new_car():
    car = Car(dry_run=True, log=lambda *x: None)
    car.use_camera()
    return car


def t0_direction():
    """★方向符号回归：必须与固件 chassis.c 一致（2026-09-16 对照修正过，别再改回去）

    chassis.c:  Chassis_MoveY_cm:  cm>=0 → 左移,  cm<0 → 右移
                Chassis_Rotate_deg: deg>=0 → 逆时针(CCW), deg<0 → 顺时针(CW)
    """
    print("[0] ★方向符号（对齐固件 chassis.c：负=右移 / 正=逆时针）")
    # 帧布局：FF cmd b1 b2 b3 b4 b5 b6 b7 FE → X 在 f[2]、Y 在 f[3]、旋转在 f[4]
    f = LA.pack_move_y_cm(LA._RIGHT_SIGN * 30)
    check("右移发负值 Y_cm=-30 (=0xE2)", f[3] == 0xE2, f.hex())
    f = LA.pack_move_y_cm(-LA._RIGHT_SIGN * 30)
    check("左移发正值 Y_cm=+30 (=0x1E)", f[3] == 0x1E, f.hex())
    check("顺时针(cw)发负值 deg=-90 (=0xA6)",
          LA.pack_rotate_deg(LA._SPIN_SIGN["cw"] * 90)[4] == 0xA6,
          LA.pack_rotate_deg(LA._SPIN_SIGN["cw"] * 90).hex())
    check("逆时针(ccw)发正值 deg=+90 (=0x5A)",
          LA.pack_rotate_deg(LA._SPIN_SIGN["ccw"] * 90)[4] == 0x5A,
          LA.pack_rotate_deg(LA._SPIN_SIGN["ccw"] * 90).hex())
    check("前进发正值 X_cm=+30", LA.pack_move_x_cm(30)[2] == 0x1E)
    check("后退发负值 X_cm=-30", LA.pack_move_x_cm(-30)[2] == 0xE2)

    car = new_car()
    car.right(30); car.left(20); car.spin(90)
    moves = [(c[1]["dir"], c[1]["cm"]) for c in car.lower.calls if c[0] == "move"]
    check("Car.right/left 语义正确", moves == [("right", 30), ("left", 20)], moves)
    check("Car.spin(90) 默认走 cw（顺时针）",
          any(c[0] == "spin" and c[1]["dir"] == "cw" for c in car.lower.calls))
    check("出启动区用的是 right（不是 left）", moves[0][0] == "right", moves[:1])


def t1_setup():
    print("[1] 参数下发（Car.setup）")
    car = new_car()
    ok = car.setup(chassis_vel=SM.CHASSIS_VEL, chassis_acc=SM.CHASSIS_ACC,
                   chassis_dec=SM.CHASSIS_DEC, grip_close_angle=SM.GRIP_CLOSE_ANGLE,
                   grip_open_angle=SM.GRIP_OPEN_ANGLE, base90=SM.BASE_90,
                   lift_up=SM.LIFT_UP, lift_down=SM.LIFT_DOWN,
                   telescope=SM.TELESCOPE, ttl_vel=SM.TTL_VEL)
    check("setup 返回 True", ok is True)
    check("下发了 13 项参数", len(car.lower.params) == 13, len(car.lower.params))
    check("chassis_vel 落地", car.lower.params.get(LA.P["chassis_vel"]) == SM.CHASSIS_VEL)
    check("base_left90 落地", car.lower.params.get(LA.P["base_left90_pulses"]) == SM.BASE_90)
    check("★夹爪下发的是角度(82°)不是脉冲",
          car.lower.params.get(LA.P["grip_close_angle"]) == SM.GRIP_CLOSE_ANGLE == 82,
          car.lower.params.get(LA.P["grip_close_angle"]))
    check("读回校验通过（无失败项）", car.lower.get_param(LA.P["chassis_vel"]) == SM.CHASSIS_VEL)


def t2_moves():
    print("[2] 底盘与机械臂动作（参数化）")
    car = new_car()
    car.fwd(80); car.back(20); car.left(10); car.right(30); car.spin(90)
    moves = [(c[1]["dir"], c[1]["cm"]) for c in car.lower.calls if c[0] == "move"]
    check("底盘动作序列正确", moves == [("fwd", 80), ("back", 20), ("left", 10), ("right", 30)], moves)
    check("自旋 90° 有记录", any(c[0] == "spin" and c[1]["deg"] == 90 for c in car.lower.calls))

    car.arm_out(3000, 30); car.arm_in(3000, 40)
    ttl = [(c[1]["axis"], c[1]["dir"], c[1]["vel"], c[1]["pulses"]) for c in car.lower.calls if c[0] == "ttl"]
    check("伸缩前伸=轴1正转3000@30", (1, 0, 30, 3000) in ttl, ttl)
    check("伸缩收回=轴1反转3000@40", (1, 1, 40, 3000) in ttl)
    car.base_left(6400, 40)
    check("base 左转=轴4正转", (4, 0, 40, 6400) in
          [(c[1]["axis"], c[1]["dir"], c[1]["vel"], c[1]["pulses"]) for c in car.lower.calls if c[0] == "ttl"])
    car.goto_cm(70.0)                      # 从 0 前进到 70
    check("goto_cm 走的是前进 70", car.lower.calls[-1][1]["dir"] == "fwd" and car.lower.calls[-1][1]["cm"] == 70,
          car.lower.calls[-1])
    car.goto_cm(35.0)                      # 回退到 35
    check("goto_cm 自动后退", car.lower.calls[-1][1]["dir"] == "back" and car.lower.calls[-1][1]["cm"] == 35)


def t3_pick_place():
    print("[3] 取料 / 放置工序（★夹爪必须走舵机 0x14/0x15）")
    car = new_car()
    ok = car.pick_here(expect_color="red", tag="G1", out_pulses=3000, out_vel=30, in_vel=40)
    check("pick_here 成功", ok is True)
    check("pick_here 里出现过前伸+收回",
          len([c for c in car.lower.calls if c[0] == "ttl" and c[1]["axis"] == 1]) >= 2)

    # ★夹爪：只能走 grip（舵机 0x14），绝不能变成 TTL 轴 1 的脉冲
    grips = [c[1] for c in car.lower.calls if c[0] == "grip"]
    check("夹爪走的是舵机 grip()，不是 TTL 轴", len(grips) >= 1, grips)
    check("默认用 0x14 开/闭（不给角度）", all(g.get("angle") is None for g in grips), grips)
    check("TTL 里没有出现\"夹爪脉冲\"（1700/1200 这类）",
          all(c[1]["pulses"] not in (1700, 1200) for c in car.lower.calls if c[0] == "ttl"))

    car.grip_angle(0)
    g = [c[1] for c in car.lower.calls if c[0] == "grip"][-1]
    check("grip_angle(0) 走 0x15 舵机角度", g.get("angle") == 0, g)

    ok = car.place_here(slot=2, tag="P1", out_pulses=3000, out_vel=30, in_vel=40)
    check("place_here 成功", ok is True)
    grp = [c[1]["name"] for c in car.lower.calls if c[0] == "run_group"]
    check("place_here 调了 0x16 去物架夹料", grp.count("BUILTIN_GRAB_VEH") == 1, grp)
    slots = [c[1]["slot"] for c in car.lower.calls
             if c[0] == "run_group" and c[1]["name"] == "BUILTIN_GRAB_VEH"]
    check("夹料用的是指定 slot=2", slots == [2], slots)


def t4_full_flow():
    print("[4] 整场流水账 run()")
    car = new_car()
    placed, out = silent(SM.run, car, "123+321", False)
    check("放置数 = 3", placed == 3, placed)
    check("三个物料全部上车且 slot 正确", car.ledger.map == {"1": 1, "2": 2, "3": 3}, car.ledger.map)
    check("放置顺序按后段 321", car.ledger.done == ["3", "2", "1"], car.ledger.done)

    moves = [(c[1]["dir"], c[1]["cm"]) for c in car.lower.calls if c[0] == "move"]
    check("①右移出启动区 %.0fcm" % SM.RIGHT_OUT, ("right", SM.RIGHT_OUT) in moves, moves[:3])
    check("②前进到扫码位", ("fwd", SM.TO_SCAN) in moves)
    check("③前进进原料区", ("fwd", SM.TO_MATERIAL) in moves)
    check("④转场前进 + 进加工区", ("fwd", SM.TO_PROCESS) in moves and ("fwd", SM.ENTER_PROCESS) in moves)
    check("⑤自旋 %.0f°" % SM.SPIN_DEG, any(c[0] == "spin" and c[1]["deg"] == SM.SPIN_DEG for c in car.lower.calls))
    check("⑥收尾右移 %.0fcm" % SM.FINISH_RIGHT, ("right", SM.FINISH_RIGHT) in moves)
    check("⑦停车被调用", any(c[0] == "stop" for c in car.lower.calls))

    car_log = Car(dry_run=True)              # 这一个要留日志：验证六次瞄准的痕迹
    car_log.use_camera()
    _, out2 = silent(SM.run, car_log, "123+321", False)
    check("六次瞄准的痕迹都在日志里（G1~G3 + P1~P3）",
          all(k in out2 for k in ("G1", "G2", "G3", "P3→1", "P2→2", "P1→3")), out2[:0])
    check("两次 base 左转（原料区 + 加工区）",
          len([c for c in car.lower.calls if c[0] == "ttl" and c[1]["axis"] == 4 and c[1]["dir"] == 0]) >= 2)
    check("三次去物架夹料(0x16)",
          [c[1]["name"] for c in car.lower.calls if c[0] == "run_group"].count("BUILTIN_GRAB_VEH") == 3)

    # 换一个码：放置顺序 ≠ 位置顺序，也要能跑（先跑最远再退回）
    car2 = new_car()
    placed2, _ = silent(SM.run, car2, "123+123", False)
    check("码 123+123 也能跑完 3 个", placed2 == 3, placed2)
    check("其放置顺序为 1→2→3", car2.ledger.done == ["1", "2", "3"], car2.ledger.done)


def t6_lost_target():
    """★丢目标分支（2026-09-16 现场在这里崩过：`_apply` 少传两个参数）

    为什么以前没测到：dry-run 的仿真视觉一直 `found=True`，这条分支从没被走到。
    """
    print("[6] 丢目标容错（曾在此崩溃）")
    from vision import MockVision

    v = MockVision(cx=200.0, cy=150.0, w=512, h=384, found=False)     # 一直找不到目标
    l = LA.MockLower(verbose=False)
    a = Aim(v, l, log=lambda *x: None, dry_run=True)
    res, _ = silent(a.converge, tag="LOST")        # converge 返回 (ok, offset) 元组
    check("一直丢目标 → 不崩、返回 False", res[0] is False, res)
    check("丢目标时发出了试探脉冲（TTL）", any(c[0] == "ttl" for c in l.calls))

    v2 = MockVision(cx=300.0, cy=200.0, w=512, h=384)
    v2.lost_frames = 3                        # 前几帧丢，之后再出现
    l2 = LA.MockLower(verbose=False)
    a2 = Aim(v2, l2, log=lambda *x: None, dry_run=True)
    res2, _ = silent(a2.converge, tag="RECOVER")
    check("丢几帧后恢复 → 仍能收敛", res2[0] is True, res2)

    v3 = MockVision(cx=330.0, cy=200.0, w=512, h=384, found=False)
    l3 = LA.MockLower(verbose=False)
    car3 = Car(dry_run=True, log=lambda *x: None)
    car3.use_camera()
    car3.vision = v3
    car3._aim = Aim(v3, l3, log=lambda *x: None, dry_run=True)
    ok3 = silent(car3.pick_here, expect_color=None, tag="G-LOST")[0]
    check("取料时丢目标 → 安全返回 False（不崩、不夹空）", ok3 is False, ok3)


def t5_param_wiring():
    print("[5] 参数区改动会真的传到车上")
    old = SM.TELESCOPE
    SM.TELESCOPE = 1234                       # 临时改参数区
    car = new_car()
    silent(SM.run, car, "123+321", False)
    hit = any(c[0] == "ttl" and c[1]["pulses"] == 1234 for c in car.lower.calls)
    SM.TELESCOPE = old
    check("改 TELESCOPE 后脉冲变成 1234", hit)


if __name__ == "__main__":
    print("=" * 66)
    print("流水账主程序（car.py + school_main.py）纯逻辑回归")
    print("=" * 66)
    t0_direction()
    t1_setup()
    t2_moves()
    t3_pick_place()
    t4_full_flow()
    t5_param_wiring()
    t6_lost_target()
    print("=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  - %s" % f)
    print("=" * 66)
    sys.exit(1 if FAIL else 0)
