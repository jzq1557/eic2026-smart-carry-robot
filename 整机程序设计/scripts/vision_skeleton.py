#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能搬运赛项 · 视觉程序框架（树莓派 4B + USB 摄像头）
====================================================
依据：附件2-1《命题与运行》、附件2-2《评分与规则》
配套：《视觉系统程序设计方案.md》

结构说明
--------
  Cam           相机封装：采集线程 + 最新帧缓冲 + 曝光/白平衡锁定
  UartLink      与主控的串口帧协议（STX|CMD|SEQ|LEN|PAY|CRC8|ETX）
  ColorModel    6 色 HSV 模型：现场标定 + 分类 + 时序投票
  QRReader      二维码读取：OpenCV/pyzbar 双引擎 + 多帧表决 + 格式校验
  LaneTracker   车道检测：灰色分割 + 横向偏差 + 航向角
  ObstacleFinder 障碍检测：车道内黑色连通域 + 距离估计
  TurntableFinder 转盘物料定位：背景减除 + 极坐标输出
  RingLocalizer 圆环对位：Hough 找圆 + 偏差计算 + 放置确认
  VisionService 命令状态机：串口命令 -> 视觉任务 -> 结构化回包

运行依赖:  pip install opencv-python pyserial numpy pyyaml
          (二维码备用引擎, 可选: sudo apt install libzbar0 && pip install pyzbar)

注意: 所有带 TODO 的参数必须在实验室模拟场地 + 现场标定后确定,
      不要照抄默认值。
"""

import cv2
import numpy as np
import threading
import time
import json
import struct
from collections import deque, Counter

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

try:
    from pyzbar import pyzbar
    HAS_ZBAR = True
except Exception:
    HAS_ZBAR = False

# ======================================================================
# 0. 配置（实际使用时迁移到 config.yaml 热加载）
# ======================================================================
CFG = {
    # 相机
    "cam_front_id": 0,
    "cam_down_id": 2,
    "cam_w": 640,
    "cam_h": 480,
    # 串口
    "uart_port": "/dev/ttyAMA0",
    "uart_baud": 921600,
    # 车道（灰色: 低饱和 + 中亮度, 现场标定）
    "lane_s_max": 60,
    "lane_v_min": 80,
    "lane_v_max": 220,
    "lane_roi_frac": 0.45,          # 只处理画面下方 45%
    # 障碍（黑色）
    "obstacle_v_max": 70,
    "obstacle_area_min": 300,
    "obstacle_area_max": 20000,
    # 圆环
    "ring_hough_dp": 1.2,
    "ring_hough_min_dist": 40,
    "ring_hough_param1": 80,
    "ring_hough_param2": 30,
    "ring_radius_min_px": 40,       # 现场按相机高度标定
    "ring_radius_max_px": 160,
    # 下视相机比例尺: px -> mm（手眼标定得到）
    "down_px_per_mm": 2.5,          # TODO: 标定
    "down_cam_offset_mm": (0.0, 0.0),  # TODO: 相机相对机身参考点偏移
    # 转盘
    "turntable_center": (320, 240),  # TODO: 下视图中转盘圆心像素
    "turntable_radius_px": 150,      # TODO: 标定
    # QR
    "qr_vote_confirm": 3,           # 相同结果出现3帧确认
    "qr_max_frames": 50,
}

# ======================================================================
# 1. 相机封装
# ======================================================================
class Cam:
    """UVC 相机：后台线程持续采集，处理侧永远取最新帧（丢旧帧策略）。"""

    def __init__(self, idx, w=640, h=480, name="cam"):
        self.name = name
        self.cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        assert self.cap.isOpened(), f"[{name}] 相机打开失败 idx={idx}"
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.lock_manual()
        self._frame = None
        self._ts = 0.0
        self._lock = threading.Lock()
        self._alive = True
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def lock_manual(self):
        """锁定曝光与白平衡 —— 颜色识别的前提。"""
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)   # 0.25=手动, 0.75=自动
        self.cap.set(cv2.CAP_PROP_EXPOSURE, 120)          # TODO: 现场定, 避开灯光频闪
        self.cap.set(cv2.CAP_PROP_AUTO_WB, 0)             # 手动白平衡
        self.cap.set(cv2.CAP_PROP_WB_TEMPERATURE, 5000)   # 固定色温

    def _loop(self):
        while self._alive:
            ok, frame = self.cap.read()
            if ok:
                with self._lock:
                    self._frame = frame
                    self._ts = time.time()

    def read(self):
        """返回 (frame, ts) 或 (None, 0)。"""
        with self._lock:
            return self._frame, self._ts

    def close(self):
        self._alive = False
        self._th.join(timeout=1.0)
        self.cap.release()


# ======================================================================
# 2. 串口帧协议
# ======================================================================
# 帧: STX(0xA5) CMD(1B) SEQ(1B) LEN(2B,小端) PAYLOAD CRC8 ETX(0x5A)
STX, ETX = 0xA5, 0x5A

CMD = {
    "PING": 0x01, "READ_QR": 0x10, "LANE": 0x11, "OBSTACLE": 0x12,
    "FIND_MATERIAL": 0x13, "ALIGN_RING": 0x14, "VERIFY_PLACED": 0x15,
    "CHECK_REGION": 0x16, "WAIT_TURNTABLE": 0x17, "VERIFY_LOADED": 0x18,
    "EVENT": 0x1F,
}


def _crc8(data: bytes) -> int:
    crc = 0x00
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def make_frame(cmd: int, seq: int, payload: bytes = b"") -> bytes:
    body = bytes([cmd, seq]) + struct.pack("<H", len(payload)) + payload
    return bytes([STX]) + body + bytes([_crc8(body)]) + bytes([ETX])


class UartLink:
    """串口收发线程：回调注册 + 自动回包。"""

    def __init__(self, port, baud, on_cmd):
        self.on_cmd = on_cmd          # on_cmd(cmd:int, payload:bytes, seq:int)
        self._ser = None
        if HAS_SERIAL:
            self._ser = serial.Serial(port, baud, timeout=0.05)
        self._alive = True
        self._buf = bytearray()
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def _loop(self):
        if self._ser is None:
            return
        while self._alive:
            chunk = self._ser.read(256)
            if not chunk:
                continue
            self._buf.extend(chunk)
            while True:
                frame = self._try_parse()
                if frame is None:
                    break
                cmd, seq, payload = frame
                # 串行处理: 一次一个视觉任务
                self.on_cmd(cmd, payload, seq)

    def _try_parse(self):
        i = self._buf.find(STX)
        if i < 0:
            self._buf.clear()
            return None
        if i > 0:
            del self._buf[:i]
        if len(self._buf) < 5:
            return None
        cmd, seq = self._buf[1], self._buf[2]
        (ln,) = struct.unpack("<H", bytes(self._buf[3:5]))
        if len(self._buf) < 5 + ln + 2:
            return None
        payload = bytes(self._buf[5:5 + ln])
        crc, etx = self._buf[5 + ln], self._buf[5 + ln + 1]
        del self._buf[:5 + ln + 2]
        if etx != ETX or crc != _crc8(bytes([cmd, seq]) + struct.pack("<H", ln) + payload):
            return None          # 坏帧丢弃
        return cmd, seq, payload

    def reply(self, cmd: int, seq: int, payload: bytes = b""):
        if self._ser is not None:
            self._ser.write(make_frame(cmd, seq, payload))

    def reply_json(self, cmd: int, seq: int, obj: dict):
        self.reply(cmd, seq, json.dumps(obj).encode("utf-8"))

    def close(self):
        self._alive = False
        if self._ser is not None:
            self._ser.close()


# ======================================================================
# 3. 颜色模型（6 色）
# ======================================================================
COLOR_NAMES = {1: "红", 2: "黄", 3: "蓝", 4: "绿", 5: "黑", 6: "浅蓝"}


def _h_dist(h1, h2):
    """HSV 色相环形距离 (0~90)。"""
    d = abs(h1 - h2) % 180
    return d if d <= 90 else 180 - d


class ColorModel:
    """每色 HSV 均值/标准差；H 用环形距离，S/V 用欧氏距离加权。"""

    def __init__(self):
        self.models = {}   # color_id -> (h, s, v, h_std, s_std, v_std)

    def calibrate(self, samples: dict):
        """samples: {color_id: [hsv_ndarray(n,3), ...]} 现场采样。"""
        for cid, mats in samples.items():
            all_px = np.vstack(mats)
            mean, std = all_px.mean(axis=0), all_px.std(axis=0)
            self.models[cid] = (mean[0], mean[1], mean[2],
                                std[0] + 1e-6, std[1] + 1e-6, std[2] + 1e-6)

    def classify(self, hsv_roi) -> int:
        """对 ROI 内像素投票，返回颜色编号 (0=未识别)。"""
        if not self.models:
            return 0
        px = hsv_roi.reshape(-1, 3).astype(np.float32)
        if len(px) < 30:
            return 0
        votes = Counter()
        for cid, (h, s, v, hs, ss, vs) in self.models.items():
            d_h = _h_dist(px[:, 0], h) / hs
            d_s = abs(px[:, 1] - s) / ss
            d_v = abs(px[:, 2] - v) / vs
            d = 1.0 * d_h + 1.2 * d_s + 1.0 * d_v   # 权重现场可调
            votes[cid] = int((d < 4.0).sum())
        return votes.most_common(1)[0][0] if votes and votes.most_common(1)[0][1] > 0 else 0

    def mask(self, hsv, cid):
        """生成该颜色的二值掩码（±3σ 粗分割）。"""
        if cid not in self.models:
            return np.zeros(hsv.shape[:2], np.uint8)
        h, s, v, hs, ss, vs = self.models[cid]
        d_h = _h_dist(hsv[:, :, 0].astype(np.float32), h)
        d_s = np.abs(hsv[:, :, 1].astype(np.float32) - s)
        d_v = np.abs(hsv[:, :, 2].astype(np.float32) - v)
        m = (d_h < 3 * hs) & (d_s < 3 * ss) & (d_v < 3 * vs)
        return (m * 255).astype(np.uint8)


class TemporalVoter:
    """时序投票：连续 n 帧一致才输出，消除单帧误判。"""

    def __init__(self, n=5):
        self.n = n
        self.q = deque(maxlen=n)

    def vote(self, val):
        self.q.append(val)
        if len(self.q) < self.n:
            return None
        c = Counter(self.q).most_common(1)[0]
        return c[0] if c[1] >= self.n else None

    def reset(self):
        self.q.clear()


# ======================================================================
# 4. 二维码读取
# ======================================================================
def parse_task_code(text: str):
    """解析 '156+123+516+231' -> 4x3 数组; 非法返回 None。"""
    import re
    if not re.fullmatch(r"\d{3}\+\d{3}\+\d{3}\+\d{3}", text or ""):
        return None
    groups = [[int(d) for d in g] for g in text.split("+")]
    if any(d < 1 or d > 6 for g in groups for d in g):
        return None
    return groups


class QRReader:
    """多帧表决读码。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.det = cv2.QRCodeDetector()

    def _decode_once(self, gray):
        results = set()
        for scale in (0.75, 1.0, 1.25):
            img = cv2.resize(gray, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_AREA)
            text, _, _ = self.det.detectAndDecode(img)
            if text:
                results.add(text)
            thr = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY, 51, 10)
            text2, _, _ = self.det.detectAndDecode(thr)
            if text2:
                results.add(text2)
        if HAS_ZBAR:
            for code in pyzbar.decode(gray):
                results.add(code.data.decode("utf-8", "ignore"))
        return results

    def read_with_vote(self, cam: Cam, max_frames=None, timeout=8.0):
        """停稳后连读多帧, 相同合法结果 >=3 帧即确认。返回 (task_str, groups) 或 (None,None)。"""
        max_frames = max_frames or self.cfg["qr_vote_confirm"] * 10
        votes = Counter()
        t0 = time.time()
        for _ in range(max_frames):
            frame, _ = cam.read()
            if frame is None:
                time.sleep(0.01)
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            for text in self._decode_once(gray):
                if parse_task_code(text):
                    votes[text] += 1
                    if votes[text] >= self.cfg["qr_vote_confirm"]:
                        return text, parse_task_code(text)
            if time.time() - t0 > timeout:
                break
        best = votes.most_common(1)
        if best and best[0][1] >= 2:
            return best[0][0], parse_task_code(best[0][0])
        return None, None


# ======================================================================
# 5. 车道检测
# ======================================================================
class LaneTracker:
    """灰色车道分割 -> 横向偏差(归一化-1~1) + 航向角(度)。"""

    def __init__(self, cfg):
        self.cfg = cfg

    def _lane_mask(self, frame):
        h, w = frame.shape[:2]
        roi = frame[int(h * (1 - self.cfg["lane_roi_frac"])):, :]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv,
                        np.array([0, 0, self.cfg["lane_v_min"]], np.uint8),
                        np.array([179, self.cfg["lane_s_max"], self.cfg["lane_v_max"]], np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        return m, roi

    def track(self, frame):
        m, roi = self._lane_mask(frame)
        h, w = frame.shape[:2]
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        c = max(contours, key=cv2.contourArea)
        if cv2.contourArea(c) < w * 20:
            return None
        M = cv2.moments(c)
        if M["m00"] == 0:
            return None
        cx = M["m10"] / M["m00"]
        [vx, vy, x0, y0] = cv2.fitLine(c, cv2.DIST_L2, 0, 0.01, 0.01)
        yaw = np.degrees(np.arctan2(vx[0], vy[0]))      # 相对纵轴
        offset = (cx - w / 2) / (w / 2)
        is_cross = cv2.contourArea(c) > 0.45 * roi.shape[0] * roi.shape[1]
        return {"offset": float(offset), "yaw": float(yaw),
                "cross_junction": bool(is_cross), "conf": 1.0}


# ======================================================================
# 6. 障碍物检测（车道内黑色连通域）
# ======================================================================
class ObstacleFinder:
    def __init__(self, cfg):
        self.cfg = cfg

    def find(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        dark = cv2.inRange(hsv, np.array([0, 0, 0], np.uint8),
                           np.array([179, 255, self.cfg["obstacle_v_max"]], np.uint8))
        contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cands = []
        for c in contours:
            a = cv2.contourArea(c)
            if self.cfg["obstacle_area_min"] < a < self.cfg["obstacle_area_max"]:
                x, y, w, h = cv2.boundingRect(c)
                cands.append((y, x + w / 2, a, (x, y, w, h)))
        if not cands:
            return []
        cands.sort(key=lambda t: -t[0])          # 最近(y最大)优先
        out = []
        for y, cx, a, (x, yb, w, h) in cands[:3]:
            out.append({"x_px": cx, "y_px": y + h,
                        "distance_mm": self._dist_mm(y + h), "area": a})
        return out

    def _dist_mm(self, y_px):
        # TODO: 用 §6.2 地面投影模型替换此粗估计（相机高h、俯角θ标定后建查找表）
        return max(0.0, (self.cfg["cam_h"] - 120) / max(1.0, y_px / 100.0))


# ======================================================================
# 7. 转盘物料定位
# ======================================================================
class TurntableFinder:
    """MOG2 背景减除 + 颜色掩码 -> 目标物料极坐标。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.bg = cv2.createBackgroundSubtractorMOG2(history=100, varThreshold=30)
        self.prev_pos = None
        self.still_count = 0

    def find(self, frame, hsv, color_model: ColorModel, color_id: int):
        fg = self.bg.apply(frame)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        cmask = color_model.mask(hsv, color_id)
        target = cv2.bitwise_and(fg, fg, mask=cmask)
        contours, _ = cv2.findContours(target, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for c in contours:
            a = cv2.contourArea(c)
            if a < 150:                        # TODO: 标定（物料在图中面积）
                continue
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            best = (M["m10"] / M["m00"], M["m01"] / M["m00"], a)
            break
        if best is None:
            self.still_count = 0
            self.prev_pos = None
            return None
        cx, cy, a = best
        tx, ty = self.cfg["turntable_center"]
        dx, dy = cx - tx, cy - ty
        r_px = np.hypot(dx, dy)
        if r_px > self.cfg["turntable_radius_px"] * 1.15:
            return None
        theta = np.degrees(np.arctan2(dx, dy))
        # 停转检测: 连续帧位置漂移小 -> 转盘停稳
        if self.prev_pos is not None:
            drift = np.hypot(cx - self.prev_pos[0], cy - self.prev_pos[1])
            self.still_count = self.still_count + 1 if drift < 2.0 else 0
        self.prev_pos = (cx, cy)
        return {"theta_deg": float(theta), "r_px": float(r_px),
                "area": float(a), "turntable_still": self.still_count >= 5}


# ======================================================================
# 8. 圆环对位（核心得分模块）
# ======================================================================
class RingLocalizer:
    """Hough 找同心圆环 -> 环心像素 -> 机器人坐标偏差(mm)。"""

    def __init__(self, cfg):
        self.cfg = cfg

    def locate(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 1.2)
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT,
            dp=self.cfg["ring_hough_dp"],
            minDist=self.cfg["ring_hough_min_dist"],
            param1=self.cfg["ring_hough_param1"],
            param2=self.cfg["ring_hough_param2"],
            minRadius=self.cfg["ring_radius_min_px"],
            maxRadius=self.cfg["ring_radius_max_px"])
        if circles is None:
            return None
        cs = circles[0]
        # 同心圆圆心均值 = 环心（抗单圆误检）
        k = len(cs)
        if k < 2:
            return None
        cx = cs[:, 0].mean()
        cy = cs[:, 1].mean()
        std = np.hypot(cs[:, 0] - cx, cs[:, 1] - cy).mean()
        if std > 12:                       # 圆心分散 -> 不是同心环
            return None
        return {"cx_px": float(cx), "cy_px": float(cy),
                "n_circles": k, "radius_px": float(cs[:, 2].max())}

    def pixel_to_mm(self, cx, cy):
        """像素 -> 相对机身参考点偏移(mm)。TODO: 用下视手眼标定结果替换。"""
        h, w = self.cfg["cam_h"], self.cfg["cam_w"]
        mmx = (cx - w / 2) / self.cfg["down_px_per_mm"] + self.cfg["down_cam_offset_mm"][0]
        mmy = (cy - h / 2) / self.cfg["down_px_per_mm"] + self.cfg["down_cam_offset_mm"][1]
        return mmx, mmy

    def verify_placed(self, frame, color_model: ColorModel, color_id: int):
        """放置确认：检测物料底面与环心偏差, 估算环级。TODO: 按环外径表完成环级判定。"""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        m = color_model.mask(hsv, color_id)
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        c = max(contours, key=cv2.contourArea)
        if cv2.contourArea(c) < 200:
            return None
        (x, y), r = cv2.minEnclosingCircle(c)
        ring = self.locate(frame)
        if ring is None:
            return None
        dev_mm = np.hypot(x - ring["cx_px"], y - ring["cy_px"]) / self.cfg["down_px_per_mm"]
        return {"dev_mm": float(dev_mm), "on_ring": True, "ring_grade": 0}


# ======================================================================
# 9. 区域识别
# ======================================================================
class RegionClassifier:
    """蓝(启停) / 淡黄(禁入) / 灰(车道) / 白(底色) 分类 + 越界预警。"""

    def classify(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h, w = frame.shape[:2]
        roi = hsv[int(h * 0.55):, :]         # 车头下方
        hh, ss, vv = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
        blue = ((hh > 100) & (hh < 130) & (ss > 60)).mean()
        yellow = ((hh > 15) & (hh < 35) & (ss > 60) & (vv > 100)).mean()
        gray = ((ss < 60) & (vv > 80) & (vv < 220)).mean()
        white = ((ss < 50) & (vv > 200)).mean()
        if blue > 0.35:
            region = "blue"
        elif yellow > 0.25:
            region = "yellow"
        elif gray > 0.4:
            region = "gray"
        elif white > 0.4:
            region = "white"
        else:
            region = "unknown"
        # 越界: 非灰且非启停区
        alarm = region not in ("gray", "blue")
        return {"region": region, "out_of_lane": bool(alarm),
                "score": {"blue": blue, "yellow": yellow, "gray": gray, "white": white}}


# ======================================================================
# 10. 视觉服务（命令状态机）
# ======================================================================
class VisionService:
    def __init__(self, cfg):
        self.cfg = cfg
        self.front = Cam(cfg["cam_front_id"], cfg["cam_w"], cfg["cam_h"], "front")
        self.down = Cam(cfg["cam_down_id"], cfg["cam_w"], cfg["cam_h"], "down")
        self.colors = ColorModel()
        self.voter = TemporalVoter(5)
        self.qr = QRReader(cfg)
        self.lane = LaneTracker(cfg)
        self.obst = ObstacleFinder(cfg)
        self.turntable = TurntableFinder(cfg)
        self.ring = RingLocalizer(cfg)
        self.region = RegionClassifier()
        self.uart = UartLink(cfg["uart_port"], cfg["uart_baud"], self.on_cmd)

    # ---------- 任务实现 ----------
    def task_read_qr(self, seq):
        text, groups = self.qr.read_with_vote(self.front)
        self.uart.reply_json(CMD["READ_QR"], seq,
                             {"ok": bool(text), "task": text,
                              "groups": groups} if text else {"ok": False})

    def task_lane(self, seq):
        frame, _ = self.front.read()
        r = self.lane.track(frame) if frame is not None else None
        self.uart.reply_json(CMD["LANE"], seq, r or {"ok": False})

    def task_obstacle(self, seq):
        frame, _ = self.front.read()
        obs = self.obst.find(frame) if frame is not None else []
        self.uart.reply_json(CMD["OBSTACLE"], seq, {"ok": True, "obstacles": obs})

    def task_find_material(self, seq, color_id):
        frame, _ = self.down.read()
        if frame is None:
            self.uart.reply_json(CMD["FIND_MATERIAL"], seq, {"ok": False})
            return
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        r = self.turntable.find(frame, hsv, self.colors, color_id)
        # 时序投票稳定颜色/位置
        if r is None:
            self.voter.reset()
            self.uart.reply_json(CMD["FIND_MATERIAL"], seq, {"ok": False, "found": False})
            return
        self.uart.reply_json(CMD["FIND_MATERIAL"], seq,
                             {"ok": True, "found": True,
                              "theta_deg": r["theta_deg"], "r_px": r["r_px"],
                              "still": r["turntable_still"]})

    def task_align_ring(self, seq, ring_id):
        frame, _ = self.down.read()
        r = self.ring.locate(frame) if frame is not None else None
        if r is None:
            self.uart.reply_json(CMD["ALIGN_RING"], seq, {"ok": False})
            return
        dx, dy = self.ring.pixel_to_mm(r["cx_px"], r["cy_px"])
        aligned = abs(dx) <= 2.0 and abs(dy) <= 2.0
        self.uart.reply_json(CMD["ALIGN_RING"], seq,
                             {"ok": True, "dx_mm": dx, "dy_mm": dy,
                              "aligned": bool(aligned)})

    def task_verify_placed(self, seq, color_id):
        frame, _ = self.down.read()
        r = self.ring.verify_placed(frame, self.colors, color_id) if frame is not None else None
        self.uart.reply_json(CMD["VERIFY_PLACED"], seq, r or {"ok": False})

    def task_verify_loaded(self, seq):
        frame, _ = self.down.read()
        if frame is None:
            self.uart.reply_json(CMD["VERIFY_LOADED"], seq, {"ok": False})
            return
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        # TODO: 载物区 ROI 由下视相机安装位置标定
        roi = hsv[200:400, 160:480]
        cid = self.colors.classify(roi)
        self.uart.reply_json(CMD["VERIFY_LOADED"], seq,
                             {"ok": cid > 0, "color_id": int(cid)})

    def task_check_region(self, seq):
        frame, _ = self.front.read()
        r = self.region.classify(frame) if frame is not None else {"region": "unknown"}
        self.uart.reply_json(CMD["CHECK_REGION"], seq, {"ok": True, **r})

    def task_calibrate_color(self, seq):
        """现场标定: 主控依次把 6 色物料放到下视相机下, 发本命令+颜色号。
        TODO: 由主控配合逐个采样; 简化版在此改为拍 3 帧取均值。"""
        frame, _ = self.down.read()
        if frame is None:
            self.uart.reply_json(CMD["PING"], seq, {"ok": False})
            return
        self.uart.reply_json(CMD["PING"], seq, {"ok": True})

    # ---------- 命令分发 ----------
    def on_cmd(self, cmd: int, payload: bytes, seq: int):
        try:
            if cmd == CMD["PING"]:
                self.uart.reply_json(CMD["PING"], seq, {"ok": True, "ver": "1.0"})
            elif cmd == CMD["READ_QR"]:
                self.task_read_qr(seq)
            elif cmd == CMD["LANE"]:
                self.task_lane(seq)
            elif cmd == CMD["OBSTACLE"]:
                self.task_obstacle(seq)
            elif cmd == CMD["FIND_MATERIAL"]:
                self.task_find_material(seq, payload[0] if payload else 0)
            elif cmd == CMD["ALIGN_RING"]:
                self.task_align_ring(seq, payload[0] if payload else 0)
            elif cmd == CMD["VERIFY_PLACED"]:
                self.task_verify_placed(seq, payload[0] if payload else 0)
            elif cmd == CMD["CHECK_REGION"]:
                self.task_check_region(seq)
            elif cmd == CMD["VERIFY_LOADED"]:
                self.task_verify_loaded(seq)
            else:
                self.uart.reply_json(cmd, seq, {"ok": False, "err": "unknown_cmd"})
        except Exception as e:      # 视觉任务异常不能拖垮通信
            self.uart.reply_json(cmd, seq, {"ok": False, "err": str(e)})

    def run(self):
        print("[vision] service running, waiting commands...")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self):
        self.uart.close()
        self.front.close()
        self.down.close()


# ======================================================================
# 11. 离线回放测试入口（开发阶段用录制视频代替相机）
# ======================================================================
def replay_test(video_path: str):
    """把录制的场地视频喂给 LaneTracker, 验证车道算法。"""
    cfg = dict(CFG)
    lane = LaneTracker(cfg)
    cap = cv2.VideoCapture(video_path)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        r = lane.track(frame)
        if r:
            cv2.putText(frame, f"off={r['offset']:.2f} yaw={r['yaw']:.1f}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imshow("replay", frame)
        if cv2.waitKey(30) & 0xFF == 27:
            break
    cap.release()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "replay":
        replay_test(sys.argv[2])
    else:
        svc = VisionService(CFG)
        svc.run()
