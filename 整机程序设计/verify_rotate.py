#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 rotate_ccw: 逆时针旋转90°后, 像素坐标按 (x,y)->(y, W-1-x) 跟随画面。

验证方法(在 Pi 上跑):
  1) ring: 同一标靶图, 原图 vs 手工 cv.rotate 后, 各自独立 RingDetector 检测,
     断言旋转后环心 == (cy0, W-1-cx0), 半径不变。
  2) qr:   同一二维码图, 原图 vs 旋转后 decode_qr,
     断言旋转后角点 == (y, W-1-x) 映射。
"""
import os
import sys

os.chdir("/home/justin/vision_robot")
import cv2 as cv
import numpy as np

sys.path.insert(0, ".")
from ring_center_detect_v2 import CALIB, RingDetector, rotate_ccw
from qr_stream_demo import CFG, decode_qr, rotate_ccw as qr_rotate

PASS = True

def check(name, cond, detail=""):
    global PASS
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        PASS = False

# ============ 1) ring: 环心坐标映射 ============
img = "工创赛标靶A4(13).png"
frame = cv.imread(img)
H, W = frame.shape[:2]
cfg_a = dict(CALIB); cfg_a["rotate_ccw"] = False; cfg_a["center_gate_r"] = 0
cfg_b = dict(CALIB); cfg_b["rotate_ccw"] = True;  cfg_b["center_gate_r"] = 0

ra = RingDetector(cfg_a).process(frame)          # 原图检测
rb = RingDetector(cfg_b).process(rotate_ccw(frame, cfg_b))  # 旋转后检测(走同一函数)
print(f"orig size={W}x{H} found={ra['found']} center=({ra['cx_px']:.1f},{ra['cy_px']:.1f}) r={ra['r_outer_px']:.1f}")
print(f"rot  size={H}x{W} found={rb['found']} center=({rb['cx_px']:.1f},{rb['cy_px']:.1f}) r={rb['r_outer_px']:.1f}")
if ra["found"] and rb["found"]:
    exp_x, exp_y = ra["cy_px"], (W - 1) - ra["cx_px"]   # 逆时针90°坐标映射
    check("ring 环心映射", abs(rb["cx_px"] - exp_x) < 2 and abs(rb["cy_px"] - exp_y) < 2,
          f"expect=({exp_x:.1f},{exp_y:.1f})")
    check("ring 半径不变", abs(rb["r_outer_px"] - ra["r_outer_px"]) < 2,
          f"r_orig={ra['r_outer_px']:.1f} r_rot={rb['r_outer_px']:.1f}")
    check("ring 数字一致", rb["digit"] == ra["digit"], f"digit={ra['digit']}/{rb['digit']}")
else:
    check("ring 均检出", False, "原图或旋转后未检出")

# ============ 2) qr: 角点坐标映射 ============
qimg = "test_qr.png"
q = cv.imread(qimg)
qh, qw = q.shape[:2]
cfgq = dict(CFG)
res_a = decode_qr(q, 1.0)
res_b = decode_qr(qr_rotate(q, cfgq), 1.0)
print(f"qr orig size={qw}x{qh} data={[d for d,_ in res_a]}")
print(f"qr rot  size={qh}x{qw} data={[d for d,_ in res_b]}")
if res_a and res_b:
    d_a, pts_a = res_a[0]
    d_b, pts_b = res_b[0]
    check("qr 内容一致", d_a == d_b, f"'{d_a}' vs '{d_b}'")
    # 每个角点: (x,y) -> (y, qw-1-x)
    exp_pts = np.stack([np.array([y, (qw - 1) - x]) for x, y in pts_a])
    err = np.abs(pts_b - exp_pts).max()
    check("qr 角点映射", err < 2, f"max_err={err:.2f}px")
    check("qr 角点数一致", len(pts_a) == len(pts_b))
else:
    check("qr 均检出", False, "原图或旋转后未检出")

print("\n" + ("ALL ROTATE TESTS PASSED" if PASS else "SOME TESTS FAILED"))
sys.exit(0 if PASS else 1)
