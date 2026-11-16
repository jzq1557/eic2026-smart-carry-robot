#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exp_speed.py —— 视觉管线微基准（给弱算力平台定优化方向：RDK X5 / 树莓派 4B）
==============================================================================
为什么需要它: 端到端 fps 只能告诉你"慢"，不能告诉你"慢在哪、改哪一项能省多少"。
本脚本把圆环管线的每一步单独计时, 并按"候选优化"逐项对比, 输出一张 ms 表。

用法(在 ~/VisualBot 或 ~/vision_robot 里):
  python3 -u exp_speed.py                 # 合成靶标, 每项 15 次
  python3 -u exp_speed.py --frames 30
  python3 -u exp_speed.py --image 工创赛标靶A4(12).png    # 用真实图

看表方法:
  · 每项都给了"现状 vs 候选", 差值就是这一项能省下的 ms/帧;
  · 把"值得改的几项"的省下时间相加, 再和总耗时比, 就知道理论收益上限;
  · 改完再跑一次本脚本 + bench_perf.sh 复测。
"""
import argparse
import os
import sys
import time

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ring_center_detect_v2 as ring                                    # noqa: E402


def bench(name, fn, n):
    fn()                                    # 预热(排除首次分配)
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = (time.perf_counter() - t0) / n * 1000.0
    print("  %-48s %8.2f ms" % (name, dt), flush=True)
    return dt


def synth():
    try:
        from ring_stream_ui import synth_ring_scene
        return synth_ring_scene(0)
    except Exception:
        # 退路: 自己画一个同心环靶标
        img = np.full((720, 1280, 3), 40, np.uint8)
        c = (640, 360)
        for r in (300, 240, 180, 120):
            cv2.circle(img, c, r, (230, 230, 230), 6)
        cv2.putText(img, "2", (600, 400), cv2.FONT_HERSHEY_SIMPLEX, 4, (240, 240, 240), 3)
        return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=15)
    ap.add_argument("--image", default=None, help="用真实图片代替合成靶标")
    a = ap.parse_args()
    n = max(3, a.frames)

    cfg = dict(ring.CALIB)
    raw = cv2.imread(a.image) if a.image else synth()
    if raw is None:
        print("!! 读不到图片", a.image)
        return 2
    frame = ring.rotate_ccw(raw, cfg)
    print("=" * 66)
    print("frame %s   cv2 %s   threads %d   cpu %d"
          % (frame.shape, cv2.__version__, cv2.getNumThreads(), os.cpu_count() or 0))
    print("=" * 66)

    # ---------- A. 旋转（每帧固定成本；相机正向时可整体省掉） ----------
    print("[A] rotate_ccw（CALIB 里 rotate_ccw=True 时每帧都要做）")
    if raw.shape[:2] != frame.shape[:2]:
        bench("cv2.rotate 90CCW @%dx%d（现状）" % (raw.shape[1], raw.shape[0]),
              lambda: cv2.rotate(raw, cv2.ROTATE_90_COUNTERCLOCKWISE), n)
        half = cv2.resize(raw, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        bench("先缩 0.5 再 rotate（省 3/4）",
              lambda: cv2.rotate(half, cv2.ROTATE_90_COUNTERCLOCKWISE), n)
        bench("不做旋转（相机正向 -> 面板关掉 rotate_ccw）", lambda: raw, n)
    else:
        print("  (当前图已是旋转后尺寸, 跳过)")

    # ---------- B. 缩放 ----------
    print("[B] 缩放（检测前的 proc_scale）")
    h, w = frame.shape[:2]
    bench("resize -> 640 宽  INTER_AREA（现状, 质量最好也最慢）",
          lambda: cv2.resize(frame, (640, int(h * 640 / w)), interpolation=cv2.INTER_AREA), n)
    bench("resize -> 640 宽  INTER_LINEAR",
          lambda: cv2.resize(frame, (640, int(h * 640 / w)), interpolation=cv2.INTER_LINEAR), n)
    bench("resize -> 480 宽  INTER_AREA",
          lambda: cv2.resize(frame, (480, int(h * 480 / w)), interpolation=cv2.INTER_AREA), n)

    # ---------- C. find_center 各环节（在 640 宽小图上） ----------
    sm = cv2.resize(frame, (640, int(h * 640 / w)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(sm, cv2.COLOR_BGR2GRAY)
    print("[C] find_center 内部环节（@%dx%d）" % (sm.shape[1], sm.shape[0]))
    bench("cvtColor BGR2GRAY", lambda: cv2.cvtColor(sm, cv2.COLOR_BGR2GRAY), n)
    bench("GaussianBlur 5x5", lambda: cv2.GaussianBlur(gray, (5, 5), 0), n)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    for bs in (41, 21, 15):
        bench("adaptiveThreshold GAUSS block=%d C=5（现状=%d）" % (bs, cfg["thr_block"]),
              lambda bs=bs: cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                  cv2.THRESH_BINARY_INV, bs, 5), n)
        bench("adaptiveThreshold MEAN  block=%d C=5" % bs,
              lambda bs=bs: cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                                  cv2.THRESH_BINARY_INV, bs, 5), n)
    bw = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, cfg["thr_block"], cfg["thr_c"])
    for k in (9, 5, 3):
        ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        bench("morphologyEx CLOSE ellipse %dx%d（现状=%d）" % (k, k, cfg["close_k"]),
              lambda ker=ker: cv2.morphologyEx(bw, cv2.MORPH_CLOSE, ker), n)
    bench("findContours RETR_EXTERNAL", lambda: cv2.findContours(bw, cv2.RETR_EXTERNAL,
                                                                cv2.CHAIN_APPROX_SIMPLE), n)
    bench("find_center 全流程 @640（现状）", lambda: ring.find_center(sm, cfg), n)
    sm480 = cv2.resize(frame, (480, int(h * 480 / w)), interpolation=cv2.INTER_AREA)
    bench("find_center 全流程 @480（proc_scale=0.375）", lambda: ring.find_center(sm480, cfg), n)
    c21 = dict(cfg, thr_block=21, close_k=5)
    bench("find_center 全流程 @640 + block21/close5", lambda: ring.find_center(sm, c21), n)

    # ---------- D. φ1 与数字识别 ----------
    print("[D] φ1 内径 + 数字识别")
    r0 = ring.find_center(sm, cfg)
    if r0:
        cx, cy, rad = r0
        bench("find_inner_radius @640", lambda: ring.find_inner_radius(gray, cx, cy, rad, cfg), n)
        bench("recognize_digit 整段(含裁剪/阈值/形态/矩阵乘)",
              lambda: ring.recognize_digit(sm, cx, cy, max(rad * 0.5, 20), cfg), n)
    ring._DIGIT_TM = None
    bench("建模板库 step=5  (2592 张, 启动一次)", lambda: ring._build_rot_templates(5), 1)
    ring._DIGIT_TM = None
    bench("建模板库 step=15 ( 864 张, 启动一次)", lambda: ring._build_rot_templates(15), 1)
    ring._DIGIT_TM = None
    steps = {}
    for st in (5, 10, 15, 30):
        ring._DIGIT_TM = None
        labels, T = ring._build_rot_templates(st)
        steps[st] = len(labels)
        print("      step=%2d°  -> %5d 张模板" % (st, len(labels)))
    ring._DIGIT_TM = None
    ring._build_rot_templates(int(cfg.get("digit_rot_step", 5)))     # 复原

    # ---------- E. 推流编码 ----------
    print("[E] 推流（JPEG 编码，受 --stream-width/--quality/--stream-fps 控制）")
    for tw, q in ((960, 85), (640, 78), (480, 70)):
        th = int(frame.shape[0] * tw / frame.shape[1])
        img = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
        bench("resize+imencode %d 宽 q%d" % (tw, q),
              lambda img=img, q=q: cv2.imencode(".jpg", img,
                                                [cv2.IMWRITE_JPEG_QUALITY, q]), n)

    # ---------- F. OpenCL / UMat（有些平台能白捡加速） ----------
    print("[F] OpenCL / UMat")
    try:
        have = cv2.ocl.haveOpenCL()
        print("      haveOpenCL=%s  useOpenCL=%s" % (have, cv2.ocl.useOpenCL()))
        if have:
            cv2.ocl.setUseOpenCL(True)
            u = cv2.UMat(blur)
            bench("UMat GaussianBlur 5x5", lambda: cv2.GaussianBlur(u, (5, 5), 0), n)
            bench("UMat adaptiveThreshold GAUSS 41",
                  lambda: cv2.adaptiveThreshold(u, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                cv2.THRESH_BINARY_INV, 41, 5), n)
            ubw = cv2.UMat(bw)
            ker9 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
            bench("UMat morphologyEx CLOSE 9",
                  lambda: cv2.morphologyEx(ubw, cv2.MORPH_CLOSE, ker9), n)
            bench("UMat -> numpy 取回 (.get())", lambda: cv2.UMat(bw).get(), n)
            cv2.ocl.setUseOpenCL(False)
        else:
            print("      本机 OpenCV 没有 OpenCL, 跳过")
    except Exception as e:
        print("      OpenCL 测试失败: %r" % e)

    print("=" * 66)
    print("结论提示: 把 [C]/[D] 里「候选 vs 现状」的差值累加, 就是理论可省时间;")
    print("          再用 bash bench_perf.sh 复测端到端 fps 验证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
