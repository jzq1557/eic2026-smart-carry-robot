#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
圆环检测 + 环心数字识别(1/2/3, 任意旋转) —— 精简版
====================================================
检测方式(沿用 ring_center_simple.py 的粗圆环带思路, 快):
  灰度 -> 自适应二值化(环线是暗色) -> 闭运算接断线 -> 最大连通域
  -> 最小外接圆 -> 圆心即靶心。无霍夫/无聚类/无标定。
新增功能:
  检出圆环后, 识别环心阿拉伯数字(只需区分 1/2/3, 支持 360° 任意旋转):
  环心裁剪 -> 自适应阈值 -> 中心区字形 -> 归一化
  -> 与"1/2/3 × 多种字体 × 每5°旋转"模板库做 NCC 匹配
  -> 推流画面在圆心处用黄色标注(纯 ASCII Hershey 字体, 无字体依赖)
数字稳定性(滑动窗口众数):
  输出 = 最近 N 次(默认20)高置信读数中出现最多的数字(1/2/3);
  窗口随新读数滚动, 不锁定; 环长时间丢失或环心跳变(换了环)才清空窗口重新统计。

用法:
  python3 ring_center_detect_v2.py               # 有显示器: 开窗
  python3 ring_center_detect_v2.py --stream      # SSH: 浏览器看 http://<树莓派IP>:8080/
  python3 ring_center_detect_v2.py --no-show     # SSH: 只看终端
  python3 ring_center_detect_v2.py 图片.jpg       # 单张图片调试
无显示环境(SSH)下不加参数会自动降级为 --no-show 模式, 不会崩溃。
画面方向: CALIB["rotate_ccw"]=True(默认)时, 摄像头画面逆时针旋转90°;
  旋转在读帧后立即做, 所有输出坐标(环心/半径)都是旋转后画面的坐标。
按键(仅显示模式): ESC/q 退出

集成接口(兼容旧版, capture_check.sh 等仍可用):
  from ring_center_detect_v2 import RingDetector, CALIB
  res = RingDetector(CALIB).process(frame)
  # res: {found, cx_px, cy_px, r_outer_px, n_rings, conf, stale,
  #       digit, digit_score, raw}
"""

import os
import sys
import time
import threading
import numpy as np
import cv2 as cv

# ----------------------------------------------------------------------
# 配置 —— 阈值/系数按现场微调
# ----------------------------------------------------------------------
CALIB = {
    "camera_id": 0,
    "width": 1280,              # 720p 高清(帧率约12fps); 要更快改 640x480
    "height": 720,
    # ---- 圆环检测(粗圆环带) ----
    "thr_block": 41,            # 自适应阈值块大小: 越大越抗光照不均
    "thr_c": 5,                 # 提取灵敏度: 调大只留更暗的线(调小更灵敏, 5为灵敏度上调)
    "close_k": 9,               # 闭运算核(px): ≈相邻环线在画面里的间距
    "min_area_frac": 0.001,     # 连通域至少占画面0.1%, 过滤小噪声(上调灵敏度后放宽)
    "center_gate_r": 200,       # 位置要求: 环心须在画面中心半径200px的圆内才识别, 0=关闭
    "proc_scale": 0.5,          # 环检测用半分辨率(快~3倍), 坐标自动换算回原图; 要最高精度改1.0
    "ema": 0.5,                 # 中心平滑系数(0~1, 越大越稳)
    "stale_max": 10,            # 连续丢失>该帧数 -> conf 降级
    # ---- 环心数字识别(1/2/3) ----
    "digit_rot_step": 5,        # 旋转模板步长(度): 5°=72张/字, 覆盖360°任意旋转
    "digit_thr": 0.42,          # NCC 阈值: 低于此视为未识别(无数字基线≈0.00, 斜置细'1'≈0.45~0.50)
    "digit_votes": 20,          # 数字稳定性: 最近 N 次高置信读数(滑动窗口)取众数作为输出
    "digit_unlock_stale": 15,   # 环连续丢失超过该帧数 -> 清空窗口(重新统计)
    "jump_px": 120,             # 原始圆心与平滑中心偏离超过该值(px) -> 视为换了环, 清空窗口
    "digit_inner_frac": 0.80,   # 数字裁剪边长 = 2*最内环半径φ1*该系数(严格在φ1之内, 虚线环也不会进裁剪区)
    "inner_fallback_frac": 0.55,  # 找不到φ1时回退: 2*外环半径*该系数
    "digit_margin": 0.05,       # 与次优类的最小分数差, 小于此输出'?'
    # ---- 相机 ----
    "lock_exposure": True,      # 锁定曝光/白平衡(颜色任务的前提, 圆心检测也受益)
    "rotate_ccw": False,         # 摄像头画面逆时针旋转90°(适配安装方向);
                                # 旋转在读帧后立即做, 输出坐标(环心/半径)均为旋转后画面坐标
}


# ----------------------------------------------------------------------
# 圆环检测: 粗圆环带 -> 最大连通域 -> 最小外接圆
# ----------------------------------------------------------------------
def rotate_ccw(frame, cfg):
    """摄像头画面逆时针旋转90°(适配安装方向), 关闭则原样返回。
    必须在检测前调用: 这样输出坐标(环心/半径)天然就是旋转后画面的坐标。"""
    if cfg.get("rotate_ccw", False):
        return cv.rotate(frame, cv.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def find_center(frame, cfg, want_bw=False):
    """返回 (cx, cy, r) 或 None；want_bw=True 时返回 (cx, cy, r, bw)。
    ★want_bw 是给 RingDetector.process 用的: φ1 那一步还要在同一尺度上做一次
      阈值+闭运算, 复用这里的 bw 能省掉一整套算子(RDK X5 上约 10ms/帧)。"""
    gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
    gray = cv.GaussianBlur(gray, (5, 5), 0)
    bw = cv.adaptiveThreshold(gray, 255, cv.ADAPTIVE_THRESH_GAUSSIAN_C,
                              cv.THRESH_BINARY_INV, cfg["thr_block"], cfg["thr_c"])
    k = cv.getStructuringElement(cv.MORPH_ELLIPSE, (cfg["close_k"], cfg["close_k"]))
    bw = cv.morphologyEx(bw, cv.MORPH_CLOSE, k)
    contours, _ = cv.findContours(bw, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    h, w = gray.shape
    r_max = 0.5 * min(h, w)     # 真靶标外接圆不会超过画面短边一半, 滤掉背景伪影
    for c in sorted(contours, key=cv.contourArea, reverse=True):
        if cv.contourArea(c) < cfg["min_area_frac"] * w * h:
            return None
        (cx, cy), r = cv.minEnclosingCircle(c)
        if r <= r_max:
            if want_bw:
                return float(cx), float(cy), float(r), bw
            return float(cx), float(cy), float(r)
    return None


def find_inner_radius(gray, cx, cy, r_out, cfg, bw_full=None):
    """找最内环线半径 φ1(px): 环线围出的"白洞"中, 取贴近环心且半径最小的洞
    (= 最内环 φ1 围出的中心洞; φ1 虚线时该洞不存在, 会退到 φ2 的洞)。
    数字在洞内不影响洞轮廓。返回 φ1 半径, 找不到返回 None。
    gray/坐标/半径需在同一尺度下。
    ★bw_full 给出时(同一尺度的全图二值图)直接切 ROI, 跳过阈值+闭运算;
      注意它是"全图阈值"的结果, 与"ROI 内单独阈值"在边界附近可能略有差别(已过回归)。"""
    h, w = gray.shape
    r_scan = max(int(r_out * 0.95), 10)
    y0 = max(0, int(cy) - r_scan)
    y1 = min(h, int(cy) + r_scan)
    x0 = max(0, int(cx) - r_scan)
    x1 = min(w, int(cx) + r_scan)
    if x1 - x0 < 24 or y1 - y0 < 24:
        return None
    if bw_full is not None:
        bw = bw_full[y0:y1, x0:x1]
    else:
        roi = gray[y0:y1, x0:x1]
        bw = cv.adaptiveThreshold(roi, 255, cv.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv.THRESH_BINARY_INV, cfg["thr_block"], cfg["thr_c"])
        k = cv.getStructuringElement(cv.MORPH_ELLIPSE, (cfg["close_k"], cfg["close_k"]))
        bw = cv.morphologyEx(bw, cv.MORPH_CLOSE, k)
    cnts, hier = cv.findContours(bw, cv.RETR_CCOMP, cv.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return None
    oy, ox = int(cy) - y0, int(cx) - x0
    best_r, min_area = None, 0.0
    for i, c in enumerate(cnts):
        if hier[0][i][3] == -1:                # 只看洞(有父轮廓的轮廓)
            continue
        area = cv.contourArea(c)
        if area < 1000:                        # 滤小噪声洞
            continue
        M = cv.moments(c)
        if M['m00'] <= 0:
            continue
        bcx = M['m10'] / M['m00']
        bcy = M['m01'] / M['m00']
        if np.hypot(bcx - ox, bcy - oy) > 0.5 * r_scan:   # 洞须贴近环心
            continue
        (_, _), rr = cv.minEnclosingCircle(c)
        if rr <= 0.15 * r_scan:                # 太小不可能是环洞
            continue
        if best_r is None or rr < best_r:
            best_r = rr
            min_area = area
    return best_r


# ----------------------------------------------------------------------
# 环心数字识别: 1/2/3, 360° 旋转不变(旋转模板库 + NCC)
# ----------------------------------------------------------------------
_DIGIT_TM = None        # (labels: list[str], T: (N,784) 每行单位范数)


def _glyph_tensor(glyph, size=28):
    """二值字形 -> size×size 均值归零、单位范数向量(重心居中)。"""
    h, w = glyph.shape
    s = max(h, w)
    sq = np.zeros((s, s), np.uint8)
    sq[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = glyph
    sq = cv.resize(sq, (size, size), interpolation=cv.INTER_AREA)
    m = cv.moments(sq)
    if m['m00'] > 0:
        dx = int(round(size / 2 - m['m10'] / m['m00']))
        dy = int(round(size / 2 - m['m01'] / m['m00']))
        sq = cv.warpAffine(sq, np.float32([[1, 0, dx], [0, 1, dy]]),
                           (size, size), borderValue=0)
    sq = sq.astype(np.float32) / 255.0
    sq -= sq.mean()
    n = np.linalg.norm(sq)
    return sq / n if n > 1e-6 else sq


def _build_rot_templates(step=5):
    """渲染 1/2/3 × 多种字体/粗细 × 每 step° 旋转 的模板矩阵(懒加载)。"""
    global _DIGIT_TM
    if _DIGIT_TM is not None:
        return _DIGIT_TM
    labels, rows = [], []
    for ch in '123':
        for font in (cv.FONT_HERSHEY_SIMPLEX, cv.FONT_HERSHEY_DUPLEX,
                     cv.FONT_HERSHEY_COMPLEX, cv.FONT_HERSHEY_TRIPLEX):
            for thick in (1, 2, 3):
                img = np.zeros((128, 128), np.uint8)
                cv.putText(img, ch, (8, 100), font, 4.0, 255, thick, cv.LINE_AA)
                nz = cv.findNonZero(img)
                if nz is None:
                    continue
                x, y, w, h = cv.boundingRect(nz)
                if w < 2 or h < 2:
                    continue
                glyph = img[y:y + h, x:x + w]
                # 字形补成正方形, 放大到 96, 放进 144 画布(旋转不裁边)
                s = max(w, h)
                sq = np.zeros((s, s), np.uint8)
                sq[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = glyph
                sq = cv.resize(sq, (96, 96), interpolation=cv.INTER_AREA)
                canvas = np.zeros((144, 144), np.uint8)
                canvas[24:120, 24:120] = sq
                cxc, cyc = 72, 72
                for ang in range(0, 360, step):
                    M = cv.getRotationMatrix2D((cxc, cyc), ang, 1.0)
                    rot = cv.warpAffine(canvas, M, (144, 144),
                                        flags=cv.INTER_LINEAR, borderValue=0)
                    nz = cv.findNonZero(rot)
                    if nz is None:
                        continue
                    rx, ry, rw, rh = cv.boundingRect(nz)
                    if rw < 2 or rh < 2:
                        continue
                    rows.append(_glyph_tensor(rot[ry:ry + rh, rx:rx + rw], 28).flatten())
                    labels.append(ch)
    _DIGIT_TM = (labels, np.stack(rows).astype(np.float32))
    return _DIGIT_TM


def recognize_digit(frame, cx, cy, r_inner_px, cfg):
    """识别圆环中心数字(1/2/3, 任意旋转)。
    裁剪严格限制在最内环 φ1 之内(数字在环心附近、φ1 以内),
    环线(实线/虚线)进不了裁剪区, 不会把虚线段误当数字。
    返回 (digit 或 None, 置信度)。"""
    gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    h, w = gray.shape[:2]
    side = max(int(2 * r_inner_px * cfg.get("digit_inner_frac", 0.80)), 24)
    x0 = max(0, int(cx) - side // 2)
    y0 = max(0, int(cy) - side // 2)
    x1 = min(w, x0 + side)
    y1 = min(h, y0 + side)
    if x1 - x0 < 12 or y1 - y0 < 12:
        return None, 0.0
    crop = gray[y0:y1, x0:x1]
    if crop.mean() < 128:                       # 深底 -> 反相成"暗字亮底"
        crop = 255 - crop
    bw = cv.adaptiveThreshold(crop, 255, cv.ADAPTIVE_THRESH_GAUSSIAN_C,
                              cv.THRESH_BINARY_INV, 15, 5)
    # 细笔画(如斜置的'1')旋转/反锯齿后易断成碎段: 先闭后开, 补断线去噪
    bw = cv.morphologyEx(bw, cv.MORPH_CLOSE,
                         cv.getStructuringElement(cv.MORPH_ELLIPSE, (3, 3)))
    bw = cv.morphologyEx(bw, cv.MORPH_OPEN,
                         cv.getStructuringElement(cv.MORPH_ELLIPSE, (3, 3)))
    chh, cww = bw.shape
    cnts, _ = cv.findContours(bw, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None, 0.0
    # 只留"中心区、不碰裁剪边界、尺寸像数字"的轮廓:
    #   环线弧段会贴到裁剪边缘(被排除), 数字在中心内部(被保留);
    #   防'1'断段 -> 取并集作字形
    keep = []
    for c in cnts:
        x, y, ww, hh = cv.boundingRect(c)
        if x <= 1 or y <= 1 or x + ww >= cww - 1 or y + hh >= chh - 1:
            continue                                # 贴边 = 环线弧段
        bcx, bcy = x + ww / 2, y + hh / 2
        if abs(bcx - cww / 2) > 0.45 * cww or abs(bcy - chh / 2) > 0.45 * chh:
            continue
        if hh < side * 0.15 or hh > side * 0.95:
            continue
        keep.append((x, y, ww, hh))
    if not keep:
        return None, 0.0
    x = min(k[0] for k in keep)
    y = min(k[1] for k in keep)
    x2 = max(k[0] + k[2] for k in keep)
    y2 = max(k[1] + k[3] for k in keep)
    glyph = bw[y:y2, x:x2]
    if glyph.shape[0] < 4 or glyph.shape[1] < 4:
        return None, 0.0
    g = _glyph_tensor(glyph, 28).flatten()
    labels, T = _build_rot_templates(int(cfg.get("digit_rot_step", 5)))
    scores = T @ g                               # 一次矩阵乘出所有模板得分
    char_best = {}
    for ch in '123':
        idx = [i for i, lab in enumerate(labels) if lab == ch]
        char_best[ch] = float(np.max(scores[idx]))
    ranked = sorted(char_best.items(), key=lambda kv: kv[1], reverse=True)
    best_ch, best_s = ranked[0]
    second_s = ranked[1][1]
    if best_s < cfg["digit_thr"]:                # 太像噪声/无数字
        return None, best_s
    if best_s - second_s < cfg["digit_margin"]:  # 与次优太接近 -> 存疑
        return '?', best_s
    return best_ch, best_s


# ----------------------------------------------------------------------
# 检测器(接口兼容旧版)
# ----------------------------------------------------------------------
class RingDetector:
    def __init__(self, calib):
        self.cal = dict(calib)
        self.sx = self.sy = self.sr = None
        self.stale = 0
        self.votes = []              # 滑动窗口: 最近 N 次高置信读数(digit字符)
        self.n = 0                   # 已处理帧计数(给 digit_every 节流用)
        self.last_digit = (None, 0.0)

    def _window_mode(self, n_votes):
        """最近 n_votes 次读数中最多出现的数字(众数), 返回 (digit 或 None, 占比)。"""
        if not self.votes:
            return None, 0.0
        cnt = {}
        for d in self.votes:
            cnt[d] = cnt.get(d, 0) + 1
        maxc = max(cnt.values())
        tied = [d for d in cnt if cnt[d] == maxc]
        if len(tied) == 1:
            best = tied[0]
        else:                       # 平票: 取窗口内最早出现的
            best = min(tied, key=lambda d: next(
                i for i, dd in enumerate(self.votes) if dd == d))
        return best, maxc / float(len(self.votes))

    def _clear_votes(self):
        self.votes = []

    def _lost(self, cfg):
        """环未识别(找不到/位置不合格)时的一帧输出: 计数丢失, 超阈值清窗口。"""
        self.stale += 1
        if self.stale >= cfg.get("digit_unlock_stale", 15):
            self._clear_votes()
        out = {"found": False, "conf": 0.0, "stale": self.stale}
        if self.sx is not None and self.sy is not None:
            out["cx_px"], out["cy_px"], out["r_outer_px"] = self.sx, self.sy, self.sr
        digit, dscore = self._window_mode(int(cfg.get("digit_votes", 20)))
        out["digit"], out["digit_score"] = digit, dscore
        return out

    def process(self, frame):
        cfg = self.cal
        self.n += 1                  # 帧计数(数字识别节流用)
        scale = float(cfg.get("proc_scale", 1.0))
        if scale != 1.0:
            small = cv.resize(frame, None, fx=scale, fy=scale,
                              interpolation=cv.INTER_AREA)
        else:
            small = frame
        r = find_center(small, cfg, want_bw=True)
        if r is None:
            return self._lost(cfg)
        # 换算回原图坐标; bw_small 留在检测尺度上给 φ1 复用(省一次阈值+闭运算)
        cx, cy, rad, bw_small = r[0] / scale, r[1] / scale, r[2] / scale, r[3]
        # 位置要求: 环心须在画面中心半径 center_gate_r(默认200px)的圆内才识别
        gate_r = float(cfg.get("center_gate_r", 0))
        if gate_r > 0:
            fh, fw = frame.shape[:2]
            if np.hypot(cx - fw / 2.0, cy - fh / 2.0) > gate_r:
                return self._lost(cfg)
        if self.sx is None:
            self.sx, self.sy, self.sr = cx, cy, rad
        else:
            a = cfg["ema"]
            self.sx += a * (cx - self.sx)
            self.sy += a * (cy - self.sy)
            self.sr += a * (rad - self.sr)
        self.stale = 0
        # 原始圆心与平滑中心大幅偏离 -> 换了环: EMA 立即跟上新环并清空窗口
        if self.sx is not None and np.hypot(cx - self.sx, cy - self.sy) > \
                cfg.get("jump_px", 120):
            self.sx, self.sy, self.sr = cx, cy, rad
            self._clear_votes()
        conf = 1.0 if self.stale < cfg["stale_max"] else \
            max(0.0, 1.0 - self.stale / cfg["stale_max"])
        # 找最内环半径 φ1(在检测尺度图像上做径向剖面, 省算力):
        # 数字在 φ1 之内, 裁剪严格按 φ1 取, 环线(实线/虚线)进不了裁剪区
        r_inner = None
        gs = small if small.ndim == 2 else cv.cvtColor(small, cv.COLOR_BGR2GRAY)
        ri = find_inner_radius(gs, self.sx * scale, self.sy * scale,
                               self.sr * scale, cfg, bw_full=bw_small)
        if ri:
            r_inner = ri / scale
        if not r_inner:
            r_inner = self.sr * cfg.get("inner_fallback_frac", 0.55)
        # ★数字识别节流: 每 digit_every 帧才做一次 NCC 匹配(默认 1 = 每帧, 行为不变)。
        #   弱算力平台设 3~4 可省掉这段(裁剪+阈值+形态学+矩阵乘), 而投票窗口本身
        #   就是在做多帧平滑, 少算几帧不影响最终众数。
        de = max(1, int(cfg.get("digit_every", 1)))
        if (self.n % de == 0) or (not self.votes):
            digit, dscore = recognize_digit(frame, self.sx, self.sy, r_inner, cfg)
            self.last_digit = (digit, dscore)
            # 只把高置信且明确的 1/2/3 计入滑动窗口
            if digit in ('1', '2', '3') and dscore >= cfg["digit_thr"]:
                self.votes.append(digit)
                maxv = int(cfg.get("digit_votes", 20))
                if len(self.votes) > maxv:
                    self.votes = self.votes[-maxv:]
        # 输出 = 窗口众数(不锁定, 随新读数滚动)
        digit, dscore = self._window_mode(int(cfg.get("digit_votes", 20)))
        return {
            "found": True,
            "cx_px": self.sx, "cy_px": self.sy,
            "r_outer_px": self.sr,
            "n_rings": 1, "conf": conf, "stale": self.stale,
            "digit": digit, "digit_score": dscore,
            "raw": (cx, cy),
        }


def get_lan_ip():
    """取局域网IP(默认路由接口); 失败回退 192.168.3.30。"""
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
    return "192.168.3.30"


# ----------------------------------------------------------------------
# HTTP MJPEG 推流
# ----------------------------------------------------------------------
def start_stream(port=8080):
    import http.server

    class State:
        frame = None

    state = State()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/stream'):
                self.send_response(200)
                self.send_header('Content-Type',
                                 'multipart/x-mixed-replace; boundary=frame')
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


# ----------------------------------------------------------------------
# 演示入口
# ----------------------------------------------------------------------
def main():
    cfg = dict(CALIB)
    argv = sys.argv[1:]
    stream_port = None
    if '--stream' in argv:
        i = argv.index('--stream')
        if i + 1 < len(argv) and argv[i + 1].isdigit():
            stream_port = int(argv[i + 1])
            argv = argv[:i] + argv[i + 2:]
        else:
            stream_port = 8080
            argv = argv[:i] + argv[i + 1:]
    img_arg = next((a for a in argv if not a.startswith('--')), None)
    use_image = img_arg is not None
    show = '--no-show' not in argv
    if show and sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):
        show = False
        print('[auto] SSH无显示, 终端输出模式; 想看画面加 --stream')
    if use_image:
        cfg["center_gate_r"] = 0   # 离线单图: 环可在画面任意位置, 关闭位置门

    det = RingDetector(cfg)
    cap = None
    if use_image:
        frame = cv.imread(img_arg)
        if frame is None:
            print("cannot read image:", img_arg)
            return
        frame = rotate_ccw(frame, cfg)
    else:
        backend = cv.CAP_V4L2 if sys.platform.startswith('linux') else cv.CAP_DSHOW
        cap = cv.VideoCapture(cfg["camera_id"], backend)
        if not cap.isOpened():
            print("Error: Cannot open camera")
            return
        cap.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv.CAP_PROP_FRAME_WIDTH, cfg["width"])
        cap.set(cv.CAP_PROP_FRAME_HEIGHT, cfg["height"])
        cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
        if cfg["lock_exposure"]:
            cap.set(cv.CAP_PROP_AUTO_EXPOSURE, 0.25)
            cap.set(cv.CAP_PROP_EXPOSURE, 156)
            cap.set(cv.CAP_PROP_AUTO_WB, 0)
            cap.set(cv.CAP_PROP_WB_TEMPERATURE, 5000)

    state = start_stream(stream_port) if stream_port else None
    if state:
        print(f"[stream] 浏览器打开 http://{get_lan_ip()}:{stream_port}/ 查看实时画面")

    fps_t0, fps_n, last_fps = time.time(), 0, 0.0
    while True:
        if cap is not None:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            frame = rotate_ccw(frame, cfg)
        else:
            time.sleep(0.03)

        res = det.process(frame)

        fps_n += 1
        if time.time() - fps_t0 >= 1.0:
            last_fps = fps_n / (time.time() - fps_t0)
            if res["found"]:
                msg = (f"[perf] fps={last_fps:.1f} center=({res['cx_px']:.0f},"
                       f"{res['cy_px']:.0f}) r={res['r_outer_px']:.0f} "
                       f"digit={res['digit']}({res['digit_score']:.2f})")
            else:
                msg = f"[perf] fps={last_fps:.1f} not found"
            print(msg)
            fps_n, fps_t0 = 0, time.time()

        disp = None
        if show or state is not None:
            disp = frame.copy()
            if res["found"]:
                px, py = int(res["cx_px"]), int(res["cy_px"])
                cv.line(disp, (px - 12, py), (px + 12, py), (0, 0, 255), 2)
                cv.line(disp, (px, py - 12), (px, py + 12), (0, 0, 255), 2)
                cv.circle(disp, (px, py), int(res["r_outer_px"]), (0, 255, 0), 2)
                # 环心数字: 黄色、圆心居中、字号随环大小(纯ASCII Hershey字体)
                if res["digit"]:
                    dch = res["digit"]
                    dscale = max(0.6, min(6.0, res["r_outer_px"] / 45.0))
                    dthick = max(2, min(8, int(dscale)))
                    (tw, th), _ = cv.getTextSize(dch, cv.FONT_HERSHEY_SIMPLEX,
                                                 dscale, dthick)
                    cv.putText(disp, dch, (px - tw // 2, py + th // 2),
                               cv.FONT_HERSHEY_SIMPLEX, dscale, (0, 255, 255),
                               dthick, cv.LINE_AA)
                txt = (f"C=({px},{py}) digit={res['digit']} "
                       f"score={res['digit_score']:.2f}")
            else:
                txt = "Center: not found"
            cv.putText(disp, txt, (10, 20), cv.FONT_HERSHEY_SIMPLEX,
                       0.55, (0, 255, 255), 1)
            cv.putText(disp, f"fps={last_fps:.1f}", (10, 40),
                       cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
            if state is not None:
                ok, jpg = cv.imencode('.jpg', disp, [cv.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    state.frame = jpg.tobytes()

        if use_image and not show:
            # 单张图片调试 + 无显示(SSH): 打印结果后退出, 不要无限循环
            if res["found"]:
                print(f"[result] found=True center=({res['cx_px']:.0f},"
                      f"{res['cy_px']:.0f}) r={res['r_outer_px']:.0f} "
                      f"digit={res['digit']}({res['digit_score']:.2f})")
            else:
                print("[result] found=False")
            break
        if not show:
            continue

        cv.imshow("Ring center + digit", disp)
        key = cv.waitKey(1) & 0xFF
        if key in (27, ord('q')):
            break

    if cap is not None:
        cap.release()
    cv.destroyAllWindows()


if __name__ == "__main__":
    main()
