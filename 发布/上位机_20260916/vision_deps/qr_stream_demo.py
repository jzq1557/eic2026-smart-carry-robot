#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
二维码识别推流 demo —— 智能搬运赛项·任务码二维码
=================================================
识别画面中的二维码(支持多个), 用绿色框标出并在框上方用黄色标注识别内容;
MJPEG 推流到浏览器, 控制台同步打印识别内容(内容变化即打印 + 每秒一行状态)。

用法:
  python3 qr_stream_demo.py --stream            # 推流: 浏览器打开 http://192.168.3.30:8080/
  python3 qr_stream_demo.py --stream 8090       # 指定端口
  python3 qr_stream_demo.py 图片.jpg             # 离线识别单张图片(循环显示)
  python3 qr_stream_demo.py --no-show           # SSH 无显示, 纯终端输出
"""

import os
import sys
import time
import threading
import numpy as np
import cv2 as cv

cv.setNumThreads(4)

CFG = {
    "camera_id": 0,
    "width": 1280,
    "height": 720,
    # ---- 帧率优化(摄像头720p原生上限约17fps, read固定~60ms) ----
    "qr_scale": 0.5,            # 解码分辨率: 0.5=半分辨率(实测200px以上码100%正确, 快2.6倍)
    "detect_every": 3,          # 隔几帧解码一次(结果帧间复用); 1=每帧, 越小越灵敏
    "lock_exposure": True,
    "rotate_ccw": True,         # 摄像头画面逆时针旋转90°(适配安装方向);
                                # 旋转在读帧后立即做, 输出角点坐标均为旋转后画面坐标
}

# 复用一个检测器, 避免每帧重建
_QR_DET = cv.QRCodeDetector()


def start_capture(cap, state):
    """后台采集线程: 摄像头持续出帧到共享槽, 主循环取最新帧处理。
    把 cap.read() 的阻塞等待移出主循环, 帧率可顶到摄像头自身上限。"""
    while not state["stop"]:
        ok, frame = cap.read()
        if ok:
            with state["lock"]:
                state["frame"] = frame
        else:
            time.sleep(0.01)


def get_lan_ip():
    """取局域网IP(默认路由接口); 失败回退 192.168.3.30。"""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "192.168.3.30"


def rotate_ccw(frame, cfg):
    """摄像头画面逆时针旋转90°(适配安装方向), 关闭则原样返回。
    必须在解码前调用: 这样输出角点坐标天然就是旋转后画面的坐标。"""
    if cfg.get("rotate_ccw", False):
        return cv.rotate(frame, cv.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def decode_qr(frame, scale=1.0):
    """识别画面中的二维码, 返回 [(data, 四角点(4,2)), ...]; 坐标已换算回原图。"""
    view = cv.resize(frame, None, fx=scale, fy=scale,
                     interpolation=cv.INTER_AREA) if scale != 1.0 else frame
    ok, infos, points, _ = _QR_DET.detectAndDecodeMulti(view)
    res = []
    if not ok or infos is None:
        return res
    for data, pts in zip(infos, points):
        if not data or pts is None:
            continue
        if scale != 1.0:
            pts = pts / scale
        res.append((data, pts.reshape(-1, 2)))
    return res


# ----------------------------------------------------------------------
# HTTP MJPEG 推流(与圆环 demo 相同)
# ----------------------------------------------------------------------
def start_stream(port=8080):
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
                        b'<img src="/stream" style="width:100%;max-width:960px"></body></html>')
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
    cfg = dict(CFG)
    argv = sys.argv[1:]
    stream_port = None
    if '--stream' in argv:
        i = argv.index('--stream')
        if i + 1 < len(argv) and argv[i + 1].isdigit():
            stream_port = int(argv[i + 1])
            argv = argv[:i] + argv[i + 2:]
        else:
            stream_port = 8080
            argv = argv[:i] + argv[i + 1:]
    # --gst [管线]: Jetson CSI 摄像头取流(UVC 摄像头不需要)。
    #   不给值 = auto 自动生成 nvarguscamerasrc 管线; 也可直接给完整管线字符串。
    gst_arg = None
    if '--gst' in argv:
        i = argv.index('--gst')
        if i + 1 < len(argv) and not argv[i + 1].startswith('--'):
            gst_arg = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
        else:
            gst_arg = 'auto'
            argv = argv[:i] + argv[i + 1:]
    img_arg = next((a for a in argv if not a.startswith('--')), None)
    use_image = img_arg is not None
    show = '--no-show' not in argv
    if show and sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):
        show = False
        print('[auto] SSH无显示, 终端输出模式; 想看画面加 --stream')

    cap = None
    if use_image:
        frame = cv.imread(img_arg)
        if frame is None:
            print("cannot read image:", img_arg)
            return
        frame = rotate_ccw(frame, cfg)
    else:
        opened_by_gst = False
        if gst_arg:
            pipeline = gst_arg if gst_arg != 'auto' else (
                "nvarguscamerasrc sensor-id=0 ! "
                "video/x-raw(memory:NVMM),width=%d,height=%d,framerate=30/1 ! "
                "nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
                "video/x-raw,format=BGR ! appsink drop=1 max-buffers=1"
                % (cfg["width"], cfg["height"]))
            print("[cam] GStreamer 管线: %s" % pipeline)
            cap = cv.VideoCapture(pipeline, cv.CAP_GSTREAMER)
            if cap.isOpened():
                ok, _ = cap.read()
                if not ok:
                    cap.release()
                    cap = None
            else:
                cap = None
            if cap is None:
                print("[warn] GStreamer 管线打不开, 回退 V4L2(UVC)方式")
            else:
                opened_by_gst = True
        if cap is None:
            backend = cv.CAP_V4L2 if sys.platform.startswith('linux') else cv.CAP_DSHOW
            cap = cv.VideoCapture(cfg["camera_id"], backend)
        if not cap.isOpened():
            print("Error: Cannot open camera")
            return
        if not opened_by_gst:      # GStreamer 后端不支持这些 V4L2 属性
            cap.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv.CAP_PROP_FRAME_WIDTH, cfg["width"])
            cap.set(cv.CAP_PROP_FRAME_HEIGHT, cfg["height"])
            cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
            if cfg["lock_exposure"]:
                cap.set(cv.CAP_PROP_AUTO_EXPOSURE, 0.25)
                cap.set(cv.CAP_PROP_EXPOSURE, 156)
                cap.set(cv.CAP_PROP_AUTO_WB, 0)
                cap.set(cv.CAP_PROP_WB_TEMPERATURE, 5000)

    state = start_stream(stream_port) if stream_port else None
    if state:
        print(f"[stream] 浏览器打开 http://{get_lan_ip()}:{stream_port}/ 查看实时画面")

    # 后台采集线程(仅摄像头模式): 读取等待不再阻塞主循环
    cam_state = None
    if cap is not None:
        cam_state = {"stop": False, "frame": None, "lock": threading.Lock()}
        threading.Thread(target=start_capture, args=(cap, cam_state),
                         daemon=True).start()

    last_print = None           # 识别内容变化时打印
    fps_t0, fps_n, last_fps = time.time(), 0, 0.0
    detect_every = 1 if use_image else max(1, int(cfg.get("detect_every", 2)))
    frame_no = 0
    last_qrs = []
    while True:
        if cap is not None:
            with cam_state["lock"]:
                frame = cam_state["frame"]
                cam_state["frame"] = None     # 取走后清槽, 等新帧(帧率=真实采集率)
            if frame is None:
                time.sleep(0.01)
                continue
            frame = rotate_ccw(frame, cfg)
        else:
            time.sleep(0.05)

        # 解码节流: 隔 detect_every 帧解码一次, 中间复用上次结果;
        # 半分辨率没找到时周期性用全分辨率兜底(防小码/远码漏检)
        frame_no += 1
        if frame_no % detect_every == 0:
            qrs = decode_qr(frame, cfg.get("qr_scale", 0.5))
            if not qrs and frame_no % (detect_every * 8) == 0:
                qrs = decode_qr(frame, 1.0)
            last_qrs = qrs
        else:
            qrs = last_qrs
        contents = tuple(d for d, _ in qrs)

        # 内容变化 -> 打印识别结果
        if contents != last_print:
            last_print = contents
            if contents:
                print("[QR] 识别到 " + " | ".join(contents))
            else:
                print("[QR] 未识别到二维码")

        fps_n += 1
        if time.time() - fps_t0 >= 1.0:
            last_fps = fps_n / (time.time() - fps_t0)
            cur = " | ".join(contents) if contents else "not found"
            print(f"[perf] fps={last_fps:.1f} QR={cur}")
            fps_n, fps_t0 = 0, time.time()

        disp = None
        if show or state is not None:
            disp = frame                       # 就地标注, 省去整帧 copy
            for data, pts in qrs:
                pts_i = pts.astype(int)
                cv.polylines(disp, [pts_i], True, (0, 255, 0), 2)
                x0, y0 = int(pts[:, 0].min()), int(pts[:, 1].min())
                y0 = max(y0 - 8, 24)
                cv.putText(disp, data, (x0, y0), cv.FONT_HERSHEY_SIMPLEX,
                           0.7, (0, 255, 255), 2, cv.LINE_AA)
            if contents:
                txt = "QR: " + " | ".join(contents)
            else:
                txt = "QR: not found"
            cv.putText(disp, txt, (10, 20), cv.FONT_HERSHEY_SIMPLEX,
                       0.6, (0, 255, 255), 1)
            cv.putText(disp, f"fps={last_fps:.1f}", (10, 40),
                       cv.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
            if state is not None:
                okj, jpg = cv.imencode('.jpg', disp, [cv.IMWRITE_JPEG_QUALITY, 80])
                if okj:
                    state.frame = jpg.tobytes()

        if use_image and not show:
            # 单张图片调试 + 无显示(SSH): 打印结果后退出, 不要无限循环
            cur = " | ".join(contents) if contents else "not found"
            print(f"[result] QR={cur}")
            break
        if not show:
            continue
        cv.imshow("QR detect", disp)
        key = cv.waitKey(1) & 0xFF
        if key in (27, ord('q')):
            break

    if cap is not None:
        if cam_state is not None:
            cam_state["stop"] = True
        cap.release()
    cv.destroyAllWindows()


if __name__ == "__main__":
    main()
