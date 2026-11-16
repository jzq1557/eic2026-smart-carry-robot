#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""find_cam.py —— 枚举可打开的摄像头与能力"""
import cv2

for i in range(0, 4):
    cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
    if cap.isOpened():
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"video{i}: OPEN res={w}x{h}")
        cap.release()
    else:
        print(f"video{i}: cannot open")
