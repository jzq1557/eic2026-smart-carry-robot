#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同心圆靶标圆心检测 v2 —— 面向工创赛智能搬运"圆环对位"
=====================================================
在原 demo 基础上保留: CLAHE 增强 / Canny 边缘 / 形态学闭合 / 霍夫圆 / 卡尔曼
新增/修正:
  1. 半径先验校验  —— 下视相机高度固定 -> 6 个圆环半径(px)已知, ±tol 过滤误检
     (表3: φ1=φ+3, φ2=φ1+5, φ3=φ2+7, φ4=φ3+10, φ5=φ4+10, φ6=φ5+10, 线宽1.5mm)
  2. 圆心聚类 + 环组选择 —— 画面同时出现多个环组(1/2/3号位)时不再把圆心
     平均到两组之间; 可用 expected_px(里程计粗位置) 辅助选组
  3. 亚像素精化 —— 对命中的最外环边缘点做最小二乘(Kasa)圆拟合
     (1环15分/2环10分…, 圆心偏 2mm 就降档, 这一步很值)
  4. 卡尔曼改进 —— 目标跳变自动重置(换环位); 丢失冻结 + stale 计数 + 置信度
  5. MJPG 编码 + 曝光/白平衡锁定; ROI 加速; FPS 显示
  6. 环心数字的干扰由半径下限自动排除(数字半径 << 最小环半径)

用法:
  python ring_center_detect_v2.py               # 摄像头实时(树莓派桌面/接显示器)
  python ring_center_detect_v2.py --no-show     # SSH 无显示环境: 纯计算+终端perf
  python ring_center_detect_v2.py --stream      # SSH 环境: 浏览器看实时画面
                                                 #   http://<树莓派IP>:8080/
  python ring_center_detect_v2.py 图片.jpg       # 单张图片调试(离线调参)
  python ring_center_detect_v2.py --calibrate    # 自动标定: 对准靶标保持静止3秒,
                                                 #   自动算出 px_per_mm 和物料直径φ 并写回配置
无显示环境(SSH)下不加参数会自动降级为 --no-show 模式, 不会崩溃。
按键(仅显示模式): ESC/q 退出, r 重置滤波器, e 切换边缘图显示

集成到视觉服务(替代 vision_skeleton.py 的 RingLocalizer):
  from ring_center_detect_v2 import RingDetector
  det = RingDetector(CALIB)
  res = det.process(frame)   # dict 或 None
"""

import os
import sys
import time
import threading
import numpy as np
import cv2 as cv

cv.setNumThreads(4)   # 树莓派4核并行

# ----------------------------------------------------------------------
# 标定参数 —— 全部现场标定, 不要照抄
# ----------------------------------------------------------------------
CALIB = {
    "camera_id": 0,
    "width": 1280,                  # LRCP G720P 原生720p; 老相机不接受就改回 640/480
    "height": 720,
    "px_per_mm": 2.5,               # TODO: 下视相机比例尺(相机高度固定后拍直尺标定); 分辨率变了要重标
    "material_dia_mm": 50.0,        # TODO: 现场实测官方物料最大直径 φ(mm)
    "radius_tol": 0.10,             # 半径匹配容差 ±10%(环间距小, 太大容易串环)
    "param2": 40,                   # 霍夫圆累加阈值: 25太松(候选爆炸卡聚类), 误检多再往上调
    "cluster_eps": 8.0,             # 圆心聚类半径(px): 同一环组的圆距此范围
    "min_rings": 2,                 # 至少命中几个同心环才输出中心(防假圆乱跳; 真靶标应4-6个)
    "roi": None,                    # 静态ROI (x,y,w,h) 加速; None=全图
    "expected_px": None,            # 目标环组粗位置(里程计), 多环组时辅助选组
    "jump_px": 60.0,                # 中心跳变>该值 -> 卡尔曼重置(换环位/换目标)
    "stale_max": 10,                # 连续丢失>该帧数 -> conf 降级
    "lock_exposure": True,          # 锁定曝光/白平衡(颜色任务的前提, 圆心检测也受益)
    # ---------- 性能相关(卡顿时先调这里) ----------
    # 连续跟踪: proc_scale=0.5 + hough_dp=1.5 (Pi实测约13fps);
    # 最终停稳对位(要最高精度): proc_scale=1.0 + hough_dp=1.0 (约1.5-3fps, 一次性拍照)
    "proc_scale": 0.5,              # 检测前缩放: 0.5=缩一半(速度约4倍), 坐标自动换算回原图
    "hough_dp": 1.5,                # 霍夫累加分辨率: 1.0最准, 1.5~2.0更快(精度略降)
    "use_clahe": True,              # 光照锁定后可设 False, 每帧省 ~10ms
    "show": True,                   # SSH/VNC 无显示环境设 False(imshow 在远程显示下极慢)
    "stage_timing": True,           # 打印每级耗时(crop/pre/hough/fit), 定位瓶颈
}


def ring_radii_mm(material_dia):
    """表3圆环外径公式 -> 半径序列(mm): φ1=φ+3 φ2=φ1+5 φ3=φ2+7 φ4=φ3+10 φ5=φ4+10 φ6=φ5+10"""
    d1 = material_dia + 3.0
    d2 = d1 + 5.0
    d3 = d2 + 7.0
    d4 = d3 + 10.0
    d5 = d4 + 10.0
    d6 = d5 + 10.0
    return np.array([d1, d2, d3, d4, d5, d6]) / 2.0


# 环半径(mm) = φ/2 + 该偏移 (表3公式推导, 与φ无关的固定增量)
RING_RADIUS_OFFSETS_MM = np.array([1.5, 4.0, 7.5, 12.5, 17.5, 22.5])


# ----------------------------------------------------------------------
# 预处理（沿用原 demo, 微调）
# ----------------------------------------------------------------------
def preprocess(frame, use_clahe=True):
    gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
    if use_clahe:
        clahe = cv.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
    gray = cv.GaussianBlur(gray, (5, 5), 1.5)
    edges = cv.Canny(gray, 30, 90, apertureSize=3)
    kernel = cv.getStructuringElement(cv.MORPH_ELLIPSE, (3, 3))
    edges = cv.morphologyEx(edges, cv.MORPH_CLOSE, kernel, iterations=1)
    return edges


# ----------------------------------------------------------------------
# 候选圆检测（带半径先验）
# ----------------------------------------------------------------------
def detect_candidates(edges, r_min, r_max, param2, dp=1.2, max_cands=400):
    circles = cv.HoughCircles(edges, cv.HOUGH_GRADIENT, dp=dp, minDist=8,
                              param1=30, param2=param2,
                              minRadius=int(r_min), maxRadius=int(r_max))
    if circles is not None and len(circles[0]) > max_cands:
        # 候选爆炸(噪声场景): 自动提高累加阈值重检, 防止下游聚类变慢
        circles = cv.HoughCircles(edges, cv.HOUGH_GRADIENT, dp=dp, minDist=8,
                                  param1=30, param2=param2 * 2,
                                  minRadius=int(r_min), maxRadius=int(r_max))
    if circles is None:
        return []
    return [(float(c[0]), float(c[1]), float(c[2])) for c in circles[0]]


def cluster_by_center(cands, eps):
    """按圆心距离聚类(并查集 + 网格桶, O(n) 期望)。
    cands: [(x, y, r), ...]; 返回成员列表的列表。"""
    n = len(cands)
    if n == 0:
        return []
    if n == 1:
        return [list(cands)]
    cell = float(max(eps, 1e-6))
    grid = {}
    for i, c in enumerate(cands):
        k = (int(c[0] // cell), int(c[1] // cell))
        grid.setdefault(k, []).append(i)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    eps2 = eps * eps

    def near(i, j):
        dx = cands[i][0] - cands[j][0]
        dy = cands[i][1] - cands[j][1]
        return dx * dx + dy * dy < eps2

    # 同格: 用"格内代表"避免 O(m^2)
    for ids in grid.values():
        reps = []
        for i in ids:
            done = False
            for r_ in reps:
                if near(i, r_):
                    union(i, r_)
                    done = True
                    break
            if not done:
                reps.append(i)
    # 邻格: 只扫 4 个规范方向, 每对跨格组合恰好处理一次
    for (gx, gy), ids in grid.items():
        for dx, dy in ((1, 0), (0, 1), (1, 1), (-1, 1)):
            others = grid.get((gx + dx, gy + dy))
            if not others:
                continue
            for i in ids:
                for j in others:
                    if near(i, j):
                        union(i, j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(cands[i])
    return list(groups.values())


def match_rings(cluster, expected_r_px, tol):
    """每个检测圆最多匹配一个期望半径(贪心一对一)。
    返回 [(期望半径, 实测半径)] 列表; 防止单圆同时命中相邻两个环虚增环数。"""
    radii = sorted([p[2] for p in cluster])
    used = [False] * len(expected_r_px)
    matched = []
    for rr in radii:
        best_i, best_d = -1, np.inf
        for i, re_ in enumerate(expected_r_px):
            if used[i]:
                continue
            d = abs(rr - re_)
            if d < best_d:
                best_d, best_i = d, i
        if best_i >= 0 and best_d <= tol * expected_r_px[best_i]:
            used[best_i] = True
            matched.append((expected_r_px[best_i], rr))
    return matched


def select_cluster(clusters, expected_r_px, tol, expected_px=None, prev_center=None):
    """按命中环数打分选组; 分数接近时偏向离期望位置近的组。
    prev_center: 上一帧确认的中心(局部缩放坐标) -> 时序一致性加分, 抑制帧间来回跳变。"""
    best, best_score = None, -1e9
    for cl in clusters:
        matched = match_rings(cl, expected_r_px, tol)
        cx = np.mean([p[0] for p in cl])
        cy = np.mean([p[1] for p in cl])
        score = len(matched) * 10.0
        if expected_px is not None:
            score -= np.hypot(cx - expected_px[0], cy - expected_px[1]) / 60.0
        if prev_center is not None:
            d = np.hypot(cx - prev_center[0], cy - prev_center[1])
            score += 2.5 * max(0.0, 1.0 - d / 60.0)   # 60px窗口内最多+2.5, 不足以压过环数差
        if score > best_score:
            best_score, best = score, cl
    return best


def refine_kasa(edges, cx0, cy0, r0, band=0.06, max_pts=3000):
    """Kasa 最小二乘圆拟合: 取半径≈r0 的边缘点, 亚像素精化圆心。
    Kasa: min Σ((x-a)^2+(y-b)^2-R^2)^2 -> 线性化求解。
    注意: 1) 先平移坐标到近似圆心, 避免法方程病态;
          2) 等步长抽采样, 拟合500点即达亚像素精度, 上万点纯属浪费CPU。"""
    ys, xs = np.nonzero(edges)
    d = np.hypot(xs - cx0, ys - cy0)
    sel = np.abs(d - r0) <= band * r0
    idx = np.nonzero(sel)[0]
    if len(idx) < 12:
        return None
    if len(idx) > max_pts:
        idx = idx[::max(1, len(idx) // max_pts)]      # 确定性等步长抽样
    pts = np.column_stack([xs[idx].astype(np.float64) - cx0,
                           ys[idx].astype(np.float64) - cy0])
    A = np.c_[2 * pts[:, 0], 2 * pts[:, 1], np.ones(pts.shape[0])]
    b = pts[:, 0] ** 2 + pts[:, 1] ** 2
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return None
    a, bb, c = sol
    r = float(np.sqrt(max(c + a * a + bb * bb, 0.0)))
    return float(cx0 + a), float(cy0 + bb), r


# ----------------------------------------------------------------------
# 中心滤波器：卡尔曼 + 跳变重置 + 丢失冻结
# ----------------------------------------------------------------------
class CenterFilter:
    """静止靶标专用: 平滑测量抖动; 目标跳变(换环位)自动重置; 丢失冻结+计数。"""

    def __init__(self, jump_px=60.0, stale_max=10):
        self.jump_px = jump_px
        self.stale_max = stale_max
        self.stale = 0
        self.last = None
        self._init_kalman()

    def _init_kalman(self):
        self.kf = cv.KalmanFilter(4, 2)
        self.kf.measurementMatrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], np.float32)
        self.kf.transitionMatrix = np.array([[1, 0, 1, 0], [0, 1, 0, 1],
                                             [0, 0, 1, 0], [0, 0, 0, 1]], np.float32)
        # 静止目标: 过程噪声小 -> 强平滑, 抖动被压掉
        self.kf.processNoiseCov = np.eye(4, dtype=np.float32) * 0.01
        self.kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * 1.0
        self.initialized = False

    def _reset(self, x, y):
        s = np.array([[np.float32(x)], [np.float32(y)], [0.0], [0.0]], np.float32)
        self.kf.statePre = s.copy()
        self.kf.statePost = s.copy()
        self.initialized = True
        self.last = (x, y)

    def update(self, meas):
        """meas: (x,y) 或 None。返回 (x,y) 或 None。"""
        if meas is None:
            self.stale += 1
            return self.last
        self.stale = 0
        x, y = meas
        if self.initialized and self.last is not None:
            if np.hypot(x - self.last[0], y - self.last[1]) > self.jump_px:
                self._reset(x, y)                       # 换环位 -> 重置, 避免追赶暂态
            else:
                self.kf.predict()
                est = self.kf.correct(np.array([[np.float32(x)],
                                                [np.float32(y)]]))
                self.last = (float(est[0, 0]), float(est[1, 0]))
        else:
            self._reset(x, y)
        return self.last

    @property
    def confident(self):
        return self.initialized and self.stale < self.stale_max

    def reset(self):
        self._init_kalman()
        self.stale = 0
        self.last = None


# ----------------------------------------------------------------------
# 检测器（可集成到视觉服务）
# ----------------------------------------------------------------------
class RingDetector:
    def __init__(self, calib):
        self.calib = dict(calib)
        self.expected_r_px = (ring_radii_mm(self.calib["material_dia_mm"])
                              * self.calib["px_per_mm"])
        self.filt = CenterFilter(self.calib["jump_px"], self.calib["stale_max"])
        self.prev_g = None          # 上一帧确认中心(原图全局坐标), 用于簇选择时序一致性

    def process(self, frame):
        """返回 dict {cx_px,cy_px,n_rings,conf,r_outer_px,stale,raw,timing} 或 None。"""
        cal = self.calib
        t0 = time.perf_counter()
        view = frame
        ox = oy = 0
        if cal["roi"]:
            x, y, w, h = cal["roi"]
            view = frame[y:y + h, x:x + w]
            ox, oy = x, y
        if view is None or view.size == 0:
            return None
        # 可选降采样: 速度约 1/scale^2 倍, 坐标最后换算回原图
        scale = float(cal.get("proc_scale", 1.0))
        if scale != 1.0:
            view = cv.resize(view, None, fx=scale, fy=scale,
                             interpolation=cv.INTER_AREA)
        t1 = time.perf_counter()

        edges = preprocess(view, cal.get("use_clahe", True))
        t2 = time.perf_counter()
        r_px = self.expected_r_px * scale
        cands = detect_candidates(edges, r_px[0] * 0.8, r_px[-1] * 1.15,
                                  cal["param2"], cal.get("hough_dp", 1.2))
        t3 = time.perf_counter()
        clusters = cluster_by_center(cands, cal["cluster_eps"])

        # expected_px / 上一帧中心 折算到 ROI 局部 + 缩放坐标
        exp_local = None
        if cal["expected_px"] is not None:
            exp_local = ((cal["expected_px"][0] - ox) * scale,
                         (cal["expected_px"][1] - oy) * scale)
        prev_local = None
        if self.prev_g is not None:
            prev_local = ((self.prev_g[0] - ox) * scale,
                          (self.prev_g[1] - oy) * scale)

        cl = select_cluster(clusters, r_px, cal["radius_tol"], exp_local, prev_local)
        if cl is None:
            self.filt.update(None)
            out = {"found": False, "conf": 0.0, "stale": self.filt.stale}
            now = time.perf_counter()
            out["timing"] = self._timing(t0, t1, t2, t3, now, now)
            return out

        matched = match_rings(cl, r_px, cal["radius_tol"])
        # 证据门槛: 命中同心环数不足 -> 视为未找到(宁缺毋滥, 杜绝假圆乱跳)
        if len(matched) < cal.get("min_rings", 2):
            self.filt.update(None)
            out = {"found": False, "n_rings": len(matched), "conf": 0.0,
                   "stale": self.filt.stale}
            now = time.perf_counter()
            out["timing"] = self._timing(t0, t1, t2, t3, now, now)
            return out

        cx0 = np.mean([p[0] for p in cl])
        cy0 = np.mean([p[1] for p in cl])
        t3b = time.perf_counter()
        # 用命中的最外环做亚像素拟合
        r_fit = max(m[1] for m in matched) if matched else max(p[2] for p in cl)
        kasa = refine_kasa(edges, cx0, cy0, r_fit)
        cx, cy = (kasa[0], kasa[1]) if kasa else (cx0, cy0)
        t4 = time.perf_counter()

        fx, fy = self.filt.update((cx, cy))
        n_rings = len(matched)
        conf = 1.0 if self.filt.confident else max(0.0, 1.0 - self.filt.stale / cal["stale_max"])
        # 缩放回原图像素坐标(ROI 偏移 + proc_scale)
        gx, gy = float(fx / scale + ox), float(fy / scale + oy)
        self.prev_g = (gx, gy)
        out = {
            "found": True,
            "cx_px": gx, "cy_px": gy,
            "n_rings": n_rings,
            "conf": conf,
            "r_outer_px": float(r_fit / scale),
            "stale": self.filt.stale,
            "raw": (float(cx0 / scale + ox), float(cy0 / scale + oy)),
        }
        out["timing"] = self._timing(t0, t1, t2, t3, t3b, t4)
        return out

    @staticmethod
    def _timing(t0, t1, t2, t3, t3b, t4):
        return {"crop_ms": (t1 - t0) * 1000, "pre_ms": (t2 - t1) * 1000,
                "hough_ms": (t3 - t2) * 1000, "cluster_ms": (t3b - t3) * 1000,
                "fit_ms": (t4 - t3b) * 1000}


# ----------------------------------------------------------------------
# 自动标定: 从同心圆半径序列反推 px_per_mm 与物料直径 φ
# ----------------------------------------------------------------------
def fit_ring_scale(radii_px):
    """用一组同心圆半径(px)拟合标定参数。
    环半径(mm) = φ/2 + RING_RADIUS_OFFSETS_MM(表3固定增量)。
    尝试"检测圆 ↔ 6个环位"的各种子集组合, 返回最优
    dict(px_per_mm, phi_mm, rms_rel, n_used) 或 None。"""
    from itertools import combinations
    offsets = RING_RADIUS_OFFSETS_MM
    rp = np.sort(np.asarray(radii_px, dtype=np.float64))
    n = len(rp)
    if n < 3:
        return None
    if n > 12:
        rp = rp[-12:]          # 环通常是簇里最大的圆, 假圆多为小圆(数字/噪声)
        n = 12
    if n > 6:
        idx_sets = combinations(range(n), 6)
        off_sets = [range(6)]
    else:
        idx_sets = [range(n)]
        off_sets = combinations(range(6), n)
    best = None
    for idx in idx_sets:
        rsel = rp[list(idx)]
        for off_idx in off_sets:
            off = offsets[list(off_idx)]
            A = np.c_[np.ones_like(off), off]
            try:
                sol, *_ = np.linalg.lstsq(A, rsel, rcond=None)
            except np.linalg.LinAlgError:
                continue
            a, b = sol
            if b <= 0:
                continue
            pred = a + b * off
            rel = np.abs(rsel - pred) / pred
            rms = float(np.sqrt(np.mean(rel ** 2)))
            if best is None or rms < best['rms_rel']:
                best = {'px_per_mm': float(b), 'phi_mm': float(2 * a / b),
                        'rms_rel': rms, 'n_used': len(off)}
    if best is not None and best['rms_rel'] < 0.05 \
            and best['n_used'] >= 4 \
            and 5.0 <= best['phi_mm'] <= 80.0 \
            and 0.5 <= best['px_per_mm'] <= 20.0:
        return best
    return None


def calibrate_from_frame(frame, param2=40, dp=1.2):
    """全分辨率检测同心圆簇并拟合标定参数, 返回 fits 列表(按 rms 升序)。"""
    edges = preprocess(frame, True)
    h, w = edges.shape[:2]
    cands = detect_candidates(edges, 10, min(h, w) // 2, param2, dp)
    clusters = cluster_by_center(cands, 10.0)
    fits = []
    for cl in clusters:
        if len(cl) < 3:
            continue
        radii = sorted(c[2] for c in cl)
        f = fit_ring_scale(radii)
        if f is not None:
            f['center'] = (float(np.mean([c[0] for c in cl])),
                           float(np.mean([c[1] for c in cl])))
            fits.append(f)
    fits.sort(key=lambda f: f['rms_rel'])
    return fits


def run_calibrate(cal, cap, use_image, frame0, stream_state):
    """采集约3秒, 拟合标定参数并自动写回本文件的 CALIB。"""
    print('[calib] 请把相机对准圆环靶标并保持静止, 采集约3秒...')
    results = []
    for i in range(8):
        if use_image:
            frame = frame0
        else:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
        fits = calibrate_from_frame(frame, cal['param2'])
        best_rms = fits[0]['rms_rel'] if fits else None
        print(f'  frame {i}: 环组候选={len(fits)} '
              f'最佳rms={best_rms*100:.1f}%' if best_rms is not None
              else f'  frame {i}: 未检出同心环组')
        if fits and fits[0]['rms_rel'] < 0.03:
            results.append(fits[0])
        if len(results) >= 5:
            break
        if stream_state is not None:
            disp = frame.copy()
            for f in fits[:3]:
                cx, cy = f['center']
                cv.circle(disp, (int(cx), int(cy)), 5, (0, 255, 255), -1)
                cv.putText(disp, f"rms={f['rms_rel']*100:.1f}% ppmm={f['px_per_mm']:.2f}",
                           (int(cx) + 10, int(cy)), cv.FONT_HERSHEY_SIMPLEX, 0.5,
                           (0, 255, 255), 1)
            okj, jpg = cv.imencode('.jpg', disp, [cv.IMWRITE_JPEG_QUALITY, 85])
            if okj:
                stream_state.frame = jpg.tobytes()
        time.sleep(0.05)
    if len(results) < 3:
        print('[calib] 失败: 未找到同心环组。检查: 1)靶标在画面内且完整 '
              '2)光照足够 3)镜头对焦。可加 --stream 在浏览器里看检测情况')
        return
    ppm = float(np.median([r['px_per_mm'] for r in results]))
    phi = float(np.median([r['phi_mm'] for r in results]))
    rms = float(np.median([r['rms_rel'] for r in results]))
    print(f'[calib] 成功: px_per_mm={ppm:.3f}  物料直径φ={phi:.1f}mm  '
          f'rms={rms*100:.2f}%  (有效帧{len(results)}, 匹配环数≥4)')
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, 'ring_center_detect_v2.py')
    try:
        src = open(path, encoding='utf-8').read()
        src2 = re.sub(r'("px_per_mm":\s*)[\d.]+',
                      lambda m: m.group(1) + f'{ppm:.3f}', src, count=1)
        src2 = re.sub(r'("material_dia_mm":\s*)[\d.]+',
                      lambda m: m.group(1) + f'{phi:.1f}', src2, count=1)
        if src2 != src:
            open(path + '.bak', 'w', encoding='utf-8').write(src)
            open(path, 'w', encoding='utf-8').write(src2)
            print('[calib] 已自动更新 CALIB(旧配置备份 .bak), 重启程序生效')
    except OSError as e:
        print(f'[calib] 写文件失败({e}), 请手动把上面两个值填进 CALIB')


# ----------------------------------------------------------------------
# HTTP MJPEG 推流(无显示环境下用浏览器看实时画面)
# ----------------------------------------------------------------------
def start_stream(port=8080):
    """启动 MJPEG 推流服务, 返回 state 对象; 主循环往 state.frame 写 JPEG 即可。"""
    import http.server

    class State:
        frame = None          # 最新JPEG bytes

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
    cal = dict(CALIB)
    if '--no-show' in sys.argv:
        cal['show'] = False              # SSH/无显示环境
    # 解析 --stream [端口]
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
    det = RingDetector(cal)
    filt = det.filt

    img_arg = next((a for a in argv if not a.startswith('--')), None)
    use_image = img_arg is not None
    cap = None
    if use_image:
        frame0 = cv.imread(img_arg)
        if frame0 is None:
            print("cannot read image:", img_arg)
            return
        frame = frame0
    else:
        # 平台后端: 树莓派=V4L2; Windows=DSHOW(MSMF 在 HP 等 UVC 相机上 0.4fps 是已知问题)
        backend = cv.CAP_V4L2 if sys.platform.startswith('linux') else cv.CAP_DSHOW
        cap = cv.VideoCapture(cal["camera_id"], backend)
        if not cap.isOpened():
            print("Error: Cannot open camera")
            return
        # 先设格式再设分辨率(部分相机顺序敏感); 设完要验证是否被接受
        cap.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv.CAP_PROP_FRAME_WIDTH, cal["width"])
        cap.set(cv.CAP_PROP_FRAME_HEIGHT, cal["height"])
        cap.set(cv.CAP_PROP_BUFFERSIZE, 1)          # 只留最新帧, 不排队
        fcc = int(cap.get(cv.CAP_PROP_FOURCC))
        fcc_s = ''.join(chr((fcc >> (8 * i)) & 0xFF) for i in range(4))
        print(f"[cam] 实际模式 {cap.get(cv.CAP_PROP_FRAME_WIDTH):.0f}x"
              f"{cap.get(cv.CAP_PROP_FRAME_HEIGHT):.0f} {fcc_s}")
        if fcc_s != "MJPG" or cap.get(cv.CAP_PROP_FRAME_WIDTH) > cal["width"] + 8:
            print("  !! 相机未接受设置(可能按1080p/YUYV工作=卡顿主因), "
                  "树莓派上先跑 scripts/cam_diag.py")
        if cal["lock_exposure"]:
            cap.set(cv.CAP_PROP_AUTO_EXPOSURE, 0.25)   # 手动曝光
            cap.set(cv.CAP_PROP_EXPOSURE, 156)          # TODO: 现场定(单位100us, 156≈1/64s)
            cap.set(cv.CAP_PROP_AUTO_WB, 0)
            cap.set(cv.CAP_PROP_WB_TEMPERATURE, 5000)

    print("Ring center detection v2. ESC/q=quit, r=reset filter, e=edges")
    show_edges = False
    fps_t0 = time.time()
    fps_n = 0
    last_fps = 0.0
    show = cal.get("show", True)
    # 自动降级: Linux 无 DISPLAY(SSH) 且未开启推流 -> 不碰 imshow, 避免 Qt xcb 崩溃
    if show and sys.platform.startswith('linux') \
            and not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
        show = False
        print("[auto] 检测到无显示环境(SSH), 自动降级为 no-show 模式; "
              "想看实时画面请加 --stream 参数")
    stream_state = None
    if stream_port is not None:
        stream_state = start_stream(stream_port)
        import socket
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = '<树莓派IP>'
        print(f"[stream] 浏览器打开 http://{ip}:{stream_port}/ 查看实时画面")

    # 自动标定模式
    if '--calibrate' in sys.argv:
        run_calibrate(cal, cap, use_image, frame if use_image else None, stream_state)
        if cap is not None:
            cap.release()
        return

    while True:
        t_read0 = time.perf_counter()
        if not use_image:
            ok, frame = cap.read()
            if not ok:
                continue
        elif fps_n > 0:
            time.sleep(0.03)
        t_read = time.perf_counter() - t_read0

        res = det.process(frame)

        # ---- 控制台性能输出(SSH 下也能看) ----
        fps_n += 1
        if time.time() - fps_t0 >= 1.0:
            fps = fps_n / (time.time() - fps_t0)
            last_fps = fps
            msg = f"[perf] fps={fps:.1f} read={t_read*1000:.0f}ms"
            if res and "timing" in res:
                ti = res["timing"]
                msg += (f" crop={ti['crop_ms']:.0f} pre={ti['pre_ms']:.0f} "
                        f"hough={ti['hough_ms']:.0f} cluster={ti['cluster_ms']:.0f} "
                        f"fit={ti['fit_ms']:.0f} ms")
            print(msg)
            fps_n, fps_t0 = 0, time.time()

        # ---- 标注画面(显示或推流共用) ----
        disp = None
        if show or stream_state is not None:
            disp = frame.copy()
            if cal["roi"]:
                x, y, w, h = cal["roi"]
                cv.rectangle(disp, (x, y), (x + w, y + h), (255, 255, 0), 1)
            if res and res["found"]:
                px, py = int(res["cx_px"]), int(res["cy_px"])
                col = (0, 255, 0) if res["conf"] >= 0.8 else (0, 165, 255)
                cv.line(disp, (px - 12, py), (px + 12, py), (0, 0, 255), 2)
                cv.line(disp, (px, py - 12), (px, py + 12), (0, 0, 255), 2)
                cv.circle(disp, (px, py), int(res["r_outer_px"]), col, 1)
                txt = f"C=({px},{py}) rings={res['n_rings']}/6 conf={res['conf']:.2f}"
                if res["stale"] > 0:
                    txt += f" stale={res['stale']}"
            else:
                txt = "Center: not found"
            cv.putText(disp, txt, (10, 20), cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
            cv.putText(disp, f"fps={last_fps:.1f}", (10, 40),
                       cv.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
            if stream_state is not None:
                ok, jpg = cv.imencode('.jpg', disp, [cv.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    stream_state.frame = jpg.tobytes()

        if not show:
            continue                    # 无显示环境: 不碰 imshow

        cv.imshow("Ring Center v2", disp)
        if show_edges:
            e = preprocess(frame[cal["roi"][1]:cal["roi"][1] + cal["roi"][3],
                               cal["roi"][0]:cal["roi"][0] + cal["roi"][2]]
                            if cal["roi"] else frame)
            cv.imshow("Edges", e)
        else:
            cv.destroyWindow("Edges") if cv.getWindowProperty("Edges", 0) >= 0 else None

        key = cv.waitKey(1) & 0xFF
        if key in (27, ord('q')):
            break
        if key == ord('r'):
            filt.reset()
            print("filter reset")
        if key == ord('e'):
            show_edges = not show_edges

    if cap is not None:
        cap.release()
    cv.destroyAllWindows()


if __name__ == "__main__":
    main()
