# -*- coding: utf-8 -*-
"""阈值定标: 无数字 / 非1-2-3数字 的基线分数, 决定 digit_thr。"""
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import ring_center_detect_v2 as m
from test_rotation import make_target


def _radii():
    dia = [54.7 + 3, 54.7 + 3 + 5, 54.7 + 3 + 5 + 7, 54.7 + 3 + 5 + 7 + 10,
           54.7 + 3 + 5 + 7 + 10 + 10, 54.7 + 3 + 5 + 7 + 10 + 10 + 10]
    return np.array(dia) / 2.0 * 3.878


def make_white_blob():
    img = np.full((520, 520, 3), 255, np.uint8)
    for r in _radii():
        cv2.circle(img, (260, 260), int(r), (0, 0, 0), 6)
    return img


print('--- 无数字(纯环) ---')
det = m.RingDetector(m.CALIB)
res = det.process(make_white_blob())
print(f'digit={res["digit"]} score={res["digit_score"]:.3f}')

print('--- 非 1/2/3 数字(4,5,6,7,8,9,0) ---')
for ch in '4567890':
    det = m.RingDetector(m.CALIB)
    res = det.process(make_target(ch, 0))
    print(f'digit={ch}: recognized={res["digit"]} score={res["digit_score"]:.3f}')

