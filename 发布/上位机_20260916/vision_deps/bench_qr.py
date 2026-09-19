# -*- coding: utf-8 -*-
"""基准测试: 不同分辨率/缩放下的 QR 解码耗时与正确性。"""
import sys
import time
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import qr_stream_demo as m

CODE = '156+123+516+231'
small = cv2.imread('test_qr.png')

for label, w, h in [('VGA 640x480', 640, 480), ('HD 1280x720', 1280, 720)]:
    frame = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    print(f'--- 模拟{label} ---')
    for scale in (1.0, 0.75, 0.5, 0.33):
        ok_cnt = 0
        times = []
        for _ in range(8):
            t0 = time.perf_counter()
            res = m.decode_qr(frame, scale)
            times.append((time.perf_counter() - t0) * 1000)
            if any(d == CODE for d, _ in res):
                ok_cnt += 1
        print(f'  scale={scale}: avg={np.mean(times):.0f}ms '
              f'fps≈{1000/np.mean(times):.1f} 解码正确率={ok_cnt}/8')

# 检测器精度参数影响
print('--- eps 参数(scale=0.5) ---')
det = cv2.QRCodeDetector()
for eps in (0.2, 0.3, 0.4):
    det.setEpsX(eps)
    det.setEpsY(eps)
    frame = cv2.resize(small, (1280, 720), interpolation=cv2.INTER_NEAREST)
    t0 = time.perf_counter()
    ok, infos, pts, _ = det.detectAndDecodeMulti(frame)
    dt = (time.perf_counter() - t0) * 1000
    good = ok and any(d == CODE for d in infos)
    print(f'  eps={eps}: {dt:.0f}ms 正确={good}')
