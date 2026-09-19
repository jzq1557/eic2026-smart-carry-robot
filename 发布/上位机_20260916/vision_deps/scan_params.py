# -*- coding: utf-8 -*-
"""参数扫描: 找合成靶标下能稳定检出环的参数组合。"""
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import ring_center_detect_v2 as m


def make_target(digit=None, line_w=6):
    img = np.full((520, 520, 3), 255, np.uint8)
    for r in m.ring_radii_mm(m.CALIB['material_dia_mm']) * m.CALIB['px_per_mm']:
        cv2.circle(img, (260, 260), int(r), (0, 0, 0), line_w)
    if digit:
        (tw, th), _ = cv2.getTextSize(digit, cv2.FONT_HERSHEY_SIMPLEX, 1.8, 3)
        cv2.putText(img, digit, (260 - tw // 2, 260 + th // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 3, cv2.LINE_AA)
    return img


def main():
    img = make_target('3')
    print('scanning param2 x proc_scale x hough_dp ...')
    for proc_scale in (1.0, 0.5):
        for hough_dp in (1.0, 1.5, 2.0):
            for param2 in (25, 30, 35, 40, 50):
                cal = dict(m.CALIB)
                cal['proc_scale'] = proc_scale
                cal['hough_dp'] = hough_dp
                cal['param2'] = param2
                det = m.RingDetector(cal)
                res = det.process(img)
                tag = 'FOUND' if res['found'] else '-----'
                print(f'ps={proc_scale} dp={hough_dp} p2={param2:>2}: '
                      f'{tag} rings={res.get("n_rings")} digit={res.get("digit")} '
                      f'score={res.get("digit_score", 0):.2f}')
    # 也扫 min_rings 放宽
    print('\nmin_rings=1:')
    for param2 in (30, 40, 50):
        cal = dict(m.CALIB)
        cal['param2'] = param2
        cal['min_rings'] = 1
        det = m.RingDetector(cal)
        res = det.process(img)
        print(f'p2={param2}: found={res["found"]} rings={res.get("n_rings")} '
              f'digit={res.get("digit")} score={res.get("digit_score", 0):.2f}')


if __name__ == '__main__':
    main()
