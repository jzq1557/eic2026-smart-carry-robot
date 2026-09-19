#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cam_rotate.py —— 作业相机「画面方向」的唯一开关（主程序 / 监控台 / 调参台三处共用）
================================================================================
为什么单独一个文件：
    画面旋转原来是写死在 `ring_center_detect_v2.CALIB["rotate_ccw"]` 里的（树莓派时代
    相机倒装 → True）。换到 RDK X5 后相机装法不同，而**调参台能在面板上调 rotate_ccw
    并存进 `ring_calib.json`，主程序却只读 CALIB 常量** —— 于是出现
    "我在调参台调好了方向，主程序里又是错的"。2026-09-16 现场就是这么翻的车
    （画面被转成 720×1280 竖图 → 圆环检测不稳 → 三次瞄准全失败）。

四种模式（命令行 `--work-rotate` / 主程序 `CAM_ROTATE` / 页面按钮都能选）：
    none = 不旋转          （相机横装、画面本来就正 —— RDK 上先试这个）
    ccw  = 逆时针 90°      （`cv2.ROTATE_90_COUNTERCLOCKWISE`，旧默认）
    cw   = 顺时针 90°
    180  = 上下颠倒

优先级（后者被前者覆盖）：
    ring_center_detect_v2.CALIB["rotate_ccw"]  <  `ring_calib.json` 里存的值  <  显式传参
所以：调参台/监控台页面里选一次方向并保存 → 主程序以后自动跟它一致（CAM_ROTATE="auto"）。
"""
from __future__ import annotations

import json
import os

ROTATE_MODES = ("none", "ccw", "cw", "180")

_ALIAS = {
    "": "auto", "auto": "auto", "default": "auto",
    "0": "none", "false": "none", "no": "none", "off": "none", "none": "none",
    "no-rotate": "none", "norotate": "none", "raw": "none", "原图": "none", "不旋转": "none",
    "1": "ccw", "true": "ccw", "yes": "ccw", "on": "ccw",          # 兼容旧的 rotate_ccw 布尔
    "ccw": "ccw", "ccw90": "ccw", "90ccw": "ccw", "left": "ccw", "逆时针": "ccw",
    "cw": "cw", "cw90": "cw", "90cw": "cw", "right": "cw", "顺时针": "cw",
    "180": "180", "flip": "180", "flip180": "180", "upsidedown": "180", "颠倒": "180",
}


def norm_mode(mode, default="none"):
    """把各种写法归一成 none/ccw/cw/180；"auto"/None/无法识别时返回 default。
    （"auto"=跟随档案，由 resolve() 负责，norm_mode 自己不当它是模式。）"""
    if mode is None:
        return default
    m = _ALIAS.get(str(mode).strip().lower())
    if not m or m == "auto":
        return default
    return m


def apply_rotate(frame, mode):
    """按模式旋转一帧；frame 为 None 或模式非法时原样返回。"""
    m = norm_mode(mode, default="none")
    if frame is None or m == "none":
        return frame
    import cv2
    if m == "ccw":
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if m == "cw":
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    return cv2.rotate(frame, cv2.ROTATE_180)


def calib_candidates(name="ring_calib.json"):
    """ring_calib.json 的候选路径（本地开发 / 部署到板子后都能找到）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return [os.path.join(os.getcwd(), name), name,
            os.path.join(here, name), os.path.join(here, "..", name),
            os.path.join(here, "..", "deploy_pi", name)]


def find_calib(name="ring_calib.json"):
    for p in calib_candidates(name):
        if p and os.path.isfile(p):
            return p
    return None


def load_calib(name="ring_calib.json"):
    """读调参台存的参数档案；读不到返回 (None, {})。"""
    p = find_calib(name)
    if not p:
        return None, {}
    try:
        with open(p, encoding="utf-8") as f:
            return p, (json.load(f) or {})
    except Exception as exc:                                     # noqa: BLE001
        print("[cam_rotate] %s 读取失败: %r" % (p, exc))
        return p, {}


def mode_from_calib(name="ring_calib.json"):
    """从档案里取方向：优先 rotate_mode，其次旧的 rotate_ccw 布尔。没有则 None。"""
    _p, cfg = load_calib(name)
    if not cfg:
        return None
    if cfg.get("rotate_mode") not in (None, ""):
        return norm_mode(cfg["rotate_mode"], default="none")
    if "rotate_ccw" in cfg:
        return "ccw" if cfg["rotate_ccw"] else "none"
    return None


def save_mode(mode, name="ring_calib.json"):
    """把方向写回档案（保留其它键）；档案不存在就新建。返回写入路径或 None。"""
    m = norm_mode(mode, default="none")
    p = find_calib(name) or os.path.join(os.getcwd(), name)
    cfg = {}
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception:                                        # noqa: BLE001
            cfg = {}
    cfg["rotate_mode"] = m
    cfg["rotate_ccw"] = (m == "ccw")          # 兼容老开关/老代码
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return p
    except Exception as exc:                                     # noqa: BLE001
        print("[cam_rotate] 写 %s 失败: %r" % (p, exc))
        return None


def resolve(rotate=None, calib_mode=None, calib_default=False):
    """算出最终模式：
       rotate     —— 显式指定（主程序 CAM_ROTATE / 命令行）；"auto"/None = 跟随档案
       calib_mode —— 档案里的值（可为 None）
       calib_default —— 连档案都没有时，用 CALIB["rotate_ccw"] 换算出来的值
    """
    if rotate not in (None, "", "auto", "auto-detect"):
        return norm_mode(rotate, default="none")
    if calib_mode:
        return norm_mode(calib_mode, default="none")
    return "ccw" if calib_default else "none"


if __name__ == "__main__":                                       # 自测
    import sys
    if len(sys.argv) > 1:
        print("norm_mode(%r) = %s" % (sys.argv[1], norm_mode(sys.argv[1])))
    else:
        p, cfg = load_calib()
        print("档案: %s" % (p or "（没有）"))
        print("档案里的方向: %s" % (mode_from_calib() or "（没存）"))
        for k in ROTATE_MODES:
            print("  %-5s -> %s" % (k, norm_mode(k)))
