#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_ui.py —— 双摄监控台（浏览器上看两路实时标注画面）
================================================================================
用途：一场比赛全程盯两台相机 ——
    · `work` 路（前方作业相机）: 圆环定位 + 数字识别，叠加样式与 `ring_stream_ui.py` 一致
      （绿圈=EMA 中心、黄十字=输出中心、红圈=原始中心(同半径)、洋红短线=偏移、左上状态字）
    · `scan` 路（侧装扫码相机）: 二维码检测，叠加样式与 `qr_stream_demo.py` 一致
      （绿框=码轮廓、框上方写解码内容、左上 fps/状态）

两种跑法：
  ① 独立跑（调参/单测）：两个相机都由它自己开
        python3 -u monitor_ui.py --work-cam 0 --scan-cam 1 --perf rdk --port 8092
  ② **内嵌进主程序**（比赛用，推荐）：主程序自己开着作业相机，这里直接用主程序读到的帧
     （`vision.FrameHub` 共享，**不重复开相机、不重复跑检测**），侧装扫码相机则由本模块开。
        school_main.py 里 `MONITOR = True` 即是这种模式。
     这样比赛时既能看画面，又不会出现"两个进程抢同一个 /dev/videoN"。

画面方向（★2026-09-16 现场踩坑）：
    work 路的旋转不再写死。页面顶部有【不旋转 / 逆时针90° / 顺时针90° / 180°】四个按钮，
    点一下立即生效**并写进 `ring_calib.json`** —— 主程序下次启动按 `CAM_ROTATE="auto"` 自动跟随，
    调参台/监控台/主程序三处方向从此一致（以前调参台调的只有调参台自己知道）。

用法（远端零窗口，全靠浏览器）：
    python3 -u monitor_ui.py --work-cam 0 --scan-cam 1 --port 8092
    python3 -u monitor_ui.py --work-cam 0 --work-rotate none --perf rdk --work-width 512
    python3 -u monitor_ui.py --no-cam            # 无相机/本机自测：两路都用合成画面
    python3 -u monitor_ui.py --list-cams         # 先看看机器上有哪些 /dev/video*

页面：  http://<机器IP>:8092/
接口：  /stream/work  /stream/scan（MJPEG）  /state（JSON）  /snapshot  /rotate?mode=xx
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, "校赛"), os.path.join(HERE, "..", "校赛"),
           os.path.join(HERE, "..", "deploy_pi")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# 复用现成的东西：圆环检测核心 / 相机与性能档公共库 / 画面方向开关（可选）
try:
    import ring_center_detect_v2 as ring
except Exception as e:                                        # pragma: no cover
    ring = None
    print("[warn] 圆环检测库不可用: %r（work 路将只推原始画面）" % e)

try:
    import cam_rotate
except Exception as e:                                        # pragma: no cover
    cam_rotate = None
    print("[warn] cam_rotate 不可用: %r（页面上的方向按钮将失效）" % e)

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


def _rot(frame, cfg):
    """按 cfg 里的 rotate_mode 旋转；没有 cam_rotate 时退回旧字段 rotate_ccw。"""
    if cam_rotate is not None:
        return cam_rotate.apply_rotate(frame, (cfg or {}).get("rotate_mode"))
    if ring is not None:
        return ring.rotate_ccw(frame, cfg or {})
    return frame


# ==============================================================================
# 一路相机（采集线程 + 处理线程 + 自己的 JPEG 帧缓冲）
# ==============================================================================
class CamWorker:
    def __init__(self, kind, index, width, quality, stream_fps, detect_every,
                 exposure=None, lock=True, log=print, synthetic=False, cfg_extra=None,
                 external=None, rotate=None, cam_size=(1280, 720)):
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
        self.external = external         # 有值 = 用外部（主程序）共享的帧，不自己开相机
        # ★采集分辨率。两台相机挂在同一个 USB 2.0 hub 上时，同时 1280×720 MJPG 很容易撞带宽
        #   → 其中一路报 `tryIoctl ... select() timeout` 打不开（2026-09-17 跑图现场就是）。
        #   扫码路不需要高清，降到 640×480 就够（二维码解码对分辨率不敏感）。
        self.cam_size = tuple(cam_size or (1280, 720))

        self.cap = None
        self.raw = None
        self.jpg = None
        self.lock_obj = threading.Lock()
        self.seq = 0
        self.running = True
        self.fps = 0.0
        self.info = {"found": False}
        self.last_enc = 0.0
        self.frames = 0
        self.ext_info = None
        self.ext_seq = -1
        self._done_seq = -1

        # 圆环检测器（work 路）：CALIB + 调参档案 + 性能档 + 画面方向
        self.cfg = {}
        self.det = None
        if kind == "work" and ring is not None:
            c = dict(ring.CALIB)
            if cam_rotate is not None:
                _p, _cfg = cam_rotate.load_calib()
                for k, v in (_cfg or {}).items():
                    if k not in ("rotate_ccw", "rotate_mode"):
                        c[k] = v
            c.update((cfg_extra or {}))
            if cam_rotate is not None:
                c["rotate_mode"] = cam_rotate.resolve(rotate, cam_rotate.mode_from_calib(),
                                                      bool(c.get("rotate_ccw")))
            else:
                c["rotate_mode"] = "none"
            c["rotate_ccw"] = (c["rotate_mode"] == "ccw")
            self.det = ring.RingDetector(c)
            self.cfg = c
        # 二维码检测器（scan 路）
        self.qr = cv2.QRCodeDetector() if kind == "scan" else None
        self.code = None
        self.code_pts = None
        self._synth_img = None

    # ---- 相机 ----
    def open(self):
        if self.external is not None:
            self.log("[%s] 用主程序共享的帧（不再自己开相机）" % self.kind)
            return True
        if self.synthetic:
            self.log("[%s] 合成画面模式（无相机）" % self.kind)
            return True
        self.cap = open_camera(self.index, lock=self.lock, exposure=self.exposure,
                               size=self.cam_size)
        if self.cap is None:
            self.log("[%s] !! /dev/video%s 打不开" % (self.kind, self.index))
            return False
        self.log("[%s] /dev/video%s 就绪 实际 %dx%d"
                 % (self.kind, self.index, int(self.cap.get(3)), int(self.cap.get(4))))
        return True

    def set_rotate(self, mode):
        """运行中换画面方向（页面按钮调用）。返回生效后的模式。"""
        if cam_rotate is None:
            return "none"
        m = cam_rotate.norm_mode(mode, default=self.cfg.get("rotate_mode", "none"))
        self.cfg["rotate_mode"] = m
        self.cfg["rotate_ccw"] = (m == "ccw")
        return m

    def start(self):
        threading.Thread(target=self._capture_loop, daemon=True).start()
        threading.Thread(target=self._process_loop, daemon=True).start()

    def _capture_loop(self):
        while self.running:
            if self.external is not None:                    # 主程序共享帧
                fr, info, seq = self.external.latest()
                if fr is not None and seq != self.ext_seq:
                    with self.lock_obj:
                        self.raw = fr
                        self.ext_info = info
                        self.ext_seq = seq
                time.sleep(0.01)
                continue
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
                    ext_seq = self.ext_seq
                if frame is None:
                    time.sleep(0.03)
                    continue
                # 外部帧：主程序没出新帧就别重复编码（省 CPU，比赛时主程序优先）
                if self.external is not None:
                    if ext_seq == self._done_seq:
                        time.sleep(0.01)
                        continue
                    self._done_seq = ext_seq
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
        if self.external is not None and self.ext_info:
            # 主程序已经检测过了：直接拿它的结果画，**不重复跑检测**（帧也已按方向旋转好）
            ex = self.ext_info
            fr = frame
            res = {"found": bool(ex.get("found")),
                   "cx_px": ex.get("cx", -1), "cy_px": ex.get("cy", -1),
                   "r_outer_px": ex.get("r", 0), "digit": ex.get("digit"),
                   "digit_score": ex.get("conf", 0.0), "stale": ex.get("stale", 0)}
        elif self.det is None:
            cv2.putText(frame, "ring detector unavailable", (10, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            return frame
        else:
            fr = _rot(frame, self.cfg)
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
        cv2.putText(view, "dir=%s  green=EMA  RED=raw  magenta=offset" % self.cfg.get("rotate_mode", "-"),
                    (10, view.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
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
                "synthetic": self.synthetic, "info": self.info,
                "shared": self.external is not None,
                "rotate": self.cfg.get("rotate_mode") if self.kind == "work" else None}

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
 button.on{background:#265;border-color:#4a8}
 #rot{margin-left:10px;color:#9cf}
</style></head><body>
<div id="bar">双摄监控台 · <b id="st">连接中...</b>
  <button onclick="snap()">抓拍两路</button><button onclick="location.reload()">刷新</button>
  <span id="rot">画面方向：
    <button id="r_none" onclick="setRot('none')">不旋转</button>
    <button id="r_ccw" onclick="setRot('ccw')">逆时针90°</button>
    <button id="r_cw" onclick="setRot('cw')">顺时针90°</button>
    <button id="r_180" onclick="setRot('180')">180°</button>
    <span id="rotnow">?</span></span></div>
<div id="wrap">
  <div class="col"><div class="cap">① 作业相机（前方 · 圆环/数字 · 瞄准用）</div>
    <img src="/stream/work"></div>
  <div class="col"><div class="cap">② 扫码相机（侧装 · 二维码 · 任务码用）</div>
    <img src="/stream/scan"></div>
</div>
<script>
function mark(m){
  for(const k of ['none','ccw','cw','180']){
    const b=document.getElementById('r_'+k);
    if(b) b.className = (k===m)?'on':'';
  }
  const t=document.getElementById('rotnow'); if(t) t.textContent = '('+m+')';
}
async function tick(){
  try{ const j = await (await fetch('/state')).json();
    const w=j.work, s=j.scan||{};
    document.getElementById('st').textContent =
      `WORK ${w.fps}fps ${w.info.found?('found r='+w.info.r+' digit='+w.info.digit):'not found'}`
      + (w.shared?' [共享主程序帧]':'')
      + ` | SCAN ${s.enabled===false?'（未启用）':(s.fps+'fps '+(s.info.code?('code='+s.info.code):'no code'))}`
      + ` | ${new Date().toLocaleTimeString()}`;
    if(w.rotate) mark(w.rotate);
  }catch(e){ document.getElementById('st').textContent='状态读取失败'; }
}
async function snap(){ const r=await (await fetch('/snapshot')).text(); alert(r); }
async function setRot(m){
  const r=await (await fetch('/rotate?mode='+m)).json();
  mark(r.mode);
  alert('画面方向已设为 '+r.mode+(r.saved?('\\n已存到 '+r.saved+'（主程序 CAM_ROTATE=auto 会自动跟随）'):''));
}
setInterval(tick, 700); tick();
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    work = None
    scan = None
    out_dir = "monitor_out"

    def log_message(self, *a):
        pass

    @staticmethod
    def _state_of(cam):
        if cam is None:
            return {"kind": "off", "cam": -1, "fps": 0.0, "synthetic": False,
                    "info": {}, "enabled": False, "rotate": None}
        d = cam.state()
        d["enabled"] = True
        return d

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif p == "/stream/work":
            self._mjpeg(self.work)
        elif p == "/stream/scan":
            self._mjpeg(self.scan)
        elif p == "/state":
            self._json({"work": self._state_of(self.work),
                        "scan": self._state_of(self.scan), "ts": now()})
        elif p == "/snapshot":
            a = self.work.snapshot(self.out_dir) if self.work else None
            b = self.scan.snapshot(self.out_dir) if self.scan else None
            self._json({"msg": "saved: %s | %s" % (a, b)})
        elif p == "/rotate":
            q = parse_qs(urlparse(self.path).query)
            mode = (q.get("mode") or ["none"])[0]
            got, saved = "none", None
            if self.work is not None and hasattr(self.work, "set_rotate"):
                got = self.work.set_rotate(mode)
            if cam_rotate is not None:
                saved = cam_rotate.save_mode(got)
            self._json({"mode": got, "saved": saved})
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
        if cam is None:
            self._send(b"this stream is disabled", "text/plain; charset=utf-8", code=404)
            return
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


def local_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        v = s.getsockname()[0]
        s.close()
        return v
    except Exception:
        return "127.0.0.1"


# ==============================================================================
# 可复用入口：主程序内嵌用
# ==============================================================================
def start_embedded(port=8092, work_hub=None, work_cam=None, scan_cam=None,
                   perf="rdk", work_width=None, scan_width=None, work_fps=0.0,
                   scan_fps=None, quality=75, scan_detect_every=2, exposure=None,
                   lock=True, work_rotate=None, scan_rotate=None,
                   work_size=None, scan_size=(640, 480),
                   out_dir="monitor_out", log=print):
    """起一个监控服务，返回 (server, work, scan)；任何一路打不开都不抛异常。

    · work_hub 非空 → work 路直接用主程序共享的帧（推荐；不会抢相机、不重复检测）
    · work_cam >= 0 → work 路自己开相机（独立跑时用）
    · scan_cam >= 0 → scan 路自己开相机；否则第二路标记为"未启用"
    · port 被占用时返回 (None, None, None)，**不影响主程序继续比赛**
    """
    p = PERF_PRESETS.get(perf, PERF_PRESETS.get("mid", {}))
    ww = work_width or p.get("width", 640)
    sw = scan_width or max(320, int(p.get("width", 640) * 0.75))
    sf = scan_fps if scan_fps is not None else max(4.0, float(p.get("stream_fps", 12)) * 0.5)

    if work_hub is not None:
        # ★共享帧模式：帧已经是"按主程序的方向旋转+缩放好"的画面，这里**不能再转**
        #   （否则会转两次）。方向由主程序的 CAM_ROTATE / ring_calib.json 决定。
        work_rotate = "none"

    work = CamWorker("work", work_cam if work_cam is not None else -1, ww, quality,
                     work_fps, 1, exposure=exposure, lock=lock, log=log,
                     synthetic=(work_hub is None and (work_cam is None or work_cam < 0)),
                     cfg_extra=p.get("cfg") or {}, external=work_hub, rotate=work_rotate,
                     cam_size=(work_size or (1280, 720)))
    if not work.open():
        log("[monitor] work 路没打开（主程序仍在跑，只是没有画面）")

    scan = None
    if scan_cam is not None and scan_cam >= 0:
        scan = CamWorker("scan", scan_cam, sw, quality, sf, scan_detect_every,
                         exposure=exposure, lock=lock, log=log, rotate=scan_rotate,
                         cam_size=(scan_size or (640, 480)))
        if not scan.open():
            log("[monitor] scan 路 /dev/video%d 打不开（相机没插 / 被占 / USB 带宽不够 →"
                " 可把 --scan-size 再降一档，如 320x240）" % scan_cam)
            scan = None

    work.start()
    if scan is not None:
        scan.start()

    Handler.work, Handler.scan, Handler.out_dir = work, scan, out_dir
    try:
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    except OSError as e:
        log("[monitor] 端口 %d 起不来: %r —— 可能已有实例在跑（ps -eo pid,args | grep monitor）" % (port, e))
        work.close()
        if scan is not None:
            scan.close()
        return None, None, None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("[monitor] 推流已开： http://%s:%d/   （work=%s%s / scan=%s）"
        % (local_ip(), port,
           "主程序共享帧" if work_hub is not None else ("cam%s" % work_cam),
           "（方向由主程序负责）" if work_hub is not None
           else ("，方向 %s" % work.cfg.get("rotate_mode") if work.cfg else ""),
           ("cam%d" % scan_cam) if scan is not None else "未启用"))
    return srv, work, scan


def stop_embedded(srv, workers):
    for w in (workers or []):
        try:
            if w is not None:
                w.close()
        except Exception:
            pass
    try:
        if srv is not None:
            srv.shutdown()
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-cam", type=int, default=0, help="作业相机（前方·瞄准）；-1=不开")
    ap.add_argument("--scan-cam", type=int, default=1, help="扫码相机（侧装）；-1=不开")
    ap.add_argument("--port", type=int, default=8092)
    ap.add_argument("--perf", default="auto", help="性能档 auto/low/mid/high/rdk")
    ap.add_argument("--work-width", type=int, default=None, help="作业路处理/推流宽")
    ap.add_argument("--scan-width", type=int, default=None, help="扫码路处理/推流宽")
    ap.add_argument("--work-fps", type=float, default=None, help="作业路推流限帧")
    ap.add_argument("--scan-fps", type=float, default=None, help="扫码路推流限帧")
    ap.add_argument("--scan-detect-every", type=int, default=2, help="扫码路每 N 帧解一次码")
    ap.add_argument("--work-rotate", default=None,
                    help="作业画面方向 none/ccw/cw/180（不给=跟随 ring_calib.json）")
    ap.add_argument("--scan-rotate", default=None, help="扫码画面方向（同上）")
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

    if a.no_cam:
        work = CamWorker("work", -1, ww, a.quality, wf, 1, synthetic=True,
                         cfg_extra=p.get("cfg") or {}, rotate=a.work_rotate)
        scan = CamWorker("scan", -1, sw, a.quality, sf, a.scan_detect_every,
                         synthetic=True, rotate=a.scan_rotate)
        for cam in (work, scan):
            cam.open()
            cam.start()
        Handler.work, Handler.scan, Handler.out_dir = work, scan, a.out
        srv = None
        try:
            srv = http.server.ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
        except OSError as e:
            print("[err] 端口 %d 起不来: %r" % (a.port, e))
            return 3
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    else:
        srv, work, scan = start_embedded(
            port=a.port, work_hub=None,
            work_cam=(a.work_cam if a.work_cam is not None and a.work_cam >= 0 else None),
            scan_cam=(a.scan_cam if a.scan_cam is not None and a.scan_cam >= 0 else None),
            perf=name, work_width=a.work_width, scan_width=a.scan_width,
            work_fps=wf, scan_fps=sf, quality=a.quality,
            scan_detect_every=a.scan_detect_every, exposure=a.exposure,
            lock=not a.no_lock, work_rotate=a.work_rotate, scan_rotate=a.scan_rotate,
            out_dir=a.out)
        if srv is None:
            return 3

    print("[web] 浏览器打开 http://%s:%d/   （左=作业相机 右=扫码相机）" % (local_ip(), a.port))
    print("[tip] 画面上方有【画面方向】四个按钮：选对了会存进 ring_calib.json，主程序自动跟随")
    print("[tip] 无相机时加 --no-cam 看合成画面；查相机编号用 --list-cams")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        stop_embedded(srv, [work, scan])
    return 0


if __name__ == "__main__":
    sys.exit(main())
