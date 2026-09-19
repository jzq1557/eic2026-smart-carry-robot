# -*- coding: utf-8 -*-
"""对当前 qr_stream_demo 主循环逐段计时(复刻实际循环)。"""
import sys
import time
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import qr_stream_demo as m

cfg = dict(m.CFG)
cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg['width'])
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg['height'])
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

t_read, t_dec, t_draw, t_enc = [], [], [], []
detect_every = max(1, int(cfg.get('detect_every', 2)))
frame_no = 0
last_qrs = []
for i in range(24):
    t0 = time.perf_counter()
    ok, frame = cap.read()
    t_read.append((time.perf_counter() - t0) * 1000)
    frame_no += 1
    if frame_no % detect_every == 0:
        t0 = time.perf_counter()
        qrs = m.decode_qr(frame, cfg.get('qr_scale', 0.5))
        if not qrs and frame_no % (detect_every * 8) == 0:
            m.decode_qr(frame, 1.0)
        t_dec.append((time.perf_counter() - t0) * 1000)
        last_qrs = qrs
    else:
        qrs = last_qrs
    t0 = time.perf_counter()
    for data, pts in qrs:
        cv2.polylines(frame, [pts.astype(int)], True, (0, 255, 0), 2)
    cv2.putText(frame, 'QR test', (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (0, 255, 255), 1)
    t_draw.append((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    okj, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    t_enc.append((time.perf_counter() - t0) * 1000)

def avg(a):
    return sum(a) / len(a) if a else 0

print('read  : %.0fms' % avg(t_read))
print('decode: %.0fms (每%d帧, 均值%.0fms/帧)' % (avg(t_dec), detect_every, avg(t_dec) / detect_every))
print('draw  : %.0fms' % avg(t_draw))
print('encode: %.0fms' % avg(t_enc))
per = avg(t_read) + avg(t_dec) / detect_every + avg(t_draw) + avg(t_enc)
print('循环合计 ≈ %.0fms -> 预计fps=%.1f' % (per, 1000 / per))
cap.release()
