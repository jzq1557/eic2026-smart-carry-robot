# -*- coding: utf-8 -*-
"""补充验证: 更多数字 / A4 真实图 / 离线推流标注不崩溃。"""
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import subprocess
import time
import os
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
    # 1) 全部数字
    ok = 0
    for ch in '0123456789':
        det = m.RingDetector(m.CALIB)
        res = det.process(make_target(digit=ch))
        got = res.get('digit')
        good = got == ch
        ok += good
        print(f'[digit={ch}] recognized={got} score={res.get("digit_score", 0):.2f} '
              f'{"OK" if good else "<<< MISMATCH"}')
    print(f'digits: {ok}/10')

    # 2) A4 真实图缩放版(无数字, 期望环检出但 digit=None)
    img = cv2.imread('target_A4.png')
    block = img[280:280 + 1196, 575:575 + 1199]
    scale = m.ring_radii_mm(54.7)[0] * 3.878 / 323.0
    small = cv2.resize(block, (int(1199 * scale), int(1196 * scale)),
                       interpolation=cv2.INTER_AREA)
    det = m.RingDetector(m.CALIB)
    res = det.process(small)
    print(f'[A4-rescaled] found={res["found"]} rings={res.get("n_rings")} '
          f'digit={res.get("digit")} conf={res.get("conf", 0):.2f}')

    # 3) 离线推流标注路径: 直接调用主循环的绘制代码段(与 main() 中一致)
    img2 = make_target(digit='5')
    det = m.RingDetector(m.CALIB)
    res = det.process(img2)
    disp = img2.copy()
    if res and res['found']:
        px, py = int(res['cx_px']), int(res['cy_px'])
        col = (0, 255, 0) if res['conf'] >= 0.8 else (0, 165, 255)
        cv2.line(disp, (px - 12, py), (px + 12, py), (0, 0, 255), 2)
        cv2.line(disp, (px, py - 12), (px, py + 12), (0, 0, 255), 2)
        cv2.circle(disp, (px, py), int(res['r_outer_px']), col, 1)
        if res.get('digit'):
            dch = res['digit']
            dscale = max(0.6, min(6.0, res['r_outer_px'] / 45.0))
            dthick = max(2, min(8, int(dscale)))
            (tw, th), _ = cv2.getTextSize(dch, cv2.FONT_HERSHEY_SIMPLEX, dscale, dthick)
            cv2.putText(disp, dch, (px - tw // 2, py + th // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, dscale, (0, 255, 255), dthick, cv2.LINE_AA)
    okj, jpg = cv2.imencode('.jpg', disp, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(f'[annotate-path] imencode ok={okj} jpeg_bytes={len(jpg.tobytes())}')

    # 4) 真实主程序: 图片模式 + --stream, 起进程 8 秒, 验证不崩溃 + 推流有数据
    cmd = ['python3', '-u', 'ring_center_detect_v2.py', 'synth_plate_annotated.png',
           '--stream', '8091']
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(8)
    alive = proc.poll() is None
    r = os.system("curl -s --max-time 3 http://127.0.0.1:8091/stream -o /tmp/stream_check.mjpeg && wc -c < /tmp/stream_check.mjpeg")
    print(f'[demo-image-stream] process_alive={alive} curl_rc={r}')
    proc.kill()
    time.sleep(1)
    print('DEMO_EXTRA_DONE')


if __name__ == '__main__':
    main()
