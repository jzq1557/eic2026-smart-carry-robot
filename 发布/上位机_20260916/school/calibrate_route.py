#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibrate_route.py —— 底盘「里程 / 自旋」标定助手（现场用）
================================================================================
为什么需要它：现场标定最费时间的就是"发 60cm、实际只走了 48cm，到底该改成多少"。
本工具把这件事变成交互循环：**发一段 → 你拿尺子量 → 输入实测值 → 它算比例并给建议值**，
反复几轮就收敛；最后打印一段**可直接粘进 `school_main.py` 参数区**的代码。

用法（RDK 上，接着下位机 USB-TTL）：
    python3 -u calibrate_route.py --port /dev/ttyUSB0
    python3 -u calibrate_route.py --port /dev/ttyUSB0 --dir fwd --target 60 --rounds 3
    python3 -u calibrate_route.py --dry-run          # 不连车，只看流程/算法

⚠️ 安全：第一次务必**架空车轮**或把车放在长直道上、正前方无人无障碍；
   每次发车前工具会提示，按回车才动。（自旋标定请把车放平、周围留出旋转半径。）
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import school_config as SC                                     # noqa: E402
import lower_api as LA                                         # noqa: E402

# 可以标定的动作（与 car.py 的底盘函数一一对应）
KINDS = [("fwd", "前进", "RIGHT_OUT / TO_SCAN / TO_MATERIAL / TO_PROCESS / ENTER_PROCESS / TO_FINISH"),
         ("back", "后退", "（取料顺序倒着走时会用到）"),
         ("left", "左移", "（进料区贴边时用）"),
         ("right", "右移", "RIGHT_OUT / FINISH_RIGHT"),
         ("spin", "原地自旋", "SPIN_DEG")]

# school_main.py 里与各动作相关的参数名（打印建议时提示改哪个）
PARAM_HINT = {
    "fwd": ["TO_SCAN", "TO_MATERIAL", "TO_PROCESS", "ENTER_PROCESS", "TO_FINISH"],
    "back": [],
    "left": [],
    "right": ["RIGHT_OUT", "FINISH_RIGHT"],
    "spin": ["SPIN_DEG"],
}


class RouteCal:
    def __init__(self, lower, dry=False, log=print):
        self.l = lower
        self.dry = dry
        self.log = log
        self.samples = {}          # kind -> [(target, measured, ratio), ...]

    # ---- 单次测量 ----
    def fire(self, kind, target):
        unit = "度" if kind == "spin" else "cm"
        self.log("\n>>> 即将%s %.1f%s（确认周围安全后回车，输入 q 跳过）"
                 % (dict((k, n) for k, n, _ in KINDS).get(kind, kind), target, unit))
        if input("    回车发车 > ").strip().lower() == "q":
            return None
        t0 = time.time()
        if kind == "spin":
            self.l.spin("cw", target)
        else:
            self.l.move(kind, target)
        self.log("    ...已发出（%.1fs）。现在拿尺子量：实际走了多少%s？" % (time.time() - t0, unit))
        s = input("    实测值（留空=跳过，q=退出）> ").strip()
        if not s or s.lower() == "q":
            return None
        try:
            return float(s)
        except ValueError:
            self.log("    !! 不是数字，跳过这次")
            return None

    def record(self, kind, target, measured):
        if measured <= 0.01:
            return None
        ratio = target / measured            # 实际走少了 → ratio>1 → 建议值要放大
        self.samples.setdefault(kind, []).append((target, measured, ratio))
        self.log("    设定 %.1f → 实测 %.1f（%s %.1f%%）→ 比例 %.4f"
                 % (target, measured,
                    "走多了" if measured > target else "走少了",
                    (measured / target - 1) * 100, ratio))
        return ratio

    # ---- 建议值 ----
    def suggestion(self, kind, target):
        rows = self.samples.get(kind) or []
        if not rows:
            return target
        k = sum(r for _, _, r in rows) / len(rows)     # 平均比例
        return target * k

    def report(self):
        self.log("\n" + "=" * 70)
        self.log("标定结果汇总")
        self.log("=" * 70)
        for kind, name, _ in KINDS:
            rows = self.samples.get(kind) or []
            if not rows:
                continue
            k = sum(r for _, _, r in rows) / len(rows)
            self.log("  %-6s 共 %d 次：平均比例 %.4f（%.1f%%）"
                     % (name, len(rows), k, (k - 1) * 100))
            for t, m, r in rows:
                self.log("        设定 %6.1f → 实测 %6.1f  (比例 %.4f)" % (t, m, r))
        # 给出 school_main.py 的建议参数
        self.log("\n建议这样改 school_main.py 参数区（按上面比例乘一下）：")
        hint = {"fwd": ["TO_SCAN", "TO_MATERIAL", "TO_PROCESS", "ENTER_PROCESS", "TO_FINISH"],
                "right": ["RIGHT_OUT", "FINISH_RIGHT"],
                "spin": ["SPIN_DEG"]}
        import re
        try:
            src = open(os.path.join(HERE, "school_main.py"), encoding="utf-8").read()
        except Exception:
            src = ""
        for kind, names in hint.items():
            rows = self.samples.get(kind) or []
            if not rows or not src:
                continue
            k = sum(r for _, _, r in rows) / len(rows)
            for nm in names:
                m = re.search(r"^%s\s*=\s*([\d.]+)" % nm, src, re.M)
                if not m:
                    continue
                old = float(m.group(1))
                self.log("    %-14s = %-8s  →  %s = %.1f"
                         % (nm, m.group(1), nm, old * k))
        self.log("  （取整到 1cm/1° 即可；自旋比例若接近 1 就别动）")
        self.log("=" * 70)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dry-run", action="store_true", help="不连车，只走流程（用假实测值）")
    ap.add_argument("--dir", default=None, choices=[k for k, _, _ in KINDS],
                    help="只标一个动作（不给就交互选）")
    ap.add_argument("--target", type=float, default=None, help="每次发的量（cm / 度）")
    ap.add_argument("--rounds", type=int, default=3, help="每个动作测几轮")
    a = ap.parse_args()

    if a.dry_run:
        lower = LA.MockLower(verbose=False)
    else:
        lower = LA.SerialLower(a.port, a.baud, verbose=False)
        if not lower.set_param(LA.P["chassis_vel"], 8):
            print("[warn] 参数下发没回执，先确认下位机在线/固件是最新版")

    cal = RouteCal(lower, dry=a.dry_run, log=print)
    print("=" * 70)
    print("底盘里程标定助手 —— 建议顺序：先 前进 → 再 右移 → 最后 自旋")
    print("  标定原理：设定值 × (设定/实测) = 新的设定值；测 2~3 轮取平均")
    print("  注意：固件里 STEPS_PER_CM / STEPS_PER_DEGREE 属于硬件换算，优先在上位机改里程，")
    print("        只有比例偏离 1 太多（比如 0.6 或 1.5）才去改固件常量并重烧。")
    print("=" * 70)

    if a.dir:
        kinds = [k for k in KINDS if k[0] == a.dir]
    else:
        print("可选动作：")
        for i, (k, n, use) in enumerate(KINDS, 1):
            print("  %d) %-4s %s" % (i, k, n))
        pick = input("选一个（回车=全部按顺序）> ").strip()
        kinds = KINDS if not pick else [KINDS[int(pick) - 1]]

    for kind, name, _ in kinds:
        default = 60.0 if kind != "spin" else 90.0
        target = a.target or default
        if a.dir is None and not a.target:
            s = input("\n%s：每次发多少（%s，回车=%.0f）> "
                      % (name, "度" if kind == "spin" else "cm", default)).strip()
            if s:
                try:
                    target = float(s)
                except ValueError:
                    pass
        for r in range(a.rounds):
            if a.dry_run:                       # 干跑：模拟"实际走了 85%"
                measured = target * 0.85
                print("    [dry-run] 模拟实测 = %.1f" % measured)
            else:
                measured = cal.fire(kind, target)
            if measured is None:
                break
            cal.record(kind, target, measured)
            target = round(cal.suggestion(kind, target), 1)     # 用建议值再测一轮
        if a.dir or len(kinds) == 1:
            break

    cal.report()
    print("\n下一步：把上面建议值填进 school_main.py 的参数区，然后 `--dry-run` 跑一遍确认流程。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
