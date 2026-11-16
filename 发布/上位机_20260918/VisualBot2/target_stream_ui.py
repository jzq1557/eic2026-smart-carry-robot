#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
target_stream_ui.py —— **彩色标靶**调参台（加工区 1/2/3 号靶位 = 红 / 绿 / 蓝）
==================================================================================
和 color_blob_stream.py 是一对，但服务对象不同：
    color_blob_stream  调的是**取料用的物料**（色块的中心）
    target_stream_ui   调的是**加工区的彩色标靶**（同心圆环 / 圆盘的中心）

★算法（按车主 2026-09-18 要求，借鉴 ring_stream_ui.py 的做法，但换成"按颜色分割 + 求圆心"）：
    ① HSV 阈值 → 只留目标颜色的像素（红/绿/蓝各自的阈值，红了跨 0 会自动环绕）
    ② 形态学开/闭（k_open / k_close）+ 可选高斯模糊 → 去噪、补洞
    ③ 找**最大外轮廓** → minEnclosingCircle 得 (cx, cy, r)，再算 circ / fill
    —— 标靶是同心圆环时，**外轮廓的中心就是标靶中心**（各环同心），所以直接用外圈即可 ✓
    ④ 面积/圆度门限过滤，防止把背景大片同色也算进来

★UI（借鉴 ring_stream_ui.py）：左边实时画面（画检测圈 + 十字 + 圆心坐标），
  右边一排参数滑条，`/set` 即时生效、`/state` 轮询回填、按钮做保存/载入/抓拍/冻结。

★★档案就是 **`color_profile.json`**（**与主程序同一个文件、同一套结构**）：
      { "red": {mode,h,hw,s_min,s_max,v_min,v_max}, …,
        "_tune": {k_open,k_close,blur_k,circ_min,min_area_frac,max_area_frac,exposure} }
   —— 调完按"保存档案"，place_demo / grab_demo / route_main 立刻就能用，不需要任何转换。
   （"_tune" 这一段是这次一起补上的：以前调参台只存颜色阈值，k_open/circ_min 这些
     保存时全丢，主程序只能拿代码默认值跑 —— 车主发现的那个坑。）

用法：
    python3 -u target_stream_ui.py --cam 0 --port 8091
    python3 -u target_stream_ui.py --cam 0 --port 8091 --profile color_profile.json
    # 然后浏览器开 http://<板子IP>:8091/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("/root/VisualBot", "/root/VisualBot2", HERE):
    if not _p or not os.path.isdir(_p):
        continue
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import cv2                                                       # noqa: E402
import numpy as np                                               # noqa: E402

COLOR_NAMES = ("red", "green", "blue")
DEFAULT_PROFILE = "color_profile.json"

# 三色的默认阈值（与 color_blob_demo.PRESETS 同一套，只是这里只留红绿蓝）
DEFAULT_PRESETS = {
    "red":   dict(mode="chroma", h=179, hw=8,  s_min=90,  s_max=255, v_min=60,  v_max=255),
    "green": dict(mode="chroma", h=60,  hw=12, s_min=70,  s_max=255, v_min=50,  v_max=255),
    "blue":  dict(mode="chroma", h=115, hw=12, s_min=110, s_max=255, v_min=50,  v_max=255),
}
# "_tune" 段的默认值（与 vision.ColorVision / color_blob_demo.ColorBlobDetector 保持一致）
# ★★k_close=9 是**关键**：闭运算把虚线同心环连成一条实心环带（ring_center_detect_v2.CALIB
#   里就是靠 close_k≈相邻环线间距 这么定的）。k_close=1 = 不连环 → 大环带不成形、
#   只能圈住中心那个小圆 → 灵敏度极低（2026-09-18 车主指出的"识别灵敏度太低"就是这个）。
# ★circ_min 默认 0.45 → 0.30：连环之后外边界变平滑，圆度≈0.86，0.45 其实也够；
#   但留点余量给"环形带不完整"的情况更稳。
DEFAULT_TUNE = dict(k_open=3, k_close=9, blur_k=1, circ_min=0.30,
                    min_area_frac=0.0008, max_area_frac=0.60, exposure=30)

# ----------------------------------------------------------------------
# 参数规格（滑条）—— 名字/范围/步长/提示
# ----------------------------------------------------------------------
PARAM_SPEC = [
    dict(name="h",   mn=0, mx=179, st=1, kind="int", odd=False,
         hint="色相中心: 红≈0/179  绿≈60  蓝≈115"),
    dict(name="hw",  mn=0, mx=60, st=1, kind="int", odd=False,
         hint="色相半宽; 红色跨 0 会自动环绕"),
    dict(name="s_min", mn=0, mx=255, st=1, kind="int", odd=False, hint="饱和度下限(越大越只认浓色)"),
    dict(name="s_max", mn=0, mx=255, st=1, kind="int", odd=False, hint="饱和度上限"),
    dict(name="v_min", mn=0, mx=255, st=1, kind="int", odd=False, hint="明度下限(过曝会把它顶到 255)"),
    dict(name="v_max", mn=0, mx=255, st=1, kind="int", odd=False, hint="明度上限"),
    dict(name="k_open",  mn=1, mx=31, st=2, kind="int", odd=True, hint="形态学开运算核(去小噪点), 1=关"),
    dict(name="k_close", mn=1, mx=31, st=2, kind="int", odd=True, hint="形态学闭运算核(补小洞), 1=关"),
    dict(name="blur_k",  mn=1, mx=15, st=2, kind="int", odd=True, hint="高斯模糊核, 1=关"),
    dict(name="min_area_frac", mn=0.0, mx=0.5, st=0.0001, kind="float", odd=False,
         hint="最小面积占画面比(滤掉碎点)"),
    dict(name="max_area_frac", mn=0.01, mx=1.0, st=0.01, kind="float", odd=False,
         hint="最大面积占画面比(滤掉大片背景)"),
    dict(name="circ_min", mn=0.0, mx=1.0, st=0.01, kind="float", odd=False,
         hint="圆度下限 4πA/P²(圆≈1)"),
    dict(name="exposure", mn=0, mx=1000, st=1, kind="int", odd=False,
         hint="锁定曝光(过曝→减, 太暗→加); K 值按哪个曝光定的就填哪个"),
]
SPEC_BY_NAME = {p["name"]: p for p in PARAM_SPEC}
# ★★必须把"颜色阈值"和"形状/形态学参数"分开（2026-09-18 实测抓到的 bug）：
#   一开始 TUNE_KEYS 写成了"除 exposure 外的全部参数"，于是 _tune 段里那份 h/hw/s_min…
#   会**覆盖**三个颜色各自的阈值 → 红/绿/蓝检测出**完全相同**的结果（都用了 red 的阈值）。
COLOR_KEYS = ("h", "hw", "s_min", "s_max", "v_min", "v_max")
TUNE_KEYS = [p["name"] for p in PARAM_SPEC
             if p["name"] not in COLOR_KEYS and p["name"] != "exposure"]


# ----------------------------------------------------------------------
# 算法：颜色分割 + 圆心
# ----------------------------------------------------------------------
def mask_of(bgr, cfg, h0=None):
    """按 HSV 阈值求目标颜色的二值掩膜（含形态学）。红跨 0 会自动环绕。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    H = hsv[:, :, 0].astype(np.int16)
    S, V = hsv[:, :, 1], hsv[:, :, 2]
    c = int(cfg.get("h", 0)) if h0 is None else int(h0)
    hw = int(cfg.get("hw", 10))
    lo, hi = c - hw, c + hw
    if lo < 0:                                   # 红: 跨 0 → 环绕
        hm = (H <= hi) | (H >= 180 + lo)
    elif hi > 179:                               # 红: 中心在 179 附近 → 往另一头绕
        hm = (H >= lo) | (H <= hi - 180)
    else:
        hm = (H >= lo) & (H <= hi)
    m = (hm & (S >= cfg.get("s_min", 0)) & (S <= cfg.get("s_max", 255))
        & (V >= cfg.get("v_min", 0)) & (V <= cfg.get("v_max", 255)))
    m = m.astype(np.uint8) * 255
    bk = int(cfg.get("blur_k", 1) or 1)
    if bk > 1:
        m = cv2.GaussianBlur(m, (bk, bk), 0)
    for key, op in (("k_open", cv2.MORPH_OPEN), ("k_close", cv2.MORPH_CLOSE)):
        k = int(cfg.get(key, 1) or 1)
        if k > 1:
            ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            m = cv2.morphologyEx(m, op, ker)
    return m


def detect_target(bgr, cfg):
    """找标靶：**最大外轮廓**的圆心/半径。返回 dict(found, cx, cy, r, area, circ, fill, n, mask)。"""
    h, w = bgr.shape[:2]
    m = mask_of(bgr, cfg)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    amin = float(cfg.get("min_area_frac", 0.0008)) * w * h
    amax = float(cfg.get("max_area_frac", 0.60)) * w * h
    cmin = float(cfg.get("circ_min", 0.0))
    best, kept = None, 0
    for c in cnts:
        a = cv2.contourArea(c)
        if a < amin or a > amax:
            continue
        p = cv2.arcLength(c, True)
        circ = 4 * np.pi * a / (p * p + 1e-6) if p > 0 else 0.0
        if circ < cmin:
            continue
        (cx, cy), r = cv2.minEnclosingCircle(c)
        fill = a / (np.pi * r * r + 1e-6)
        kept += 1
        if best is None or a > best["area"]:
            best = dict(cx=float(cx), cy=float(cy), r=float(r), area=float(a),
                        circ=float(circ), fill=float(fill))
    out = {"found": False, "mask": m, "n_contours": len(cnts), "n_kept": kept,
           "k_open": int(cfg.get("k_open", 1) or 1),
           "k_close": int(cfg.get("k_close", 1) or 1)}
    if best:
        out.update(best)
        out["found"] = True
    return out


def annotate(bgr, res, color_name):
    """左：原图 + 检测结果；右：**二值掩膜**（形态学处理后的黑白图）。

    ★2026-09-18 车主："需要和 colorblob 一样整一个经过图像处理和二值化的黑白图进行参考"。
      color_blob_stream 是把掩膜缩到 1/4 摆在右下角；这里改成**并排等大**，
      因为标靶是同心圆环，环线很细 —— 小窗里看不清"哪几圈被阈值吃掉了、哪几圈没吃到"。
      掩膜上还会用黄色描出**保留下来参与判定的轮廓**，一眼能看出门限切得对不对。
    """
    view = bgr.copy()
    if res.get("found"):
        cx, cy, r = int(res["cx"]), int(res["cy"]), int(res["r"])
        cv2.circle(view, (cx, cy), r, (0, 255, 255), 2)
        cv2.drawMarker(view, (cx, cy), (0, 255, 255), cv2.MARKER_CROSS, 22, 2)
        cv2.putText(view, "%s r=%.0f circ=%.2f fill=%.2f" % (color_name, res["r"], res["circ"], res["fill"]),
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    else:
        cv2.putText(view, "%s NOT FOUND (contours=%d)" % (color_name, res.get("n_contours", 0)),
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 128, 255), 2)
    h, w = view.shape[:2]
    cv2.line(view, (w // 2, 0), (w // 2, h), (90, 90, 90), 1)
    cv2.line(view, (0, h // 2), (w, h // 2), (90, 90, 90), 1)

    m = res.get("mask")
    if m is None:
        return view
    mb = cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(mb, cnts, -1, (0, 255, 255), 2)      # 黄=参与判定的轮廓
    cv2.putText(mb, "MASK %s  contours=%d kept=%d" % (color_name, res.get("n_contours", 0),
                                                      res.get("n_kept", 0)),
                (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    cv2.putText(mb, "white = color passes  (k_open=%s k_close=%s)" % (res.get("k_open"), res.get("k_close")),
                (10, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 220, 120), 2)
    return cv2.hconcat([view, mb])


def _estimate_close_k(mask, max_k=31):
    """估"闭运算连环核" —— 严格照 `ring_center_detect_v2.CALIB` 的思路：

        "close_k": 9,   # 闭运算核(px): ≈相邻环线在画面里的间距

    闭运算把**虚线同心环连成一条实心环带**（这就是"膨胀成一个大圆环"）。
    ★这一步是本算法的关键：不连环时，虚线环的每一小段都是独立连通域，
      真正的大环带根本不成形，只能勉强选中中心那个小圆 —— 标靶稍微偏一点、
      或者某几段虚线被反光吃掉，就直接丢目标，表现就是"**识别灵敏度太低**"
      （2026-09-18 车主指出，实测 r 从 157 变成 204、圆度 0.56→0.86）。

    做法：核从 3 扫到 max_k，取**第一个让"最大连通域占掩膜总面积 ≥ KEEP_FRAC"的核**。
      ★判据用"最大块占比"而不是"必须连成 1 块" —— 我们要的是"**环带成形、变成一个够大的圆**"，
        不是"整个掩膜只剩一块"。后者会把核逼得过大（实测要 19~31），过度闭运算会把
        环带糊粗、甚至把旁边的同色反光连进来。
    返回 (best_k, 最大块占比, 连环前的连通域数)。
    """
    KEEP_FRAC = 0.70

    def largest_frac(m, tot):
        """最大连通域**像素数** / 掩膜总像素数。

        ⚠️ 不能用 cv2.contourArea：它按格林公式算多边形面积，遇到自交/非凸轮廓会算出
           **大于实际像素数**的值（实测出现过 144%），判据直接失效 —— 于是估出 k_close=3，
           等于没连环。（2026-09-18 实测抓到。）
        """
        n, _lbl, stats, _c = cv2.connectedComponentsWithStats((m > 0).astype(np.uint8), 8)
        if n <= 1:
            return 0.0
        return float(stats[1:, 4].max()) / max(1.0, tot)

    def n_cc(m):
        return len(cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0])

    tot = float((mask > 0).sum())
    n0 = n_cc(mask)
    if tot <= 0:
        return 1, 0.0, n0
    best_k, best_frac = 1, largest_frac(mask, tot)
    for k in range(3, max_k + 1, 2):
        ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        f = largest_frac(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, ker), tot)
        if f > best_frac:
            best_frac, best_k = f, k
        if f >= KEEP_FRAC:               # 环带已经成形，不必再加大
            break
    return best_k, best_frac, n0


# ----------------------------------------------------------------------
# ★自动标定：从当前帧估一套"能稳稳框住标靶"的阈值
# ----------------------------------------------------------------------
def autotune(bgr, base_tune=None):
    """从当前帧自动估阈值（车主 2026-09-18 要求"加入自动标定功能"）。

    思路（**不假设标靶一定在画面正中**）：
      ① 先按"有色像素"粗筛（S>=60 且 V>=40）—— 白底、灰色爪片自然被排掉；
      ② 在有色像素里做 H 直方图（环形平滑，红跨 0 不会断），取**最强峰**当标靶主色相
         （现场画面里除了彩色标靶就是白底和灰爪片，所以最强峰基本就是标靶）；
      ③ 用峰附近的像素定：h 取峰位、hw 由扫描决定；S/V 取 3%~99% 分位数
         （分位数比 min/max 稳，不会被几个离群点拉飞）；
      ④ 拿这组阈值跑一次检测：
           拿到轮廓 → 反推 circ_min / min_area_frac / max_area_frac（按实测值放宽一点）
           没拿到   → 把 hw 放宽一档重试（最多 5 档）
      ⑤ 返回建议值 + 每一步理由 —— 前端会显示出来，方便你判断这套值可不可信。

    返回 (suggest_dict 或 None, info_dict)。
    """
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    H = hsv[:, :, 0].astype(np.int16)
    S, V = hsv[:, :, 1], hsv[:, :, 2]
    reasons = []

    base = (S >= 60) & (V >= 40)
    n_color = int(base.sum())
    if n_color < 200:
        return None, {"msg": "画面里几乎没有『有色』像素（S>=60 且 V>=40）——"
                             "标靶是不是不在画面里？或者曝光太暗/过曝？",
                      "reasons": reasons}
    reasons.append("有色像素 %d 个（占画面 %.1f%%）" % (n_color, 100.0 * n_color / (h * w)))

    hist = np.bincount(H[base].ravel(), minlength=180).astype(np.float64)
    sm = np.zeros_like(hist)
    for d in range(-4, 5):                       # 环形平滑，让跨 0 的红峰连起来
        sm += np.roll(hist, d)
    h0 = int(np.argmax(sm))
    reasons.append("主色相峰 H=%d（峰位像素 %d）" % (h0, int(hist[h0])))

    dh = np.abs(((H - h0 + 90) % 180) - 90)     # 与 h0 的环形距离(0..90)

    best = None
    for hw in (6, 10, 14, 20, 28):
        sel = base & (dh <= hw)
        n = int(sel.sum())
        if n < 200:
            reasons.append("hw=%d：峰内只剩 %d 像素，跳过" % (hw, n))
            continue
        hs = H[sel]
        s_lo = int(np.percentile(S[sel], 3))
        s_hi = int(np.percentile(S[sel], 99))
        v_lo = int(np.percentile(V[sel], 3))
        v_hi = int(np.percentile(V[sel], 99))
        cfg = dict(h=h0, hw=hw, s_min=s_lo, s_max=min(255, s_hi),
                   v_min=v_lo, v_max=min(255, v_hi))
        # ★★自动标定时**不要**沿用现场那套形状门限（2026-09-18 实测踩到）：
        #   现场可能把 k_open 调到 7~15、circ_min 调到 0.55 ——
        #   开运算核一大，同心圆环的细环线就被腐蚀成**锯齿边**，周长暴涨、圆度掉到 0.3 左右，
        #   于是被 circ_min 一刀切掉 → 现象是"颜色明明估对了，却一个轮廓都保留不下来"
        #   （mask 上显示 contours=1 kept=0 就是这个）。
        #   所以这里先用**中性门限**把颜色调对、拿到真实轮廓，最后再根据实测圆度/面积
        #   反过来建议形状参数。只保留 blur_k（它只是平滑，不会改变拓扑）。
        cfg.update({k: v for k, v in (base_tune or {}).items() if k == "blur_k"})
        cfg["k_open"] = 1
        cfg["k_close"] = 1
        cfg["min_area_frac"] = 0.0002
        cfg["max_area_frac"] = 0.95
        cfg["circ_min"] = 0.0
        r = detect_target(bgr, cfg)
        reasons.append("试 hw=%d（峰内 %d 像素, S=%d..%d V=%d..%d）→ %s"
                       % (hw, n, s_lo, s_hi, v_lo, v_hi,
                          ("检出 r=%.0f circ=%.2f fill=%.2f 面积占比=%.3f%%"
                           % (r["r"], r["circ"], r["fill"], 100.0 * r["area"] / (h * w)))
                          if r["found"] else "没检出"))
        if not r["found"]:
            continue
        # 评分：越圆、越居中、面积越合理越好（用来在多个 hw 之间挑）
        ar = r["area"] / float(h * w)
        cx_off = abs(r["cx"] - w / 2.0) / w
        cy_off = abs(r["cy"] - h / 2.0) / h
        score = r["circ"] * 2.0 + r["fill"] - 1.5 * (cx_off + cy_off) - abs(ar - 0.06) * 4.0
        if best is None or score > best["score"]:
            best = dict(score=score, hw=hw, cfg=cfg, res=r, area_frac=ar)
    if best is None:
        return None, {"msg": "自动标定没找到可用阈值 —— 可能标靶不在画面里，"
                             "或者颜色太淡/过曝。先把标靶对准再试。",
                      "reasons": reasons}

    r = best["res"]
    ar = best["area_frac"]
    sug = {
        "h": h0, "hw": best["hw"],
        "s_min": best["cfg"]["s_min"], "s_max": best["cfg"]["s_max"],
        "v_min": best["cfg"]["v_min"], "v_max": best["cfg"]["v_max"],
        # ★形状门限按实测值放宽：面积给 0.4×~2.5×，圆度略低于实测（留点余量给抖动）
        "min_area_frac": round(max(0.00005, ar * 0.4), 5),
        "max_area_frac": round(min(1.0, max(0.05, ar * 2.5)), 4),
        "circ_min": round(max(0.15, min(0.80, r["circ"] * 0.60)), 3),
    }

    # ★★连环核：拿"最佳颜色"的原始掩膜去扫，估出能把虚线环连成一块的核。
    #   这一步**必须在颜色定下来之后做**，而且是整个标定里最关键的一步
    #   （ring_stream_ui 的 CALIB 就是这么定 close_k 的：≈相邻环线间距）。
    raw_cfg = dict(sug, k_open=1, k_close=1, blur_k=1)
    ck, keep_frac, n_before = _estimate_close_k(mask_of(bgr, raw_cfg))
    sug["k_close"] = ck
    sug["k_open"] = 3                     # 去毛刺；不超过连环核的一半
    sug["blur_k"] = 1
    if n_before > 1 and ck > 1:
        reasons.append("虚线环原本 %d 个独立连通域 → 闭运算核 %d 让最大块占掩膜 %.0f%%"
                       "（ring_stream_ui 里 close_k≈环线间距 的思路；不连环时大环带不成形，"
                       "只能圈住中心小圆，灵敏度极低）"
                       % (n_before, ck, 100.0 * keep_frac))
    else:
        reasons.append("掩膜本来就是 %d 个连通域（标靶可能不是虚线环）→ k_close 用 %d"
                       % (n_before, ck))
    reasons.append("选中 hw=%d（评分最高）" % best["hw"])
    reasons.append("实测面积占比 %.3f%% → min_area_frac=%.5f, max_area_frac=%.4f"
                   % (100.0 * ar, sug["min_area_frac"], sug["max_area_frac"]))
    reasons.append("实测圆度 %.2f → circ_min=%.3f（留余量）" % (r["circ"], sug["circ_min"]))

    # ★★最后必须**复验一次**：把"建议值"和"现场其余形状参数"合起来真跑一遍。
    #   只验证颜色那一段是不够的 —— 现场可能还留着 k_close=9 / blur_k=5（别的调参台存的），
    #   这些同样会改变轮廓；2026-09-18 实测就出现过"标定说成功、用建议值一跑 found=False"。
    #   复验不过就把会干扰的形状参数一并拉回中性值，并把结论写进理由里。
    def _verify(cfg_try):
        r2 = detect_target(bgr, cfg_try)
        return r2

    final_cfg = dict(base_tune or {})
    final_cfg.update(sug)
    r2 = _verify(final_cfg)
    if not r2["found"]:
        # ★复验不过时**绝不能**把连环核拉回 1 —— 那正好把灵敏度打回原形
        #   （2026-09-18 就是这么把主程序瞄准的 k_close 从 9 改成 1、导致"灵敏度太低"的）。
        #   正确方向是**加大**连环核：环线间距估小了就连不成一块。
        for k in (int(ck) * 2 + 1, int(ck) * 3 + 1, 31):
            k = int(min(31, max(3, k | 1)))
            sug["k_close"] = k
            final_cfg["k_close"] = k
            r2 = _verify(final_cfg)
            reasons.append("复验没检出 → 把连环核加大到 %d 再试：%s"
                           % (k, "通过" if r2["found"] else "仍不行"))
            if r2["found"]:
                break
    if r2["found"]:
        reasons.append("✔ 复验通过：cx=%.0f cy=%.0f r=%.0f circ=%.2f fill=%.2f（用这套值现在就能检出）"
                       % (r2["cx"], r2["cy"], r2["r"], r2["circ"], r2["fill"]))
    else:
        reasons.append("✘ 复验仍未检出 —— 别急着保存；先确认标靶在画面里、曝光合适，再点一次")
    return sug, {"msg": "自动标定完成（已复验；先看检测圈稳不稳再保存）",
                 "reasons": reasons, "score": round(best["score"], 3),
                 "verified": bool(r2["found"])}


# ----------------------------------------------------------------------
# 档案读写（就是 color_profile.json）
# ----------------------------------------------------------------------
def load_profile(path):
    """读档案 → (presets, tune)。档案里的颜色阈值 + "_tune" 段。"""
    presets = {k: dict(v) for k, v in DEFAULT_PRESETS.items()}
    tune = dict(DEFAULT_TUNE)
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f) or {}
            for k, v in cfg.items():
                if isinstance(v, dict) and k in presets:
                    presets[k].update({kk: vv for kk, vv in v.items()
                                       if kk in COLOR_KEYS or kk == "mode"})
            # ★只吸收"形状/形态学"那部分：`_tune` 里可能混着别的调参台存进去的颜色阈值
            #   （color_blob_stream 的 snapshot_params 会把 h/hw/s_min… 一起写进来），
            #   照单全收会把三个颜色的阈值全部覆盖成同一个 → 三色检测结果一模一样。
            if isinstance(cfg.get("_tune"), dict):
                tune.update({kk: vv for kk, vv in cfg["_tune"].items() if kk in TUNE_KEYS})
        except Exception as exc:                                 # noqa: BLE001
            print("[profile] %s 读取失败: %r（用默认值）" % (path, exc))
    return presets, tune


def save_profile(path, presets, tune):
    """写档案 —— **保留其它颜色的原有内容**，只更新这三色 + _tune。"""
    cfg = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception:                                        # noqa: BLE001
            cfg = {}
    for k in COLOR_NAMES:
        cfg[k] = dict(presets[k])
    # ★_tune 只存"形状/形态学"参数（TUNE_KEYS），颜色阈值走各颜色自己的段
    cfg["_tune"] = {k: tune.get(k, DEFAULT_TUNE.get(k)) for k in TUNE_KEYS + ["exposure"]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return os.path.abspath(path)


# ----------------------------------------------------------------------
# 状态 + 采集/处理线程
# ----------------------------------------------------------------------
class State:
    def __init__(self, args, presets, tune):
        self.args = args
        self.presets = presets
        self.tune = tune
        self.color = args.color if args.color in COLOR_NAMES else "red"
        self.lock = threading.Lock()
        self.cap = None
        self.jpg = None
        self.raw = None
        self.res = {"found": False}
        self.fps = 0.0
        self.frozen = False
        self.running = True
        self.cfg = self._cfg()

    def _cfg(self):
        """当前颜色的完整配置 = 该颜色阈值 + _tune 里的形状参数。"""
        c = dict(self.presets[self.color])
        c.update({k: self.tune.get(k, DEFAULT_TUNE.get(k)) for k in TUNE_KEYS})
        return c

    def params(self):
        """给网页的值：当前颜色的色阈值 + _tune 的形状参数 + exposure。"""
        out = {}
        for k in ("h", "hw", "s_min", "s_max", "v_min", "v_max"):
            out[k] = int(self.cfg.get(k, 0))
        for k in TUNE_KEYS:
            p = SPEC_BY_NAME[k]
            v = float(self.cfg.get(k, DEFAULT_TUNE.get(k, 0)))
            out[k] = int(round(v)) if p["kind"] == "int" else round(v, 5)
        out["exposure"] = int(self.tune.get("exposure", DEFAULT_TUNE["exposure"]))
        return out

    def apply(self, name, raw):
        """浏览器改一个参数 → 校验 → 写回（色阈值进 presets，形状参数进 tune）。"""
        p = SPEC_BY_NAME.get(name)
        if p is None:
            return False, None, "未知参数: %s" % name
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return False, None, "%s 需要数字" % name
        if p["kind"] == "int":
            v = int(round(v))
            if p["odd"] and v % 2 == 0:
                v += 1
        else:
            v = round(v, 5)
        v = max(p["mn"], min(p["mx"], v))
        if name == "exposure":
            self.tune["exposure"] = int(v)
        elif name in ("h", "hw", "s_min", "s_max", "v_min", "v_max"):
            self.presets[self.color][name] = int(v)
        else:
            self.tune[name] = v
        self.cfg = self._cfg()
        return True, v, "ok"


def open_cam(idx, exposure=None):
    """开相机；优先复用 color_blob_demo 的 open_camera（它会把曝光真正锁死）。"""
    try:
        from color_blob_demo import open_camera
        return open_camera(idx, lock=(exposure is not None), exposure=exposure)
    except Exception as exc:                                     # noqa: BLE001
        print("[cam] 复用 open_camera 失败(%r)，改用裸 VideoCapture" % exc)
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        return cap if cap.isOpened() else None


def capture_loop(st):
    cap = open_cam(st.args.cam, exposure=st.tune.get("exposure"))
    if cap is None:
        print("!! 相机 %d 打不开（被占用？试试 --cam 2）" % st.args.cam)
        st.running = False
        return
    st.cap = cap
    n, t0 = 0, time.time()
    while st.running:
        ok, fr = cap.read()
        if not ok or fr is None:
            time.sleep(0.03)
            continue
        n += 1
        if n % 20 == 0:
            st.fps = 20.0 / max(1e-3, time.time() - t0)
            t0 = time.time()
        with st.lock:
            if not st.frozen:
                st.raw = fr
    try:
        cap.release()
    except Exception:                                            # noqa: BLE001
        pass


def process_loop(st):
    while st.running:
        with st.lock:
            fr = None if st.raw is None else st.raw.copy()
            frozen = st.frozen
        if fr is None:
            time.sleep(0.05)
            continue
        if st.args.width and fr.shape[1] > st.args.width:
            sc = st.args.width / float(fr.shape[1])
            fr = cv2.resize(fr, (int(fr.shape[1] * sc), int(fr.shape[0] * sc)))
        res = detect_target(fr, st.cfg)
        view = annotate(fr, res, st.color)
        ok, jpg = cv2.imencode(".jpg", view, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok:
            with st.lock:
                st.jpg = jpg.tobytes()
                st.res = {k: v for k, v in res.items() if k != "mask"}
                if frozen:
                    st.raw = None            # 冻结时不再刷新
        time.sleep(0.02 if frozen else 0.0)


# ----------------------------------------------------------------------
# 网页（骨架与 ring_stream_ui.py 一致：滑条 + /set + /state 轮询）
# ----------------------------------------------------------------------
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>target stream ui</title>
<style>
 body{margin:0;background:#111;color:#ddd;font:14px system-ui,sans-serif}
 #wrap{display:flex;flex-wrap:wrap}#left{flex:1 1 620px;padding:8px}#right{width:440px;padding:8px}
 img{width:100%;background:#000}.row{display:flex;align-items:center;margin:2px 0}
 .row label{width:130px;color:#9cf;flex:0 0 130px}
 .row input[type=range]{flex:1;min-width:80px}
 .row input[type=number]{width:74px;background:#222;color:#ffd;border:1px solid #444;padding:2px}
 .hint{color:#888;font-size:11px;margin-left:6px;flex:1 1 140px}
 button{background:#2a6;color:#fff;border:0;padding:6px 10px;margin:2px;cursor:pointer}
 button.c{background:#555}button.on{background:#c62}
 #info{white-space:pre-wrap;color:#8f8;font-family:monospace;font-size:12px;max-height:200px;overflow:auto}
 #msg{color:#ffd;font-size:12px;min-height:16px}
</style></head><body><div id="wrap">
 <div id="left"><img id="im" src="/stream"><div id="info">loading...</div></div>
 <div id="right">
  <div class="row"><span style="color:#9cf">目标颜色:</span>
   <button id="bred"   class="c" onclick="go('/color?name=red')">红</button>
   <button id="bgreen" class="c" onclick="go('/color?name=green')">绿</button>
   <button id="bblue"  class="c" onclick="go('/color?name=blue')">蓝</button>
   <span id="cur" style="color:#ffd;margin-left:8px"></span></div>
  <div id="sliders"></div>
  <div class="row"><button onclick="go('/autotune')" style="background:#c62">★ 自动标定</button>
   <button onclick="go('/freeze')">冻结/解冻</button>
   <button onclick="go('/snapshot')">抓拍存图</button></div>
  <div class="row"><button onclick="go('/save')">★ 保存档案</button>
   <button onclick="go('/load')">载入档案</button>
   <button onclick="go('/reset')">恢复默认</button></div>
  <div id="msg"></div></div></div>
<script>
const SPEC = __SPEC__;
const S={}; const box=document.getElementById('sliders');
function frac(x){const s=String(x);const i=s.indexOf('.');return i<0?0:s.length-i-1;}
SPEC.forEach(p=>{
 const row=document.createElement('div');row.className='row';box.appendChild(row);
 const lb=document.createElement('label');lb.textContent=p.name;
 if(p.hint)lb.title=p.hint; row.appendChild(lb);
 const rng=document.createElement('input');rng.type='range';
 rng.min=p.mn;rng.max=p.mx;rng.step=p.st; row.appendChild(rng);
 const num=document.createElement('input');num.type='number';
 num.min=p.mn;num.max=p.mx;num.step=p.st; row.appendChild(num);
 const hint=document.createElement('span');hint.className='hint';
 hint.textContent=p.hint||''; row.appendChild(hint);
 function send(v){fetch('/set?'+p.name+'='+encodeURIComponent(v)).then(r=>r.json()).then(j=>{
   document.getElementById('msg').textContent=j.msg||'ok';
   if(j.value!==undefined){rng.value=j.value;num.value=j.value;}});}
 rng.oninput=()=>{num.value=rng.value;send(rng.value);};
 num.onchange=()=>{let v=parseFloat(num.value);
   if(isNaN(v)){document.getElementById('msg').textContent=p.name+' 需要数字';return;}
   if(p.kind==='int'){v=Math.round(v); if(p.odd&&v%2===0){v+=1;}}
   const dec=(p.kind==='int')?0:Math.max(frac(p.st),1);
   v=parseFloat(v.toFixed(dec)); num.value=v; rng.value=v; send(v);};
 S[p.name]={rng:rng,num:num};});
function go(u){fetch(u).then(r=>r.json()).then(j=>{
 document.getElementById('msg').textContent=j.msg||'ok';
 if(j.text)document.getElementById('info').textContent=j.text;
 load();});}
function load(){fetch('/state').then(r=>r.json()).then(j=>{
 for(const n in S){ if(j.params[n]!==undefined){
   if(document.activeElement!==S[n].num){S[n].num.value=j.params[n];}
   S[n].rng.value=j.params[n];}}
 document.getElementById('cur').textContent=j.color;
 for(const c of ['red','green','blue']){
   document.getElementById('b'+c).className='c'+(c===j.color?' on':'');}
 document.getElementById('info').textContent=
  'color='+j.color+'\\nfps='+(j.fps||0).toFixed(1)+'\\nresult: '+JSON.stringify(j.info,null,1);
 setTimeout(load,800);});}
load();
</script></body></html>"""


def make_page():
    spec = [dict(name=p["name"], mn=p["mn"], mx=p["mx"], st=p["st"],
                 kind=p["kind"], odd=bool(p["odd"]), hint=p.get("hint", ""))
            for p in PARAM_SPEC]
    return PAGE.replace("__SPEC__", json.dumps(spec, ensure_ascii=False))


import http.server                                               # noqa: E402


class Handler(http.server.BaseHTTPRequestHandler):
    st = None

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                            # noqa: C901
        st = self.st
        path, _, query = self.path.partition("?")
        q = {}
        for kv in query.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                q[k] = v
        if path in ("/", "/index.html"):
            body = make_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while st.running:
                    with st.lock:
                        jpg = st.jpg
                    if jpg is None:
                        time.sleep(0.05)
                        continue
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                    self.wfile.write(("Content-Length: %d\r\n\r\n" % len(jpg)).encode())
                    self.wfile.write(jpg)
                    self.wfile.write(b"\r\n")
                    time.sleep(1 / 15.0)
            except Exception:                                    # noqa: BLE001
                pass
        elif path == "/state":
            with st.lock:
                info = dict(st.res)
            self._json({"color": st.color, "params": st.params(),
                        "info": info, "fps": st.fps, "frozen": st.frozen})
        elif path == "/autotune":
            # ★自动标定：拿"当前这一帧"去估阈值（先冻结画面再点，结果更可复现）
            with st.lock:
                fr = None if st.raw is None else st.raw.copy()
            if fr is None:
                self._json({"msg": "还没有画面（相机刚启动？稍等一下再点）"})
                return
            if st.args.width and fr.shape[1] > st.args.width:
                sc = st.args.width / float(fr.shape[1])
                fr = cv2.resize(fr, (int(fr.shape[1] * sc), int(fr.shape[0] * sc)))
            sug, info = autotune(fr, base_tune=st.tune)
            if sug:
                for k, v in sug.items():
                    st.apply(k, v)
                st.cfg = st._cfg()
            self._json({"msg": info.get("msg", "ok"), "params": st.params(),
                        "text": "\n".join(info.get("reasons", []))})
        elif path == "/set":
            msg = "ok"
            val = None
            for k, v in q.items():
                ok, val, m = st.apply(k, v)
                msg = m if ok else ("fail: " + m)
            self._json({"msg": msg, "value": val, "params": st.params()})
        elif path == "/color":
            name = q.get("name", "red")
            if name in COLOR_NAMES:
                st.color = name
                st.cfg = st._cfg()
                self._json({"msg": "color=" + name, "params": st.params()})
            else:
                self._json({"msg": "unknown color"}, 400)
        elif path == "/freeze":
            st.frozen = not st.frozen
            self._json({"msg": "frozen=%s" % st.frozen})
        elif path == "/snapshot":
            try:
                os.makedirs("target_out", exist_ok=True)
                with st.lock:
                    jpg = st.jpg
                name = os.path.join("target_out", "target_%s.jpg" % time.strftime("%H%M%S"))
                with open(name, "wb") as f:
                    f.write(jpg or b"")
                self._json({"msg": "snapshot -> " + os.path.abspath(name)})
            except Exception as e:                               # noqa: BLE001
                self._json({"msg": "snapshot fail %r" % e}, 500)
        elif path == "/save":
            try:
                p = save_profile(st.args.profile, st.presets, st.tune)
                self._json({"msg": "saved %s（含 _tune 里 k_open/circ_min 等 %d 项形状参数）"
                                   % (p, len(TUNE_KEYS))})
            except Exception as e:                               # noqa: BLE001
                self._json({"msg": "save fail %r" % e}, 500)
        elif path == "/load":
            try:
                st.presets, st.tune = load_profile(st.args.profile)
                st.cfg = st._cfg()
                self._json({"msg": "loaded " + os.path.abspath(st.args.profile),
                            "params": st.params()})
            except Exception as e:                               # noqa: BLE001
                self._json({"msg": "load fail %r" % e}, 500)
        elif path == "/reset":
            st.presets = {k: dict(v) for k, v in DEFAULT_PRESETS.items()}
            st.tune = dict(DEFAULT_TUNE)
            st.cfg = st._cfg()
            self._json({"msg": "reset to defaults", "params": st.params()})
        else:
            self._json({"msg": "unknown path"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=0, help="相机索引(默认0=作业相机)")
    ap.add_argument("--port", type=int, default=8091, help="网页端口(默认 8091)")
    ap.add_argument("--color", default="red", choices=list(COLOR_NAMES))
    ap.add_argument("--profile", default=DEFAULT_PROFILE, help="档案文件(默认 color_profile.json)")
    ap.add_argument("--width", type=int, default=0, help="处理宽度(0=原始)")
    a = ap.parse_args()

    presets, tune = load_profile(a.profile)
    st = State(a, presets, tune)
    Handler.st = st
    print("[target] 档案    : %s" % (os.path.abspath(a.profile) if os.path.isfile(a.profile)
                                    else "%s（还没有，保存时新建）" % a.profile))
    print("[target] 标靶颜色: 红/绿/蓝（点页面上的按钮切换）")
    print("[target] 曝光锁  : %s" % tune.get("exposure"))

    t1 = threading.Thread(target=capture_loop, args=(st,), daemon=True)
    t2 = threading.Thread(target=process_loop, args=(st,), daemon=True)
    t1.start()
    t2.start()

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    print("[target] 打开 http://<板子IP>:%d/   （Ctrl+C 退出）" % a.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[target] 退出")
    finally:
        st.running = False
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
