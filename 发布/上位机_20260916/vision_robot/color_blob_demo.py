#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
color_blob_demo.py —— 转盘物料颜色识别 demo(手动/自动双标定 UI)

流程(方案一 + 工程化补强):
  1) 采图(相机 / 单图 / 合成场景) → 可选灰世界或白点归一
  2) 色相增幅/减幅:按"到目标色相的距离"给饱和度做增益(近目标放大、远目标压制)
  3) 阈值二值化:圆环色相距离(H 环绕)+ S/V 门限(黑色物料走"无彩色"分支)
  4) 形态学:开(去噪) → 闭(补洞) → 可选腐蚀/膨胀
  5) 圆形目标提取:轮廓 + 面积 + 圆度过滤 → 最小外接圆 + 方框标注

标定:
  · 手动:一堆 trackbar(H 半宽/S/V/增幅/核大小/面积/圆度…),所见即所得
  · 自动:把物料放到 ROI(画面准星/鼠标点选),按 a 或按 1..6 逐一标定
          → 用 ROI 内像素的分位数+圆均值直接算出 H/S/V 阈值(瞬时,<1s/色)
  · 键 s 保存 / l 读取 JSON 档;现场 6 色标定可在 10s 内完成

用法(树莓派,VNC 里操作):
  python3 color_blob_demo.py --cam 1                 # 相机实时 + UI
  python3 color_blob_demo.py --image pic.jpg --no-show   # 单图命令行输出
  python3 color_blob_demo.py --synthetic 3 --no-show     # 合成场景自测
  python3 color_blob_demo.py --selftest                  # 六色自测(无相机/无UI)

按键: a=按当前色自动标定 1..6=把ROI样本标定为该色 q/ESC=退出
      m=手动/自动切换 s=保存档案 l=读取档案 n/p=上/下一个颜色
      b=灰世界白平衡开关 v=高光剔除开关 space=冻结画面 c=抓拍
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

# ----------------------------------------------------------------------
# 颜色档案(OpenCV: H 0..179, S/V 0..255)
#  mode: "chroma" 用色相距离 + S/V; "dark" 用于黑色(无彩色,看 V 低)
# ----------------------------------------------------------------------
PRESETS = {
    "red":       dict(mode="chroma", h=0,   hw=8,  s_min=90, s_max=255, v_min=60, v_max=255),
    "yellow":    dict(mode="chroma", h=28,  hw=10, s_min=90, s_max=255, v_min=90, v_max=255),
    "green":     dict(mode="chroma", h=60,  hw=12, s_min=70, s_max=255, v_min=50, v_max=255),
    "blue":      dict(mode="chroma", h=115, hw=12, s_min=110, s_max=255, v_min=50, v_max=255),
    "lightblue": dict(mode="chroma", h=100, hw=12, s_min=40, s_max=140, v_min=120, v_max=255),
    "black":     dict(mode="dark",   h=0,   hw=180, s_min=0, s_max=120, v_min=0, v_max=85),
    "custom":    dict(mode="chroma", h=90,  hw=12, s_min=60, s_max=255, v_min=50, v_max=255),
}
COLOR_NAMES = list(PRESETS.keys())


def hsv_dist(h_img, center):
    """H(0..179)环绕距离:|H-c| 与 180-|H-c| 取小。"""
    d = np.abs(h_img.astype(np.int16) - int(center))
    return np.minimum(d, 180 - d).astype(np.uint8)


class ColorBlobDetector:
    def __init__(self, cfg=None):
        self.cfg = dict(cfg or PRESETS["red"])
        # 可视化/调参项(不属于颜色档案本体)
        self.boost = 1.0          # 目标色相饱和度放大倍数
        self.suppress = 1.0       # 非目标色相饱和度压制倍数
        self.boost_band = 30      # 增幅作用的色相半宽
        self.k_open = 3
        self.blur_k = 5          # ★阈值前降噪(暗色物料 S 极不稳定,必须先平滑)
        self.k_close = 9
        self.erode_iter = 0
        self.dilate_iter = 1
        self.min_area_frac = 0.0008
        self.max_area_frac = 0.10
        self.circ_min = 0.55
        self.use_wb = False       # 灰世界
        self.drop_glare = True    # 高光剔除(阈值内不计入统计/掩膜)

    # ---------------- 预处理 ----------------
    @staticmethod
    def gray_world(bgr):
        b, g, r = cv2.split(bgr.astype(np.float32))
        m = (b.mean() + g.mean() + r.mean()) / 3.0 + 1e-6
        b *= m / (b.mean() + 1e-6)
        g *= m / (g.mean() + 1e-6)
        r *= m / (r.mean() + 1e-6)
        return cv2.merge([np.clip(b, 0, 255), np.clip(g, 0, 255),
                          np.clip(r, 0, 255)]).astype(np.uint8)

    def emphasize(self, hsv):
        """色相增幅/减幅:对 S 通道做与目标色相距离相关的加权。"""
        h, s, v = cv2.split(hsv)
        cfg = self.cfg
        if cfg["mode"] == "dark":
            return hsv, None
        d = hsv_dist(h, cfg["h"]).astype(np.float32)
        w = np.full_like(d, self.suppress)
        near = d <= self.boost_band
        # 近目标:线性插值到 boost;远处:suppress
        t = 1.0 - (d / float(max(1, self.boost_band)))
        t = np.clip(t, 0.0, 1.0)
        w = np.where(near, self.suppress + (self.boost - self.suppress) * t, w)
        s2 = np.clip(s.astype(np.float32) * w, 0, 255).astype(np.uint8)
        d_u8 = np.clip(d, 0, 255).astype(np.uint8)
        return cv2.merge([h, s2, v]), d_u8

    def build_mask(self, hsv, dist_map=None):
        cfg = self.cfg
        h, s, v = cv2.split(hsv)
        if cfg["mode"] == "dark":
            m = cv2.inRange(v, int(cfg["v_min"]), int(cfg["v_max"]))
            m2 = cv2.inRange(s, int(cfg["s_min"]), int(cfg["s_max"]))
            mask = cv2.bitwise_and(m, m2)
        else:
            d = dist_map if dist_map is not None else hsv_dist(h, cfg["h"])
            mask = cv2.inRange(d, 0, int(cfg["hw"]))
            mask = cv2.bitwise_and(mask, cv2.inRange(s, int(cfg["s_min"]), int(cfg["s_max"])))
            mask = cv2.bitwise_and(mask, cv2.inRange(v, int(cfg["v_min"]), int(cfg["v_max"])))
        if self.drop_glare:      # 高亮+低饱和 = 镜面反光,剔除
            glare = cv2.bitwise_and(cv2.inRange(v, 246, 255), cv2.inRange(s, 0, 40))
            mask = cv2.bitwise_and(mask, cv2.bitwise_not(glare))
        return mask

    def morph(self, mask):
        k1 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(1, self.k_open | 1),) * 2)
        k2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(1, self.k_close | 1),) * 2)
        m = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k1)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k2)
        if self.erode_iter:
            m = cv2.erode(m, k1, iterations=self.erode_iter)
        if self.dilate_iter:
            m = cv2.dilate(m, k1, iterations=self.dilate_iter)
        return m

    # ---------------- 主检测 ----------------
    def detect(self, bgr, color_name=None):
        if color_name:
            self.cfg = dict(PRESETS[color_name])
        img = self.gray_world(bgr) if self.use_wb else bgr
        if self.blur_k and self.blur_k >= 3:
            k = self.blur_k | 1
            img = cv2.GaussianBlur(img, (k, k), 0)      # ★降噪:抑制暗色 S 抖动
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        emph, dist_map = self.emphasize(hsv)
        mask = self.build_mask(emph, dist_map)
        mask = self.morph(mask)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        h, w = mask.shape[:2]
        amin = self.min_area_frac * w * h
        amax = self.max_area_frac * w * h
        best = None
        for c in cnts:
            a = cv2.contourArea(c)
            if a < amin or a > amax:
                continue
            p = cv2.arcLength(c, True) + 1e-6
            circ = 4.0 * np.pi * a / (p * p)
            if circ < self.circ_min:
                continue
            (cx, cy), r = cv2.minEnclosingCircle(c)
            score = a * circ
            if best is None or score > best["score"]:
                best = dict(cx=float(cx), cy=float(cy), r=float(r), area=float(a),
                            circ=float(circ), score=float(score))
        if best is None:
            return dict(found=False, mask=mask, emph=emph, dist=dist_map, info=None)
        # blob 内均值(核内像素)供核对
        core = np.zeros_like(mask)
        cv2.circle(core, (int(best["cx"]), int(best["cy"])), max(2, int(best["r"] * 0.5)), 255, -1)
        sel = cv2.bitwise_and(hsv, hsv, mask=core)
        idx = core > 0
        mean_hsv = [float(x) for x in cv2.mean(hsv, mask=core)[:3]] if idx.any() else [0, 0, 0]
        best.update(mean_hsv=mean_hsv,
                    fill=float(best["area"] / (np.pi * best["r"] ** 2 + 1e-6)))
        return dict(found=True, mask=mask, emph=emph, dist=dist_map, info=best)

    # ---------------- 自动标定 ----------------
    def auto_calibrate(self, bgr, cx, cy, r=None, k_sigma=2.5, margin=(20, 20, 25)):
        """用 ROI 内像素统计自动生成 H/S/V 阈值(瞬时)。返回 (cfg, 样本数)。"""
        r = int(r or max(12, min(bgr.shape[:2]) // 24))
        img = self.gray_world(bgr) if self.use_wb else bgr
        if self.blur_k and self.blur_k >= 3:
            k = self.blur_k | 1
            img = cv2.GaussianBlur(img, (k, k), 0)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        roi = np.zeros(hsv.shape[:2], np.uint8)
        cv2.circle(roi, (int(cx), int(cy)), r, 255, -1)
        h, s, v = cv2.split(hsv)
        sel = roi > 0
        # 高光像素不参与统计
        if self.drop_glare:
            glare = (v >= 246) & (s <= 40)
            sel = sel & (~glare)
        if sel.sum() < 20:
            return None, 0
        hs = h[sel].astype(np.float32)
        ss = s[sel].astype(np.float32)
        vs = v[sel].astype(np.float32)
        v_med = float(np.median(vs))
        s_med = float(np.median(ss))
        cfg = dict(self.cfg)
        # 暗/无彩色判定:画面中位亮度低或饱和度极低 → 按"黑色物料"处理
        if v_med < 95 and s_med < 70:
            cfg["mode"] = "dark"
            cfg["s_max"] = int(min(255, np.percentile(ss, 95) + 40))   # 暗色放宽 S
            cfg["s_min"] = 0
            cfg["v_max"] = int(min(255, np.percentile(vs, 95) + 30))
            cfg["v_min"] = 0
        else:
            cfg["mode"] = "chroma"
            ang = hs * (np.pi / 90.0)              # H(0..179) → 角度(0..2π)
            ch, sh = np.cos(ang).mean(), np.sin(ang).mean()
            mean_h = (np.arctan2(sh, ch) * 90.0 / np.pi) % 180.0
            R = float(np.hypot(ch, sh)) + 1e-6
            circ_std = float(np.sqrt(max(0.0, -2.0 * np.log(R)))) * 90.0 / np.pi
            cfg["h"] = int(round(mean_h)) % 180
            cfg["hw"] = int(min(60, max(6, round(k_sigma * circ_std + 3))))
            cfg["s_min"] = int(max(0, np.percentile(ss, 10) - margin[0]))
            cfg["s_max"] = int(min(255, np.percentile(ss, 95) + margin[0]))
            cfg["v_min"] = int(max(0, np.percentile(vs, 5) - margin[2]))
            cfg["v_max"] = int(min(255, np.percentile(vs, 99) + margin[2]))
        self.cfg = cfg
        return cfg, int(sel.sum())

    # ---------------- 绘制 ----------------
    def draw(self, view, res, label=""):
        if res["info"]:
            i = res["info"]
            cv2.circle(view, (int(i["cx"]), int(i["cy"])), int(i["r"]), (0, 255, 255), 2)
            x0, y0 = int(i["cx"] - i["r"]), int(i["cy"] - i["r"])
            cv2.rectangle(view, (x0, y0), (int(i["cx"] + i["r"]), int(i["cy"] + i["r"])),
                          (255, 200, 0), 2)
            txt = "%s r=%.0f circ=%.2f fill=%.2f" % (label, i["r"], i["circ"], i["fill"])
        else:
            txt = "%s: not found" % label
        cv2.putText(view, txt, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cfg = self.cfg
        cv2.putText(view, "H=%d+-%d S=%d..%d V=%d..%d %s" %
                    (cfg["h"], cfg["hw"], cfg["s_min"], cfg["s_max"],
                     cfg["v_min"], cfg["v_max"], cfg["mode"]),
                    (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
        return view


# ----------------------------------------------------------------------
# 合成场景(无相机时自测/演示用):灰底 + 转盘 + 若干锥形物料 + 高光 + 噪声
# ----------------------------------------------------------------------
def synthetic_scene(colors, size=(1280, 720), seed=0, glare=True, cast=0.0, noise=6.0):
    """下视视角合成场景:圆锥物料在画面里是"圆斑"(垂直投影),而不是侧影三角。"""
    rng = np.random.default_rng(seed)
    w, h = size
    img = np.full((h, w, 3), 205, np.uint8)
    cv2.circle(img, (w // 2, h // 2), int(min(w, h) * 0.38), (150, 150, 150), -1)  # 转盘
    cv2.circle(img, (w // 2, h // 2), int(min(w, h) * 0.38), (110, 110, 110), 3)
    ref_hsv = {"red": (0, 220, 210), "yellow": (28, 220, 220), "green": (60, 200, 160),
               "blue": (115, 220, 180), "lightblue": (100, 90, 235), "black": (0, 40, 45)}
    n = len(colors)
    for i, c in enumerate(colors):
        ang = 2 * np.pi * i / max(1, n) + 0.4
        cx = int(w / 2 + 0.24 * min(w, h) * np.cos(ang))
        cy = int(h / 2 + 0.24 * min(w, h) * np.sin(ang))
        hh, ss, vv = ref_hsv[c]
        bgr = cv2.cvtColor(np.uint8([[[hh, ss, vv]]]), cv2.COLOR_HSV2BGR)[0, 0]
        col = tuple(int(x) for x in bgr)
        R = int(min(w, h) * 0.075)
        # 顶视圆斑 + 轻微明暗(模拟圆锥顶面/侧面的光照差)
        cv2.circle(img, (cx, cy), R, col, -1)
        rim = tuple(int(x * 0.75) for x in col)
        cv2.circle(img, (cx, cy), R, rim, 2)
        cv2.ellipse(img, (cx - R // 4, cy - R // 4), (R // 3, R // 5),
                    -30, 0, 360, tuple(min(255, int(x * 1.35)) for x in col), -1)
    if glare:
        for _ in range(2):
            gx = int(rng.uniform(0.25, 0.75) * w)
            gy = int(rng.uniform(0.25, 0.75) * h)
            cv2.ellipse(img, (gx, gy), (40, 18), int(rng.uniform(0, 180)), 0, 360,
                        (250, 250, 250), -1)
    if cast:
        img = np.clip(img.astype(np.float32) * np.array([1.0 - cast, 1.0, 1.0 + cast]),
                      0, 255).astype(np.uint8)
    if noise:
        img = np.clip(img.astype(np.float32) +
                      rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    return img


# ----------------------------------------------------------------------
# 自测:六色各构造若干场景,统计检出/颜色正确率,并存图
# ----------------------------------------------------------------------
def selftest(out_dir, n_each=4, verbose=True):
    os.makedirs(out_dir, exist_ok=True)
    others = [c for c in ["red", "yellow", "green", "blue", "lightblue", "black"]]
    ok = {c: 0 for c in others}
    tot = {c: 0 for c in others}
    for c in others:
        for k in range(n_each):
            companions = [x for x in others if x != c]
            rng = np.random.default_rng(hash((c, k)) % 99991)
            pick = list(rng.choice(companions, size=2, replace=False)) + [c]
            rng.shuffle(pick)
            img = synthetic_scene(pick, seed=int(rng.integers(0, 99999)),
                                  cast=float(rng.uniform(0, 0.06)))
            det = ColorBlobDetector()
            res = det.detect(img, c)
            tot[c] += 1
            if res["found"]:
                ok[c] += 1
                det.draw(img, res, c)
            cv2.imwrite(os.path.join(out_dir, "st_%s_%d.jpg" % (c, k)), img)
    if verbose:
        print("--- 合成场景检出率(仅验证管线,不代表现场) ---")
        for c in others:
            print("  %-10s %d/%d" % (c, ok[c], tot[c]))
    bad = [c for c in others if ok[c] == 0]
    return bad


def auto_calib_selftest(out_dir, verbose=True):
    """自动标定鲁棒性:用干净样本标定 → 在加扰场景上评估。"""
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for c in ["red", "yellow", "green", "blue", "lightblue", "black"]:
        det = ColorBlobDetector()
        clean = synthetic_scene([c], seed=7, glare=False, cast=0.0, noise=1.0)
        # 已知合成位置(单色时在转盘上方第一个位置)
        h, w = clean.shape[:2]
        cx, cy = int(w / 2 + 0.24 * min(w, h) * np.cos(0.4)), int(h / 2 + 0.24 * min(w, h) * np.sin(0.4))
        cfg, npx = det.auto_calibrate(clean, cx, cy)
        hit = 0
        for k in range(4):
            rng = np.random.default_rng(100 + k)
            comp = list(rng.choice([x for x in ["red", "yellow", "green", "blue",
                                                "lightblue", "black"] if x != c],
                                   size=2, replace=False)) + [c]
            rng.shuffle(comp)
            img = synthetic_scene(comp, seed=int(rng.integers(0, 99999)),
                                  cast=float(rng.uniform(0, 0.08)))
            res = det.detect(img)
            if res["found"]:
                hit += 1
            det.draw(img, res, c + "(auto)")
            cv2.imwrite(os.path.join(out_dir, "ac_%s_%d.jpg" % (c, k)), img)
        rows.append((c, npx, json.dumps(cfg, ensure_ascii=False), hit))
    if verbose:
        print("--- 自动标定后检出(0.08 色偏 + 高光 + 噪声) ---")
        for c, npx, cfg, hit in rows:
            print("  %-10s px=%4d %d/4  %s" % (c, npx, hit, cfg))
    return rows


# ----------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------
def make_trackbars(win, det):
    def noop(_):
        pass
    cv2.createTrackbar("H_center", win, det.cfg["h"], 179, noop)
    cv2.createTrackbar("H_halfwidth", win, det.cfg["hw"], 90, noop)
    cv2.createTrackbar("S_min", win, det.cfg["s_min"], 255, noop)
    cv2.createTrackbar("S_max", win, det.cfg["s_max"], 255, noop)
    cv2.createTrackbar("V_min", win, det.cfg["v_min"], 255, noop)
    cv2.createTrackbar("V_max", win, det.cfg["v_max"], 255, noop)
    cv2.createTrackbar("hue_boost_x10", win, int(det.boost * 10), 40, noop)
    cv2.createTrackbar("hue_suppress_x10", win, int(det.suppress * 10), 10, noop)
    cv2.createTrackbar("boost_band", win, det.boost_band, 90, noop)
    cv2.createTrackbar("open_k", win, det.k_open, 31, noop)
    cv2.createTrackbar("close_k", win, det.k_close, 31, noop)
    cv2.createTrackbar("min_area_x1e5", win, int(det.min_area_frac * 1e5), 200, noop)
    cv2.createTrackbar("circ_min_x100", win, int(det.circ_min * 100), 100, noop)


def read_trackbars(win, det):
    g = lambda n: cv2.getTrackbarPos(n, win)
    det.cfg["h"] = g("H_center")
    det.cfg["hw"] = max(1, g("H_halfwidth"))
    det.cfg["s_min"] = g("S_min")
    det.cfg["s_max"] = max(g("S_min") + 1, g("S_max"))
    det.cfg["v_min"] = g("V_min")
    det.cfg["v_max"] = max(g("V_min") + 1, g("V_max"))
    det.boost = max(0.1, g("hue_boost_x10") / 10.0)
    det.suppress = max(0.1, g("hue_suppress_x10") / 10.0)
    det.boost_band = max(1, g("boost_band"))
    det.k_open = max(1, g("open_k") | 1)
    det.k_close = max(1, g("close_k") | 1)
    det.min_area_frac = max(1e-6, g("min_area_x1e5") / 1e5)
    det.circ_min = g("circ_min_x100") / 100.0


def write_trackbars(win, det):
    cv2.setTrackbarPos("H_center", win, det.cfg["h"])
    cv2.setTrackbarPos("H_halfwidth", win, det.cfg["hw"])
    cv2.setTrackbarPos("S_min", win, det.cfg["s_min"])
    cv2.setTrackbarPos("S_max", win, det.cfg["s_max"])
    cv2.setTrackbarPos("V_min", win, det.cfg["v_min"])
    cv2.setTrackbarPos("V_max", win, det.cfg["v_max"])
    cv2.setTrackbarPos("hue_boost_x10", win, int(det.boost * 10))
    cv2.setTrackbarPos("hue_suppress_x10", win, int(det.suppress * 10))
    cv2.setTrackbarPos("boost_band", win, det.boost_band)


def gst_pipeline(width=1280, height=720, fps=30, sensor_id=0):
    """Jetson(CSI 摄像头)专用 GStreamer 管线字符串; UVC 摄像头不需要它。
    用法: open_camera(gst=gst_pipeline(1280,720,30)) 或命令行 --gst。"""
    return ("nvarguscamerasrc sensor-id=%d ! "
            "video/x-raw(memory:NVMM),width=%d,height=%d,framerate=%d/1 ! "
            "nvvidconv ! video/x-raw,format=BGRx ! "
            "videoconvert ! video/x-raw,format=BGR ! appsink drop=1 max-buffers=1"
            % (int(sensor_id), int(width), int(height), int(fps)))


def _v4l2_lock_exposure(dev, exp):
    """用 v4l2-ctl 兜底锁曝光。

    ★实测（RDK X5 + Microdia LRCP G720P，2026-09-16）：OpenCV 的
      `CAP_PROP_AUTO_EXPOSURE=0.25` + `CAP_PROP_EXPOSURE=156` 在这台相机上**不生效**
      （v4l2 里 `auto_exposure` 仍是 3=Aperture Priority、`exposure_time_absolute` 标 `inactive`），
      而 `v4l2-ctl -c auto_exposure=1 -c exposure_time_absolute=N` 能真正锁住
      （设完读回 value=1 Manual Mode、value=157 且不再 inactive）。
      所以这里做"OpenCV 先设 → 读回不匹配 → v4l2-ctl 兜底"。"""
    import subprocess
    try:
        subprocess.run(["v4l2-ctl", "-d", dev,
                        "-c", "auto_exposure=1",
                        "-c", "exposure_time_absolute=%d" % int(exp)],
                       timeout=4, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 读回校验：只认 v4l2 层的 auto_exposure 是否真的变成 1(Manual)
        # （OpenCV 的 CAP_PROP_EXPOSURE 即使控件 inactive 也能读回数值, 不可信）
        r = subprocess.run(["v4l2-ctl", "-d", dev, "--get-ctrl", "auto_exposure"],
                           timeout=4, capture_output=True, text=True)
        val = "".join(ch for ch in (r.stdout or "") if ch.isdigit())
        if val == "1":
            print("[cam] 曝光已锁: %s  auto_exposure=1(Manual)  exposure_time_absolute=%d"
                  % (dev, int(exp)))
            return True
        print("[warn] 曝光没锁上: %s  auto_exposure 读回=%r（期望 1=Manual）" % (dev, val or "?"))
        return False
    except Exception as e:
        print("[warn] v4l2-ctl 锁曝光失败(%r) → 曝光可能仍是自动" % e)
        return False


def open_camera(idx=None, lock=True, exposure=None, gst=None, size=(1280, 720),
                lock_wb=False, wb_temp=5000):
    """打开相机。
    · gst 给出时走 GStreamer(Jetson CSI: nvarguscamerasrc),否则走 V4L2 并自动回退扫描 0..3;
    · lock=True 时锁曝光（OpenCV 设不动会自动用 v4l2-ctl 兜底）;
    · lock_wb=True 才锁白平衡 —— ⚠️ 部分 UVC 相机不支持 red/blue balance（max=0），
      锁了就偏色（RDK 那台 LRCP G720P 实测画面发青），所以**默认不锁**（保持自动 WB）。"""
    if gst:
        cap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                print("[cam] GStreamer(CSI) 实际 %dx%d  管线=%s"
                      % (int(cap.get(3)), int(cap.get(4)), gst))
                return cap
            cap.release()
        print("[warn] GStreamer 管线打不开(检查 nvarguscamerasrc / sensor-id / OpenCV 是否带 GStreamer)")
        return None
    cands = []
    if idx is not None:
        cands.append(idx)
    for i in (0, 1, 2, 3):
        if i not in cands:
            cands.append(i)
    for i in cands:
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(size[0]))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(size[1]))
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if lock and exposure is not None:
                exp = float(exposure)
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
                cap.set(cv2.CAP_PROP_EXPOSURE, exp)
                # ★总是用 v4l2-ctl 再锁一遍并读回校验：这台 UVC 上 OpenCV 的设置不可信
                #   （控件标 inactive 时 OpenCV 也能读回数值，看不出没锁上）
                _v4l2_lock_exposure("/dev/video%d" % i, exp)
                if lock_wb:                                # 默认不锁：部分 UVC 锁了会偏色
                    cap.set(cv2.CAP_PROP_AUTO_WB, 0)
                    cap.set(cv2.CAP_PROP_WB_TEMPERATURE, int(wb_temp))
            elif lock:
                # ★没给 --exposure 就**不要乱锁**：默认值（156）是树莓派那台相机的，
                #   换相机后可能直接过曝（RDK 上那台 LRCP G720P 实测就是一片惨白）→ 保持自动
                print("[cam] lock=True 但没给 exposure 值 → 保持自动曝光/白平衡（要锁请传 exposure=N）")
            ok, _ = cap.read()
            if ok:
                note = "" if (idx is None or i == idx) else "(指定 %s 打不开,已回退)" % idx
                locked = (lock and exposure is not None)
                print("[cam] 使用 /dev/video%d 实际 %dx%d  曝光=%s(读回 %s)  锁WB=%s %s"
                      % (i, int(cap.get(3)), int(cap.get(4)),
                         "手动锁" if locked else "自动",
                         cap.get(cv2.CAP_PROP_EXPOSURE), lock_wb, note))
                return cap
            cap.release()
    return None


# ----------------------------------------------------------------------
# 性能档: 同一套代码在不同机器上自动选参数(Pi4 / Jetson Orin / RDK X5 等)
#   width       处理(检测)宽度; stream_width 推流最长边; quality JPEG 质量;
#   diag_every  每 N 帧做一次完整诊断; threads OpenCV 线程数; stream_fps 推流限帧
#   · low/mid/high = 通用三档(high 给 Jetson);
#   · "rdk" = RDK X5 专用档(8×Cortex-A55 纯 CPU): 实测 1280 全管线只有 4.3fps,
#     640 处理 + 640 推流才有 7.5fps 量级 → 宁可糊一点也要跟得上手。
# ----------------------------------------------------------------------
PERF_PRESETS = {
    "low":  dict(width=640,  stream_width=480,  quality=70, diag_every=5, threads=2,  stream_fps=10),
    "mid":  dict(width=960,  stream_width=720,  quality=75, diag_every=3, threads=2,  stream_fps=15),
    "high": dict(width=1280, stream_width=960,  quality=85, diag_every=1, threads=4,  stream_fps=25),
    "rdk":  dict(width=512,  stream_width=512,  quality=76, diag_every=20, threads=4,  stream_fps=12,
                 # 弱算力平台顺带覆盖算法参数: 实测再省 ~27% 检测时间, 精度与 41/9 一致
                 cfg={"thr_block": 25, "close_k": 5, "proc_scale": 0.34, "digit_every": 3}),
}


def detect_perf():
    """返回档位名: Jetson(Tegra/Orin/Xavier/Nano)→high; RDK X5(D-Robotics)→rdk(专用档);
    其它按 CPU 核数: ≥8→high, ≥4→mid, 否则 low。"""
    model = ""
    try:
        with open("/proc/device-tree/model") as f:
            model = f.read().lower()
    except Exception:
        model = ""
    if any(k in model for k in ("orin", "xavier", "nano", "tegra", "jetson")):
        return "high"
    if any(k in model for k in ("rdk", "d-robotics", "drobotics", "sunrise")):
        return "rdk"
    try:
        with open("/proc/version") as f:
            if "tegra" in f.read().lower():
                return "high"
    except Exception:
        pass
    n = os.cpu_count() or 2
    if n >= 8:
        return "high"
    if n >= 4:
        return "mid"
    return "low"


def resolve_perf(args, perf_arg):
    """把 --perf(auto|low|mid|high) 解析进 args(只填空值, 显式传入的优先)。"""
    name = detect_perf() if (perf_arg in (None, "auto")) else str(perf_arg)
    if name not in PERF_PRESETS:
        name = "mid"
    p = PERF_PRESETS[name]
    applied = {}
    for k in ("width", "stream_width", "quality", "diag_every", "stream_fps"):
        if getattr(args, k, None) is None:
            setattr(args, k, p[k])
            applied[k] = p[k]
    try:
        cv2.setNumThreads(int(p["threads"]))
    except Exception:
        pass
    return name, applied


def ui_available():
    """判断当前进程能不能开 OpenCV 窗口(Linux 看 DISPLAY/Wayland/Qt 平台)。"""
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
                    or os.environ.get("QT_QPA_PLATFORM"))
    return True


NO_DISPLAY_HINT = """[!] 当前 shell 没有图形显示(环境变量 DISPLAY 为空),OpenCV 开窗会直接崩。
    三种办法任选:
      1) 【推荐】在 VNC 桌面里打开 Terminal,直接运行本命令(DISPLAY 自动就绪)
      2) SSH 里指定显示:  DISPLAY=:0 python3 color_blob_demo.py --cam 0
         (不行就把 :0 换成 :1 再试)
      3) 纯无桌面:  QT_QPA_PLATFORM=vnc python3 color_blob_demo.py --cam 0
         然后用 VNC 客户端连 <树莓派IP>:5900
    也可以先用 --no-show / --synthetic / --selftest 做无界面验证。"""


def ui_loop(cap, det, color_idx, args):
    win = "color blob demo (q=quit a=auto 1-6=calib s=save l=load)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, 1100, 700)
    make_trackbars(win, det)
    roi_center = [0, 0]      # 鼠标点选标定位置;0,0=用画面中心
    frozen = [None]
    manual = [True]
    snap = [0]

    def on_mouse(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN:
            roi_center[0], roi_center[1] = x, y
            print("[ui] 标定 ROI = (%d,%d)" % (x, y))
    cv2.setMouseCallback(win, on_mouse)

    print(__doc__)
    while True:
        if frozen[0] is not None:
            frame = frozen[0].copy()
        else:
            if cap is None:
                frame = synthetic_scene(["red", "green", "lightblue"], seed=int(time.time()) % 1000)
            else:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue
        name = COLOR_NAMES[color_idx[0]]
        if manual[0]:
            read_trackbars(win, det)
        res = det.detect(frame)
        view = det.draw(frame.copy(), res, name)
        # 侧栏:掩膜 + 增幅预览
        m = cv2.cvtColor(res["mask"], cv2.COLOR_GRAY2BGR)
        m = cv2.resize(m, (view.shape[1] // 4, view.shape[0] // 4))
        view[0:m.shape[0], view.shape[1] - m.shape[1]:] = m
        cv2.putText(view, "cam=%s manual=%s frozen=%s" %
                    ("on" if cap else "synthetic", manual[0], frozen[0] is not None),
                    (10, view.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        cv2.imshow(win, view)
        k = cv2.waitKey(1) & 0xFF
        if k in (27, ord('q')):
            break
        elif k == ord('a'):
            cx = roi_center[0] or frame.shape[1] // 2
            cy = roi_center[1] or frame.shape[0] // 2
            cfg, npx = det.auto_calibrate(frame, cx, cy)
            if cfg:
                print("[auto] %s: %s (样本 %d px)" % (name, cfg, npx))
                write_trackbars(win, det)
            else:
                print("[auto] ROI 内有效像素太少,换个位置再按 a")
        elif ord('1') <= k <= ord('6'):
            i = k - ord('1')
            cx = roi_center[0] or frame.shape[1] // 2
            cy = roi_center[1] or frame.shape[0] // 2
            cfg, npx = det.auto_calibrate(frame, cx, cy)
            if cfg:
                PRESETS[COLOR_NAMES[i]] = dict(cfg)
                print("[calib] %s <- %s (样本 %d px)" % (COLOR_NAMES[i], cfg, npx))
                color_idx[0] = i
                det.cfg = dict(PRESETS[COLOR_NAMES[i]])
                write_trackbars(win, det)
        elif k == ord('n'):
            color_idx[0] = (color_idx[0] + 1) % len(COLOR_NAMES)
            det.cfg = dict(PRESETS[COLOR_NAMES[color_idx[0]]])
            write_trackbars(win, det)
        elif k == ord('p'):
            color_idx[0] = (color_idx[0] - 1) % len(COLOR_NAMES)
            det.cfg = dict(PRESETS[COLOR_NAMES[color_idx[0]]])
            write_trackbars(win, det)
        elif k == ord('m'):
            manual[0] = not manual[0]
            print("[ui] 手动模式" if manual[0] else "[ui] 自动模式(每次 detect 前会重读 trackbar)")
        elif k == ord('b'):
            det.use_wb = not det.use_wb
            print("[ui] 灰世界白平衡:", det.use_wb)
        elif k == ord('v'):
            det.drop_glare = not det.drop_glare
            print("[ui] 高光剔除:", det.drop_glare)
        elif k == ord(' '):
            frozen[0] = None if frozen[0] is not None else frame.copy()
            print("[ui] 冻结" if frozen[0] is not None else "[ui] 解冻")
        elif k == ord('s'):
            with open(args.profile, "w") as f:
                json.dump(PRESETS, f, ensure_ascii=False, indent=2)
            print("[ui] 档案已保存:", args.profile)
        elif k == ord('l'):
            if os.path.exists(args.profile):
                with open(args.profile) as f:
                    PRESETS.update(json.load(f))
                det.cfg = dict(PRESETS[COLOR_NAMES[color_idx[0]]])
                write_trackbars(win, det)
                print("[ui] 档案已载入:", args.profile)
        elif k == ord('c'):
            p = os.path.join(args.out, "snap_%03d.jpg" % snap[0])
            snap[0] += 1
            cv2.imwrite(p, view)
            print("[ui] 抓拍:", p)
        elif k in (ord('['), ord(']')) and cap is not None:
            cur = cap.get(cv2.CAP_PROP_EXPOSURE)
            if not cur or cur <= 0:
                cur = 156.0
            newv = max(1.0, min(2000.0, cur + (-20 if k == ord('[') else 20)))
            cap.set(cv2.CAP_PROP_EXPOSURE, newv)
            print("[ui] 曝光 -> %.0f(偏暗按 ] 加,过曝按 [ 减)" % cap.get(cv2.CAP_PROP_EXPOSURE))
    cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=None, help="相机索引(默认自动 0..3)")
    ap.add_argument("--image", default=None, help="单图模式")
    ap.add_argument("--synthetic", type=int, default=0, help="合成场景数量")
    ap.add_argument("--selftest", action="store_true", help="六色自测(无相机/UI)")
    ap.add_argument("--debug", action="store_true", help="打印候选轮廓的面积/圆度(调参用)")
    ap.add_argument("--no-show", action="store_true", help="不弹窗(SSH/无显示)")
    ap.add_argument("--color", default="red", help="目标颜色名(见 PRESETS)")
    ap.add_argument("--profile", default="color_profile.json")
    ap.add_argument("--no-lock", action="store_true",
                    help="不锁曝光/白平衡(默认锁: 曝光156 + WB5000,与 ring/aim 一致)")
    ap.add_argument("--exposure", type=float, default=None,
                    help="锁曝光时的曝光值(默认156;偏暗/偏亮时改这个)")
    ap.add_argument("--out", default="color_demo_out")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    if args.selftest:
        bad = selftest(args.out)
        auto_calib_selftest(args.out)
        print("\nSELFTEST_%s" % ("OK" if not bad else "WEAK:" + ",".join(bad)))
        return 0

    det = ColorBlobDetector(PRESETS.get(args.color, PRESETS["custom"]))

    if args.debug:
        img = cv2.imread(args.image) if args.image else synthetic_scene([args.color], seed=1)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        emph, dist = det.emphasize(hsv)
        mask = det.morph(det.build_mask(emph, dist))
        h, w = mask.shape[:2]
        amin = det.min_area_frac * w * h
        amax = det.max_area_frac * w * h
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        print("[debug] color=%s mask_px=%d  area门限=%.0f..%.0f  circ_min=%.2f"
              % (args.color, int((mask > 0).sum()), amin, amax, det.circ_min))
        for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:6]:
            a = cv2.contourArea(c)
            p = cv2.arcLength(c, True) + 1e-6
            print("   area=%8.0f  circ=%.2f  %s" % (a, 4 * np.pi * a / (p * p),
                  "OK" if (amin <= a <= amax and 4 * np.pi * a / (p * p) >= det.circ_min)
                  else "reject"))
        cv2.imwrite(os.path.join(args.out, "debug_%s.jpg" % args.color),
                    det.draw(img.copy(), det.detect(img), args.color))
        return 0

    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            print("读图失败:", args.image)
            return 2
        res = det.detect(img, args.color)
        if res["found"]:
            i = res["info"]
            print("[result] %s found center=(%.0f,%.0f) r=%.0f circ=%.2f fill=%.2f hsv=(%.0f,%.0f,%.0f)"
                  % (args.color, i["cx"], i["cy"], i["r"], i["circ"], i["fill"], *i["mean_hsv"]))
        else:
            print("[result] %s not found" % args.color)
        cv2.imwrite(os.path.join(args.out, "result_%s.jpg" % args.color),
                    det.draw(img.copy(), res, args.color))
        return 0

    if args.synthetic:
        for k in range(args.synthetic):
            img = synthetic_scene(["red", "yellow", "green", "blue", "lightblue", "black"],
                                  seed=k, cast=0.03 * k)
            res = det.detect(img, args.color)
            print("[synthetic %d] %s found=%s" % (k, args.color, res["found"]))
            cv2.imwrite(os.path.join(args.out, "syn_%d.jpg" % k),
                        det.draw(img.copy(), res, args.color))
        return 0

    cap = open_camera(args.cam, lock=not args.no_lock, exposure=args.exposure)
    color_idx = [COLOR_NAMES.index(args.color) if args.color in COLOR_NAMES else 6]
    if args.no_show:
        print("[info] --no-show: 只跑一次检测(SSH 冒烟)")
        if cap is None:
            frame = synthetic_scene(["red", "green", "lightblue"], seed=3)
        else:
            ok, frame = cap.read()
            if not ok:
                print("相机读帧失败")
                return 2
        res = det.detect(frame, args.color)
        print("[result]", "found" if res["found"] else "not found",
              res["info"] if res["found"] else "")
        cv2.imwrite(os.path.join(args.out, "smoke_%s.jpg" % args.color),
                    det.draw(frame.copy(), res, args.color))
        if cap:
            cap.release()
        return 0
    if cap is None:
        print("[warn] 没找到相机(试过 --cam %s 与 0..3)→ 合成场景演示" % args.cam)
    if not ui_available():
        print(NO_DISPLAY_HINT)
        frame = None
        if cap is not None:
            ok, frame = cap.read()
            if not ok:
                frame = None
        if frame is None:
            frame = synthetic_scene(["red", "green", "lightblue"], seed=5)
            print("[info] 无相机 → 用合成场景做一次无界面检测")
        res = det.detect(frame, args.color)
        print("[result]", "found" if res["found"] else "not found",
              res["info"] if res["found"] else "")
        cv2.imwrite(os.path.join(args.out, "headless_%s.jpg" % args.color),
                    det.draw(frame.copy(), res, args.color))
        if cap:
            cap.release()
        return 0
    ui_loop(cap, det, color_idx, args)
    if cap:
        cap.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
