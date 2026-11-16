# -*- coding: utf-8 -*-
"""
school_config.py —— 校赛规则常量（改这里就能改比赛行为，不用碰主程序）
=======================================================================
设计原则：**能配的都不硬编码**。所有"现场才知道"的数字集中在本文件 +
`motion_profile.json`（动作组/里程），别写进 school_flow.py。

⚠️ 标 [待确认] 的项见 校赛开发.md §9.3，赛前必须核对。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# ----------------------------------------------------------------------
# 一、任务码语义
# ----------------------------------------------------------------------
# 校赛码形如 "123+321"：前段=取料顺序，后段=放置顺序（数字=颜色编号）
CODE_DEFAULT = "123+321"        # 扫码超时时的兜底码（会告警，但保证能跑完）
CODE_RE = r"^([123]{3})\+([123]{3})$"

# 颜色 ↔ 物料编号（校赛只有红绿蓝三色，位置固定）
COLOR_OF = {"1": "red", "2": "green", "3": "blue"}     # 编号 -> 颜色名
NUM_OF = {v: k for k, v in COLOR_OF.items()}           # 颜色名 -> 编号
POS_OF = {"1": 1, "2": 2, "3": 3}                      # 编号 -> 原料区固定位置 [待确认]

# 放置映射：物料编号 -> 加工区靶位编号（3->1, 2->2, 1->3，交叉）
PLACEMENT_MAP = {"3": 1, "2": 2, "1": 3}

# ----------------------------------------------------------------------
# 二、路线与工位（单位 cm / 度；全部可按现场实测覆盖）
#     最终以 motion_profile.json 的 "route" 段为准（本文件只给默认值）
# ----------------------------------------------------------------------
ROUTE_DEFAULT = {
    "leave_start_right_cm": 30,     # 右移出启动区
    "to_scan_fwd_cm": 60,           # 前进到扫码位
    "to_material_fwd_cm": 80,       # 前进进原料区
    "material_pitch_cm": 35,        # 原料区相邻物料间距
    "to_process_fwd_cm": 70,        # 出原料区前进
    "spin_deg": 90,                 # 原地自旋
    "enter_process_fwd_cm": 50,     # 自旋后进加工区
    "target_pitch_cm": 35,          # 加工区相邻靶位间距
    "to_finish_fwd_cm": 60,         # 出加工区前进
    "finish_right_cm": 120,         # 向右长距平移进结束位
}

# 工位纵向坐标（cm，相对"进料区作业起点"=0，前进为正）—— 用来算"该前进还是后退"
# 物料位：[待确认] 默认位1最近、位3最远
MATERIAL_POS_CM = {1: 0.0, 2: 35.0, 3: 70.0}
# 加工靶位：默认靶A最近、靶C最远
TARGET_POS_CM = {1: 0.0, 2: 35.0, 3: 70.0}

# ----------------------------------------------------------------------
# 三、瞄准参数（来自 aim_and_place.py 的现场标定值；改了要同步标定）
# ----------------------------------------------------------------------
K_PX2STEP_BASE = 0.2593      # base 轴：像素 -> 脉冲
K_PX2STEP_ARM = 0.0413       # 伸缩轴：像素 -> 脉冲
CAM_GRIP_OFFSET_X = 0.0      # 夹爪与光轴不共轴补偿（px，现场实测）
CAM_GRIP_OFFSET_Y = 0.0
AIM_TOL_PX = 8.0             # 收敛判据：|dx|,|dy| <= 8px 且连续 2 帧
AIM_SETTLE_S = 0.4           # 每次动作后停稳等待（补偿视觉延迟）
AIM_OUT_MAX_PX = 30          # 单拍限幅（防冲过头）
AIM_MAX_ITER = 16            # 单次瞄准最大迭代（每拍限幅 30px，够覆盖 ~200px 初始偏差）
AIM_RETRY = 3                # 丢目标时的小步试探次数
AIM_PROBE_PX = 40            # 丢目标试探步长（px -> 脉冲换算后下发）

# ----------------------------------------------------------------------
# 四、超时（秒）—— 赛规"静止 15s 判负"，所以每个动作都要有超时
# ----------------------------------------------------------------------
T_ACK_FAST = 3.0             # 普通命令 ACK
T_ACK_ACT = 25.0             # 阻塞动作组 ACK（0x13/0x16/0x24）
T_SCAN = 10.0                # 扫码
T_MOVE = 10.0                # 单段移动
T_AIM = 20.0                 # 单次瞄准
T_PLACE_MISSION = 60.0       # 单次放置 mission（瞄+取+放）
T_FINISH = 15.0              # 结束段

# ----------------------------------------------------------------------
# 五、视觉参数（性能档与检测开关）
# ----------------------------------------------------------------------
PERF = "rdk"                 # RDK X5 专用档（512 处理 / proc_scale 0.34 / block25 / close5）
CAM_INDEX = 0                # [待确认] 侧装扫码相机 / 前方作业相机 = 两个不同 index
CAM_FRONT = 1                # 前方作业相机（瞄准用）
CAM_SIDE = 0                 # 侧装扫码相机
QR_CONSISTENT_N = 3          # 连续 N 帧解出同一个码才采纳（防误读）
QR_SCAN_INTERVAL_S = 0.15    # 扫码帧间隔

# 实测：RDK X5 上 ring_stream_ui --perf rdk = 15.8fps → 单帧 ~63ms
FRAME_MS_EST = 63.0


# ----------------------------------------------------------------------
# 六、装载动作组/里程（优先用 JSON，缺项用本文件默认）
# ----------------------------------------------------------------------
def _strip_jsonc(text):
    """去掉 JSON 里的 // 行注释与 /* */ 块注释（JSON 标准不支持注释, 但现场更爱写注释）。

    只处理**字符串之外**的注释 —— 路径里的 "//"（如 "http://x"）不会被误删。
    支持 JSONC 后, motion_profile.json 可以逐行写说明, 现场改起来不容易踩坑。"""
    out, i, n = [], 0, len(text)
    in_str, esc = False, False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def load_profile(path=None):
    """读 motion_profile.json（允许 // 与 /* */ 注释）；缺失字段用默认值补齐。"""
    p = path or os.path.join(HERE, "motion_profile.json")
    with open(p, encoding="utf-8") as f:
        raw = f.read()
    try:
        prof = json.loads(raw)
    except json.JSONDecodeError:
        prof = json.loads(_strip_jsonc(raw))          # 带注释的版本走这里
    route = dict(ROUTE_DEFAULT)
    route.update(prof.get("route") or {})
    prof["route"] = route
    prof.setdefault("params", {})
    prof.setdefault("groups", {})
    return prof


def parse_code(text):
    """'123+321' -> ('123', '321')；不合法返回 (None, None)。"""
    import re
    if not text:
        return None, None
    m = re.match(CODE_RE, text.strip())
    if not m:
        return None, None
    return m.group(1), m.group(2)


def place_plan(place_order):
    """放置顺序 -> [(物料编号, 目标靶位编号), ...]"""
    return [(mid, PLACEMENT_MAP[mid]) for mid in place_order]


def color_name(num):
    return COLOR_OF.get(str(num), "?")
