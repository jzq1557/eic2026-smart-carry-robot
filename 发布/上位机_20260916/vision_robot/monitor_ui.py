#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_ui.py —— 双摄监控台（浏览器上看两路实时标注画面）
================================================================================
用途：一场比赛全程盯两台相机 ——
    · `work` 路（前方作业相机）: 圆环定位 + 数字识别，叠加样式与 `ring_stream_ui.py` 一致
      （绿圈=EMA 中心、黄十字=输出中心、红圈=原始中心(同半径)、洋红短线=偏移、左上状态字、右上掩膜小窗）
    · `scan` 路（侧装扫码相机）: 二维码检测，叠加样式与 `qr_stream_demo.py` 一致
      （绿框=码轮廓、框上方写解码内容、左上 fps/状态）

两个相机**各自独立线程**采集+处理，互不阻塞；两路都可调分辨率/限帧，避免在 RDK 上抢 CPU。

用法（远端零窗口，全靠浏览器）：
    python3 -u monitor_ui.py --work-cam 1 --scan-cam 0 --port 8092
    python3 -u monitor_ui.py --work-cam 1 --scan-cam 0 --perf rdk --work-width 512 --scan-width 480
    python3 -u monitor_ui.py --no-cam            # 无相机/本机自测：两路都用合成画面
    python3 -u monitor_ui.py --list-cams         # 先看看机器上有哪些 /dev/video*

页面：  http://<机器IP>:8092/
接口：  /stream/work  /stream/scan（MJPEG）  /state（JSON）  /snapshot（存两路快照）
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import sys
import threading
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if os.path.join(HERE, "..", "校赛") not in sys.path:
    sys.path.insert(0, os.path.join(HERE, "..", "校赛"))

# 复用现成的东西：圆环检测核心 / 相机与性能档公共库 / 任务码解析（可选）
try:
    import ring_center_detect_v2 as ring
except Exception as e:                                        # pragma: no cover
    ring = None
    print("[warn] 圆环检测库不可用: %r（work 路将只推原始画面）" % e)

try:
    from color_blob_demo import open_camera, PERF_PRESETS, detect_perf
except Exception:                                             # pragma: no cover
    PERF_PRESETS = {"low": dict(width=640, stream_width=480, quality=70, threads=2, stream_fps=10),
                    "mid": dict(width=960, stream_width=720, quality=75, threads=2, stream_fps=15),
                    "high": dict(width=1280, stream_width=960, quality=85, threads=4, stream_fps=25),
                    "rdk": dict(width=512, stream_width=512, quality=76, threads=4, stream_fps=12,
                                cfg=dict(thr_block=25, close_k=5, proc_scale=0.34, digit_every=3))}

    def detect_perf():
        return "mid"

    def open_camera(idx=None, lock=True, exposure=None, gst=None, size=(1280, 720)):
        cap = cv2.VideoCapture(idx if idx is not None else 0, cv2.CAP_V4L2)
        return cap if cap.isOpened() else None


def now():
    return time.strftime("%H:%M:%S")


# ==============================================================================
# 一路相机（采集线程 + 处理线程 + 自己的 JPEG 帧缓冲）
# ==============================================================================
class CamWorker:
    def __init__(self, kind, index, width, quality, stream_fps, detect_every,
                 exposure=None, lock=True, log=print, synthetic=False, cfg_extra=None):
        self.kind = kind                 # "work"（圆环） / "scan"（二维码）
        self.index = index
        self.width = int(width or 0)
        self.quality = int(quality)
        self.stream_fps = float(stream_fps or 0)
        self.detect_every = max(1, int(detect_every))
        self.exposure = exposure
        self.lock = lock
        self.log = log
        self.synthetic = synthetic

        self.cap = None
        self.raw = None
        self.jpg = None
        self.lock_obj = threading.Lock()
        self.seq = 0
        self.running = True
        self.fps = 0.0
        self.info = {"found": False}
        self.diag = {}
        self.last_enc = 0.0
        self.frames = 0

        # 圆环检测器（work 路）
        self.det = None
        if kind == "work" and ring is not None:
            c = dict(ring.CALIB)
            c.update((cfg_extra or {}))
            self.det = ring.RingDetector(c)
            self.cfg = c
        # 二维码检测器（scan 路）
        self.qr = cv2.QRCodeDetector() if kind == "scan" else None
        self.code = None
        self.code_pts = None
        self._synth_img = None

    # ---- 相机 ----
    def open(self):
        if self.synthetic:
            self.log("[%s] 合成画面模式（无相机）" % self.kind)
            return True
        self.cap = open_camera(self.index, lock=self.lock, exposure=self.exposure,
                               size=(1280, 720))
        if self.cap is None:
            self.log("[%s] !! /dev/video%s 打不开" % (self.kind, self.index))
            return False
        self.log("[%s] /dev/video%s 就绪 实际 %dx%d"
                 % (self.kind, self.index, int(self.cap.get(3)), int(self.cap.get(4))))
        return True

    def start(self):
        threading.Thread(target=self._capture_loop, daemon=True).start()
        threading.Thread(target=self._process_loop, daemon=True).start()

    def _capture_loop(self):
        while self.running:
            if self.cap is None:
                time.sleep(0.1)
                continue
            ok, fr = self.cap.read()
            if not ok:
                time.sleep(0.03)
                continue
            with self.lock_obj:
                self.raw = fr

    # ---- 合成画面（无相机时的自测素材）----
    def _synthetic(self):
        if self.kind == "work":
            try:
                from ring_stream_ui import synth_ring_scene
                return synth_ring_scene(self.frames // 10 % 3)
            except Exception:
                img = np.full((720, 1280, 3), 40, np.uint8)
                for r in (300, 240, 180, 120):
                    cv2.circle(img, (640, 360), r, (235, 235, 235), 6)
                cv2.putText(img, "2", (600, 400), cv2.FONT_HERSHEY_SIMPLEX,
                            4, (240, 240, 240), 3)
                return img
        # scan：用 OpenCV 自己生成一张真二维码（内容 = 校赛任务码）
        if self._synth_img is None:
            img = np.full((720, 1280, 3), 235, np.uint8)
            text = "123+321"
            made = False
            try:
                enc = cv2.QRCodeEncoder_create()
                qr = enc.encode(text)
                if qr is not None and qr.size:
                    qr = cv2.resize(qr, (420, 420), interpolation=cv2.INTER_NEAREST)
                    qr = cv2.cvtColor(qr, cv2.COLOR_GRAY2BGR)
                    img[150:570, 430:850] = qr
                    made = True
            except Exception:
                pass
            if not made:                       # 退路：画个假的码样子
                cv2.rectangle(img, (430, 150), (850, 570), (0, 0, 0), 3)
                for i in range(6):
                    for j in range(6):
                        if (i + j) % 2 == 0:
                            cv2.rectangle(img, (440 + i * 68, 160 + j * 68),
                                          (440 + i * 68 + 60, 160 + j * 68 + 60), (0, 0, 0), -1)
            cv2.putText(img, "SYNTHETIC QR  (no camera)", (30, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 200), 2)
            self._synth_img = img
        return self._synth_img.copy()

    # ---- 处理主循环 ----
    def _process_loop(self):
        t0, n = time.time(), 0
        while self.running:
            if self.synthetic:
                frame = self._synthetic()
            else:
                with self.lock_obj:
                    frame = self.raw
                if frame is None:
                    time.sleep(0.03)
                    continue
            self.frames += 1

            # 只缩小不放大（对齐 ring_stream_ui 的教训）
            if self.width and frame.shape[1] > self.width:
                sc = self.width / float(frame.shape[1])
                frame = cv2.resize(frame, (self.width, int(frame.shape[0] * sc)),
                                   interpolation=cv2.INTER_AREA)

            if self.kind == "work":
                view = self._draw_work(frame)
            else:
                view = self._draw_scan(frame)

            n += 1
            if time.time() - t0 >= 1.0:
                self.fps = n / (time.time() - t0)
                n, t0 = 0, time.time()

            # 推流限帧（编码是 CPU 大头）
            t = time.perf_counter()
            if self.stream_fps <= 0 or (t - self.last_enc) >= 1.0 / self.stream_fps - 1e-4:
                ok, jpg = cv2.imencode(".jpg", view,
                                       [cv2.IMWRITE_JPEG_QUALITY, self.quality])
                if ok:
                    with self.lock_obj:
                        self.jpg = jpg.tobytes()
                        self.seq += 1
                        self.last_enc = t
            time.sleep(0.001)

    # ---- 叠加：work（样式对齐 ring_stream_ui）----
    def _draw_work(self, frame):
        if self.det is None:
            cv2.putText(frame, "ring detector unavailable", (10, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            return frame
        do_diag = (self.frames % self.detect_every == 0)
        fr = ring.rotate_ccw(frame, self.cfg)
        res = self.det.process(fr)
        found = bool(res.get("found"))
        self.info = {"found": found,
                     "cx": round(res.get("cx_px", -1), 1),
                     "cy": round(res.get("cy_px", -1), 1),
                     "r": round(res.get("r_outer_px", 0), 1),
                     "digit": res.get("digit"),
                     "score": round(res.get("digit_score", 0), 3),
                     "stale": res.get("stale", 0)}
        view = fr
        if found:
            cx, cy = int(res["cx_px"]), int(res["cy_px"])
            r = max(2, int(res["r_outer_px"]))
            cv2.circle(view, (cx, cy), r, (0, 255, 0), 2)                    # 绿圈=EMA 中心
            cv2.drawMarker(view, (cx, cy), (0, 255, 255), cv2.MARKER_CROSS, 20, 2)  # 黄十字
            if res.get("raw"):
                rx, ry = int(res["raw"][0]), int(res["raw"][1])
                cv2.circle(view, (rx, ry), r, (0, 0, 255), 2)                # 红圈=原始中心(同半径)
                if (rx, ry) != (cx, cy):
                    cv2.line(view, (rx, ry), (cx, cy), (255, 0, 255), 1)     # 洋红短线=偏移
            if res.get("digit"):
                cv2.putText(view, "digit=%s(%.2f)" % (res["digit"], res.get("digit_score", 0)),
                            (cx - 60, cy - r - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        tag = "found" if found else "not found"
        line1 = "WORK(cam%s)  fps=%.1f  %s  r=%.0f digit=%s" % (
            self.index, self.fps, tag, self.info["r"], self.info["digit"])
        line2 = "c=(%.0f,%.0f) raw_vs_ema=%s" % (
            self.info["cx"], self.info["cy"],
            round(abs(res.get("raw", (0, 0))[0] - res.get("cx_px", 0)), 1) if found else "-")
        cv2.putText(view, line1, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 255), 2)
        cv2.putText(view, line2, (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
        cv2.putText(view, "green=EMA  RED=raw(same r)  magenta=offset", (10, view.shape[0] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        return view

    # ---- 叠加：scan（样式对齐 qr_stream_demo）----
    def _draw_scan(self, frame):
        if self.qr is None:
            return frame
        do_det = (self.frames % self.detect_every == 0) or (self.code is None)
        if do_det:
            try:
                data, pts, _ = self.qr.detectAndDecode(frame)
            except Exception:
                data, pts = None, None
            self.code = (data.strip().replace(" ", "") if data else None)
            self.code_pts = pts
        self.info = {"found": bool(self.code), "code": self.code}
        view = frame
        if self.code_pts is not None:
            try:
                cv2.polylines(view, [np.asarray(self.code_pts, dtype=np.int32)],
                              True, (0, 255, 0), 2)                    # 绿框=码轮廓
                x0 = int(np.min(self.code_pts[:, 0][:, 0])) if self.code_pts.ndim == 3 else 10
                y0 = int(np.min(self.code_pts[:, 0][:, 1])) if self.code_pts.ndim == 3 else 30
                cv2.putText(view, self.code or "", (x0, max(24, y0 - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            except Exception:
                pass
        txt = "SCAN(cam%s)  code=%s" % (self.index, self.code or "no code")
        cv2.putText(view, txt, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 255), 2)
        cv2.putText(view, "fps=%.1f" % self.fps, (10, 52),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
        return view

    # ---- 取帧 ----
    def get_jpg(self):
        with self.lock_obj:
            return self.jpg

    def mjpeg(self):
        last = -1
        while self.running:
            with self.lock_obj:
                jpg, seq = self.jpg, self.seq
            if jpg and seq != last:
                last = seq
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
            else:
                time.sleep(0.01)

    def state(self):
        return {"kind": self.kind, "cam": self.index, "fps": round(self.fps, 1),
                "synthetic": self.synthetic, "info": self.info}

    def snapshot(self, out_dir):
        jpg = self.get_jpg()
        if not jpg:
            return None
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "%s_%04d.jpg" % (self.kind, int(time.time() % 100000)))
        with open(p, "wb") as f:
            f.write(jpg)
        return p

    def close(self):
        self.running = False
        try:
            if self.cap:
                self.cap.release()
        except Exception:
            pass


# ==============================================================================
# 网页
# ==============================================================================
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>双摄监控台</title>
<style>
 body{margin:0;background:#111;color:#ddd;font:14px system-ui,sans-serif}
 #wrap{display:flex;flex-wrap:wrap}
 .col{flex:1 1 480px;padding:8px}
 .cap{color:#9cf;font-weight:600;margin:2px 0 6px}
 img{width:100%;background:#000;border:1px solid #333}
 #bar{padding:8px 12px;background:#1b1b1b;border-bottom:1px solid #333;font-family:ui-monospace,monospace}
 #bar b{color:#ffd}
 button{background:#2a2a2a;color:#ddd;border:1px solid #444;padding:4px 10px;margin-right:6px;cursor:pointer}
</style></head><body>
<div id="bar">双摄监控台 · <b id="st">连接中...</b>
  <button onclick="snap()">抓拍两路</button><button onclick="location.reload()">刷新</button></div>
<div id="wrap">
  <div class="col"><div class="cap">① 作业相机（前方 · 圆环/数字 · 瞄准用）</div>
    <img src="/stream/work"></div>
  <div class="col"><div class="cap">② 扫码相机（侧装 · 二维码 · 任务码用）</div>
    <img src="/stream/scan"></div>
</div>
<script>
async function tick(){
  try{ const j = await (await fetch('/state')).json();
    const w=j.work, s=j.scan;
    document.getElementById('st').textContent =
      `WORK ${w.fps}fps ${w.info.found?('found r='+w.info.r+' digit='+w.info.digit):'not found'}`
      + ` | SCAN ${s.fps}fps ${s.info.code?('code='+s.info.code):'no code'}`
      + ` | ${new Date().toLocaleTimeString()}`;
  }catch(e){ document.getElementById('st').textContent='状态读取失败'; }
}
async function snap(){ const r=await (await fetch('/snapshot')).text(); alert(r); }
setInterval(tick, 700); tick();
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    work = None
    scan = None
    out_dir = "monitor_out"

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif p == "/stream/work":
            self._mjpeg(self.work)
        elif p == "/stream/scan":
            self._mjpeg(self.scan)
        elif p == "/state":
            self._json({"work": self.work.state(), "scan": self.scan.state(),
                        "ts": now()})
        elif p == "/snapshot":
            a = self.work.snapshot(self.out_dir)
            b = self.scan.snapshot(self.out_dir)
            self._json({"msg": "saved: %s | %s" % (a, b)})
        else:
            self.send_error(404)

    def _send(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _mjpeg(self, cam):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for chunk in cam.mjpeg():
                self.wfile.write(chunk)
        except Exception:
            pass


def list_cams():
    import glob
    devs = sorted(glob.glob("/dev/video*"))
    print("检测到 %d 个 video 设备：" % len(devs))
    for d in devs:
        try:
            with open("/sys/class/video4linux/%s/name" % os.path.basename(d)) as f:
                name = f.read().strip()
        except Exception:
            name = "?"
        print("  %s   %s" % (d, name))
    print("提示：同一台相机常占两个节点（采集 + metadata），能用的是能读出画面的那个。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-cam", type=int, default=1, help="作业相机（前方·瞄准）")
    ap.add_argument("--scan-cam", type=int, default=0, help="扫码相机（侧装）")
    ap.add_argument("--port", type=int, default=8092)
    ap.add_argument("--perf", default="auto", help="性能档 auto/low/mid/high/rdk")
    ap.add_argument("--work-width", type=int, default=None, help="作业路处理/推流宽")
    ap.add_argument("--scan-width", type=int, default=None, help="扫码路处理/推流宽")
    ap.add_argument("--work-fps", type=float, default=None, help="作业路推流限帧")
    ap.add_argument("--scan-fps", type=float, default=None, help="扫码路推流限帧")
    ap.add_argument("--scan-detect-every", type=int, default=2, help="扫码路每 N 帧解一次码")
    ap.add_argument("--quality", type=int, default=75)
    ap.add_argument("--exposure", type=float, default=None,
                    help="给了才锁曝光（不给=保持自动；不同相机合适值差别很大，RDK 上那台 156 会过曝）")
    ap.add_argument("--no-cam", action="store_true", help="无相机：两路都用合成画面")
    ap.add_argument("--no-lock", action="store_true", help="不锁曝光/白平衡")
    ap.add_argument("--out", default="monitor_out")
    ap.add_argument("--list-cams", action="store_true")
    a = ap.parse_args()

    if a.list_cams:
        list_cams()
        return 0

    name = detect_perf() if a.perf in (None, "auto") else str(a.perf)
    p = PERF_PRESETS.get(name, PERF_PRESETS.get("mid", {}))
    ww = a.work_width or p.get("width", 640)
    sw = a.scan_width or max(320, int(p.get("width", 640) * 0.75))
    wf = a.work_fps if a.work_fps is not None else p.get("stream_fps", 12)
    sf = a.scan_fps if a.scan_fps is not None else max(4.0, float(p.get("stream_fps", 12)) * 0.5)
    print("[perf] 档位=%s → work %d宽/%.0ffps   scan %d宽/%.0ffps"
          % (name, ww, wf, sw, sf))

    work = CamWorker("work", a.work_cam, ww, a.quality, wf, 1,
                     exposure=a.exposure, lock=not a.no_lock,
                     synthetic=a.no_cam, cfg_extra=p.get("cfg") or {})
    scan = CamWorker("scan", a.scan_cam, sw, a.quality, sf, a.scan_detect_every,
                     exposure=a.exposure, lock=not a.no_lock, synthetic=a.no_cam)

    for cam in (work, scan):
        if not cam.open() and not a.no_cam:
            print("[warn] %s 路没打开；可只用另一路，或加 --no-cam 看合成画面" % cam.kind)
        cam.start()

    Handler.work, Handler.scan, Handler.out_dir = work, scan, a.out
    try:
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    except OSError as e:
        print("[err] 端口 %d 起不来: %r —— 多半上一个实例还在跑（ps -eo pid,args | grep monitor_ui）" % (a.port, e))
        return 3
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def ip():
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            v = s.getsockname()[0]
            s.close()
            return v
        except Exception:
            return "127.0.0.1"
    print("[web] 浏览器打开 http://%s:%d/   （左=作业相机 右=扫码相机）" % (ip(), a.port))
    print("[tip] 无相机时加 --no-cam 看合成画面；查相机编号用 --list-cams")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        work.close()
        scan.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
