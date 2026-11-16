# -*- coding: utf-8 -*-
"""
lower_api.py —— 下位机串口 API（上位机侧唯一的"下位机入口"）
================================================================
设计目标（对应 校赛开发.md §3）：
  · 下位机只提供**空函数接口**，速度/脉冲/加速度/动作组步骤全部由上位机下发；
  · 本文件把"下发"和"执行"两件事都封成方法，主程序里看不到任何魔数；
  · 纯逻辑可测：`MockLower` 不需要串口/硬件，本机就能跑整场流程（--dry-run）。

帧格式（沿用固件）： 0xFF + cmd + 7B payload + 0xFE   共 10 字节，无校验，多字节大端
命令字：0x01~0x16 沿用旧表；本文件新增 0x20~0x24（见 校赛开发.md §3.3）

   0x20 SET_PARAM      : id_be16(2) + value_be32(4) + 0
   0x21 SET_MOTION_STEP: 见 _STEP_* 三个子类型（一条帧放不下脉冲，拆两条）
   0x22 GET_PARAM      : id_be16(2) + 0×5      ←下位机回：id_be16 + value_be32 + 0
   0x23 SAVE_PARAMS    : 0×7
   0x24 MOTION_RUN     : group(1) + slot(1) + 0×5

回归：python test_school_local.py
"""
from __future__ import annotations

import time

HEAD, TAIL = 0xFF, 0xFE

# ---------------- 命令字（新增部分） ----------------
CMD_SET_PARAM = 0x20
CMD_SET_STEP = 0x21
CMD_GET_PARAM = 0x22
CMD_SAVE_PARAMS = 0x23
CMD_MOTION_RUN = 0x24

# 旧命令（沿用）
CMD_MOVE_X_CM = 0x01        # 前后（+ 前进）
CMD_MOVE_Y_CM = 0x02        # 左右（★负=右移，见下方 _RIGHT_SIGN；以固件 chassis.c 为准）
CMD_ROTATE_DEG = 0x03
CMD_SHOW_QR = 0x04
CMD_TTL_M1 = 0x10           # 伸缩轴
CMD_TTL_M23 = 0x11          # 龙门双轴
CMD_TTL_M4 = 0x12           # base 轴
CMD_PLACE = 0x13            # 内建放料动作组
CMD_GRIP = 0x14             # 夹爪(★舵机): buf[1]=0 开 / 1 闭
CMD_GRIP_ANG = 0x15         # 夹爪(★舵机) 任意角度: buf[1]=0~255°

# 0x21 子类型
_STEP_CLEAR = 0
_STEP_HEAD = 1
_STEP_PULSES = 2
_STEP_COMMIT = 3

# 步骤类型（与 motion_profile.json 里第一个字段对应）
STEP_KIND = {"axis": 1, "grip_close": 2, "grip_open": 3, "wait": 4}

# 内建组（不走 0x21 下发，直接用旧命令）
BUILTIN_PLACE = 0xFE        # → 0x13
BUILTIN_GRAB_VEH = 0xFF     # → 0x16（slot 由 run_group 的第二参数给）

# ---------------- 参数 ID 表（与固件 params.c 对齐） ----------------
P = {
    "chassis_vel": 0x0001, "chassis_acc": 0x0002, "chassis_dec": 0x0003,
    "ttl_default_vel": 0x0010,
    "grip_close_angle": 0x0011,   # ★夹爪是舵机: 闭合角度 0~255°（不是脉冲!）
    "grip_open_angle": 0x0012,    # ★夹爪是舵机: 张开角度 0~255°
    "base_left90_pulses": 0x0020, "base_right90_pulses": 0x0021,
    "lift_up_pulses": 0x0022, "lift_down_pulses": 0x0023,
    "telescope_out_pulses": 0x0024, "telescope_in_pulses": 0x0025,
    "axes_home_speed": 0x0030,
}
# 下发后要读回校验的关键参数（防"以为设了其实没设"）
CHECKED = ["chassis_vel", "chassis_acc", "grip_close_angle", "base_left90_pulses"]


class PackError(ValueError):
    pass


def _i8(v):
    v = int(v)
    if not (-128 <= v <= 127):
        raise PackError("int8 越界: %d" % v)
    return v & 0xFF


def _be16(v):
    v = int(v) & 0xFFFF
    return (v >> 8) & 0xFF, v & 0xFF


def _be32(v):
    v = int(v)
    if not (0 <= v <= 0xFFFFFFFF):
        raise PackError("32位越界: %d" % v)
    return ((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def frame(cmd, b1=0, b2=0, b3=0, b4=0, b5=0, b6=0, b7=0):
    """通用帧（不限制命令字范围，0x20~0x24 也走这里）。"""
    return bytes([HEAD, int(cmd) & 0xFF,
                  b1 & 0xFF, b2 & 0xFF, b3 & 0xFF, b4 & 0xFF,
                  b5 & 0xFF, b6 & 0xFF, b7 & 0xFF, TAIL])


# ---------------- 帧构造（给回归直接断言用） ----------------
def pack_set_param(pid, value):
    a, b = _be16(pid)
    v3, v2, v1, v0 = _be32(value)
    return frame(CMD_SET_PARAM, a, b, v3, v2, v1, v0, 0)


def pack_get_param(pid):
    a, b = _be16(pid)
    return frame(CMD_GET_PARAM, a, b, 0, 0, 0, 0, 0)


def pack_save_params():
    return frame(CMD_SAVE_PARAMS)


def pack_step_clear(gid):
    return frame(CMD_SET_STEP, _STEP_CLEAR, gid, 0, 0, 0, 0, 0)


def pack_step_head(gid, idx, kind, axis, d, vel):
    return frame(CMD_SET_STEP, _STEP_HEAD, gid, idx, kind, axis, d, vel)


def pack_step_pulses(gid, idx, pulses):
    k3, k2, k1, k0 = _be32(pulses)
    return frame(CMD_SET_STEP, _STEP_PULSES, gid, idx, k3, k2, k1, k0)


def pack_step_commit(gid, nsteps):
    return frame(CMD_SET_STEP, _STEP_COMMIT, gid, nsteps, 0, 0, 0, 0)


def pack_motion_run(gid, slot=0):
    return frame(CMD_MOTION_RUN, gid, slot, 0, 0, 0, 0, 0)


def pack_move_x_cm(cm):
    return frame(CMD_MOVE_X_CM, _i8(cm))


def pack_move_y_cm(cm):
    return frame(CMD_MOVE_Y_CM, 0, _i8(cm))


def pack_rotate_deg(deg):
    return frame(CMD_ROTATE_DEG, 0, 0, _i8(deg))


def pack_grip(close_):
    """0x14 夹爪（★舵机，固件 claw.c 的 Grip_Close/Grip_Open）开/闭。
    具体角度由固件 car_config.h 的 SERVO_GRIP_OPEN / SERVO_GRIP_CLOSE 决定。"""
    return frame(CMD_GRIP, 1 if close_ else 0)


def pack_grip_angle(deg):
    """0x15 夹爪（★舵机）设任意角度 0~255°。瞄准时可设 0° 完全张开，免得爪片挡住摄像头。"""
    d = int(deg)
    if not (0 <= d <= 255):
        raise PackError("夹爪角度越界: %d（舵机 0~255°）" % d)
    return frame(CMD_GRIP_ANG, d)


def pack_ttl(axis, d, vel, pulses):
    """axis 1→0x10, 2/3→0x11, 4→0x12；buf[1]=dir, buf[2..3]=vel, buf[4..7]=pulses。"""
    cmd = {1: CMD_TTL_M1, 2: CMD_TTL_M23, 3: CMD_TTL_M23, 4: CMD_TTL_M4}.get(int(axis))
    if cmd is None:
        raise PackError("轴号只能是 1/2/3/4（收到 %r）" % axis)
    vh, vl = _be16(vel)
    k0, k1, k2, k3 = _be32(pulses)
    return frame(cmd, 0 if d == 0 else 1, vh, vl, k0, k1, k2, k3)


def pack_show_qr(code):
    """0x04：把任务码前 4 个字符用两个 be16 显示（沿用旧接口，校赛码 7 字符取前 4）。"""
    s = (str(code) + "    ")[:4]
    a = (ord(s[0]) << 8) | ord(s[1])
    b = (ord(s[2]) << 8) | ord(s[3])
    ah, al = _be16(a)
    bh, bl = _be16(b)
    return frame(CMD_SHOW_QR, 0, 0, 0, ah, al, bh, bl)


# ---------------- 左右/旋转方向符号 ----------------
# ★方向约定以**固件为准**（`CLEAN\chassis.c`，2026-09-16 对照修正）：
#     Chassis_MoveY_cm(cm):    cm >= 0 → DIR_LEFT（左移）;  cm < 0 → DIR_RIGHT（右移）
#         （`protocol_and_main.c` 的实测注释也写着"负=右, 已实测校正"）
#     Chassis_MoveX_cm(cm):    cm >= 0 → 前进;            cm < 0 → 后退
#     Chassis_Rotate_deg(deg): deg >= 0 → DIR_CCW（逆时针/左旋）; deg < 0 → DIR_CW（顺时针/右旋）
# ⚠️ 原先这里写成 _RIGHT_SIGN=+1、cw=+1 → "右移"发正值 → 车实际**左移**（已实测暴露），现已修正。
_RIGHT_SIGN = -1                       # 右移 = 发负值
_SPIN_SIGN = {"cw": -1, "ccw": +1}     # 顺时针(CW)=负值；逆时针(CCW)=正值


class LowerBase:
    """共同逻辑：把"名字"翻译成帧，把参数/动作组下发下去。子类只管收发的物理层。"""

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.gid = {}           # 组名 -> 下发用的 id（按 profile 顺序分配）
        self.t0 = time.time()

    def _log(self, *a):
        if self.verbose:
            print("[lower] " + " ".join(str(x) for x in a), flush=True)

    # ---- 子类实现 ----
    def send(self, data, wait_ack=True, timeout=3.0):
        raise NotImplementedError
    def query(self, data, timeout=1.0):
        raise NotImplementedError

    # ---- 高层的"下发" ----
    def sync_profile(self, profile):
        """★上电后一次性同步：参数 + 动作组。返回 (ok, 详情)。"""
        ok, fail = True, []
        for name, val in (profile.get("params") or {}).items():
            pid = P.get(name)
            if pid is None:
                continue
            if not self.set_param(pid, val):
                ok = False
                fail.append("param:%s" % name)
        # 校验关键参数
        for name in CHECKED:
            pid = P.get(name)
            if pid is None:
                continue
            got = self.get_param(pid)
            want = (profile.get("params") or {}).get(name)
            if got is not None and want is not None and int(got) != int(want):
                ok = False
                fail.append("verify:%s(%s!=%s)" % (name, got, want))
        # 动作组
        self.gid = {}
        for i, (gname, steps) in enumerate((profile.get("groups") or {}).items(), start=1):
            self.gid[gname] = i
            if not self.define_group(i, steps):
                ok = False
                fail.append("group:%s" % gname)
        self._log("sync done ok=%s 组=%d %s" % (ok, len(self.gid), ("失败:" + ",".join(fail)) if fail else ""))
        return ok, fail

    def define_group(self, gid, steps):
        if not self.send(pack_step_clear(gid)):
            return False
        for idx, st in enumerate(steps):
            kind_s, axis, d, pulses, vel = (list(st) + [0, 0, 0, 0, 0])[:5]
            kind = STEP_KIND.get(str(kind_s), 1)
            if not self.send(pack_step_head(gid, idx, kind, int(axis), int(d), int(vel))):
                return False
            if not self.send(pack_step_pulses(gid, idx, int(pulses))):
                return False
        return self.send(pack_step_commit(gid, len(steps)))

    def set_param(self, pid, value):
        r = self.send(pack_set_param(pid, value))
        self._log("set_param 0x%04X=%s -> %s" % (pid, value, r))
        return r

    def get_param(self, pid):
        return self.query(pack_get_param(pid))

    def save_params(self):
        return self.send(pack_save_params())

    # ---- 动作执行 ----
    def run_group(self, name, slot=0):
        """执行动作组：内建组走旧命令（0x13/0x16），其余走 0x24。"""
        if name in ("BUILTIN_PLACE", "PLACE"):
            return self.send(frame(CMD_PLACE), timeout=25.0)
        if name in ("BUILTIN_GRAB_VEH", "GRAB_VEH"):
            s = int(slot) or 1
            return self.send(frame(CMD_GRAB_VEH, s), timeout=25.0)
        gid = self.gid.get(name)
        if gid is None:
            self._log("!! 未定义的组:", name)
            return False
        return self.send(pack_motion_run(gid, slot), timeout=25.0)

    def move(self, direction, cm):
        """direction: fwd/back/left/right；cm 正数。"""
        d = str(direction).lower()
        cm = abs(int(cm))
        if d in ("fwd", "forward", "front"):
            return self.send(pack_move_x_cm(+cm), timeout=10.0)
        if d in ("back", "backward", "rear"):
            return self.send(pack_move_x_cm(-cm), timeout=10.0)
        if d == "right":
            return self.send(pack_move_y_cm(_RIGHT_SIGN * cm), timeout=10.0)
        if d == "left":
            return self.send(pack_move_y_cm(-_RIGHT_SIGN * cm), timeout=10.0)
        raise PackError("未知方向: %r" % direction)

    def spin(self, direction, deg):
        s = _SPIN_SIGN.get(str(direction).lower())
        if s is None:
            raise PackError("自旋方向只能是 cw/ccw")
        return self.send(pack_rotate_deg(s * abs(int(deg))), timeout=12.0)

    def grip(self, close_=True, angle=None):
        """夹爪是★舵机★（不是 TTL 电机）：angle 给了就走 0x15 设角度，否则 0x14 开/闭。"""
        if angle is not None:
            return self.send(pack_grip_angle(angle), timeout=5.0)
        return self.send(pack_grip(close_), timeout=5.0)

    def ttl(self, axis, d, vel, pulses):
        return self.send(pack_ttl(axis, d, vel, pulses), timeout=8.0)

    def show_qr(self, code):
        return self.send(pack_show_qr(code))

    def stop(self):
        return self.send(frame(CMD_MOVE_X_CM, 0), timeout=2.0)


class SerialLower(LowerBase):
    """真实串口（pyserial 延迟 import，本机没装也能 import 本模块）。"""

    def __init__(self, port="/dev/ttyUSB0", baud=115200, timeout=0.5, verbose=True):
        super().__init__(verbose)
        import serial                                  # 延迟 import
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.ser.reset_input_buffer()
        self._log("串口已打开 %s @%d" % (port, baud))

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _read_frame(self, timeout=1.0, expect=None):
        """读一帧: 0xFF ... 0xFE。
        expect 给了就按定长读 —— 参数值里可能含 0xFE, 只靠包尾会误截(0x22 读回就踩过)。"""
        t0 = time.time()
        buf = bytearray()
        while time.time() - t0 < timeout:
            b = self.ser.read(1)
            if not b:
                continue
            buf += b
            if expect and len(buf) >= expect:
                return bytes(buf[:expect])
            if len(buf) >= 2 and buf[0] == HEAD and buf[-1] == TAIL:
                return bytes(buf)
            if len(buf) > 32:                          # 防跑飞
                buf = buf[-2:]
        return None

    def send(self, data, wait_ack=True, timeout=3.0):
        self.ser.write(data)
        self.ser.flush()
        if not wait_ack:
            return True
        cmd = data[1]
        t0 = time.time()
        while time.time() - t0 < timeout:
            f = self._read_frame(timeout=0.5)
            # 兼容两种固件:
            #   · 旧固件(26090901TEST): 所有命令统一回 FF 01 FE(PKT_ACK_DONE)
            #   · 新固件(26091601TEST): 0x20~0x24 回显命令字, 旧命令仍回 01
            if f and len(f) >= 3 and (f[1] == cmd or f[1] == 0x01):
                return True
        self._log("!! ACK 超时 cmd=0x%02X（%.1fs）" % (cmd, timeout))
        return False

    def query(self, data, timeout=1.0):
        """读参数(0x22): 固件回 FF 22 idH idL v3 v2 v1 v0 FE(10 字节)。"""
        self.ser.write(data)
        self.ser.flush()
        t0 = time.time()
        while time.time() - t0 < timeout:
            f = self._read_frame(timeout=0.5, expect=10)
            if f and len(f) >= 9 and f[1] == data[1]:
                return (f[4] << 24) | (f[5] << 16) | (f[6] << 8) | f[7]
        return None


class MockLower(LowerBase):
    """干跑用：不发串口，记录调用序列 + 模拟耗时；--dry-run / 本机回归都用它。"""

    def __init__(self, verbose=True, speed=20.0):
        super().__init__(verbose)
        self.calls = []
        self.params = {}
        self.groups = {}
        self.t_sim = 0.0
        self.speed = speed          # 模拟"每秒走多少 cm/度"，只为打印节奏感

    def _rec(self, kind, **kw):
        self.calls.append((kind, kw))

    def send(self, data, wait_ack=True, timeout=3.0):
        cmd = data[1]
        self._rec("frame", cmd="0x%02X" % cmd, raw=data.hex())
        if cmd == CMD_SET_PARAM:
            pid = (data[2] << 8) | data[3]
            self.params[pid] = (data[4] << 24) | (data[5] << 16) | (data[6] << 8) | data[7]
        elif cmd == CMD_SET_STEP:
            # 帧布局: [0]FF [1]21 [2]子类型 [3]gid [4]idx [5..8]各字段  ← 别数错偏移
            sub = data[2] & 0xFF
            gid = data[3] & 0xFF
            if sub == _STEP_CLEAR:
                self.groups[gid] = []
            elif sub == _STEP_HEAD:
                idx = data[4]
                g = self.groups.setdefault(gid, [])
                while len(g) <= idx:
                    g.append({})
                g[idx] = {"kind": data[5], "axis": data[6], "dir": data[7], "vel": data[8]}
            elif sub == _STEP_PULSES:
                g = self.groups.setdefault(gid, [])
                idx = data[4]
                if idx < len(g):
                    g[idx]["pulses"] = ((data[5] << 24) | (data[6] << 16)
                                        | (data[7] << 8) | data[8])
        self._sim_sleep(0.01)
        return True

    def query(self, data, timeout=1.0):
        pid = (data[2] << 8) | data[3]
        return self.params.get(pid)

    def _sim_sleep(self, sec):
        self.t_sim += sec
        time.sleep(0.002)           # 真睡一点点，避免日志爆速

    def move(self, direction, cm):
        self._rec("move", dir=direction, cm=cm)
        self._sim_sleep(abs(cm) / self.speed)
        return True

    def spin(self, direction, deg):
        self._rec("spin", dir=direction, deg=deg)
        self._sim_sleep(abs(deg) / (self.speed * 2))
        return True

    def run_group(self, name, slot=0):
        self._rec("run_group", name=name, slot=slot)
        self._sim_sleep(0.05)
        return True

    def grip(self, close_=True, angle=None):
        self._rec("grip", close=bool(close_), angle=angle)
        self._sim_sleep(0.02)
        return True
        return True

    def ttl(self, axis, d, vel, pulses):
        self._rec("ttl", axis=axis, dir=d, vel=vel, pulses=pulses)
        self._sim_sleep(0.02)
        return True

    def stop(self):
        self._rec("stop")
        return True

    def show_qr(self, code):
        self._rec("show_qr", code=code)
        return True
