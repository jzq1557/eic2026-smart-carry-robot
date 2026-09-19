#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
圆环靶标中心检测 —— 极简版(树莓派4B + USB摄像头)
=================================================
思路: 不区分6个同心环。密排细环整体上就是一个"宽环带":
  灰度 -> 自适应二值化(环线是暗色) -> 闭运算接断线 -> 最大连通域
  -> 最小外接圆 -> 圆心即靶心。无霍夫/无聚类/无标定。

用法:
  python3 ring_center_simple.py              # 有显示器: 开窗
  python3 ring_center_simple.py --stream     # SSH: 浏览器看 http://<树莓派IP>:8080/stream
  python3 ring_center_simple.py --no-show    # SSH: 只看终端中心坐标
  python3 ring_center_simple.py 图片.jpg      # 单张图片调试
"""

import os
import sys
import time
import threading
import cv2 as cv

CFG = {
    "cam_id": 0,
    "width": 1280,
    "height": 720,
    "thr_block": 41,        # 自适应阈值块大小: 越大越抗光照不均
    "thr_c": 8,             # 提取灵敏度: 调大只留更暗的线
    "close_k": 9,           # 闭运算核(px): ≈相邻环线在画面里的间距
    "min_area_frac": 0.002,  # 连通域至少占画面0.2%, 过滤小噪声
    "ema": 0.5,             # 中心平滑系数(0~1, 越大越稳)
}


def open_camera(cfg):
    backend = cv.CAP_V4L2 if sys.platform.startswith('linux') else cv.CAP_DSHOW
    cap = cv.VideoCapture(cfg["cam_id"], backend)
    if not cap.isOpened():
        return None
    cap.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv.CAP_PROP_FRAME_WIDTH, cfg["width"])
    cap.set(cv.CAP_PROP_FRAME_HEIGHT, cfg["height"])
    cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
    return cap


def find_center(frame, cfg):
    """返回 (cx, cy, r) 或 None。"""
    gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
    gray = cv.GaussianBlur(gray, (5, 5), 0)
    bw = cv.adaptiveThreshold(gray, 255, cv.ADAPTIVE_THRESH_GAUSSIAN_C,
                              cv.THRESH_BINARY_INV, cfg["thr_block"], cfg["thr_c"])
    k = cv.getStructuringElement(cv.MORPH_ELLIPSE, (cfg["close_k"], cfg["close_k"]))
    bw = cv.morphologyEx(bw, cv.MORPH_CLOSE, k)
    contours, _ = cv.findContours(bw, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    h, w = gray.shape
    r_max = 0.5 * min(h, w)     # 真靶标外接圆不会超过画面短边一半, 滤掉背景伪影
    for c in sorted(contours, key=cv.contourArea, reverse=True):
        if cv.contourArea(c) < cfg["min_area_frac"] * w * h:
            return None
        (cx, cy), r = cv.minEnclosingCircle(c)
        if r <= r_max:
            return float(cx), float(cy), float(r)
    return None


def start_stream(port):
    import http.server
    state = type("S", (), {"frame": None})()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.startswith("/stream"):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
            self.end_headers()
            try:
                while True:
                    if state.frame:
                        self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(state.frame)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return state


def main():
    cfg = dict(CFG)
    argv = sys.argv[1:]
    stream_port = None
    if "--stream" in argv:
        i = argv.index("--stream")
        if i + 1 < len(argv) and argv[i + 1].isdigit():
            stream_port = int(argv[i + 1])
            argv = argv[:i] + argv[i + 2:]
        else:
            stream_port = 8080
            argv = argv[:i] + argv[i + 1:]
    img_arg = next((a for a in argv if not a.startswith("--")), None)
    show = "--no-show" not in argv
    if show and sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        show = False
        print("[auto] SSH无显示, 终端输出模式; 想看画面加 --stream")

    state = start_stream(stream_port) if stream_port else None
    if state:
        print(f"[stream] 浏览器打开 http://<树莓派IP>:{stream_port}/stream")

    cap = None if img_arg else open_camera(cfg)
    img = cv.imread(img_arg) if img_arg else None
    if cap is None and img is None:
        print("打不开相机")
        return

    sx = sy = sr = None
    stale = 0
    t0, n = time.time(), 0
    while True:
        if cap is not None:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
        else:
            frame = img
            time.sleep(0.03)

        r = find_center(frame, cfg)
        if r is not None:
            cx, cy, rad = r
            if sx is None:
                sx, sy, sr = cx, cy, rad
            else:
                a = cfg["ema"]
                sx += a * (cx - sx)
                sy += a * (cy - sy)
                sr += a * (rad - sr)
            stale = 0
        else:
            stale += 1

        n += 1
        if time.time() - t0 >= 1.0:
            fps = n / (time.time() - t0)
            if sx is not None and stale <= 15:
                msg = f"center=({sx:.1f},{sy:.1f}) r={sr:.0f} fps={fps:.1f}"
                if stale:
                    msg += f" stale={stale}"
                print(msg)
            else:
                print(f"not found  fps={fps:.1f}")
            n, t0 = 0, time.time()

        disp = frame.copy()
        if sx is not None and stale <= 15:
            cv.line(disp, (int(sx) - 15, int(sy)), (int(sx) + 15, int(sy)), (0, 0, 255), 2)
            cv.line(disp, (int(sx), int(sy) - 15), (int(sx), int(sy) + 15), (0, 0, 255), 2)
            cv.circle(disp, (int(sx), int(sy)), int(sr), (0, 255, 255), 2)
            cv.putText(disp, f"({sx:.0f},{sy:.0f})", (10, 30),
                       cv.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        else:
            cv.putText(disp, "not found", (10, 30),
                       cv.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        if state is not None:
            okj, jpg = cv.imencode(".jpg", disp, [cv.IMWRITE_JPEG_QUALITY, 80])
            if okj:
                state.frame = jpg.tobytes()
        if show:
            cv.imshow("ring center", disp)
            if cv.waitKey(1) & 0xFF in (27, ord("q")):
                break

    if cap is not None:
        cap.release()


if __name__ == "__main__":
    main()
