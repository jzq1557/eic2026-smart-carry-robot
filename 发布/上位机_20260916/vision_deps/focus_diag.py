#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对焦能力终诊: 摄像头空闲时抓帧, 测清晰度; 尝试所有对焦控制通道。
输出结论: 该摄像头 UVC 是否支持软件调焦。"""
import time
import cv2
import numpy as np

def sharpness(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(g, cv2.CV_64F).var()

cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
if not cap.isOpened():
    print("FATAL: cannot open camera (可能被占用)")
    raise SystemExit(1)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
time.sleep(0.5)

def grab():
    for _ in range(6):
        ok, f = cap.read()
    return f

print("== 1) 当前画面清晰度(3帧) ==")
for i in range(3):
    f = grab()
    if f is None:
        print(f"frame {i}: read fail"); continue
    h, w = f.shape[:2]
    full = sharpness(f)
    mid = sharpness(f[h//3:2*h//3, w//3:2*w//3])
    print(f"frame {i}: size={w}x{h} sharp_full={full:7.1f} sharp_mid={mid:7.1f}")

print("\n== 2) 对焦控制通道尝试 ==")
# OpenCV 属性
for prop, name in ((cv2.CAP_PROP_AUTOFOCUS, "AUTOFOCUS"), (cv2.CAP_PROP_FOCUS, "FOCUS")):
    v = cap.get(prop)
    ok = cap.set(prop, 0.0)
    v2 = cap.get(prop)
    print(f"{name}: get={v} set(0)={ok} get_after={v2}")

# 枚举所有支持的属性
print("\n== 3) 相关 CAP_PROP 枚举 ==")
for prop, name in ((cv2.CAP_PROP_BRIGHTNESS, "BRIGHTNESS"),
                   (cv2.CAP_PROP_CONTRAST, "CONTRAST"),
                   (cv2.CAP_PROP_SATURATION, "SATURATION"),
                   (cv2.CAP_PROP_GAIN, "GAIN"),
                   (cv2.CAP_PROP_EXPOSURE, "EXPOSURE"),
                   (cv2.CAP_PROP_ZOOM, "ZOOM"),
                   (cv2.CAP_PROP_PAN, "PAN"),
                   (cv2.CAP_PROP_TILT, "TILT")):
    v = cap.get(prop)
    print(f"{name}: {v}")

cap.release()
print("\nDONE")
