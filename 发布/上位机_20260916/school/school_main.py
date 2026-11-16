#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
school_main.py —— 校赛主程序（**流水账**：一串函数调用，参数就是括号里的变量）
================================================================================
怎么读这个文件：
    · 上半部分 = 「现场参数区」——要调的东西全在那一段，带注释；
    · 中间     = 「工序函数」——每个工序一个函数，函数体就是把 car 的动作串起来；
    · 下半部分 = run() —— **整场比赛的流水账**，从上到下就是车的实际动作顺序。

动作来自 `car.py` 的方法（那里是"车与机械臂的动作库"），本文件只负责"什么时候做什么"。

用法：
    python3 -u school_main.py --dry-run                 # 空跑：不连车/不用相机，验证流程
    python3 -u school_main.py --dry-run --code 123+321  # 空跑并指定任务码
    python3 -u school_main.py --port /dev/ttyUSB0       # 真跑
    python3 -u school_main.py --port /dev/ttyUSB0 --skip-scan   # 跳过扫码（人工给码）

⚠️ 第一次上车务必：① 架空车轮试 setup()；② --dry-run 走一遍；③ 再落地上料。
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
from car import Car                                            # noqa: E402


# ==============================================================================
# 现场参数区（**只改这一段**；改完直接重跑本程序 —— 开赛时会自动下发给 STM32）
# ==============================================================================

# ---- 底盘（档位，不是 m/s）----
CHASSIS_VEL = 8          # 平移/前进速度档：飘就降到 6，稳了可加到 10
CHASSIS_ACC = 6          # 加速档
CHASSIS_DEC = 3          # 减速档（比 ACC 小 = 刹车比起步缓，防点头丢步）

# ---- 夹爪：★是「舵机」（固件 claw.c 的 Grip_Close/Grip_Open，协议 0x14/0x15）★
#      单位是**角度 0~255°**，不是脉冲！这两个值会随 setup() 下发给下位机参数表
#      （固件若已改成"读参数"，就按这里的值动；否则用 car_config.h 的 SERVO_GRIP_* 默认值）
GRIP_CLOSE_ANGLE = 82    # 闭合角度（固件默认 SERVO_GRIP_CLOSE=82：顶死就减，夹不住就加）
GRIP_OPEN_ANGLE  = 50    # 张开角度（固件默认 SERVO_GRIP_OPEN=50；瞄准想完全张开可设 0）

# ---- 机械臂行程（脉冲；3200 脉冲 = 电机 1 圈，16 细分）----
#   ★本车结构（车主确认）：伸缩=TTL id1、**龙门架升降=TTL id2+id3 反向传动**、base=TTL id4；
#     夹爪=舵机（见上）；**车上没有独立步进升降轴、没有载物台旋转电机**（载物台固定）。
BASE_90      = 6400      # base 转 90°（id4）
LIFT_UP      = 4400      # 龙门架抬升 —— 本车的"升降"就是它（id2/id3 反向传动，协议 0x11）
LIFT_DOWN    = 4400      # 龙门架下放
TELESCOPE    = 3000      # 伸缩前伸/收回行程（id1）
ARM_OUT_VEL  = 30        # 前伸速度 RPM（慢一点，别撞料）
ARM_IN_VEL   = 40        # 收回速度 RPM
TTL_VEL      = 40        # 机械臂默认转速

# ---- 里程（cm / 度）—— ★全部要现场实测标定 ----
RIGHT_OUT     = 30       # ① 右移出启动区
TO_SCAN       = 60       # ② 前进到扫码位
TO_MATERIAL   = 80       # ③ 前进进原料区
TO_PROCESS    = 70       # ④ 出原料区后前进
SPIN_DEG      = 90       # ⑤ 原地自旋角度
ENTER_PROCESS = 50       # ⑥ 自旋后进加工区
TO_FINISH     = 60       # ⑦ 出加工区前进
FINISH_RIGHT  = 120      # ⑧ 向右长距平移进结束位
PITCH         = 35       # 相邻物料/靶位间距（下面坐标表默认按这个排）

# ---- 工位纵向坐标（cm，进区作业起点 = 0，前进为正）----
# ⚠️[待确认] 方向：默认"1 号位最近、3 号位最远"；若实测相反，把数字倒过来即可
MATERIAL_CM = {1: 0.0, 2: PITCH * 1.0, 3: PITCH * 2.0}   # 原料区 1/2/3 号位
TARGET_CM = {1: 0.0, 2: PITCH * 1.0, 3: PITCH * 2.0}     # 加工区 1/2/3 号靶位

# ---- 相机 / 扫码 ----
CAM_INDEX    = 0         # 作业相机编号（现场用 `monitor_ui.py --list-cams` 确认；打不开会自动回退到 0..3）
PERF         = "rdk"     # 视觉性能档（RDK X5 用 rdk）
SCAN_TIMEOUT = 10.0      # 扫码超时（秒）；超时用兜底码继续跑，别卡死

# ==============================================================================


# ------------------------------------------------------------------------------
# 工序函数：一个工序一个函数，函数体就是"什么时候做什么"
# ------------------------------------------------------------------------------
def scan_task_code(car, code=None, skip=False):
    """扫码工序：返回 (code, 取料顺序, 放置顺序)。连续 3 帧一致才采纳，超时用兜底码。"""
    if code:
        car.log("[扫码] 命令行指定任务码：%s" % code)
    elif skip:
        code = SC.CODE_DEFAULT
        car.log("[扫码] --skip-scan：用兜底码 %s" % code)
    else:
        import qr_scan
        car.log("[扫码] 侧装相机监听中（最多 %.0fs）..." % SCAN_TIMEOUT)
        got, ok = qr_scan.listen(car.vision, timeout=SCAN_TIMEOUT, log=car.log)
        if not ok:
            car.log("!! 扫码超时 → 用兜底码 %s（继续跑完，不卡死）" % SC.CODE_DEFAULT)
        code = got if ok else SC.CODE_DEFAULT
    pick, place = SC.parse_code(code)
    if not pick:
        car.log("!! 任务码不合法 %r → 用兜底码 %s" % (code, SC.CODE_DEFAULT))
        code = SC.CODE_DEFAULT
        pick, place = SC.parse_code(code)
    car.lower.show_qr(code)                     # 任务码上屏（赛规要求显示）
    car.log("[扫码] 任务码 %s → 取料顺序 %s / 放置顺序 %s（颜色：1红 2绿 3蓝）"
            % (code, pick, place))
    return code, pick, place


def go_material_area(car):
    """进原料区并摆出作业姿态：前进到位 + base 左转 90°（车左侧朝向料区）。"""
    car.fwd(TO_MATERIAL)
    car.cur_cm = 0.0                            # 进区作业起点 = 0
    car.base_left(BASE_90)


def pick_all(car, pick_order):
    """取料工序：按扫码顺序，逐个移动到工位 → 瞄准 → 夹 → 收回 → 记账。"""
    car.log("[取料] 共 %d 个：%s" % (len(pick_order), "→".join(pick_order)))
    for i, mid in enumerate(pick_order, start=1):
        tag = "G%d" % i
        car.log("[取料] %s：去 %s 号位（%s，%.0fcm）" % (tag, mid, SC.color_name(mid), MATERIAL_CM[int(mid)]))
        car.goto_cm(MATERIAL_CM[int(mid)], tag="  ")
        ok = car.pick_here(expect_color=SC.color_name(mid), tag=tag,
                           out_pulses=TELESCOPE, out_vel=ARM_OUT_VEL, in_vel=ARM_IN_VEL)
        if ok:
            car.ledger.record(mid, i)           # 第 i 个取的 → 车上 slot i
            car.log("  ✓ 已上车：%s 号 → slot %d（账本 %s）" % (mid, i, car.ledger.summary()))
        else:
            car.log("  !! %s 取料失败，跳过该物料" % tag)


def go_process_area(car):
    """转场工序：收臂 → 前进 → 原地自旋 → 前进进加工区 + base 左转 90°。"""
    car.home(telescope=TELESCOPE, base90=BASE_90, speed=TTL_VEL)
    car.fwd(TO_PROCESS)
    car.spin(SPIN_DEG)
    car.fwd(ENTER_PROCESS)
    car.cur_cm = 0.0
    car.base_left(BASE_90)


def place_all(car, place_order):
    """放置工序：按后段顺序，逐个到靶位 → 瞄准记偏移 → 回物架夹料 → 回放 → 放。"""
    plan = SC.place_plan(place_order)            # [(物料编号, 目标靶位), ...]
    car.log("[放置] 共 %d 个：%s" % (len(plan),
                                    "、".join("%s号→%d位" % (m, t) for m, t in plan)))
    for mid, tgt in plan:
        tag = "P%s→%d" % (mid, tgt)
        try:
            slot = car.ledger.slot_of(mid)
        except RuntimeError as e:
            car.log("  !! %s，跳过" % e)
            continue
        car.log("[放置] %s：%s 号料（%s）→ 加工区 %d 号靶位（%.0fcm，车上 slot %d）"
                % (tag, mid, SC.color_name(mid), tgt, TARGET_CM[tgt], slot))
        car.goto_cm(TARGET_CM[tgt], tag="  ")
        ok = car.place_here(slot=slot, tag=tag,
                            out_pulses=TELESCOPE, out_vel=ARM_OUT_VEL, in_vel=ARM_IN_VEL)
        if ok:
            car.ledger.consume(mid)
            car.log("  ✓ 已放置：%s 号 → 靶位 %d（账本 %s）" % (mid, tgt, car.ledger.summary()))
        else:
            car.log("  !! %s 放置失败" % tag)


def go_finish(car):
    """收尾工序：收臂 → 前进 → 向右长距平移 → 停车。"""
    car.home(telescope=TELESCOPE, base90=BASE_90, speed=TTL_VEL)
    car.fwd(TO_FINISH)
    car.right(FINISH_RIGHT)
    car.stop()


# ------------------------------------------------------------------------------
# ★ 整场流水账（从上到下 = 车的实际动作顺序）
# ------------------------------------------------------------------------------
def run(car, code=None, skip_scan=False):
    car.log("=" * 62)
    car.log("校赛开始：任务码 %s" % (code or ("(跳过扫码)" if skip_scan else "(待扫码)")))
    car.log("=" * 62)

    # ① 参数下发（这一步不通就别发车）
    if not car.setup(chassis_vel=CHASSIS_VEL, chassis_acc=CHASSIS_ACC, chassis_dec=CHASSIS_DEC,
                     grip_close_angle=GRIP_CLOSE_ANGLE, grip_open_angle=GRIP_OPEN_ANGLE, base90=BASE_90,
                     lift_up=LIFT_UP, lift_down=LIFT_DOWN, telescope=TELESCOPE, ttl_vel=TTL_VEL):
        car.log("!! 参数下发/读回校验未通过 —— 建议先查通信再发车")

    # ② 出启动区 → 前进到扫码位 → 扫码
    car.right(RIGHT_OUT)
    car.fwd(TO_SCAN)
    code, pick_order, place_order = scan_task_code(car, code=code, skip=skip_scan)

    # ③ 进原料区 + base 左转 90°（车左侧朝向料区）
    go_material_area(car)

    # ④ 三次瞄准取料（G1/G2/G3：瞄上即夹）
    pick_all(car, pick_order)

    # ⑤ 收臂转场：前进 → 自旋 90° → 进加工区 + base 左转 90°
    go_process_area(car)

    # ⑥ 三次瞄准放置（P1/P2/P3：瞄 → 取料 → 放）
    place_all(car, place_order)

    # ⑦ 前进 → 向右长距平移 → 停
    go_finish(car)

    placed = len(car.ledger.done)
    car.log("=" * 62)
    car.log("完赛：放置 %d/3，用时 %.1fs，账本 %s" % (placed, car.seconds(), car.ledger.summary()))
    car.log("=" * 62)
    return placed


# ------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dry-run", action="store_true", help="不连车/不用相机，验证流程")
    ap.add_argument("--code", default=None, help="直接给任务码（跳过扫码），如 123+321")
    ap.add_argument("--skip-scan", action="store_true", help="跳过扫码，用兜底码")
    ap.add_argument("--cam", type=int, default=CAM_INDEX, help="作业相机编号")
    ap.add_argument("--perf", default=PERF, help="视觉性能档(auto/low/mid/high/rdk)")
    a = ap.parse_args()

    car = Car(port=a.port, baud=a.baud, dry_run=a.dry_run)
    try:
        if not a.dry_run:
            car.use_camera(cam_index=a.cam, perf=a.perf)
        else:
            car.use_camera()                      # dry-run 装仿真视觉
        run(car, code=a.code, skip_scan=a.skip_scan)
    except KeyboardInterrupt:
        car.log("!! 人工中断 → 停车")
        car.stop()
        return 130
    finally:
        car.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
