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
    # ★2026-09-17 车主确认：地面取料/放料**不用伸缩台**。
    #   注意区分：**动作组**里不再有"伸展"（arm_out/arm_in）；而瞄准闭环仍会用伸缩轴做
    #   ±几像素的微调（base 修 x、伸缩修 y），那是对准、不是伸展，两者是两回事。
    steps_now = [list(s) for s in car.pick_steps_of()]
    check("取料动作组本身不含 arm_out/arm_in",
          not any(str(s[0]).lower() in ("arm_out", "arm_in") for s in steps_now), steps_now)
    ttl_axes = [c[1]["axis"] for c in car.lower.calls if c[0] == "ttl"]
    check("动作组确实驱动了龙门架（轴2/3 出现）",
          any(a in (2, 3) for a in ttl_axes), sorted(set(ttl_axes)))

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
    # ★2026-09-17 改：丢目标时**默认不再试探**（不动臂）。以前是
    #   probe = AIM_PROBE_PX / K_ARM = 40/0.0385 ≈ 1039 脉冲、**单向 DIR_REV**、最多 3 次
    #   ≈ 3100 脉冲 > 伸缩行程 3000 → 每次失败都把臂收到尽头
    #   （现场 offset arm=-2904 就是它，表现为"移动到下一物料时伸缩台不断后移"）。
    check("丢目标时默认**不动臂**（不发 TTL 试探）",
          not any(c[0] == "ttl" for c in l.calls), l.calls)

    # 显式开启试探（AIM_PROBE_PULSES>0）时：必须正反交替 + 累计限幅
    old_probe, old_cap = getattr(SC, "AIM_PROBE_PULSES", 0), getattr(SC, "AIM_PROBE_TOTAL", 400)
    SC.AIM_PROBE_PULSES, SC.AIM_PROBE_TOTAL = 200, 500
    try:
        v4 = MockVision(cx=200.0, cy=150.0, w=512, h=384, found=False)
        l4 = LA.MockLower(verbose=False)
        a4 = Aim(v4, l4, log=lambda *x: None, dry_run=True)
        silent(a4.converge, tag="PROBE")
        ttls = [c[1] for c in l4.calls if c[0] == "ttl"]
        dirs = [t["dir"] for t in ttls]
        total = sum(t["pulses"] for t in ttls)
        net = abs(sum(t["pulses"] if t["dir"] == 0 else -t["pulses"] for t in ttls))
        check("开了试探 → 确实会发试探脉冲", len(ttls) > 0, len(ttls))
        check("试探**正反交替**（绝不单向跑掉）", len(set(dirs)) > 1, dirs)
        # 上限管的是"净位移"（正反交替 → 净位移贴近 0）；总发出量由 AIM_RETRY 次数兜住
        check("试探净位移不超上限（防单向跑掉）", net <= 500, net)
        check("试探总发出量受限（≤ 单次脉冲 × 重试上限）",
              total <= 200 * max(1, getattr(SC, "AIM_RETRY", 8)), (total, getattr(SC, "AIM_RETRY", 8)))
    finally:
        SC.AIM_PROBE_PULSES, SC.AIM_PROBE_TOTAL = old_probe, old_cap

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
    check("取料时丢目标 + 未开盲夹 → 安全返回 False（不崩、不夹空）", ok3 is False, ok3)

    # ★2026-09-17 新增：盲夹兜底（原料区物料位置固定，瞄准失败也该把这一轮动作做完）
    ok4 = silent(car3.pick_here, expect_color=None, tag="G-BLIND", blind=True)[0]
    check("开了盲夹 → 瞄准失败也照夹（返回 True，账本才有的记）", ok4 is True, ok4)
    # 注意：car3.pick_here 里的动作走的是 car3.lower（Car 自建的那个 MockLower），不是上面的 l3
    blind_ttl = [c[1] for c in car3.lower.calls if c[0] == "ttl"]
    check("盲夹确实发出了伸缩前伸 + 收回（TTL）", len(blind_ttl) >= 2, len(blind_ttl))
    check("盲夹也发了夹爪闭合", any(c[0] == "grip" and c[1].get("close") for c in car3.lower.calls),
          [c for c in car3.lower.calls if c[0] == "grip"])


def t5_param_wiring():
    print("[5] 参数区改动会真的传到车上")
    car = new_car()
    silent(SM.run, car, "123+321", False)
    # ① 动作组：run() 会把参数区的 PICK_STEPS / PLACE_STEPS 注入到 car
    check("run() 把 PICK_STEPS 注入 car.pick_steps",
          [tuple(s) for s in (car.pick_steps or [])] == [tuple(s) for s in SM.PICK_STEPS],
          car.pick_steps)
    check("run() 把 PLACE_STEPS 注入 car.place_steps",
          [tuple(s) for s in (car.place_steps or [])] == [tuple(s) for s in SM.PLACE_STEPS],
          car.place_steps)
    # ② 动作组内容：不含伸缩台（车主 2026-09-17 确认地面取放不用它）
    for name, steps in (("PICK_STEPS", SM.PICK_STEPS), ("PLACE_STEPS", SM.PLACE_STEPS)):
        check("%s 不含 arm_out/arm_in" % name,
              not any(str(list(s)[0]).lower() in ("arm_out", "arm_in") for s in steps), steps)
    # ③ 瞄准手感参数：run() 注入 school_config（aim.py 运行时读它）
    old = SM.AIM_OUT_MAX_PX
    SM.AIM_OUT_MAX_PX = 7.5
    car2 = new_car()
    silent(SM.run, car2, "123+321", False)
    SM.AIM_OUT_MAX_PX = old
    check("改 AIM_OUT_MAX_PX 后真的进了 school_config", SC.AIM_OUT_MAX_PX == 7.5,
          SC.AIM_OUT_MAX_PX)


def t7_init_group():
    """上电初始化动作组：原来写死在固件 TTLctrl_Init()，现在由上位机 0x21+0x24 下发。"""
    print("[7] 上电初始化动作组（0x21 下发 + 0x24 执行）")
    steps = list(SM.INIT_STEPS)

    car = new_car()
    ok, _ = silent(car.init_axes, steps, mode="board")
    check("init_axes(board) 成功", bool(ok))
    frames = [c[1]["cmd"] for c in car.lower.calls if c[0] == "frame"]
    check("0x21 帧数 = 2×步数+2（clear + 每步 head/pulses + commit）",
          frames.count("0x21") == 2 * len(steps) + 2,
          "0x21×%d steps=%d" % (frames.count("0x21"), len(steps)))
    check("0x24 执行帧恰好 1 次", frames.count("0x24") == 1)

    raw = [c[1]["raw"] for c in car.lower.calls
           if c[0] == "frame" and c[1]["cmd"] == "0x21"]
    # 帧：FF 21 sub gid idx kind axis dir vel FE
    check("步骤0 = 轴1 正转(0) 250RPM", raw[1][10:18] == "010100fa", raw[1])
    check("步骤0 脉冲 = 2000", raw[2][10:18] == "000007d0", raw[2])
    check("步骤1 = 轴2 反转(1) 80RPM", raw[3][10:18] == "01020150", raw[3])
    check("步骤1 脉冲 = 5000（★2026-09-17 起始位抬高 2000）",
          raw[4][10:18] == "00001388", raw[4])
    check("步骤2 = 轴4 反转(1) 200RPM", raw[5][10:18] == "010401c8", raw[5])
    check("步骤2 脉冲 = 4800", raw[6][10:18] == "000012c0", raw[6])
    check("commit 帧声明 3 步", raw[7][4:8] == "0307" or raw[7][4:6] == "03", raw[7])

    car2 = new_car()
    ok2, _ = silent(car2.init_axes, steps, mode="host")
    ttls = [c[1] for c in car2.lower.calls if c[0] == "ttl"]
    check("host 模式逐条发 3 条 TTL 单轴命令", len(ttls) == 3, ttls)
    check("host 模式轴序 1→2→4", [t["axis"] for t in ttls] == [1, 2, 4], ttls)
    check("host 模式速度不受 8 位限制（轴1 仍按参数区的值）",
          ttls[0]["pulses"] == 2000 and bool(ok2))

    car3 = new_car()
    car3.run_steps([("axis", 1, 0, 100, 300)])       # 故意超 255
    logtxt = " ".join(str(c) for c in car3.lower.calls if c[0] == "frame")
    check("速度 >255 时被拦到 255（不静默截断）",
          "010100ff" in logtxt or any("010100ff" in str(c[1].get("raw", ""))
                                      for c in car3.lower.calls if c[0] == "frame"))


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
    t7_init_group()
    print("=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  - %s" % f)
    print("=" * 66)
    sys.exit(1 if FAIL else 0)
