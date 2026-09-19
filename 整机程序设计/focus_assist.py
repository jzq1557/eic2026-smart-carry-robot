#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对焦辅助工具 —— 帮物理调焦(拧镜头)时找到最清晰的焦点
====================================================
背景: Microdia LRCP G720P 是定焦摄像头(UVC 无 focus 控件, 软件无法调焦)。
  只能物理拧镜头。本工具实时显示画面 + 清晰度分数(拉普拉斯方差, 越大越清晰),
  拧镜头时盯着分数调到最大, 即可让焦点落在目标距离(如 20cm 地面)。

用法:
  python3 focus_assist.py --stream [port]   # 浏览器 http://<IP>:8080/ 看画面+分数
  python3 focus_assist.py --no-show         # SSH 终端, 每秒打印分数(无画面)

分数含义:
  - 整体: 全画面方差; 区域: 9宫格中央3格(地面/目标通常在中下区域)
  - 变化幅度很大(几十~几百), 对比分数相对大小即可, 无需绝对阈值
"""
import os
import sys
import time
import threading

import cv2
import numpy as np

CFG = {
    "width": 1280,
    "height": 720,
    "rotate_ccw": True,     # 与主 demo 一致: 画面逆时针旋转90°
}

_PORT = 8080


def rotate_ccw(frame):
    return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)


def sharpness(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(g, cv2.CV_64F).var()


def start_stream(port):
    import http.server

    class State:
        frame = None

    state = State()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/stream'):
                self.send_response(200)
                self.send_header('Content-Type',
                                 'multipart/x-mixed-replace; boundary=frame')
                self.end_headers()
                try:
                    while True:
                        jpg = state.frame
                        if jpg is not None:
                            self.wfile.write(b'--frame\r\n')
                            self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                            self.wfile.write(jpg)
                            self.wfile.write(b'\r\n')
                        time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            elif self.path in ('/', '/index.html'):
                html = (b'<html><body style="margin:0;background:#111">'
                        b'<img src="/stream" style="width:100%;max-width:960px">'
                        b'</body></html>')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('0.0.0.0', port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return state


def main():
    argv = sys.argv[1:]
    port = _PORT
    if '--stream' in argv:
        i = argv.index('--stream')
        if i + 1 < len(argv) and argv[i + 1].isdigit():
            port = int(argv[i + 1])
    show = '--stream' in argv or '--no-show' not in argv
    if show and sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):
        show = False

    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    if not cap.isOpened():
        print("Error: Cannot open camera (被占用? 先杀残留进程)")
        return
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CFG["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CFG["height"])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    # 后台采集线程
    cam_state = {"stop": False, "frame": None, "lock": threading.Lock()}

    def capture():
        while not cam_state["stop"]:
            ok, f = cap.read()
            if ok:
                with cam_state["lock"]:
                    cam_state["frame"] = f
            else:
                time.sleep(0.01)

    threading.Thread(target=capture, daemon=True).start()

    state = start_stream(port)
    print(f"[stream] 浏览器打开 http://<树莓派IP>:{port}/ 实时画面+清晰度分数")

    last_score = 0.0
    t0, n = time.time(), 0
    while True:
        with cam_state["lock"]:
            frame = cam_state["frame"]
            cam_state["frame"] = None
        if frame is None:
            time.sleep(0.01)
            continue
        frame = rotate_ccw(frame)
        h, w = frame.shape[:2]

        s_full = sharpness(frame)
        # 9宫格中央3格(目标通常在中下区域): 中上/正中/中下
        third_h, third_w = h // 3, w // 3
        s_top = sharpness(frame[third_h:2 * third_h, third_w:2 * third_w])
        s_bot = sharpness(frame[2 * third_h:, third_w:2 * third_w])
        s_cen = sharpness(frame[third_h:2 * third_h, :])
        last_score = s_cen

        n += 1
        if time.time() - t0 >= 1.0:
            fps = n / (time.time() - t0)
            print(f"[focus] fps={fps:.1f} 全画面={s_full:7.1f} | "
                  f"中央区={s_top:7.1f} 下半中央={s_bot:7.1f} (下半=地面/近目标)")
            n, t0 = 0, time.time()

        if state is not None or show:
            disp = frame.copy()
            # 画中央区框 + 分数
            cv2.rectangle(disp, (third_w, third_h), (2 * third_w, 2 * third_h),
                          (0, 255, 255), 2)
            cv2.rectangle(disp, (third_w, 2 * third_h), (2 * third_w, h),
                          (0, 255, 0), 2)
            cv2.putText(disp, f"center={s_top:.0f} lower={s_bot:.0f}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (0, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(disp, "turn lens until score is MAX",
                        (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 200, 255), 1, cv2.LINE_AA)
            if state is not None:
                okj, jpg = cv2.imencode('.jpg', disp, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if okj:
                    state.frame = jpg.tobytes()

        if not show:
            continue
        cv2.imshow("focus assist", disp)
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord('q')):
            break

    cam_state["stop"] = True
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
