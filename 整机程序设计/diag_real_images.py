#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断: 为什么真实标靶图/测试码识别失败。"""
import os
import sys

os.chdir("/home/justin/vision_robot")
import cv2 as cv
import numpy as np

sys.path.insert(0, ".")
from ring_center_detect_v2 import CALIB, find_center, find_inner_radius

for img in ("工创赛标靶A4(12).png", "工创赛标靶A4(13).png"):
    print(f"\n===== {img} =====")
    frame = cv.imread(img)
    if frame is None:
        print("cannot read"); continue
    h, w = frame.shape[:2]
    print(f"size={w}x{h}")
    gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
    print(f"gray mean={gray.mean():.1f} min={gray.min()} max={gray.max()} std={gray.std():.1f}")
    # 看自适应二值化后的白色像素占比
    bw = cv.adaptiveThreshold(gray, 255, cv.ADAPTIVE_THRESH_GAUSSIAN_C,
                              cv.THRESH_BINARY_INV, CALIB["thr_block"], CALIB["thr_c"])
    k = cv.getStructuringElement(cv.MORPH_ELLIPSE, (CALIB["close_k"], CALIB["close_k"]))
    bw2 = cv.morphologyEx(bw, cv.MORPH_CLOSE, k)
    for name, im in (("bw", bw), ("bw_close", bw2)):
        white = (im > 0).sum()
        print(f"{name}: white={white} ({100*white/im.size:.2f}%)")
    cnts, _ = cv.findContours(bw2, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    areas = sorted((cv.contourArea(c) for c in cnts), reverse=True)[:5]
    print(f"top5 contour areas: {[int(a) for a in areas]}")
    min_area = CALIB["min_area_frac"] * w * h
    print(f"min_area_frac threshold={min_area:.0f}")
    r = find_center(frame, CALIB)
    print(f"find_center -> {r}")
    if r:
        cx, cy, ro = r
        phi1 = find_inner_radius(gray, cx, cy, ro, CALIB)
        print(f"find_inner_radius -> {phi1}")

print("\n===== test_qr.png =====")
qr = cv.imread("test_qr.png")
print(f"size={qr.shape[1]}x{qr.shape[0]}" if qr is not None else "cannot read")
if qr is not None:
    g = cv.cvtColor(qr, cv.COLOR_BGR2GRAY)
    print(f"gray mean={g.mean():.1f} min={g.min()} max={g.max()}")
    d = cv.QRCodeDetector()
    for scale, name in ((1.0, "full"), (0.5, "half")):
        im = qr if scale == 1.0 else cv.resize(qr, None, fx=scale, fy=scale)
        ok, data, pts, _ = d.detectAndDecodeMulti(im)
        print(f"detectAndDecodeMulti scale={name}: ok={ok} data={data!r}")
    ok1, data1, _, _ = d.detectAndDecode(qr)
    print(f"detectAndDecode full: ok={ok1} data={data1!r}")
