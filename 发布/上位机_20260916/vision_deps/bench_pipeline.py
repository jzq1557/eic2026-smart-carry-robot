#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_pipeline.py —— 检测管线各阶段耗时基准(本地模拟, 供评估树莓派帧率影响)
用法: python3 bench_pipeline.py [图片路径]  # 缺省自动找 工创赛标靶A4(12|13).png
"""
import sys, os, time, cv2, numpy as np

def _find_default_img():
    for name in ("工创赛标靶A4(12).png", "工创赛标靶A4(13).png"):
        p = os.path.join(os.getcwd(), name)
        if os.path.exists(p):
            return p
    return None

IMG = sys.argv[1] if len(sys.argv) > 1 and os.path.exists(sys.argv[1]) else _find_default_img()

def bench(fn, img, n=10):
    fn(img)  # 预热
    t0 = time.perf_counter()
    for _ in range(n):
        fn(img)
    return (time.perf_counter() - t0) / n * 1000.0

def main():
    img = cv2.imread(IMG)
    full = cv2.resize(img, (1280, 720))               # 原版 1280x720
    half = cv2.resize(img, (640, 360))                # proc_scale=0.5 后检测尺寸
    print(f"full 1280x720 / half 640x360")

    def st_split(i):
        b, g, r = cv2.split(i); return b, g, r
    def st_dark(i):                                   # 暗通道(在原版灰度基础上多花的时间)
        b, g, r = cv2.split(i)
        return cv2.min(cv2.min(b, g), r)
    def st_gray(i):
        return cv2.cvtColor(i, cv2.COLOR_BGR2GRAY)
    def st_gauss(i):
        return cv2.GaussianBlur(cv2.cvtColor(i, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    def st_thr(i):
        g = cv2.GaussianBlur(cv2.cvtColor(i, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        return cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY_INV, 41, 5)
    def st_close9(i):
        g = cv2.GaussianBlur(cv2.cvtColor(i, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, 41, 5)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        return cv2.morphologyEx(b, cv2.MORPH_CLOSE, k)
    def st_close31(i):
        g = cv2.GaussianBlur(cv2.cvtColor(i, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, 41, 5)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
        return cv2.morphologyEx(b, cv2.MORPH_CLOSE, k)
    def st_contour_all(i):
        g = cv2.GaussianBlur(cv2.cvtColor(i, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, 41, 5)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, k)
        cnts, _ = cv2.findContours(b, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        h, w = i.shape[:2]
        amin = 0.0004 * w * h
        pts = []
        for c in cnts:
            if cv2.contourArea(c) >= amin:
                pts.extend(c.reshape(-1, 2))
        return len(pts)

    print("\n===== 640x360(proc_scale 后实际检测尺寸) =====")
    print(f"原版: 灰度+高斯+阈值+闭9x9 = {bench(st_close9, half):.2f} ms")
    print(f"暗通道替换灰度(新增代价)      = {bench(st_dark, half):.2f} ms (min 本身很快)")
    print(f"多一次大核闭 31x31            = {bench(st_close31, half) - bench(st_close9, half):.2f} ms")
    print(f"轮廓全收集+合并               = {bench(st_contour_all, half):.2f} ms")

    print("\n===== 1280x720(若不用 proc_scale) =====")
    print(f"原版管线 = {bench(st_close9, full):.2f} ms")
    print(f"大核闭31增额 = {bench(st_close31, full) - bench(st_close9, full):.2f} ms")
    return 0

if __name__ == "__main__":
    sys.exit(main())
