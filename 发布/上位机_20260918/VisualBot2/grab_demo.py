#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
grab_demo.py —— 「瞄准 → 夹取 → 放到 1/2/3 号物架」分步调试 demo（VisualBot2）
================================================================================
要解决的事：把「从地面瞄准夹起一个物料，然后按需要放到车上物架的 1/2/3 号位」
            这套动作**一步一步试出来**。

为什么要有它：**"放到物架"这套动作在老工程和固件里都没有现成的** ——
  固件只有"从物架**取**料"（`TTLctrl_Act_GrabFromVehicle_1/2/3`，协议 0x16），
  反过来"放上去"需要自己拼动作组。所以这个 demo 就是给你**单步试 + 随时改脉冲 + 打印成代码**用的。

用法
    cd /root/VisualBot2
    python3 -u grab_demo.py --port /dev/stm32 --color red
    python3 -u grab_demo.py --dry-run            # 不连车，只验证菜单/动作组逻辑

菜单
    h 归位（收臂 + base 回正）         l 进作业姿态（base 左转 BASE_90）
    a 只瞄准（颜色法，看清 dx/dy 收敛） g 取料一遍：瞄准 + PICK_STEPS
    1 / 2 / 3      放到物架对应号位（执行 RACK_STEPS[N]）
    A / B / C      一条龙：瞄准 → 夹取 → 放到物架 1 / 2 / 3
    n              切换"当前动作组"（PICK / RACK1 / RACK2 / RACK3）
    s              单步执行当前动作组（每步回车：回车=执行 s=跳过 r=重跑上一步 q=退出）
    p              打印"当前动作组"（**可直接粘回本文件参数区**）
    e N [脉冲] [速度]        改当前动作组第 N 步
    i N 名称 [脉冲] [速度]   在第 N 步之后插入一步
    d N            删除第 N 步
    m              手动发单个动作（base / lift / arm / grip / wait）
    q              退出

⚠️ 安全：动作组会动伸缩台、龙门架、base、夹爪 —— 每次真发动作前都会再问一次；
   转 base / 抬龙门时先确认周围清空。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# ★★sys.path 顺序至关重要（2026-09-18 的大坑）：共享模块会把**老工程 `/root/VisualBot`**
#   也塞进 sys.path，而 `/root/VisualBot/校赛/` 下有**同名的旧版 aim.py / car.py / vision.py**。
#   原来的顺序插入（`insert(0, …)` 逐个加）会让**后插的排最前**，`/root/VisualBot`
#   反超成本目录之前 → `import aim` 命中旧版 → 三阶段瞄准/软化/arm 阻尼/K 全部失效。
#   → 倒序插入 + 先清掉重插，保证本项目自己的目录最终排最前。
for _p in ("/root/VisualBot", "/root/VisualBot2", HERE):
    if not _p or not os.path.isdir(_p):
        continue
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import school_config as SC                                          # noqa: E402
import car as CAR                                                   # noqa: E402
import route_main as RM                                             # noqa: E402

# ==============================================================================
# 参数区（★现场要调的都在这一段）
# ==============================================================================

# ---- 基本行程 / 速度（与主工程保持一致；改这里会同时影响取料与放物架）----
LIFT_UP        = 4400        # 龙门抬升量（= school_main 现场值；★route_main 里没这个键，
                             #   所以别再写成 getattr(RM,…) —— 那只会静默回退，见下面 K 的坑）
LIFT_DOWN      = 4400        # 龙门下探量（从瞄准高度到地面抓取）★现场实测
LIFT_UP_VEL    = 40
LIFT_DOWN_VEL  = 30
BASE_90        = getattr(RM, "BASE_90", 4800)               # base 转 90°（作业姿态）
TTL_VEL        = getattr(RM, "TTL_VEL", 40)
ARM_OUT_VEL    = 200
ARM_IN_VEL     = 200

# ---- ① 取料动作组：从**地面**夹取（不用伸缩台；瞄准完直接下龙门架）----
PICK_STEPS = [
    ("grip_open",),                               # 张爪
    ("lift_down", LIFT_DOWN, LIFT_DOWN_VEL),      # 龙门下探到料
    ("grip_close",),                              # 夹住
    ("lift_up", LIFT_UP, LIFT_UP_VEL),            # 抬起
]

# ---- ② 放到**车上物架**的动作组（1/2/3 号位各一组）----
#   ★流程（前提：车已在**左侧 90° 工作位**、龙门在瞄准高度、伸缩在原位、夹爪夹着料）：
#       ① 龙门架上升一定高度（别蹭到物架）
#       ② base **向右（顺时针）** 转到该号位的角度 ← 1 号位在左前方，从 90° 转 60° 到 30°
#       ③ 伸缩臂**向后缩**一段
#       ④ 龙门架下放一段（到物架料位）
#       ⑤ 松开夹爪（放料）
#       ⑥ 龙门架**小抬一段**，让夹爪离开物料高度
#       ⑦ **先把伸缩臂伸出去**（回到原来长度）
#       ⑧ base **向左（逆时针）** 转回 90° 工作位
#       ⑨ 转回**之后**把龙门架送回**瞄准开始时的高度**（方向自动：可能抬、也可能下放）
#   ★★★为什么"必须**先伸出伸缩、再转回 base**"（车主 2026-09-18 现场纠正，非常关键）：
#       放料时伸缩是**缩回**的（③ arm_in）→ 物料正好落在**缩回半径**那个圆周上。
#       如果保持缩回就去转 base，臂会在**和物料同一个半径**上横扫过去 → **把物料打飞**。
#       所以必须先把伸缩**伸出去**（⑦），让臂退到更大的半径、离开物料所在的那一圈，
#       然后再转回 90°（⑧）✓
#       ⚠️ 我曾想当然地把 arm_out 挪到转回**之后**，理由是"缩着扫掠半径更小更安全" ——
#          **完全错了**：缩回半径 ≈ 物料半径，转过去正好扫到。别再犯。
#   ★龙门架的"两段"处理（车主 2026-09-18："最后转完 90 度再调龙门架"、"别干涉到物料"）：
#       ⑥ 转回**前**只小抬一段（RACK_LIFT_CLEAR）：⑤松爪时龙门停在料位，直接转回会刮到物料；
#          而此时臂还在号位上方，抬太高又会撞物架 → 所以只抬"够离开物料"这么点。
#       ⑨ 转回**后**才做完整的高度校正（RACK_LIFT_BACK）：此时臂已离开物架，怎么动都安全。
#   ★★高度必须配平（车主 2026-09-18："不应该是放回瞄准开始时的高度吗"）：
#       ① +UP 、④ −DOWN 、⑥ +CLEAR 、⑨ +BACK  →  四者之和必须 = 0，
#       才叫"放完料回到瞄准开始高度"。所以 BACK = DOWN − UP − CLEAR（**可以是负数 = 下放**）。
#   ★几何依据（车主 2026-09-17 明确确认）：
#      **三个置物架在以 base 轴为圆心的同一个圆上** —— 也就是说从 base 轴看过去，
#      三个位置**半径相同、只差角度**。所以：
#        · 从左侧 90° 工作位出发，各转 60° / 90° / 120° 就到 1 / 2 / 3 号位；
#        · **因为半径相同，伸缩臂和龙门架的量三个号位完全共用一套**（不需要按号位分别标定）
#          → 见下面 RACK_ARM_IN / RACK_LIFT_UP / RACK_LIFT_DOWN / RACK_LIFT_BACK：
#            它们只有一份，RACK_STEPS 里三个号位引用的是同一批常量 ✓
#      这也解释了为什么只要对准角度就行、不必为每个号位改行程。
#   ⚠️ 角度值已由车主确认；伸缩/龙门的具体脉冲数仍需现场按实际料位微调 ——
#      用菜单 m 手试、e 改、s 单步验、p 打印，最后抄回这里。
RACK_WORK_DEG  = 90.0    # 作业位 = base 左侧 90°（0° = 车正前方，+= 左转/逆时针）
RACK_TURN_DEG = {1: 55, 2: 90, 3: 125}   # 从 90° 工作位顺时针转过去多少度（★车主确认）
                                         #   → 号位的**绝对角** = RACK_WORK_DEG - RACK_TURN_DEG[n]
                                         #     1 号位 = 30°（左前方）  2 号位 = 0°（正前方）
                                         #     3 号位 = -30°（右前方，即 330°）
                                         #   三者都是 30° 的整数倍 → 正好印证"同圆等分"
#   ★★2026-09-17 现场教训（车主报"点 1 却向右转"）：
#      动作组里的 ("base_right", …) 是**相对**转动，它默认"此刻 base 就在 90° 工作位"。
#      车主在**初始化位置（0°）**直接点 1 → 又顺时针转 3200 脉冲 → 跑到车**右前方**
#      （-60°）去了，跟他要的左前方差 120°。
#      → 所以菜单里跑 1/2/3 时，base 那两步**不再按脉冲数相对转**，改由 goto_base_deg()
#        按【绝对角】定位（见 _run_rack）。下面 RACK_STEPS 里的 base 脉冲值只是
#        "从 90° 出发该转多少"的记录，供 p 打印/抄写用，实际执行以绝对角为准。
RACK_LIFT_UP   = 2800    # ① 去物架前先抬多高
                         #   ★2026-09-17 车主现场要求：原 800 → **+1000 = 1800**（抬得更高，免蹭物架）
RACK_ARM_IN    = 6500    # ③ 伸缩缩回多少
RACK_LIFT_DOWN = 300    # ④ 下放到物架料位多少
                         #   ★同步 +1000（1000→2000）——车主："记得放下的时候再都放回来"。
                         #   验算：④ 到达高度 = 瞄准高度 + 1800 − 2000 = 瞄准高度 − 200，
                         #   与改动前（+800 − 1000 = −200）**完全一样** → 料位高度不变 ✓
RACK_LIFT_CLEAR = 300    # ★2026-09-18 车主定稿：300 → 800
                         #   （现场 15:28 一度误输成 8000：那会让 BACK = −10500 →
                         #     ⑨ 下放 10500、取料侧 ① 抬 10500 脉冲，第一步就顶死限位）
                         # ⑥ 松爪后、**转回 base 之前**先抬这么高 —— 只为让夹爪离开物料高度。
                         #   ⑤ 松爪时龙门停在料位，直接转回的话夹爪会贴着物料扫过去 → 刮倒物料。
                         #   这一段要**小**（此时臂还在号位上方，抬太高会撞物架）；
                         #   真正"抬到目标高度"放在转回**之后**的 ⑨。
                         #   ⚠️ 值要现场试：太小会刮料，太大在号位可能蹭物架。用 `m` 手试。
RACK_LIFT_BACK = RACK_LIFT_DOWN - RACK_LIFT_UP - RACK_LIFT_CLEAR
                         # ★⑨ 转回 90° **之后**，把龙门架送回**瞄准开始时的高度**所需的位移。
                         #   ★★高度账（起点 = 瞄准开始高度 H，要求结束也回到 H）：
                         #       ① +RACK_LIFT_UP       （抬起来去号位）
                         #       ④ −RACK_LIFT_DOWN     （下放到料位 —— 这里是料位）
                         #       ⑥ +RACK_LIFT_CLEAR    （小抬，脱离物料）
                         #       ⑨ +RACK_LIFT_BACK     ← 让它把总和补回 0
                         #     → RACK_LIFT_BACK = RACK_LIFT_DOWN − RACK_LIFT_UP − RACK_LIFT_CLEAR
                         #   当前 = 300 − 2800 − 800 = **−3300** → ⑨ 实际执行「**下放 3300**」。
                         #   ★**这个值允许为负**：负 = 还要往下走（lift_down），正 = 往上抬（lift_up）。
                         #     方向由 _back_steps() 自动判断 —— 不用手算，也不会再把负脉冲
                         #     直接下发给下位机（那会抛 PackError 打断整组）。
                         #   ★历史教训：早先这一步只能是"正值抬升"，而 ① 抬的(2800) 远大于
                         #     ④ 放的(1000)，高度只会越走越高（净 +3100）→ 车主 2026-09-18：
                         #     "转回 90° 之后这个高度真的对吗，不应该是放回瞄准开始时的高度吗"
                         #     —— 就是这笔账没配平。
RACK_HOME_BEFORE = True  # ★放料前先"归零"（车主 2026-09-18 建议，默认开）：
                         #   把瞄准挪走的 base / **伸缩** 偏移**原样还回去**，回到确定的起点
                         #   （base 90° 工作位、伸缩原位、龙门瞄准高度），再开始放料。
                         #   为什么需要：动作组里 base 是绝对角定位、不受影响；但
                         #   ("arm_in", RACK_ARM_IN) 是**相对**位移 —— 瞄准动过伸缩之后，
                         #   缩回的落点每次都不一样，可能撞物架。归零后每次都一致 ✓
                         #   False = 关（直接从当前位置放料，用于对比排查）。
RACK_TTL_VEL   = 40      # base 转动速度 RPM


def _back_steps():
    """⑨ 的动作：把龙门送回**瞄准开始高度**（见 RACK_LIFT_BACK 的高度账）。

    用 RACK_LIFT_BACK 的**符号**决定方向（负 = 下放），脉冲取绝对值。
    ⚠️ 绝不能把负脉冲直接下发给下位机：pack_ttl 会抛 PackError 并**打断整组**，
       老症状就是"放完物料之后没有后续动作"。差值为 0 时干脆不加这一步。
    """
    d = int(RACK_LIFT_BACK)
    if d > 0:
        return [("lift_up", d, LIFT_UP_VEL)]
    if d < 0:
        return [("lift_down", -d, LIFT_DOWN_VEL)]
    return []

# （原来这里有一道"RACK_LIFT_BACK ≤ 0 就回退成 RACK_LIFT_DOWN"的保险 ——
#   现在 RACK_LIFT_BACK **允许为负**（负 = 下放，由 _back_steps() 自动判方向），
#   所以那道保险已经删掉，否则会把"下放回瞄准高度"这种正确情况给改坏。）


def _deg2pulses(deg):
    """角度 → 脉冲（按 BASE_90 折算；1° ≈ BASE_90/90 ≈ 53 脉冲）。"""
    return int(round(BASE_90 * float(deg) / 90.0))


RACK_STEPS = {
    n: [
        ("lift_up", RACK_LIFT_UP, LIFT_UP_VEL),                       # ① 抬龙门
        ("base_right", _deg2pulses(RACK_TURN_DEG[n]), RACK_TTL_VEL),  # ② 顺时针转到该号位
        ("arm_in", RACK_ARM_IN, ARM_IN_VEL),                          # ③ 伸缩缩回
        ("lift_down", RACK_LIFT_DOWN, LIFT_DOWN_VEL),                 # ④ 下放到料位
        ("grip_open",),                                               # ⑤ 松爪放料
        ("lift_up", RACK_LIFT_CLEAR, LIFT_UP_VEL),                    # ⑥ ★先小抬一段，让夹爪离开物料高度
        ("arm_out", RACK_ARM_IN, ARM_OUT_VEL),                        # ⑦ ★★**先把伸缩伸出去**（必须！）
                                                                      #   缩回半径 ≈ 物料所在半径 → 缩着转 base
                                                                      #   会**把物料打飞**；先伸出去让臂退到更大的
                                                                      #   半径、离开物料那一圈，转过去才碰不到 ✓
        ("base_left", _deg2pulses(RACK_TURN_DEG[n]), RACK_TTL_VEL),   # ⑧ 再逆时针转回 90° 工作位
    ] + _back_steps()                                                 # ⑨ ★转回**后**把龙门送回
                                                                      #   **瞄准开始时的高度**
                                                                      #   （方向自动判断：负=下放；
                                                                      #    高度账见 RACK_LIFT_BACK）
    for n in (1, 2, 3)
}

# ---- ★上电初始化动作（与主工程 / 跑图同一套：把机械臂从"摆放位"送到"起始位"）----
#   轴号：1=伸缩滑台 2=龙门架 4=base 轴；方向：id1 0=前伸/1=后缩，其余 0=正转/1=反转
#   ⚠️ 是**相对行程**，只在机械臂处于【上电摆放位】时执行；
#      臂已在起始位时再跑一遍会继续往同方向走 → 顶死限位 → 那种情况用 `--no-init`。
INIT_STEPS = [
    ("axis", 1, 0, 2000, 250),   # 伸缩前伸到机械零点
    ("axis", 2, 1, 5000,   80),  # 龙门抬升到机械零点（= 起始位/瞄准高度）
    ("axis", 4, 1, 4800, 200),   # base 右转回正
]
INIT_ON_START = True             # 跑 demo 前先做初始化（STM32 刚上电那一次）

# ---- 下发给 STM32 的参数（★底盘档位最重要：决定车走多快，夹取/放料时别飘）----
CHASSIS_VEL      = 7
CHASSIS_ACC      = 6
CHASSIS_DEC      = 3
GRIP_CLOSE_ANGLE = 82            # 夹爪闭合角度（舵机，度）
GRIP_OPEN_ANGLE  = 10            # 夹爪张开角度（舵机，度）★2026-09-18 车主：
                                 #   "所有瞄准时把夹爪再开大点，开到舵机 10 度"（50 → 20 → 10）
                                 #   ⚠️ 对这台车：**角度越小 = 爪片张得越开**，别按"越大越开"想。
                                 #   ★改完必须**重启程序**才生效 —— setup() 只在启动时下发一次。
                                 #   为什么瞄准前要张爪：半开/闭合时爪片会伸进镜头，把色块切掉一块、
                                 #   甚至自己被当成目标（车主原话："瞄准物料的过程中夹爪要再次打开"）。
TELESCOPE        = 6400          # 伸缩行程（伸向物架用 6400，与固件 0x16 一致）

# ---- 相机（取料瞄准用前方作业相机）----
CAM_INDEX    = 0
# ★★2026-09-17 现场根因（"瞄准大幅摇摆"的真凶）：这里以前是 "auto"。
#    /root/VisualBot2 下**没有 ring_calib.json**，于是 cam_rotate.resolve("auto") 回落到
#    ring_center_detect_v2.CALIB["rotate_ccw"] = True（树莓派时代相机倒装的旧默认）
#    → 画面被**逆时针转 90°**：1280×720 变成 **720×1280 竖图**
#      （`v` 的日志 cx≈360 / cy≈653 正好反推出画面中心 360×640，即 720×1280）。
#    而 K_BASE/K_ARM 是**按 none（原图）换算**出来的 → 原图的 +x 在画面里变成了 ±y
#    → **x/y 轴整个对调**：base 实际在修 cy、arm 实际在修 cx
#    → 分轴收敛彻底失效：两个轴互相拆台，20 拍单调跑飞、`!! 未收敛`。
#    对照：老工程 /root/VisualBot/校赛 能通过 ../ring_calib.json 读到 none，所以它不犯这个错。
#    → 这里显式写死 "none"，另外 build_car 里加了一道"实际方向不是 none 就告警"的保险。
CAM_ROTATE   = "none"
CAM_EXPOSURE = 30        # ★★2026-09-18 从 60 降到 30 —— 60 在当前光照下**过曝 45%**！
                         #   实测扫档（/dev/video0，1280x720，当前环境光）：
                         #       曝光 15 → 过曝 0.29%   20 → 0.46%   25 → 0.62%
                         #       曝光 30 → 过曝 0.95%   ← 取这个：够亮且几乎不过曝
                         #       曝光 40 → 17.3%   50 → 39.3%   60 → **45.1%**（原值）
                         #   过曝的后果：颜色**饱和**（红色被洗成粉白）→ 质心飘 →
                         #   瞄准"越修越偏"。车主怀疑"瞄准问题就是因为这个"，方向是对的。
                         #   ⚠️ 曝光是**按场景**定的：换环境光/换距离要重测，用
                         #      `python3 -u exposure_tune.py`（它会逐档给建议值）。
PERF         = "rdk"

# ---- 瞄准换算 K（px/脉冲）★必须用**本车**实测值 ----
#   ⚠️ 2026-09-17 抓到：build_car 里原先是 `getattr(RM, "K_BASE", SC.K_PX2STEP_BASE)`，
#      而 RM = route_main **根本没有 K_BASE**（跑图用不上）→ 静默回退成 school_config 里
#      "老师那台车"的旧值 0.2593 / 0.0413。
#   ★★2026-09-18 第二次标定（干净数据，全部判据通过）：
#        base 400 脉冲: FWD +141.5px / REV -146.9px  → **0.3605**（回位仅差 -5.3px）
#        arm 1500 脉冲: FWD +108.6px / REV -106.4px  → **0.0716**（回位仅差 +1.1px）
#        耦合 base→y=+0.05、arm→x=+0.02（±0.05 内）→ 分轴解耦成立 ✓
#      K_ARM 与上次几乎一致（0.0717）→ 测法可靠；K_BASE 与上次（0.4049）差 12%。
#      ⚠️ 那次 r=112.7、这次 r=135.5 —— 而**r 变大 K 反而变小**，与"近处同脉冲位移更大"
#         的直觉相反。原因是那个 r 是"红色区域总面积等效半径"，**把同心圆环也算进去了**
#         （外接框 452px），"检测到几圈环"会随曝光/阈值变 → **r 在两次测量间不可比**。
#      → 所以下面把 K_R_SCALE 关掉（见参数区）：在 r 不可靠的前提下"按 r 加权"是在**引入**
#        误差而不是修正它。等以后在 2~3 个**确定不同**的距离各测一次、看清 K 与距离的真实
#        关系，再决定怎么加权。
#      旧值留档：0.4049 / 0.0717（上一次）；0.213 / 0.0385（school_main 人工换算的老值）。
K_BASE = 0.3605
K_ARM  = 0.0716

# ---- 瞄准闭环参数（★"瞄起来左右摇摆"就调这里，运行时用 k 命令改，不用重部署）----
#   控制律（aim.py）：每拍只修**一个**轴 —— base 修画面 x、伸缩修画面 y，
#   顺序 base→arm→base 循环；误差进了容差且连续 2 拍都在容差内才判定收敛。
AIM_TOL_PX       = 8.0     # 收敛容差（px）：x、y **同时** ≤ 它才算"到了"
AIM_OUT_MAX_PX   = 12.0    # 单拍限幅（px）：太大→冲过头（来回摆）；太小→收敛慢/超时
AIM_SETTLE_S     = 0.8     # 每拍动作后停稳等待（秒）—— 机械余振没停就读帧 = 基于假信息决策。
                           #   ★2026-09-18 车主"狠狠加修正阻尼"：0.6 → **0.8**（更稳，代价是慢一点）
AIM_MAX_ITER     = 20      # 单次瞄准最大拍数。★注意每 3 拍只有 2 拍修 x，
                           #   所以实际能覆盖的初始偏差 ≈ 20×12×2/3 ≈ 160px（超过就必超时）
AIM_RETRY        = 8       # 连续丢目标多少次放弃本次瞄准
AIM_PROBE_PULSES = 0       # 丢目标时的小步试探脉冲（0 = 只等待、不动臂 ← 推荐）
AIM_VEL_BASE     = 40      # ★base 微调速度（RPM）。40 时 56 脉冲(≈1°)只要 ~4ms，
                           #   等于"瞬跳+急停"，易丢步/激起机械余振 → 画面晃。
                           #   **怀疑摇摆是机械引起时，先把它降到 15~20 对比。**
AIM_VEL_ARM      = 40      # ★伸缩轴微调速度（RPM），同上
AIM_WARMUP_FRAMES = 3      # ★开工预热帧数 —— 治"**一开始修正方向与正确方向相反**，走一段才绕回来"。
                           #   开工先丢 3 帧，等到"本帧真检测到(stale=0) 且帧间抖动 ≤3px"再进迭代，
                           #   避免拿"机械余振帧"或"tracker 保持的旧中心"（stale 保持会返回上次
                           #   中心且 found=True）去算方向。代价：每次瞄准最多多花 ~1.8s。
                           #   0 = 关闭（老工程默认 0，行为不变）。详见 school_config.py 同名项。
AIM_FLIP_BASE    = False   # ★★2026-09-17 实测结论：**False（不翻转）才是对的**。
                           #   经过：先按"base 一开始朝远离物料的方向转"的现场观察误设成 True，
                           #   车主实测 log 立刻打脸 —— dx 单调发散且符号不变
                           #   （-19.5 → -44.2 → -81.5 → -124.4 → -187.1，"越跑越远"），
                           #   这正是"方向反"的特征（**超调**会翻符号，**方向反**不会）。
                           #   所以 base 原方向本来就是对的；那次"一开始反向"是
                           #   **第一拍数据不可信**造成的（已由上面的 AIM_WARMUP_FRAMES 预热治掉），
                           #   不是符号问题。
                           #   ★教训：方向这种事**别靠现象猜**，用 `c 200` 量（30 秒）。
AIM_FLIP_ARM     = False   # 伸缩方向暂按原假设（车主只反馈 base 反了）
AIM_DAMP_MAX_FLIP = 4      # ★★修正阻尼上限（车主 2026-09-18："狠狠加修正阻尼"）。
                           #   误差符号**反复翻转**（= 上一拍冲过头）时步长逐次减半，
                           #   最多压到 1/2^N。3 → 1/8（原值），**4 → 1/16（现在）**，嫌晃可加 5。
                           #   连续同号（方向对、离得远）时阻尼立刻回到 1，所以不会白白变慢。
# ---- 视觉坐标滤波（取料"物料圆心" / 放置"圆环中心"共用）----
#   原本只有 EMA；这次加**中值滤波**并在它之后再 EMA ——
#   中值对"偶发跳变"（阈值边缘蹦一帧）几乎免疫，且滞后远小于同强度 EMA，所以更稳而不肉。
COLOR_MED_N    = 3         # ★中值滤波窗口（帧）：1=关，3/5 常用
COLOR_EMA      = 0.35      # EMA 系数（越小越平滑、滞后越大）。原来是 0.5
COLOR_JUMP_PX  = 90        # 突跳阈值（px）：原来是 120
COLOR_STALE_MAX = 10       # 连续丢失保持帧数
CAM_FLUSH_FRAMES = 4       # ★★每次取帧前**抽干相机缓冲区里的旧帧**（治"读到 2~3 拍前的画面"）。
                           #   这是"瞄准越修越偏、20 拍不收敛"的**真凶**：cap.read() 返回队列里
                           #   最旧的一帧，而瞄准每拍才读一次、相机 30fps → 积压几十帧
                           #   → 控制器基于过期画面决策 → 过冲 → 振荡。0 = 退回旧行为。
AIM_READ_FRAMES  = 2       # ★瞄准每个决策拍**连采几帧**（让中值/EMA 追上当前真实位置）
AIM_LIMIT_STAGES = [(60.0, 45.0), (25.0, 20.0)]
                           # ★★分段单拍限幅 —— 治"瞄准还是慢"（提速主力）。
                           #   [(误差阈值px, 该档限幅px), …] 从大到小匹配，都不满足就用 AIM_OUT_MAX_PX。
                           #   原来一律 12px：偏差 120px 要 11 拍 ≈9.4s；
                           #   分段后同样条件 5 拍 ≈4.2s（离线仿真，提速 2.2 倍）。[] = 退回旧行为。
K_R_SCALE = False          # ★★2026-09-18 **暂时关掉**（原为 True）。
                           #   理由：两次干净标定给出 r=112.7→K_BASE=0.4049、
                           #   r=135.5→K_BASE=0.3605，**r 变大 K 反而变小**，与"近处同脉冲
                           #   位移更大（K 应更大）"的直觉相反。根因是那个 r 是"红色区域总面积
                           #   等效半径"，**把同心圆环也算进去了**（外接框 452px），
                           #   "检测到几圈环"随曝光/阈值变 → **r 在两次测量间不可比**。
                           #   在 r 不可靠时"按 r 加权"是在**引入**误差，不是在修正它。
                           #   等以后在 2~3 个**确定不同**的距离各测一次、看清 K 与距离的真实
                           #   关系，再决定用哪种加权（或换一个可靠的距离量，比如目标外接框宽）。
                           #   True = 恢复 `K_eff = K × (r / K_R_REF)`。
K_R_REF   = 135.0          #   标定 K 时的目标半径（px）：本次 `c 400 1500` 的起点 r=135.5。
                           #   即使 K_R_SCALE 关着也留着 —— 以后要加权时它是基准点。
AIM_STAGE_CONFIRM = 1      # ★三阶段瞄准的阶段切换确认拍数（1 = 达标即切换，推荐）。
                           #   三步流程 X1→Y→X2 见 aim.py 里 converge 的 STAGES 注释。
                           #   每 +1，三个阶段边界就多花 3 拍纯等待（仿真：1拍=9拍收敛，2拍=12拍）。
# ---- arm（伸缩）轴专项阻尼：治"伸缩台前后调整时在中心前后晃动" ----
#   根因：arm 的 K 只有 base 的 1/5.5（0.0385 vs 0.213），同样 12px 限幅，
#   base 只要 56 脉冲而 arm 要 312 脉冲；而 K_ARM 的标定信号只有 7.8px、噪声占比大，
#   一旦偏小，arm 每拍就走过头 → 在中心附近跨过去再摆回来。
AIM_DAMP_MAX_FLIP_ARM = 5  # arm 轴阻尼上限：5 → 1/32（比 base 的 4 → 1/16 更狠）
AIM_ARM_LIMIT_SCALE = 0.5  # arm 轴各档限幅统一乘这个系数（0.5 = 只用一半）
AIM_SOFT_ZONE_PX = 20.0    # ★★近中心**软化**区（px）——治"中心来回摆"的正解：
AIM_SOFT_GAIN = 0.5        #   误差进入软化区后每拍只走这个比例，一步就跨不过容差。
                           #   仿真（起点30、容差8、K偏大60%）：不软化 [30,10.8,-6.5]（跨中心），
                           #   软化后 [30,10.8,2.2]（收敛且拍数不变）。0 = 关闭。

# 上面这些参数会被注入 school_config（SC），运行时就是它们在生效；
# 菜单里 `k` 可查看、`k 名字 值`（如 `k VEL_BASE 15`、`k COLOR_EMA 0.25`）可现场改。
AIM_KEYS = ("AIM_TOL_PX", "AIM_OUT_MAX_PX", "AIM_SETTLE_S", "AIM_MAX_ITER",
            "AIM_RETRY", "AIM_PROBE_PULSES", "AIM_VEL_BASE", "AIM_VEL_ARM",
            "AIM_WARMUP_FRAMES", "AIM_FLIP_BASE", "AIM_FLIP_ARM",
            "AIM_DAMP_MAX_FLIP", "AIM_READ_FRAMES", "K_R_SCALE", "K_R_REF",
            "AIM_STAGE_CONFIRM", "AIM_DAMP_MAX_FLIP_ARM", "AIM_ARM_LIMIT_SCALE",
            "AIM_SOFT_ZONE_PX", "AIM_SOFT_GAIN")
VISION_KEYS = ("COLOR_MED_N", "COLOR_EMA", "COLOR_JUMP_PX", "COLOR_STALE_MAX",
               "CAM_FLUSH_FRAMES")

# ==============================================================================


# ---- base 角度软跟踪 ------------------------------------------------------------------
#   ★为什么需要：机械上**没有 base 角度反馈**（TTL 闭环电机有编码器，但固件没把它回传给
#     上位机），所以只能在上位机里"记账"。约定：**初始化结束时 base 在正前方 = 0°**，
#     左转为正（+90° = 左侧作业位）。之后每次动 base 就加减。
#   ⚠️ 手动拧过 base、或中途接手时会失准 → 那种情况按一次 `l`（它会按"目标 90°"重新对齐）。
def _step_base_deg(s, per_pulse):
    """一步动作对 base 角度的贡献（正 = 左转/逆时针）。"""
    k = str(s[0]).lower()
    if k in ("base_left", "base_ccw") and len(s) > 1:
        return float(s[1]) * per_pulse
    if k in ("base_right", "base_cw") and len(s) > 1:
        return -float(s[1]) * per_pulse
    if k == "base_turn" and len(s) > 1:
        cw = bool(s[2]) if len(s) > 2 else False
        return (-1.0 if cw else 1.0) * float(s[1]) * per_pulse
    if k == "axis" and len(s) > 3 and int(s[1]) == 4:      # axis 4 = base；dir 0=正转(左)
        return (1.0 if int(s[2]) == 0 else -1.0) * float(s[3]) * per_pulse
    return 0.0


def net_base_deg(steps, base90=None):
    """一组动作对 base 角度的**净变化**（度）。"""
    per = 90.0 / float(base90 or BASE_90 or 4800)
    return sum(_step_base_deg(list(st), per) for st in (steps or []))


def goto_base_deg(car, cur_deg, tgt_deg, note="", base90=None, ttl_vel=None):
    """把 base **绝对**转到 tgt_deg（正 = 左转/逆时针），返回转完的角度。

    ★为什么不能发"相对 N 脉冲"：相对转动隐含了"此刻 base 正好在预期角度"这个前提。
      现场经常是在**初始化位置（0°）**直接操作的，一发相对脉冲就跑到反方向去
      —— 2026-09-17 车主"点 1 却向右转"就是这么来的。

    ★2026-09-18：`base90` / `ttl_vel` 允许调用方**显式传入自己的参数**
      （place_demo 现在自带一份参数区，不再 import 本文件的常量）。
      不传 = 用本文件的模块级 BASE_90 / TTL_VEL，老调用方式完全不受影响。
    """
    b90 = float(BASE_90) if base90 is None else float(base90)
    vel = int(TTL_VEL) if ttl_vel is None else int(ttl_vel)
    delta = float(tgt_deg) - float(cur_deg)
    p = int(round(abs(delta) * b90 / 90.0))
    if p < 1:
        car.log("  → %s：base 已在 ≈%.0f°（目标 %.0f°）→ 不动" % (note or "定位", cur_deg, tgt_deg))
        return float(tgt_deg)
    car.log("  → %s：base %.0f° → %.0f°（%s %d 脉冲 ≈ %.0f°）"
            % (note or "定位", cur_deg, tgt_deg,
               "左转/逆时针" if delta > 0 else "右转/顺时针", p, abs(delta)))
    if delta > 0:
        car.base_left(p, vel)
    else:
        car.base_right(p, vel)
    return float(tgt_deg)


RACK_BASE_KEYS = ("base_left", "base_right", "base_cw", "base_ccw", "base_turn")


def aim_and_track(car, a, base_deg, tag="AIM", mode=None, base90=None):
    """瞄准 + **把 base 偏移记进软跟踪**，返回 (ok, 新的 base_deg, 本次瞄准的 offset)。

    ★为什么必须记账（2026-09-17 车主问题 3："放置完物料转回的瞄准位应该是**进行瞄准操作
      之前**的位置，而不是瞄准**之后**的位置"）：
      瞄准（converge）会微调 base —— offset_base 通常是几十~几百脉冲（几度）。
      而 base 没有角度反馈，"位置"完全靠 grab_demo 里的 base_deg 软跟踪；
      可瞄准走的是 car.aim()，**不经过** goto_base_deg()，于是以前这笔账根本没记。
      记上这一笔后，放料流程的 base 两步才准。

    ★第三个返回值 offset 要留着：放料前用它做一次"归零"（见 rack_zero()），
      把瞄准挪走的 base / **伸缩**原样还回去 —— 尤其是伸缩，动作组里是相对位移，
      不归零的话每次落点都不一样。
    """
    b90 = float(BASE_90) if base90 is None else float(base90)   # ★允许调用方传自己的
    ok, off = car.aim(mode=mode or "color", color=a.color, tag=tag)
    dp = int(off.get("base", 0) or 0)
    if dp:
        deg = dp * 90.0 / b90
        base_deg += deg
        car.log("  → 软跟踪：瞄准把 base 挪了 %+d 脉冲（≈%+.1f°）→ base≈%.0f°"
                % (dp, deg, base_deg))
    ap = int(off.get("arm", 0) or 0)
    if ap:
        car.log("  → 软跟踪：瞄准把**伸缩**挪了 %+d 脉冲（放料前会归零还回去）" % ap)
    return ok, base_deg, off


def rack_zero(car, base_deg, aim_off, work_deg=None, enable=True, base90=None):
    """放料前的"归零"：把瞄准偏移**原样还回去**，回到一个确定的起点。

    ★★车主 2026-09-18："我感觉你程序里的放置时转动的角度以及伸缩臂伸缩的距离是仅仅
      相对于瞄准完之后的新位置进行移动的……先做一次归零（回到左侧 90 度工位）然后再往
      载物台放行吗，这样更稳定" —— **完全正确**，这是个真问题：

      · **base**：动作组里已是**绝对角定位**（goto_base_deg），本来就不受瞄准偏移影响 ✓
      · **伸缩**：动作组里 ("arm_in", RACK_ARM_IN) 是**相对**位移！瞄准会用伸缩轴修画面 y
        （offset_arm 常有几百脉冲），于是缩回的落点变成「原位 + 瞄准偏移 − RACK_ARM_IN」
        → **每次都不一样**，重复性差，还可能撞物架 ✗
      · **龙门**：瞄准不动龙门，所以高度本来就是干净的 ✓

      做法：`car.replay(aim_off, sign=-1)` 把瞄准挪走的 base / 伸缩反着走一遍，
      精确回到"瞄准之前"的状态；再确认 base 在 90° 工作位。
      之后放料动作组从**同一个确定的起点**出发，每次一致 ✓

    返回归零后的 base 角度。
    """
    work = RACK_WORK_DEG if work_deg is None else float(work_deg)
    ob = int((aim_off or {}).get("base", 0) or 0)
    oa = int((aim_off or {}).get("arm", 0) or 0)
    if not enable:
        car.log("  [归零] 已关闭（RACK_HOME_BEFORE=False）→ 直接从当前位置放料")
        return base_deg
    if not ob and not oa:
        car.log("  [归零] 没有瞄准偏移要还，只确认 base 在 %.0f° 工作位" % work)
        return goto_base_deg(car, base_deg, work, "归零到 %.0f° 作业位" % work, base90=base90)
    before = float(base_deg) - ob * 90.0 / (float(BASE_90) if base90 is None else float(base90))
    car.log("  [归零] 把瞄准偏移原样还回去：base %+d / 伸缩 %+d 脉冲" % (ob, oa))
    car.replay({"base": ob, "arm": oa}, sign=-1, tag="-zero")
    return goto_base_deg(car, before, work, "归零到 %.0f° 作业位" % work, base90=base90)


def _run_rack(car, steps, n, base_deg, turn_deg=None, work_deg=None, prefix="",
              what="放物架"):
    """执行"物架号位"动作组：**base 那两步改成绝对角定位**，其余步骤照原顺序单步执行。

    · `what` 只影响日志文案 —— grab_demo 是"放物架"，place_demo 反向用它"夹料"，
      不区分的话现场日志会读成"明明是夹料却写着放物架"。
    返回 (执行后的 base 角度, 成功步数, 总步数)。
    """
    work = RACK_WORK_DEG if work_deg is None else float(work_deg)
    turn = RACK_TURN_DEG[n] if turn_deg is None else float(turn_deg)
    tgt = work - turn
    steps = list(steps or [])
    car.log("  %s[%s] %d 号位：base 目标角 = %.0f°（%.0f° 作业位顺时针 %.0f°）"
            % (prefix, what, n, tgt, work, turn))
    n_ok = 0
    for i, st in enumerate(steps, 1):
        k = str(list(st)[0]).strip().lower()
        if k in RACK_BASE_KEYS:
            if k in ("base_right", "base_cw"):       # 动作组里"去号位"的那一步
                base_deg = goto_base_deg(car, base_deg, tgt, "第%d步 转到 %d 号位" % (i, n))
            else:                                    # "回作业位"的那一步
                base_deg = goto_base_deg(car, base_deg, work, "第%d步 回 %.0f° 作业位" % (i, work))
            n_ok += 1
        else:
            # ★脉冲自检（2026-09-18 加）：标定的时候很容易把某一步的脉冲填成 0 或负数
            #   （最常见就是"抬高量/下放量"填反）。下位机的 pack_ttl 会直接抛
            #   `PackError('32位越界: …')` → **异常打断整组** →
            #   症状是"**放完料之后没有后续动作**"（现场就踩到过：第 6 步 -1800）。
            #   这里先自检：坏参数只**跳过这一步**并告警，后面的步骤照跑，便于定位。
            bad = None
            if len(list(st)) > 1 and k != "wait":
                try:
                    _pv = int(list(st)[1])
                    if _pv <= 0:
                        bad = _pv
                except (TypeError, ValueError):
                    bad = list(st)[1]
            if bad is not None:
                car.log("  %s!! 第 %d 步 %r 的脉冲不合法（= %s，必须 > 0）→ **跳过这一步**，"
                        "后面的步骤继续（请查参数区）" % (prefix, i, list(st), bad))
                continue
            car.log("  %s[%d/%d] %r" % (prefix, i, len(steps), list(st)))
            if car.apply_step(st):
                n_ok += 1
    return base_deg, n_ok, len(steps)


def show_steps(steps, indent="   "):
    for i, st in enumerate(steps, 1):
        print("%s%2d. %s" % (indent, i, list(st)))


def as_code(steps, name="STEPS"):
    lines = ["%s = [" % name]
    for st in steps:
        st = list(st)
        args = ", ".join(repr(x) for x in st)
        if len(st) == 1:
            args += ","
        lines.append("    (%s)," % args)
    lines.append("]")
    return "\n".join(lines)


def build_car(a):
    """连车 + 装相机 + 注入参数（与主工程同一套瞄准参数）。

    ★参数注入必须在 use_camera() **之前**：ColorVision 是在 use_camera() 里创建的，
      它那一刻就去读 COLOR_EMA / COLOR_MED_N / COLOR_JUMP_PX 这些滤波参数；
      注入晚了，颜色滤波用的就还是 school_config 的默认值、而不是这里的参数区值。
    """
    SC.K_PX2STEP_BASE = K_BASE
    SC.K_PX2STEP_ARM = K_ARM
    for _k in AIM_KEYS + VISION_KEYS:
        setattr(SC, _k, globals()[_k])
    car = CAR.Car(port=a.port, baud=a.baud, dry_run=a.dry_run)
    car.use_camera(cam_index=CAM_INDEX, perf=PERF, exposure=CAM_EXPOSURE,
                   rotate=CAM_ROTATE, width=0)
    # ★画面方向保险：K_BASE/K_ARM 是按 none（原图）标定的，方向一变 x/y 就对调 → 瞄准必摇摆
    _rm = getattr(car.vision, "rotate_mode", None)
    if _rm not in (None, "none"):
        print("  !! 画面方向 = %s（不是 none）—— K 值是按 none 标定的，"
              "方向不对会让 **x/y 轴对调**、瞄准来回摆！" % _rm)
    elif _rm == "none":
        print("  [camera] 画面方向 = none ✓（与 K 值标定坐标系一致）")
    return car


def show_aim():
    """打印当前**实际生效**的瞄准 / 滤波参数（SC 上的值）。"""
    for k in ("K_PX2STEP_BASE", "K_PX2STEP_ARM") + AIM_KEYS + VISION_KEYS:
        print("   %-17s = %s" % (k, getattr(SC, k, "?")))


def step_run(car, steps):
    """单步跑：回车=执行，s=跳过，r=重跑上一步，q=退出。"""
    print("   单步模式：回车=执行该步，s=跳过，r=重跑上一步，q=退出单步")
    i = 0
    while 0 <= i < len(steps):
        st = steps[i]
        try:
            cmd = input("   [%d/%d] %s > " % (i + 1, len(steps), list(st))).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if cmd == "q":
            return
        if cmd == "r":
            i = max(0, i - 1)
            continue
        if cmd == "s":
            print("      跳过")
            i += 1
            continue
        r = car.apply_step(st)
        print("      %s" % ("完成" if r else "未执行"))
        i += 1


def manual(car):
    """手动发单个动作（不确定动作组怎么写时，先用它试出单个轴该走多少）。"""
    print("   手动发单个动作：")
    print("     b  base 转（左/右 + 脉冲）      l 龙门（上/下 + 脉冲）")
    print("     a  伸缩（伸/收 + 脉冲）          g  夹爪（开/关/角度）")
    print("     w  等待 ms                      Enter 取消")
    k = (input("   选哪个 > ").strip().lower() or "")[:1]
    if not k:
        return
    if k == "b":
        d = (input("    方向 l=左转 c=右转 > ").strip().lower() or "l")[:1]
        p = int(input("    脉冲 > ").strip() or "1700")
        car.apply_step(("base_left", p, TTL_VEL) if d == "l" else ("base_right", p, TTL_VEL))
    elif k == "l":
        d = (input("    方向 d=下放 u=抬起 > ").strip().lower() or "d")[:1]
        p = int(input("    脉冲 > ").strip() or str(LIFT_DOWN))
        car.apply_step(("lift_down", p, LIFT_DOWN_VEL) if d == "d" else ("lift_up", p, LIFT_UP_VEL))
    elif k == "a":
        d = (input("    方向 o=伸出 i=收回 > ").strip().lower() or "o")[:1]
        p = int(input("    脉冲 > ").strip() or str(RACK_ARM_OUT))
        car.apply_step(("arm_out", p, ARM_OUT_VEL) if d == "o" else ("arm_in", p, ARM_IN_VEL))
    elif k == "g":
        d = (input("    开/关/角度(0-255) > ").strip().lower() or "o")[:1]
        if d == "o":
            car.apply_step(("grip_open",))
        elif d == "c":
            car.apply_step(("grip_close",))
        else:
            try:
                car.apply_step(("grip_angle", max(0, min(255, int(d)))))
            except ValueError:
                print("    !! 角度要是 0~255 的数字")
    elif k == "w":
        ms = int(input("    毫秒 > ").strip() or "500")
        car.apply_step(("wait", ms))


def jitter_test(car, a, n=30, dt=0.15):
    """静置抖动测试：**不动电机**，连续采帧，看目标中心自己跳多厉害。

    ★为什么先做这个：瞄准"摇摆"有两种**完全不同**的病根 ——
        ① 视觉噪声：cx 帧间自己就跳 > 容差 → 闭环在追噪声 → 再怎么调控制参数都没用；
        ② 控制/机械：视觉很稳，但一动就冲过头（超调）→ 改速度 / K 值 / 限幅。
      不先分清就改参数 = 瞎调。用 `v` 就是干这个的。
    """
    mode = "color" if (a.color and car.color_vision is not None) else "ring"
    n = max(3, int(n))
    # ★★先张爪（同 couple_calib）：否则测到的是挡在镜头前的夹爪，不是物料
    try:
        car.grip_open()
        time.sleep(0.6)
    except Exception:                                            # noqa: BLE001
        pass
    print("  静止采样 %d 帧（**不动电机**，%s 模式）…" % (n, mode))
    xs, ys = [], []
    for i in range(n):
        loc = car.locate(mode=mode, color=a.color)
        if not loc.get("found"):
            print("   [%2d/%d] 未找到目标（stale=%s）" % (i + 1, n, loc.get("stale")))
            time.sleep(dt)
            continue
        xs.append(float(loc["cx"]))
        ys.append(float(loc["cy"]))
        print("   [%2d/%d] cx=%7.1f cy=%7.1f r=%5.1f 圆度=%.2f 填充=%.2f 占比=%.1f%% conf=%.2f stale=%s"
              % (i + 1, n, loc["cx"], loc["cy"], loc.get("r", 0),
                 loc.get("circ", 0.0), loc.get("fill", 0.0),
                 100.0 * loc.get("area_frac", 0.0), loc.get("conf", 0),
                 loc.get("stale")))
        time.sleep(dt)
    if len(xs) < 3:
        print("  !! 有效帧只有 %d 帧 —— 先把视觉调通（曝光 %s / 目标在画面里）再测"
              % (len(xs), CAM_EXPOSURE))
        return
    import statistics as st
    tol = float(SC.AIM_TOL_PX)
    sx, sy = st.pstdev(xs), st.pstdev(ys)
    print("  ── 静止抖动统计（%d 帧）──" % len(xs))
    print("     cx: 均值 %7.1f   σ %.2f   极差 %.1f" % (st.mean(xs), sx, max(xs) - min(xs)))
    print("     cy: 均值 %7.1f   σ %.2f   极差 %.1f" % (st.mean(ys), sy, max(ys) - min(ys)))
    jit = max(sx, sy) * 3.0                       # 3σ 当作"最坏抖动"
    print("     3σ 抖动 %.1fpx   收敛容差 %.0fpx" % (jit, tol))
    if jit > tol:
        print("     !! 3σ 抖动 > 容差 → **视觉噪声是主因**：闭环在追噪声，永远稳不下来。")
        print("        先治视觉：曝光(CAM_EXPOSURE)/掩膜阈值/EMA/jump；急用可把 AIM_TOL_PX 放大到 3σ 之上。")
    else:
        print("     ✓ 3σ 抖动 ≤ 容差 → 视觉够稳，摇摆出在**控制/机械**侧。")
        print("        接着看 `a` 的日志：dx 是否来回翻（超调）、单拍脉冲多大、速度是否太猛。")


def _avg_loc(car, a, mode, n=4, dt=0.25):
    """连续采 n 帧取平均（单帧有 1~2px 噪声，平均后才看得清方向）。
    返回 (cx, cy, r) —— r 用来记"这次测量所在的距离"，也就是 K_R_REF。
    ★2026-09-18：n 从 3 加到 4、间隔 0.2→0.25（共 1s），标定时读数更稳。"""
    xs, ys, rs = [], [], []
    for _ in range(n):
        loc = car.locate(mode=mode, color=a.color)
        if loc.get("found"):
            xs.append(float(loc["cx"]))
            ys.append(float(loc["cy"]))
            rs.append(float(loc.get("r") or 0.0))
        time.sleep(dt)
    if not xs:
        return None
    return sum(xs) / len(xs), sum(ys) / len(ys), sum(rs) / len(rs)


def couple_calib(car, a, pulse=200, pulse_arm=None):
    """★★重测 K（px/脉冲）+ 查耦合 + 验方向 —— 一个命令全包。

    ★为什么用它而不是 calibrate_aim.py：
      `calibrate_aim.py` 内部用 aim_and_place 的 CALIB（rotate_ccw=True）把画面**逆时针转了 90°**，
      坐标轴与主程序（CAM_ROTATE=none）差 90° —— 它把 base 的位移记到 y、臂的记到 x，
      算出来的 K 小 20~30 倍，**只能靠人工换算**（school_main.py 里那段注释就是这么来的）。
      本命令走的是**主程序自己的相机和坐标系**（cam_rotate + prepare），量出来直接可用，不用换算。

    ★base 和 arm 的脉冲数**要分开给**：
      K_ARM(0.0385) 只有 K_BASE(0.213) 的 1/5.5，同样脉冲数 arm 的位移只有 base 的 1/5。
      所以 base 用 200（≈43px）就够，arm 要 800（≈31px）信号才够大。
      **用法：`c 200 800`**（只给一个数则两边相同）。

    ⚠️ 会**真的动电机**（base ±N、伸缩 ±N 脉冲，各自双向一次后回位），先确认周围清空。
    ⚠️ 请在**实际瞄准那个距离**上测 —— K 随距离变，测完把当时的 r 一起填进 K_R_REF。
    """
    pb = int(pulse)
    pa = int(pulse_arm if pulse_arm is not None else pulse)
    mode = "color" if (a.color and car.color_vision is not None) else "ring"
    # ★★2026-09-18 **必须先张爪**：夹爪半开/闭合时爪片挡在镜头前，颜色检测到的"目标"
    #   其实是**夹爪附近那块色**（或爪片反光），而夹爪**随 base/arm 一起动** →
    #   转完一圈它在画面里几乎不动 → 标出来的 K 接近 0、耦合也乱。
    #   实测踩到过：base 400 脉冲只走 0.9px（正常应 162px）、耦合 base→y=0.38 / arm→x=0.57
    #   （正常 ±0.02）、目标半径还从 112 变成 155（更近更大 = 更像爪子自己）。
    try:
        car.grip_open()                       # 张到初始开度，跟瞄准前的动作一致
        time.sleep(0.6)
    except Exception:                                            # noqa: BLE001
        pass
    p0 = _avg_loc(car, a, mode)
    if p0 is None:
        print("  !! 看不到目标 —— 先把物料/靶放到正常位置、调好曝光，再跑标定")
        return
    print("  [K 值标定] base ±%d 脉冲 / arm ±%d 脉冲，每步停 1.8s 后取 4 帧平均（%s 模式）"
          % (pb, pa, mode))
    print("   起点   cx=%7.1f  cy=%7.1f  r=%.1f（r 就是这次的距离，等下填 K_R_REF）"
          % (p0[0], p0[1], p0[2]))

    def _step(axis, d, note, n_pulse):
        vel = AIM_VEL_BASE if axis == 4 else AIM_VEL_ARM
        car.lower.ttl(axis, d, vel, int(n_pulse))
        time.sleep(1.8)                       # ★2026-09-18 从 1.0 加长：1s 常常还没到位，
                                              #   后一步就会读到前一步的中间态 → 看起来"两个方向
                                              #   都朝同一方向累积" → 双向差分必然算出近 0 的 K
        p = _avg_loc(car, a, mode)
        if p is None:
            print("   %-22s → 丢目标" % note)
            return None
        # ★关键：位移要按**相对上一步**算（相对固定起点会被前面步骤的残留污染）
        d_cx, d_cy = p[0] - _step.prev[0], p[1] - _step.prev[1]
        print("   %-22s cx=%7.1f cy=%7.1f   Δ上一步=(%+6.1f,%+6.1f)  离起点=(%+6.1f,%+6.1f)"
              % (note, p[0], p[1], d_cx, d_cy, p[0] - p0[0], p[1] - p0[1]))
        _step.prev = p
        return (p[0] - p0[0], p[1] - p0[1]), (d_cx, d_cy)

    _step.prev = p0
    r = {}
    r["b+"] = _step(4, 0, "base 正转 FWD", pb)
    r["b-"] = _step(4, 1, "base 反转 REV(回位)", pb)
    r["a+"] = _step(1, 0, "arm  正转 FWD", pa)
    r["a-"] = _step(1, 1, "arm  反转 REV(回位)", pa)
    if any(v is None for v in r.values()):
        print("  !! 中途丢目标，结果不完整 —— 换个距离或调曝光重跑")
        return

    print("  ── 逐轴净效果（看『Δ上一步』那一列，这才是每个方向真正的位移）──")
    print("     base: FWD %+.1fpx  REV %+.1fpx   (两者应**一正一负**、大小接近)"
          % (r["b+"][1][0], r["b-"][1][0]))
    print("     arm : FWD %+.1fpx  REV %+.1fpx" % (r["a+"][1][1], r["a-"][1][1]))

    # ★回位校验：REV 完之后应该回到起点附近，否则说明"没等到位"或机械回差大
    back_b = abs(r["b-"][0][0]) + abs(r["b-"][0][1])
    back_a = abs(r["a-"][0][0]) + abs(r["a-"][0][1])
    if back_b > 15.0 or back_a > 15.0:
        print("  ⚠️ 回位不准（base 离起点 %.0fpx / arm 离起点 %.0fpx）—— 说明每一步还没停稳就读了，"
              "或者机械回差大。" % (back_b, back_a))
        print("     建议：把脉冲**再加大**（信号更强）重测；若仍不准，先跑 `v 40` 看视觉是否稳。")

    # ── 折算 px/脉冲 ──
    # ★★2026-09-18 修：原来用 (离起点的FWD − 离起点的REV)/(2N)，**少算了一半** ——
    #    FWD 之后"离起点"= +d，REV 之后"离起点"≈0，两者之差只有 d（不是 2d），
    #    再除以 2N 就得到 d/(2N)，只有真值的一半。
    #    （车主 2026-09-18 实测：base 400 脉冲走出 162.7px → 真值 0.407，工具却报 0.2015。）
    #    正确做法：用**每步相对上一步**的位移做双向平均 —— 这才是"一去一回"消回差：
    #        K = (|ΔFWD| + |ΔREV|) / (2N)，符号取 FWD 的方向。
    def _k_from(d_fwd, d_rev, n):
        if not d_fwd and not d_rev:
            return 0.0
        k = (abs(d_fwd) + abs(d_rev)) / (2.0 * max(1, n))
        return k if d_fwd > 0 else -k

    bx = _k_from(r["b+"][1][0], r["b-"][1][0], pb)
    by = _k_from(r["b+"][1][1], r["b-"][1][1], pb)
    ax = _k_from(r["a+"][1][0], r["a-"][1][0], pa)
    ay = _k_from(r["a+"][1][1], r["a-"][1][1], pa)
    print("  ── 折算 px/脉冲（正转 = dir0）──")
    print("     base: Δcx/脉冲 = %+0.4f   Δcy/脉冲 = %+0.4f" % (bx, by))
    print("     arm : Δcx/脉冲 = %+0.4f   Δcy/脉冲 = %+0.4f" % (ax, ay))
    print("     当前配置：K_PX2STEP_BASE=%.4f（base→x）  K_PX2STEP_ARM=%.4f（arm→y）"
          % (SC.K_PX2STEP_BASE, SC.K_PX2STEP_ARM))

    # ---- 信号强度自检：太小就是脉冲给少了，测出来会被噪声淹没 ----
    sig_warn = []
    if abs(bx) * pb < 8.0:
        sig_warn.append("base 位移仅 %.1fpx（<8px）" % (abs(bx) * pb))
    if abs(ay) * pa < 8.0:
        sig_warn.append("arm 位移仅 %.1fpx（<8px）" % (abs(ay) * pa))
    if sig_warn:
        print("  ⚠️ 信号太小：%s → 结果会被噪声主导，请**加大脉冲**重测"
              % "、".join(sig_warn))
        print("     （base 建议 200~400，arm 建议 800~1500）")

    print("  ── 判定 ──")
    # ★先看"两个方向是否一正一负" —— 同号说明**根本没测到位**，后面的 K 全是废数
    #   （2026-09-18 车主真跑 `c 200 800` 时就是这种情况：FWD +24.0 / REV +14.8 同为正）
    fwd_b, rev_b = r["b+"][1][0], r["b-"][1][0]
    fwd_a, rev_a = r["a+"][1][1], r["a-"][1][1]
    bad = []
    if fwd_b * rev_b > 0:
        bad.append("base(FWD %+.1f / REV %+.1f 同号)" % (fwd_b, rev_b))
    if fwd_a * rev_a > 0:
        bad.append("arm (FWD %+.1f / REV %+.1f 同号)" % (fwd_a, rev_a))
    if bad:
        print("     !! **本次数据不可信**：%s" % "；".join(bad))
        print("        正常应该一正一负（一去一回）。同号 = 每步没停稳就读了、或命令没生效。")
        print("        请：① 加大脉冲 `c 400 1500` 重测；② 先 `v 40` 确认视觉稳。")
    base_ok = abs(bx) > abs(by)
    arm_ok = abs(ay) > abs(ax)
    print("     %s base 主要改 cx" % ("✓" if base_ok else "!! "))
    print("     %s arm  主要改 cy" % ("✓" if arm_ok else "!! "))
    if bx:
        print("     耦合 base→(y/x) = %+.2f" % (by / bx))
    if ay:
        print("     耦合 arm →(x/y) = %+.2f" % (ax / ay))
    if bx < 0:
        print("     ⚠️ base 正转让 cx 变小 → 方向与假设相反，应把 AIM_FLIP_BASE 设 True")
    if ay < 0:
        print("     ⚠️ arm  正转让 cy 变小 → 方向与假设相反，应把 AIM_FLIP_ARM 设 True")

    print("  ══ 建议写回 grab_demo.py 参数区（本车实测）══")
    print("     K_BASE  = %.4f      # 原 %.4f" % (abs(bx), SC.K_PX2STEP_BASE))
    print("     K_ARM   = %.4f      # 原 %.4f" % (abs(ay), SC.K_PX2STEP_ARM))
    print("     K_R_REF = %.0f        # 这次测量的目标半径 r，供『按距离加权』用" % p0[2])
    print("     ⚠️ 抄之前先看上面有没有 ⚠️：有就说明信号不足/方向反，别急着改。")


MENU = """
──────────────────────────────────────────────────────────────
 取料 / 放物架 分步调试 demo    颜色=%s   动作组=%s   base≈%.0f°（0=正前方 +90=左侧作业位）
  h 归位(→0°)      l 进作业位(→90°)      a 只瞄准      g 取料一遍(瞄+夹)
  1 / 2 / 3  放物架号位（★base 按**绝对角**定位，不要求先在作业位）
             号位目标角：%s        可加角度：1 60
  A / B / C  一条龙(瞄→夹→放 1/2/3)
  n 切换动作组   s 单步跑当前组   p 打印当前组(可粘回代码)
  v [帧数] 静止抖动测试（★瞄准摇摆先测这个，不动电机）  a 瞄准日志看 dx/dy
  c [base脉冲] [arm脉冲]  ★重测 K 值 / 查耦合 / 验方向（推荐 c 200 800；会动电机）
  k 看瞄准参数（K/容差/限幅/速度）；k VEL_BASE 15 现场改，不用重部署
  e N [脉冲] [速度] 改第N步    i N 名称 [脉冲] [速度] 插入    d N 删除
  m 手动发单个动作    q 退出
──────────────────────────────────────────────────────────────"""


def rack_tgt_line():
    """菜单里显示三个物架号位的**绝对目标角**（跟着 RACK_TURN_DEG 走）。"""
    return "  ".join("%d号→%.0f°" % (n, RACK_WORK_DEG - RACK_TURN_DEG[n]) for n in (1, 2, 3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/stm32")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--color", default="red", help="瞄准目标颜色 red/green/blue")
    ap.add_argument("--dry-run", action="store_true", help="不连车（验证菜单与动作组）")
    ap.add_argument("--init", dest="init", action="store_true", default=None,
                    help="强制做上电初始化动作组（默认按 INIT_ON_START）")
    ap.add_argument("--no-init", dest="init", action="store_false",
                    help="★跳过上电初始化（臂已在起始位时用这个，否则会顶死限位）")
    a = ap.parse_args()

    car = build_car(a)

    # ① 参数下发（底盘档位 / 夹爪角度 / 各轴行程）—— 底盘速度就靠这一步
    if not a.dry_run:
        car.setup(chassis_vel=CHASSIS_VEL, chassis_acc=CHASSIS_ACC, chassis_dec=CHASSIS_DEC,
                  grip_close_angle=GRIP_CLOSE_ANGLE, grip_open_angle=GRIP_OPEN_ANGLE,
                  base90=BASE_90, lift_up=LIFT_UP, lift_down=LIFT_DOWN,
                  telescope=TELESCOPE, ttl_vel=TTL_VEL)
    else:
        print("[setup] dry-run：跳过参数下发")

    # ② 上电初始化（把机械臂送到起始位）
    do_init = INIT_ON_START if a.init is None else bool(a.init)
    if do_init:
        print("[init] 上电初始化 %d 步（整组交下位机执行）：%s" % (len(INIT_STEPS), INIT_STEPS))
        if not car.init_axes(INIT_STEPS):
            print("!! 初始化没跑成功 —— 臂不在起始位的话，后面所有动作都是歪的")
    else:
        print("[init] 跳过初始化（--no-init）：臂应已停在起始位")
    groups = {"PICK": list(PICK_STEPS), "RACK1": list(RACK_STEPS[1]),
              "RACK2": list(RACK_STEPS[2]), "RACK3": list(RACK_STEPS[3])}
    cur = "PICK"
    # ★base 角度软跟踪：初始化结束 = 正前方 0°（INIT_STEPS 最后一步 base 右转回正）
    base_deg = 0.0
    # 最近一次瞄准产生的偏移（base/arm 脉冲）—— 放料前用它"归零"，见 rack_zero()
    last_aim_off = {"base": 0, "arm": 0}
    print("=" * 66)
    print("取料 / 放物架 分步调试 | 颜色=%s | %s"
          % (a.color, "dry-run（不动电机）" if a.dry_run else "真跑模式"))
    print("建议顺序：h 归位 → l 进作业姿态 → a 只瞄看收敛 → g 取料 → 1/2/3 试放物架")
    print(MENU % (a.color, cur, base_deg, rack_tgt_line()))
    try:
        while True:
            try:
                raw = input("grab> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not raw:
                continue
            p = raw.split()
            c = p[0].lower()
            try:
                if c in ("q", "quit"):
                    break
                elif c in ("h", "home"):
                    # ★绝对归位：不能无脑右转 90° —— 初始化后 base 在正前方 0°，
                    #   那时再右转 90° 会跑到**右侧** 90°（2026-09-17 现场就是这么撞上的）。
                    base_deg = goto_base_deg(car, base_deg, 0.0, "归位到正前方")
                elif c in ("l", "left"):
                    # 绝对定位到"左侧 90° 作业位"：已在位就不动，免得再转 90° 变成 180°
                    base_deg = goto_base_deg(car, base_deg, RACK_WORK_DEG, "进左侧作业位")
                elif c in ("a", "aim") and p[0] not in ("A", "B", "C"):
                    # ★注意 p[0] 的原样判断：c 是 lower() 过的，大写的 A/B/C 会被当成 "a"
                    #   而误进瞄准分支 → 所以一条龙用 p[0] 原样区分（2026-09-17 发现的老 bug）
                    ok, base_deg, last_aim_off = aim_and_track(car, a, base_deg, tag="AIM")
                    print("  ► 瞄准：收敛=%s   base 现在 ≈%.0f°（偏移已记账，放料前会归零）"
                          % (ok, base_deg))
                elif c in ("g", "grab"):
                    # ★瞄准单独走一步：这样可以拿到 offset 并记进 base 软跟踪
                    #   （pick_here 内部瞄准时 offset 不外传，会导致后面转号位的角度偏掉）
                    _ok_a, base_deg, last_aim_off = aim_and_track(car, a, base_deg, tag="PICK")
                    if not _ok_a:
                        print("  !! 瞄准失败 → 盲夹（物料位置固定时的兜底）")
                    ok = car.pick_here(expect_color=a.color, tag="PICK",
                                       steps=groups["PICK"], blind=True, aim=False)
                    print("  ► 取料：%s   base≈%.0f°" % ("成功" if ok else "失败", base_deg))
                elif c in ("1", "2", "3"):
                    n = int(c)
                    # 可选第二参数：直接给"从工作位顺时针多少度"（覆盖 RACK_TURN_DEG）
                    turn = float(p[1]) if len(p) > 1 else None
                    # ★放料前先归零（车主 2026-09-18 建议）：把瞄准挪走的 base/伸缩偏移
                    #   原样还回去 → 从确定的起点（90° 工作位 + 伸缩原位）出发，每次一致
                    base_deg = rack_zero(car, base_deg, last_aim_off, enable=RACK_HOME_BEFORE)
                    last_aim_off = {"base": 0, "arm": 0}
                    base_deg, n_ok, n_all = _run_rack(car, groups["RACK%d" % n], n,
                                                      base_deg, turn_deg=turn)
                    print("  ► 放物架 %d 号位：执行 %d/%d 步，base 现在 ≈%.0f°"
                          % (n, n_ok, n_all, base_deg))
                elif p[0] in ("A", "B", "C") or c in ("a1", "a2", "a3"):
                    # ★用 p[0] 原样判断（c 已 lower，"A" 会变成 "a" 被瞄准分支抢走）
                    #   ⚠️ 别写 dict.get(p[0], int(p[0][-1])) —— 默认值是**立即求值**的，
                    #      输入 "A" 时会先算 int("A") 抛 ValueError（2026-09-17 抓到）
                    if p[0] in ("A", "B", "C"):
                        n = {"A": 1, "B": 2, "C": 3}[p[0]]
                    else:
                        n = int(p[0][-1])
                    _ok_a, base_deg, last_aim_off = aim_and_track(car, a, base_deg, tag="PICK")
                    ok1 = car.pick_here(expect_color=a.color, tag="PICK",
                                        steps=groups["PICK"], blind=True, aim=False)
                    if not ok1:
                        print("  !! 取料没成功，先别放（可先 g 单测取料）")
                        continue
                    # ★放料前先归零（同 1/2/3）：还掉瞄准偏移，从确定起点出发
                    base_deg = rack_zero(car, base_deg, last_aim_off, enable=RACK_HOME_BEFORE)
                    last_aim_off = {"base": 0, "arm": 0}
                    base_deg, n_ok, n_all = _run_rack(car, groups["RACK%d" % n], n,
                                                      base_deg, prefix="[一条龙] ")
                    print("  ► 一条龙：取料✓ → 放物架 %d 号位（%d/%d 步），base 现在 ≈%.0f°"
                          % (n, n_ok, n_all, base_deg))
                elif c in ("n", "next"):
                    order = ["PICK", "RACK1", "RACK2", "RACK3"]
                    cur = order[(order.index(cur) + 1) % len(order)]
                    print("  当前动作组 → %s（%d 步）" % (cur, len(groups[cur])))
                elif c in ("s", "step"):
                    step_run(car, groups[cur])
                elif c in ("p", "print"):
                    print("  当前动作组 %s（%d 步）：" % (cur, len(groups[cur])))
                    show_steps(groups[cur])
                    print("  ── 可直接粘回 grab_demo.py 参数区 ──")
                    print(as_code(groups[cur], name="%s_STEPS" % cur))
                elif c in ("e", "edit"):
                    i = int(p[1]) - 1
                    st = list(groups[cur][i])
                    if len(p) > 2:
                        st[1] = int(p[2])
                    if len(p) > 3 and len(st) > 2:
                        st[2] = int(p[3])
                    groups[cur][i] = tuple(st)
                    print("  已改第 %d 步 → %s" % (i + 1, list(groups[cur][i])))
                elif c in ("i", "insert"):
                    i = int(p[1])
                    name = p[2]
                    extra = tuple(int(x) for x in p[3:])
                    groups[cur].insert(i, (name,) + extra)
                    print("  已插入 → %s" % (list(groups[cur][i]),))
                elif c in ("d", "del"):
                    i = int(p[1]) - 1
                    print("  已删除：%s" % (groups[cur].pop(i),))
                elif c in ("m", "manual"):
                    manual(car)
                    print("   （手动动过 base 的话，上面那个 base≈ 估算会失准 → 按一次 l 重新对齐）")
                elif c in ("k", "kaim"):
                    if len(p) < 3:
                        print("  ── 当前瞄准参数（SC 实际生效值）──")
                        show_aim()
                        print("  改法：k VEL_BASE 15 / k OUT_MAX_PX 25 / k TOL_PX 12 / k SETTLE_S 0.8")
                    else:
                        name = p[1].upper()
                        if not name.startswith(("AIM_", "K_")):
                            name = "AIM_" + name
                        if not hasattr(SC, name):
                            print("  !! 没有这个参数：%s" % name)
                        else:
                            cur = getattr(SC, name)
                            val = float(p[2])
                            new = int(val) if isinstance(cur, int) else val
                            setattr(SC, name, new)
                            print("   %s: %s → %s" % (name, cur, new))
                elif c in ("c", "couple") and p[0] not in ("A", "B", "C"):
                    couple_calib(car, a,
                                 pulse=int(p[1]) if len(p) > 1 else 200,
                                 pulse_arm=int(p[2]) if len(p) > 2 else None)
                elif c in ("v", "verify", "jitter"):
                    jitter_test(car, a, n=int(p[1]) if len(p) > 1 else 30)
                elif c in ("x", "stop"):
                    car.stop()
                    print("  已停车")
                else:
                    print("  未知命令（输 p 看动作组，或看上面的菜单）")
            except (IndexError, ValueError) as exc:
                print("  !! 参数不对：%r" % exc)
            except Exception as exc:                                 # noqa: BLE001
                print("  !! 命令出错：%r" % exc)
            print(MENU % (a.color, cur, base_deg, rack_tgt_line()))
    finally:
        print("收工：打印全部动作组（可抄回 grab_demo.py 参数区）")
        for k in ("PICK", "RACK1", "RACK2", "RACK3"):
            print(as_code(groups[k], name="%s_STEPS" % k))
        car.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
