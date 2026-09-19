# -*- coding: utf-8 -*-
"""旋转鲁棒性测试: 数字 1/2/3 在 0-330° 任意角度下的识别。"""
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import ring_center_detect_v2 as m

CANVAS = 520
CXC, CYC = 260, 260


def make_target(digit, angle, line_w=6):
    img = np.full((CANVAS, CANVAS, 3), 255, np.uint8)
    for r in _radii():
        cv2.circle(img, (CXC, CYC), int(r), (0, 0, 0), line_w)
    # 在中心渲染旋转后的数字
    sub = np.zeros((200, 200), np.uint8)
    cv2.putText(sub, digit, (20, 130), cv2.FONT_HERSHEY_SIMPLEX, 2.4, 255, 3, cv2.LINE_AA)
    M = cv2.getRotationMatrix2D((100, 100), angle, 1.0)
    rot = cv2.warpAffine(sub, M, (200, 200), flags=cv2.INTER_LINEAR, borderValue=0)
    nz = cv2.findNonZero(rot)
    if nz is None:
        return img
    x, y, w, h = cv2.boundingRect(nz)
    glyph = rot[y:y + h, x:x + w]
    chh, cww = glyph.shape
    s = max(chh, cww)
    sq = np.zeros((s, s), np.uint8)
    sq[(s - chh) // 2:(s - chh) // 2 + chh, (s - cww) // 2:(s - cww) // 2 + cww] = glyph
    sq = cv2.resize(sq, (120, 120), interpolation=cv2.INTER_AREA)
    # 放到环心(内环半径≈112px, 数字直径120px 略超, 用100px)
    sq = cv2.resize(sq, (100, 100), interpolation=cv2.INTER_AREA)
    x0 = CXC - 50
    y0 = CYC - 50
    dark = (sq > 0)
    img[y0:y0 + 100, x0:x0 + 100][dark] = (0, 0, 0)
    return img


def _radii():
    """φ=54.7mm 时表3六个环外径(φ+3, +5, +7, +10, +10, +10) 的半径px(px_per_mm=3.878)。"""
    dia = [54.7 + 3, 54.7 + 3 + 5, 54.7 + 3 + 5 + 7, 54.7 + 3 + 5 + 7 + 10,
           54.7 + 3 + 5 + 7 + 10 + 10, 54.7 + 3 + 5 + 7 + 10 + 10 + 10]
    return np.array(dia) / 2.0 * 3.878


def main():
    ok = 0
    total = 0
    for ch in '123':
        for ang in range(0, 360, 30):
            img = make_target(ch, ang)
            det = m.RingDetector(m.CALIB)
            res = det.process(img)
            got = res.get('digit')
            total += 1
            good = (got == ch)
            ok += good
            mark = 'OK' if good else f'<<< got={got}'
            print(f'digit={ch} ang={ang:3d}°: found={res["found"]} '
                  f'recognized={got} score={res.get("digit_score", 0):.2f} {mark}')
    print(f'RESULT: {ok}/{total} correct')
    sys.exit(0 if ok == total else 1)


if __name__ == '__main__':
    main()
