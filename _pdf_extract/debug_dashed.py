# -*- coding: utf-8 -*-
"""调试: 虚线环下 2@0° 与 3@180° 识别失败原因。"""
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import ring_center_detect_v2 as m
from test_dashed import make_dashed_target

for ch, ang in [('2', 0), ('3', 180), ('2', 60), ('1', 0)]:
    img = make_dashed_target(ch, ang, dashed_rings=(2, 5))
    det = m.RingDetector(m.CALIB)
    res = det.process(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ri = m.find_inner_radius(gray, res['cx_px'], res['cy_px'],
                             res['r_outer_px'], m.CALIB)
    print('--- digit=%s ang=%d: r_out=%.0f phi1=%s got=%s' %
          (ch, ang, res['r_outer_px'], ri, res['digit']))
    r_inner = ri if ri else res['r_outer_px'] * 0.55
    side = max(int(2 * r_inner * m.CALIB.get('digit_inner_frac', 0.80)), 24)
    x0 = max(0, int(res['cx_px']) - side // 2)
    y0 = max(0, int(res['cy_px']) - side // 2)
    crop = gray[y0:y0 + side, x0:x0 + side]
    bw = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 15, 5)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print('  side=%d crop_mean=%.0f bw_nonzero=%d contours=%d' %
          (side, crop.mean(), np.count_nonzero(bw), len(cnts)))
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:4]:
        print('   bbox=%s' % (cv2.boundingRect(c),))
