# -*- coding: utf-8 -*-
"""
vision.py —— 视觉门面（把两份 demo 收进一个接口）
====================================================
· 圆环定位：`ring_center_detect_v2.RingDetector`（aim_and_place 与 ring_stream_ui 用的同一个检测核心）
  —— 输出环心(cx,cy)/外径(r)/置信度/数字；EMA 平滑、突跳清窗、丢失保持都是它的内建行为。
· 颜色识别：`color_blob_demo.ColorBlobDetector`（color_blob_stream 用的同一个算法）
  —— 圆形物料找色块中心/半径，用来"确认这一位是不是该取的料"。
· 相机：复用 `color_blob_demo.open_camera`（V4L2 + 自动回退 + 可选锁曝光）。
· **画面方向**：统一走 `cam_rotate.py`（none/ccw/cw/180）。优先级：
  本文件显式传的 `rotate` > `ring_calib.json` 里存的方向 > `ring_center_detect_v2.CALIB["rotate_ccw"]`。
  ★2026-09-16 现场教训：以前主程序只读 CALIB 常量，调参台里调好的方向存进 ring_calib.json
  却没人读 → "我在面板上明明调过，主程序里方向又是错的"。现在两边通了。
· **调参档案**：`ring_calib.json`（圆环阈值等）与 `color_profile.json`（三色阈值）若存在，
  自动合并进来 —— 调参台/监控台页面里调好并保存的参数，主程序直接生效。

三台机器通用（RDK X5 = 主角）；`MockVision` 让没有相机/硬件时也能把整场流程跑通（--dry-run）。
"""
from __future__ import annotations

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# 让本模块能找到 deploy_pi 里的公共库（本地开发是兄弟目录，部署到机器上时是同目录的兄弟文件）
for p in (HERE, os.path.join(HERE, ".."), os.path.join(HERE, "..", "deploy_pi"),
          "/root/VisualBot", "/root/VisualBot_current", "/root/VisualBot_current/校赛",
          "/home/justin/vision_robot", "/home/justin/vision_robot/校赛"):
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

try:                                            # 画面方向开关（缺失也不致命，退化成"不旋转"）
    import cam_rotate
except Exception as _e:                                          # noqa: BLE001
    cam_rotate = None
    print("[warn] cam_rotate 不可用: %r（画面将不做旋转）" % _e)

try:                                            # 参数集中区（滤波/阻尼等现场可调）
    import school_config as _SC
except Exception as _e:                                          # noqa: BLE001
    _SC = None
    print("[warn] school_config 不可用: %r（滤波参数用内置默认值）" % _e)


def _sc(name, default):
    """从 school_config 读一个参数；读不到就退回内置默认值。

    ★为什么要这样：滤波参数以前**硬编码**在 ColorVision/BlobTracker 里
      （ema=0.5 / jump_px=120），现场想"让它更稳一点"只能改代码。
      现在统一挂到 school_config 的参数区，`grab_demo.py` 里也能用 `k` 现场调。
    """
    v = getattr(_SC, name, None) if _SC is not None else None
    return default if v is None else v


# ----------------------------------------------------------------------
# 帧共享：主程序读到的帧顺手喂给监控推流（避免监控台再开一次相机抢资源）
# ----------------------------------------------------------------------
class FrameHub:
    """线程安全的"最新一帧"信箱：主程序 publish，监控推流 latest。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._info = None
        self._seq = 0

    def publish(self, frame, info=None):
        with self._lock:
            self._frame = frame
            self._info = info
            self._seq += 1

    def latest(self):
        """返回 (frame, info, seq)；没有帧时 frame 为 None。"""
        with self._lock:
            return self._frame, self._info, self._seq


# ----------------------------------------------------------------------
# 真实视觉
# ----------------------------------------------------------------------
class RingVision:
    """圆环/标靶定位（校赛里瞄准的主力）。"""

    def __init__(self, cfg=None, perf="rdk", cam_index=0, lock=True, exposure=None,
                 rotate="auto", hub=None, calib="ring_calib.json"):
        import cv2                                     # noqa: F401  延迟 import
        import ring_center_detect_v2 as ring
        self.cv2 = cv2
        self.ring = ring
        c = dict(ring.CALIB)

        # ① 性能档（rdk 档会覆盖 thr_block/close_k/proc_scale/digit_every 等）
        try:
            from color_blob_demo import PERF_PRESETS, detect_perf
            c.update((PERF_PRESETS.get(perf) or {}).get("cfg") or {})
            self.perf = perf if perf in PERF_PRESETS else detect_perf()
        except Exception:                                        # noqa: BLE001
            self.perf = perf

        # ② 调参台存下的圆环档案（阈值/EMA/proc 等），让"面板上调好的"真正生效
        self.calib_path, self.calib_cfg = (None, {})
        if cam_rotate is not None:
            self.calib_path, self.calib_cfg = cam_rotate.load_calib(calib)
        for k, v in (self.calib_cfg or {}).items():
            if k in ("rotate_ccw", "rotate_mode"):
                continue                       # 方向单独处理（见下）
            c[k] = v

        # ③ 调用方显式给的覆盖
        if cfg:
            c.update(cfg)

        # ④ 画面方向：显式 > 档案 > CALIB 常量
        calib_mode = None
        if self.calib_cfg:
            if self.calib_cfg.get("rotate_mode") not in (None, ""):
                calib_mode = self.calib_cfg["rotate_mode"]
            elif "rotate_ccw" in self.calib_cfg:
                calib_mode = "ccw" if self.calib_cfg["rotate_ccw"] else "none"
        if cam_rotate is not None:
            self.rotate_mode = cam_rotate.resolve(rotate, calib_mode,
                                                  calib_default=bool(c.get("rotate_ccw")))
        else:
            self.rotate_mode = "none"
        c["rotate_mode"] = self.rotate_mode
        c["rotate_ccw"] = (self.rotate_mode == "ccw")      # 兼容旧字段/旧代码

        self.cfg = c
        self.det = ring.RingDetector(c)
        self.cap = None
        self.cam_index = cam_index
        self.lock = lock
        self.exposure = exposure
        self.hub = hub
        self.last_frame = None
        self.last_loc = {"found": False}
        self.actual_index = None

    # ---- 相机 ----
    def open(self, index=None):
        from color_blob_demo import open_camera
        idx = self.cam_index if index is None else index
        self.cap = open_camera(idx, lock=self.lock, exposure=self.exposure)
        self.actual_index = idx
        # ★驱动侧缓冲取 2（2026-09-20 在 RDK X5 上实测确定）：
        #   =1 会让驱动无法流水线，取流被砍到 15.0 FPS（v4l2-ctl 直测驱动层有 28.4 FPS）；
        #   =2 即回到 30.0 FPS，额外延迟仅约 1 帧(~33ms)。原注释"压到最小"的代价是吞吐减半。
        #   grab() 里的"抽干队列"仍然保留：即便缓冲为 2，抽干后取到的仍是最新帧。
        #   ⚠️ 只设 BUFFERSIZE，**绝不在这里碰分辨率**：分辨率一变 K_PX2STEP_* 就全失效。
        try:
            if self.cap is not None:
                self.cap.set(38, 2)         # CAP_PROP_BUFFERSIZE = 2
        except Exception:                                            # noqa: BLE001
            pass
        return self.cap is not None

    def grab(self, retries=3):
        """取**最新**一帧：先把相机缓冲区里积压的旧帧抽干，再取回最近那帧。

        ★★2026-09-18 抓到的真凶（"瞄准越修越偏、20 拍不收敛、dx 先连续几拍纹丝不动
          然后一路冲过 0 再回头振荡"）：
          原来这里是直接 `self.cap.read()`，而 OpenCV/V4L2 的 read() 返回的是
          **缓冲区里最旧**的那一帧（FIFO）。瞄准每拍才读一次（settle ≈0.8s），
          而相机以 30fps 持续产帧 → 队列里积压二三十帧 →
          **读到的画面比现实晚 2~3 拍（约 1.5~2.5 秒）**。
          于是控制器以为"还没到位"就继续发脉冲，其实机械早就到位、甚至冲过头了
          → 过冲 → 反向 → 再滞后 → 来回振荡，最后 20 拍耗尽。
          修法：读之前用 `cap.grab()`（只抓取、不解码，很快）把队列抽干，
          再用 `cap.retrieve()` 解码取出**最新**那帧。
          `CAM_FLUSH_FRAMES` 控制抽几帧（默认 4；设 0 = 退回旧行为）。
        """
        if self.cap is None:
            return None
        flush = int(_sc("CAM_FLUSH_FRAMES", 4) or 0)
        for _ in range(retries):
            ok, fr = False, None
            try:
                for _ in range(max(0, flush)):
                    if not self.cap.grab():          # 只抓不取：不解码，开销极小
                        break
                ok, fr = self.cap.retrieve()         # 取回最新那帧
            except Exception:                                        # noqa: BLE001
                ok, fr = False, None
            if not ok or fr is None:                 # 抽干失败就退回普通 read
                ok, fr = self.cap.read()
            if ok and fr is not None:
                self.last_frame = fr
                return fr
            time.sleep(0.05)
        return None

    # ---- 方向 ----
    def set_rotate(self, mode):
        """运行中换方向（页面按钮/命令行）。返回生效后的模式。"""
        if cam_rotate is not None:
            self.rotate_mode = cam_rotate.norm_mode(mode, default="none")
        else:
            self.rotate_mode = "none"
        self.cfg["rotate_mode"] = self.rotate_mode
        self.cfg["rotate_ccw"] = (self.rotate_mode == "ccw")
        return self.rotate_mode

    # ---- 预处理（画面方向 + 缩放）：locate 与颜色检测共用，保证坐标系一致 ----
    def prepare(self, frame):
        """按当前画面方向旋转 + 按 proc_w 缩放。

        ★取料（颜色质心）与放置（圆环中心）必须走**同一条预处理**，否则两套坐标对不上、
        收敛时会越修越偏。`locate()` 内部也调用它。"""
        if frame is None:
            return None
        fr = cam_rotate.apply_rotate(frame, self.rotate_mode) if cam_rotate is not None else frame
        w = int(self.cfg.get("proc_w") or 0)
        if w and fr.shape[1] > w:                       # 只缩小不放大
            fr = self.cv2.resize(fr, (w, int(fr.shape[0] * w / fr.shape[1])),
                                 interpolation=self.cv2.INTER_AREA)
        return fr

    # ---- 检测 ----
    def locate(self, frame):
        """返回 dict(found, cx, cy, r, digit, conf, stale, shape)；
        坐标系 = **旋转（+缩放）之后**的图（与 aim 的目标中心 w/2,h/2 同一坐标系）。"""
        if frame is None:
            self.last_loc = {"found": False}
            return self.last_loc
        fr = self.prepare(frame)
        res = self.det.process(fr)
        if not res.get("found"):
            out = {"found": False, "stale": res.get("stale", 0), "shape": fr.shape,
                   "frame": fr}
        else:
            out = {"found": True,
                   "cx": float(res["cx_px"]), "cy": float(res["cy_px"]),
                   "r": float(res["r_outer_px"]), "digit": res.get("digit"),
                   "conf": float(res.get("conf", 0.0)), "stale": int(res.get("stale", 0)),
                   "shape": fr.shape, "frame": fr}
        self.last_loc = out
        if self.hub is not None:                        # 顺手喂给推流（叠加用同一结果，不重复检测）
            self.hub.publish(out.get("frame"), out)
        return out

    def close(self):
        try:
            if self.cap:
                self.cap.release()
        except Exception:                                        # noqa: BLE001
            pass


class BlobTracker:
    """颜色质心滤波：EMA 平滑 + 突跳清窗 + 丢失保持。

    语义与 `RingDetector` 的 `ema / jump_px / stale_max` 对齐 —— 取料（颜色）与放置（圆环）
    走同一套滤波规则，行为一致、参数面板也眼熟。

    为什么颜色也要滤波：blob 质心逐帧会抖（阈值边缘跳动），而瞄准是"像素→脉冲"闭环，
    不滤波就会来回过冲（现场表现：爪子在料上方左右晃、越修越偏）。
    """

    def __init__(self, ema=0.5, jump_px=120, stale_max=10, med_n=1):
        self.ema = float(ema)
        self.jump_px = float(jump_px)
        self.stale_max = int(stale_max)
        self.med_n = max(1, int(med_n))       # 中值滤波窗口（1 = 关）
        self._hist = []                       # 最近若干帧的**原始**中心，供中值滤波
        self.cx = None
        self.cy = None
        self.r = 0.0
        self.stale = 0

    def reset(self):
        self.cx = self.cy = None
        self.r = 0.0
        self.stale = 0
        self._hist = []

    def update(self, raw):
        """raw = {"found", cx, cy, r}；返回同结构（多带 raw/conf/stale/held，供绘制与调试）。

        ★2026-09-18 加**中值滤波**（车主："加上对于物料圆心识别坐标的滤波维稳，如果已经加了，
          那就让它更稳一些"）：原来只有 EMA。EMA 靠压低新值权重来平滑，代价是**相位滞后**
          —— 滞后本身就容易让"像素→脉冲"闭环来回过冲；而中值滤波对**偶发跳变**
          （阈值边缘蹦一帧、色块忽有忽无）几乎完全免疫，且只引入约 1 帧滞后。
          两者叠加才是"稳而不肉"。顺序：先取最近 med_n 帧原始中心的**逐分量中值**，
          再把它与 EMA 平滑值做指数混合。
        """
        if not raw.get("found"):
            self.stale += 1
            if self.cx is not None and self.stale <= self.stale_max:
                # 短暂丢失：保持上次中心（瞄准闭环不至于立刻放弃）
                return {"found": True, "cx": self.cx, "cy": self.cy, "r": self.r,
                        "raw": (self.cx, self.cy), "conf": 0.5, "stale": self.stale,
                        "held": True}
            self.reset()
            return {"found": False, "raw": None, "conf": 0.0, "stale": self.stale}
        rx, ry = float(raw["cx"]), float(raw["cy"])
        rr = float(raw.get("r", 0.0))
        if self.cx is None:
            self.cx, self.cy, self.r = rx, ry, rr
            self._hist = [(rx, ry)]
            self.stale = 0
            return {"found": True, "cx": self.cx, "cy": self.cy, "r": self.r,
                    "raw": (rx, ry), "conf": 1.0, "stale": 0, "held": False}
        if abs(rx - self.cx) > self.jump_px or abs(ry - self.cy) > self.jump_px:
            # 突跳 → 视为换了目标：直接跳过去，中值窗口清空重来
            self.cx, self.cy, self.r = rx, ry, rr
            self._hist = [(rx, ry)]
            self.stale = 0
            return {"found": True, "cx": self.cx, "cy": self.cy, "r": self.r,
                    "raw": (rx, ry), "conf": 1.0, "stale": 0, "held": False}
        # ① 中值滤波：窗口内原始中心的逐分量中值
        self._hist.append((rx, ry))
        if len(self._hist) > self.med_n:
            self._hist.pop(0)
        mx, my = rx, ry
        if self.med_n >= 3 and len(self._hist) >= 3:
            mx = sorted(p[0] for p in self._hist)[len(self._hist) // 2]
            my = sorted(p[1] for p in self._hist)[len(self._hist) // 2]
        # ② EMA 平滑（对中值后的值混合，而不是对原始值）
        a = self.ema
        self.cx = a * mx + (1 - a) * self.cx
        self.cy = a * my + (1 - a) * self.cy
        self.r = a * rr + (1 - a) * self.r
        self.stale = 0
        return {"found": True, "cx": self.cx, "cy": self.cy, "r": self.r,
                "raw": (rx, ry), "conf": 1.0, "stale": 0, "held": False}


class ColorVision:
    """圆形颜色识别：**原料区取料时用它定位目标颜色的圆块中心**（也用于取料前的颜色核对）。"""

    def __init__(self, profile=None, perf="rdk", profile_file="color_profile.json"):
        from color_blob_demo import ColorBlobDetector, PRESETS
        self.PRESETS = PRESETS
        self.ColorBlobDetector = ColorBlobDetector
        # 现场在 color_blob_stream 里调好并保存的三色阈值 → 主程序直接用
        self.profile_path = None
        self.tune = {}                    # ★★调参台的形状/形态学参数（见下）
        if cam_rotate is not None and profile_file:
            p, cfg = cam_rotate.load_calib(profile_file)
            if cfg:
                self.profile_path = p
                # ★★2026-09-18 车主发现："调完提示 saved color_profile.json，但 k_open 之类的
                #   参数一个没保存过" —— 因为调参台的 /save 以前**只写颜色阈值**，
                #   `k_open / k_close / circ_min / min_area_frac / blur_k / use_wb / drop_glare /
                #   boost / suppress / boost_band` 这些**一个都没写进档案** → 一保存就丢，
                #   主程序只能拿代码默认值跑（"面板上调好了、跑起来又不对"）。
                #   现在调参台会把它们存进档案的 `_tune` 段，这里读出来并注入 detector。
                self.tune = dict(cfg.get("_tune") or {})
                for k, v in cfg.items():
                    if isinstance(v, dict) and k in self.PRESETS:
                        PRESETS[k].update(v)
        if profile:
            for k, v in profile.items():
                if k in self.PRESETS:
                    self.PRESETS[k].update(v)
        self.profile = profile
        self.det = {}
        self.trackers = {}
        # 颜色质心滤波参数
        # ★2026-09-18：改成从 school_config 读（以前是硬编码 0.5/120/10，
        #   现场"想让它更稳"只能改代码）。现在是：中值滤波(med_n) → EMA(ema) 两级串联。
        self.ema = float(_sc("COLOR_EMA", 0.5))            # EMA 平滑系数（0.35 = 比原来更平滑）
        self.jump_px = float(_sc("COLOR_JUMP_PX", 120))    # 突跳阈值（px）
        self.stale_max = int(_sc("COLOR_STALE_MAX", 10))   # 连续丢失保持帧数
        self.med_n = int(_sc("COLOR_MED_N", 1))            # ★中值滤波窗口（3 = 开）
        # ★形状门限（school_main.py 参数区可覆盖）
        #   ⚠️ ColorBlobDetector 的 max_area_frac 默认只有 0.10：**目标离得近、占画面 >10% 会被整块丢掉**
        #      —— 2026-09-17 现场"料就在镜头前却找不到"的头号嫌疑（调参台的面积门限不进档案！）；
        #      这里把默认放宽到 0.60，圆度门限也适度放松。
        self.min_area_frac = 0.0008
        self.max_area_frac = 0.60
        self.circ_min = 0.45

    def _det_of(self, color):
        if color not in self.det:
            preset = dict(self.PRESETS.get(color, self.PRESETS["red"]))
            det = self.ColorBlobDetector(preset)
            det.min_area_frac = self.min_area_frac      # 每个颜色一个 detector，门限统一注入
            det.max_area_frac = self.max_area_frac
            det.circ_min = self.circ_min
            # ★★再把**档案里存的形状/形态学参数**覆盖上去（2026-09-18 车主发现"调完没保存"）：
            #   顺序很关键 —— 先铺代码默认值，再用现场调好的值覆盖，现场值优先。
            #   以前这些只存在于调参台进程内存里，主程序永远用默认值。
            for _k, _v in (self.tune or {}).items():
                if _k in ("mode", "color"):
                    continue
                try:
                    if _k in ("use_wb", "drop_glare"):
                        setattr(det, _k, bool(_v))
                    elif _k in ("circ_min", "min_area_frac", "max_area_frac",
                                "boost", "suppress"):
                        setattr(det, _k, float(_v))
                    elif _k in ("k_open", "k_close", "blur_k", "boost_band"):
                        setattr(det, _k, max(1, int(_v)))
                except Exception:                                    # noqa: BLE001
                    pass
            self.det[color] = det
        return self.det[color]

    def find(self, frame, color):
        """单帧原始结果：dict(found, cx, cy, r, circ, fill, score)（frame 需与瞄准同一坐标系）。"""
        if frame is None:
            return {"found": False}
        res = self._det_of(color).detect(frame, color)
        info = res.get("info") or {}
        # ⚠️ `found` 在 detect() 返回的**外层**，不在 info 里（info 只是 best 的 cx/cy/r/area/circ/score）。
        #    2026-09-17 板上自测抓到：以前写 info.get("found") → 颜色检测**永远返回 found=False**。
        #    （这条路径过去只在"取料后的颜色确认"里用到，从没被真正跑到，所以一直没暴露。）
        return {"found": bool(res.get("found")),
                "cx": float(info.get("cx", -1)), "cy": float(info.get("cy", -1)),
                "r": float(info.get("r", 0)),
                "circ": float(info.get("circ", 0.0)),
                "fill": float(info.get("fill", 0.0)),
                "score": float(info.get("score", 0.0))}

    def _tracker_of(self, color):
        if color not in self.trackers:
            self.trackers[color] = BlobTracker(ema=self.ema, jump_px=self.jump_px,
                                               stale_max=self.stale_max,
                                               med_n=self.med_n)
        return self.trackers[color]

    def track(self, frame, color):
        """★取料瞄准的入口：找**目标颜色圆块的质心** + EMA 滤波。

        返回 {found, cx, cy, r, raw, conf, stale, shape, frame, color}：
          · cx/cy = 平滑后的中心（瞄准闭环用这个）
          · raw   = 本帧原始中心（监控台画红圈/看抖动用）
        """
        if frame is None:
            return {"found": False, "frame": None, "shape": None, "color": color}
        raw = self.find(frame, color)
        out = self._tracker_of(color).update(raw)
        out["frame"] = frame
        out["shape"] = getattr(frame, "shape", None)
        # ★2026-09-18：把**形状特征**一并透传出去，用来判断"套住的到底是不是那块圆料"。
        #   背景/料架那种大片红色也能通过面积门限，光看 cx/cy/r 分不出来；
        #   而 circ（4πA/P²）与 fill（A/πr²）对"圆"很敏感，一眼能看出问题。
        #   （现场症状：标定时 r 异常偏大、两个方向测出来同向 —— 就是套错了目标。）
        out["circ"] = float(raw.get("circ", 0.0))
        out["fill"] = float(raw.get("fill", 0.0))
        try:
            _shp = out["shape"] or (0, 0, 0)
            _hw = float(_shp[0]) * float(_shp[1])
            _rf = float(out.get("r", 0.0))
            out["area_frac"] = (3.14159265 * _rf * _rf / _hw) if _hw > 0 else 0.0
        except Exception:                                        # noqa: BLE001
            out["area_frac"] = 0.0
        out["color"] = color
        return out

    def save_debug(self, frame, color, out_dir="aim_fail", tag=""):
        """把"没找到目标"的那一帧存下来（原图 + 该颜色的掩膜）。

        事后一眼能分清两种情况：**料根本不在画面里** vs **阈值/门限把它过滤掉了**。
        返回存下的路径；任何异常都吞掉（诊断功能绝不能影响比赛）。
        """
        if frame is None:
            return None
        try:
            import cv2
            os.makedirs(out_dir, exist_ok=True)
            ts = time.strftime("%H%M%S")
            base = "%s_%s" % (str(tag or color or "x").replace("/", "_"), ts)
            p = os.path.join(out_dir, base + "_raw.jpg")
            cv2.imwrite(p, frame)
            try:
                res = self._det_of(color).detect(frame, color)
                mask = res.get("mask")
                if mask is not None:
                    cv2.imwrite(os.path.join(out_dir, base + "_mask.png"), mask)
            except Exception:                                    # noqa: BLE001
                pass
            return p
        except Exception:                                        # noqa: BLE001
            return None

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
        self.rotate_mode = "none"
        self.last_frame = None
        self.last_loc = {"found": False}
        self.hub = None

    def nudge(self, dx_px, dy_px):
        self.cx += float(dx_px)
        self.cy += float(dy_px)

    def set_rotate(self, mode):
        self.rotate_mode = mode
        return mode

    def grab(self, retries=1):
        return ("mock-frame", self.cx, self.cy)

    def locate(self, frame=None):
        self.calls += 1
        if not self.found:
            self.last_loc = {"found": False, "stale": 1, "shape": (self.h, self.w, 3)}
            return self.last_loc
        if self.lost_frames > 0:                 # 模拟偶发丢目标
            self.lost_frames -= 1
            self.last_loc = {"found": False, "stale": 1, "shape": (self.h, self.w, 3)}
            return self.last_loc
        self.last_loc = {"found": True, "cx": self.cx, "cy": self.cy, "r": self.r,
                         "digit": self.digit, "conf": 1.0, "stale": 0,
                         "shape": (self.h, self.w, 3)}
        if self.hub is not None:
            self.hub.publish(None, self.last_loc)
        return self.last_loc

    def find(self, frame, color):
        return {"found": True, "cx": self.cx, "cy": self.cy, "r": self.r,
                "color": color}

    def close(self):
        pass
