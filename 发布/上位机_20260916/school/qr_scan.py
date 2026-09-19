# -*- coding: utf-8 -*-
"""
qr_scan.py —— 侧装摄像头扫码（校赛：出启动区后前进途中监听）
================================================================
规则差异：校赛的码在**侧面**，车是"开到扫码位 → 一边低速通过/停稳一边监听"。
所以这里做三件事：
  1. `decode_qr(frame)`：OpenCV QRCodeDetector（单帧）
  2. `listen(...)`：连续采帧，**同一个码连续 N 帧一致**才采纳（防误读/半张码）
  3. `scan_while_moving(...)`：可选的"边走边扫"（速度由 route 决定），失败自动退化为停稳扫

本模块只用 cv2，不依赖下位机；dry-run 时用 `mock_code` 直接给结果。
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import school_config as SC                                   # noqa: E402


def _make_detector():
    import cv2
    return cv2.QRCodeDetector()


def decode_qr(frame, det=None):
    """单帧解码；返回字符串或 None。"""
    if frame is None:
        return None
    import cv2
    d = det or _make_detector()
    try:
        data, _pts, _ = d.detectAndDecode(frame)
    except Exception:
        return None
    if not data:
        return None
    return data.strip().replace(" ", "")


def listen(vision, cfg=None, timeout=None, mock_code=None, log=print):
    """连续监听直到"同一码连续 N 帧一致"或超时。
    返回 (code, ok)：超时时 ok=False，code=兜底码（保证比赛能继续跑完）。"""
    cfg = cfg or SC
    timeout = SC.T_SCAN if timeout is None else timeout
    if mock_code is not None:
        log("[qr] dry-run 直接给出码: %s" % mock_code)
        return mock_code, True
    try:
        import cv2                                          # noqa: F401
    except Exception:
        log("[qr] 本机/该环境没有 cv2（纯逻辑模式）→ 用兜底码 %s" % SC.CODE_DEFAULT)
        return SC.CODE_DEFAULT, False
    det = _make_detector()
    last, same, n = None, 0, 0
    t0 = time.time()
    while time.time() - t0 < timeout:
        frame = vision.grab() if hasattr(vision, "grab") else None
        code = decode_qr(frame, det)
        n += 1
        if code and SC.parse_code(code)[0]:
            if code == last:
                same += 1
            else:
                last, same = code, 1
            if same >= SC.QR_CONSISTENT_N:
                log("[qr] 采纳任务码 %s（连续 %d 帧一致，共采 %d 帧）" % (code, same, n))
                return code, True
        time.sleep(SC.QR_SCAN_INTERVAL_S)
    log("[qr] !! 扫码超时（%.0fs，采了 %d 帧），使用兜底码 %s —— 请检查侧装相机与光源"
        % (timeout, n, SC.CODE_DEFAULT))
    return SC.CODE_DEFAULT, False


def scan_while_moving(lower, vision, cfg, route, log=print):
    """边走边扫：先低速通过扫码区，同时监听；没扫到就倒回去停稳再扫一次。"""
    log("[qr] 低速通过扫码区并监听 ...")
    code, ok = listen(vision, timeout=SC.T_SCAN, log=log)
    if ok:
        return code, True
    # 停稳补扫：回退一点，停住再扫（多花 ~2s，换稳定）
    lower.move("back", 10)
    time.sleep(0.3)
    return listen(vision, timeout=SC.T_SCAN * 0.6, log=log)
