#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
color_blob_stream.py —— 转盘物料颜色识别(浏览器 UI 版, 树莓派无 GUI 运行)

为什么有这一版:
  上一版用 cv2.imshow 走 Qt 桌面窗口, 在树莓派 VNC 里会拖死整个图形栈
  (只能拔电)。本版:
    · Pi 端不创建任何窗口(Qt/GTK 完全不参与) → 不会卡死
    · 只用 OpenCV + http.server: MJPEG 推流 + 网页滑条/按钮
    · 控制台日志纯 ASCII(避免 VNC 终端缺中文字体显示成方块)
    · 浏览器打开 http://<PiIP>:<port>/ 操作, 电脑手机都行

网页能做的事(等价于原来的 UI):
  · 看实时画面 + 检测框; 点画面 = 指定标定 ROI
  · 拖动滑条改 H/S/V 阈值、色相增幅/压制、形态学核、面积/圆度门限(即时生效)
  · 「自动标定」按钮 = 用当前 ROI 像素分位数+圆均值瞬时算阈值
  · 颜色下拉 1..6(红/黄/绿/蓝/浅蓝/黑); 「存/读档案」写 color_profile.json
  · 「抓拍」保存当前标注图

用法(SSH 里跑, 不需要 VNC):
  python3 color_blob_stream.py --cam 0 --port 8090
  python3 color_blob_stream.py --no-cam --port 8090       # 无相机:合成场景演示
  # 浏览器打开 http://<树莓派IP>:8090/
停止: kill $(cat /tmp/color_blob_stream.pid)
"""
import argparse
import json
import os
import sys
import threading
import time
import http.server

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    # 相机 / 性能档(Pi4 与 Jetson 通用)
    from color_blob_demo import (ColorBlobDetector, PRESETS, COLOR_NAMES,  # noqa: E402
                                 open_camera, synthetic_scene, gst_pipeline,
                                 detect_perf, resolve_perf, PERF_PRESETS)
except Exception:                                              # pragma: no cover
    from color_blob_demo import (ColorBlobDetector, PRESETS, COLOR_NAMES,  # noqa: E402
                                 open_camera, synthetic_scene)

    gst_pipeline = None
    PERF_PRESETS = {
        "low":  dict(width=640,  stream_width=480, quality=70, diag_every=5, threads=2, stream_fps=10),
        "mid":  dict(width=960,  stream_width=720, quality=75, diag_every=3, threads=2, stream_fps=15),
        "high": dict(width=1280, stream_width=960, quality=85, diag_every=1, threads=4, stream_fps=25),
        "rdk":  dict(width=512,  stream_width=512, quality=76, diag_every=20, threads=4, stream_fps=12,
                     cfg={"thr_block": 25, "close_k": 5, "proc_scale": 0.34, "digit_every": 3}),
    }

    def detect_perf():
        return "mid"

    def resolve_perf(args, perf_arg=None):
        name = detect_perf() if perf_arg in (None, "auto") else str(perf_arg)
        p = PERF_PRESETS.get(name, PERF_PRESETS["mid"])
        applied = {}
        for k in ("width", "stream_width", "quality", "stream_fps"):
            if getattr(args, k, None) is None:
                setattr(args, k, p[k])
                applied[k] = p[k]
        return name, applied

TUNE_KEYS = ("boost", "suppress", "boost_band", "k_open", "k_close",
             "circ_min", "use_wb", "drop_glare", "blur_k")
CFG_KEYS = ("h", "hw", "s_min", "s_max", "v_min", "v_max")


class State:
    def __init__(self, det, cap, args):
        self.det = det
        self.cap = cap
        self.args = args
        self.lock = threading.Lock()
        self.jpg = None
        self.info = {"found": False}
        self.fps = 0.0
        self.roi = [0, 0]          # 标定 ROI 中心(0,0=画面中心)
        self.color = args.color
        self.seq = 0

    def snapshot_params(self):
        d = self.det
        out = {k: d.cfg[k] for k in CFG_KEYS}
        out["mode"] = d.cfg.get("mode", "chroma")
        for k in TUNE_KEYS:
            out[k] = getattr(d, k)
        out["min_area_frac"] = d.min_area_frac
        return out


def annotate(frame, det, res, state, extra=""):
    view = frame.copy()
    if res.get("found"):
        i = res["info"]
        cv2.circle(view, (int(i["cx"]), int(i["cy"])), int(i["r"]), (0, 255, 255), 2)
        cv2.rectangle(view, (int(i["cx"] - i["r"]), int(i["cy"] - i["r"])),
                      (int(i["cx"] + i["r"]), int(i["cy"] + i["r"])), (255, 200, 0), 2)
        txt = "%s r=%.0f circ=%.2f fill=%.2f" % (state.color, i["r"], i["circ"], i["fill"])
    else:
        txt = "%s: not found" % state.color
    cv2.putText(view, txt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    c = det.cfg
    cv2.putText(view, "H=%d+-%d S=%d..%d V=%d..%d %s  fps=%.1f %s"
                % (c["h"], c["hw"], c["s_min"], c["s_max"], c["v_min"], c["v_max"],
                   c.get("mode", ""), state.fps, extra),
                (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    rx, ry = state.roi
    if rx or ry:
        cv2.drawMarker(view, (rx, ry), (255, 0, 255), cv2.MARKER_CROSS, 24, 2)
    # 右下角小窗显示掩膜
    m = cv2.cvtColor(res["mask"], cv2.COLOR_GRAY2BGR)
    m = cv2.resize(m, (view.shape[1] // 4, view.shape[0] // 4))
    view[0:m.shape[0], view.shape[1] - m.shape[1]:] = m
    return view


def capture_loop(state):
    """后台采集线程: 把 cap.read() 阻塞挪出主循环(与 qr demo 同思路)。"""
    cap = state.cap
    ok_prev = True
    while state.running:
        if cap is None:
            time.sleep(0.05)
            continue
        ok, frame = cap.read()
        if not ok:
            if ok_prev:
                print("[warn] camera read fail, retrying")
                ok_prev = False
            time.sleep(0.05)
            continue
        ok_prev = True
        with state.lock:
            state.raw = frame


def process_loop(state):
    t0, n, fps = time.time(), 0, 0.0
    seed = 0
    while state.running:
        with state.lock:
            frame = None if state.cap is None else getattr(state, "raw", None)
        if frame is None:
            if state.cap is None:      # 无相机: 合成场景(六色轮转, 按组合缓存)
                cols = ["red", "yellow", "green", "blue", "lightblue", "black"]
                pick = [cols[(seed + k) % 6] for k in range(3)]
                cache = synthetic_scene.__dict__.setdefault("_bench_cache", {})
                key = tuple(pick)
                if key not in cache:       # 合成一张要几十 ms, 别每帧重造(否则 bench 测的是画图)
                    cache[key] = synthetic_scene(pick, seed=seed, glare=True, cast=0.03)
                frame = cache[key]
                seed += 1
                time.sleep(0.05)
            else:
                time.sleep(0.03)
                continue
        det = state.det
        if state.proc_w and frame.shape[1] != state.proc_w:
            fh = int(frame.shape[0] * state.proc_w / frame.shape[1])
            frame = cv2.resize(frame, (state.proc_w, fh), interpolation=cv2.INTER_AREA)
        res = det.detect(frame, state.color)
        n += 1
        if time.time() - t0 >= 1.0:
            fps = n / (time.time() - t0)
            n, t0 = 0, time.time()
        state.fps = fps
        view = annotate(frame, det, res, state)
        # 推流只缩小不放大(对齐 ring UI 的教训): 处理分辨率高时省编码时间
        sw = getattr(state, "stream_w", 0) or 0
        if sw and view.shape[1] > sw:
            sh = int(view.shape[0] * sw / view.shape[1])
            view = cv2.resize(view, (sw, sh), interpolation=cv2.INTER_AREA)
        ok, jpg = False, None
        # 推流限帧: 编码是大头, 检测照常每帧跑(网页 info 每帧更新)
        now = time.perf_counter()
        fps_cap = getattr(state, "stream_fps", 0) or 0
        if (fps_cap <= 0) or (now - getattr(state, "last_enc_t", 0.0) >= 1.0 / fps_cap - 1e-4):
            ok, jpg = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, state.quality])
        with state.lock:
            state.info = res.get("info") or {"found": False}
            state.frame_for_calib = frame
            if ok:
                state.jpg = jpg.tobytes()
                state.last_enc_t = now
                state.seq += 1
        if int(time.time() * 2) % 4 == 0:      # 每 ~2s 打一行 ASCII 日志
            i = state.info
            print("[loop] fps=%.1f color=%s found=%s center=(%d,%d) r=%.0f"
                  % (fps, state.color, i.get("found", False),
                     i.get("cx", -1), i.get("cy", -1), i.get("r", 0)))


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>color blob stream</title>
<style>
 body{margin:0;background:#111;color:#ddd;font:14px system-ui,sans-serif}
 #wrap{display:flex;flex-wrap:wrap}
 #left{flex:1 1 640px;padding:8px}
 #right{width:420px;padding:8px}
 img{width:100%;background:#000;cursor:crosshair}
 .row{display:flex;align-items:center;margin:3px 0}
 .row label{width:150px;color:#9cf}
 .row input[type=range]{flex:1}
 .val{width:52px;text-align:right;color:#ffd}
 button{background:#2a6;color:#fff;border:0;padding:6px 10px;margin:2px;cursor:pointer}
 select,input[type=text]{background:#222;color:#ddd;border:1px solid #444;padding:3px}
 #info{white-space:pre-wrap;color:#8f8;font-family:monospace;font-size:12px}
</style></head><body>
<div id="wrap">
 <div id="left">
  <img id="im" src="/stream">
  <div id="info">loading...</div>
 </div>
 <div id="right">
  <div class="row"><label>颜色</label>
   <select id="color"></select>
   <button onclick="autoCalib()">自动标定(ROI)</button></div>
  <div class="row"><label>ROI x</label><input id="rx" type="text" size="5" value="0">
   <label style="width:60px">y</label><input id="ry" type="text" size="5" value="0">
   <button onclick="setRoi()">设ROI</button></div>
  <div id="sliders"></div>
  <div class="row">
   <button onclick="post('/save')">保存档案</button>
   <button onclick="post('/load')">读取档案</button>
   <button onclick="post('/snapshot')">抓拍</button>
   <span id="msg"></span></div>
 </div>
</div>
<script>
const CFG = [["h",0,179],["hw",1,90],["s_min",0,255],["s_max",1,255],
             ["v_min",0,255],["v_max",1,255]];
const TUNE = [["boost",0.1,4.0,0.1],["suppress",0.1,1.0,0.05],
              ["boost_band",1,90,1],["k_open",1,31,2],["k_close",1,31,2],
              ["circ_min",0,1,0.01],["blur_k",1,15,2]];
let P = {};
function el(t,attrs,parent){const e=document.createElement(t);for(k in attrs)e.setAttribute(k,attrs[k]);parent.appendChild(e);return e}
function buildSlider(key,min,max,step,parent){
 const row=el('div',{class:'row'},parent);
 el('label',{},row).textContent=key;
 const inp=el('input',{type:'range',min:min,max:max,step:step},row);
 const v=el('span',{class:'val'},row);
 inp.oninput=()=>{v.textContent=inp.value; post('/set?'+key+'='+inp.value);};
 return {inp:inp,val:v};
}
const S={};
const sl=document.getElementById('sliders');
CFG.forEach(c=>{S[c[0]]=buildSlider(c[0],c[1],c[2],1,sl)});
TUNE.forEach(c=>{S[c[0]]=buildSlider(c[0],c[1],c[2],c[3],sl)});
S["use_wb"]=buildSlider("use_wb",0,1,1,sl);
S["drop_glare"]=buildSlider("drop_glare",0,1,1,sl);
const sel=document.getElementById('color');
function post(u){return fetch(u,{method:'GET'}).then(r=>r.json()).then(j=>{
 document.getElementById('msg').textContent=j.msg||'ok'; return j;});}
function setColor(){post('/color?name='+sel.value);}
function setRoi(){const x=parseInt(document.getElementById('rx').value)||0;
 const y=parseInt(document.getElementById('ry').value)||0;
 post('/roi?x='+x+'&y='+y).then(()=>load());}
function autoCalib(){post('/calib').then(()=>load());}
document.getElementById('im').onclick=function(e){
 const r=this.getBoundingClientRect(); const fw=P.w||1280, fh=P.h||720;
 const x=Math.round((e.clientX-r.left)/r.width*fw), y=Math.round((e.clientY-r.top)/r.height*fh);
 document.getElementById('rx').value=x; document.getElementById('ry').value=y;
 post('/roi?x='+x+'&y='+y);};
function load(){fetch('/state').then(r=>r.json()).then(j=>{
 P=j; if(!sel.options.length){j.colors.forEach(c=>{const o=document.createElement('option');
   o.value=c;o.textContent=c;sel.appendChild(o);}); sel.value=j.color;
   sel.onchange=setColor;}
 for(k in S){ if(j.params[k]!==undefined){ S[k].inp.value=j.params[k];
   S[k].val.textContent=(typeof j.params[k]==='number')?
     (Math.round(j.params[k]*100)/100):j.params[k]; } }
 document.getElementById('rx').value=j.roi[0]; document.getElementById('ry').value=j.roi[1];
 document.getElementById('info').textContent=JSON.stringify(j.info)+
   "\\nfps="+j.fps.toFixed(1)+"  color="+j.color+
   "\\n点击画面=选ROI; 滑条即时生效";
 setTimeout(load,800);});}
load();
</script></body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    state = None

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        st = self.state
        path, _, query = self.path.partition("?")
        q = {}
        for kv in query.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                q[k] = v
        if path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            last = -1
            try:
                while True:
                    with st.lock:
                        jpg, seq = st.jpg, st.seq
                    if jpg is not None and seq != last:
                        last = seq
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        elif path == "/state":
            with st.lock:
                info = dict(st.info or {"found": False})
                frame = getattr(st, "frame_for_calib", None)
            h, w = (frame.shape[:2] if frame is not None else (720, 1280))
            self._json({"color": st.color, "colors": COLOR_NAMES,
                        "params": st.snapshot_params(), "roi": st.roi,
                        "info": info, "fps": st.fps, "w": w, "h": h})
        elif path == "/set":
            d = st.det
            try:
                for k, v in q.items():
                    if k in CFG_KEYS:
                        d.cfg[k] = int(float(v))
                    elif k in ("boost", "suppress"):
                        setattr(d, k, float(v))
                    elif k in ("boost_band", "k_open", "k_close", "blur_k"):
                        iv = int(float(v))
                        setattr(d, k, max(1, iv if k == "blur_k" else (iv | 1)))
                    elif k == "circ_min":
                        d.circ_min = float(v)
                    elif k == "use_wb":
                        d.use_wb = bool(int(float(v)))
                    elif k == "drop_glare":
                        d.drop_glare = bool(int(float(v)))
                msg = "set ok"
            except Exception as e:
                msg = "set fail: %r" % e
            self._json({"msg": msg, "params": st.snapshot_params()})
        elif path == "/color":
            name = q.get("name", "red")
            if name in PRESETS:
                st.color = name
                st.det.cfg = dict(PRESETS[name])
                self._json({"msg": "color=" + name})
            else:
                self._json({"msg": "unknown color"}, 400)
        elif path == "/roi":
            st.roi = [int(float(q.get("x", 0))), int(float(q.get("y", 0)))]
            self._json({"msg": "roi=%s" % st.roi, "roi": st.roi})
        elif path == "/calib":
            with st.lock:
                frame = getattr(st, "frame_for_calib", None)
            if frame is None:
                self._json({"msg": "no frame yet"}, 503)
            else:
                x, y = st.roi
                if not x and not y:
                    x, y = frame.shape[1] // 2, frame.shape[0] // 2
                cfg, npx = st.det.auto_calibrate(frame, x, y)
                if cfg:
                    PRESETS[st.color] = dict(cfg)
                    st.det.cfg = dict(PRESETS[st.color])
                    print("[calib] %s <- %s (%d px)" % (st.color, cfg, npx))
                    self._json({"msg": "calib ok %s px=%d" % (cfg, npx),
                                "params": st.snapshot_params()})
                else:
                    self._json({"msg": "ROI too few valid pixels"}, 400)
        elif path == "/save":
            try:
                # ★★2026-09-18 修（车主发现："调完提示 saved color_profile.json，但 k_open
                #   之类的参数一个没保存过"）：以前这里只写 PRESETS（各颜色的色阈值），
                #   而面板上的 k_open / k_close / circ_min / min_area_frac / blur_k /
                #   use_wb / drop_glare / boost / suppress / boost_band **一个都没写** →
                #   一保存就丢，主程序只能拿代码默认值跑（"面板上调好了、跑起来又不对"）。
                #   现在把 snapshot_params() 一起存进档案的 `_tune` 段
                #   （下划线开头，不会和颜色名冲突；vision.ColorVision 会读它并注入检测器）。
                prof = dict(PRESETS)
                prof["_tune"] = st.snapshot_params()
                with open(st.args.profile, "w") as f:
                    json.dump(prof, f, ensure_ascii=False, indent=2)
                self._json({"msg": "saved " + os.path.abspath(st.args.profile)
                                   + "（含 k_open/circ_min/min_area 等 %d 项形状参数）"
                                   % len(st.snapshot_params())})
            except Exception as e:
                self._json({"msg": "save fail %r" % e}, 500)
        elif path == "/load":
            try:
                with open(st.args.profile) as f:
                    cfg = json.load(f)
                tune = cfg.pop("_tune", None)          # ★形状/形态学参数单独取出来
                PRESETS.update(cfg)
                st.det.cfg = dict(PRESETS[st.color])
                if tune:                               # ★并且还原回检测器（以前读档也丢这些）
                    d = st.det
                    for k, v in tune.items():
                        try:
                            if k in ("use_wb", "drop_glare"):
                                setattr(d, k, bool(v))
                            elif k in ("circ_min", "min_area_frac", "max_area_frac",
                                       "boost", "suppress"):
                                setattr(d, k, float(v))
                            elif k in ("k_open", "k_close", "blur_k", "boost_band"):
                                setattr(d, k, max(1, int(v)))
                        except Exception:                            # noqa: BLE001
                            pass
                self._json({"msg": "loaded " + st.args.profile
                                   + ("（含 %d 项形状参数）" % len(tune) if tune else ""),
                            "params": st.snapshot_params()})
            except Exception as e:
                self._json({"msg": "load fail %r" % e}, 500)
        elif path == "/snapshot":
            os.makedirs(st.args.out, exist_ok=True)
            with st.lock:
                jpg = st.jpg
            if jpg is None:
                self._json({"msg": "no frame"}, 503)
            else:
                p = os.path.join(st.args.out, "snap_%04d.jpg" % int(time.time() % 100000))
                with open(p, "wb") as f:
                    f.write(jpg)
                self._json({"msg": "saved " + p})
        else:
            self._json({"msg": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=0, help="相机索引(默认0; 失败自动回退)")
    ap.add_argument("--no-cam", action="store_true", help="不打开相机(合成场景)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--color", default="red")
    ap.add_argument("--profile", default="color_profile.json")
    ap.add_argument("--out", default="color_demo_out")
    ap.add_argument("--exposure", type=float, default=None)
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--width", type=int, default=None,
                    help="处理(检测)宽度(默认: 性能档 low/mid/high=640/960/1280; 0=不缩放)")
    ap.add_argument("--stream-width", type=int, default=None,
                    help="推流最长边(默认: 性能档 480/720/960; 只缩小不放大, 编码是大头)")
    ap.add_argument("--quality", type=int, default=None, help="JPEG 质量(默认: 性能档 70/75/85)")
    ap.add_argument("--stream-fps", type=float, default=None,
                    help="推流限帧(默认: 性能档 10/15/25; 0=不限, 检测仍每帧跑)")
    # 性能档 / 相机类型: 同一份代码同时跑 Pi4 / Jetson Orin / RDK X5; auto 自动识别
    ap.add_argument("--perf", default="auto", choices=["auto", "low", "mid", "high", "rdk"],
                    help="性能档(默认 auto: Jetson→high / RDK X5→rdk / 否则按核数); 显式参数优先")
    ap.add_argument("--gst", nargs="?", const="auto", default=None,
                    help="用 GStreamer 管线取流(Jetson CSI 摄像头需要); "
                         "`--gst`=auto 自动生成, `--gst none` 关闭, 也可给完整管线字符串")
    ap.add_argument("--sensor-id", type=int, default=0, help="CSI sensor-id(--gst auto 时用)")
    ap.add_argument("--cam-width", type=int, default=1280, help="相机采集宽(UVC 时用)")
    ap.add_argument("--cam-height", type=int, default=720, help="相机采集高(UVC 时用)")
    args = ap.parse_args()

    perf_name, perf_applied = resolve_perf(args, args.perf)
    print("[perf] 档位=%s%s" % (perf_name,
                                ("  " + str(perf_applied)) if perf_applied else "  (参数已显式指定)"))

    gst = args.gst
    if gst is not None and str(gst).lower() in ("auto", "1", "true"):
        gst = gst_pipeline(args.cam_width, args.cam_height, 30, args.sensor_id) if gst_pipeline else None
        print("[cam] GStreamer 管线: %s" % gst)
    elif gst is not None and str(gst).lower() in ("none", "0", "false", ""):
        gst = None

    cap = None
    if not args.no_cam:
        cap = open_camera(args.cam, lock=not args.no_lock, exposure=args.exposure,
                          gst=gst, size=(args.cam_width, args.cam_height))
        if cap is None and gst:
            print("[cam] CSI 管线失败, 回退 V4L2(UVC)方式再试一次 ...")
            cap = open_camera(args.cam, lock=not args.no_lock, exposure=args.exposure,
                              size=(args.cam_width, args.cam_height))
        if cap is None:
            print("[warn] no camera found (tried %s and 0..3) -> synthetic" % args.cam)
    det = ColorBlobDetector(PRESETS.get(args.color, PRESETS["red"]))
    st = State(det, cap, args)
    st.running = True
    st.raw = None
    st.frame_for_calib = None
    st.proc_w = args.width
    st.stream_w = args.stream_width
    st.stream_fps = float(args.stream_fps or 0)
    st.last_enc_t = 0.0
    st.quality = args.quality

    Handler.state = st
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
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
    print("[web] open http://%s:%d/   (Ctrl-C or kill $(cat /tmp/color_blob_stream.pid) to stop)"
          % (ip(), args.port))
    try:
        with open("/tmp/color_blob_stream.pid", "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    if cap is not None:
        threading.Thread(target=capture_loop, args=(st,), daemon=True).start()
    try:
        process_loop(st)
    except KeyboardInterrupt:
        pass
    finally:
        st.running = False
        time.sleep(0.2)
        if cap is not None:
            cap.release()
        print("[bye] stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
