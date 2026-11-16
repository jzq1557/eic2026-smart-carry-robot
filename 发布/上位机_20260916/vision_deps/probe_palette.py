#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_palette.py —— 统计一张图的主色调(PIL,支持中文路径),为色域/色卡打底
用法: python probe_palette.py 图片路径 [采样间隔=3]
输出: 占比最高的若干颜色(hex + HSV + 粗略中文色名)。
"""
import sys
import os
import colorsys
from collections import Counter

try:
    from PIL import Image
except ImportError:
    print("[err] need pillow: pip install pillow")
    sys.exit(1)


def guess_name(h, s, v):
    if v < 0.10:
        return "黑"
    if s < 0.08:
        return "白" if v > 0.85 else "灰"
    if s < 0.25:
        return "浅灰? 色相%.0f" % (h * 360)
    deg = h * 360
    if deg < 12 or deg >= 345:
        return "红"
    if deg < 30:
        return "橙/橙红"
    if deg < 62:
        return "黄"
    if deg < 90:
        return "黄绿"
    if deg < 155:
        return "绿"
    if deg < 190:
        return "青/蓝绿"
    if deg < 250:
        return "蓝"
    if deg < 300:
        return "紫/蓝紫"
    if deg < 340:
        return "品红/洋红"
    return "红"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    step = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    cnt = Counter()
    for y in range(0, h, step):
        for x in range(0, w, step):
            cnt[px[x, y]] += 1
    total = sum(cnt.values())
    print("image %s  %dx%d  sampled %d px" % (os.path.basename(path), w, h, total))
    print("--- top colors by sampled pixel share ---")
    for (rgb, n) in cnt.most_common(14):
        r, g, b = rgb
        hh, ss, vv = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        print("  #%02X%02X%02X  share=%5.1f%%  HSV(H=%.0f S=%.2f V=%.2f)  ~ %s"
              % (r, g, b, 100.0 * n / total, hh * 360, ss, vv, guess_name(hh, ss, vv)))


if __name__ == "__main__":
    main()
