# -*- coding: utf-8 -*-
"""实时循环分阶段计时: read / decode(0.5) / decode(1.0) / encode。"""
import sys
import time
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import qr_stream_demo as m

cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

t_read, t_half, t_full, t_enc = [], [], [], []
for i in range(20):
    t0 = time.perf_counter()
    ok, frame = cap.read()
    t_read.append((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    r1 = m.decode_qr(frame, 0.5)
    t_half.append((time.perf_counter() - t0) * 1000)
    if not r1 and i % 4 == 0:
        t0 = time.perf_counter()
        m.decode_qr(frame, 1.0)
        t_full.append((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    okj, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    t_enc.append((time.perf_counter() - t0) * 1000)

print('read   : avg=%.0fms' % np.mean(t_read))
print('decode0.5: avg=%.0fms (n=%d)' % (np.mean(t_half), len(t_half)))
print('decode1.0: avg=%.0fms (n=%d)' % (np.mean(t_full), len(t_full)))
print('encode : avg=%.0fms' % np.mean(t_enc))
print('合计   : %.0fms -> 预计fps=%.1f' % (np.mean(t_read) + np.mean(t_half) + np.mean(t_enc),
       1000 / (np.mean(t_read) + np.mean(t_half) + np.mean(t_enc))))
cap.release()
