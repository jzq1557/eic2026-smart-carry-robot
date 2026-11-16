# -*- coding: utf-8 -*-
"""真实感测试: 二维码以中等尺寸置于720p画面, 各缩放档解码正确性+耗时。"""
import sys
import time
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import qr_stream_demo as m

CODE = '156+123+516+231'
qr = cv2.imread('test_qr.png')          # 420x420

for qr_px in (200, 300, 400):           # 二维码在画面中的边长
    frame = np.full((720, 1280, 3), 255, np.uint8)
    s = cv2.resize(qr, (qr_px, qr_px), interpolation=cv2.INTER_AREA)
    x0, y0 = (1280 - qr_px) // 2, (720 - qr_px) // 2
    frame[y0:y0 + qr_px, x0:x0 + qr_px] = s
    line = f'QR={qr_px}px: '
    for scale in (1.0, 0.75, 0.5, 0.33):
        times = []
        good = 0
        for _ in range(6):
            t0 = time.perf_counter()
            res = m.decode_qr(frame, scale)
            times.append((time.perf_counter() - t0) * 1000)
            if any(d == CODE for d, _ in res):
                good += 1
        line += f'x{scale}={np.mean(times):.0f}ms/{"OK" if good == 6 else f"{good}/6"}  '
    print(line)
