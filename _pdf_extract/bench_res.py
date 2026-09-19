# -*- coding: utf-8 -*-
"""顺序测量各分辨率: 纯读取fps + 半分辨率解码耗时。"""
import sys
import time
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import qr_stream_demo as m


def measure(w, h):
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    frame = None
    ts = []
    for i in range(15):
        t0 = time.perf_counter()
        ok, f = cap.read()
        ts.append((time.perf_counter() - t0) * 1000)
        if ok and frame is None:
            frame = f
    read_avg = sum(ts[3:]) / len(ts[3:])
    dec = 0.0
    if frame is not None:
        t0 = time.perf_counter()
        m.decode_qr(frame, 0.5)
        dec = (time.perf_counter() - t0) * 1000
    cap.release()
    time.sleep(1)
    print('%dx%d: 纯读取 %.0fms (~%.0ffps)  实际尺寸 %s  decode0.5=%.0fms' %
          (w, h, read_avg, 1000 / read_avg,
           ('%dx%d' % frame.shape[1::-1] if frame is not None else 'N/A'), dec))


measure(640, 480)
measure(960, 540)
measure(1280, 720)
