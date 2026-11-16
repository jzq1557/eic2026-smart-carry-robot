#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exp_config.py —— 圆环检测「配置组合」扫描：每一项改动能省多少 ms、会不会掉精度
================================================================================
背景: RDK X5 这类弱算力平台上, 端到端只有 7.5fps。微基准(exp_speed.py)显示大头是
      adaptiveThreshold(GAUSSIAN 大窗口) 与 morphologyEx(大椭圆核), 其次是每帧的
      rotate/resize 与诊断重跑。本脚本把这些"可调参数"组合起来实测:
        · 速度: 每帧 RingDetector.process 的 ms
        · 精度: found / 圆心 / 半径 / 数字 / 分数 —— 与基线逐项对比, 掉精度会标红

用法(在 ~/VisualBot 或 ~/vision_robot 里, 最好带上真实标靶图):
  python3 -u exp_config.py                              # 只用合成靶标
  python3 -u exp_config.py --image a4_12.png            # 加上真实 A4 标靶
  python3 -u exp_config.py --frames 10 --width 640
"""
import argparse
import os
import sys
import time

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ring_center_detect_v2 as ring                                    # noqa: E402


def load_images(path):
    imgs = []
    try:
        from ring_stream_ui import synth_ring_scene
        for s in (0, 1, 2):
            imgs.append(("synth%d" % s, synth_ring_scene(s)))
    except Exception as e:
        print("!! 合成场景不可用: %r" % e)
    if path:
        im = cv2.imread(path)
        if im is None:
            print("!! 读不到 %s" % path)
        else:
            name = os.path.basename(path)
            imgs.append((name, im))
            imgs.append((name + "#ccw", ring.rotate_ccw(im, ring.CALIB)))
    return imgs


def prep(img, cfg, width):
    fr = ring.rotate_ccw(img, cfg)
    if width and fr.shape[1] > width:
        fr = cv2.resize(fr, (width, int(fr.shape[0] * width / fr.shape[1])),
                        interpolation=cv2.INTER_AREA)
    return fr


def run_cfg(cfg, frames, images, width):
    """返回 (ms/帧, 每张图的检测结果) —— 每张图都重建 detector, 避免状态串味。"""
    times = []
    outs = {}
    for name, img in images:
        fr = prep(img, cfg, width)
        det = ring.RingDetector(cfg)
        det.process(fr)                                  # 预热
        t0 = time.perf_counter()
        for _ in range(frames):
            res = det.process(fr)
        times.append((time.perf_counter() - t0) / frames * 1000.0)
        outs[name] = res
    return float(np.mean(times)), outs


def fmt(res):
    if not res.get("found"):
        return "not-found"
    return "c=(%4.0f,%4.0f) r=%3.0f digit=%s score=%.2f" % (
        res["cx_px"], res["cy_px"], res["r_outer_px"],
        res.get("digit"), res.get("digit_score", 0.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=None, help="真实标靶图(建议 A4)")
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--width", type=int, default=640, help="先把图缩到这么宽(模拟 --width)")
    a = ap.parse_args()

    base = dict(ring.CALIB)
    images = load_images(a.image)
    if not images:
        print("没有可用图片")
        return 2
    print("=" * 78)
    print("平台 cpu=%s  cv2=%s  threads=%d   图片=%s   处理宽=%d"
          % (os.cpu_count(), cv2.__version__, cv2.getNumThreads(),
             ",".join(n for n, _ in images), a.width))
    print("基线 CALIB: proc_scale=%s thr_block=%s thr_c=%s close_k=%s digit_rot_step=%s"
          % (base.get("proc_scale"), base.get("thr_block"), base.get("thr_c"),
             base.get("close_k"), base.get("digit_rot_step")))
    print("=" * 78)

    cands = [
        ("基线（现状）", {}),
        ("thr_block 41 -> 25", {"thr_block": 25}),
        ("thr_block 41 -> 17", {"thr_block": 17}),
        ("close_k   9  -> 5", {"close_k": 5}),
        ("close_k   9  -> 3", {"close_k": 3}),
        ("proc_scale .5 -> .40", {"proc_scale": 0.40}),
        ("proc_scale .5 -> .30", {"proc_scale": 0.30}),
        ("block25 + close5", {"thr_block": 25, "close_k": 5}),
        ("block25 + close5 + scale.40", {"thr_block": 25, "close_k": 5, "proc_scale": 0.40}),
        ("block17 + close5 + scale.30", {"thr_block": 17, "close_k": 5, "proc_scale": 0.30}),
        ("digit_rot_step 5 -> 15", {"digit_rot_step": 15}),
    ]

    ref = None
    rows = []
    for label, over in cands:
        cfg = dict(base, **over)
        ms, outs = run_cfg(cfg, a.frames, images, a.width)
        if ref is None:
            ref = outs
        same = all(
            (outs[k].get("found") == ref[k].get("found"))
            and (not outs[k].get("found")
                 or (abs(outs[k]["cx_px"] - ref[k]["cx_px"]) < 3
                     and abs(outs[k]["cy_px"] - ref[k]["cy_px"]) < 3
                     and outs[k].get("digit") == ref[k].get("digit")))
            for k in ref)
        rows.append((label, ms, same, outs))
        print("\n[%s]  %.0f ms/帧  %s" % (label, ms, "精度=与基线一致 ✓" if same else "**精度有差异**"))
        for k in ref:
            print("     %-22s %s" % (k, fmt(outs[k])))

    print("\n" + "=" * 78)
    print("%-34s %10s %10s %s" % ("配置", "ms/帧", "相对基线", "精度"))
    bms = rows[0][1]
    for label, ms, same, _ in rows:
        print("%-34s %10.1f %9.0f%% %s"
              % (label, ms, (ms / bms - 1) * 100, "✓" if same else "有差异(需人工看上面)"))
    print("=" * 78)
    print("说明: 精度只对比了 found/圆心/数字; 真要改参数前请再跑 test_rotation.py /")
    print("      test_dashed.py / test_stability.py 三套回归 (它们比本表严格)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
