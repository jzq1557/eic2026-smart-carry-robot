#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""exp_highlight_ring.py —— 高光破坏圆环鲁棒性实验(本地/树莓派通用)
对比: 原版最大连通域 vs 暗通道+轮廓合并圆拟合
用法: python3 exp_highlight_ring.py [图片路径]
      不给图则自动找同目录/工作目录下的 工创赛标靶A4(12|13).png
      (Windows cv2 不吃中文路径: Windows 上请把图复制到纯 ASCII 路径再传参)
"""
import sys, os, cv2, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ring_center_detect_v2 as ring

def _find_default_img():
    for name in ("工创赛标靶A4(12).png", "工创赛标靶A4(13).png"):
        p = os.path.join(os.getcwd(), name)
        if os.path.exists(p):
            return p
    for cand in sys.argv[1:]:
        if os.path.exists(cand):
            return cand
    return None

IMG = sys.argv[1] if len(sys.argv) > 1 and os.path.exists(sys.argv[1]) else _find_default_img()

# ---------- 原版 ----------
def detect_orig(frame):
    cfg = dict(ring.CALIB); cfg["center_gate_r"] = 0
    return ring.find_center(frame, cfg)

# ---------- 暗通道 + 轮廓合并 + 最小二乘圆拟合 ----------
def fit_lsq(pts):
    P = np.array(pts, np.float32)
    if len(P) < 5:
        return None
    x, y = P[:, 0], P[:, 1]
    A = np.column_stack([x, y, np.ones_like(x)])
    B = -(x * x + y * y)
    try:
        sol = np.linalg.lstsq(A, B, rcond=None)[0]
    except Exception:
        return None
    cx, cy = -0.5 * sol[0], -0.5 * sol[1]
    r = np.sqrt(max(0.0, cx * cx + cy * cy - sol[2]))
    return (float(cx), float(cy), float(r))

def detect_robust(frame, close_k=(9, 9), area_frac=0.0004):
    b, g, r = cv2.split(frame)
    dark = cv2.min(cv2.min(b, g), r)          # 暗通道: 压掉三通道都高的反光
    gray = cv2.GaussianBlur(dark, (5, 5), 0)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 41, 5)
    k1 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k[0], close_k[1]))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k1)
    h, w = frame.shape[:2]
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    amin = area_frac * w * h
    pts = []
    for c in cnts:
        if cv2.contourArea(c) >= amin:
            pts.extend(c.reshape(-1, 2))
    if len(pts) < 30:
        return None
    return fit_lsq(pts)

# ---------- 模拟高光: 环带上抹白块(2~6块, 尺寸可调) ----------
def add_highlight(frame, nblocks=3, size=90, seed=0):
    h, w = frame.shape[:2]
    out = frame.copy()
    rng = np.random.default_rng(seed)
    # 圆心粗估(图中心即可, 因为靶标基本居中)
    cx, cy = w // 2, h // 2
    # 参考半径(按图比例取0.3~0.5短边), 让白块落在环带上
    for i in range(nblocks):
        ang = rng.uniform(0, 2 * np.pi)
        rr = rng.uniform(0.28, 0.48) * min(h, w)
        bx = int(cx + rr * np.cos(ang))
        by = int(cy + rr * np.sin(ang))
        s = int(size * rng.uniform(0.7, 1.3))
        x0, y0 = max(0, bx - s // 2), max(0, by - s // 2)
        x1, y1 = min(w, bx + s // 2), min(h, by + s // 2)
        cv2.rectangle(out, (x0, y0), (x1, y1), (250, 250, 250), -1)
    return out

def err_str(d, ref):
    if d is None:
        return "None(丢检测)"
    if ref is None:
        return f"({d[0]:.0f},{d[1]:.0f})"
    de = np.hypot(d[0] - ref[0], d[1] - ref[1])
    return f"({d[0]:.0f},{d[1]:.0f}) 偏差{de:.0f}px"

def main():
    img = cv2.imread(IMG)
    if img is None:
        print("read fail:", IMG); return 1
    H, W = img.shape[:2]
    # 缩放便于显示速度(实验用)
    img = cv2.resize(img, (W // 2, H // 2))
    print(f"图 {img.shape[1]}x{img.shape[0]}")
    # 基准
    ref = detect_robust(img)
    print(f"[完整图基准] robust: {err_str(ref, None)}")
    orig_ref = detect_orig(img)
    print(f"[完整图基准] orig  : {err_str(orig_ref, None)}")

    # 不同破坏程度
    print("\n=== 破坏程度扫描 (白块数 2/4/6, 尺寸90px) ===")
    for nb in (2, 4, 6):
        for seed in (0, 1):
            hl = add_highlight(img, nblocks=nb, seed=seed)
            d1 = detect_orig(hl)
            d2 = detect_robust(hl)
            print(f"高光{nb}块/seed{seed}:  orig={err_str(d1, ref):<22} robust={err_str(d2, ref)}")

    # 更大的白块(更强破坏)
    print("\n=== 大白块(140px, 模拟严重反光) ===")
    for nb in (2, 3):
        hl = add_highlight(img, nblocks=nb, size=140, seed=3)
        d1 = detect_orig(hl)
        d2 = detect_robust(hl)
        print(f"高光{nb}块/大: orig={err_str(d1, ref):<22} robust={err_str(d2, ref)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
