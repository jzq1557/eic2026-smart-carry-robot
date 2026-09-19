#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
route_main.py —— VisualBot2「只跑图」主程序
================================================================================
干什么：让车**只按比赛路线走位**（含 base 轴作业姿态与侧装相机扫码），
        一路有双摄推流可看，**机械臂/龙门架/夹爪完全不参与**。

为什么单独一个工程：车走歪了，原因只可能是「里程参数」或「底盘本身」；
        不会再有"是瞄准没收敛还是机械臂撞了"这种互相掩盖的问题。等路径稳了，
        再由主工程（/root/VisualBot）接上取料/放置动作。

用法
    cd /root/VisualBot2
    python3 -u route_main.py --port /dev/stm32                    # 分段跑：每段停下等你回车
    python3 -u route_main.py --port /dev/stm32 --auto             # 一口气跑完（不看实测）
    python3 -u route_main.py --port /dev/stm32 --start 3          # 从第 3 段开始
    python3 -u route_main.py --dry-run                            # 不连车、不动电机（验证流程）
    python3 -u route_main.py --port /dev/stm32 --no-monitor       # 不开推流

看画面：浏览器 http://<板子IP>:8092/  （左=前方作业相机，右=侧装扫码相机，扫码结果直接在上面）
标定：路线走完量出实际距离后，用 route_debug.py 算比例、给建议值。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# ★★sys.path 顺序至关重要（2026-09-18 的大坑）：共享模块会把**老工程 `/root/VisualBot`**
#   也塞进 sys.path，而那里 `/root/VisualBot/校赛/` 下有**同名的旧版 aim.py / car.py /
#   vision.py**。原来的 `for p in (HERE, …): insert(0, p)` 是顺序插入，**后插的排最前**
#   → `/root/VisualBot` 反超到 sys.path[0] → `import aim` 命中旧版 →
#   三阶段瞄准 / 软化 / arm 专项阻尼 / 实测 K **全部静默失效**。
#   实测证据：`>>> aim = /root/VisualBot/校赛/aim.py`。
#   → 倒序插入（后插的优先）+ 先清掉重插，保证本项目自己的目录最终排在最前。
for _p in ("/root/VisualBot", "/root/VisualBot2", HERE):
    if not _p or not os.path.isdir(_p):
        continue
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import route_car as RC                                          # noqa: E402
import school_config as SC                                      # noqa: E402  任务码解析 / 颜色映射（复用）
from route_car import RouteCar                                  # noqa: E402

# ==============================================================================
# 现场参数区（**只改这一段**；改完直接重跑，不用重烧固件）
# ==============================================================================

# ---- 底盘档位 ----
CHASSIS_VEL = 7          # 平移/前进速度档（飘就降到 6，稳了可加到 10）
CHASSIS_ACC = 6          # 加速档
CHASSIS_DEC = 3          # 减速档（比 ACC 小 = 刹车比起步缓，防点头丢步）

# ---- base 轴（作业姿态）----
BASE_90     = 4800       # base 转 90° 的脉冲数（★现场实测值）
BASE_180    = 9600       # ★base 转 **180°** —— 扫码专用：侧装相机装在龙门架右侧，而二维码在
                         #   原料区同一侧，得把 base 转半圈才能让相机正对二维码。
                         #   理论值 = 2×BASE_90，但机械回差会累积 → 现场单独量一次更准
                         #   （route_debug.py 里选"自定义"发一段脉冲，量实际转了多少度）。
TTL_VEL     = 80         # base 转动速度 RPM ★2026-09-18 晚 40→80：
                         #   车主"全程序（除瞄准动作）的 base 轴旋转速度翻个倍"。
                         #   ⚠️ 瞄准那一套是独立的 AIM_VEL_BASE/ARM（仍 40），**不受影响** ——
                         #      瞄准是小脉冲微调，提速会丢步、激起余振。
                         #   这个值会下发固件（ttl_default_vel），并被各 demo/动作组引用。

# ---- ★路线里程（cm / 度）—— 全部要现场实测，用 route_debug.py 算比例 ----
#   ★★ 这一组 = 2026-09-17 车主现场实测值，**部署脚本默认保护、不会覆盖**；
#      要改就在板上改，改完用 pull_from_rdk.ps1 收回来（见 README「现场参数保护」）。
RIGHT_OUT     = 11       # ① 右移出启动区
TO_SCAN       = 33       # ② 前进到扫码位
TO_MATERIAL   = 35       # ③ 前进进原料区
TO_PROCESS    = 82       # ④ 出原料区后前进      ← 车主实测（曾被我误覆盖成 50）
SPIN_DEG      = 63.3       # ⑤ 原地自旋角度（顺时针）← 车主实测（曾被我误覆盖成 70）
ENTER_PROCESS = 55       # ⑥ 自旋后进加工区      ← 车主实测（曾被我误覆盖成 30）
TO_FINISH     = 80       # ⑦ 出加工区前进        ← 车主实测（曾被我误覆盖成 30）
FINISH_RIGHT  = 120      # ⑧ 向右长距平移进结束位

# ---- ★区内工位（原料区 3 个号位 / 加工区 3 个靶位）----
#   两区都是：**三个位置沿车前进方向排成一列**，进区时车正好对着 1 号位，
#   再往前依次是 2 号位、3 号位；**相邻号位间隔 10cm**（车主实测）。
#   取料/放置时车就在这条线上**前后移动**去对准不同号位（下面两张表按 PITCH 生成）。
PITCH        = 12        # 相邻号位间距（cm）
MATERIAL_CM  = {1: 0.0, 2: PITCH * 1.0, 3: PITCH * 2.0}   # 原料区 1/2/3 号位 → 0 / 10 / 20 cm
TARGET_CM    = {1: 0.0, 2: PITCH * 1.0, 3: PITCH * 2.0}   # 加工区 1/2/3 号靶位 → 0 / 10 / 20 cm

# ---- ★上电初始化动作（把机械臂从"摆放位"送到"起始位"）----
#   与 /root/VisualBot 是同一套（原来写死在固件 TTLctrl_Init()，现已搬到上位机，
#   由 0x21 下发步骤 + 0x24 触发，下位机 motion.c 逐条执行、每步等到位）。
#   轴号：1=伸缩滑台  2=龙门架(id2+id3 反向传动，写 2 即可)  4=base 轴
#   方向：id1 —— 0=正转(前伸) / 1=反转(后缩)；其余轴 0=正转 / 1=反转
#   ⚠️ 是**相对行程**，只在机械臂处于【上电摆放位】时执行；
#      臂已在起始位时再跑一遍会继续往同方向走 → 顶死限位 → 那时用 `--no-init`。
INIT_STEPS = [
    ("axis", 1, 0, 2000, 250),   # 伸缩前伸到机械零点（原 TTLctrl_Motor1 (0,300,2000)）
    ("axis", 2, 1, 5000,   80),  # 龙门抬升到机械零点（原 3000，2026-09-17 车主 +2000）
    ("axis", 4, 1, 4800, 200),   # base 右转回正（原 TTLctrl_Motor4 (1,200,4800)）
]
INIT_ON_START = True     # 跑图开始前先做初始化（STM32 刚上电那一次）；之后跑图加 --no-init

# ---- ★原料区作业（2026-09-18 车主："将 grab 放入跑图过程中"）----
PICK_IN_ROUTE = True     # True = 跑图时真做「瞄准 → 取料 → 放到对应置物架」
                         # False = 恢复原来的"只走位"（跑图标定/调试用）
RACK_OF_COLOR = {"red": 1, "green": 2, "blue": 3}
                         # ★车主定的规则：红色物料 → 1 号置物架、绿 → 2、蓝 → 3。
                         #   与 school_config.COLOR_OF（1=red / 2=green / 3=blue）一致，
                         #   所以"颜色对应的物架号"恰好就是物料编号本身；
                         #   仍然按**颜色→物架**显式写出来，以后改规则只动这一行。

# ---- ★加工区放置作业（2026-09-18 车主："也把 place 嵌入到 route 中，记得每次在
#      加工区标靶上放完物料也要进行回零"）----
PLACE_IN_ROUTE = True    # True = 跑图时真做「瞄准标靶 → 归零 → 从物架取料 → 回靶心
                         #        → 放置 → **回零**」（见 s6_process / place_demo.run_one）
                         # False = 恢复原来的"只走位"（跑图标定/调试用）
GRIP_OPEN_ANGLE  = 10    # 夹爪张开角（舵机，度）★2026-09-18 车主："所有瞄准时开到 10 度"
                         #   （原 50 → 两个 demo 先改 20 → 现在三个程序统一到 10）
                         #   ⚠️ 对这台车：**角度越小 = 爪片张得越开**。改完必须重启程序。
GRIP_CLOSE_ANGLE = 82    #   "在瞄准物料的过程中夹爪要再次打开成初始化大小"——
                         #   夹爪半开/闭合时爪片会伸进镜头，把色块切掉一块、甚至自己被当成目标，
                         #   所以**每次瞄准前都会先张到这个开度**（car.pick_here 里做的）。
                         #   这两个值会随 setup() 下发给固件（决定 SERVO_GRIP_OPEN/CLOSE），
                         #   与 grab_demo.py 参数区保持一致。
                         #   ⚠️ 取料/放置**动作组里**的 grip 步骤不受影响
                         #      （车主："放置到载物架的动作组中保持当前程序的大小即可"）。

# ---- 相机 / 推流 ----
CAM_INDEX        = 0     # 前方作业相机（RDK 上是 video0）
CAM_ROTATE       = "none"    # ★★画面方向：**显式 none，别用 "auto"**。
                         #   /root/VisualBot2 若没有 ring_calib.json，cam_rotate.resolve("auto")
                         #   会回落到 ring_center_detect_v2.CALIB 的 rotate_ccw=True
                         #   （树莓派时代相机倒装的旧默认）→ 画面被转 90° → **x/y 轴整个对调**
                         #   → 瞄准"越修越偏"（2026-09-17 现场踩过这个坑，grab_demo 也踩过一次）。
                         #   现在跑图要开相机做瞄准了，所以必须钉死 none。
CAM_EXPOSURE     = 30    # ★★2026-09-18 从 60 降到 30。60 在当前光照下过曝 **45%**，
                         #   颜色饱和（红料被洗成粉白）→ 质心飘 → 瞄准"越修越偏"。
                         #   实测扫档：15/0.29%、20/0.46%、25/0.62%、**30/0.95%**、
                         #   40/17.3%、50/39.3%、60/45.1%。取 30（够亮且几乎不过曝）。
                         #   ⚠️ 曝光按场景定：换环境光/距离要重测，用 exposure_tune.py。
CAM_WIDTH        = 0     # 处理宽度（0 = 按性能档）
PERF             = "rdk" # 视觉性能档
MONITOR          = True  # 是否开双摄推流页面
MONITOR_PORT     = 8092
MONITOR_SCAN_CAM = 2     # 侧装扫码相机（video2）；-1 = 不开第二路
SCAN_TIMEOUT     = 15.0  # 扫码等待（秒）

# ==============================================================================


def scan_from_monitor(port=MONITOR_PORT, timeout=SCAN_TIMEOUT, log=print):
    """从内嵌监控台的 /state 里读侧装相机的解码结果（连续 3 次一致才采纳）。

    为什么这么做：监控台**已经在**对侧装相机做二维码解码了，跑图程序直接问它要结果就行，
    不用再抢一次相机（同一台相机同一时刻只能被一个进程打开）。
    """
    t0, last, same = time.time(), None, 0
    while time.time() - t0 < timeout:
        code = None
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/state" % port, timeout=2) as r:
                st = json.loads(r.read().decode("utf-8"))
            code = ((st.get("scan") or {}).get("info") or {}).get("code")
        except Exception:                                        # noqa: BLE001
            code = None
        if code:
            if code == last:
                same += 1
                if same >= 3:
                    log("  [扫码] 读到任务码 %s（连续 3 帧一致）" % code)
                    return code
            else:
                last, same = code, 1
        time.sleep(0.2)
    log("  !! [扫码] %.0fs 内没读到稳定任务码（看监控台右路画面：码有没有进框、清不清楚）"
        % timeout)
    return None


def start_monitor(log=print, work_cam=None, hub=None):
    """起推流。失败也不影响跑图。

    ★`work_cam`：要开哪一路作业相机；**-1 = 不开**。
    ★★`hub`（2026-09-18 修）：**作业模式下应该走这个** —— 把主程序（瞄准）读到的帧
      顺手喂给推流，于是"推流能看到作业画面"且**不抢相机、不重复读同一台 V4L2**。
      之前我只传 `work_cam=-1, hub=None`，结果 work 那一路**根本没启用**，
      页面上只剩扫码那路 → 车主看到的现象是"推流开成了扫码的摄像头"。
      （`start_embedded` 的约定：work_hub 非空 → 用共享帧；否则 work_cam>=0 才自己开。）
    """
    try:
        import monitor_ui as MU
        srv, work, scan = MU.start_embedded(
            port=MONITOR_PORT, work_hub=hub,
            work_cam=(CAM_INDEX if work_cam is None else work_cam),
            scan_cam=(MONITOR_SCAN_CAM if MONITOR_SCAN_CAM is not None and MONITOR_SCAN_CAM >= 0 else None),
            perf=PERF, exposure=CAM_EXPOSURE, work_rotate=CAM_ROTATE, log=log)
        if srv is None:
            return None
        return (srv, [work, scan])
    except Exception as exc:                                     # noqa: BLE001
        log("[monitor] 推流起不来：%r（跑图照常，只是没有画面）" % exc)
        return None


def stop_monitor(mon):
    if not mon:
        return
    try:
        import monitor_ui as MU
        MU.stop_embedded(mon[0], mon[1])
    except Exception:                                            # noqa: BLE001
        pass


# ==============================================================================
# 路线：一段一个函数（**只动底盘 + base**）
# ==============================================================================
def s1_out(car):
    """① 出启动区：右移。

    ★2026-09-18 晚 车主："在启动区进行摄像头啥的初始化完之后，不要直接向右平移出启动区，
      需要等我在 ssh 终端里打个回车再动"。
      → 先 `car.pause()` 停一下：给车主留出"确认场地清空 / 看初始化日志 / 摆好料"的时间，
        免得程序一启动车就自己走了。（`--auto` 一口气模式下 pause 会自动跳过。）
    """
    # ★2026-09-18 晚：auto 模式下 main() 已经在**开跑前强制等过一次**
    #   （见 main 里的 `car.pause(..., auto=False)`），这里再 pause 只会重复打印、不会真等
    #   （car.pause 在 auto 时打印完就直接 return）。所以判掉：**非 auto 才由这里负责等** ——
    #   两种模式都只等**一次**，也不会出现"提示了却没等"的迷惑现象。
    if not getattr(car, "auto", False):
        car.pause("初始化完成 —— 确认场地清空、料已摆好，按回车再右移出启动区")
    car.right(RIGHT_OUT)


def s2_scan(car):
    """② **在去扫码区的路上**就把 base 转 180° → 到位置后扫码 → 转回原位。

    ★2026-09-18 晚 车主："将到了扫码区然后再进行 base 180 度旋转改一下，改为在去扫码区的
      路上进行 base 180 度旋转，从而缩短时间，因为车辆前进和 base 旋转不一定同时完成，
      等二者全部动作完成之后再进行后面的操作"。
    做法（利用"固件 TTL 命令本来就是非阻塞的"这个事实）：
      ① `base_cw(..., wait=False)` —— **只发命令、立刻返回**（同时拿到它的估算耗时）；
      ② 紧接着 `car.fwd(TO_SCAN)` —— 阻塞等底盘到位；
      ③ 再按"**谁慢等谁**"补足 base 剩下的时间（底盘通常更慢，多数时候不用补）。
    ⇒ "前进 TO_SCAN cm" 和 "base 转 180°" 真正并行了，省掉 base 那一整段（≈1.4s）。

    ★为什么扫完必须转回：侧装相机装在**龙门架右侧**，而二维码在原料区同一侧 ——
      只有把 base 转半圈，相机才照得到码；不转回来的话，后面进原料区的"90° 作业姿态"就是歪的。
      （转 180° 时龙门架+机械臂会扫过整片空间，跑之前先确认周围清空、别站人。）
    """
    import time
    _t0 = time.time()
    car.log("  [扫码] ① 先把 base 顺时针 180° **发出去不等**，同时车开始前进（两者并行）")
    _t_base = car.base_cw(BASE_180, TTL_VEL, tag="扫码朝向", wait=False)
    car.fwd(TO_SCAN)                            # 阻塞：等底盘到位
    _left = float(_t_base or 0.0) - (time.time() - _t0)
    if _left > 0.05:
        car.log("  [扫码] ② 前进已到位，base 还差 %.2fs → 等它转完（两者取慢的）" % _left)
        time.sleep(_left)
    else:
        car.log("  [扫码] ② 前进已到位，base 同时也已转完（省下 %.2fs）" % (-_left))
    car.log("  [扫码] 开始读取侧装相机上的二维码")
    try:
        code = scan_from_monitor(log=car.log)
        if code:
            car.show_qr(code)                   # 赛规要求把任务码显示在车上
        else:
            code = SC.CODE_DEFAULT              # 超时用兜底码（与真比赛一致）
            car.log("  （没读到码 → 用兜底码 %s 继续）" % code)
        car.code = code                         # ★后面原料区/加工区靠它决定去哪个号位
        _pick, _place = SC.parse_code(code)
        car.log("  [任务码] %s → 取料顺序 %s / 放置顺序 %s"
                % (code, _pick or "?", _place or "?"))
    finally:
        # ★放 finally：无论扫码成功、超时还是 Ctrl-C，都要把 base 转回原位
        car.log("  [扫码] 转回原位（逆时针 180°）")
        car.base_ccw(BASE_180, TTL_VEL, tag="回正")


def s3_material(car):
    """③ 进原料区 + base 左转 90° → 按任务码顺序走位 → **逐个瞄准取料并放到对应物架**。

    三个号位沿车前进方向排成一列，进区时正好对着 1 号位，往前依次是 2/3 号位（间隔 PITCH）。
    ★2026-09-18 车主："将 grab 放入跑图过程中，在走位到位之后的三个位置各跑一次瞄准抓取，
      自动放置在对应置物架上"；规则："红→1号架 / 绿→2号架 / 蓝→3号架"（见 RACK_OF_COLOR）。
    ★"base 左转 90° 到工作位"这一步**本来就有**（下面 car.base_left），不用另加。
    ★把 PICK_IN_ROUTE 设 False 就退回原来的"只走位"，方便单独标定跑图。
    """
    car.fwd(TO_MATERIAL)
    car.cur_cm = 0.0                            # 进区作业起点 = 0（= 正对 1 号位）
    car.base_left(BASE_90, TTL_VEL)             # ← 原有动作，保持不动
    code = car.code or SC.CODE_DEFAULT
    pick, _place = SC.parse_code(code)
    if not pick:
        car.log("  !! 任务码 %r 不合法 → 用兜底码 %s" % (code, SC.CODE_DEFAULT))
        pick, _place = SC.parse_code(SC.CODE_DEFAULT)
    car.log("  [原料区] 取料顺序 %s（%s）→ 按这个顺序在 1/2/3 号位之间前后移动"
            % ("→".join(pick), "、".join("%s=%s" % (m, SC.color_name(m)) for m in pick)))

    # 作业所需的瞄准 / 取料 / 放物架实现都在 grab_demo 里（已过现场调试，不重复造）。
    # ★必须在**函数内** import：grab_demo 顶层有 `import route_main as RM`，
    #   顶层互相 import 会成环 → 这里延迟到调用时才导入就没事。
    aim_and_track = rack_zero = _run_rack = None
    RACK_STEPS = RACK_HOME_BEFORE = RACK_WORK_DEG = None
    PICK_STEPS = None
    if PICK_IN_ROUTE:
        from grab_demo import (aim_and_track, rack_zero, _run_rack, PICK_STEPS,
                               RACK_STEPS, RACK_HOME_BEFORE, RACK_WORK_DEG,
                               merge_zero_into_steps)   # ★原料区也把"归零"并进动作组
        _base_deg = RACK_WORK_DEG               # 进作业位后 base 就在 90°（上面左转过了）
        car.log("  [原料区] 作业已开启：每个号位「瞄准 → 取料 → 放到对应置物架」")
    else:
        car.log("  [原料区] PICK_IN_ROUTE=False → 只走位，不做取料/放置")

    for i, mid in enumerate(pick, 1):
        cm = MATERIAL_CM[int(mid)]
        color = SC.color_name(mid)
        rack = RACK_OF_COLOR.get(color)
        car.log("  [原料区] 第 %d 个：%s 号料（%s）→ %d 号位（%.0fcm）→ 目标 %s 号置物架"
                % (i, mid, color, int(mid), cm, rack if rack else "?"))
        car.goto_cm(cm, tag="  ")

        if not PICK_IN_ROUTE:
            car.pause("已到 %d 号位（%.0fcm）—— 只走位模式；确认对准后继续" % (int(mid), cm))
            continue

        # ① 瞄准（同时把 base/arm 偏移记账，供放料前"归零"用）
        _ok_a, _base_deg, _off = aim_and_track(car, _ColorArg(color), _base_deg,
                                               tag="M%s" % mid)
        # ② 取料：瞄准已在①做过（能拿到 offset），这里 aim=False 免得再瞄一遍
        _ok_p = car.pick_here(expect_color=color, tag="M%s" % mid,
                              steps=PICK_STEPS, blind=True, aim=False)
        if not _ok_p:
            car.log("  !! %s 号料取料没成功 → 跳过它的放置" % mid)
            continue
        # ③ 归零 —— ★2026-09-18 晚：不再在这里**串行**做，改成**并进放物架动作组第①步**
        #    （龙门抬升 + base 回正 + 伸缩回正 = 三轴并行），与加工区完全一致。
        #    原本：rack_zero() 先等 base 一段、再等伸缩一段 → 然后第①步抬龙门又等一段。
        #    base 回正的脉冲就是本次瞄准偏移的相反数，所以精确回到"瞄准之前"，
        #    不需要再补 goto；软跟踪由 _run_rack 自动记账（见 _track_par_base）。
        #    ⚠️ RACK_HOME_BEFORE=False（关掉归零）时不传 off，退回老行为。
        # ④ 放到对应物架
        if not rack:
            car.log("  !! 颜色 %s 没有对应的置物架规则 → 夹着料跳过（检查 RACK_OF_COLOR）" % color)
            continue
        _r_steps = merge_zero_into_steps(
            RACK_STEPS[rack], _off if RACK_HOME_BEFORE else None, base_vel=TTL_VEL)
        if RACK_HOME_BEFORE and ((_off or {}).get("base") or (_off or {}).get("arm")):
            car.log("  [原料区] ★把「归零」并进放物架第①步：龙门抬升 + base 回正(%+d) "
                    "+ 伸缩回正(%+d) 三轴并行"
                    % (int((_off or {}).get("base", 0) or 0),
                       int((_off or {}).get("arm", 0) or 0)))
        _base_deg, _n_ok, _n_all = _run_rack(car, _r_steps, rack, _base_deg,
                                             prefix="[%s→%d号架] " % (mid, rack))
        car.log("  [原料区] %s 号料（%s）→ %d 号置物架：%d/%d 步，base≈%.0f°"
                % (mid, color, rack, _n_ok, _n_all, _base_deg))
        car.pause("第 %d 个（%s）已放到 %d 号架；确认后继续下一个"
                  % (i, color, rack))

    # ★2026-09-18 晚 车主："改为直接从当前区域任务完成位置直接移出"—— 不再绕回 1 号位。
    #   补偿放在**下一段 s4**（少走 cur_cm，保证出区后的**绝对位置与老流程完全一致**），
    #   所以这里只需要"别动"，把当前坐标留给 s4 去算。
    car.log("  [原料区] 三个号位走完 → **就地停下**（不回 1 号位），当前 %.0fcm；出区补偿交给 ④"
            % float(getattr(car, "cur_cm", 0.0) or 0.0))


class _ColorArg:
    """只为满足 grab_demo.aim_and_track(car, a, …) 里 `a.color` 的鸭子类型。"""
    __slots__ = ("color",)

    def __init__(self, color):
        self.color = color


def s4_leave_material(car):
    """④ 出原料区：**从当前所在料位直接前进**（不再先绕回 1 号位）。

    ★2026-09-18 晚 车主："我打算将原料区和加工区的三个位置移动（原来不是都要回到 1 号位
      再进行定距离移动吗）改为直接从当前区域任务完成位置直接移出"。
    做法：前进量 = TO_PROCESS − **当前所在料位** —— 这样**出区后的绝对位置与老流程完全一致**，
      后面所有段（自旋、进加工区…）的里程一个都不用改；省掉的只是"从最后一个料位
      退回 1 号位"的那段往返。
      例：老流程 3 号位(24cm) 先退到 0cm 再前进 80cm；现在直接从 24cm 前进 56cm。
    读不到 cur_cm 时退回原行为（不会少走、也不会撞）。
    """
    cur = float(getattr(car, "cur_cm", 0.0) or 0.0)
    fwd = TO_PROCESS - cur
    car.log("  [出原料区] 从当前 %.0fcm 直接前进 %.0fcm（= TO_PROCESS %.0f − 当前位置 %.0f；"
            "不回 1 号位，出区后绝对位置仍与老流程一致）" % (cur, fwd, TO_PROCESS, cur))
    if fwd >= 0:
        car.fwd(fwd)
    else:
        car.back(-fwd)


def s5_spin(car):
    """⑤ 原地自旋（顺时针）。"""
    car.spin(SPIN_DEG)


def s6_process(car):
    """⑥ 进加工区 + base 左转 90° → **按放置顺序逐个靶位「瞄准 → 取料 → 放置 → 回零」**。

    放置映射：物料编号 → 靶位编号（`school_config.PLACEMENT_MAP`，**同色对齐**：1→1 / 2→2 / 3→3）
    —— ★2026-09-19 车主："将加工区三个物料摆放改为颜色对应摆放（只动这里）"。
    ★2026-09-18 车主："也把 place 嵌入到 route 中，记得每次在加工区标靶上放完物料也要进行回零"。
      每个靶位的完整工序 = `place_demo.run_one()`：
          回 90° 作业位 → 瞄准该靶位的标靶颜色 → 归零 → 去物架夹料 → 回放到靶心
          → 放置（下放/松爪/抬回）→ **回零**
    ★"回零"为什么必须做：瞄准会动**伸缩**（相对位移），放完不回零的话，下一个靶位的
      伸缩起点都不一样；base 还能靠 goto 纠正，**伸缩没人纠正** → 可能撞物架。
    ★把 `PLACE_IN_ROUTE` 设 False 就退回原来的"只走位"，方便单独标定跑图。
    """
    car.fwd(ENTER_PROCESS)
    car.cur_cm = 0.0
    code = car.code or SC.CODE_DEFAULT
    _pick, place = SC.parse_code(code)
    if not place:
        _pick, place = SC.parse_code(SC.CODE_DEFAULT)
    plan = SC.place_plan(place)                 # [(物料号, 靶位号), ...]
    car.log("  [加工区] 放置顺序 %s → %s"
            % (place, "、".join("%s号料→%d位" % (m, t) for m, t in plan)))

    # 作业所需的「瞄准 / 归零 / 取料 / 回放 / 放置 / 回零」实现都在 place_demo 里
    # （已过现场调试，不重复造）。
    # ★必须在**函数内** import：place_demo 顶层 import 了 grab_demo，而 grab_demo
    #   顶层又 `import route_main as RM` → 顶层互相 import 会成环，延迟到这里就没事
    #   （与上面 s3_material 里 import grab_demo 是同一个道理）。
    run_one = TARGET_COLOR = None
    _base_deg = 0.0
    if PLACE_IN_ROUTE:
        from place_demo import run_one, TARGET_COLOR, RACK_WORK_DEG as _PLACE_WORK
        _base_deg = _PLACE_WORK        # 进加工区后 base 仍在 90°（原料区那边左转过后没再动）
        car.log("  [加工区] 作业已开启：每个靶位「瞄准 → 归零 → 取料 → 回靶心 → 放置 → 回零」")
    else:
        car.log("  [加工区] PLACE_IN_ROUTE=False → 只走位，不做取料/放置")

    for i, (mid, tgt) in enumerate(plan, 1):
        tgt = int(tgt)
        cm = TARGET_CM[tgt]
        color = SC.color_name(mid)
        # ★按"**这块料的颜色**"决定从哪个物架取料 —— 物架里装的就是按颜色归置的料
        #   （红→1号架 / 绿→2号架 / 蓝→3号架，见 RACK_OF_COLOR）。
        #   ★2026-09-19 起 PLACEMENT_MAP 已是**同色对齐**，所以物架号恰好 == 靶位号；
        #     但仍然按颜色算 —— 万一以后规则再变，这里不用动。
        rack = RACK_OF_COLOR.get(color)
        car.log("  [加工区] 第 %d 个：%s 号料（%s）→ %d 号靶位（%.0fcm）→ 从 %s 号物架取料"
                % (i, mid, color, tgt, cm, rack if rack else "?"))
        car.goto_cm(cm, tag="  ")

        if not PLACE_IN_ROUTE:
            car.pause("已到 %d 号靶位（%.0fcm）—— 只走位模式；确认对准后继续" % (tgt, cm))
            continue

        # 目标标靶颜色 vs 这块料的颜色 —— ★**同色对齐**（2026-09-19 车主改为颜色对应摆放）：
        #   物料 i → i 号靶位，而 i 号靶位的标靶颜色恰好就是物料 i 的颜色，
        #   所以正常情况下**不会触发**；一旦触发，说明 PLACEMENT_MAP 或颜色表被改乱了，
        #   那种情况**放置位置真的会错**，所以这里保留成 ⚠️ 告警（不再是"正常"说明）。
        tcolor = TARGET_COLOR.get(tgt)
        if tcolor and color and tcolor != color:
            car.log("  ⚠️ 【颜色不匹配】%s 号料是【%s】，但 %d 号靶位的标靶是【%s】——"
                    "同色对齐规则下不该发生，请检查 school_config.PLACEMENT_MAP / COLOR_OF"
                    % (mid, tgt, tcolor))
        if not rack:
            car.log("  !! 颜色 %s 没有对应的物架规则 → 跳过这个靶位（检查 RACK_OF_COLOR）" % color)
            continue

        _base_deg, _off = run_one(car, tgt, _base_deg,
                                  rack=int(rack), prefix="[%d号靶] " % tgt)
        car.pause("第 %d 个（%s号料 → %d 号靶位）已放置并回零；确认后继续" % (i, mid, tgt))

    # ★2026-09-18 晚：同原料区 —— 放完最后一个料**就地停下**，不回 1 号靶位；
    #   出区补偿交给 ⑦（少走 cur_cm，保证结束段的绝对位置不变）。
    car.log("  [加工区] 三个靶位走完 → **就地停下**（不回 1 号靶位），当前 %.0fcm；出区补偿交给 ⑦"
            % float(getattr(car, "cur_cm", 0.0) or 0.0))


def s7_finish(car):
    """⑦ 结束段：前进 + 右移长距 + 停车。

    ★2026-09-18 晚：前进量补偿成 TO_FINISH − 当前靶位，使**结束位置与老流程一致**
      （加工区不再绕回 1 号靶位，省掉那段往返）。
    """
    cur = float(getattr(car, "cur_cm", 0.0) or 0.0)
    fwd = TO_FINISH - cur
    car.log("  [结束段] 从当前 %.0fcm 直接前进 %.0fcm（= TO_FINISH %.0f − 当前位置 %.0f）"
            % (cur, fwd, TO_FINISH, cur))
    if fwd >= 0:
        car.fwd(fwd)
    else:
        car.back(-fwd)
    car.right(FINISH_RIGHT)
    car.right(31)
    car.fwd(17)
    car.stop()


STAGES = [
    ("① 出启动区（右移）",                      s1_out,            "量：右移了多少 cm（应为 RIGHT_OUT）"),
    ("② 前进到扫码位 + base 转180°扫码 + 转回",  s2_scan,           "量：前进距离（应为 TO_SCAN）；看 base 转完 180° 后相机是否正对二维码、能否稳定读码"),
    ("③ 进原料区 + base 左转 + 按取料顺序走位",    s3_material,       "量：进区距离(TO_MATERIAL)、base 是否 90°；每个号位确认「车正对料」（相邻号位 10cm）"),
    ("④ 出原料区（前进）",                       s4_leave_material, "量：前进距离（应为 TO_PROCESS）"),
    ("⑤ 原地自旋（顺时针）",                     s5_spin,           "量：实际转了多少度（应为 SPIN_DEG）"),
    ("⑥ 进加工区 + base 左转 + 按放置顺序走位",    s6_process,        "量：进区距离(ENTER_PROCESS)、base 是否 90°；每个靶位确认对准（相邻 10cm）"),
    ("⑦ 结束段（前进 + 右移长距 + 停车）",        s7_finish,         "量：前进/右移距离（TO_FINISH / FINISH_RIGHT）"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/stm32", help="串口（udev 固定名；也可 /dev/ttyUSB1）")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dry-run", action="store_true", help="不连车/不动电机，验证流程与分段交互")
    ap.add_argument("--auto", action="store_true", help="一口气跑完，不在每段后停下")
    ap.add_argument("--start", type=int, default=1, help="从第几段开始（1~7）")
    ap.add_argument("--no-monitor", action="store_true", help="不开双摄推流")
    ap.add_argument("--monitor-port", type=int, default=MONITOR_PORT)
    ap.add_argument("--scan-cam", type=int, default=MONITOR_SCAN_CAM, help="侧装扫码相机编号")
    ap.add_argument("--init", dest="init", action="store_true", default=None,
                    help="强制做上电初始化动作组（默认按参数区 INIT_ON_START）")
    ap.add_argument("--no-init", dest="init", action="store_false",
                    help="★跳过上电初始化（臂已在起始位时用这个，否则会顶死限位）")
    a = ap.parse_args()

    car = RouteCar(port=a.port, baud=a.baud, dry_run=a.dry_run)
    car.auto = bool(a.auto)          # --auto = 不在每步停下；默认每步等回车
    mon = None
    try:
        car.log("=" * 66)
        car.log("VisualBot2 跑图（底盘 + base%s）"
                % (" + 原料区取料/放置" if PICK_IN_ROUTE else "；机械臂不参与"))
        car.log("=" * 66)
        if not a.dry_run:
            car.setup(chassis_vel=CHASSIS_VEL, chassis_acc=CHASSIS_ACC, chassis_dec=CHASSIS_DEC,
                      base90=BASE_90, ttl_vel=TTL_VEL,
                      # ★把夹爪角度也下发：瞄准前"张到初始开度"靠的就是固件里的
                      #   SERVO_GRIP_OPEN，不下发的话固件用自己的默认值，可能张不够
                      grip_close_angle=GRIP_CLOSE_ANGLE, grip_open_angle=GRIP_OPEN_ANGLE)
        else:
            car.log("[setup] dry-run：跳过参数下发")

        # ★装相机 + 注入瞄准参数（2026-09-18：跑图开始做取料/放置了，必须开相机）
        #   参数**直接复用 grab_demo 参数区**——那些值是现场一点点标出来的
        #   （实测 K、分段限幅、arm 专项阻尼、近中心软化、中值滤波…），
        #   在这里再写一份必然走样。grab_demo 顶层 import 了 route_main，但那是模块级、
        #   此时 route_main 早已加载完，不会成环。
        hub = None            # ★FrameHub：作业相机读到的帧顺手喂给推流（见下面 use_camera）
        if PICK_IN_ROUTE:
            try:
                from vision import FrameHub
                hub = FrameHub()
                import grab_demo as G
                SC.K_PX2STEP_BASE = G.K_BASE
                SC.K_PX2STEP_ARM = G.K_ARM
                for _k in G.AIM_KEYS + G.VISION_KEYS:
                    _v = getattr(G, _k, None)
                    if _v is not None:
                        setattr(SC, _k, _v)
                car.log("[aim] 瞄准/滤波参数已从 grab_demo 注入（K_BASE=%.4f K_ARM=%.4f，"
                        "分段限幅%s，软化 %.0fpx×%.2f）"
                        % (G.K_BASE, G.K_ARM, G.AIM_LIMIT_STAGES,
                           G.AIM_SOFT_ZONE_PX, G.AIM_SOFT_GAIN))
                car.use_camera(cam_index=CAM_INDEX, perf=PERF, exposure=CAM_EXPOSURE,
                               rotate=CAM_ROTATE, width=CAM_WIDTH, hub=hub)
                _rm = getattr(car.vision, "rotate_mode", None)
                if _rm not in (None, "none"):
                    car.log("!! 画面方向 = %s（不是 none）—— K 值按 none 标的，"
                            "方向不对会让 x/y 轴对调、瞄准必废！" % _rm)
                else:
                    car.log("[camera] 画面方向 = none ✓（与 K 值标定坐标系一致）")
            except Exception as _e:                                  # noqa: BLE001
                car.log("!! 开相机/注入瞄准参数失败：%r —— 原料区作业会被跳过" % _e)

        # ★上电初始化：把机械臂从"摆放位"送到"起始位"（相对行程，只在刚上电时做一次）
        do_init = INIT_ON_START if a.init is None else bool(a.init)
        if do_init:
            car.log("[init] 上电初始化 %d 步（整组交下位机执行）" % len(INIT_STEPS))
            if not car.init_axes(INIT_STEPS):
                car.log("!! 初始化没跑成功 —— 臂不在起始位时，后面 base 转动会把臂带着乱摆")
        else:
            car.log("[init] 跳过初始化（--no-init）：臂应已停在起始位")

        if MONITOR and not a.no_monitor:
            # ★作业模式下推流**不开作业相机那一路**（见 start_monitor 注释：
            #   两处同时 read /dev/video0 会互相偷帧，把瞄准的抽帧修复废掉）。
            # ★作业模式传 hub：推流的 work 那一路直接用**主程序（瞄准）读到的帧**
            #   —— 既不抢相机、不重复读同一台 V4L2，页面上**也能看到作业画面**。
            #   （之前只传 work_cam=-1 导致 work 路没启用，页面上只剩扫码那路，
            #     车主看到的现象是"推流开成了扫码的摄像头"。）
            mon = start_monitor(log=car.log, hub=hub,
                                work_cam=(-1 if PICK_IN_ROUTE else CAM_INDEX))
            if hub is not None:
                car.log("[monitor] 作业画面走 FrameHub 共享帧（不抢相机），扫码那路照开")

        # ★2026-09-18 晚 车主："我指的是在 auto 模式里加入最一开始的回车等待" ——
        #   `--auto` 会让 car.pause() 全程自动跳过，但**开跑前这一次**要保留：
        #   初始化刚做完，得让车主确认场地清空 / 料摆好，再让车动。
        #   non-auto 模式下 s1_out 里本来就有一次 pause，所以这里**只补 auto 那一次**，
        #   免得两种模式都等两遍。（传 auto=False = 强制等，不受 --auto 影响。）
        if a.auto and max(1, a.start) == 1:
            car.pause("初始化完成 —— 确认场地清空、料已摆好，按回车开跑（q=退出）", auto=False)

        for i, (name, fn, hint) in enumerate(STAGES, 1):
            if i < max(1, a.start):
                continue
            car.log("")
            car.log("─" * 66)
            car.log("▶ 第 %d/%d 段：%s" % (i, len(STAGES), name))
            t0 = time.time()
            fn(car)
            car.log("✓ 本段完成（%.1fs）；累计 %.1fs" % (time.time() - t0, car.seconds()))
            if not a.auto:
                # ★2026-09-18 晚 把提示做得**醒目**：车主反馈"向右平移出启动区后长时间停止延时"，
                #   很可能就是这里在**等回车**（提示原来混在滚动日志里，看着像卡死）。
                car.log("")
                car.log("⏸ 【本段完成 —— 等你按回车才走下一段】要量的东西：" + hint)
                try:
                    ans = input("   ▶ 按回车继续（q 退出）> ").strip().lower()
                except EOFError:
                    car.log("   （stdin 结束 → 退出）")
                    break
                if ans == "q":
                    car.log("   收到 q → 停在当前段")
                    break
            else:
                car.log("   【要量的东西】" + hint)
        car.log("")
        car.log("=" * 66)
        car.log("跑图结束：累计 %.1fs" % car.seconds())
        car.log("下一步：把下面这些实测值填回去（或跑 route_debug.py 让它算比例）")
        for k, v in (("RIGHT_OUT", RIGHT_OUT), ("TO_SCAN", TO_SCAN), ("TO_MATERIAL", TO_MATERIAL),
                     ("TO_PROCESS", TO_PROCESS), ("SPIN_DEG", SPIN_DEG),
                     ("ENTER_PROCESS", ENTER_PROCESS), ("TO_FINISH", TO_FINISH),
                     ("FINISH_RIGHT", FINISH_RIGHT), ("BASE_90", BASE_90)):
            car.log("   %-14s = %s" % (k, v))
        car.log("=" * 66)
    except KeyboardInterrupt:
        car.log("!! 人工中断 → 停车")
        car.stop()
        return 130
    except Exception as exc:                                     # noqa: BLE001
        # ★串口掉线（USB 断 / 被占用）不甩 traceback：给排查步骤并干净停机
        try:
            from lower_api import LowerLinkError
        except Exception:                                        # noqa: BLE001
            LowerLinkError = ()                                  # type: ignore
        if isinstance(exc, LowerLinkError):
            car.log("!! 串口掉线：%s" % exc)
            car.log("   排查顺序：① USB 线被车拖动/松动（换线换口、扎带固定）")
            car.log("             ② 动力电把 USB 拉垮（电机一起步就掉 → 供电分开）")
            car.log("             ③ 有别的程序占着串口：pgrep -af python3")
            car.log("             ④ 设备还在不在：ls -l /dev/stm32 /dev/ttyUSB*")
            return 2
        car.log("!! 出错：%r" % exc)
        return 3
    finally:
        stop_monitor(mon)
        car.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
