#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cam_probe.py —— 抓一帧相机画面并报告统计(不入库,临时诊断用)"""
import sys
import time
import cv2
import numpy as np

idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
if not cap.isOpened():
    print("open fail video%d" % idx)
    sys.exit(1)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
for _ in range(5):          # 丢弃前几帧,等曝光稳定
    cap.read()
    time.sleep(0.05)
ok, frame = cap.read()
cap.release()
if not ok:
    print("read fail")
    sys.exit(1)
gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
print("frame %dx%d  mean=%.1f std=%.1f  中央128x128亮度=%.1f"
      % (frame.shape[1], frame.shape[0], gray.mean(), gray.std(),
         gray[296:424, 576:704].mean()))
# 主色(粗量化统计)
q = (frame // 32 * 32).reshape(-1, 3)
uniq, cnt = np.unique(q, axis=0, return_counts=True)
order = np.argsort(-cnt)[:6]
print("主色(量化到32级, BGR -> 占比):")
for i in order:
    b, g, r = uniq[i]
    print("   BGR(%3d,%3d,%3d) %5.1f%%" % (b, g, r, 100.0 * cnt[i] / len(q)))
hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
print("HSV 中位: H=%d S=%d V=%d" % tuple(np.median(hsv.reshape(-1, 3), axis=0).astype(int)))
out = "/home/justin/vision_robot/color_demo_out/cam_frame.jpg"
cv2.imwrite(out, frame)
print("已保存:", out)
