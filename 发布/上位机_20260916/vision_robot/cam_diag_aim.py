#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cam_diag_aim.py —— 圆心检测稳定性诊断(aim_and_place v4 API)

连续 N 帧记录原版 RingDetector 输出, 统计圆心抖动:
  - std < 2px      → 检测稳定, 可进瞄准闭环
  - std 大 / 频繁丢 → 先查对焦(定焦镜头只能物理拧)、光照/反光、靶标完整性
输出坐标 = aim_and_place.aim_loop 使用的同一坐标空间(已按 CALIB 旋转)。

用法: python3 cam_diag_aim.py [--cam N] [--frames 20]
"""
import sys, time, argparse, statistics as st

sys.path.insert(0, "/home/justin/vision_robot")

ap = argparse.ArgumentParser()
ap.add_argument("--cam", type=int, default=None,
                help="摄像头索引(默认自动尝试 0/1/2/3, 设备号漂移时指定)")
ap.add_argument("--frames", type=int, default=20)
a = ap.parse_args()

import aim_and_place as A

cap = A.open_camera(a.cam if a.cam is not None else None)
if cap is None:
    print("[err] camera open failed, 用 find_cam.py 看哪个索引可用"); raise SystemExit(1)
print("cap resolution: %dx%d  fps: %s" % (int(cap.get(3)), int(cap.get(4)), cap.get(5)))
det = A.make_detector()

# 等几帧让 RingDetector 的 EMA 收敛
for _ in range(5):
    ok, frame = cap.read()
    if ok:
        det.process(A.rotate_ccw(frame, A.CALIB))
    time.sleep(0.05)

xs, ys, rs, ds = [], [], [], []
N = a.frames
for i in range(N):
    ok, frame = cap.read()
    if not ok:
        print("[err] read"); break
    frame = A.rotate_ccw(frame, A.CALIB)      # 与 aim_loop 同一坐标空间
    res = det.process(frame)
    if res["found"]:
        xs.append(res["cx_px"]); ys.append(res["cy_px"])
        rs.append(res["r_outer_px"])
        if res.get("digit"):
            ds.append(res["digit"])
    time.sleep(0.1)

cap.release()

print("frames with circle: %d/%d" % (len(xs), N))
if xs:
    print("cx: mean=%.1f std=%.2f min=%.0f max=%.0f"
          % (st.mean(xs), st.pstdev(xs), min(xs), max(xs)))
    print("cy: mean=%.1f std=%.2f min=%.0f max=%.0f"
          % (st.mean(ys), st.pstdev(ys), min(ys), max(ys)))
    print("r : mean=%.1f std=%.2f min=%.0f max=%.0f"
          % (st.mean(rs), st.pstdev(rs), min(rs), max(rs)))
    print("digit reads:", ds)
    print("(圆心均值 %.0f,%.0f 即瞄准坐标系下的实际圆心位置, 若夹爪应对准它可参考)"
          % (st.mean(xs), st.mean(ys)))
else:
    print("SUGGEST: 画面没稳定检出圆环 —— 先调对焦/光照, 或用 ring_center_detect_v2.py 单图模式排查")
