# -*- coding: utf-8 -*-
"""诊断: 观察字形 vs 模板库 的内部数据。"""
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import importlib
import ring_center_detect_v2 as m
importlib.reload(m)

from test_rotation import make_target

img = make_target('2', 0)
det = m.RingDetector(m.CALIB)
res = det.process(img)
print('res:', {k: (round(v, 2) if isinstance(v, float) else v)
               for k, v in res.items() if k != 'timing'})

# 手动跑 recognize_digit 的中间步骤
frame = img
gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
cx, cy, r_out = res['cx_px'], res['cy_px'], res['r_outer_px']
side = max(int(2 * r_out * m.CALIB['digit_crop_frac']), 24)
x0 = max(0, int(cx) - side // 2)
y0 = max(0, int(cy) - side // 2)
crop = gray[y0:y0 + side, x0:x0 + side]
print('side:', side, 'crop mean:', round(crop.mean(), 1))
bw = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                           cv2.THRESH_BINARY_INV, 15, 5)
bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                      cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
print('bw nonzero frac:', round(np.count_nonzero(bw) / bw.size, 4))
cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
print('contours:', len(cnts))
chh, cww = bw.shape
for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:5]:
    x, y, w, h = cv2.boundingRect(c)
    print(f'  bbox=({x},{y},{w},{h}) area={cv2.contourArea(c):.0f} '
          f'center_off=({x+w/2-cww/2:.0f},{y+h/2-chh/2:.0f})')

# 模板库
labels, T = m._build_rot_templates(5)
print('template count:', T.shape, 'labels sample:', labels[:6])
g = m._glyph_tensor(bw[y:y2, x:x2] if False else bw[50:150, 50:150], 28).flatten()
print('g norm:', round(np.linalg.norm(g), 3), 'g mean:', round(g.mean(), 4))
scores = T @ g
print('scores: min=%.3f max=%.3f' % (scores.min(), scores.max()))
for ch in '123':
    idx = [i for i, lab in enumerate(labels) if lab == ch]
    print(f'  char {ch}: best=%.3f' % scores[idx].max())
