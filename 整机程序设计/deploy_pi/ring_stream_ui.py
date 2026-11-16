#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ring_stream_ui.py —— 圆环识别中心:浏览器调参/诊断台(Pi 端零窗口)
====================================================================
为什么这么做: 上一代用 cv2.imshow(Qt) 在树莓派 VNC 上会拖死图形栈,
本脚本**不创建任何窗口**, 只用 OpenCV + http.server:
    MJPEG 推流 + 网页滑条/按钮 → 树莓派只跑 SSH 也不会卡死, 控制台纯 ASCII。

网页能力:
  · 实时画面: 绿圈(外环)/黄十字(环心)/数字+置信度; 右下角小窗 = 二值掩膜 + 数字裁剪图
  · 全部 CALIB 参数滑条(即时生效): thr_block/thr_c/close_k/min_area_frac/center_gate_r/
    proc_scale/ema/stale_max/digit_*/jump_px/inner_fallback_frac/rotate_ccw/曝光…
  · 诊断面板: 原始 vs EMA 圆心(抖动px)、φ1 与是否 fallback、数字各类分数(top1/top2)、
    投票窗口直方图、各阶段耗时
  · 按钮: 自动估参 / 冻结 / 抓拍 / 保存·载入档案 / 一键跑三套回归 / 一键加模板(打印体)
  · 支持 --image 离线单图(现场照片/A4 靶标), 用于无相机时调参

用法(SSH 里跑; 浏览器打开 http://<PiIP>:8091/):
  python3 ring_stream_ui.py --cam 0 --port 8091
  python3 ring_stream_ui.py --image 工创赛标靶A4(12).png --port 8091
  python3 ring_stream_ui.py --no-cam --port 8091            # 合成靶标(离线自测)
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import http.server

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ring_center_detect_v2 as ring                      # noqa: E402
import digit_templates_ext as dtpl                        # noqa: E402

try:
    # 复用相机/锁曝光/性能档位逻辑 (Pi 与 Jetson 通用)
    from color_blob_demo import (open_camera, gst_pipeline,      # noqa: E402
                                 detect_perf, resolve_perf,
                                 PERF_PRESETS)
except Exception:                                          # pragma: no cover
    open_camera = None
    gst_pipeline = None

    PERF_PRESETS = {
        "low":  dict(width=640,  stream_width=480, quality=70, diag_every=5, threads=2, stream_fps=10),
        "mid":  dict(width=960,  stream_width=720, quality=75, diag_every=3, threads=2, stream_fps=15),
        "high": dict(width=1280, stream_width=960, quality=85, diag_every=1, threads=4, stream_fps=25),
        "rdk":  dict(width=512,  stream_width=512, quality=76, diag_every=20, threads=4, stream_fps=12,
                     cfg={"thr_block": 25, "close_k": 5, "proc_scale": 0.34, "digit_every": 3}),
    }

    def detect_perf():
        return "mid"

    def resolve_perf(args, perf_arg=None):
        name = detect_perf() if perf_arg in (None, "auto") else str(perf_arg)
        p = PERF_PRESETS.get(name, PERF_PRESETS["mid"])
        applied = {}
        for k in ("width", "stream_width", "quality", "diag_every", "stream_fps"):
            if getattr(args, k, None) is None:
                setattr(args, k, p[k])
                applied[k] = p[k]
        return name, applied

# ----------------------------------------------------------------------
# 参数表: 每项 = dict(名, 键, 最小, 最大, 步进, 类型, 奇数约束, 提示)
#   kind: "int" / "float";  odd=True 表示只允许奇数;  键=写进 cfg 的键名(默认同名)
#   注: cv2.adaptiveThreshold 要求 blockSize 奇数且 ≥3, 所以 thr_block 最小 3;
#       想要"更小的有效窗口"用 proc_scale=1.0(同 blockSize 覆盖更小物理范围)+ pre_blur。
PARAM_SPEC = [
    dict(name="thr_block",      key="thr_block",      mn=3,      mx=101,  st=2,     kind="int",   odd=True,
         hint="整数·奇数, ≥3(OpenCV限制); 越小越局域、越易受噪声影响"),
    dict(name="thr_c",          key="thr_c",          mn=-20,    mx=40,   st=0.1,   kind="float", odd=False,
         hint="小数, 步进0.1; 1~5 之间可精调(越大只留更暗的线)"),
    dict(name="pre_blur",       key="pre_blur",       mn=1,      mx=15,   st=2,     kind="int",   odd=True,
         hint="整数·奇数; 阈值前高斯核(1=关); 小 thr_block 时必须靠它压噪"),
    dict(name="close_k",        key="close_k",        mn=1,      mx=31,   st=1,     kind="int",   odd=False,
         hint="整数(奇偶都可); 10~15 可精调(≈环线间距px), 把断线接起来"),
    dict(name="min_area_frac",  key="min_area_frac",  mn=0.00001, mx=0.003, st=0.00001, kind="float", odd=False,
         hint="小数; 连通域最小面积占比(滤小噪声)"),
    dict(name="center_gate_r",  key="center_gate_r",  mn=0,      mx=800,  st=10,    kind="int",   odd=False,
         hint="整数 px; 0=关闭位置门(离线/瞄准必须0)"),
    dict(name="proc_scale",     key="proc_scale",     mn=0.2,    mx=1.0,  st=0.05,  kind="float", odd=False,
         hint="小数 0.2~1.0; 检测分辨率比例(坐标自动回算)"),
    dict(name="ema",            key="ema",            mn=0.05,   mx=1.0,  st=0.01,  kind="float", odd=False,
         hint="小数; 圆心平滑系数(大=稳但滞后)"),
    dict(name="stale_max",      key="stale_max",      mn=1,      mx=50,   st=1,     kind="int",   odd=False,
         hint="整数; 连续丢失多少帧后 conf 降级"),
    dict(name="jump_px",        key="jump_px",        mn=10,     mx=400,  st=10,    kind="int",   odd=False,
         hint="整数 px; 超过它=判定'换环'→立即跟上并清数字窗口"),
    dict(name="digit_rot_step", key="digit_rot_step", mn=5,      mx=45,   st=5,     kind="int",   odd=False,
         hint="整数°; 模板旋转步长(大=模板少/构建快, 角度容错略降)"),
    dict(name="digit_thr",      key="digit_thr",      mn=0.0,    mx=1.0,  st=0.01,  kind="float", odd=False,
         hint="小数; 数字 NCC 置信阈值(低=易读出/易误读)"),
    dict(name="digit_votes",    key="digit_votes",    mn=1,      mx=120,  st=1,     kind="int",   odd=False,
         hint="整数; 滑动窗口长度(众数输出, 大=稳但更新慢)"),
    dict(name="digit_unlock_stale", key="digit_unlock_stale", mn=1, mx=120, st=1,   kind="int",   odd=False,
         hint="整数; 环丢失多少帧后清空投票窗口"),
    dict(name="digit_inner_frac", key="digit_inner_frac", mn=0.3, mx=1.0, st=0.01, kind="float", odd=False,
         hint="小数; 数字裁剪边长=2×φ1×该值"),
    dict(name="inner_fallback_frac", key="inner_fallback_frac", mn=0.2, mx=1.0, st=0.01, kind="float", odd=False,
         hint="小数; 找不到φ1时的退路半径系数(×外环半径)"),
    dict(name="digit_margin",   key="digit_margin",   mn=0.0,    mx=0.5,  st=0.01,  kind="float", odd=False,
         hint="小数; top1 与 top2 差小于它 → 输出 '?'"),
    dict(name="rotate_ccw",     key="rotate_ccw",     mn=0,      mx=1,    st=1,     kind="int",   odd=False,
         hint="0/1; 读帧后逆时针转90°(输出坐标随之变)"),
    dict(name="lock_exposure",  key="lock_exposure",  mn=0,      mx=1,    st=1,     kind="int",   odd=False,
         hint="0/1; 锁曝光(颜色/圆环复现性前提)"),
    dict(name="exposure",       key="exposure",       mn=1,      mx=2000, st=10,    kind="int",   odd=False,
         hint="整数; 锁定时的曝光值(暗→加, 过曝→减)"),
]
SPEC_BY_NAME = {p["name"]: p for p in PARAM_SPEC}


def cfg_to_web(cfg):
    """cfg → 网页显示值(直接是实际物理值, 不再乘缩放系数)。"""
    out = {}
    for p in PARAM_SPEC:
        v = cfg.get(p["key"], 0)
        if p["kind"] == "int":
            out[p["name"]] = int(round(float(v)))
        else:
            out[p["name"]] = round(float(v), 5)
    out["exposure"] = int(cfg.get("exposure", 156) or 156)
    return out


def apply_param(cfg, name, raw):
    """校验+写回。返回 (ok, 实际写入值, 提示)。奇数约束/范围/类型都会在这把关。"""
    p = SPEC_BY_NAME.get(name)
    if p is None:
        return False, None, "未知参数: %s" % name
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return False, None, "%s 需要数字" % name
    if p["kind"] == "int":
        v = int(round(v))
        if p["odd"] and v % 2 == 0:
            v += 1 if v >= p["mn"] else 1
            msg = "%s 只接受奇数, 已取 %d" % (name, v)
        else:
            msg = "ok"
    else:
        v = round(v, 4)
        msg = "ok"
    if v < p["mn"]:
        v = p["mn"]
        msg = "%s 下限 %s" % (name, p["mn"])
    if v > p["mx"]:
        v = p["mx"]
        msg = "%s 上限 %s" % (name, p["mx"])
    if p["name"] in ("rotate_ccw", "lock_exposure"):
        cfg[p["key"]] = bool(int(v))
        return True, int(v), msg
    cfg[p["key"]] = v
    return True, v, msg


# ----------------------------------------------------------------------
# 圆环/数字诊断: 复刻 ring 的裁剪与打分, 但额外给出各类分数与中间图
# ----------------------------------------------------------------------
def glyph_from_frame(frame, cx, cy, r_inner, cfg):
    """按 recognize_digit 同一套逻辑取出"紧致二值字形"; 返回 (glyph_bw 或 None, crop_bgr)。"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    h, w = gray.shape[:2]
    side = max(int(2 * r_inner * cfg.get("digit_inner_frac", 0.80)), 24)
    x0 = max(0, int(cx) - side // 2)
    y0 = max(0, int(cy) - side // 2)
    x1 = min(w, x0 + side)
    y1 = min(h, y0 + side)
    if x1 - x0 < 12 or y1 - y0 < 12:
        return None, None
    crop = gray[y0:y1, x0:x1].copy()
    crop_bgr = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    if crop.mean() < 128:
        crop = 255 - crop
    bw = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 15, 5)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    chh, cww = bw.shape
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    keep = []
    for c in cnts:
        x, y, ww, hh = cv2.boundingRect(c)
        if x <= 1 or y <= 1 or x + ww >= cww - 1 or y + hh >= chh - 1:
            continue
        bcx, bcy = x + ww / 2, y + hh / 2
        if abs(bcx - cww / 2) > 0.45 * cww or abs(bcy - chh / 2) > 0.45 * chh:
            continue
        if hh < side * 0.15 or hh > side * 0.95:
            continue
        keep.append((x, y, ww, hh))
    if not keep:
        return None, crop_bgr
    x = min(k[0] for k in keep)
    y = min(k[1] for k in keep)
    x2 = max(k[0] + k[2] for k in keep)
    y2 = max(k[1] + k[3] for k in keep)
    g = bw[y:y2, x:x2]
    if g.shape[0] < 4 or g.shape[1] < 4:
        return None, crop_bgr
    return g, crop_bgr


def digit_scores(glyph, cfg):
    """返回 (每类最高分 dict, top1, top2, 模板总数)。"""
    if glyph is None:
        return {}, None, 0.0, 0
    g = ring._glyph_tensor(glyph, 28).flatten()
    labels, T = ring._build_rot_templates(int(cfg.get("digit_rot_step", 5)))
    sc = T @ g
    best = {}
    for ch in "123":
        idx = [i for i, lab in enumerate(labels) if lab == ch]
        best[ch] = float(np.max(sc[idx])) if idx else 0.0
    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    return best, ranked[0][0], ranked[1][1], len(labels)


def ring_stage_timings(frame_small, cfg):
    """对当前帧跑一次底层各阶段, 返回 (结果, 掩膜, 耗时 ms, 环线间距估计)。"""
    t = {}
    t0 = time.perf_counter()
    gray = cv2.cvtColor(frame_small, cv2.COLOR_BGR2GRAY)
    thr_block = int(cfg["thr_block"])
    if thr_block < 3:                      # OpenCV 硬性要求奇数 ≥3
        thr_block = 3
    if thr_block % 2 == 0:
        thr_block += 1
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, thr_block, float(cfg["thr_c"]))
    ck = max(1, int(round(float(cfg["close_k"]))))     # ★允许偶数(10~15 精调)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ck, ck))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k)
    t["thr"] = (time.perf_counter() - t0) * 1000.0
    t0 = time.perf_counter()
    r = ring.find_center(frame_small, cfg)
    t["center"] = (time.perf_counter() - t0) * 1000.0
    spacing = None
    if r:
        cx, cy, rad = r
        # 沿 8 条射线数"环线交点"间距(用于建议 close_k)
        hs, ws = bw.shape[:2]
        gaps = []
        for a in range(0, 360, 45):
            th = np.deg2rad(a)
            prev = None
            for rr in range(int(rad * 0.15), int(rad * 1.05), 2):
                x = int(cx + rr * np.cos(th))
                y = int(cy + rr * np.sin(th))
                if not (0 <= x < ws and 0 <= y < hs):
                    break
                if bw[y, x] > 0:
                    if prev is not None and 3 < rr - prev < 60:
                        gaps.append(rr - prev)
                    prev = rr
        if gaps:
            spacing = float(np.median(gaps))
    return r, bw, t, spacing


# ----------------------------------------------------------------------
# 状态与处理
# ----------------------------------------------------------------------
class State:
    def __init__(self, cfg, args, cap, image_path):
        self.cfg = cfg
        self.args = args
        self.cap = cap
        self.image_path = image_path
        self.lock = threading.Lock()
        self.det = ring.RingDetector(cfg)
        self.jpg = None
        self.seq = 0
        self.fps = 0.0
        self.info = {"found": False}
        self.diag = {}
        self.raw = None
        self.last_frame = None
        self.last_det = None
        self.frozen = False
        self.running = True
        self.tpl_ready = False       # 模板后台构建完成后置 True
        self.image_cache = None      # --image 模式: 只读一次盘
        self.stream_w = getattr(args, "stream_width", 720)   # 推流最长边
        self.stream_fps = float(getattr(args, "stream_fps", 0) or 0)  # 推流限帧(0=不限)
        self.last_enc_t = 0.0        # 上次 JPEG 编码时刻(推流限帧用)
        self.proc_w = args.width
        self.quality = args.quality

    def params(self):
        p = cfg_to_web(self.cfg)
        p["exposure"] = int(self.cfg.get("exposure", 156))
        return p


def do_detect(st, frame):
    """跑一次检测(含 rotate_ccw), 返回 (annotated view, result, diag)。"""
    cfg = st.cfg
    # ★先缩放再旋转: 90° 旋转会交换 w/h, 所以"旋转后宽度 = proc_w"等价于
    #   "旋转前高度 = proc_w" —— 先缩到那个尺寸再转, 少处理一半像素
    #   (RDK X5 实测 rotate+resize 由 ~25ms 降到 ~12ms)。输出尺寸与原来完全一致。
    if st.proc_w:
        h0, w0 = frame.shape[:2]
        if cfg.get("rotate_ccw", False):
            if h0 > st.proc_w:
                sc = st.proc_w / float(h0)
                frame = cv2.resize(frame, (max(1, int(w0 * sc)), st.proc_w),
                                   interpolation=cv2.INTER_AREA)
        elif w0 > st.proc_w:
            frame = cv2.resize(frame, (st.proc_w, max(1, int(h0 * st.proc_w / w0))),
                               interpolation=cv2.INTER_AREA)
    fr = ring.rotate_ccw(frame, cfg)
    pk = int(cfg.get("pre_blur", 1) or 1)    # ★阈值前额外平滑(叠加在内置5×5之上)
    if pk >= 3:
        fr = cv2.GaussianBlur(fr, (pk | 1, pk | 1), 0)
    t0 = time.perf_counter()
    res = st.det.process(fr)
    t_proc = (time.perf_counter() - t0) * 1000.0
    st.frame_idx = getattr(st, "frame_idx", 0) + 1
    due = (getattr(st, "diag_every", 1) <= 1) or (st.frame_idx % getattr(st, "diag_every", 1) == 0)
    if due:
        scale = float(cfg.get("proc_scale", 1.0))
        small = cv2.resize(fr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) \
            if scale != 1.0 else fr
        r_raw, bw, tim, spacing = ring_stage_timings(small, cfg)
        tim["process"] = t_proc
        diag = {"timings_ms": {k: round(v, 2) for k, v in tim.items()},
                "line_spacing_px": (round(spacing, 1) if spacing else None),
                "templates": len(ring._build_rot_templates(int(cfg.get("digit_rot_step", 5)))[0])}
        st.last_bw = bw
        if res.get("found"):
            diag["raw_center"] = [round(res["raw"][0], 1), round(res["raw"][1], 1)] if res.get("raw") else None
            diag["ema_center"] = [round(res["cx_px"], 1), round(res["cy_px"], 1)]
            if res.get("raw"):
                dx = res["raw"][0] - res["cx_px"]
                dy = res["raw"][1] - res["cy_px"]
                diag["raw_vs_ema_px"] = round(float(np.hypot(dx, dy)), 2)
            gs = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
            ri = ring.find_inner_radius(gs, res["cx_px"] * scale, res["cy_px"] * scale,
                                        res["r_outer_px"] * scale, cfg)
            if ri:
                r_inner = ri / scale
                diag["phi1_px"] = round(r_inner, 1)
                diag["phi1_source"] = "inner_radius"
            else:
                r_inner = res["r_outer_px"] * cfg.get("inner_fallback_frac", 0.55)
                diag["phi1_px"] = round(r_inner, 1)
                diag["phi1_source"] = "fallback"
            glyph, crop_bgr = glyph_from_frame(fr, res["cx_px"], res["cy_px"], r_inner, cfg)
            best, top1, top2, ntm = digit_scores(glyph, cfg)
            diag["digit_scores"] = {k: round(v, 3) for k, v in best.items()}
            diag["digit_top1"] = top1
            diag["digit_top2_score"] = round(top2, 3)
            diag["digit_thr"] = cfg.get("digit_thr")
            diag["glyph_ok"] = glyph is not None
            st.last_crop_bgr = crop_bgr          # 只在内存里, 免得每帧写盘
            st.last_glyph = glyph
            votes = getattr(st.det, "votes", [])
            diag["votes"] = {d: votes.count(d) for d in "123"}
        st.diag = diag
    else:
        diag = st.diag
    bw = getattr(st, "last_bw", None)
    if bw is None:
        bw = np.zeros((fr.shape[0] // 2, fr.shape[1] // 2), np.uint8)
    # ---- 画图: ★先缩放到推流尺寸, 再画叠加(否则 2px 细线在降采样后会被抹掉) ----
    #    ★推流缩放用 INTER_LINEAR: 预览图缩到 1/3 时肉眼差别可忽略,
    #      但比 INTER_AREA 省一半时间(RDK 上 11ms -> 5ms, 是第二大开销)
    disp = fr
    sc = 1.0
    if st.stream_w and max(fr.shape[:2]) > st.stream_w:
        sc = st.stream_w / float(max(fr.shape[:2]))
        disp = cv2.resize(fr, (int(fr.shape[1] * sc), int(fr.shape[0] * sc)),
                          interpolation=cv2.INTER_LINEAR)
    view = disp if disp is not fr else fr.copy()
    if res.get("found"):
        cx, cy = int(round(res["cx_px"] * sc)), int(round(res["cy_px"] * sc))
        r = max(2, int(round(res["r_outer_px"] * sc)))
        cv2.circle(view, (cx, cy), r, (0, 255, 0), 2)                     # 绿=EMA 中心
        cv2.drawMarker(view, (cx, cy), (0, 255, 255), cv2.MARKER_CROSS, 20, 2)  # 黄十字=输出中心
        if res.get("raw"):
            rx, ry = int(round(res["raw"][0] * sc)), int(round(res["raw"][1] * sc))
            # ★红色圆环: 以"原始(红色标定)中心"为圆心, 半径与黄/绿环完全相同 → 直观看抖动偏移
            cv2.circle(view, (rx, ry), r, (0, 0, 255), 2)
            cv2.circle(view, (rx, ry), 3, (0, 0, 255), -1)
            cv2.line(view, (cx, cy), (rx, ry), (255, 0, 255), 1)
        txt = ("digit=%s(%.2f) r=%d stale=%d conf=%.2f"
               % (res.get("digit"), res.get("digit_score", 0), r,
                  res.get("stale", 0), res.get("conf", 0)))
    else:
        txt = "not found"
    cv2.putText(view, txt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)
    d = diag
    line2 = ("fps=%.1f  phi1=%s(%s)  raw_vs_ema=%s px  spacing=%s"
             % (st.fps, d.get("phi1_px"), d.get("phi1_source"),
                d.get("raw_vs_ema_px"), d.get("line_spacing_px")))
    cv2.putText(view, line2, (10, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    line3 = ("S1=%s S2=%s S3=%s thr=%.2f votes=%s"
             % (d.get("digit_scores", {}).get("1"), d.get("digit_scores", {}).get("2"),
                d.get("digit_scores", {}).get("3"), d.get("digit_thr", 0),
                d.get("votes")))
    cv2.putText(view, line3, (10, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
    cv2.putText(view, "green/EMA center  RED=raw center (same r)  magenta=offset",
                (10, 106), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
    # 小窗: 二值掩膜 + 数字裁剪(内存, 不写盘)
    m = cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)
    m = cv2.resize(m, (view.shape[1] // 5, view.shape[0] // 5))
    view[0:m.shape[0], view.shape[1] - m.shape[1]:] = m
    cimg = getattr(st, "last_crop_bgr", None)
    if cimg is not None:
        ch = view.shape[0] // 5
        cw = int(cimg.shape[1] * ch / max(1, cimg.shape[0]))
        cimg = cv2.resize(cimg, (max(4, cw), ch))
        view[view.shape[0] - ch:, 0:max(4, cw)] = cimg
    return view, res, diag


def capture_loop(st):
    while st.running:
        if st.cap is None:
            time.sleep(0.05)
            continue
        ok, frame = st.cap.read()
        if not ok:
            time.sleep(0.05)
            continue
        with st.lock:
            st.raw = frame


def process_loop(st):
    t0, n, fps = time.time(), 0, 0.0
    seed = 0
    while st.running:
        t_loop = time.perf_counter()
        frame = None
        if st.image_path:
            if st.image_cache is None:
                st.image_cache = cv2.imread(st.image_path)   # 只读一次, 免得每帧读盘
            frame = st.image_cache
        elif st.cap is not None:
            with st.lock:
                frame = st.raw
        if frame is None:
            if st.cap is None and not st.image_path:      # 合成靶标(3 张缓存轮播)
                frame = synth_ring_scene(seed)
                seed = (seed + 1) % 3
                time.sleep(0.01)
            else:
                time.sleep(0.03)
                continue
        if st.frozen and st.last_frame is not None:
            frame = st.last_frame
        if not st.tpl_ready:
            # 模板还在后台构建: 先出画面(不检测), 避免"看起来卡住"
            fr = ring.rotate_ccw(frame, st.cfg)
            h2, w2 = fr.shape[:2]
            if st.proc_w and w2 > st.proc_w:
                fr = cv2.resize(fr, (st.proc_w, int(h2 * st.proc_w / w2)),
                                interpolation=cv2.INTER_AREA)
            cv2.putText(fr, "building digit templates ...", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            ok, jpg = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, st.quality])
            if ok:
                with st.lock:
                    st.jpg = jpg.tobytes()
                    st.info = {"found": False, "note": "templates_building"}
                    st.seq += 1
            time.sleep(0.05)
            continue
        view, res, diag = do_detect(st, frame)
        # ★只在本帧被冻结(或首帧)时保留一份原图副本: 每帧多转一次 90° 在 RDK 上约 7ms
        if st.frozen or st.last_frame is None:
            st.last_frame = ring.rotate_ccw(frame, st.cfg)
        st.last_det = res
        n += 1
        if time.time() - t0 >= 1.0:
            fps = n / (time.time() - t0)
            n, t0 = 0, time.time()
        st.fps = fps
        enc = view
        if st.stream_w and max(enc.shape[:2]) > st.stream_w:      # ★推流前降采样:编码是大头
            sc = st.stream_w / float(max(enc.shape[:2]))
            enc = cv2.resize(enc, (int(enc.shape[1] * sc), int(enc.shape[0] * sc)),
                             interpolation=cv2.INTER_AREA)
        # ★推流限帧: 浏览器根本看不出 30fps 以上的差别, 编码却是 CPU 大头。
        #   检测照常每帧跑(info/diag 每帧更新), 只有 JPEG 编码按 --stream-fps 节流。
        now = time.perf_counter()
        due = (st.stream_fps <= 0) or (now - st.last_enc_t >= 1.0 / st.stream_fps - 1e-4)
        if due:
            ok, jpg = cv2.imencode(".jpg", enc, [cv2.IMWRITE_JPEG_QUALITY, st.quality])
        else:
            ok = False
        with st.lock:
            st.info = {"found": bool(res.get("found")),
                       "cx": round(res.get("cx_px", -1), 1),
                       "cy": round(res.get("cy_px", -1), 1),
                       "r": round(res.get("r_outer_px", 0), 1),
                       "digit": res.get("digit"),
                       "score": round(res.get("digit_score", 0), 3),
                       "conf": round(res.get("conf", 0), 2),
                       "stale": res.get("stale", 0)}
            st.diag = diag
            if ok:
                st.jpg = jpg.tobytes()
                st.last_enc_t = now
                st.seq += 1
        if int(time.time() * 2) % 4 == 0:
            i = st.info
            print("[loop] fps=%.1f loop_ms=%.0f found=%s center=(%s,%s) r=%s digit=%s score=%s"
                  % (fps, (time.perf_counter() - t_loop) * 1000.0, i.get("found"),
                     i.get("cx"), i.get("cy"), i.get("r"), i.get("digit"), i.get("score")))


def synth_ring_scene(seed=0):
    """合成靶标(无相机时自测): 6 个同心环 + 中央数字 1/2/3 轮换。
    结果按 seed 缓存 —— 免得每帧重造图像拖慢帧率。"""
    cache = synth_ring_scene.__dict__.setdefault("_cache", {})
    if seed in cache:
        return cache[seed]
    w, h = 1280, 720
    img = np.full((h, w, 3), 235, np.uint8)
    cx, cy = w // 2, h // 2
    radii = [int(0.42 * h), int(0.34 * h), int(0.27 * h), int(0.21 * h), int(0.16 * h), int(0.11 * h)]
    for r in radii:
        cv2.circle(img, (cx, cy), r, (60, 60, 60), 2)
    ch = "123"[seed % 3]
    cv2.putText(img, ch, (cx - 18, cy + 22), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (40, 40, 40), 4)
    img = np.clip(img.astype(np.float32) + np.random.default_rng(seed).normal(0, 4, img.shape),
                  0, 255).astype(np.uint8)
    cache[seed] = img
    return img


# ----------------------------------------------------------------------
# 网页
# ----------------------------------------------------------------------
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>ring stream ui</title>
<style>
 body{margin:0;background:#111;color:#ddd;font:14px system-ui,sans-serif}
 #wrap{display:flex;flex-wrap:wrap}#left{flex:1 1 640px;padding:8px}#right{width:430px;padding:8px}
 img{width:100%;background:#000}.row{display:flex;align-items:center;margin:2px 0}
 .row label{width:150px;color:#9cf;flex:0 0 150px}
 .row input[type=range]{flex:1;min-width:80px}
 .row input[type=number]{width:72px;background:#222;color:#ffd;border:1px solid #444;padding:2px}
 .hint{color:#888;font-size:11px;margin-left:6px;flex:1 1 150px}
 .val{width:52px;text-align:right;color:#ffd;flex:0 0 52px}
 button{background:#2a6;color:#fff;border:0;padding:6px 10px;margin:2px;cursor:pointer}
 #info{white-space:pre-wrap;color:#8f8;font-family:monospace;font-size:12px;max-height:220px;overflow:auto}
 #msg{color:#ffd;font-size:12px;min-height:16px}
</style></head><body><div id="wrap">
 <div id="left"><img id="im" src="/stream"><div id="info">loading...</div></div>
 <div id="right"><div id="sliders"></div>
  <div class="row"><button onclick="go('/autotune')">自动估参</button>
   <button onclick="go('/template?label=1')">加模板1</button>
   <button onclick="go('/template?label=2')">加模板2</button>
   <button onclick="go('/template?label=3')">加模板3</button></div>
  <div class="row"><button onclick="go('/freeze')">冻结/解冻</button>
   <button onclick="go('/snapshot')">抓拍</button>
   <button onclick="go('/save')">保存档案</button>
   <button onclick="go('/load')">载入档案</button></div>
  <div class="row"><button onclick="go('/reload')">重读图片</button>
   <button onclick="go('/regress')">跑三套回归</button>
   <button onclick="go('/reset')">恢复默认</button></div>
  <div id="msg"></div></div></div>
<script>
const SPEC = __SPEC__;          // [{name,mn,mx,st,kind,odd,hint}, ...]
const S={}; const box=document.getElementById('sliders');
function frac(x){const s=String(x);const i=s.indexOf('.');return i<0?0:s.length-i-1;}
function build(sp){sp.forEach(p=>{
 const row=document.createElement('div');row.className='row';box.appendChild(row);
 const lb=document.createElement('label');lb.textContent=p.name;
 if(p.hint)lb.title=p.hint; row.appendChild(lb);
 const rng=document.createElement('input');rng.type='range';
 rng.min=p.mn;rng.max=p.mx;rng.step=p.st; row.appendChild(rng);
 const num=document.createElement('input');num.type='number';
 num.min=p.mn;num.max=p.mx;num.step=p.st; row.appendChild(num);
 const hint=document.createElement('span');hint.className='hint';
 hint.textContent=p.hint||''; row.appendChild(hint);
 function send(v){fetch('/set?'+p.name+'='+encodeURIComponent(v)).then(r=>r.json()).then(j=>{
   document.getElementById('msg').textContent=j.msg||'ok';
   if(j.value!==undefined){rng.value=j.value;num.value=j.value;}});}
 rng.oninput=()=>{num.value=rng.value;send(rng.value);};
 num.onchange=()=>{let v=parseFloat(num.value);
   if(isNaN(v)){document.getElementById('msg').textContent=p.name+' 需要数字('+p.kind+(p.odd?', 奇数':'')+')';return;}
   if(p.kind==='int'){v=Math.round(v); if(p.odd&&v%2===0){v+=1;}}
   const dec=(p.kind==='int')?0:Math.max(frac(p.st),1);
   v=parseFloat(v.toFixed(dec));
   num.value=v; rng.value=v; send(v);};
 S[p.name]={rng:rng,num:num};});}
build(SPEC);
function go(u){fetch(u).then(r=>r.json()).then(j=>{
 document.getElementById('msg').textContent=(j.msg||'ok');
 if(j.text)document.getElementById('info').textContent=j.text;
 load();});}
function load(){fetch('/state').then(r=>r.json()).then(j=>{
 for(const n in S){ if(j.params[n]!==undefined){
   if(document.activeElement!==S[n].num){S[n].num.value=j.params[n];}
   S[n].rng.value=j.params[n];}}
 document.getElementById('info').textContent=
  'info: '+JSON.stringify(j.info)+'\\nfps='+j.fps+'\\ndiag: '+JSON.stringify(j.diag,null,1);
 setTimeout(load,900);});}
load();
</script></body></html>"""


def make_page():
    spec = [dict(name=p["name"], mn=p["mn"], mx=p["mx"], st=p["st"],
                 kind=p["kind"], odd=bool(p["odd"]), hint=p.get("hint", ""))
            for p in PARAM_SPEC]
    return PAGE.replace("__SPEC__", json.dumps(spec, ensure_ascii=False))


class Handler(http.server.BaseHTTPRequestHandler):
    st = None

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, s, code=200):
        b = s.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        st = self.st
        path, _, query = self.path.partition("?")
        q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
        if path == "/":
            body = make_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            last = -1
            try:
                while True:
                    with st.lock:
                        jpg, seq = st.jpg, st.seq
                    if jpg is not None and seq != last:
                        last = seq
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        elif path == "/state":
            with st.lock:
                info, diag, seq = dict(st.info), dict(st.diag), st.seq
            self._json({"info": info, "diag": diag, "fps": round(st.fps, 1),
                        "params": st.params(), "seq": seq})
        elif path == "/set":
            msgs = []
            last_val = None
            for k, v in q.items():
                ok, val, msg = apply_param(st.cfg, k, v)
                if ok:
                    last_val = val
                    if msg != "ok":
                        msgs.append(msg)
                else:
                    msgs.append(msg)
            st.det.cal.update(st.cfg)              # ★检测器内部是拷贝, 必须同步
            # 曝光/白平衡立即作用到相机
            if st.cap is not None and ("exposure" in q or "lock_exposure" in q):
                try:
                    st.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE,
                               0.25 if st.cfg.get("lock_exposure") else 0.75)
                    if st.cfg.get("lock_exposure"):
                        st.cap.set(cv2.CAP_PROP_EXPOSURE,
                                   float(st.cfg.get("exposure", 156) or 156))
                except Exception:
                    pass
            self._json({"msg": "; ".join(msgs) if msgs else "set ok",
                        "value": last_val, "params": st.params()})
        elif path == "/freeze":
            st.frozen = not st.frozen
            self._json({"msg": "frozen=%s" % st.frozen})
        elif path == "/snapshot":
            os.makedirs(st.args.out, exist_ok=True)
            with st.lock:
                jpg = st.jpg
            if jpg is None:
                self._json({"msg": "no frame"}, 503)
            else:
                p = os.path.join(st.args.out, "ring_snap_%d.jpg" % int(time.time() % 100000))
                with open(p, "wb") as f:
                    f.write(jpg)
                self._json({"msg": "saved " + p})
        elif path == "/reload":
            st.image_cache = None          # 下一帧重新读盘
            self._json({"msg": "reload ok(单图模式会重新读文件)"})
        elif path == "/save":
            try:
                with open(st.args.profile, "w") as f:
                    json.dump(st.cfg, f, ensure_ascii=False, indent=2)
                self._json({"msg": "saved " + os.path.abspath(st.args.profile)})
            except Exception as e:
                self._json({"msg": "save fail %r" % e}, 500)
        elif path == "/load":
            try:
                with open(st.args.profile) as f:
                    st.cfg.update(json.load(f))
                st.det.cal.update(st.cfg)
                self._json({"msg": "loaded " + st.args.profile, "params": st.params()})
            except Exception as e:
                self._json({"msg": "load fail %r" % e}, 500)
        elif path == "/reset":
            st.cfg.clear()
            st.cfg.update(dict(ring.CALIB))
            st.det.cal.update(st.cfg)
            self._json({"msg": "reset to CALIB", "params": st.params()})
        elif path == "/autotune":
            self._json(self._autotune(st))
        elif path == "/template":
            self._json(self._template(st, q))
        elif path == "/regress":
            self._json(self._regress(st))
        else:
            self._json({"msg": "not found"}, 404)

    # ---------- 功能实现 ----------
    def _autotune(self, st):
        frame = st.last_frame
        if frame is None:
            return {"msg": "no frame yet"}
        scale = float(st.cfg.get("proc_scale", 1.0))
        small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) \
            if scale != 1.0 else frame
        pk = int(st.cfg.get("pre_blur", 1) or 1)
        if pk >= 3:
            small = cv2.GaussianBlur(small, (pk | 1, pk | 1), 0)
        r, bw, tim, spacing = ring_stage_timings(small, st.cfg)
        if r is None:
            return {"msg": "当前帧没检出圆环, 无法估参"}
        cx, cy, rad = r
        hs, ws = small.shape[:2]
        sug = {}
        reasons = []
        if spacing and spacing >= 1:
            ck = int(max(1, min(31, round(spacing))))     # ★允许偶数(10~15 精调)
            sug["close_k"] = ck
            reasons.append("环线间距≈%.1fpx(检测尺度) → close_k=%d" % (spacing, ck))
        area_frac = (np.pi * rad * rad) / float(ws * hs)
        maf = max(0.00001, min(0.003, area_frac * 0.4))
        sug["min_area_frac"] = round(maf, 5)
        reasons.append("最大环面积占比≈%.4f → min_area_frac≈%.5f" % (area_frac, maf))
        sug["center_gate_r"] = 0 if st.image_path else 200
        reasons.append("离线单图→gate=0; 实时→200(可按抖动调)")
        sug["digit_inner_frac"] = 0.80
        sug["inner_fallback_frac"] = 0.55
        if int(st.cfg.get("thr_block", 41)) <= 7 and int(st.cfg.get("pre_blur", 1) or 1) < 3:
            sug["pre_blur"] = 3
            reasons.append("thr_block≤7 且 pre_blur<3 → 建议 pre_blur=3 压噪")
        for k, v in sug.items():
            apply_param(st.cfg, k, v)
        st.det.cal.update(st.cfg)
        print("[autotune] " + "; ".join(reasons))
        return {"msg": "估参完成", "params": st.params(), "text": "\n".join(reasons)}

    def _template(self, st, q):
        label = q.get("label", "1")
        frame = st.last_frame
        res = st.last_det
        if frame is None or not res or not res.get("found"):
            return {"msg": "需要先检出圆环并稳定, 再按加模板"}
        cfg = st.cfg
        scale = float(cfg.get("proc_scale", 1.0))
        small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) \
            if scale != 1.0 else frame
        gs = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        ri = ring.find_inner_radius(gs, res["cx_px"] * scale, res["cy_px"] * scale,
                                    res["r_outer_px"] * scale, cfg)
        r_inner = (ri / scale) if ri else res["r_outer_px"] * cfg.get("inner_fallback_frac", 0.55)
        glyph, _ = glyph_from_frame(frame, res["cx_px"], res["cy_px"], r_inner, cfg)
        if glyph is None:
            return {"msg": "没取到有效字形(可调 digit_inner_frac / 让数字更清晰)"}
        p = dtpl.add_crop(label, glyph, custom_dir=st.args.custom_dir, verbose=False)
        n_samples = dtpl.count_samples(st.args.custom_dir)
        best, top1, top2, ntm = digit_scores(glyph, cfg)
        msg = ("已把当前字形加入 %s 号模板(%s); 样例=%d, 模板总数=%d, 本次各类分数=%s"
               % (label, os.path.basename(p), n_samples, ntm,
                  {k: round(v, 3) for k, v in best.items()}))
        print("[template] " + msg)
        return {"msg": msg}

    def _regress(self, st):
        out = []
        for t in ("test_rotation.py", "test_dashed.py", "test_stability.py"):
            if not os.path.exists(t):
                out.append("%s: 不存在" % t)
                continue
            try:
                r = subprocess.run([sys.executable, "-u", t], cwd=os.getcwd(),
                                   capture_output=True, text=True, timeout=240)
                tail = [ln for ln in r.stdout.strip().splitlines() if ln.strip()][-3:]
                out.append("%s rc=%d | %s" % (t, r.returncode, " / ".join(tail)))
            except subprocess.TimeoutExpired:
                out.append("%s: 超时" % t)
        print("[regress] " + " || ".join(out))
        return {"msg": "回归完成(见下方)", "text": "\n".join(out)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=0)
    ap.add_argument("--no-cam", action="store_true")
    ap.add_argument("--image", default=None, help="离线单图模式(现场照片/A4 靶标)")
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--width", type=int, default=None,
                    help="处理宽度(默认: 性能档, 相机 low/mid/high=640/960/1280; 单图=0不缩放)")
    ap.add_argument("--diag-every", type=int, default=None,
                    help="每 N 帧算一次完整诊断(默认: 性能档 5/3/1; 1=每帧, 更耗CPU)")
    ap.add_argument("--digit-rot-step", type=int, default=None,
                    help="数字模板旋转步长(默认用 CALIB 的5°; 15° 构建快3倍、精度略降)")
    ap.add_argument("--quality", type=int, default=None,
                    help="JPEG 质量(默认: 性能档 70/75/85)")
    ap.add_argument("--stream-width", type=int, default=None,
                    help="推流图像最长边(默认: 性能档 480/720/960; 编码是大头, 调小更快)")
    ap.add_argument("--stream-fps", type=float, default=None,
                    help="推流限帧(默认: 性能档 10/15/25; 0=不限, 检测仍每帧跑)")
    # 性能档 / 相机类型: 一台代码同时跑 Pi4 与 Jetson Orin; `auto` 自动识别
    ap.add_argument("--perf", default="auto", choices=["auto", "low", "mid", "high", "rdk"],
                    help="性能档(默认 auto: Jetson→high / RDK X5→rdk / 否则按核数); 显式参数优先")
    ap.add_argument("--gst", nargs="?", const="auto", default=None,
                    help="用 GStreamer 管线取流(Jetson CSI 摄像头需要); "
                         "`--gst`=auto 自动生成, `--gst none` 关闭, 也可直接给完整管线字符串")
    ap.add_argument("--sensor-id", type=int, default=0, help="CSI sensor-id(--gst auto 时用)")
    ap.add_argument("--cam-width", type=int, default=1280, help="相机采集宽(UVC 时用)")
    ap.add_argument("--cam-height", type=int, default=720, help="相机采集高(UVC 时用)")
    ap.add_argument("--profile", default="ring_calib.json")
    ap.add_argument("--custom-dir", default="digit_custom")
    ap.add_argument("--out", default="ring_ui_out")
    ap.add_argument("--exposure", type=float, default=None)
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--no-custom-templates", action="store_true",
                    help="不加载 digit_custom 里已存的现场数字模板")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    # 离线单图默认不缩放(宽=0, 原图才看得清细节);
    # --no-cam(合成靶标)仍然用性能档宽度 —— 让合成自测与真实相机走同一条路径,
    # 否则 bench_perf.sh 测出来的耗时与真机差一倍(RDK 上实测如此)。
    if args.image and args.width is None:
        args.width = 0
    perf_name, perf_applied = resolve_perf(args, args.perf)
    print("[perf] 档位=%s%s" % (perf_name,
                                ("  " + str(perf_applied)) if perf_applied else "  (参数已显式指定)"))

    cfg = dict(ring.CALIB)
    cfg.setdefault("pre_blur", 1)          # 新增滑条: 阈值前额外平滑(默认关)
    cfg.setdefault("exposure", 156)
    if args.image:
        cfg["center_gate_r"] = 0
    if args.exposure is not None:
        cfg["exposure"] = int(args.exposure)
    if args.digit_rot_step is not None:
        cfg["digit_rot_step"] = int(args.digit_rot_step)
    # ★性能档可顺带覆盖算法参数(弱算力平台用更省的组合):
    #   面板滑条仍然可以随时改回去, 这两个值只是"开机默认"
    _over = (PERF_PRESETS.get(perf_name) or {}).get("cfg") or {}
    if _over:
        cfg.update(_over)
        print("[perf] 算法参数按 %s 档覆盖: %s" % (perf_name, _over))

    def _build_templates():
        try:
            if not args.no_custom_templates:
                dtpl.install(args.custom_dir, rot_step=cfg.get("digit_rot_step", 5),
                             verbose=True)
            else:
                ring._build_rot_templates(int(cfg.get("digit_rot_step", 5)))
            st.tpl_ready = True
            print("[templates] ready", flush=True)
        except Exception as e:
            st.tpl_ready = True
            print("[templates] build fail: %r (继续跑, 数字识别可能不可用)" % e, flush=True)

    cap = None
    if not args.no_cam and not args.image:
        gst = args.gst
        if gst is not None and str(gst).lower() in ("auto", "1", "true"):
            gst = gst_pipeline(args.cam_width, args.cam_height, 30, args.sensor_id) if gst_pipeline else None
            print("[cam] GStreamer 管线: %s" % gst)
        elif gst is not None and str(gst).lower() in ("none", "0", "false", ""):
            gst = None
        if open_camera:
            # ★只在显式给了 --exposure 时才锁曝光：默认值(156)是树莓派那台相机的，
            #   换相机后可能直接过曝（RDK 上那台 LRCP G720P 实测一片惨白）→ 不给就保持自动
            _exp = args.exposure if args.exposure is not None else None
            cap = open_camera(args.cam, lock=not args.no_lock,
                              exposure=_exp, gst=gst,
                              size=(args.cam_width, args.cam_height))
            if cap is None and gst:
                print("[cam] CSI 管线失败, 回退 V4L2(UVC)方式再试一次 ...")
                gst = None
                cap = open_camera(args.cam, lock=not args.no_lock,
                                  exposure=_exp,
                                  size=(args.cam_width, args.cam_height))
        if cap is None:
            print("[warn] no camera -> synthetic target scenes")
    st = State(cfg, args, cap, args.image)
    st.diag_every = max(1, int(args.diag_every))
    Handler.st = st

    try:
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    except OSError as e:
        print("[err] 端口 %d 起不来: %r" % (args.port, e))
        print("      多半是上一个实例还在跑: ps -eo pid,args | grep ring_stream_ui"
              " 然后 kill <PID>; 或换 --port")
        return 3
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def ip():
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            v = s.getsockname()[0]
            s.close()
            return v
        except Exception:
            return "127.0.0.1"
    src = args.image if args.image else ("/dev/video%d" % args.cam if cap else "synthetic")
    print("[web] open http://%s:%d/   source=%s  width=%d"
          % (ip(), args.port, src, args.width))
    print("[start] digit templates building in background (这一步 ~2-7s) ...", flush=True)
    threading.Thread(target=_build_templates, daemon=True).start()
    try:
        with open("/tmp/ring_stream_ui.pid", "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    if cap is not None:
        threading.Thread(target=capture_loop, args=(st,), daemon=True).start()
    try:
        process_loop(st)
    except KeyboardInterrupt:
        pass
    finally:
        st.running = False
        time.sleep(0.2)
        if cap is not None:
            cap.release()
        print("[bye] stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
