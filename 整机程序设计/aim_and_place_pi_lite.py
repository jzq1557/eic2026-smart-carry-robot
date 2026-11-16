#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aim_and_place.py —— 视觉瞄准标靶 + 分轴PID微调 + 记录偏移(树莓派端 v4)

★树莓派轻量版：原 RingDetector 保留，同时增加强反光下的残缺圆弧几何检测:
  - RingDetector.process(frame) 内部: proc_scale 半分辨率检测 + EMA 平滑 + 坐标回算
  - rotate_ccw / width / height / 锁曝光 均按原版 CALIB 执行, 不另切画幅

实际使用流程(物料在车上置物台,夹爪不直接抓料瞄准):
  1. 机械在"观察位"摆好(夹爪张开0°不遮挡,摄像头能看到标靶圆环)
  2. 分轴 PID 瞄准(避免两轴耦合):
      阶段A: 只用 base轴(4) PID 收敛 x 偏差(dx)
      阶段B: 只用伸缩台(1) PID 收敛 y 偏差(dy)
  3. 两轴都进死区 → 瞄准完成,记录 offset(观察位→靶心)到 aim_offset.txt
  4. [--mission 自动接续] 归位回观察位 → 0x16 去车载物架夹料(下位机动作组)
     → 回放 offset 到靶心 → 0x13 放料
  5. 分步调试: --home 归位 / --grab 夹料 / --replay 回放放料

用法:
  python3 aim_and_place.py --port /dev/ttyUSB0 --stream 8090          # 只瞄准
  python3 aim_and_place.py --port /dev/ttyUSB0 --mission --stream 8090 # 全流程
"""
import sys, time, argparse, threading, os
import serial
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ring_center_detect_v2 import RingDetector, CALIB, rotate_ccw   # ★原版检测


# ---------------- 树莓派轻量版：反光mask + Canny + 局部圆弧拟合 ----------------
# 设计目标：
#   1) 不在每帧跑 Hough；
#   2) 有 RingDetector / 上一帧圆心时，只在小 ROI 内做 Canny + 圆拟合；
#   3) 只有“首次找靶/连续跟丢”时，才在 1/3 分辨率上跑一次 Hough；
#   4) 取消双边滤波、LAB、CLAHE、高光软修补，减少树莓派 CPU 开销；
#   5) 反光 mask 只负责把纯白高光边缘从 Canny 中剔除，不尝试恢复已过曝纹理。
#
# 1280x720 摄像头仍保持不变，因此你原来的像素->脉冲标定不用因为本算法缩放而重做；
# 检测内部缩小，最后会自动把圆心/半径换算回原图坐标。

cv2.setUseOptimized(True)
try:
    cv2.setNumThreads(2)       # 树莓派上避免 OpenCV 线程开太多
except Exception:
    pass

# ---- 轻量高光 mask ----
GLARE_FILTER_DEFAULT = True
GLARE_BRIGHT_THR = 248         # 最大通道达到该值
GLARE_NEUTRAL_SPREAD = 38      # max(B,G,R)-min(B,G,R) 较小 -> 接近白色高光

# ---- 轻量残缺圆弧检测 ----
ARC_DETECT_DEFAULT = True
ARC_SCALE = 0.33               # 1280x720 -> 约 422x238
ARC_CANNY_LOW = 45
ARC_CANNY_HIGH = 125
ARC_HOUGH_DP = 1.25
ARC_HOUGH_PARAM2 = 34          # Hough 只在丢失时执行，可以稍微放宽
ARC_MIN_R_RATIO = 0.10
ARC_MAX_R_RATIO = 0.48
ARC_MIN_SCORE = 0.20
ARC_EMA_ALPHA = 0.50

ARC_TRACK_BAND_RATIO = 0.10    # 有先验时，只找 prior 半径 ±10% 附近边缘
ARC_TRACK_BAND_MIN = 3.0       # 小图上的最小环带宽度
ARC_TRACK_MAX_CENTER_SHIFT = 0.28
ARC_TRACK_MAX_RADIUS_CHANGE = 0.25
ARC_MAX_FIT_POINTS = 700       # 拟合最多取 700 个点，避免大数组最小二乘
ARC_HOUGH_MAX_CANDIDATES = 8   # Hough 最多评估前 8 个候选
ARC_REACQUIRE_AFTER = 2        # 连续局部拟合失败 2 次，才重新 Hough


class GlarePreprocessor:
    """树莓派轻量高光检测：不再做双边/CLAHE，只生成高光 mask。"""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.kernel3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    def process(self, frame):
        if not self.enabled:
            return frame, None

        # 不转 HSV/LAB，直接用 BGR 最大/最小通道近似判断“接近白色的过曝区”。
        b, g, r = cv2.split(frame)
        mx = cv2.max(cv2.max(b, g), r)
        mn = cv2.min(cv2.min(b, g), r)
        spread = cv2.subtract(mx, mn)

        bright = cv2.inRange(mx, GLARE_BRIGHT_THR, 255)
        neutral = cv2.inRange(spread, 0, GLARE_NEUTRAL_SPREAD)
        glare = cv2.bitwise_and(bright, neutral)

        # 只扩一圈，覆盖高光边界；不做昂贵修补。
        glare = cv2.dilate(glare, self.kernel3, iterations=1)
        return frame, glare


class ArcCircleDetector:
    """
    轻量策略：
      正常跟踪：prior -> 小 ROI Canny -> 剩余圆弧最小二乘拟合
      跟丢/首次：全局 1/3 分辨率 Hough -> 圆弧拟合
    """

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.last = None
        self.last_result = None
        self.lost_count = 0
        self.kernel3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    @staticmethod
    def _fit_circle_ls(points):
        if points is None or len(points) < 28:
            return None

        # 点太多会增加最小二乘开销，均匀抽样即可。
        if len(points) > ARC_MAX_FIT_POINTS:
            step = max(1, len(points) // ARC_MAX_FIT_POINTS)
            points = points[::step][:ARC_MAX_FIT_POINTS]

        x = points[:, 0].astype(np.float32)
        y = points[:, 1].astype(np.float32)
        A = np.column_stack((2.0 * x, 2.0 * y, np.ones_like(x))).astype(np.float32)
        b = (x * x + y * y).astype(np.float32)
        try:
            sol, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
        except np.linalg.LinAlgError:
            return None
        cx, cy, c = map(float, sol)
        r2 = cx * cx + cy * cy + c
        if r2 <= 0:
            return None
        return cx, cy, float(np.sqrt(r2))

    @staticmethod
    def _support(edges_dilated, glare, cx, cy, r):
        """180 个采样点即可；只重视上半圈和左右，忽略底部中央强反光区。"""
        angles = np.linspace(0.0, 2.0 * np.pi, 180, endpoint=False, dtype=np.float32)
        xs = np.rint(cx + r * np.cos(angles)).astype(np.int32)
        ys = np.rint(cy + r * np.sin(angles)).astype(np.int32)

        h, w = edges_dilated.shape[:2]
        inside = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        if np.count_nonzero(inside) < 50:
            return 0.0, 0.0, 0.0, 0.0

        xs = xs[inside]
        ys = ys[inside]
        aa = angles[inside]

        valid = glare[ys, xs] == 0
        hit = edges_dilated[ys, xs] > 0

        # 图像坐标：下方约为 45°~135°，这块最容易反光，评分时忽略。
        useful = valid & ~((aa > np.deg2rad(45)) & (aa < np.deg2rad(135)))
        top = valid & (np.sin(aa) < -0.15)
        left = valid & (np.cos(aa) < -0.55)
        right = valid & (np.cos(aa) > 0.55)

        def ratio(mask):
            n = int(np.count_nonzero(mask))
            return float(np.count_nonzero(hit & mask) / n) if n >= 6 else 0.0

        useful_s = ratio(useful)
        top_s = ratio(top)
        left_s = ratio(left)
        right_s = ratio(right)

        # 简化评分，取消 12 扇区循环。
        sector_s = 0.45 * top_s + 0.275 * left_s + 0.275 * right_s
        score = 0.70 * useful_s + 0.30 * sector_s
        return float(score), float(top_s), float(left_s), float(right_s)

    def _prepare_small(self, frame, glare_mask):
        h0, w0 = frame.shape[:2]
        scale = ARC_SCALE
        ws = max(1, int(w0 * scale))
        hs = max(1, int(h0 * scale))

        # 灰度后再缩小，比先缩 BGR 再灰度少一些数据处理。
        gray0 = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray0, (ws, hs), interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)

        if glare_mask is None:
            glare = np.zeros((hs, ws), dtype=np.uint8)
        else:
            glare = cv2.resize(glare_mask, (ws, hs), interpolation=cv2.INTER_NEAREST)
            glare = cv2.dilate(glare, self.kernel3, iterations=1)

        edges = cv2.Canny(gray, ARC_CANNY_LOW, ARC_CANNY_HIGH, L2gradient=False)
        edges[glare > 0] = 0
        edge_band = cv2.dilate(edges, self.kernel3, iterations=1)
        return gray, glare, edges, edge_band

    def _fit_near_prior(self, edges, edge_band, glare, prior_small):
        """最常走的快速路径：只看 prior 周围 ROI，不跑 Hough。"""
        pcx, pcy, pr = prior_small
        if pr < 8:
            return None

        h, w = edges.shape[:2]
        pad = max(8, int(pr * 1.25))
        x0 = max(0, int(pcx - pad))
        x1 = min(w, int(pcx + pad + 1))
        y0 = max(0, int(pcy - pad))
        y1 = min(h, int(pcy + pad + 1))
        if x1 - x0 < 20 or y1 - y0 < 20:
            return None

        roi = edges[y0:y1, x0:x1]
        ys, xs = np.nonzero(roi)
        if len(xs) < 35:
            return None

        xs = xs.astype(np.float32) + x0
        ys = ys.astype(np.float32) + y0

        d = np.hypot(xs - pcx, ys - pcy)
        band = max(ARC_TRACK_BAND_MIN, pr * ARC_TRACK_BAND_RATIO)
        keep = np.abs(d - pr) <= band
        xs, ys = xs[keep], ys[keep]
        if len(xs) < 32:
            return None

        # 去掉底部中央（最容易被反光毁掉）。
        ang = np.arctan2(ys - pcy, xs - pcx)
        keep = ~((ang > np.deg2rad(45)) & (ang < np.deg2rad(135)))
        pts = np.column_stack((xs[keep], ys[keep]))
        if len(pts) < 28:
            return None

        fit = self._fit_circle_ls(pts)
        if fit is None:
            return None

        # 只做 1 次残差清理，减少 CPU。
        cx, cy, rr = fit
        residual = np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - rr)
        med = float(np.median(residual))
        thr = max(1.6, min(3.8, med * 2.6))
        pts2 = pts[residual <= thr]
        if len(pts2) >= 28:
            fit2 = self._fit_circle_ls(pts2)
            if fit2 is not None:
                cx, cy, rr = fit2

        # 防止局部拟合突然跳到内圈/数字。
        center_shift = np.hypot(cx - pcx, cy - pcy) / max(pr, 1.0)
        radius_change = abs(rr - pr) / max(pr, 1.0)
        if center_shift > ARC_TRACK_MAX_CENTER_SHIFT:
            return None
        if radius_change > ARC_TRACK_MAX_RADIUS_CHANGE:
            return None

        score, top_s, left_s, right_s = self._support(edge_band, glare, cx, cy, rr)
        strong = sum(v >= 0.12 for v in (top_s, left_s, right_s))
        if score < ARC_MIN_SCORE or strong < 2:
            return None

        return {
            "found": True,
            "cx_s": cx,
            "cy_s": cy,
            "r_s": rr,
            "score": score,
            "top_support": top_s,
            "left_support": left_s,
            "right_support": right_s,
            "method": "roi_arcfit",
        }

    def _global_reacquire(self, gray, edges, edge_band, glare, prior_small=None):
        """较重路径：仅首次/连续跟丢时执行 Hough。"""
        h, w = gray.shape[:2]
        min_dim = min(h, w)

        min_r = max(8, int((min_dim / ARC_SCALE) * ARC_MIN_R_RATIO * ARC_SCALE))
        max_r = max(min_r + 4, int((min_dim / ARC_SCALE) * ARC_MAX_R_RATIO * ARC_SCALE))
        min_dist = max(28, int(min_dim * 0.18))

        # 把高光区域填成中值，避免高光边界自己产生 Hough 假圆。
        hough_gray = gray.copy()
        valid = hough_gray[glare == 0]
        fill = int(np.median(valid)) if valid.size else 127
        hough_gray[glare > 0] = fill

        circles = cv2.HoughCircles(
            hough_gray,
            cv2.HOUGH_GRADIENT,
            dp=ARC_HOUGH_DP,
            minDist=min_dist,
            param1=ARC_CANNY_HIGH,
            param2=ARC_HOUGH_PARAM2,
            minRadius=min_r,
            maxRadius=max_r,
        )
        if circles is None:
            return None

        best = None
        for raw in circles[0][:ARC_HOUGH_MAX_CANDIDATES]:
            cx, cy, rr = map(float, raw)
            score, top_s, left_s, right_s = self._support(
                edge_band, glare, cx, cy, rr
            )
            strong = sum(v >= 0.12 for v in (top_s, left_s, right_s))
            if strong < 2:
                continue

            prior_bonus = 0.0
            if prior_small is not None:
                pcx, pcy, pr = prior_small
                dc = np.hypot(cx - pcx, cy - pcy) / max(pr, rr, 1.0)
                dr = abs(rr - pr) / max(pr, rr, 1.0)
                prior_bonus = float(np.exp(-2.0 * dc - 1.8 * dr))

            rank = score + 0.20 * prior_bonus
            item = {
                "found": score >= ARC_MIN_SCORE,
                "cx_s": cx,
                "cy_s": cy,
                "r_s": rr,
                "score": score,
                "rank": rank,
                "top_support": top_s,
                "left_support": left_s,
                "right_support": right_s,
                "method": "hough_reacquire",
            }
            if best is None or rank > best["rank"]:
                best = item

        return best if best is not None and best["found"] else None

    def process(self, frame, glare_mask=None, prior=None):
        if not self.enabled:
            return {"found": False}

        gray, glare, edges, edge_band = self._prepare_small(frame, glare_mask)
        scale = ARC_SCALE

        # 优先使用原 RingDetector 的先验；没有则用上一帧。
        if prior is not None and prior[2] and prior[2] > 1:
            prior_small = (prior[0] * scale, prior[1] * scale, prior[2] * scale)
        elif self.last is not None:
            prior_small = (self.last[0] * scale, self.last[1] * scale, self.last[2] * scale)
        else:
            prior_small = None

        result = None

        # ---- 快路径：局部圆弧拟合 ----
        if prior_small is not None:
            result = self._fit_near_prior(edges, edge_band, glare, prior_small)

        if result is not None:
            self.lost_count = 0
        else:
            self.lost_count += 1

            # 有上一帧时，允许短暂复用，避免偶发一帧反光导致重新跑 Hough。
            if self.last_result is not None and self.lost_count < ARC_REACQUIRE_AFTER:
                reused = dict(self.last_result)
                reused["method"] = "reuse_last"
                reused["edges"] = edges
                return reused

            # ---- 慢路径：首次/连续跟丢才重新 Hough ----
            result = self._global_reacquire(
                gray, edges, edge_band, glare, prior_small=prior_small
            )
            if result is None:
                return {"found": False, "edges": edges}
            self.lost_count = 0

        cx = result["cx_s"] / scale
        cy = result["cy_s"] / scale
        rr = result["r_s"] / scale

        # 轻量 EMA
        if self.last is not None:
            a = ARC_EMA_ALPHA
            cx = a * cx + (1.0 - a) * self.last[0]
            cy = a * cy + (1.0 - a) * self.last[1]
            rr = a * rr + (1.0 - a) * self.last[2]

        self.last = (cx, cy, rr)

        out = {
            "found": True,
            "cx_px": cx,
            "cy_px": cy,
            "r_outer_px": rr,
            "score": result.get("score", 0.0),
            "top_support": result.get("top_support", 0.0),
            "left_support": result.get("left_support", 0.0),
            "right_support": result.get("right_support", 0.0),
            "method": result.get("method", "arcfit_lite"),
            "edges": edges,
        }
        self.last_result = dict(out)
        return out


def fuse_ring_and_arc(ring_res, arc_res):
    """轻量融合：RingDetector 负责目标身份/数字，Arc 负责强反光下几何圆心。"""
    ring_ok = bool(ring_res.get("found"))
    arc_ok = bool(arc_res.get("found"))

    if ring_ok and arc_ok:
        rcx = float(ring_res["cx_px"])
        rcy = float(ring_res["cy_px"])
        rr = float(ring_res.get("r_outer_px", arc_res["r_outer_px"]) or arc_res["r_outer_px"])
        acx = float(arc_res["cx_px"])
        acy = float(arc_res["cy_px"])
        ar = float(arc_res["r_outer_px"])

        shift = np.hypot(acx - rcx, acy - rcy)
        max_shift = max(35.0, 0.30 * max(rr, ar, 1.0))

        out = dict(ring_res)
        if shift <= max_shift:
            # 不做复杂动态权重，固定 70% 几何圆弧 + 30% 原 detector。
            out["cx_px"] = 0.30 * rcx + 0.70 * acx
            out["cy_px"] = 0.30 * rcy + 0.70 * acy
            out["r_outer_px"] = 0.30 * rr + 0.70 * ar
            out["circle_method"] = "ring+arc_lite"
            out["arc_score"] = arc_res.get("score", 0.0)
            return out

        out["circle_method"] = "ring_only"
        out["arc_score"] = arc_res.get("score", 0.0)
        return out

    if ring_ok:
        out = dict(ring_res)
        out["circle_method"] = "ring_only"
        out["arc_score"] = 0.0
        return out

    if arc_ok:
        return {
            "found": True,
            "cx_px": arc_res["cx_px"],
            "cy_px": arc_res["cy_px"],
            "r_outer_px": arc_res["r_outer_px"],
            "digit": None,
            "circle_method": "arc_lite_fallback",
            "arc_score": arc_res.get("score", 0.0),
        }

    return {"found": False, "circle_method": "none", "arc_score": 0.0}

# ---------------- 串口通信(0xFF + 8 payload + 0xFE) ----------------
PKT_HEAD, PKT_TAIL = 0xFF, 0xFE
ACK_DONE = 0x01
ACK_WAIT_FAST = 3.0     # 非阻塞电机命令: 下位机即时回执
ACK_WAIT_ACT = 25.0     # 阻塞动作组(0x13 放料 / 0x16 夹料): 下位机整组完成后才回执

def build_cmd(mode, b1=0, b2=0, b3=0, b4=0, b5=0, b6=0, b7=0):
    return bytes([PKT_HEAD, mode, b1, b2, b3, b4, b5, b6, b7, PKT_TAIL])

def ttl_motor_cmd(mode, dir_, vel, clk):
    return build_cmd(mode, dir_,
                     (vel >> 8) & 0xFF, vel & 0xFF,
                     (clk >> 24) & 0xFF, (clk >> 16) & 0xFF,
                     (clk >> 8) & 0xFF, clk & 0xFF)

class Lower:
    def __init__(self, port, baud=115200, timeout=0.5):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    def send(self, cmd, wait_s=ACK_WAIT_FAST):
        """发命令并等 ACK(0xFF 0x01 0xFE)。wait_s 按命令类型给:
        非阻塞电机命令 3s 足够; 0x13/0x16 这类整组动作要 25s(下位机整组完成后才回执)。
        ★阻塞动作组必须等回执再发下一条, 否则命令会在下位机忙时被后帧覆盖丢失。"""
        self.ser.write(cmd)
        t0 = time.time()
        buf = bytearray()
        while time.time() - t0 < wait_s:
            b = self.ser.read(1)
            if not b:
                continue
            buf.append(b[0])
            if len(buf) >= 3 and buf[-3:] == bytes([0xFF, ACK_DONE, 0xFE]):
                return True
            if len(buf) > 16:
                buf = buf[-3:]
        print("[warn] ACK timeout"); return False

    def motor1(self, dir_, vel, clk):  return self.send(ttl_motor_cmd(0x10, dir_, vel, clk))
    def motor23(self, dir_, vel, clk): return self.send(ttl_motor_cmd(0x11, dir_, vel, clk))
    def motor4(self, dir_, vel, clk):  return self.send(ttl_motor_cmd(0x12, dir_, vel, clk))
    def place(self):                    return self.send(build_cmd(0x13), wait_s=ACK_WAIT_ACT)
    def grip(self, open_=True):         return self.send(build_cmd(0x14, 0 if open_ else 1))
    def grip_angle(self, deg):          return self.send(build_cmd(0x15, deg & 0xFF))
    def grab_from_vehicle(self):        return self.send(build_cmd(0x16), wait_s=ACK_WAIT_ACT)
    def aim_pose(self):                 return self.grip_angle(0)   # 张开0°防遮挡

# ---------------- 摄像头 + 检测器(照原版 demo 创建) ----------------
def make_detector():
    """按原版 CALIB 建检测器; center_gate_r 关闭(瞄准允许靶心在画面任意处起步)。"""
    cfg = dict(CALIB)                 # 拷贝,不改原版
    cfg["center_gate_r"] = 0          # 瞄准: 初始偏差可能 >200px, 关位置门
    return RingDetector(cfg)

def open_camera(cam_idx=None, exposure=None):
    """摄像头初始化: 与原版 demo 一致(MJPG/分辨率/锁曝光), 不另切画幅。
    依次尝试候选索引(设备号可能因插拔漂移): 指定 cam_idx > 候选列表。"""
    candidates = ([cam_idx] if cam_idx is not None else
                  [CALIB["camera_id"], 1, 0, 2, 3])
    for idx in candidates:
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        if not cap.isOpened():
            continue
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CALIB["width"])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CALIB["height"])
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if CALIB.get("lock_exposure"):
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            exp_value = 156 if exposure is None else exposure
            cap.set(cv2.CAP_PROP_EXPOSURE, exp_value)
            print(f"[cam] manual exposure={exp_value}")
            cap.set(cv2.CAP_PROP_AUTO_WB, 0)
            cap.set(cv2.CAP_PROP_WB_TEMPERATURE, 5000)
        # 确认真的能读到帧
        ok, _ = cap.read()
        if ok:
            print(f"[cam] 使用 /dev/video{idx}, "
                  f"实际 {int(cap.get(3))}x{int(cap.get(4))}")
            return cap
        cap.release()
    return None

# ---------------- 标定值(2026-09-03 实测, 1280x720, calibrate_aim.py) ----------------
K_PX2STEP_BASE = 0.2593   # base: 200脉冲→dx=+51.9px (dir0 画面右移 ✓)
K_PX2STEP_ARM  = 0.0413   # 伸缩: 200脉冲→dy=+8.3px  (dir0 画面下移 ✓)

# ★夹爪-摄像头中心不共轴补偿(单位 px, 1280x720 画面下; 数值你实测填):
#   瞄准的目标不是"圆心=画面中心", 而是"圆心=目标点(GX,GY)",
#   其中 GX/GY = 画面中心 + 偏移 —— 偏移量就是夹爪相对摄像头光轴的安装偏差。
#   标定方法: 把物料放夹爪正下方(夹爪对准物料), 记下此时圆心在画面的像素,
#             offset_x = 圆心x - 画面中心x, offset_y = 圆心y - 画面中心y
CAM_GRIP_OFFSET_X = 0.0   # 正=夹爪偏右(圆心需停在画面中心右侧 GX 处)
CAM_GRIP_OFFSET_Y = 0.0   # 正=夹爪偏下(圆心需停在画面中心下方 GY 处)

# ★2026-09-03 防晃调参(用户反馈: 速度太快 + 视觉延迟 + 伸缩台前后大幅摇晃):
#   1) 降速: arm 25RPM / base 40RPM —— 运动慢, 视觉每拍都能跟上
#   2) 拉长 settle: 0.4s —— 补偿 RingDetector 处理延迟(1280x720 半分辨率几十ms),
#      让检测到的是"运动后真正停稳"的位置, 减小相位滞后
#   3) 增益更软 + 阻尼更大: kp 0.3~0.35 / kd 0.4~0.6(arm 阻尼更强, 它最爱晃)
#   4) 单步输出限幅 OUT_MAX_PX=30: 一拍最多修 30px, 杜绝大步冲过头
BASE_VEL, ARM_VEL = 40, 25
STEP_MIN = 10
STEP_MAX = 250            # 单步脉冲上限(防大步过冲)
OUT_MAX_PX = 30.0         # PID 单拍输出像素上限
DEAD_ZONE = 8.0           # 收敛死区(px, 1280 宽)
STABLE_FRAMES = 5
SETTLE_S = 0.40           # 运动停稳后再等, 补偿视觉延迟

# 分轴 PID(防振荡; arm 更保守+更强阻尼)
PID_BASE = dict(kp=0.35, ki=0.005, kd=0.40, i_max=12.0)
PID_ARM  = dict(kp=0.30, ki=0.005, kd=0.60, i_max=10.0)

def frame_center(cap):
    return int(cap.get(3)) / 2, int(cap.get(4)) / 2

# ---------------- 推流(与原 ring demo 相同) ----------------
def start_stream(port=8090):
    import http.server
    class State:
        frame = None
    state = State()
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/stream'):
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                self.end_headers()
                try:
                    while True:
                        jpg = state.frame
                        if jpg is not None:
                            self.wfile.write(b'--frame\r\n')
                            self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                            self.wfile.write(jpg)
                            self.wfile.write(b'\r\n')
                        time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            elif self.path in ('/', '/index.html'):
                html = (b'<html><body style="margin:0;background:#111">'
                        b'<img src="/stream" style="width:100%;max-width:960px"></body></html>')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            else:
                self.send_response(404)
                self.end_headers()
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(('0.0.0.0', port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return state

def get_lan_ip():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "10.169.170.145"

# ---------------- 离散 PID ----------------
class PidAxis:
    """对单轴误差 e(px) 做增量式 PID, 输出 = 应移动的像素量(带符号)。
    含过冲阻尼: 方向翻转则输出减半, 防来回晃。"""
    def __init__(self, p):
        self.p = p
        self.integral = 0.0
        self.last_e = 0.0

    def step(self, e):
        kp, ki, kd = self.p['kp'], self.p['ki'], self.p['kd']
        damp = 0.5 if (self.last_e != 0.0 and e * self.last_e < 0.0) else 1.0
        self.integral += e
        imax = self.p['i_max']
        self.integral = max(-imax, min(imax, self.integral))
        d = e - self.last_e
        self.last_e = e
        out = damp * (kp * e + ki * self.integral + kd * d)
        # 输出限幅: 单拍最多修 OUT_MAX_PX 像素, 防大步过冲
        out = max(-OUT_MAX_PX, min(OUT_MAX_PX, out))
        return out

# ---------------- 闭环: 阶段A base 收敛 x, 阶段B 伸缩收敛 y ----------------
def pump_frames(cap, det, secs, preproc=None, arc_det=None):
    """电机停稳期间只更新原 RingDetector 的 EMA；不跑高光mask/Hough，降低CPU。"""
    t_end = time.time() + secs
    while time.time() < t_end:
        ok, frame = cap.read()
        if ok:
            frame = rotate_ccw(frame, CALIB)
            det.process(frame)
        time.sleep(0.04)

def aim_loop(lower, cap, det, state=None, show=False, preproc=None, filter_view=False, arc_det=None, arc_view=False):
    icx, icy = frame_center(cap)
    last_stream_t = 0.0
    # 目标点 = 画面中心 + 夹爪-摄像头安装偏移(不共轴补偿)
    gx, gy = icx + CAM_GRIP_OFFSET_X, icy + CAM_GRIP_OFFSET_Y
    pid_x = PidAxis(PID_BASE)
    pid_y = PidAxis(PID_ARM)
    off_base = 0
    off_arm = 0
    stage = 'X'        # 三段瞄准: X(校base-x) → Y(校伸缩-y) → X2(二次校base-x)
    x_pass = 1         # 第几次 X(1=首次, 2=Y之后复校)
    stable = 0

    for it in range(300):
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.05); continue
        frame = rotate_ccw(frame, CALIB)         # 与原版一致(当前 False 即不旋转)
        detect_frame = frame
        glare_mask = None
        if preproc is not None:
            detect_frame, glare_mask = preproc.process(frame)
        ring_res = det.process(detect_frame)      # 原 RingDetector：数字/粗圆心
        prior = None
        if ring_res.get("found"):
            prior = (ring_res["cx_px"], ring_res["cy_px"],
                     ring_res.get("r_outer_px", 0.0))
        arc_res = ({"found": False} if arc_det is None else
                   arc_det.process(detect_frame, glare_mask, prior=prior))
        res = fuse_ring_and_arc(ring_res, arc_res)

        if arc_view and arc_res.get("edges") is not None:
            e = arc_res["edges"]
            e = cv2.resize(e, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_NEAREST)
            disp = cv2.cvtColor(e, cv2.COLOR_GRAY2BGR)
        else:
            disp = detect_frame if filter_view else frame
        if state is not None or show:
            disp = disp.copy()
            if glare_mask is not None and (filter_view or arc_view or show):
                glare_contours, _ = cv2.findContours(
                    glare_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(disp, glare_contours, -1, (255, 0, 255), 1)
            if arc_res.get("found"):
                acx, acy = int(arc_res["cx_px"]), int(arc_res["cy_px"])
                ar = int(arc_res["r_outer_px"])
                # 青色 = 只用剩余圆弧反推出的圆；绿色 = 最终融合结果
                cv2.circle(disp, (acx, acy), ar, (255, 255, 0), 2)
                cv2.drawMarker(disp, (acx, acy), (255, 255, 0),
                               cv2.MARKER_CROSS, 12, 1)
                cv2.putText(disp,
                            f"arc score={arc_res.get('score', 0.0):.2f}",
                            (8, 42), cv2.FONT_HERSHEY_SIMPLEX,
                            0.50, (255, 255, 0), 1)
        if res["found"]:
            cx, cy = res["cx_px"], res["cy_px"]
            r = res.get("r_outer_px", 0.0)
            # 偏差 = 圆心 相对【目标点】(非画面中心)
            dx, dy = cx - gx, cy - gy
            stagelbl = stage if stage == 'Y' else f"{stage}{x_pass}"   # X1/X2/Y
            if state is not None or show:
                cv2.circle(disp, (int(cx), int(cy)), int(r), (0, 255, 0), 2)
                # 目标点: 黄色 ×(夹爪要对准处); 画面中心: 小红点(参考)
                cv2.drawMarker(disp, (int(gx), int(gy)), (0, 255, 255),
                               cv2.MARKER_CROSS, 18, 2)
                cv2.circle(disp, (int(icx), int(icy)), 3, (0, 0, 255), -1)
                txt = (f"stage={stagelbl} dx={dx:+.1f} dy={dy:+.1f} "
                       f"off_b={off_base:+d} off_a={off_arm:+d} digit={res.get('digit')} "
                       f"{res.get('circle_method', '')}")
                cv2.putText(disp, txt, (8, 20), cv2.FONT_HERSHEY_SIMPLEX,
                            0.55, (0, 255, 255), 1)
            tag = (f"[{it}] stage={stagelbl} center=({cx:.0f},{cy:.0f}) "
                   f"dx={dx:+.1f} dy={dy:+.1f} r={r:.0f} digit={res.get('digit')} "
                   f"method={res.get('circle_method', '')} arc={res.get('arc_score', 0.0):.2f}")
        else:
            dx = dy = None
            stagelbl = stage if stage == 'Y' else f"{stage}{x_pass}"
            tag = f"[{it}] stage={stagelbl} not found"
            if state is not None or show:
                cv2.putText(disp, "not found", (8, 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
        print(tag)

        if state is not None and (time.time() - last_stream_t) >= 0.10:
            okj, jpg = cv2.imencode('.jpg', disp, [cv2.IMWRITE_JPEG_QUALITY, 72])
            if okj:
                state.frame = jpg.tobytes()
                last_stream_t = time.time()
        if show:
            cv2.imshow("aim", disp)
            if cv2.waitKey(1) & 0xFF == 27:
                return False, off_base, off_arm

        if not res["found"] or dx is None:
            continue

        if stage == 'X':
            e = dx
            if abs(e) <= DEAD_ZONE:
                stable += 1
                if stable >= STABLE_FRAMES:
                    if x_pass == 1:
                        print(f"  [X#{x_pass} done] dx={dx:+.1f} 进死区, 转阶段Y(校伸缩-y)")
                        stage = 'Y'; x_pass = 2
                    else:
                        print(f"  [X#{x_pass} done] dx={dx:+.1f} 二次复校完成")
                        return True, off_base, off_arm
                    stable = 0
                continue
            stable = 0
            out = pid_x.step(e)
            steps = int(abs(out) / K_PX2STEP_BASE) or STEP_MIN
            steps = max(STEP_MIN, min(STEP_MAX, steps))
            dir_ = 1 if out > 0 else 0
            if lower is None:
                print(f"      [demo] would base dir={dir_} steps={steps}")
                time.sleep(0.2); continue
            lower.motor4(dir_, BASE_VEL, steps)
            off_base += steps if dir_ == 0 else -steps
            print(f"      -> base dir={dir_} steps={steps} (off_base={off_base:+d})")
            pump_frames(cap, det, steps / (BASE_VEL * 3200 / 60) + SETTLE_S, preproc, arc_det)
        else:                                   # stage == 'Y': 校伸缩-y
            e = dy
            if abs(e) <= DEAD_ZONE:
                stable += 1
                if stable >= STABLE_FRAMES:
                    print(f"  [Y done] dy={dy:+.1f} 进死区, 回 X 二次复校 base-x")
                    stage = 'X'; stable = 0
                    continue
                continue
            stable = 0
            out = pid_y.step(e)
            steps = int(abs(out) / K_PX2STEP_ARM) or STEP_MIN
            steps = max(STEP_MIN, min(STEP_MAX, steps))
            dir_ = 1 if out > 0 else 0
            if lower is None:
                print(f"      [demo] would arm dir={dir_} steps={steps}")
                time.sleep(0.2); continue
            lower.motor1(dir_, ARM_VEL, steps)
            off_arm += steps if dir_ == 0 else -steps
            print(f"      -> arm dir={dir_} steps={steps} (off_arm={off_arm:+d})")
            pump_frames(cap, det, steps / (ARM_VEL * 3200 / 60) + SETTLE_S, preproc, arc_det)

    print("[fail] 迭代超限未收敛")
    return False, off_base, off_arm

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--cam", type=int, default=None,
                    help="摄像头索引(默认自动尝试; 设备号漂移时可指定)")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--stream", type=int, default=0)
    ap.add_argument("--no-filter", action="store_true",
                    help="关闭强反光预处理，直接使用原始画面检测")
    ap.add_argument("--filter-view", action="store_true",
                    help="推流/窗口显示滤波后的画面；默认仍显示原始画面")
    ap.add_argument("--no-arc", action="store_true",
                    help="关闭 Canny+反光mask+Hough+残缺圆弧拟合")
    ap.add_argument("--arc-view", action="store_true",
                    help="推流/窗口直接显示去反光后的 Canny 边缘，方便调阈值")
    ap.add_argument("--exposure", type=float, default=None,
                    help="覆盖相机手动曝光值，例如 --exposure 110；默认沿用原值156")
    ap.add_argument("--save", default="aim_offset.txt")
    ap.add_argument("--grab", action="store_true",
                    help="触发下位机 0x16 去车载物架夹料(机械须在观察位)")
    ap.add_argument("--replay", action="store_true",
                    help="读 aim_offset.txt, 从观察位回放偏移到靶心并 0x13 放料")
    ap.add_argument("--home", action="store_true",
                    help="瞄准后归位: 读偏移反向回观察位(供单独调试用)")
    ap.add_argument("--mission", action="store_true",
                    help="全流程: 瞄准记录偏移 → 归位 → 0x16去物架夹料 → 回放偏移回靶心 → 0x13放料")
    a = ap.parse_args()

    # ── 纯动作模式(不需摄像头): --grab / --replay / --home ──
    if (a.grab or a.replay or a.home) and not a.demo:
        lower = Lower(a.port, a.baud)
        if a.home:
            return_to_home(lower, a.save)
        if a.grab:
            print("--- 0x16 去车载物架夹料(下位机动作组,结束时回观察位) ---")
            lower.grab_from_vehicle()      # 等整组完成回执(25s)再继续
        if a.replay:
            replay_and_place(lower, a.save)
        return 0 if (a.replay or a.grab or a.home) else 2

    det = make_detector()
    preproc = GlarePreprocessor(enabled=not a.no_filter)
    arc_det = ArcCircleDetector(enabled=not a.no_arc)
    print(f"[vision] glare mask={'OFF' if a.no_filter else 'ON'} (lightweight BGR threshold)")
    print(f"[vision] partial-arc detector={'OFF' if a.no_arc else 'ON'} "
          f"(ROI Canny+arcfit; Hough only on reacquire, scale={ARC_SCALE})")
    cap = open_camera(a.cam if a.cam is not None else None, exposure=a.exposure)
    if cap is None:
        print("[err] camera open failed, 试试 --cam 1/2/3"); return 1

    state = start_stream(a.stream) if a.stream else None
    if state:
        print(f"[stream] 浏览器打开 http://{get_lan_ip()}:{a.stream}/ 查看实时画面")
    lower = None if a.demo else Lower(a.port, a.baud)

    if not a.demo and lower is not None:
        print("--- 瞄准姿态:夹爪张开 0°(不遮挡) ---")
        lower.aim_pose()
        time.sleep(0.5)

    print("--- 开始瞄准: 阶段A=base收敛x → 阶段B=伸缩收敛y ---")
    ok, off_base, off_arm = aim_loop(
        lower, cap, det, state=state, show=a.show,
        preproc=preproc, filter_view=a.filter_view,
        arc_det=arc_det, arc_view=a.arc_view
    )

    if ok:
        line = (f"offset_base_pulses={off_base:+d}\n"
                f"offset_arm_pulses={off_arm:+d}\n")
        print("=" * 52)
        print("瞄准完成! 净偏移(夹料后回观察位复现即对准靶心):")
        print(f"  base轴: {off_base:+d} 脉冲 (dir0 为正=画面右移方向)")
        print(f"  伸缩台: {off_arm:+d} 脉冲 (dir0 为正=画面下移方向)")
        print(f"  已保存: ~/vision_robot/{a.save}")
        print("=" * 52)
        try:
            with open(a.save, "w") as f:
                f.write(line)
        except Exception as e:
            print("[warn] save failed:", e)

        # ── --mission 全流程: 瞄准 → 归位 → 夹料 → 回放放料 ──
        if a.mission and lower is not None:
            print("\n[mission] 阶段0: 瞄准完成,归位回观察位(撤销瞄准偏移)")
            return_to_home(lower, a.save)
            print("[mission] 阶段1: 0x16 去车载物架夹料(下位机动作组,结束回观察位)")
            lower.grab_from_vehicle()      # 等整组完成回执(25s), 避免与回放命令打架
            print("[mission] 夹料完成, 阶段2: 回放偏移到靶心并放料")
            replay_and_place(lower, a.save)
            print("[mission] 全流程完成")
    else:
        print("[aborted] 未收敛或中断, 不记录偏移")
    cap.release()
    return 0 if ok else 2

def replay_and_place(lower, save_path="aim_offset.txt"):
    """放置段: 读瞄准偏移 → 从观察位回放 base/arm 到靶心 → 0x13 放料。"""
    if not os.path.exists(save_path):
        print(f"[err] 找不到 {save_path}, 先跑一次瞄准生成偏移"); return
    off_base = off_arm = 0
    with open(save_path) as f:
        for ln in f:
            ln = ln.strip()
            if ln.startswith("offset_base_pulses="):
                off_base = int(ln.split("=")[1])
            elif ln.startswith("offset_arm_pulses="):
                off_arm = int(ln.split("=")[1])
    print(f"--- 回放偏移: base={off_base:+d} arm={off_arm:+d} (dir0 为正) ---")
    if off_base != 0:
        dir_ = 0 if off_base > 0 else 1
        clk = abs(off_base)
        print(f"  base dir={dir_} steps={clk}")
        lower.motor4(dir_, BASE_VEL, clk)
        time.sleep(clk / (BASE_VEL * 3200 / 60) + 0.5)
    if off_arm != 0:
        dir_ = 0 if off_arm > 0 else 1
        clk = abs(off_arm)
        print(f"  arm  dir={dir_} steps={clk}")
        lower.motor1(dir_, ARM_VEL, clk)
        time.sleep(clk / (ARM_VEL * 3200 / 60) + 0.5)
    print("--- 已回靶心, 发 0x13 放料(龙门架下放-开爪-抬升) ---")
    lower.place()      # 等整组完成回执(25s)再宣告完成
    print("[done] 放料完成")

def return_to_home(lower, save_path="aim_offset.txt"):
    """瞄准后归位: 瞄准结束时停在靶心, 反向回放回观察位(供 0x16 夹料用)。"""
    if not os.path.exists(save_path):
        print("[warn] 无偏移文件, 认为已在观察位, 跳过归位"); return
    off_base = off_arm = 0
    with open(save_path) as f:
        for ln in f:
            ln = ln.strip()
            if ln.startswith("offset_base_pulses="):
                off_base = int(ln.split("=")[1])
            elif ln.startswith("offset_arm_pulses="):
                off_arm = int(ln.split("=")[1])
    print(f"--- 瞄准后归位: 反向回放 base={-off_base:+d} arm={-off_arm:+d} ---")
    if off_base != 0:
        dir_ = 1 if off_base > 0 else 0
        clk = abs(off_base)
        print(f"  base 归位 dir={dir_} steps={clk}")
        lower.motor4(dir_, BASE_VEL, clk)
        time.sleep(clk / (BASE_VEL * 3200 / 60) + 0.5)
    if off_arm != 0:
        dir_ = 1 if off_arm > 0 else 0
        clk = abs(off_arm)
        print(f"  arm  归位 dir={dir_} steps={clk}")
        lower.motor1(dir_, ARM_VEL, clk)
        time.sleep(clk / (ARM_VEL * 3200 / 60) + 0.5)
    print("--- 已回到观察位 ---")

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(130)
