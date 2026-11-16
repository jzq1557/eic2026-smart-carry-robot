#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prof_loop.py —— 给 ring_stream_ui 主循环各步骤打点, 找出真正的耗时大头"""
import time
import cv2
import numpy as np
import ring_center_detect_v2 as ring
import ring_stream_ui as U

cfg = dict(ring.CALIB)
U.ring._build_rot_templates(int(cfg["digit_rot_step"]))
det = ring.RingDetector(cfg)
frame = U.synth_ring_scene(0)
N = 12
acc = {}


def tick(k, t0):
    dt = (time.perf_counter() - t0) * 1000.0
    acc[k] = acc.get(k, 0.0) + dt
    return time.perf_counter()


for i in range(N):
    t = time.perf_counter()
    f = frame.copy()
    t = tick("copy_frame", t)
    fr = ring.rotate_ccw(f, cfg)
    t = tick("rotate", t)
    res = det.process(fr)
    t = tick("process", t)
    # 诊断(每3帧)
    if i % 3 == 0:
        scale = float(cfg.get("proc_scale", 1.0))
        small = cv2.resize(fr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        r, bw, tim, sp = U.ring_stage_timings(small, cfg)
        t = tick("stage_timings", t)
        if res.get("found"):
            ri = ring.find_inner_radius(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY),
                                        res["cx_px"] * scale, res["cy_px"] * scale,
                                        res["r_outer_px"] * scale, cfg)
            t = tick("find_inner", t)
            rin = (ri / scale) if ri else res["r_outer_px"] * cfg.get("inner_fallback_frac", 0.55)
            g, crop = U.glyph_from_frame(fr, res["cx_px"], res["cy_px"], rin, cfg)
            t = tick("glyph", t)
            best, a, b, c = U.digit_scores(g, cfg)
            t = tick("digit_scores", t)
    # 画图
    view = fr.copy()
    cv2.circle(view, (int(res["cx_px"]), int(res["cy_px"])), int(res["r_outer_px"]),
               (0, 255, 0), 2)
    cv2.putText(view, "x", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    t = tick("annotate", t)
    enc = view
    if max(enc.shape[:2]) > 720:
        sc = 720 / float(max(enc.shape[:2]))
        enc = cv2.resize(enc, (int(enc.shape[1] * sc), int(enc.shape[0] * sc)))
    t = tick("stream_resize", t)
    ok, jpg = cv2.imencode(".jpg", enc, [cv2.IMWRITE_JPEG_QUALITY, 75])
    t = tick("imencode", t)

print("frame size after rotate:", fr.shape, " N=%d" % N)
for k, v in sorted(acc.items(), key=lambda kv: -kv[1]):
    print("  %-14s %7.1f ms/frame (avg over %d)" % (k, v / N, N))
print("  %-14s %7.1f ms/frame" % ("TOTAL", sum(acc.values()) / N))
