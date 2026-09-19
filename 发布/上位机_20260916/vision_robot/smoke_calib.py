#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_calib.py —— 部署冒烟测试(aim_and_place v4 API)

验证: 1) aim_and_place / calibrate_aim 能否 import
      2) 摄像头能否打开(自动尝试候选索引, 与瞄准闭环同一策略)
      3) 原版 RingDetector 能否检出画面里的圆环
用法: python3 smoke_calib.py
      python3 smoke_calib.py --cam 1        # 摄像头设备号漂移时指定
"""
import sys, time, argparse

sys.path.insert(0, "/home/justin/vision_robot")

ap = argparse.ArgumentParser()
ap.add_argument("--cam", type=int, default=None)
a = ap.parse_args()

try:
    import aim_and_place as A
    print("import aim_and_place OK: Lower=%s open_camera=%s make_detector=%s"
          % (hasattr(A, 'Lower'), hasattr(A, 'open_camera'), hasattr(A, 'make_detector')))
except Exception as e:
    print("import aim_and_place FAIL:", e); sys.exit(1)

try:
    import calibrate_aim as C
    print("import calibrate_aim OK: measure_shift=%s grab_center=%s"
          % (hasattr(C, 'measure_shift'), hasattr(C, 'grab_center')))
except Exception as e:
    print("import calibrate_aim FAIL:", e); sys.exit(1)

# 摄像头:与 aim_and_place.open_camera 完全一致(候选索引/分辨率/锁曝光)
cap = A.open_camera(a.cam if a.cam is not None else None)
if cap is None:
    print("[err] camera open failed, 用 find_cam.py 看哪个索引可用, 再 --cam N 重试")
    sys.exit(1)
det = A.make_detector()

found = 0
N = 5
for i in range(N):
    ok, frame = cap.read()
    if not ok:
        print("  frame%d: read fail" % i)
        continue
    # 与 aim_loop 同一坐标空间(rotate_ccw=True 时画面先旋转)
    frame = A.rotate_ccw(frame, A.CALIB)
    res = det.process(frame)
    if res["found"]:
        found += 1
        print("  frame%d: center=(%.0f,%.0f) r=%.0f digit=%s"
              % (i, res["cx_px"], res["cy_px"], res["r_outer_px"], res.get("digit")))
    else:
        print("  frame%d: circle not found" % i)
    time.sleep(0.1)

cap.release()
print("circle found in %d/%d frames" % (found, N))
print("SMOKE_OK" if found > 0 else "SMOKE_WARN_NO_CIRCLE")
