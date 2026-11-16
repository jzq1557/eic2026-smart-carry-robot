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
# HERE = 校赛目录；".." = 与它同级的目录（部署到板子上后 monitor_ui.py 就在那里）
for _p in (HERE, os.path.join(HERE, "..")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import school_config as SC                                     # noqa: E402
import lower_api as LA                                         # noqa: E402
from car import Car                                            # noqa: E402


# ==============================================================================
# 现场参数区（**只改这一段**；改完直接重跑本程序 —— 开赛时会自动下发给 STM32）
# ==============================================================================

# ---- 底盘（档位，不是 m/s）----
CHASSIS_VEL = 7          # 平移/前进速度档：飘就降到 6，稳了可加到 10
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
BASE_90      = 4800      # base 转 90°（id4）
LIFT_UP      = 2400      # 龙门架抬升 —— 本车的"升降"就是它（id2/id3 反向传动，协议 0x11）
                         # ★2026-09-17: 4400→2400。起始位抬高了 2000（INIT_STEPS 龙门 3000→5000），
                         #   要保持"物架上方绝对高度不变"就少抬 2000；固件 0x16 那两处已同步改 2400。
LIFT_DOWN    = 2400      # 龙门架下放（同上）
TELESCOPE    = 3000      # 伸缩前伸/收回行程（id1）
ARM_OUT_VEL  = 0        # 前伸速度 RPM（慢一点，别撞料）
ARM_IN_VEL   = 40        # 收回速度 RPM
LIFT_UP_VEL  = 40        # 龙门架抬升速度 RPM
LIFT_DOWN_VEL = 30       # 龙门架下探速度 RPM（慢一点，别砸料）
TTL_VEL      = 40        # 机械臂默认转速

# ---- 里程（cm / 度）—— ★全部要现场实测标定 ----
#   ★本组值 = 2026-09-16 22:37 现场调参版（从 RDK 远端 VSCode 本地历史恢复，勿再被部署覆盖）
RIGHT_OUT     = 12       # ① 右移出启动区
TO_SCAN       = 33       # ② 前进到扫码位
TO_MATERIAL   = 35       # ③ 前进进原料区
TO_PROCESS    = 50       # ④ 出原料区后前进
# ★2026-09-28：SPIN_DEG 语义同 route_main.py —— **真实度数**（要求固件 STEPS_PER_DEGREE=36）。
#   ⚠️ 本文件是校赛早期「状态机版」，其余参数（TO_PROCESS=50 等）已过时，
#      现行跑图请看 VisualBot2\route_main.py。
SPIN_DEG      = 90       # ⑤ 原地自旋角度（顺时针，真实度数）
ENTER_PROCESS = 30       # ⑥ 自旋后进加工区
TO_FINISH     = 30       # ⑦ 出加工区前进
FINISH_RIGHT  = 120      # ⑧ 向右长距平移进结束位
PITCH         = 5        # 相邻物料/靶位间距（下面坐标表默认按这个排）

# ---- 上电初始化动作（把机械臂从"上电摆放位"送到"起始位"）----
#   ★这段原来是**写死在固件** `TTLctrl_Init()` 里的三段运动，现已搬到上位机：
#     由 0x21 下发步骤 + 0x24 触发，下位机 motion.c 的动作组解释器逐条执行、每步等到位。
#   轴号：1=伸缩滑台  2=龙门架(id2+id3 反向传动，写 2 即可)  4=base 轴
#   方向：id1 —— 0=正转(前伸) / 1=反转(后缩)；其余轴 0=正转 / 1=反转
#   下面三段与固件原代码**逐条对应**（改完直接重跑，不用重烧固件）：
#       ("axis", 1, 0, 2000, 250)  ← 原 TTLctrl_Motor1 (0, 300, 2000)  伸缩前伸到机械零点
#       ("axis", 2, 1, 5000,  80)  ← 龙门抬升到机械零点（★2026-09-17 车主改：原固件是 3000，
#                                     现 +2000 → 5000，起始位抬高 2000 脉冲）
#       ("axis", 4, 1, 4800, 200)  ← 原 TTLctrl_Motor4 (1, 200, 4800)  base 右转回正
#   ⚠️ **动作组通道的速度字段只有 1 字节（上限 255 RPM）**，而单轴命令(0x10/0x11/0x12)是 2 字节。
#      固件原来给伸缩轴用 300RPM，走动作组发不出去 → 这里用 250 近似（2000 脉冲差 0.02s）。
#      想严格 300RPM：把 INIT_MODE 改成 "host"（上位机逐条发单轴命令，速度位宽不受限）。
#   ⚠️⚠️ **起始位抬高 2000 后，后面所有以"起始位"为基准的龙门架动作都要跟着改**
#      （固件内建组 0x13 放料 / 0x16 去物架夹料里的抬升量是硬编码的 4400/1200，
#        它们从更高的位置再抬 4400 会顶到机械限位）—— 这块**待现场确认后统一改**。
#   还可以用"说人话"的名字（调试器里更好读）：("arm_out", 脉冲, RPM)、("lift_up", …)、
#       ("base_right", …)、("grip_close",)、("grip_open",)、("wait", 毫秒)
INIT_STEPS = [
    ("axis", 1, 0, 2000, 250),
    ("axis", 2, 1, 5000,   80),
    ("axis", 4, 1, 4800, 200),
]
#   ⚠️⚠️ 只有**烧了"把这三段摘掉"的新固件**（26091601TEST 及以后）才能设 True；
#          旧固件自己会做这三段，上位机再发一遍 = 双倍位移（会撞机械限位）。
#   ★★ 初始化是**相对行程**（走 2000/3000/4800 脉冲），不是绝对定位！所以：
#        · 只在机械臂处于【上电摆放位】时执行一次；
#        · 臂已经在起始位时再跑一遍 = 继续往同方向走 → **顶死限位**；
#        · 这种情况跑主程序加 `--no-init` 跳过（或把 INIT_ON_START 改回 False）。
#      2026-09-17 固件已摘掉三段，故这里打开；建议每次上车只跑一次初始化。
INIT_ON_START = True     # 上电初始化开关（True = 每次跑主程序都先做初始化）
INIT_MODE     = "board"  # "board"=整组交下位机执行（推荐：快、时序准、每步等到位）
                         # "host" =上位机逐条执行（每条都打日志，便于排查是哪一段不对）

# ---- 工位纵向坐标（cm，进区作业起点 = 0，前进为正）----
# ⚠️[待确认] 方向：默认"1 号位最近、3 号位最远"；若实测相反，把数字倒过来即可
MATERIAL_CM = {1: 0.0, 2: PITCH * 1.0, 3: PITCH * 2.0}   # 原料区 1/2/3 号位
TARGET_CM = {1: 0.0, 2: PITCH * 1.0, 3: PITCH * 2.0}     # 加工区 1/2/3 号靶位

# ---- 相机 / 扫码 ----
#   ★2026-09-16 现场教训：主程序过去写死 rotate_ccw=True（树莓派时代相机倒装），
#     RDK 上画面被转成 720×1280 竖图 → 圆环检测忽有忽无 → 三次瞄准全失败。
#     现在方向是参数，并且与调参台/监控台共用 ring_calib.json —— 三处永远一致。
CAM_INDEX    = 0         # 作业相机编号（RDK 上是 0；打不开会自动回退到别的编号）
CAM_ROTATE   = "auto"    # 画面方向：auto(跟随 ring_calib.json) / none / ccw / cw / 180
                         #   怎么定：开 MONITOR 后浏览器页面顶部有四个方向按钮，
                         #   点一下立即生效并写进 ring_calib.json，主程序自动跟随。
CAM_EXPOSURE = 60        # ★曝光（2026-09-17 逐档实测定下来的）：
                         #   自动曝光会让画面一直漂（red 掩膜命中量在 1.9k~37k 之间乱跳），
                         #   而库里的 157 直接过曝到**零命中**。实测表（同一个红标靶）：
                         #     自动≈亮度143 → 命中 3.6万但圆度卡门限（找不到）
                         #     60  → 亮度190 → **命中 4.4万、波动仅1.6k、稳定找到大红圆** ★用这个
                         #     80  → 亮度219 → 命中掉到 7千且不稳
                         #     100 → 亮度232 → 只剩几百
                         #     120/157 → 0（全白）
                         #   换场地/换光照后要重测：`python3 -u /tmp/diag_exp.py` 看哪档又大又稳。
                         #   None = 不锁（回到自动曝光，一般不推荐）
CAM_WIDTH    = 0         # 处理宽度（0=按性能档；嫌慢可设 512）
PERF         = "rdk"     # 视觉性能档（RDK X5 用 rdk）
SCAN_TIMEOUT = 10.0      # 扫码超时（秒）；超时用兜底码继续跑，别卡死

# ---- 双摄监控推流（★已整合进主程序：复用主程序读到的帧，不再另开进程抢相机）----
MONITOR          = True  # True = 跑主程序时同时开浏览器推流页面（比赛时看画面用）
MONITOR_PORT     = 8092  # 打开 http://<板子IP>:8092/
MONITOR_SCAN_CAM = -1    # 侧装扫码相机编号；-1 = 不开第二路（本车扫码用的是作业相机）

# ---- 瞄准标定（★2026-09-17 本车实测，已换算到**主程序的画面坐标系**）----
#   单位：像素/脉冲（正转让画面内容向 +x/+y 移动）。
#   ⚠️⚠️ `calibrate_aim.py` 打印的"建议值"**不能直接抄**：它内部用 aim_and_place 的
#        CALIB（rotate_ccw=True）把画面逆时针转了 90°，坐标轴与主程序（CAM_ROTATE=none）差 90°，
#        于是它把 base 的位移记到 y、把臂的位移记到 x，算出的 K 小 20~30 倍。
#        正确做法：看它打印的原始 dx/dy，**按下面方式换算**（或给它加 --rotate none 后再跑）。
#   本车原始标定（--pulses 200，两个方向各一次，取平均）：
#        base dir0: 旋转后 dx=+1.9 dy=-43.6 → 原图 +x 43.6px → 0.218 px/脉冲
#        base dir1: 旋转后 dx=-1.2 dy=+41.6 → 原图 -x 41.6px → 0.208 px/脉冲
#        arm  dir0: 旋转后 dx=+7.8 dy=+0.3  → 原图 +y  7.8px → 0.039 px/脉冲
#        arm  dir1: 旋转后 dx=-7.6 dy=+0.8  → 原图 -y  7.6px → 0.038 px/脉冲
#   方向全部符合 aim.py 的假设（base 正转→画面右移、arm 正转→画面下移）→ dir 分支不用对调 ✓
#   对比老师那台车（K_BASE 0.2593 / K_ARM 0.0413）量级吻合 → 交叉验证通过 ✓
K_BASE            = 0.213    # base 轴：两方向平均 (0.218+0.208)/2
K_ARM             = 0.0385   # 伸缩轴：两方向平均 (0.039+0.038)/2
                             # ★arm 位移只有 7.8px，相对噪声偏大 → 想更准就重跑：
                             #   python3 -u calibrate_aim.py --port /dev/ttyUSB0 --pulses 800
                             #   然后把原始 dx/dy 告诉我，我重新换算
CAM_GRIP_OFFSET_X = 0.0      # 夹爪与光轴不共轴补偿（px）；目标点 = 画面中心 + 这两个值
CAM_GRIP_OFFSET_Y = 0.0
AIM_TOL_PX        = 8.0      # 收敛判据：|dx|、|dy| 都 ≤ 此值（且连续 2 拍）

# ---- 瞄准收敛手感（★2026-09-17 现场反馈"在目标点周围大幅晃"后加的）----
AIM_OUT_MAX_PX    = 12.0     # 单拍限幅（px）：**每拍最多只修这么多像素**。
                             #   原来是 30px —— 太大：K 值是在"标定那个距离"上测的，取料时相机离料
                             #   更近、同样脉冲造成的像素位移更大 → 每拍冲过头再冲回来 = **大幅晃**。
                             #   调小到 12 再配合"多给几拍"，就是小步安稳逼近，
                             #   而且**不依赖 K 值标得准不准**。
AIM_SETTLE_S      = 0.6      # 每拍动作后的停稳等待（秒）。原来 0.4s 偏短：机械和画面还没稳就拍
                             #   下一帧，会看到"正在运动的目标"→ 中心在飘 → 瞄准追着飘。

# ---- 取料：颜色质心滤波（与圆环的 ema/jump_px/stale_max 同款语义）----
#   取料时瞄准的是"目标颜色圆块的中心"（原料区是实心圆块，没有圆环）。
#   blob 质心逐帧会抖，不滤波瞄准会来回过冲 → 爪子在料上方左右晃。
COLOR_EMA        = 0.5       # EMA 平滑系数（0~1；越小越稳但越迟钝，与圆环默认一致）
COLOR_JUMP_PX    = 120       # 突跳阈值（px）：原始质心偏离平滑中心超过它 → 视为换了目标
COLOR_STALE_MAX  = 10        # 连续丢失 ≤ 它帧 → 保持上次中心（超了才算真丢）
#   ★形状门限 —— 这两条是"物料就在镜头前却找不到"的头号嫌疑：
#     库里 max_area_frac 默认只有 0.10（目标占画面 >10% 整块丢掉），而**调参台的面积门限不进档案**。
COLOR_MIN_AREA_FRAC = 0.0008 # 目标最小面积占比（0.0008 = 万分之八；太小会捡噪声）
COLOR_MAX_AREA_FRAC = 0.60   # 最大面积占比（0.60 = 允许物料占掉大半个画面）
COLOR_CIRC_MIN      = 0.45   # 圆度下限（0~1；圆片一般 0.8+，放宽到 0.45 更耐遮挡/反光）
GRIP_OPEN_DEG    = 0         # 取料瞄准前把夹爪张到几度（0=完全张开，免得爪片挡颜色块）

# ---- ★取料动作组（「从地面夹取」的全部细节都在这几行；配合 pick_debug.py 单步调）----
#   一行一步，支持这些写法：
#     ("grip_open",) / ("grip_close",)          张爪 / 闭合
#     ("grip_angle", 角度)                       舵机设任意角度（0~255°）
#     ("arm_out", 脉冲, RPM) / ("arm_in", 脉冲, RPM)        伸缩前伸 / 收回（轴1）
#     ("lift_down", 脉冲, RPM) / ("lift_up", 脉冲, RPM)      龙门下探 / 抬起（轴2，id2+id3 反向）
#     ("axis", 轴号, 方向, 脉冲, RPM)             任意轴（1=伸缩 2=龙门 4=base；方向 0=正转 1=反转）
#     ("wait", 毫秒) / ("break", "说明")          纯等待 / 断点（单步调试在这一步停）
#   调法：① 直接改这几行 → 重跑；② 更推荐——开着 `pick_debug.py` 单步试出数值，再抄回这里。
#   ⚠️ 龙门架的脉冲是**相对当前高度**的行程，不是绝对高度：起始位已由 INIT_STEPS 抬高 2000。
PICK_STEPS = [
    ("grip_open",),                               # ① 张爪
    ("lift_down", LIFT_DOWN, LIFT_DOWN_VEL),      # ② 龙门下探到料（★本车地面取料**不用伸缩台**）
    ("grip_close",),                              # ③ 夹住
    ("lift_up", LIFT_UP, LIFT_UP_VEL),            # ④ 抬起来（别蹭地）
]

# ---- ★放置动作组（"放到地面"的细节，同样**不用伸缩台**）----
PLACE_STEPS = [
    ("lift_down", LIFT_DOWN, LIFT_DOWN_VEL),      # ① 龙门下放到地面
    ("grip_open",),                               # ② 开爪放料
    ("lift_up", LIFT_UP, LIFT_UP_VEL),            # ③ 抬起
]

# ★收臂归位时伸缩台收回多少脉冲。**0 = 不动伸缩台**（默认）——
#   因为地面取料/放料已经不用伸缩台，它一直停在原位；若每次归位都"反转 3000 脉冲"，
#   会一路缩到底、顶死限位。只有当你确实把伸缩台伸出去了，才把这个值设成实际伸出量。
HOME_TELESCOPE = 0

# ---- 数字识别 ----
#   校赛**与数字无关**（取料认颜色、放置认圆环），所以把 NCC 数字匹配等效关掉，白赚帧率。
#   RingDetector 只支持"节流"（digit_every），给个极大值即等效关闭（不动上游库、不影响回归）。
DIGIT_EVERY      = 999

# ---- 瞄准超时 / 重试 / 盲夹兜底（★2026-09-17 现场要求）----
#   原料区三个物料的**位置和顺序都是固定的** → 瞄准失败不该白跑一趟，直接按固定位置盲夹。
BLIND_PICK        = True   # 瞄准失败后**盲夹**（伸到位直接夹，账本照记）；False = 失败即放弃
BLIND_PICK_PULSES = 0      # 盲夹前伸脉冲；0 = 用 TELESCOPE（与正常取料相同）
AIM_TIMEOUT       = 30.0   # 单次瞄准总超时（秒）；以前 20s 偏短
AIM_MAX_ITER      = 20     # 单次瞄准最大迭代拍数；以前 16
AIM_RETRY         = 8      # 连续丢目标几次才放弃（★以前 3 太急，丢两下就放弃）；放弃后走盲夹
AIM_PROBE_PULSES  = 0      # 丢目标时"试探"的固定脉冲数；**0 = 不试探、只等待**（推荐）
                           #   ⚠️以前是按 AIM_PROBE_PX/K_ARM 换算 ≈1039 脉冲且单向后收
                           #     → 每次失败都把伸缩臂收到行程尽头（现场 offset arm=-2904 就是它）
AIM_PROBE_TOTAL   = 400    # 试探累计上限（脉冲），防止越跑越偏

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
                           steps=PICK_STEPS, open_deg=GRIP_OPEN_DEG, blind=BLIND_PICK)
        if ok:
            car.ledger.record(mid, i)           # 第 i 个取的 → 车上 slot i
            car.log("  ✓ 已上车：%s 号 → slot %d（账本 %s）" % (mid, i, car.ledger.summary()))
        else:
            car.log("  !! %s 取料失败，跳过该物料" % tag)


def go_process_area(car):
    """转场工序：收臂 → 前进 → 原地自旋 → 前进进加工区 + base 左转 90°。"""
    car.home(telescope=HOME_TELESCOPE, base90=BASE_90, speed=TTL_VEL)
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
        ok = car.place_here(slot=slot, tag=tag, steps=PLACE_STEPS)
        if ok:
            car.ledger.consume(mid)
            car.log("  ✓ 已放置：%s 号 → 靶位 %d（账本 %s）" % (mid, tgt, car.ledger.summary()))
        else:
            car.log("  !! %s 放置失败" % tag)


def go_finish(car):
    """收尾工序：收臂 → 前进 → 向右长距平移 → 停车。"""
    car.home(telescope=HOME_TELESCOPE, base90=BASE_90, speed=TTL_VEL)
    car.fwd(TO_FINISH)
    car.right(FINISH_RIGHT)
    car.stop()


# ------------------------------------------------------------------------------
# ★ 整场流水账（从上到下 = 车的实际动作顺序）
# ------------------------------------------------------------------------------
def run(car, code=None, skip_scan=False, init=None, stop_at_material=False):
    # 把本文件参数区里的瞄准标定值注入 school_config（aim.py 直接读 SC 的这几个常量）
    SC.K_PX2STEP_BASE = K_BASE
    SC.K_PX2STEP_ARM = K_ARM
    SC.CAM_GRIP_OFFSET_X = CAM_GRIP_OFFSET_X
    SC.CAM_GRIP_OFFSET_Y = CAM_GRIP_OFFSET_Y
    SC.AIM_TOL_PX = AIM_TOL_PX
    SC.AIM_OUT_MAX_PX = AIM_OUT_MAX_PX      # 单拍限幅（调小 → 不晃）
    SC.AIM_SETTLE_S = AIM_SETTLE_S          # 每拍停稳等待（调大 → 不追飘）
    SC.T_AIM = AIM_TIMEOUT                  # 单次瞄准总超时（秒）
    SC.AIM_MAX_ITER = AIM_MAX_ITER          # 最大迭代拍数
    SC.AIM_RETRY = AIM_RETRY                # 连续丢目标的容忍次数
    SC.AIM_PROBE_PULSES = AIM_PROBE_PULSES  # 丢目标试探脉冲（0 = 不试探、只等待）
    SC.AIM_PROBE_TOTAL = AIM_PROBE_TOTAL    # 试探累计上限
    # 视觉参数注入（相机此时已装好）：取料的颜色质心滤波 + 关掉数字识别
    if getattr(car, "color_vision", None) is not None:
        car.color_vision.ema = COLOR_EMA
        car.color_vision.jump_px = COLOR_JUMP_PX
        car.color_vision.stale_max = COLOR_STALE_MAX
        car.color_vision.min_area_frac = COLOR_MIN_AREA_FRAC
        car.color_vision.max_area_frac = COLOR_MAX_AREA_FRAC
        car.color_vision.circ_min = COLOR_CIRC_MIN
    if getattr(car, "vision", None) is not None and hasattr(car.vision, "cfg"):
        car.vision.cfg["digit_every"] = DIGIT_EVERY
    car.pick_steps = list(PICK_STEPS)        # ★取料动作组（参数区 PICK_STEPS 那几行）
    car.place_steps = list(PLACE_STEPS)      # ★放置动作组（参数区 PLACE_STEPS）
    car.log("=" * 62)
    car.log("校赛开始：任务码 %s" % (code or ("(跳过扫码)" if skip_scan else "(待扫码)")))
    car.log("=" * 62)
    car.log("[瞄准] K_BASE=%.4f  K_ARM=%.4f px/脉冲，容差 %.1fpx"
            "（★这两个 K 没在本车重测过就必然收敛不了）" % (K_BASE, K_ARM, AIM_TOL_PX))

    # ① 参数下发（这一步不通就别发车）
    if not car.setup(chassis_vel=CHASSIS_VEL, chassis_acc=CHASSIS_ACC, chassis_dec=CHASSIS_DEC,
                     grip_close_angle=GRIP_CLOSE_ANGLE, grip_open_angle=GRIP_OPEN_ANGLE, base90=BASE_90,
                     lift_up=LIFT_UP, lift_down=LIFT_DOWN, telescope=TELESCOPE, ttl_vel=TTL_VEL):
        car.log("!! 参数下发/读回校验未通过 —— 建议先查通信再发车")

    # ①b 上电初始化：把机械臂从"摆放位"送到"起始位"
    #     （原本写死在固件 TTLctrl_Init() 里，现在由上位机 0x21+0x24 下发执行）
    do_init = INIT_ON_START if init is None else bool(init)
    if do_init:
        car.log("[init] 上电初始化 %d 步（%s 模式）" % (len(INIT_STEPS), INIT_MODE))
        if not car.init_axes(INIT_STEPS, mode=INIT_MODE):
            car.log("!! 初始化动作组没跑成功 —— 臂不在起始位，后面全是歪的，先解决再发车")
    else:
        car.log("[init] 跳过自动初始化（INIT_ON_START=False）；要跑加 `--init`；"
                "固件若还带 TTLctrl_Init 的三段运动就不能重复发（会双倍位移）")

    # ② 出启动区 → 前进到扫码位 → 扫码
    car.right(RIGHT_OUT)
    car.fwd(TO_SCAN)
    code, pick_order, place_order = scan_task_code(car, code=code, skip=skip_scan)

    # ③ 进原料区 + base 左转 90°（车左侧朝向料区）
    go_material_area(car)

    # ③b ★调试用：到原料区就停住，把车留在"能反复试取料动作"的姿态
    if stop_at_material:
        car.log("=" * 62)
        car.log("!! --stop-at-material：已到原料区（base 左转 90°、车就位），后面的流程不跑了")
        car.log("   接下来用取料调试器反复试动作组：")
        car.log("     cd /root/VisualBot_current/校赛")
        car.log("     python3 -u pick_debug.py --port /dev/ttyUSB0 --color red")
        car.log("=" * 62)
        return 0

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
# 双摄监控推流（内嵌，不另开进程）：work 路复用主程序读到的帧，scan 路自己开侧装相机
# ------------------------------------------------------------------------------
def start_monitor(hub=None, port=8092, scan_cam=-1, perf="rdk", log=print):
    """起推流服务；失败也不影响比赛（返回 None）。返回 (server, [workers])。"""
    try:
        import monitor_ui as MU
    except Exception as e:                                       # noqa: BLE001
        log("[monitor] 推流模块不可用：%r（比赛照常进行，只是没有画面）" % e)
        return None
    srv, work, scan = MU.start_embedded(
        port=port, work_hub=hub, work_cam=None,
        scan_cam=(scan_cam if scan_cam is not None and scan_cam >= 0 else None),
        perf=perf, log=log)
    if srv is None:
        return None
    return (srv, [work, scan])


def stop_monitor(mon):
    if not mon:
        return
    try:
        import monitor_ui as MU
        MU.stop_embedded(mon[0], mon[1])
    except Exception:                                            # noqa: BLE001
        pass


# ------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dry-run", action="store_true", help="不连车/不用相机，验证流程")
    ap.add_argument("--code", default=None, help="直接给任务码（跳过扫码），如 123+321")
    ap.add_argument("--skip-scan", action="store_true", help="跳过扫码，用兜底码")
    ap.add_argument("--cam", type=int, default=CAM_INDEX, help="作业相机编号")
    ap.add_argument("--rotate", default=CAM_ROTATE, help="画面方向 auto/none/ccw/cw/180")
    ap.add_argument("--exposure", type=float, default=CAM_EXPOSURE,
                    help="锁曝光值（不给=自动曝光；RDK 上那台 156 会过曝）")
    ap.add_argument("--perf", default=PERF, help="视觉性能档(auto/low/mid/high/rdk)")
    ap.add_argument("--no-monitor", action="store_true", help="不开浏览器推流页面")
    ap.add_argument("--monitor-port", type=int, default=MONITOR_PORT)
    ap.add_argument("--scan-cam", type=int, default=MONITOR_SCAN_CAM,
                    help="侧装扫码相机编号（-1=第二路不推）")
    ap.add_argument("--init", dest="init", action="store_true", default=None,
                    help="强制跑上电初始化动作组（默认按参数区 INIT_ON_START）")
    ap.add_argument("--no-init", dest="init", action="store_false",
                    help="强制跳过上电初始化动作组（固件仍自带三段运动时用这个）")
    ap.add_argument("--stop-at-material", action="store_true",
                    help="★调试用：跑到原料区（base 左转后）就停下，留给 pick_debug.py 调取料动作组")
    a = ap.parse_args()

    car = Car(port=a.port, baud=a.baud, dry_run=a.dry_run)
    hub, mon = None, None
    want_mon = MONITOR and not a.no_monitor
    try:
        if want_mon:
            from vision import FrameHub
            hub = FrameHub()
        if a.dry_run:
            car.use_camera(rotate=a.rotate)                    # dry-run 装仿真视觉
        else:
            car.use_camera(cam_index=a.cam, perf=a.perf, exposure=a.exposure,
                           rotate=a.rotate, hub=hub, width=CAM_WIDTH)
        if want_mon:
            mon = start_monitor(hub=hub, port=a.monitor_port, scan_cam=a.scan_cam,
                                perf=a.perf, log=car.log)
        run(car, code=a.code, skip_scan=a.skip_scan, init=a.init,
            stop_at_material=a.stop_at_material)
    except KeyboardInterrupt:
        car.log("!! 人工中断 → 停车")
        car.stop()
        return 130
    except LA.LowerLinkError as e:
        car.log("!! 串口掉线：%s" % e)
        car.log("   排查顺序：① USB 线被车拖动/松动（换口、换线、扎带固定）")
        car.log("             ② 舵机/电机供电把 USB 拉掉（动力电与 USB 分开供）")
        car.log("             ③ 有别的程序占着串口：ps -eo pid,args | grep school_main")
        car.log("   （车已停在原地，不会带着坏通信继续瞎跑；硬件确认后重跑即可）")
        return 2
    except RuntimeError as e:
        car.log("!! 启动失败：%s" % e)
        return 3
    finally:
        stop_monitor(mon)
        car.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
