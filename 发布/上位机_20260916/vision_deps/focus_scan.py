#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测摄像头对焦控制能力 + 对焦扫描(找 20cm 处最清晰的手动对焦值)。"""
import os
import sys
import time

import cv2
import numpy as np

def sharpness(img):
    """拉普拉斯方差: 无参考清晰度指标, 越大越清晰。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(g, cv2.CV_64F).var()

cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

def grab(n=5):
    for _ in range(n):
        ok, f = cap.read()
    return f

print("== 探测对焦控制 ==")
for prop, name in ((cv2.CAP_PROP_AUTOFOCUS, "CAP_PROP_AUTOFOCUS"),
                   (cv2.CAP_PROP_FOCUS, "CAP_PROP_FOCUS")):
    v = cap.get(prop)
    print(f"{name} get -> {v}")

print("\n== 尝试关自动对焦 ==")
ok = cap.set(cv2.CAP_PROP_AUTOFOCUS, 0.0)
print(f"set AUTOFOCUS=0 -> ok={ok} now={cap.get(cv2.CAP_PROP_AUTOFOCUS)}")

print("\n== 尝试读当前 FOCUS ==")
v = cap.get(cv2.CAP_PROP_FOCUS)
print(f"FOCUS get -> {v}")

print("\n== 扫描 FOCUS 0..255 (每步抓帧评估清晰度, 测中区) ==")
results = []
for foc in range(0, 256, 5):
    cap.set(cv2.CAP_PROP_FOCUS, float(foc))
    time.sleep(0.08)          # 给驱动一点时间
    f = grab(3)
    if f is None:
        print(f"focus={foc}: read fail")
        continue
    h, w = f.shape[:2]
    mid = f[int(h*0.3):int(h*0.7), int(w*0.2):int(w*0.8)]  # 画面中部(地面应在中下)
    s = sharpness(mid)
    results.append((s, foc))
    print(f"focus={foc:3d} sharpness(mid)={s:8.1f}")

results.sort(reverse=True)
print("\n== TOP5 清晰 focus 值 ==")
for s, foc in results[:5]:
    print(f"focus={foc} sharpness={s:.1f}")

# 回到扫描前状态: 恢复自动
cap.set(cv2.CAP_PROP_AUTOFOCUS, 1.0)
cap.release()
print("\nDONE (已恢复自动对焦)")
