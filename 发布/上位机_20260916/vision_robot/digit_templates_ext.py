#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
digit_templates_ext.py —— 给 ring_center_detect_v2 的数字识别"热加模板"
====================================================================
背景: 现场靶心数字多为打印体(黑体/Arial)。内置模板库是 4 种 Hershey 矢量字体,
       与真实印刷体/现场打印机仍可能有差距 → 本模块把"现场样例"加入模板库。

用法(任何脚本一行接入):
    import digit_templates_ext
    digit_templates_ext.install()          # 默认目录 ./digit_custom
之后 ring_center_detect_v2 的数字识别就会用"内置模板 + 现场样例"的合集。

模板目录结构(install/add_crop 自动创建):
    digit_custom/<label>/<任意名>.png      # label ∈ 1/2/3, 文件为"紧致二值字形"(0/255)

说明:
  · 每个样例会像内置字体一样, 按 digit_rot_step 生成多角度模板;
  · install() 是幂等的: 每次从"内置库 + 目录里的所有样例"重建, 可反复调用;
  · 不改动 ring_center_detect_v2.py 源码, 只替换其缓存 _DIGIT_TM。
"""
import os
import glob

import cv2
import numpy as np

import ring_center_detect_v2 as ring

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digit_custom")


def _sample_to_tensors(path, step):
    """把一张"紧致二值字形"样例按 ring 的内置流程生成旋转模板行。"""
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return []
    if img.mean() > 127:                      # 深底亮字 → 反相成"亮字暗底"
        img = 255 - img
    _, bw = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    nz = cv2.findNonZero(bw)
    if nz is None:
        return []
    x, y, w, h = cv2.boundingRect(nz)
    glyph = bw[y:y + h, x:x + w]
    if w < 3 or h < 3:
        return []
    s = max(w, h)
    sq = np.zeros((s, s), np.uint8)
    sq[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = glyph
    sq = cv2.resize(sq, (96, 96), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((144, 144), np.uint8)
    canvas[24:120, 24:120] = sq
    rows = []
    for ang in range(0, 360, max(1, int(step))):
        M = cv2.getRotationMatrix2D((72, 72), ang, 1.0)
        rot = cv2.warpAffine(canvas, M, (144, 144), flags=cv2.INTER_LINEAR, borderValue=0)
        nz = cv2.findNonZero(rot)
        if nz is None:
            continue
        rx, ry, rw, rh = cv2.boundingRect(nz)
        if rw < 2 or rh < 2:
            continue
        rows.append(ring._glyph_tensor(rot[ry:ry + rh, rx:rx + rw], 28).flatten())
    return rows


def install(custom_dir=None, rot_step=None, verbose=True):
    """重建模板缓存 = 内置模板 + custom_dir 下所有样例。返回 (样例数, 模板总数)。"""
    custom_dir = custom_dir or DEFAULT_DIR
    step = int(rot_step if rot_step is not None
               else ring.CALIB.get("digit_rot_step", 5))
    base_labels, base_T = ring._build_rot_templates(step)
    labels = list(base_labels)
    rows = [base_T]
    n_samples = 0
    for label in ("1", "2", "3"):
        d = os.path.join(custom_dir, label)
        for p in sorted(glob.glob(os.path.join(d, "*.png"))):
            rs = _sample_to_tensors(p, step)
            if rs:
                rows.append(np.stack(rs).astype(np.float32))
                labels.extend([label] * len(rs))
                n_samples += 1
    ring._DIGIT_TM = (labels, np.vstack(rows).astype(np.float32))
    if verbose:
        print("[templates] custom samples=%d  total templates=%d (step=%d, dir=%s)"
              % (n_samples, len(labels), step, custom_dir))
    return n_samples, len(labels)


def add_crop(label, glyph_bw, custom_dir=None, rot_step=None, name=None, verbose=True):
    """把一张紧致二值字形(bw, 0/255)存为样例并重建模板。返回保存路径。"""
    custom_dir = custom_dir or DEFAULT_DIR
    label = str(label)
    if label not in ("1", "2", "3"):
        raise ValueError("label 只能 1/2/3")
    d = os.path.join(custom_dir, label)
    os.makedirs(d, exist_ok=True)
    import time as _t
    fn = name or ("crop_%d.png" % int(_t.time() * 1000 % 10 ** 9))
    p = os.path.join(d, fn)
    cv2.imwrite(p, glyph_bw)
    install(custom_dir, rot_step, verbose=verbose)
    return p


def count_samples(custom_dir=None):
    custom_dir = custom_dir or DEFAULT_DIR
    return sum(len(glob.glob(os.path.join(custom_dir, l, "*.png")))
               for l in ("1", "2", "3"))


if __name__ == "__main__":
    n, t = install(verbose=True)
    print("samples=%d templates=%d" % (n, t))
