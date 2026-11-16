#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
drive_calib.py —— 平移比例标定（前进 / 后退 / 左移 / 右移）
================================================================================
它只做一件事：**发一段 → 你量实际走了多少 → 它算出比例，并告诉你"该改哪个数、改成多少"**。

## 为什么只测一个方向就够

固件 `chassis.c` 的四个方向**共用同一个 `STEPS_PER_CM`**：

```c
void Chassis_MoveX_cm(int8_t cm){ ... steps = |cm| * STEPS_PER_CM; ... }
void Chassis_MoveY_cm(int8_t cm){ ... steps = |cm| * STEPS_PER_CM; ... }
```

所以把**一个方向**（默认前进）测准，另外三个方向的比例也就定了。
若你怀疑某个方向机械效率不同（例如横移阻力更大），可以用 `--dir` 分别测，程序会**分开统计**。

## 用法

    cd /root/VisualBot3
    python3 -u drive_calib.py                          # 交互：前进 100cm × 3 轮
    python3 -u drive_calib.py --dist 100 --rounds 3    # 指定距离与轮数
    python3 -u drive_calib.py --dir right --dist 50    # 单独测右移
    python3 -u drive_calib.py --dir all --dist 100     # 四个方向各测一遍（对比机械效率）
    python3 -u drive_calib.py --dry-run                # 不连车，验证流程与公式

交互约定：
    每轮发车前问一次「回车发车」（输 q 中止）；发完问实测 cm。
    实测留空 = 跳过该轮；q = 结束统计、直接出结果。

## ⚠️ 改哪里（很重要）

测出来的比例偏差，**根因在固件**：

| 方案 | 位置 | 代价 | 评价 |
|---|---|---|---|
| **A（推荐）** | 固件 `26091601TEST/CLEAN/car_config.h` 的 `STEPS_PER_CM` | **必须 Rebuild + 重烧** | 最干净，语义正确 |
| B（备选） | 上位机预乘（`car.fwd(cm * K)`） | 免重烧 | ⚠️ **不推荐**：`0x01/0x02` 的距离字段是 **int8（±127）**，预乘后极易越界，而 `lower_api._i8()` 会**直接抛 `PackError`** |

> 参考：`0x03`（自旋）已经踩过同一个坑 —— 详见 `doc/01` 与 `car_config.h` 里
> `STEPS_PER_DEGREE` 的注释（51 → 36）。
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# ★sys.path 顺序至关重要（2026-09-18 的坑）：共享模块会把老工程 `/root/VisualBot` 也塞进
#   sys.path，而它下面有**同名的旧版 aim.py / car.py / vision.py** → 顺序错了会静默用旧版。
#   倒序插入 + 先清掉重插，保证本项目目录排最前。
for _p in ("/root/VisualBot", "/root/VisualBot2", HERE):
    if not _p or not os.path.isdir(_p):
        continue
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import route_main as RM                                          # noqa: E402
from route_car import RouteCar                                   # noqa: E402

# 固件 car_config.h 里 STEPS_PER_CM 的当前值。**只用来算建议值**，程序不会改它。
# ★2026-09-28 已由 130 校正为 97（实测：指令 100cm → 走 133.9cm）。
#   下次再测时，本值即为"当前基准"，改过固件记得同步这里（或用 --fw-steps 覆盖）。
FW_STEPS_PER_CM = 97

# 方向：键 = car 上的方法名；值 = (中文名, 一个字符)
DIRS = {
    "fwd":   ("前进", "f"),
    "back":  ("后退", "b"),
    "left":  ("左移", "l"),
    "right": ("右移", "r"),
}


# ----------------------------------------------------------------------
# 交互小工具
# ----------------------------------------------------------------------
def ask(prompt, default=None):
    try:
        s = input(prompt).strip()
    except EOFError:
        return default
    return s if s else default


def ask_float(prompt, default=None):
    s = ask(prompt)
    if s is None:
        return default
    try:
        return float(s)
    except ValueError:
        print("   !! 不是数字：%r" % s)
        return default


# ----------------------------------------------------------------------
# 单轮测量
# ----------------------------------------------------------------------
def run_one(car, kind, dist, idx, total):
    """发一段并问实测值。返回实测 cm（float）；跳过/中止返回 None。

    中止时抛 KeyboardInterrupt，由上层收尾出统计。
    """
    label = DIRS[kind][0]
    print("\n── 第 %d/%d 轮：%s %.1f cm ──" % (idx, total, label, dist))
    ans = (ask("   回车发车（q 中止本次统计）> ") or "").lower()
    if ans == "q":
        raise KeyboardInterrupt

    t0 = time.time()
    getattr(car, kind)(dist)                 # car.fwd / back / left / right
    dt = time.time() - t0
    if dt < 0.05:
        # dry-run 下不会真等，这里只是提示一下
        print("   （dry-run：未真实移动）")
    else:
        print("   已走完（命令耗时 %.2fs）。" % dt)
    print("   现在用尺量：车**实际**移动了多少 cm？（量起点到终点的直线距离）")

    m = ask_float("   实测 cm（留空=跳过这轮）> ")
    if m is None:
        print("   跳过。")
        return None
    if m <= 0.01:
        print("   !! 实测值太小，忽略")
        return None
    return m


# ----------------------------------------------------------------------
# 统计与报告
# ----------------------------------------------------------------------
def report(rows, dist, fw_steps):
    """rows = [(实测cm, 比例M/N), ...]（各方向合并前的单方向样本）"""
    if not rows:
        print("\n  没有有效样本，无法给出建议。")
        return

    ms = [m for m, _ in rows]
    ratios = [r for _, r in rows]            # 实测/指令
    r_mean = statistics.mean(ratios)
    r_sd = statistics.stdev(ratios) if len(ratios) > 1 else 0.0

    # 走少了(实测<指令) → 每 cm 需要**更多**步 → STEPS_PER_CM 要**变大**
    k = 1.0 / r_mean                          # 放大倍数 = 指令/实测
    new_steps = fw_steps * k

    print("\n" + "=" * 66)
    print("  结果（指令 %.1f cm，样本 %d 个）" % (dist, len(rows)))
    print("=" * 66)
    print("  各轮实测：%s" % "、".join("%.1f" % m for m in ms))
    print("  平均实测：%.2f cm" % statistics.mean(ms))
    print("")
    print("  ① 比例（实测/指令）= %.4f   %s" % (
        r_mean, "（走少了）" if r_mean < 1 else ("（走多了）" if r_mean > 1 else "（正好）")))
    print("  ② 指令/实测（放大倍数）= %.4f" % k)
    print("")
    print("  ★ 固件建议值：STEPS_PER_CM  %d → **%.1f**" % (fw_steps, new_steps))
    print("     位置：26091601TEST/CLEAN/car_config.h")
    print("     ⚠️ 改完必须 Keil **Rebuild all** → 确认 hex 时间戳 → 重烧，否则不生效。")
    print("")
    print("  （备选，不推荐）上位机预乘系数 = %.4f" % k)
    print("     原因：0x01/0x02 距离字段是 int8(±127)，预乘后容易越界并抛 PackError。")

    # 一致性检查
    if len(rows) > 1 and r_sd > 0.02:
        print("")
        print("  ⚠️ 各轮差异偏大（σ=%.4f，即 ±%.1f%%）—— 建议排查：" % (r_sd, r_sd * 100))
        print("     地面打滑 / 电量不一致 / 轮子或联轴器松动 / 起点没对准同一条线")

    # 反算示例，直观验证
    print("")
    print("  验证：按新值 %.1f，以后发 %.1f cm 应该正好走 %.1f cm"
          % (new_steps, dist, dist * (new_steps / fw_steps) * r_mean))
    print("=" * 66)


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="平移比例标定（发一段→量实际→算比例）")
    ap.add_argument("--port", default="/dev/stm32", help="串口设备（默认 /dev/stm32）")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dist", type=float, default=100.0, help="每次发多少 cm（默认 100）")
    ap.add_argument("--rounds", type=int, default=3, help="每个方向测几轮（默认 3）")
    ap.add_argument("--dir", default="fwd",
                    choices=["fwd", "back", "left", "right", "all"],
                    help="测哪个方向；all = 四个方向各测（默认 fwd）")
    ap.add_argument("--fw-steps", type=float, default=FW_STEPS_PER_CM,
                    help="固件里 STEPS_PER_CM 的当前值（用来算建议值，默认 %d）" % FW_STEPS_PER_CM)
    ap.add_argument("--dry-run", action="store_true", help="不连车，只走流程与公式")
    a = ap.parse_args()

    if a.dist <= 0 or a.dist > 127:
        print("!! --dist 必须在 (0, 127] 之间 —— 0x01/0x02 的距离字段是 int8(±127)")
        return 2
    if a.rounds < 1:
        print("!! --rounds 至少为 1")
        return 2

    kinds = list(DIRS) if a.dir == "all" else [a.dir]

    car = RouteCar(port=a.port, baud=a.baud, dry_run=a.dry_run)
    print("=" * 66)
    print("平移比例标定 | %s" % ("dry-run（不动电机）" if a.dry_run else "真跑模式"))
    print("  指令距离 %.1f cm × %d 轮/方向；方向：%s"
          % (a.dist, a.rounds, "、".join(DIRS[k][0] for k in kinds)))
    print("  固件 STEPS_PER_CM 当前值（用于算建议）：%.1f" % a.fw_steps)
    print("=" * 66)

    if not a.dry_run:
        car.setup(chassis_vel=RM.CHASSIS_VEL, chassis_acc=RM.CHASSIS_ACC,
                  chassis_dec=RM.CHASSIS_DEC, base90=RM.BASE_90, ttl_vel=RM.TTL_VEL)

    all_rows = {}
    try:
        for kind in kinds:
            rows = []
            for i in range(1, a.rounds + 1):
                try:
                    m = run_one(car, kind, a.dist, i, a.rounds)
                except KeyboardInterrupt:
                    print("\n   [已中止本方向的后续轮次]")
                    break
                if m is not None:
                    rows.append((m, m / a.dist))
            all_rows[kind] = rows
            if len(kinds) > 1:
                print("\n  ══ %s 方向小结 ══" % DIRS[kind][0])
            report(rows, a.dist, a.fw_steps)
    finally:
        car.close()

    # 四方向对比（只在 --dir all 时）
    if len(kinds) > 1:
        print("\n" + "=" * 66)
        print("  四方向对比（比例 = 实测/指令）")
        print("=" * 66)
        print("  %-6s %-8s %-10s %s" % ("方向", "样本", "平均比例", "固件建议 STEPS_PER_CM"))
        for kind in kinds:
            rows = all_rows.get(kind) or []
            if not rows:
                print("  %-6s %-8s %s" % (DIRS[kind][0], 0, "（无样本）"))
                continue
            r = statistics.mean([x for _, x in rows])
            print("  %-6s %-8d %-10.4f %.1f" % (DIRS[kind][0], len(rows), r, a.fw_steps / r))
        print("")
        print("  ⚠️ 若四个方向的建议值差异明显 → 说明机械效率不同，")
        print("     此时固件只有一个 STEPS_PER_CM，只能取折中，或给方向单独加补偿系数。")
        print("=" * 66)

    return 0


if __name__ == "__main__":
    sys.exit(main())
