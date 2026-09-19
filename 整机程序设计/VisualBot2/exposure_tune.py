#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""exposure_tune.py —— 按**当前场景**挑曝光档（既不过曝、颜色检测又大又稳）

为什么要它：曝光值不是一劳永逸的 —— 换场地、换灯、换物料距离，合适值就变了。
  2026-09-17 现场：上午锁 60 时"命中 4.4 万、稳"，下午在原料区同一档就过曝了。

用法（跑之前先关掉别的程序：相机同一时刻只能被一个程序占用）
    cd /root/VisualBot/校赛
    python3 -u exposure_tune.py --color red                      # 默认测 20~100 各档
    python3 -u exposure_tune.py --color green --levels 20,30,40,50,60

输出里挑一行判定为「✓ 可用」的，把数字填进 school_main.py 的 CAM_EXPOSURE；
或临时锁一下：v4l2-ctl -d /dev/video0 -c auto_exposure=1 -c exposure_time_absolute=<数字>
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/root/VisualBot/校赛")
sys.path.insert(0, "/root/VisualBot")

import cv2
import numpy as np

from color_blob_demo import PRESETS, ColorBlobDetector, open_camera   # noqa: E402
from vision import ColorVision                                        # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--color", default="red", help="拿哪个颜色的物料来测 red/green/blue")
ap.add_argument("--levels", default="20,30,40,50,60,80,100",
                help="要试的曝光档（逗号分隔）；会先测一档『不锁（自动）』做对照")
ap.add_argument("--frames", type=int, default=3, help="每档抓几帧看稳定性")
ap.add_argument("--cam", type=int, default=0)
a = ap.parse_args()

levels = [None] + [int(x) for x in str(a.levels).split(",") if str(x).strip()]

cv = ColorVision()                                   # 读现场档案（阈值以你调的为准）
cfg = dict(cv.PRESETS.get(a.color) or PRESETS[a.color])
print("目标颜色 %s 阈值: h=%s hw=%s s=[%s,%s] v=[%s,%s]"
      % (a.color, cfg.get("h"), cfg.get("hw"), cfg.get("s_min"), cfg.get("s_max"),
         cfg.get("v_min"), cfg.get("v_max")))
print("形状门限（来自主程序参数区）：面积 %.4f~%.2f、圆度 %.2f"
      % (cv.min_area_frac, cv.max_area_frac, cv.circ_min))
print("-" * 84)

best = None
for exp in levels:
    cap = open_camera(a.cam, lock=True, exposure=exp)
    if cap is None:
        print("  %-8s 相机打不开（有别的程序占着？）" % exp)
        break
    for _ in range(8):                               # 让自动曝光/增益先稳定
        cap.read()
    rows = []
    for _ in range(max(1, a.frames)):
        ok, fr = cap.read()
        if not ok or fr is None:
            continue
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        over = float((gray >= 250).mean() * 100.0)   # 过曝（打满到白的像素占比）
        det = ColorBlobDetector(cfg)
        det.min_area_frac, det.max_area_frac, det.circ_min = cv.min_area_frac, cv.max_area_frac, cv.circ_min
        res = det.detect(fr, a.color)
        hits = int((res["mask"] > 0).sum())
        info = res.get("info")
        sel = ("c=(%.0f,%.0f) r=%.0f" % (info["cx"], info["cy"], info["r"])) if info else "未找到"
        rows.append((float(gray.mean()), over, hits, sel))
    cap.release()
    if not rows:
        continue
    lum = float(np.mean([r[0] for r in rows]))
    over = float(np.mean([r[1] for r in rows]))
    hits = [r[2] for r in rows]
    found = sum(1 for r in rows if r[3] != "未找到")
    ok_row = (found == len(rows)) and max(hits) > 2000 and over < 3.0
    if found == 0:
        verdict = "✗ 找不到目标（太暗/过曝/阈值不符）"
    elif over >= 3.0:
        verdict = "✗ 过曝（%.1f%% 像素打满）" % over
    elif ok_row:
        verdict = "✓ 可用"
    else:
        verdict = "△ 勉强（命中偏小或不稳）"
    for i, r in enumerate(rows):
        print("%-8s 亮度%-7.1f 过曝%-6.1f%% 命中%-8d %s"
              % (("自动" if exp is None else exp) if i == 0 else "", r[0], r[1], r[2], r[3]))
    print("%-8s → 平均亮度 %.0f、过曝 %.1f%%、命中 %d~%d → %s"
          % ("", lum, over, min(hits), max(hits), verdict))
    if ok_row and (best is None or max(hits) > best[1]):
        best = (exp, max(hits))
    print("-" * 84)

print("怎么选：挑判定『✓ 可用』的那一档（过曝 <3%、命中量大且几帧之间波动小）。")
if best:
    print("★ 本次建议：CAM_EXPOSURE = %s（命中 %d 像素）" % (best[0], best[1]))
    print("   临时锁：v4l2-ctl -d /dev/video%d -c auto_exposure=1 -c exposure_time_absolute=%s"
          % (a.cam, best[0]))
else:
    print("!! 没有一档达到『可用』：先确认物料在画面里、阈值匹配（用 color_blob_stream.py 重标）")
