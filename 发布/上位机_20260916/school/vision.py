# -*- coding: utf-8 -*-
"""
vision.py —— 视觉门面（把两份 demo 收进一个接口）
====================================================
· 圆环定位：`ring_center_detect_v2.RingDetector`（aim_and_place 与 ring_stream_ui 用的同一个检测核心）
  —— 输出环心(cx,cy)/外径(r)/置信度/数字；EMA 平滑、突跳清窗、丢失保持都是它的内建行为。
· 颜色识别：`color_blob_demo.ColorBlobDetector`（color_blob_stream 用的同一个算法）
  —— 圆形物料找色块中心/半径，用来"确认这一位是不是该取的料"。
· 相机：复用 `color_blob_demo.open_camera`（V4L2 + 自动回退 + 锁曝光）。

三台机器通用（RDK X5 = 主角）；`MockVision` 让没有相机/硬件时也能把整场流程跑通（--dry-run）。
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# 让本模块能找到 deploy_pi 里的公共库（部署到机器上时它们是同目录的兄弟文件）
for p in (HERE, os.path.join(HERE, "..", "deploy_pi"), "/root/VisualBot", "/home/justin/vision_robot"):
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)


# ----------------------------------------------------------------------
# 真实视觉
# ----------------------------------------------------------------------
class RingVision:
    """圆环/标靶定位（校赛里瞄准的主力）。"""

    def __init__(self, cfg=None, perf="rdk", cam_index=0, lock=True, exposure=156):
        import cv2                                     # noqa: F401  延迟 import
        import ring_center_detect_v2 as ring
        self.cv2 = cv2
        self.ring = ring
        c = dict(ring.CALIB)
        try:
            from color_blob_demo import PERF_PRESETS, detect_perf
            c.update((PERF_PRESETS.get(perf) or {}).get("cfg") or {})
            self.perf = perf if perf in PERF_PRESETS else detect_perf()
        except Exception:
            self.perf = perf
        if cfg:
            c.update(cfg)
        self.cfg = c
        self.det = ring.RingDetector(c)
        self.cap = None
        self.cam_index = cam_index
        self.lock = lock
        self.exposure = exposure

    # ---- 相机 ----
    def open(self, index=None):
        from color_blob_demo import open_camera
        idx = self.cam_index if index is None else index
        self.cap = open_camera(idx, lock=self.lock, exposure=self.exposure)
        return self.cap is not None

    def grab(self, retries=3):
        if self.cap is None:
            return None
        for _ in range(retries):
            ok, fr = self.cap.read()
            if ok and fr is not None:
                return fr
            time.sleep(0.05)
        return None

    # ---- 检测 ----
    def locate(self, frame):
        """返回 dict(found, cx, cy, r, digit, conf, stale)；坐标系 = rotate_ccw 之后的图。"""
        if frame is None:
            return {"found": False}
        fr = self.ring.rotate_ccw(frame, self.cfg)
        w = int(self.cfg.get("proc_w") or 0)
        if w and fr.shape[1] > w:                       # 只缩小不放大
            fr = self.cv2.resize(fr, (w, int(fr.shape[0] * w / fr.shape[1])),
                                 interpolation=self.cv2.INTER_AREA)
        res = self.det.process(fr)
        if not res.get("found"):
            return {"found": False, "stale": res.get("stale", 0)}
        return {"found": True,
                "cx": float(res["cx_px"]), "cy": float(res["cy_px"]),
                "r": float(res["r_outer_px"]), "digit": res.get("digit"),
                "conf": float(res.get("conf", 0.0)), "stale": int(res.get("stale", 0)),
                "shape": fr.shape}

    def close(self):
        try:
            if self.cap:
                self.cap.release()
        except Exception:
            pass


class ColorVision:
    """圆形颜色识别：确认"这一位是不是我要的料"。"""

    def __init__(self, profile=None, perf="rdk"):
        from color_blob_demo import ColorBlobDetector, PRESETS
        self.PRESETS = PRESETS
        self.ColorBlobDetector = ColorBlobDetector
        self.profile = profile
        self.det = {}

    def _det_of(self, color):
        if color not in self.det:
            preset = dict(self.PRESETS.get(color, self.PRESETS["red"]))
            self.det[color] = self.ColorBlobDetector(preset)
        return self.det[color]

    def find(self, frame, color):
        """返回 dict(found, cx, cy, r)（frame 需与瞄准同一坐标系：先 rotate_ccw）。"""
        if frame is None:
            return {"found": False}
        res = self._det_of(color).detect(frame, color)
        info = res.get("info") or {}
        return {"found": bool(info.get("found")),
                "cx": float(info.get("cx", -1)), "cy": float(info.get("cy", -1)),
                "r": float(info.get("r", 0))}

    def close(self):
        pass


# ----------------------------------------------------------------------
# 仿真视觉（--dry-run / 本机回归用；能真实"闭环"收敛）
# ----------------------------------------------------------------------
class MockVision:
    """模拟一个会随车运动而移动的目标：
        `nudge(dx_px, dy_px)` 由瞄准控制器在每次下发脉冲后调用，
        于是 dry-run 里 PID 真的会收敛，而不是死循环。"""

    def __init__(self, cx=200.0, cy=150.0, r=60.0, digit="2",
                 found=True, color=None, w=512, h=384):
        self.cx, self.cy, self.r = float(cx), float(cy), float(r)
        self.digit, self.found, self.color = digit, found, color
        self.w, self.h = w, h
        self.calls = 0
        self.lost_frames = 0

    def nudge(self, dx_px, dy_px):
        self.cx += float(dx_px)
        self.cy += float(dy_px)

    def grab(self, retries=1):
        return ("mock-frame", self.cx, self.cy)

    def locate(self, frame=None):
        self.calls += 1
        if not self.found:
            return {"found": False, "stale": 1}
        if self.lost_frames > 0:                 # 模拟偶发丢目标
            self.lost_frames -= 1
            return {"found": False, "stale": 1}
        return {"found": True, "cx": self.cx, "cy": self.cy, "r": self.r,
                "digit": self.digit, "conf": 1.0, "stale": 0,
                "shape": (self.h, self.w, 3)}

    def find(self, frame, color):
        return {"found": True, "cx": self.cx, "cy": self.cy, "r": self.r,
                "color": color}

    def close(self):
        pass
