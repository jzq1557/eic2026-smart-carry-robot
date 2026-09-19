# -*- coding: utf-8 -*-
"""仿真靶标测试: 按 CALIB 参数渲染 6 同心环 + 环心数字, 端到端验证识别。"""
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import cv2
import numpy as np
import ring_center_detect_v2 as m

CANVAS = 520
CXC, CYC = 260, 260


def make_target(digit=None, line_w=6, noise=False):
    img = np.full((CANVAS, CANVAS, 3), 255, np.uint8)
    for r in m.ring_radii_mm(m.CALIB['material_dia_mm']) * m.CALIB['px_per_mm']:
        cv2.circle(img, (CXC, CYC), int(r), (0, 0, 0), line_w)
    if digit:
        (tw, th), _ = cv2.getTextSize(digit, cv2.FONT_HERSHEY_SIMPLEX, 1.8, 3)
        cv2.putText(img, digit, (CXC - tw // 2, CYC + th // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 3, cv2.LINE_AA)
    if noise:
        rng = np.random.default_rng(7)
        for _ in range(400):
            x, y = rng.integers(0, CANVAS, 2)
            cv2.circle(img, (int(x), int(y)), 1, (0, 0, 0), 1)
    return img


def main():
    # 1) 无数字: 只验证环检测
    det = m.RingDetector(m.CALIB)
    res = det.process(make_target())
    print(f'[no-digit] found={res["found"]} rings={res.get("n_rings")} '
          f'conf={res.get("conf", 0):.2f} digit={res.get("digit")}')
    assert res['found'], '环检测失败!'

    # 2) 带数字: 验证识别
    ok = 0
    for ch in ['1', '2', '3', '6']:
        det = m.RingDetector(m.CALIB)
        res = det.process(make_target(digit=ch))
        got = res.get('digit')
        good = (got == ch)
        ok += good
        print(f'[digit={ch}] found={res["found"]} rings={res.get("n_rings")} '
              f'recognized={got} score={res.get("digit_score", 0):.3f} '
              f'{"OK" if good else "<<< MISMATCH"}')

    # 3) 噪声鲁棒性
    det = m.RingDetector(m.CALIB)
    res = det.process(make_target(digit='3', noise=True))
    got = res.get('digit')
    print(f'[digit=3+noise] found={res["found"]} rings={res.get("n_rings")} '
          f'recognized={got} score={res.get("digit_score", 0):.3f} '
          f'{"OK" if got == "3" else "<<< MISMATCH"}')

    # 4) 生成推流标注测试图(验证主循环画字路径)
    img = make_target(digit='2')
    disp = img.copy()
    det = m.RingDetector(m.CALIB)
    res = det.process(img)
    if res and res['found']:
        px, py = int(res['cx_px']), int(res['cy_px'])
        dch = res.get('digit') or '?'
        dscale = max(0.6, min(6.0, res['r_outer_px'] / 45.0))
        dthick = max(2, min(8, int(dscale)))
        (tw, th), _ = cv2.getTextSize(dch, cv2.FONT_HERSHEY_SIMPLEX, dscale, dthick)
        cv2.putText(disp, dch, (px - tw // 2, py + th // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, dscale, (0, 255, 255), dthick, cv2.LINE_AA)
        cv2.circle(disp, (px, py), int(res['r_outer_px']), (0, 255, 0), 1)
    cv2.imwrite('synth_plate_annotated.png', disp)
    print('annotated frame saved: synth_plate_annotated.png')

    print(f'RESULT: {ok}/4 digits OK')
    sys.exit(0 if ok == 4 else 1)


if __name__ == '__main__':
    main()
