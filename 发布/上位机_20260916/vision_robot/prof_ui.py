#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prof_ui.py —— 按 ring_stream_ui.do_detect 的**真实顺序**分段计时
=================================================================
prof_loop.py 只测了"rotate/process/编码"几段, 没覆盖 resize→rotate 的新顺序、
推流缩放、诊断重跑等; 本脚本把整条链路按 UI 的实际做法走一遍, 给出每段 ms 与占比,
用来回答"端到端 86ms 到底花在哪"。

用法(在 ~/VisualBot 或 ~/vision_robot 里):
  python3 -u prof_ui.py --perf rdk            # 按某个性能档
  python3 -u prof_ui.py --perf auto --frames 20
"""
import argparse
import os
import sys
import time

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ring_center_detect_v2 as ring                                      # noqa: E402
from ring_stream_ui import (synth_ring_scene, glyph_from_frame,           # noqa: E402
                            digit_scores, ring_stage_timings)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perf", default="rdk")
    ap.add_argument("--frames", type=int, default=20)
    a = ap.parse_args()

    from color_blob_demo import PERF_PRESETS, detect_perf
    name = detect_perf() if a.perf == "auto" else a.perf
    p = PERF_PRESETS.get(name, PERF_PRESETS["mid"])
    cfg = dict(ring.CALIB)
    cfg.update(p.get("cfg") or {})
    proc_w = p["width"]
    stream_w = p["stream_width"]
    quality = p["quality"]
    diag_every = max(1, int(p.get("diag_every", 1)))

    print("=" * 70)
    print("perf=%s  width=%s  stream_w=%s  q=%s  diag_every=%s  cfg覆盖=%s"
          % (name, proc_w, stream_w, quality, diag_every, p.get("cfg")))
    print("检测尺度 = width x proc_scale = %s x %s" % (proc_w, cfg.get("proc_scale")))
    print("=" * 70)

    det = ring.RingDetector(cfg)
    src = synth_ring_scene(0)
    # ★预热: 数字模板库是懒加载的(首次在 RDK 上要 1s 量级), 不预热会把"建库"
    #   整段时间算进 diag.digit_scores, 让占比表彻底失真(踩过)
    _fr0 = ring.rotate_ccw(src, cfg)
    _r0 = det.process(_fr0)
    if _r0.get("found"):
        _g0, _ = glyph_from_frame(_fr0, _r0["cx_px"], _r0["cy_px"],
                                  _r0["r_outer_px"] * 0.5, cfg)
        digit_scores(_g0, cfg)
    print("预热完成: 模板 %d 张" % len(ring._build_rot_templates(
        int(cfg.get("digit_rot_step", 5)))[0]))
    acc = {}

    def tick(k, t0):
        acc[k] = acc.get(k, 0.0) + (time.perf_counter() - t0) * 1000.0
        return time.perf_counter()

    fr = src
    res = {}
    for i in range(a.frames):
        t = time.perf_counter()
        frame = src
        # —— 与 do_detect 一致: 先缩(旋转前高度=proc_w)再转 ——
        h0, w0 = frame.shape[:2]
        if proc_w and cfg.get("rotate_ccw", False) and h0 > proc_w:
            sc = proc_w / float(h0)
            frame = cv2.resize(frame, (max(1, int(w0 * sc)), proc_w),
                               interpolation=cv2.INTER_AREA)
        elif proc_w and not cfg.get("rotate_ccw", False) and w0 > proc_w:
            frame = cv2.resize(frame, (proc_w, max(1, int(h0 * proc_w / w0))),
                               interpolation=cv2.INTER_AREA)
        t = tick("resize", t)
        fr = ring.rotate_ccw(frame, cfg)
        t = tick("rotate", t)
        res = det.process(fr)
        t = tick("process", t)

        if i % diag_every == 0 and res.get("found"):
            scale = float(cfg.get("proc_scale", 1.0))
            small = cv2.resize(fr, None, fx=scale, fy=scale,
                               interpolation=cv2.INTER_AREA) if scale != 1.0 else fr
            ring_stage_timings(small, cfg)
            t = tick("diag.stage_timings", t)
            gs = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
            ring.find_inner_radius(gs, res["cx_px"] * scale, res["cy_px"] * scale,
                                   res["r_outer_px"] * scale, cfg)
            t = tick("diag.inner(单独算)", t)
            g, crop = glyph_from_frame(fr, res["cx_px"], res["cy_px"],
                                       res["r_outer_px"] * 0.5, cfg)
            t = tick("diag.glyph", t)
            digit_scores(g, cfg)
            t = tick("diag.digit_scores", t)

        # —— 推流: 先缩到 stream_w 再画 ——
        disp = fr
        if stream_w and max(fr.shape[:2]) > stream_w:
            sc = stream_w / float(max(fr.shape[:2]))
            disp = cv2.resize(fr, (int(fr.shape[1] * sc), int(fr.shape[0] * sc)),
                              interpolation=cv2.INTER_AREA)
        t = tick("stream_resize", t)
        view = disp.copy()
        if res.get("found"):
            cv2.circle(view, (int(res["cx_px"]), int(res["cy_px"])),
                       int(res["r_outer_px"]), (0, 255, 0), 2)
        cv2.putText(view, "diag", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        t = tick("annotate", t)
        ok, jpg = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, quality])
        t = tick("imencode", t)
        t = tick("rest(锁/JSON等)", t)

    tot = sum(acc.values()) / a.frames
    print("帧尺寸=%s  平均 %d 帧" % (fr.shape, a.frames))
    for k, v in sorted(acc.items(), key=lambda kv: -kv[1]):
        print("  %-20s %7.2f ms/帧   %5.1f%%" % (k, v / a.frames, v / tot * 100))
    print("  %-20s %7.2f ms/帧   ->  %.1f fps" % ("TOTAL", tot, 1000.0 / tot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
