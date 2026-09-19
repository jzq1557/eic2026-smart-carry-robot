#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
route_debug.py —— VisualBot2 走位标定助手
================================================================================
它不替你决定参数，只做三件事：**发一段 → 你量实际值 → 它算比例并给出该填多少**。

    cd /root/VisualBot2
    python3 -u route_debug.py --port /dev/stm32

菜单：
  m  里程标定：选一段（或自定义）→ 发车 → 你输入实测 cm   → 算出该段参数该填多少
  s  自旋标定：发 SPIN_DEG → 你输入实测角度               → 算出 SPIN_DEG 该填多少
  b  base 标定：发 BASE_90 脉冲 → 你输入机械臂实际转了多度 → 算出 BASE_90 该填多少
  g  手动发一段：方向(f/b/l/r) + 距离，或 自旋 + 角度
  p  显示当前参数（= route_main.py 里的值）
  h  急停
  q  退出

安全约定：每次真发动作前都会再问你一次"按回车发车"；发完会等它走完再让你量。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# ★★sys.path 顺序至关重要（2026-09-18 的大坑）：共享模块会把**老工程 `/root/VisualBot`**
#   也塞进 sys.path，而 `/root/VisualBot/校赛/` 下有**同名的旧版 aim.py / car.py / vision.py**。
#   原来的顺序插入（后插的排最前）会让 `/root/VisualBot` 反超 → `import aim` 命中旧版 →
#   三阶段瞄准 / 软化 / arm 专项阻尼 / 实测 K 全部静默失效。
#   → 倒序插入 + 先清掉重插，保证本项目自己的目录最终排最前。
for _p in ("/root/VisualBot", "/root/VisualBot2", HERE):
    if not _p or not os.path.isdir(_p):
        continue
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import route_main as RM                                          # noqa: E402
from route_car import RouteCar                                   # noqa: E402

# 可标定的里程段：参数名 → (方向, route_main 里的当前值取法, 中文名)
MILESTONES = [
    ("RIGHT_OUT",     "r", "① 出启动区（右移）"),
    ("TO_SCAN",       "f", "② 前进到扫码位"),
    ("TO_MATERIAL",   "f", "③ 前进进原料区"),
    ("TO_PROCESS",    "f", "④ 出原料区后前进"),
    ("ENTER_PROCESS", "f", "⑥ 自旋后进加工区"),
    ("TO_FINISH",     "f", "⑦ 出加工区前进"),
    ("FINISH_RIGHT",  "r", "⑧ 向右长距平移进结束位"),
]


def now_of(name):
    return float(getattr(RM, name))


def wait_enter(prompt="   按回车发车（q 取消）> "):
    try:
        return input(prompt).strip().lower() != "q"
    except EOFError:
        return False


def ask_float(prompt, default=None):
    try:
        s = input(prompt).strip()
    except EOFError:
        return default
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        print("   !! 不是数字：%r" % s)
        return default


def suggest(name, cur, target, measured):
    """比例法：需要的值 = 当前值 × 目标 / 实测。"""
    if not measured or measured <= 0:
        return None
    return cur * float(target) / float(measured)


def do_mileage(car):
    print("\n  可标定的段：")
    for i, (name, d, desc) in enumerate(MILESTONES, 1):
        print("    %d) %-16s %-5s 当前 %-6s %s" % (i, name, "前后" if d == "f" else "右移",
                                                  now_of(name), desc))
    print("    0) 自定义（只发一段，算比例用）")
    try:
        idx = int(input("  选哪一段 > ").strip() or "0")
    except (EOFError, ValueError):
        return
    if 1 <= idx <= len(MILESTONES):
        name, d, desc = MILESTONES[idx - 1]
        cur = now_of(name)
        target = cur                                  # 目标就是当前设的值
    else:
        name, d, desc, cur, target = "自定义", "f", "自定义段", None, None
        d = (input("   方向 f=前进 b=后退 l=左移 r=右移 > ").strip().lower() or "f")[:1]
        cur = ask_float("   要发多少（cm）> ", 50.0)
        target = cur

    print("   即将：%s %s %.1f cm（%s）" % (desc, {"f": "前进", "b": "后退", "l": "左移", "r": "右移"}[d],
                                          cur, name))
    if not wait_enter():
        return
    t0 = time.time()
    {"f": car.fwd, "b": car.back, "l": car.left, "r": car.right}[d](cur)
    print("   已发送（%.1fs）。现在用尺量：车实际移动了多少 cm？" % (time.time() - t0))
    measured = ask_float("   实测 cm > ")
    if measured is None:
        print("   没量就跳过（下次再标）")
        return
    need = suggest(name, cur, target, measured)
    print("   ── 结果 ──")
    print("     发了 %.1f cm，实测 %.1f cm → 比例 %.3f（实测/指令）"
          % (cur, measured, measured / cur if cur else 0))
    if name != "自定义" and need:
        print("     ✔ %s 建议改成： %.1f" % (name, need))
        print("       （route_main.py 参数区：%s = %.1f）" % (name, round(need, 1)))
    elif name == "自定义":
        print("     要让它走 %.1f cm，就发 %.1f" % (target, need))


def do_spin(car):
    cur = now_of("SPIN_DEG")
    print("\n  自旋标定：当前 SPIN_DEG = %s（顺时针）。" % cur)
    print("   请在车身上做个参照（贴张纸/对准地砖缝），跑完量它实际转了多少度。")
    if not wait_enter():
        return
    car.spin(cur)
    print("   已发送。量一下：车实际转了多少度？（顺时针为正）")
    measured = ask_float("   实测角度 > ")
    if measured is None:
        return
    need = suggest("SPIN_DEG", cur, cur, measured)
    print("   ── 结果 ──")
    print("     发了 %.1f°，实测 %.1f° → 比例 %.3f" % (cur, measured, measured / cur if cur else 0))
    if need:
        print("     ✔ SPIN_DEG 建议改成： %.1f" % need)
        print("       （route_main.py 参数区：SPIN_DEG = %.1f）" % round(need, 1))


def do_base(car):
    """base 标定 —— 支持任意目标角度：90°（作业姿态）/ **180°（扫码朝向）**。

    方向固定用**顺时针**（扫码要的就是顺时针半圈）；量出来一般是正的度数。
    """
    p90 = now_of("BASE_90")
    p180 = getattr(RM, "BASE_180", p90 * 2)
    print("\n  base 标定：当前 BASE_90 = %s 脉冲、BASE_180 = %s 脉冲。" % (p90, p180))
    print("   量法：车头方向与机械臂指向之间的夹角（或在底盘上贴两条线对准）。")
    target = ask_float("   这次要转多少度？（回车=90 作业姿态；扫码朝向填 180）> ", 90.0)
    if target is None or target <= 0:
        return
    pulses = p90 * float(target) / 90.0          # 按 90° 的比例折算该发多少脉冲
    print("   将发 %d 脉冲（按 %s 脉冲/%s° 折算）顺时针转 %.0f°"
          % (int(round(pulses)), p90, 90, target))
    print("   ⚠️ 转之前确认龙门架+机械臂扫过的空间是空的、没人。")
    if not wait_enter():
        return
    car.base_cw(int(round(pulses)), RM.TTL_VEL, tag="标定")
    print("   已发送。量一下：机械臂实际转了多少度？")
    measured = ask_float("   实测角度 > ")
    print("   ── 结果 ──")
    if measured is None:
        print("     没量就跳过；现在把 base 转回原位")
        if wait_enter("   按回车转回原位 > "):
            car.base_ccw(int(round(pulses)), RM.TTL_VEL, tag="回正")
        return
    scale = measured / float(target) if target else 0
    print("     发了 %.0f°（%d 脉冲），实测 %.1f° → 比例 %.3f"
          % (target, int(round(pulses)), measured, scale))
    if abs(target - 90.0) < 1.0:
        need = p90 * 90.0 / measured if measured else None
        if need:
            print("     ✔ BASE_90 建议改成： %.0f     （route_main.py：BASE_90 = %d）"
                  % (need, int(round(need))))
    else:
        need = pulses * float(target) / measured if measured else None
        key = "BASE_180" if abs(target - 180.0) < 1.0 else "BASE_%d" % int(round(target))
        if need:
            print("     ✔ %s 建议改成： %.0f   （route_main.py：%s = %d）"
                  % (key, need, key, int(round(need))))
    if wait_enter("   按回车把 base 转回原位 > "):
        car.base_ccw(int(round(pulses)), RM.TTL_VEL, tag="回正")


def do_manual(car):
    d = (input("   方向 f/b/l/r（或 s=自旋）> ").strip().lower() or "f")[:1]
    if d == "s":
        deg = ask_float("   自旋角度（正=顺时针）> ", 90.0)
        if wait_enter():
            car.spin(deg)
        return
    cm = ask_float("   距离 cm > ", 20.0)
    if wait_enter():
        {"f": car.fwd, "b": car.back, "l": car.left, "r": car.right}[d](cm)


def show_params():
    print("\n  当前参数（route_main.py 参数区）：")
    for k in ("CHASSIS_VEL", "CHASSIS_ACC", "CHASSIS_DEC", "BASE_90", "TTL_VEL",
              "RIGHT_OUT", "TO_SCAN", "TO_MATERIAL", "TO_PROCESS", "SPIN_DEG",
              "ENTER_PROCESS", "TO_FINISH", "FINISH_RIGHT",
              "CAM_INDEX", "CAM_ROTATE", "CAM_EXPOSURE", "MONITOR_SCAN_CAM"):
        print("    %-16s = %s" % (k, getattr(RM, k, "?")))


MENU = """
──────────────────────────────────────────────────────────────
 VisualBot2 走位标定助手
   m 里程标定（选段→发车→你量 cm→给建议值）
   s 自旋标定（发 SPIN_DEG→你量角度→给建议值）
   b base 标定（90°作业姿态 / 180°扫码朝向：可输入目标角度）
   g 手动发一段      p 看参数      h 急停      q 退出
──────────────────────────────────────────────────────────────"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/stm32")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dry-run", action="store_true", help="不连车（只走菜单与计算逻辑）")
    a = ap.parse_args()

    car = RouteCar(port=a.port, baud=a.baud, dry_run=a.dry_run)
    print("=" * 66)
    print("VisualBot2 走位标定助手 | %s" % ("dry-run（不动电机）" if a.dry_run else "真跑模式"))
    if not a.dry_run:
        car.setup(chassis_vel=RM.CHASSIS_VEL, chassis_acc=RM.CHASSIS_ACC,
                  chassis_dec=RM.CHASSIS_DEC, base90=RM.BASE_90, ttl_vel=RM.TTL_VEL)
    print(MENU)
    try:
        while True:
            try:
                c = (input("route> ").strip().lower() or "")[:1]
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if c in ("q", "quit"):
                break
            elif c == "m":
                do_mileage(car)
            elif c == "s":
                do_spin(car)
            elif c == "b":
                do_base(car)
            elif c == "g":
                do_manual(car)
            elif c == "p":
                show_params()
            elif c == "h":
                car.stop()
                print("   已急停")
            else:
                print("   未知命令（m/s/b/g/p/h/q）")
            print(MENU)
    finally:
        car.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
