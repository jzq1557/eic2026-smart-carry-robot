# -*- coding: utf-8 -*-
"""虚线环测试: 用户报告"虚线环把虚线段误识别为1, 2/3难识别"。
验证: 1) find_inner_radius 在虚线环下仍能找出最内环φ1
      2) 裁剪严格在φ1内 -> 虚线段进不了裁剪区 -> 1/2/3 识别正常
"""
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import ring_center_detect_v2 as m
from test_rotation import _radii, CXC, CYC


def make_dashed_target(digit=None, angle=0, dashed_rings=(2, 5),
                       dash=16, gap=14, line_w=6):
    """部分环虚线: dashed_rings 指定哪些环(1~6)画成虚线, 其余实线; 中心可选数字。
    (贴合现场"个别圆环有较多虚线"的情况: 环检测正常, 但虚线碎片易被误识别)"""
    img = np.full((520, 520, 3), 255, np.uint8)
    for i, r in enumerate(_radii(), start=1):
        if i in dashed_rings:
            a = 0
            while a < 360:
                cv2.ellipse(img, (CXC, CYC), (int(r), int(r)), 0,
                            a, min(a + dash, 360), (0, 0, 0), line_w)
                a += dash + gap
        else:
            cv2.circle(img, (CXC, CYC), int(r), (0, 0, 0), line_w)
    if digit:
        sub = np.zeros((200, 200), np.uint8)
        cv2.putText(sub, digit, (20, 130), cv2.FONT_HERSHEY_SIMPLEX,
                    2.4, 255, 3, cv2.LINE_AA)
        M = cv2.getRotationMatrix2D((100, 100), angle, 1.0)
        rot = cv2.warpAffine(sub, M, (200, 200), flags=cv2.INTER_LINEAR,
                             borderValue=0)
        nz = cv2.findNonZero(rot)
        x, y, w, h = cv2.boundingRect(nz)
        glyph = rot[y:y + h, x:x + w]
        chh, cww = glyph.shape
        s = max(chh, cww)
        sq = np.zeros((s, s), np.uint8)
        sq[(s - chh) // 2:(s - chh) // 2 + chh,
           (s - cww) // 2:(s - cww) // 2 + cww] = glyph
        sq = cv2.resize(sq, (100, 100), interpolation=cv2.INTER_AREA)
        x0, y0 = CXC - 50, CYC - 50
        img[y0:y0 + 100, x0:x0 + 100][sq > 0] = (0, 0, 0)
    return img


def main():
    cases = [
        ('个别环虚线(φ2,φ5)', (2, 5)),
        ('最内环虚线(φ1)', (1,)),
    ]
    all_ok = True
    for name, dashed in cases:
        # 无数字: 环检测 + φ1 估计
        img = make_dashed_target(None, 0, dashed_rings=dashed)
        det = m.RingDetector(m.CALIB)
        res = det.process(img)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        ri = m.find_inner_radius(gray, res['cx_px'], res['cy_px'],
                                 res['r_outer_px'], m.CALIB)
        ok_det = res['found'] and ri is not None and abs(ri - 112) < 20
        print(f'[{name}] no-digit: found={res["found"]} r_out={res["r_outer_px"]:.0f} '
              f'phi1_est={ri} (期望≈112) {"OK" if ok_det else "<<< DETECT FAIL"}')
        # 数字 1/2/3 多角度
        ok = 0
        total = 0
        for ch in '123':
            for ang in range(0, 360, 60):
                img = make_dashed_target(ch, ang, dashed_rings=dashed)
                det = m.RingDetector(m.CALIB)
                res = det.process(img)
                got = res.get('digit')
                total += 1
                good = got == ch
                ok += good
                if not good:
                    print(f'  [digit={ch} ang={ang:3d}] got={got} '
                          f'score={res.get("digit_score", 0):.2f} <<<')
        print(f'  digits: {ok}/{total} OK')
        all_ok = all_ok and ok_det and ok == total
    print('RESULT:', 'PASS' if all_ok else 'FAIL')
    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
