#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
place_demo.py —— 加工区「从车载物架夹料 → 瞄准标靶 → 放置」分步调试器
==================================================================================
和 grab_demo.py 是一对：
    grab_demo  = 原料区：瞄准**物料** → 夹取 → 放到**车载物架**
    place_demo = 加工区：从**车载物架**夹料 → 瞄准**标靶** → 放置

★★工序顺序（2026-09-18 车主纠正，**这一版改了**）：
      ① 进 90° 作业位
      ② 瞄准标靶（记下本次瞄准偏移 off）
      ③ **归零** —— 把 off 原样还回去，回到"瞄准之前"那个确定的起点
      ④ 去 1/2/3 号置物架夹料（动作组从这个确定起点出发 → 每次一致 ✔）
      ⑤ **回放** —— 把 off 正着再走一遍，精确回到刚瞄准好的靶心
      ⑥ 下放 → 松爪 → 抬回（放置）

  为什么不能"先夹料、再瞄准"（上一版的错）：
      瞄准一定会微调 base 和**伸缩**（offset 常有几百脉冲），而夹料动作组里的
      伸缩是**相对**位移 —— 起点不干净时每次落点都不一样；反过来"先瞄 → 归零 →
      夹料 → 把 off 补回来"，夹料完就精确回到靶心。这与现场验证过的
      `aim.py` 里 `place_mission()` 完全同构：
          converge → replay(off, -1) → BUILTIN_GRAB_VEH → replay(off, +1) → BUILTIN_PLACE
      对应本 demo 的按键：  a → z → P → b → 0

═══════════════════════════════════════════════════════════════════════════════
★标靶颜色（车主 2026-09-18）：**与市赛不同，1 / 2 / 3 号靶位分别是 红 / 绿 / 蓝**。
  见下面 TARGET_COLOR。

★★"从载物台夹取"的动作组 = **把 grab_demo 放到物架上的那 9 步整组反过来**
  （车主原话："具体从载物台夹取的路径你直接反向照抄 grab 里放到置物架上的过程就行"）。

  原放料（起点 = 瞄准高度 H，参数 UP=2800 DOWN=1000 CLEAR=300，
          BACK = DOWN-UP-CLEAR = -2100，即第⑨步是"下放 2100"）：
     ① lift_up   +UP(2800)              → H+2800
     ② base 顺时针转到号位
     ③ arm 缩回
     ④ lift_down -DOWN(1000)            → H+1800   ← **料位**
     ⑤ grip_open 松爪
     ⑥ lift_up   +CLEAR(300)            → H+2100
     ⑦ arm 伸出
     ⑧ base 逆时针转回 90° 工作位
     ⑨ lift_down -|BACK|(2100)          → H         （净高度 = 0 ✓ 回到瞄准高度）

  反向取料 = 同一条路径**倒着走**、每步位移**取反**：
     ① lift_up   +|BACK|(2100)                    反⑨
     ② base 顺时针转到号位                         反⑧
     ③ arm 缩回                                   反⑦
     ④ lift_down -CLEAR(300)            → H+1800  反⑥  ← **料位**（与放料同一高度 ✓）
     ⑤ grip_close 夹住 ★                          反⑤（松爪→夹住）
     ⑥ lift_up   +DOWN(1000)                      反④
     ⑦ arm 伸出                                   反③
     ⑧ base 逆时针转回 90° 工作位                  反②
     ⑨ lift_down -UP(2800)              → H        反①
     ⇒ 高度账 2100 - 300 + 1000 - 2800 = **0**，收工时正好回到瞄准高度 ✓
═══════════════════════════════════════════════════════════════════════════════

用法（与 grab_demo 完全一致的风格）：
    python3 -u place_demo.py --port /dev/stm32                     # 首次上电：带初始化
    python3 -u place_demo.py --port /dev/stm32 --no-init           # 之后每次
    python3 -u place_demo.py --dry-run                             # 不动电机，验证流程

⚠️ 安全：会动伸缩台 / 龙门架 / base / 夹爪，真发动作前先确认周围清空。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# ★★sys.path 顺序至关重要（2026-09-18 的大坑）：共享模块会把**老工程 `/root/VisualBot`**
#   也塞进 sys.path，而 `/root/VisualBot/校赛/` 下有**同名的旧版 aim.py / car.py / vision.py**
#   → 顺序插错就会命中旧版，三阶段瞄准等新改动**全部静默失效**。
#   → 倒序插入（后插的优先）+ 先清掉重插，保证本项目自己的目录排最前。
for _p in ("/root/VisualBot", "/root/VisualBot2", HERE):
    if not _p or not os.path.isdir(_p):
        continue
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

# ★只复用 grab_demo 的**代码**（连车/装相机/瞄准记账/归零/物架动作组执行器），
#   **不再共用数值** —— 动作脉冲/距离/角度/速度改成 place_demo 自己参数区里的**一份备份**
#   （见下面"0. 本文件自带的一份参数"）。
#   车主 2026-09-18："保存当前参数，将 placedemo 里的各个动作脉冲距离变量重新在
#   placedemo 里做备份，两个程序内容还是不太一样，不能直接链接变量" —— 对：
#     两个 demo 的动作组本来就不一样（grab_demo 是"放到物架"，本文件是它的**反向**取料
#     + 加工区标靶放置），继续 `from grab_demo import RACK_XXX` 会出现"在 grab_demo 里
#     改一个值，本文件偷偷跟着变"，现场排查时分不清是谁在动。
#   → 数值各调各的；改完记得两边分别同步（本文件 `p` 命令可打印自己这一份）。
from grab_demo import (                                          # noqa: E402
    build_car, aim_and_track, rack_zero, _run_rack, goto_base_deg,
    jitter_test, couple_calib, manual, show_aim, show_steps, as_code,
    merge_zero_into_steps,          # ★把"归零"并进动作组第①步（三轴并行，与原料区共用）
)
import school_config as SC                                       # noqa: E402

# ★轴号 / 方向（与 car.py 里的一致；这里就地写一份，省掉一次 import 及其副作用）
AXIS_ARM, AXIS_GANTRY, AXIS_BASE = 1, 2, 4
FWD, REV = 0, 1


class _ColorArg:
    """只为满足 `aim_and_track(car, a, …)` 里 `a.color` 的鸭子类型。

    （route_main 里也有一个同名的小类，但那是给跑图用的；这里自带一份，避免
      place_demo 依赖 route_main —— 那样会连跑图那一整套一起 import 进来。）
    """
    __slots__ = ("color",)

    def __init__(self, color):
        self.color = color


# ==============================================================================
# ★参数区（**只改这一段**）
# ==============================================================================

# ------------------------------------------------------------------------------
# 0. ★本文件**自带**的一份动作参数（2026-09-18 从 grab_demo 现场值抄过来做备份）
# ------------------------------------------------------------------------------
#   为什么要有这一份：两个 demo 的动作组内容不一样，共享常量会互相干扰
#   （在 grab_demo 里调一个值，本文件跟着变，现场分不清谁在动）。
#   ⚠️ 这里**必须与 grab_demo 的当前现场值保持一致**才能动作正确；在任一边改过之后，
#      另一边要手工同步。本文件的 `p` 命令会把这一份打印出来，方便对照抄写。

# ---- base 轴 ----
BASE_90       = 4800      # base 转 90° 的脉冲（1° ≈ 53 脉冲）
TTL_VEL       = 80        # base 转动速度 RPM（goto_base_deg 用它）★2026-09-18 晚 40→80
RACK_WORK_DEG = 90.0      # 作业位 = base 左侧 90°（0° = 车正前方，+ = 左转/逆时针）
RACK_TURN_DEG = {1: 55, 2: 90, 3: 125}   # 从 90° 工作位**顺时针**转过去多少度
                                         #   → 号位绝对角 = 90 − 它：1号=35° 2号=0° 3号=−35°

# ---- 车载物架：取料行程（= grab_demo 放料 9 步的**反向**，见下面 PICK_FROM_RACK_STEPS）----
#   ★高度账（起点 = 瞄准高度 H，要求收工也回到 H）：
#       ① +|RACK_LIFT_BACK|  ② base 转号位   ③ 伸缩缩回
#       ④ −RACK_LIFT_CLEAR   ⑤ 夹住         ⑥ +RACK_LIFT_DOWN
#       ⑦ 伸缩伸出           ⑧ base 回 90°   ⑨ −RACK_LIFT_UP   → 回到 H
RACK_LIFT_UP    = 2800    # ⑨ 反①：最后从料位抬回瞄准高度
RACK_LIFT_DOWN  = 300     # ⑥ 反④：夹住后抬离料位
RACK_LIFT_CLEAR = 300     # ④ 反⑥：从瞄准高度下到**料位**
                          #   ★2026-09-18 车主定稿：300 → 800（现场一度误输成 8000）
                          #   高度账：① 抬 |RACK_LIFT_BACK| = |300 − 2800 − 800| = **3300**
                          #           ④ 下 800  → 料位 = 瞄准高度 + 2500（与 grab_demo 一致 ✓）
                          #           ⑥ 抬 300、⑨ 下 2800 → 收工正好回到瞄准高度 ✓
RACK_LIFT_BACK  = RACK_LIFT_DOWN - RACK_LIFT_UP - RACK_LIFT_CLEAR   # ①（负 = 要往上抬）
RACK_ARM_IN     = 6500    # ③⑦ 伸缩缩回 / 伸出量
RACK_TTL_VEL    = 80      # ②⑧ base 转号位的速度 RPM ★2026-09-18 晚 40→80（翻倍）
RACK_HOME_BEFORE = True   # 夹料前先"归零"（把瞄准偏移原样还回去 → 得到确定起点）

# ---- 各轴速度 RPM（★速度调越高，等待时间越长，见 school_config.TTL_ACC_RPM_PER_S）----
LIFT_UP_VEL   = 60        # ★2026-09-18 晚 40→60（与 grab_demo 同步）
LIFT_DOWN_VEL = 40        # ★30→40（下放要准，比抬升保守）
ARM_IN_VEL    = 240       # ★200→240
ARM_OUT_VEL   = 240       # ★200→240

# ---- 夹爪舵机角度（0x14；瞄准前张到这个角度，夹取时闭合）----
#   ★2026-09-18 车主："所有瞄准时把夹爪再开大点，开到舵机 10 度"（50 → 20 → 10）
#   ⚠️ 对这台车：**角度越小 = 爪片张得越开**。改完必须重启程序（setup() 只在启动时下发一次）。
GRIP_OPEN_ANGLE  = 10
GRIP_CLOSE_ANGLE = 82

# ---- 底盘档（下发给 STM32，决定车走多快）----
CHASSIS_VEL = 7
CHASSIS_ACC = 6
CHASSIS_DEC = 3

# ---- 上电初始化（**相对**行程，只在机械臂处于"上电摆放位"时执行）----
#   轴号：1=伸缩 2=龙门 4=base；方向：id1 0=前伸/1=后缩，其余 0=正转/1=反转
INIT_STEPS = [
    ("axis", 1, 0, 2000, 250),   # 伸缩前伸到机械零点
    ("axis", 2, 1, 5000,   80),  # 龙门抬升到机械零点（= 起始位/瞄准高度）
    ("axis", 4, 1, 4800, 200),   # base 右转回正
]
INIT_ON_START = True             # 跑本 demo 前先做初始化（STM32 刚上电那一次）


def _deg2pulses(deg):
    """角度 → 脉冲（用**本文件**的 BASE_90；1° ≈ BASE_90/90 ≈ 53 脉冲）。"""
    return int(round(BASE_90 * float(deg) / 90.0))


# 本文件自带的动作参数清单 —— 菜单 `p` 会把它们的**当前值**打出来，
# 方便和 grab_demo 参数区逐行对照（两边各一份，改完记得手工同步）。
_SELF_PARAM_KEYS = (
    "BASE_90", "TTL_VEL", "RACK_WORK_DEG", "RACK_TURN_DEG",
    "RACK_LIFT_UP", "RACK_LIFT_DOWN", "RACK_LIFT_CLEAR", "RACK_LIFT_BACK",
    "RACK_ARM_IN", "RACK_TTL_VEL", "RACK_HOME_BEFORE",
    "LIFT_UP_VEL", "LIFT_DOWN_VEL", "ARM_IN_VEL", "ARM_OUT_VEL",
    "GRIP_OPEN_ANGLE", "GRIP_CLOSE_ANGLE",
    "CHASSIS_VEL", "CHASSIS_ACC", "CHASSIS_DEC",
    "PLACE_DOWN", "PLACE_UP", "INIT_ON_START",
)


# ---- ★★标靶颜色（车主 2026-09-18）：与市赛不同 ----
#   1 号靶位 = 红、2 号靶位 = 绿、3 号靶位 = 蓝。
#   ⚠️ 2026-09-18 晚更新：**这只用于日志/区分靶位**。瞄准本身走 **ring 模式找圆环中心**
#      （见 do_aim_target 的说明）—— 标靶是同心虚线圆环，用颜色块检测根本找不到。
#   ★颜色现在与"放哪块料"**直接对应**：2026-09-19 车主"将加工区三个物料摆放改为颜色对应摆放"
#      → `school_config.PLACEMENT_MAP` 已改成**同色对齐**（1号料→1号靶、2→2、3→3）。
#      也就是说：投到 i 号靶位的料，颜色就是该靶位的标靶颜色。
TARGET_COLOR = {1: "red", 2: "green", 3: "blue"}

# ---- ① 从车载物架夹料：**grab_demo 放料 9 步的反向版** ----
#   起点/终点都是【90° 工作位 + 瞄准高度 H】，中间下到料位夹一块料回来。
#   ★用算式而不是写死数字：以后单独改 RACK_LIFT_UP / DOWN / CLEAR，这里会自动跟着变。
PICK_FROM_RACK_STEPS = {
    n: [
        ("lift_up", abs(int(RACK_LIFT_BACK)), LIFT_UP_VEL),           # ① 反⑨：抬离瞄准高度
        ("base_right", _deg2pulses(RACK_TURN_DEG[n]), RACK_TTL_VEL),  # ② 反⑧：转到该号位
        ("arm_in", RACK_ARM_IN, ARM_IN_VEL),                          # ③ 反⑦：伸缩缩回
        ("lift_down", RACK_LIFT_CLEAR, LIFT_DOWN_VEL),                # ④ 反⑥：下到**料位**
        ("lift_down", 1000, LIFT_DOWN_VEL),
        ("grip_close",),                                              # ⑤ 反⑤：★夹住（原为松爪）
        ("lift_up", 1000, LIFT_UP_VEL),
        ("lift_up", RACK_LIFT_DOWN, LIFT_UP_VEL),                     # ⑥ 反④：抬离料位
        ("arm_out", RACK_ARM_IN, ARM_OUT_VEL),                        # ⑦ 反③：伸缩伸出
        ("base_left", _deg2pulses(RACK_TURN_DEG[n]), RACK_TTL_VEL),   # ⑧ 反②：转回 90° 工作位
        ("lift_down", RACK_LIFT_UP, LIFT_DOWN_VEL),                   # ⑨ 反①：回瞄准高度
    ]
    for n in (1, 2, 3)
}

# ---- ② 放到标靶上的动作组 ----#   前提：车已停在靶位前、龙门在瞄准高度、夹爪夹着料。
#   下放 → 松爪 → 抬回（一放一抬，净高度 0 → 收工仍在瞄准高度）。
PLACE_DOWN = 4000        # ★下放量（瞄准高度 → 靶面）—— 现场用 m 手试、e 改
PLACE_UP = 4000          # 抬回量（= 下放量，保持净高度 0）
PLACE_STEPS = [
    ("lift_down", PLACE_DOWN, LIFT_DOWN_VEL),   # 下放到靶面
    ("grip_open",),                             # 松爪放料
    ("lift_up", PLACE_UP, LIFT_UP_VEL),         # 抬回瞄准高度
]

MENU = """
──────────────────────────────────────────────────────────────
 加工区 放置 分步调试 demo    动作组=%s   base≈%.0f°（0=正前方 +90=左侧作业位）
  h 归位(→0°)      l 进作业位(→90°)
  a 只瞄准标靶(按当前 n)  ★记住本次 off        z 归零(把 off 还回去)
  b 回放(把 off 正着补回来 → 回靶心)           n 换靶位(1/2/3)  当前 n=%d 色=%s
  P 从物架夹料(只夹)   0 只放置(只放)   A/B/C 一条龙(瞄→归零→夹→回放→放)
  v [帧数] 静止抖动测试   c [base脉冲] [arm脉冲] K 值标定/查耦合
  k 看瞄准参数    m 手动发单个动作    p 打印动作组    q 退出
  ── 完整手动顺序： l → a → z → P → b → 0 ──
──────────────────────────────────────────────────────────────"""


def do_pick_from_rack(car, n, base_deg, prefix="[夹料] ", rack=None, aim_off=None):
    """① 从车载物架夹一块料（反向动作组），返回执行后的 base 角度。

    · `n`    = **靶位号**（只用于打日志 / 作为默认物架号）
    · `rack` = **实际从哪个物架取料**，默认 = n。
      ★跑图里仍然**显式传**：按"**这块料的颜色**"算物架号（`RACK_OF_COLOR[color]`）——
        物架里装的就是按颜色归置的料。
        （2026-09-19 起 `PLACEMENT_MAP` 已是**同色对齐**，所以物架号恰好等于靶位号；
          但显式传更稳：万一以后规则再变回交叉，这里不用动。）
    · `aim_off` = 本次瞄准的偏移。★给了它就**把"归零"并进动作组第①步**：
        原本 rack_zero() 先串行把 base/伸缩还回去、再抬龙门架，要等两段；
        现在把 **base 回正 / 伸缩回正 / 龙门抬升 三轴并行**发出，只等最慢的那一步。
        这正是车主 2026-09-18 要的："龙门架、伸缩台、base 轴同时开启运动…
        等最后一个部分做完了，再进行 base 大回转"（大回转 = 下面的第②步，仍在并行组之后）。
    """
    rack = int(n) if rack is None else int(rack)
    # ★把"归零"并进第①步 → 三轴并行组（公共实现，与原料区用同一个函数）
    steps = merge_zero_into_steps(PICK_FROM_RACK_STEPS[rack], aim_off, base_vel=TTL_VEL)
    ob = int((aim_off or {}).get("base", 0) or 0)
    oa = int((aim_off or {}).get("arm", 0) or 0)
    if ob or oa:
        car.log("  %s★把「归零」并进第①步：龙门抬升 + base 回正(%+d) + 伸缩回正(%+d) 三轴并行"
                % (prefix, ob, oa))
    car.log("  %s从车载物架 %d 号位夹料（%d 步，反向照抄放料动作组）"
            % (prefix, rack, len(steps)))
    # 复用 grab_demo 的 _run_rack：它会把 base 那两步改成**绝对角定位**（不依赖"此刻一定在 90°"），
    # 并且会在处理完并行组后**自动**把 base 的回正脉冲记进软跟踪（见 _track_par_base）。
    base_deg, n_ok, n_all = _run_rack(car, steps, rack, base_deg,
                                      turn_deg=RACK_TURN_DEG[rack], work_deg=RACK_WORK_DEG,
                                      prefix=prefix, what="夹料")
    car.log("  %s夹料动作组 %d/%d 步，base≈%.0f°" % (prefix, n_ok, n_all, base_deg))
    return base_deg, (n_ok == n_all)


def do_aim_target(car, n, base_deg, prefix="[瞄准] "):
    """② 瞄准 n 号靶位。

    ★★2026-09-18 晚 现场大发现：**加工区必须用 ring 模式（找圆环中心），不能用 color！**

      之前这里传的是颜色名（走 `aim` 的 "color" 模式 = 找**实心色块**质心），
      而加工区的标靶是「**同心虚线圆环**」（中间是空的）——
      色块检测的填充率/圆度判据根本过不了 → **每帧都检测不到** →
      `BlobTracker` 的 stale 保持就把**上一轮的旧中心**当作 found=True 返回 →
      上层算出"已经在画面中心附近"→ **三拍零修正的"假收敛"**。
      现场证据（日志）：三个靶位 offset 全是 `{'base': 0, 'arm': 0}`，一个动作都没发；
      诊断帧 `aim_fail/T2_213400_raw.jpg` 里标靶明明是清晰的红/绿/蓝同心圆环。

      ring 模式走 `ring_center_detect_v2.RingDetector`（与 ring_stream_ui 同款），
      它的 `close_k=9` 闭运算就是**专门把虚线同心环连成实心环带**再取最小外接圆心的；
      参数 `center_gate_r` 只认画面中心附近的那个环 → 三个靶位同时入镜也不会认错。

    `TARGET_COLOR` 仍然保留：只用于日志与"该靶位标靶颜色 vs 这块料颜色"的一致性检查。
    """
    color = TARGET_COLOR.get(n)
    car.log("  %s瞄准 %d 号靶位（标靶颜色 = %s；ring 模式找**圆环中心**）"
            % (prefix, n, color))
    return aim_and_track(car, _ColorArg(None), base_deg, tag="T%d" % n,
                         mode="ring", base90=BASE_90)


def do_place(car, prefix="[放置] "):
    """⑥ 下放 → 松爪 → 抬回。返回 (执行步数, 是否全成功)。"""
    car.log("  %s放到靶面（%d 步）：%s" % (prefix, len(PLACE_STEPS), PLACE_STEPS))
    return car.run_steps_host(PLACE_STEPS, stop_at_break=False)


def do_replay_aim(car, aim_off, base_deg, prefix="[回放] "):
    """⑤ 把瞄准偏移**正着再走一遍** → 回到『刚瞄准好』的靶心位置（可以放料）。

    ★与 rack_zero() 是一对，方向正好相反：
        rack_zero(off) = car.replay(off, sign=-1) → 回瞄准**之前**（夹料的确定起点）
        本函数(off)    = car.replay(off, sign=+1) → 回瞄准**之后**（放料的位置）
      两步中间夹着一次『去物架夹料』；夹料动作组首尾的伸缩净位移 = 0
      （arm_in RACK_ARM_IN 与 arm_out RACK_ARM_IN 抵消），base 又是**绝对角定位**
      （终点必回 90° 作业位），龙门净高度也是 0 —— 所以夹料完的状态与『归零后』
      完全一致，再补上 off 就精确回到靶心 ✓

    base 没有编码器反馈、replay 也不走 goto_base_deg（不记账），所以这里手工记，
    返回更新后的 base 软跟踪角度。
    """
    ob = int((aim_off or {}).get("base", 0) or 0)
    oa = int((aim_off or {}).get("arm", 0) or 0)
    if not ob and not oa:
        car.log("  %s没有瞄准偏移要回放 → 当前位置就是瞄准位" % prefix)
        return base_deg
    car.log("  %s把瞄准偏移正着走回来：base %+d / 伸缩 %+d 脉冲" % (prefix, ob, oa))
    car.replay({"base": ob, "arm": oa}, sign=+1, tag="-back")
    if ob:
        deg = ob * 90.0 / float(BASE_90)
        base_deg += deg
        car.log("  → 软跟踪：回放把 base 挪了 %+d 脉冲（≈%+.1f°）→ base≈%.0f°"
                % (ob, deg, base_deg))
    return base_deg


def do_place_zero(car, aim_off, base_deg, prefix="[回零] "):
    """⑦ 放完料之后的「回零」：把这次瞄准的偏移原样还回去 → base 回 90° 作业位。

    ★车主 2026-09-18："每次在加工区标靶上放完物料也要进行回零"。
      为什么必须做（和放料**前**那次归零同一个理由）：
        放料结束时 base / 伸缩还停在**靶心对齐**的位置上；下一个靶位虽然 run_one
        开头会 goto_base_deg(90°) 把 **base** 拉回来，但 **伸缩是相对位移**、
        没人纠正 → 每个靶位的伸缩起点都不同（多放几个靶位后可能撞物架）。
      做法：car.replay(off, sign=-1) 把 off 反着走一遍 → 精确回到"瞄准之前"。

    与 rack_zero() 是**同一个动作**，只是发生在放料**之后**，所以单独包一层、
    日志文案写成"放完料回零"，现场一眼能分清是哪一次。
    """
    car.log("  %s放完料回零：把这次瞄准的偏移还回去（base/伸缩）→ 回 %.0f° 作业位"
            % (prefix, RACK_WORK_DEG))
    return rack_zero(car, base_deg, aim_off, work_deg=RACK_WORK_DEG,
                     enable=RACK_HOME_BEFORE, base90=BASE_90)


def run_one(car, n, base_deg, pick=True, aim=True, place=True,
            rack=None, zero_after=True, prefix=""):
    """一条完整工序（★顺序按车主 2026-09-18 的纠正）：

        回 90° 作业位 → 瞄准标靶 → **归零** → 去物架夹料 → **回放到靶心** → 放置 → **回零**

    · `n`    = **靶位号**：决定瞄准哪个颜色的标靶（`TARGET_COLOR[n]`）
    · `rack` = **从哪个物架取料**，默认与靶位号相同（手动调试时省事）。
      ★跑图（route_main.s6_process）里会显式传实际物架号：按"这块料的颜色"算 ——
        2026-09-19 起 `PLACEMENT_MAP` 已是**同色对齐**，所以它恰好等于靶位号。
    · `zero_after` = 放完料后**回零**。
      ★车主 2026-09-18："每次在加工区标靶上放完物料也要进行回零"。
    · 只瞄不放（aim=True, pick=False）时**不归零也不回放**，只做放料后的回零 ——
      靶位就是靠瞄准对出来的，放料必须落在瞄准后的那个位置上。
    · 只夹不放（pick=True, aim=False）时没有 off 可回放 → 夹料完直接放。

    返回 (执行后的 base 角度, 本轮用到的瞄准偏移 off)。
    """
    # ① 开工先确保在 90° 作业位（上一次操作可能把 base 挪走了一点）
    base_deg = goto_base_deg(car, base_deg, RACK_WORK_DEG,
                             "%s回 %.0f° 作业位" % (prefix, RACK_WORK_DEG),
                             base90=BASE_90, ttl_vel=TTL_VEL)
    off = {"base": 0, "arm": 0}

    # ② 瞄准标靶 —— ★必须先瞄：off 是后面『归零』与『回放』的唯一依据
    if aim:
        ok_aim, base_deg, off = do_aim_target(car, n, base_deg, prefix=prefix)
        if not ok_aim:
            car.log("  %s!! 标靶瞄准失败（未收敛）→ 放置位置会偏；"
                    "建议先只按 a 单独调，别硬跑一条龙" % prefix)

    # ③ 归零 —— ★2026-09-18 晚：不再在这里单独做，而是**并进 ④ 的第①步并行执行**。
    #    原来：rack_zero() 先把 base/伸缩还回去（串行等两段）→ 再抬龙门架（又等一段）。
    #    现在：base 回正 + 伸缩回正 + 龙门抬升 **三轴同时发出去**，只等最慢的一步。
    #    安全性不变：三个轴互不干涉；base 回正脉冲 = 本次瞄准偏移的相反数，
    #    所以它精确回到"瞄准之前"（= 90° 作业位），不需要再补一次 goto。
    #    ⚠️ RACK_HOME_BEFORE=False（关掉归零）时就不传 aim_off，退回串行老行为。
    _home_off = off if (aim and pick and RACK_HOME_BEFORE) else None

    # ④ 去物架夹料（★rack 可以 ≠ n：跑图里按"物料颜色"取架）
    if pick:
        base_deg, ok_pick = do_pick_from_rack(car, n, base_deg, prefix=prefix,
                                              rack=rack, aim_off=_home_off)
        if not ok_pick:
            car.log("  %s!! 夹料动作组没跑完 —— 后面放置大概率是空的，先查这一步" % prefix)

    # ⑤ 回放 off → 精确回到刚瞄准好的靶心
    if aim and pick:
        base_deg = do_replay_aim(car, off, base_deg, prefix=prefix)

    # ⑥ 放置
    if place:
        n_ok, ok_all = do_place(car, prefix=prefix)
        car.log("  %s放置 %d/%d 步%s" % (prefix, n_ok, len(PLACE_STEPS),
                                        "" if ok_all else "（有步骤失败）"))
        # ⑦ 放完料回零（车主 2026-09-18）—— 把 off 反着走一遍，收工回到 90° 作业位
        if zero_after:
            base_deg = do_place_zero(car, off, base_deg, prefix=prefix)
    return base_deg, off


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/stm32")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dry-run", action="store_true", help="不连车/不动电机，验证流程")
    ap.add_argument("--init", dest="init", action="store_true", default=None)
    ap.add_argument("--no-init", dest="init", action="store_false",
                    help="★跳过上电初始化（臂已在起始位时用这个）")
    a = ap.parse_args()
    # build_car 只用到 a.port/a.baud/a.dry_run/a.color（颜色由下面的 n 决定，这里给个占位）
    a.color = TARGET_COLOR[1]

    car = build_car(a)

    # ★★把底盘/夹爪参数下发给 STM32（0x20）——**尤其是夹爪张开角**。
    #   为什么必须做：瞄准前"张到初始开度"走的是 0x14 → 固件 Grip_Open() →
    #   它读的是**下发的 P_GRIP_OPEN_ANGLE**，只有下发过才生效；没下发就用固件的
    #   SERVO_GRIP_OPEN 兜底（car_config.h = 50）。原来 place_demo 漏了这一步，
    #   于是在参数区改 GRIP_OPEN_ANGLE 根本不起作用（2026-09-18 车主问"从哪调"）。
    #   ⚠️ 必须把 base90 一起传：Car.setup 的默认值是 6400，而本车 BASE_90=4800，
    #      漏传会把固件里的 base 行程改错。
    if not a.dry_run:
        car.setup(chassis_vel=CHASSIS_VEL, chassis_acc=CHASSIS_ACC, chassis_dec=CHASSIS_DEC,
                  base90=BASE_90, ttl_vel=TTL_VEL,
                  grip_close_angle=GRIP_CLOSE_ANGLE, grip_open_angle=GRIP_OPEN_ANGLE)
        print("[setup] 夹爪张开角 %d° / 闭合角 %d° 已下发（瞄准前就张到这个开度）"
              % (GRIP_OPEN_ANGLE, GRIP_CLOSE_ANGLE))
    else:
        print("[setup] dry-run：跳过参数下发")

    do_init = INIT_ON_START if a.init is None else bool(a.init)
    if do_init:
        print("[init] 上电初始化 %d 步：%s" % (len(INIT_STEPS), INIT_STEPS))
        if not car.init_axes(INIT_STEPS):
            print("!! 初始化没跑成功 —— 臂不在起始位时后面所有动作都是歪的")
    else:
        print("[init] 跳过初始化（--no-init）：臂应已停在起始位")

    # ★★软跟踪初值必须是 **0°（正前方）**，不是 90°（2026-09-18 车主发现）。
    #   INIT_STEPS 最后一步是 ("axis",4,1,4800) —— **base 右转回正**，即初始化结束时
    #   base 停在**正前方 0°**。这里若写成 RACK_WORK_DEG(90)，软件就以为"已经在作业位"：
    #       点 l（→90°）算出差 0 → "已在位，不动"
    #       点 h（→0°） 算出差 -90 → **反而右转 90°**
    #   现象就是"l 不动、h 却转 90°"。grab_demo 里这一行本来就是 0.0，抄过来时抄错了。
    base_deg = 0.0
    n = 1
    # ★最近一次瞄准的偏移 + 它当前处在哪一步（防止把归零/回放重复按两遍——
    #   重复按 z 会再 replay(-1) 一次，直接把车转偏一个 off，白跑一趟）。
    last_off = {"base": 0, "arm": 0}
    aim_state = "none"        # none / aimed(已瞄准) / zeroed(已归零) / backed(已回靶心)
    print("=" * 66)
    print("加工区 放置 分步调试 | %s" % ("dry-run（不动电机）" if a.dry_run else "真跑模式"))
    print("标靶颜色：%s" % "、".join("%d号=%s" % (k, v) for k, v in sorted(TARGET_COLOR.items())))
    print("★正确顺序：l 进作业位 → a 瞄准 → z 归零 → P 去物架夹料 → b 回靶心 → 0 放置")
    print("            （或直接按 A / B / C 一条龙，自动按上面这个顺序跑完）")
    print(MENU % ("（见参数区）", base_deg, n, TARGET_COLOR[n]))
    try:
        while True:
            try:
                raw = input("place> ").strip()
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
                    base_deg = goto_base_deg(car, base_deg, 0.0, "归位到正前方",
                                             base90=BASE_90, ttl_vel=TTL_VEL)
                elif c in ("l", "left"):
                    base_deg = goto_base_deg(car, base_deg, RACK_WORK_DEG, "进左侧作业位",
                                             base90=BASE_90, ttl_vel=TTL_VEL)
                elif c in ("n", "next"):
                    n = {1: 2, 2: 3, 3: 1}[n]
                    car.log("  当前靶位号 → %d（标靶颜色 %s）" % (n, TARGET_COLOR[n]))
                elif c in ("1", "2", "3") and not p[0].isupper():
                    n = int(c)
                    car.log("  当前靶位号 → %d（标靶颜色 %s）" % (n, TARGET_COLOR[n]))
                elif c in ("a", "aim") and p[0] not in ("A", "B", "C"):
                    # ★必须有 p[0] 排除：c 是 lower() 过的，大写的 A/B/C 会变成 "a"
                    #   而被这里抢走，导致一条龙永远执行不到（2026-09-18 冒烟测试抓到）
                    ok, base_deg, last_off = do_aim_target(car, n, base_deg, prefix="[只瞄] ")
                    aim_state = "aimed"
                    print("  ► 标靶瞄准：收敛=%s  offset=%s  base≈%.0f°" % (ok, last_off, base_deg))
                    print("     ★已记住这次 off → 接着按 z 归零，夹完料按 b 回到这个靶心")
                elif c in ("z", "zero"):
                    # 归零：把瞄准偏移原样还回去 → 回到夹料的确定起点
                    if aim_state != "aimed":
                        print("  !! 现在没有『刚瞄好、还没归零』的偏移（状态=%s）" % aim_state)
                        print("     顺序是 l → a → z → P → b → 0；先按 a 瞄一次再来")
                    else:
                        base_deg = rack_zero(car, base_deg, last_off, work_deg=RACK_WORK_DEG,
                                             enable=RACK_HOME_BEFORE, base90=BASE_90)
                        aim_state = "zeroed"
                        print("  ► 已归零：瞄准偏移还回去了，base≈%.0f°（这就是夹料的确定起点）"
                              % base_deg)
                elif c in ("b", "back") and p[0] not in ("A", "B", "C"):
                    # ★★必须有 p[0] 排除：c 是 lower() 过的，**大写的 B（2 号靶位一条龙）
                    #   会被这里当成小写 b（回放）抢走** —— 2026-09-18 车主现场踩到：
                    #   按 B 报"没有『待回放』的偏移（状态=none）"，绿色靶位根本进不去。
                    #   ⚠️ 规律：本函数里凡是**小写**命令分支（a / b / c …），都要照这样
                    #      排除 p[0] in ("A","B","C")，否则大写一条龙会被抢先命中。
                    # 回放：把瞄准偏移正着补回来 → 回靶心（夹完料之后按这个）
                    if aim_state == "aimed":
                        print("  !! 你还在瞄准位上（没归零、也没去夹料）→ 无需回放")
                    elif aim_state != "zeroed":
                        print("  !! 没有『待回放』的偏移（状态=%s）" % aim_state)
                        print("     顺序是 l → a → z → P → b → 0")
                    else:
                        base_deg = do_replay_aim(car, last_off, base_deg, prefix="[回放] ")
                        aim_state = "backed"
                        print("  ► 已回放到瞄准后的靶心位置：base≈%.0f° → 现在可以按 0 放置"
                              % base_deg)
                elif c == "p" and p[0] == "P":
                    if aim_state == "aimed":
                        print("  !! 你刚瞄完还没归零 → 夹料起点被瞄准偏移污染了。"
                              "标准顺序是先按 z 归零再按 P")
                    base_deg, ok = do_pick_from_rack(car, n, base_deg)
                    print("  ► 夹料：%s  base≈%.0f°" % ("完成" if ok else "有步骤失败", base_deg))
                elif c in ("p", "print"):
                    print("  靶位 %d（%s）的动作组：" % (n, TARGET_COLOR[n]))
                    print("  ── 从物架夹料（放料反向）──")
                    show_steps(PICK_FROM_RACK_STEPS[n])
                    print("  ── 放到标靶 ──")
                    show_steps(PLACE_STEPS)
                    print("  ── 可直接粘回 place_demo.py 参数区 ──")
                    print(as_code(PICK_FROM_RACK_STEPS[n], name="PICK_FROM_RACK_STEPS_%d" % n))
                    print(as_code(PLACE_STEPS, name="PLACE_STEPS"))
                    print("  ── 本文件**自带**的那份动作参数（与 grab_demo 各一份，互不影响）──")
                    for _k in _SELF_PARAM_KEYS:
                        print("      %-16s = %r" % (_k, globals().get(_k)))
                    print("      %-16s = %r" % ("INIT_STEPS", INIT_STEPS))
                elif c == "0":
                    if aim_state == "zeroed":
                        print("  !! 你归零后还没回放 → 现在落的是『瞄准前』的位置，会偏；"
                              "顺序是先按 b 回到靶心再放")
                    n_ok, ok_all = do_place(car)
                    print("  ► 只放置：%d/%d 步" % (n_ok, len(PLACE_STEPS)))
                elif p[0] in ("A", "B", "C") or c in ("a1", "a2", "a3"):
                    if p[0] in ("A", "B", "C"):
                        n = {"A": 1, "B": 2, "C": 3}[p[0]]
                    else:
                        n = int(p[0][-1])
                    base_deg, last_off = run_one(car, n, base_deg, prefix="[%d号靶] " % n)
                    aim_state = "none"        # 一条龙跑完已经「回零」（放完料把瞄准偏移还回去了）
                elif c in ("v", "verify", "jitter"):
                    jitter_test(car, a, n=int(p[1]) if len(p) > 1 else 30)
                elif c in ("c", "couple") and p[0] not in ("A", "B", "C"):
                    couple_calib(car, a,
                                 pulse=int(p[1]) if len(p) > 1 else 200,
                                 pulse_arm=int(p[2]) if len(p) > 2 else None)
                elif c in ("k", "kaim"):
                    if len(p) < 3:
                        show_aim()
                    else:
                        name = p[1].upper()
                        if not name.startswith(("AIM_", "K_")):
                            name = "AIM_" + name
                        if hasattr(SC, name):
                            cur = getattr(SC, name)
                            val = float(p[2])
                            setattr(SC, name, int(val) if isinstance(cur, int) else val)
                            print("   %s: %s → %s" % (name, cur, getattr(SC, name)))
                        else:
                            print("  !! 没有这个参数：%s" % name)
                elif c in ("m", "manual"):
                    manual(car)
                else:
                    print("  未知命令（P=夹料 / 0=放置 / A=一条龙 / n 换靶位）")
            except (IndexError, ValueError) as exc:
                print("  !! 参数不对：%r" % exc)
            except Exception as exc:                                 # noqa: BLE001
                print("  !! 命令出错：%r" % exc)
            print(MENU % ("（见参数区）", base_deg, n, TARGET_COLOR[n]))
    finally:
        print("收工：打印本 demo 的两组动作组（可抄回 place_demo.py 参数区）")
        print(as_code(PICK_FROM_RACK_STEPS[1], name="PICK_FROM_RACK_STEPS[1]"))
        print(as_code(PLACE_STEPS, name="PLACE_STEPS"))
        car.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
