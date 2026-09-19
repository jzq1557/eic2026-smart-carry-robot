#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pick_debug.py —— 原料区**取料专项调试器**（像 aim_and_place.py 的 --home/--grab 那套）
================================================================================
用途：把车留在原料区作业姿态，**反复单试**取料动作组，直到把"从地面夹取"的细节调好，
      然后把调好的动作组原样抄回 `school_main.py` 的 `PICK_STEPS`。

怎么进到这个姿态：
    cd /root/VisualBot_current/校赛
    python3 -u school_main.py --port /dev/ttyUSB0 --skip-scan --no-init --stop-at-material
    #  ↑ 跑到原料区（base 左转 90°）就停住，后面的流程不跑

然后：

    python3 -u pick_debug.py --port /dev/ttyUSB0 --color red

菜单（输入字母+回车）：

    h / home     归位：伸缩收回 + base 回正（观察位）
    l / left     进左侧作业姿态：base 左转 BASE_90
    a / aim      只瞄准（颜色法），打印收敛结果与偏移
    g / grab     完整取料一遍：瞄准 + 取料动作组（瞄准失败自动盲夹）
    b / blind    盲夹一遍：跳过瞄准，直接跑取料动作组
    s / step     单步跑动作组（每步按回车；s=跳过该步、r=重跑上一步、q=退出单步）
    r / run      整组跑动作组（不瞄准）
    1 2 3 …      只单独执行动作组里的第 N 步（改一步试一步最常用）
    p / params   显示参数 + 当前动作组 + **可直接粘回 school_main.py 的代码**
    e 2 3200 25  改第 2 步：脉冲 3200、速度 25（只给一个数就只改脉冲）
    d 3          删除第 3 步
    i 3 grip_close   在第 3 步之后插入一步
    n 5          车前进 5cm（换工位；-5 = 后退）
    w 300        单纯等 300ms
    x / stop     停车
    q / quit     退出

⚠️ 只动 base(4) / 伸缩(1) / 龙门(2) / 夹爪；不动底盘 —— 除了你显式敲 `n`。
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, "..")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import school_config as SC                                          # noqa: E402
import school_main as SM                                            # noqa: E402
from car import Car                                                 # noqa: E402

MENU = """
──────────────────────────────────────────────────────────────
 原料区取料调试器    颜色=%s    姿态：%s
 h 归位(收臂+base回正)   l 左转进作业姿态   a 只瞄准
 g 完整取料(瞄+动作组)   b 盲夹(不瞄)        r 整组跑动作组
 s 单步跑动作组          1..N 只跑第N步      p 看参数+动作组
 e N [脉冲] [速度] 改第N步   d N 删除第N步    i N 名称 插入一步
 n N 前进Ncm(负=后退)    w MS 等待           x 停车     q 退出
──────────────────────────────────────────────────────────────
"""


def build_car(a):
    """连车 + 装相机 + 注入与主程序完全一致的参数。"""
    car = Car(port=a.port, baud=a.baud, dry_run=a.dry_run)
    car.use_camera(cam_index=SM.CAM_INDEX, perf=SM.PERF, exposure=SM.CAM_EXPOSURE,
                   rotate=SM.CAM_ROTATE, width=SM.CAM_WIDTH)
    SC.K_PX2STEP_BASE = SM.K_BASE
    SC.K_PX2STEP_ARM = SM.K_ARM
    SC.CAM_GRIP_OFFSET_X = SM.CAM_GRIP_OFFSET_X
    SC.CAM_GRIP_OFFSET_Y = SM.CAM_GRIP_OFFSET_Y
    SC.AIM_TOL_PX = SM.AIM_TOL_PX
    SC.T_AIM = SM.AIM_TIMEOUT
    SC.AIM_MAX_ITER = SM.AIM_MAX_ITER
    SC.AIM_RETRY = SM.AIM_RETRY
    SC.AIM_PROBE_PULSES = SM.AIM_PROBE_PULSES
    SC.AIM_PROBE_TOTAL = SM.AIM_PROBE_TOTAL
    if getattr(car, "color_vision", None) is not None:
        car.color_vision.ema = SM.COLOR_EMA
        car.color_vision.jump_px = SM.COLOR_JUMP_PX
        car.color_vision.stale_max = SM.COLOR_STALE_MAX
        car.color_vision.min_area_frac = SM.COLOR_MIN_AREA_FRAC
        car.color_vision.max_area_frac = SM.COLOR_MAX_AREA_FRAC
        car.color_vision.circ_min = SM.COLOR_CIRC_MIN
    if getattr(car, "vision", None) is not None and hasattr(car.vision, "cfg"):
        car.vision.cfg["digit_every"] = SM.DIGIT_EVERY
    car.pick_steps = list(SM.PICK_STEPS)
    return car


def show_steps(steps, indent="   "):
    for i, st in enumerate(steps, 1):
        print("%s%2d. %s" % (indent, i, list(st)))


def as_code(steps, name="PICK_STEPS"):
    """生成可直接粘回 school_main.py 的代码块（把调试结果固化下来）。"""
    lines = ["%s = [" % name]
    for st in steps:
        st = list(st)
        args = ", ".join(repr(x) for x in st)
        if len(st) == 1:
            args += ","
        lines.append("    (%s)," % args)
    lines.append("]")
    return "\n".join(lines)


def show_all(car, steps, color):
    print("  关键参数：TELESCOPE=%s  LIFT_DOWN=%s  LIFT_UP=%s  BASE_90=%s"
          % (SM.TELESCOPE, SM.LIFT_DOWN, SM.LIFT_UP, SM.BASE_90))
    print("  速度：ARM_OUT_VEL=%s  ARM_IN_VEL=%s  LIFT_DOWN_VEL=%s  LIFT_UP_VEL=%s  TTL_VEL=%s"
          % (SM.ARM_OUT_VEL, SM.ARM_IN_VEL, SM.LIFT_DOWN_VEL, SM.LIFT_UP_VEL, SM.TTL_VEL))
    print("  瞄准：K_BASE=%.4f  K_ARM=%.4f  容差=%.1fpx  颜色=%s"
          % (SM.K_BASE, SM.K_ARM, SM.AIM_TOL_PX, color))
    print("  取料动作组（%d 步）：" % len(steps))
    show_steps(steps)
    print("  ── 调好之后把下面这段直接替换回 school_main.py 的 PICK_STEPS ──")
    print(as_code(steps))


def step_run_interactive(car, steps):
    """单步跑：回车执行、s 跳过、r 重跑上一步、q 退出。"""
    print("  单步模式：回车=执行该步，s=跳过，r=重跑上一步，q=退出单步")
    i = 0
    while 0 <= i < len(steps):
        st = steps[i]
        try:
            cmd = input("  [%d/%d] %s > " % (i + 1, len(steps), list(st))).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if cmd == "q":
            return
        if cmd == "r":
            i = max(0, i - 1)
            continue
        if cmd == "s":
            print("    跳过")
            i += 1
            continue
        r = car.apply_step(st)
        print("    %s" % ("执行完成" if r else ("未执行（%r）" % r)))
        i += 1


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--color", default="red", help="瞄准目标颜色 red/green/blue")
    ap.add_argument("--dry-run", action="store_true", help="不连车、不动电机（只验证菜单/动作组）")
    return ap.parse_args()


def main():
    a = parse_args()
    car = build_car(a)
    steps = list(SM.PICK_STEPS)
    posture = "未知"
    print("=" * 66)
    print("原料区取料调试器 | 颜色=%s | 动作组 %d 步 | %s"
          % (a.color, len(steps), "dry-run（不动电机）" if a.dry_run else "真跑模式"))
    print("建议顺序：先 h 归位 → l 左转进作业姿态 → a 只瞄一次看能不能收敛 → 再 s 单步调动作组")
    print(MENU % (a.color, posture))
    try:
        while True:
            try:
                raw = input("pick> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not raw:
                continue
            p = raw.split()
            c = p[0].lower()
            try:
                if c in ("q", "quit", "exit"):
                    break
                elif c in ("h", "home"):
                    car.home(telescope=SM.TELESCOPE, base90=SM.BASE_90, speed=SM.TTL_VEL)
                    posture = "观察位(收臂+base回正)"
                elif c in ("l", "left"):
                    car.base_left(SM.BASE_90)
                    posture = "左侧作业姿态(base 左转)"
                elif c in ("a", "aim"):
                    ok, off = car.aim(mode="color", color=a.color, tag="DBG")
                    print("  ► 瞄准：收敛=%s  offset=%s" % (ok, off))
                elif c in ("g", "grab"):
                    ok = car.pick_here(expect_color=a.color, tag="DBG", steps=steps, blind=True)
                    print("  ► 完整取料：%s" % ("成功" if ok else "失败"))
                elif c in ("b", "blind"):
                    ok = car.pick_here(expect_color=a.color, tag="DBG", steps=steps,
                                       blind=True, aim=False)
                    print("  ► 盲夹：%s" % ("完成" if ok else "失败"))
                elif c in ("r", "run"):
                    n_ok, ok = car.run_steps_host(steps, stop_at_break=False)
                    print("  ► 整组跑完：成功 %d/%d 步" % (n_ok, len(steps)))
                elif c in ("s", "step"):
                    step_run_interactive(car, steps)
                elif c in ("p", "params"):
                    show_all(car, steps, a.color)
                elif c in ("n", "next"):
                    cm = float(p[1])
                    car.goto_cm(car.cur_cm + cm, tag="  ")
                elif c in ("e", "edit"):
                    i = int(p[1]) - 1
                    st = list(steps[i])
                    if len(p) > 2:
                        st[1] = int(p[2])
                    if len(p) > 3:
                        st[2] = int(p[3]) if len(st) > 2 else st[2]
                    steps[i] = tuple(st)
                    print("  已改第 %d 步 → %s" % (i + 1, list(steps[i])))
                elif c in ("d", "del"):
                    i = int(p[1]) - 1
                    print("  已删除：%s" % (steps.pop(i),))
                elif c in ("i", "insert"):
                    i = int(p[1])
                    name = p[2]
                    extra = tuple(int(x) for x in p[3:])
                    steps.insert(i, (name,) + extra)
                    print("  已插入：%s" % (list(steps[i]),))
                elif c in ("w", "wait"):
                    car.wait(int(p[1]))
                elif c in ("x", "stop"):
                    car.stop()
                    print("  已停车")
                elif c.isdigit():
                    i = int(c) - 1
                    print("  单独执行第 %d 步：%s" % (i + 1, list(steps[i])))
                    r = car.apply_step(steps[i])
                    print("    %s" % ("完成" if r else "未执行"))
                else:
                    print("  未知命令：%r（输 p 看菜单/参数）" % raw)
            except (IndexError, ValueError) as exc:
                print("  !! 参数不对：%r" % exc)
            except Exception as exc:                                 # noqa: BLE001
                print("  !! 命令出错：%r" % exc)
            print(MENU % (a.color, posture))
    finally:
        car.log("收工：打印最终动作组（可直接抄回 school_main.py）")
        print(as_code(steps))
        car.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
