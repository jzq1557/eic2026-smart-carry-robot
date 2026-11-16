"""cyl_stream_ui.py — 黑柱识别 + 测距 demo（**一个脚本搞定所有用法**，不需要任何 .sh）

用法（和 ``color_blob_demo.py`` 同一个风格：脚本 + 开关）::

    # ① 相机实时 + 浏览器 UI（主用法）
    python3 cyl_stream_ui.py --cam 0
    python3 cyl_stream_ui.py --cam 0 --capture gst --port 8094 --quality 80

    # ①b 相机实时 + **HDMI 屏本地窗口**（不编码 JPEG、不走 HTTP，最跟手）
    #     ssh 里跑要先 export DISPLAY=:0 XAUTHORITY=/var/run/lightdm/root/:0
    python3 cyl_stream_ui.py --cam 0 --local --capture gst
    python3 cyl_stream_ui.py --cam 0 --local --capture gst --width 800 --height 600
    #     按键：q/Esc 退出　空格 冻结　s 截图　f 全屏切换　+/- 阈值　[/] 间隔　b/l/t 显隐

    # ①c 取流后端 `--capture`（决定"卡不卡"的那个开关，实测见 continue.md §11）
    #     gst  = GStreamer jpegdec：解码在独立线程，主循环只花 ~2ms → 浏览器模式 24→30 FPS
    #     v4l2 = OpenCV 的 V4L2 后端（默认）：解码串在主循环里，慢但兼容性最好

    # ② 后台跑 / 看状态 / 看日志 / 停止（后台管理也在这个脚本里，不用 .sh）
    python3 cyl_stream_ui.py --cam 0 --daemon
    python3 cyl_stream_ui.py --status
    python3 cyl_stream_ui.py --log 30
    python3 cyl_stream_ui.py --stop

    # ③ 单图 / 一批图：命令行输出检测结果
    python3 cyl_stream_ui.py --image a.jpg b.jpg --save-dir /tmp/cyl_out

    # ④ 用图片目录当"假相机"循环推流（没相机也能看界面）
    python3 cyl_stream_ui.py --frames-dir /tmp/cyl_test

    # ⑤ 合成场景自测（现画深色圆柱，跑完整管线）
    python3 cyl_stream_ui.py --synthetic 5 --no-show

    # ⑥ 全链路自测（无相机、无 UI）：模型加载 + 前后处理 + 输出解码
    python3 cyl_stream_ui.py --selftest

    # ⑦ 纯性能：跑 N 次推理打印耗时与帧率
    python3 cyl_stream_ui.py --bench 200

    # ⑧ 无人值守验证：跑 10 秒后自动退出，并存一张带框图
    python3 cyl_stream_ui.py --cam 0 --seconds 10 --snapshot /tmp/cyl_ui.jpg

界面内容（需求方 2026-09-22 圈定，全部实现）：目标框+置信度+类别名；**估算距离**
（``Z = f_px × 100 / h_px``，f_px 可现场标定）+ 横向位置；**取流/预处理/BPU/后处理/编码/端到端
分段计时**；置信度阈值与检测间隔现场可调；**官方 yolo11n 对照切换**；最近 20 条检测记录 +
CSV 导出；相机曝光/增益与一键锁曝光；车道辅助线；一键截屏；演示/调试双模式。

⚠️ 画面上的文字一律用 **ASCII**：OpenCV 的 ``putText`` 画不了中文（会变成 ``?``），中文只放浏览器面板。
"""

from __future__ import annotations

import argparse
import csv
import glob
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np

# ---------------------------------------------------------------- 常量
DEMO_DIR = "/app/pydev_demo/02_detection_sample/02_ultralytics_yolo11"
ROOT = Path("/root/VisualBot2")
SELF_DIR = ROOT if (ROOT / "models").is_dir() else Path(__file__).resolve().parent
SELF_MODEL = str(SELF_DIR / "models" / "black_cylinder_bayese_640x640_nv12.bin")
SELF_LABELS = str(SELF_DIR / "models" / "black_cylinder.names")
STOCK_MODEL = DEMO_DIR + "/yolo11n_detect_bayese_640x640_nv12.bin"
STOCK_LABELS = DEMO_DIR + "/coco_classes.names"
SHOT_DIR = SELF_DIR / "captures"
PID_FILE = Path("/tmp/cyl_stream_ui.pid")
LOG_FILE = Path("/tmp/cyl_stream_ui.log")

#: 单目测距：Z(mm) = f_px × 圆柱真实高度(mm) / 像素高度。f_px 现场可按实测改。
CYL_HEIGHT_MM = 100.0
DEFAULT_F_PX = 914.0
#: 推流目标宽度（960 是浏览器 MJPEG 的性价比点；本地窗口模式会改成屏幕宽度）
DEFAULT_STREAM_WIDTH = 960
#: 本地窗口（--local）的窗口标题与状态条高度
WINDOW_NAME = "cyl_local"
BAR_HEIGHT = 26
#: 置信度下限：给检测器一个很低的门槛，实际阈值在界面上过滤（这样滑块才能实时生效）
BASE_SCORE_THRES = 0.05
RECORD_LIMIT = 20


def fourcc_value(text: str) -> int:
    """'MJPG' → OpenCV 的 FOURCC 整数（OpenCV 5 已移除 VideoWriter_fourcc）。"""
    value = 0
    for index, char in enumerate(text[:4]):
        value |= ord(char) << (8 * index)
    return value


def rate_of(stamps: deque[float]) -> float:
    """按时间戳算**真实**频率（次/秒）。

    ⚠️ 不能用 ``1000/平均耗时`` 代替：那算的是"理论上限"，取流耗时 16.7ms 会显示成
    60 FPS，而相机实际只出 30 FPS，很容易把人骗了。
    """
    if len(stamps) < 2:
        return 0.0
    span = stamps[-1] - stamps[0]
    return round((len(stamps) - 1) / span, 1) if span > 0 else 0.0


def load_labels(path: str) -> list[str]:
    """读类别名文件（每行一个）。"""
    try:
        with open(path, encoding="utf-8") as handle:
            names = [line.strip() for line in handle if line.strip()]
        return names or ["black_cylinder"]
    except OSError:
        return ["black_cylinder"]


def run_ctl(args: list[str]) -> str:
    """跑一条 shell 命令并返回输出（失败也返回文本，不抛异常）。"""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=6)
        return (done.stdout or done.stderr).strip()
    except Exception as error:
        return "error: %s" % error


def synthetic_frame(width: int = 1280, height: int = 720, seed: int = 0) -> np.ndarray:
    """画一帧合成场景：浅色地面 + 一个深色圆柱（位置随 seed 变）。"""
    rng = np.random.default_rng(seed)
    frame: np.ndarray = np.full((height, width, 3), 200, dtype=np.uint8)
    frame = np.clip(
        frame.astype(np.float32) + rng.normal(0, 3.0, frame.shape), 0, 255
    ).astype(np.uint8)
    box_h = int(height * (0.18 + 0.06 * (seed % 4)))
    box_w = max(int(box_h / 2), 8)
    cx = int(width * (0.3 + 0.12 * (seed % 5)))
    cy = int(height * 0.85)
    cv2.rectangle(frame, (cx - box_w // 2, cy - box_h), (cx + box_w // 2, cy), (38, 38, 40), -1)
    cv2.ellipse(frame, (cx, cy - box_h), (box_w // 2, max(box_w // 6, 2)), 0, 0, 360, (60, 60, 62), -1)
    return frame


def filter_detections(
    boxes: Any, class_ids: Any, scores: Any, labels: list[str], threshold: float, only_cylinder: bool
) -> list[dict[str, Any]]:
    """按阈值筛框（自训模型只保留 black_cylinder 这一类）。"""
    result: list[dict[str, Any]] = []
    for box, class_id, score in zip(boxes, class_ids, scores, strict=False):
        if float(score) < threshold:
            continue
        if only_cylinder and labels[int(class_id)] != "black_cylinder":
            continue
        result.append(
            {"box": [float(v) for v in box], "class_id": int(class_id), "score": float(score)}
        )
    return result


def describe_detections(
    detections: list[dict[str, Any]], labels: list[str], width: int, f_px: float
) -> list[str]:
    """把检测结果变成可读文本（CLI 用）。"""
    lines = []
    for item in detections:
        x1, y1, x2, y2 = item["box"]
        box_h = max(y2 - y1, 1.0)
        name = labels[int(item["class_id"])] if int(item["class_id"]) < len(labels) else "obj"
        lateral = "L" if (x1 + x2) / 2 < width / 3 else ("R" if (x1 + x2) / 2 > width * 2 / 3 else "C")
        lines.append(
            "  %-16s conf=%.3f  box=(%.0f,%.0f)-(%.0f,%.0f)  %.0fx%.0fpx  ratio=%.2f  ~%.0fcm %s"
            % (
                name, item["score"], x1, y1, x2, y2, x2 - x1, y2 - y1,
                box_h / max(x2 - x1, 1.0), f_px * CYL_HEIGHT_MM / box_h / 10.0, lateral,
            )
        )
    return lines


class Detector:
    """BPU 检测器包装：可切换模型（自训黑柱 / 官方 yolo11n）。"""

    def __init__(self) -> None:
        self._module: Any = None
        self._cache: dict[str, Any] = {}
        self.current = "self"
        self.error = ""
        self.input_size = (640, 640)

    def _load_demo_module(self) -> Any:
        """按路径加载官方 demo 模块（它的 main() 有 __main__ 保护，import 不会跑推理）。"""
        if self._module is not None:
            return self._module
        sys.path.insert(0, "/app/pydev_demo")
        sys.path.insert(0, DEMO_DIR)
        spec = importlib.util.spec_from_file_location(
            "official_yolo11", DEMO_DIR + "/ultralytics_yolo11.py"
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("加载官方 demo 模块失败：%s" % DEMO_DIR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._module = module
        return module

    def use(self, name: str) -> None:
        """切换模型（'self' 或 'stock'）。"""
        if name not in ("self", "stock"):
            return
        if name not in self._cache:
            path = SELF_MODEL if name == "self" else STOCK_MODEL
            self._cache[name] = self._load_demo_module().YoloV11(
                SimpleNamespace(model_path=path, score_thres=BASE_SCORE_THRES)
            )
        self.current = name

    def labels(self) -> list[str]:
        """当前模型的类别名。"""
        return load_labels(SELF_LABELS if self.current == "self" else STOCK_LABELS)

    def infer(self, frame: np.ndarray) -> tuple[list[Any], list[Any], list[Any], dict[str, float]]:
        """推理一帧，返回 (boxes, class_ids, scores, 各阶段耗时 ms)。"""
        detector = self._cache[self.current]
        height, width = frame.shape[:2]
        timing: dict[str, float] = {}
        start = time.perf_counter()
        tensor = detector.pre_process(frame)
        after_pre = time.perf_counter()
        outputs = detector.forward(tensor)
        after_bpu = time.perf_counter()
        boxes, class_ids, scores = detector.post_process(outputs, width, height)
        after_post = time.perf_counter()
        timing["pre"] = (after_pre - start) * 1000.0
        timing["bpu"] = (after_bpu - after_pre) * 1000.0
        timing["post"] = (after_post - after_bpu) * 1000.0
        return boxes, class_ids, scores, timing


def annotate(
    frame: np.ndarray,
    detections: list[dict[str, Any]],
    labels: list[str],
    show_boxes: bool = True,
    show_lines: bool = True,
    show_text: bool = True,
    f_px: float = DEFAULT_F_PX,
) -> np.ndarray:
    """把检测结果画到帧上（只用 ASCII 文字）。

    ``detections`` 每项：``{box:(x1,y1,x2,y2), class_id, score}``；距离由框高现算。
    """
    height, width = frame.shape[:2]
    if show_lines:
        # 中心十字 + 左右 1/3 分界线（对齐比赛场地，方便看目标在哪个车道）
        cv2.line(frame, (width // 2, 0), (width // 2, height), (120, 120, 120), 1)
        cv2.line(frame, (0, height // 2), (width, height // 2), (120, 120, 120), 1)
        for ratio in (1 / 3, 2 / 3):
            cv2.line(frame, (int(width * ratio), 0), (int(width * ratio), height), (80, 80, 80), 1)
    for item in detections:
        if not show_boxes:
            break
        x1, y1, x2, y2 = [int(v) for v in item["box"]]
        box_h = max(y2 - y1, 1)
        color = (0, 255, 0)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        if not show_text:
            continue
        name = labels[int(item["class_id"])] if int(item["class_id"]) < len(labels) else "obj"
        distance_cm = f_px * CYL_HEIGHT_MM / box_h / 10.0
        cx = (x1 + x2) / 2.0
        lateral = "L" if cx < width / 3 else ("R" if cx > width * 2 / 3 else "C")
        cv2.putText(
            frame, "%s %.2f" % (name, float(item["score"])), (x1, max(y1 - 22, 14)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 2,
        )
        cv2.putText(
            frame, "~%.0fcm %s h=%dpx" % (distance_cm, lateral, box_h), (x1, max(y1 - 5, 30)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2,
        )
    return frame


class GstSource:
    """GStreamer 取流：``jpegdec`` 在**流水线线程里**解码，主循环每帧只花 ~2ms。

    板子上 OpenCV 没编 GStreamer 后端（``GStreamer: NO``），但系统自带 ``python3-gi``
    和 ``libgstjpeg``（libjpeg-turbo + NEON），所以直接用 gi 绑定。

    实测（1280×720 MJPG @30，RDK X5）：OpenCV V4L2 后端 ``read()`` 平均 33.3 ms
    （等相机 + 软解串在一起），这里只要 **1.75 ms** —— 解码不占主循环。
    """

    def __init__(
        self, device: str, width: int, height: int, fps: int, buffersize: int = 2
    ) -> None:
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.shape = (height, width, 3)
        self.opened = False
        self.error = ""
        self.gst: Any = None
        self.pipeline: Any = None
        self.sink: Any = None
        try:
            gi = importlib.import_module("gi")
            gi.require_version("Gst", "1.0")
            gst = importlib.import_module("gi.repository.Gst")
            gst.init(None)
        except Exception as error:  # 缺依赖就退回 v4l2，不该炸
            self.error = "GStreamer 绑定不可用（%s）" % error
            return
        self.gst = gst
        desc = (
            "v4l2src device=%s io-mode=2 ! image/jpeg,width=%d,height=%d,framerate=%d/1 "
            "! jpegdec ! videoconvert ! video/x-raw,format=BGR "
            "! appsink name=sink max-buffers=%d drop=true sync=false"
        ) % (device, width, height, fps, buffersize)
        try:
            self.pipeline = gst.parse_launch(desc)
        except Exception as error:  # 同上：起不来就退回 v4l2
            self.error = "GStreamer 管线建不起来（%s）" % error
            return
        self.sink = self.pipeline.get_by_name("sink")
        self.pipeline.set_state(gst.State.PLAYING)
        # 等第一帧，确认 caps 协商成功（相机不支持请求的分辨率会在这里暴露）
        first = self.read()
        if first is None:
            self.error = "GStreamer 起流后读不到帧（相机 %s 支持这个分辨率吗？）" % device
            return
        self.opened = True

    def read(self) -> np.ndarray | None:
        """取最新一帧 BGR（阻塞到有帧；解码已经在别的线程做完了）。"""
        if self.pipeline is None or self.sink is None:
            return None
        sample = self.sink.emit("pull-sample")
        if sample is None:
            return None
        structure = sample.get_caps().get_structure(0)
        width = int(structure.get_value("width"))
        height = int(structure.get_value("height"))
        self.shape = (height, width, 3)
        buffer = sample.get_buffer()
        ok, info = buffer.map(self.gst.MapFlags.READ)
        if not ok:
            return None
        try:
            frame = np.frombuffer(info.data, np.uint8).reshape(self.shape)
            return frame.copy()  # unmap 之后这块内存就还给流水线了
        finally:
            buffer.unmap(info)

    def release(self) -> None:
        """收工：把流水线停掉。"""
        if self.pipeline is not None and self.gst is not None:
            self.pipeline.set_state(self.gst.State.NULL)

    def info(self) -> dict[str, Any]:
        """跟 V4L2 那条路对齐的"相机实际参数"。"""
        return {
            "device": self.device,
            "width": self.shape[1],
            "height": self.shape[0],
            "fps_set": float(self.fps),
            "buffersize": 0,
            "exposure": 0,
            "gain": 0,
            "backend": "gstreamer(jpegdec)",
        }


class Pipeline:
    """相机取流 + 门控推理 + 叠加 + JPEG 编码；供 MJPEG 与 API 读取最新结果。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.detector = Detector()
        self.lock = threading.Lock()
        self.latest_jpeg: bytes | None = None
        self.latest_frame: np.ndarray | None = None
        # 本地窗口模式（--local）：不编码 JPEG，直接把缩放后的裸帧交给主线程 imshow
        self.local = bool(getattr(args, "local", False))
        self.latest_stream: np.ndarray | None = None
        self.running = True
        self.error = ""
        self.frame_index = 0
        self.detections: list[dict[str, Any]] = []
        self.records: deque[dict[str, Any]] = deque(maxlen=RECORD_LIMIT)
        # 运行时配置（界面可改）
        self.threshold = args.threshold
        self.interval = max(1, args.interval)
        self.quality = args.quality
        self.stream_width = args.stream_width
        self.show_boxes = True
        self.show_lines = True
        self.show_text = True
        self.f_px = args.f_px  # 命令行给的标定值（界面里还能再改）
        # 统计（最近 60 帧的滑动平均）
        self.capture_ms: deque[float] = deque(maxlen=60)
        self.infer_ms: deque[float] = deque(maxlen=60)
        self.total_ms: deque[float] = deque(maxlen=60)
        # 真实频率靠时间戳算（见 rate_of）
        self.capture_ts: deque[float] = deque(maxlen=60)
        self.infer_ts: deque[float] = deque(maxlen=60)
        self.total_ts: deque[float] = deque(maxlen=60)
        self.breakdown: dict[str, float] = {"pre": 0.0, "bpu": 0.0, "post": 0.0, "resize": 0.0, "encode": 0.0}
        self.capture_fps = 0.0
        self.infer_fps = 0.0
        self.e2e_fps = 0.0
        self.camera_info: dict[str, Any] = {}
        self.started = time.time()
        # 帧来源：相机 / 图片目录（循环）/ 合成场景
        self.source_kind = "camera"
        self._files: list[str] = []
        self._file_index = 0
        if args.frames_dir:
            self.source_kind = "dir"
            for pattern in ("*.jpg", "*.jpeg", "*.png"):
                self._files += sorted(glob.glob(str(Path(args.frames_dir) / pattern)))
            if not self._files:
                self.error = "目录里没有图片：%s" % args.frames_dir
        elif args.synthetic > 0:
            self.source_kind = "synthetic"
        self.detector.use("self")
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    # ------------------------------------------------------------ 帧来源
    def _read_frame(self, cap: Any) -> np.ndarray | None:
        """按来源取一帧（相机 / 图片目录循环 / 合成）。"""
        if self.source_kind == "dir":
            path = self._files[self._file_index % len(self._files)]
            self._file_index += 1
            time.sleep(max(0.0, 1.0 / max(self.args.fps, 1)))
            return cv2.imread(path)
        if self.source_kind == "synthetic":
            frame = synthetic_frame(self.args.width, self.args.height, self.frame_index)
            time.sleep(max(0.0, 1.0 / max(self.args.fps, 1)))
            return frame
        if cap is None:
            return None
        if isinstance(cap, GstSource):
            return cap.read()
        ok, frame = cap.read()
        return frame if ok else None

    def _source_info(self, cap: Any) -> dict[str, Any]:
        """给界面显示的"来源信息"。"""
        if isinstance(cap, GstSource):
            return cap.info()
        if cap is not None:
            return self._camera_info(cap)
        if self.source_kind == "dir":
            sample = cv2.imread(self._files[0]) if self._files else None
            return {
                "device": "图片目录 %s" % self.args.frames_dir,
                "width": sample.shape[1] if sample is not None else self.args.width,
                "height": sample.shape[0] if sample is not None else self.args.height,
                "fps_set": self.args.fps, "buffersize": 0, "exposure": 0, "gain": 0,
            }
        return {
            "device": "合成场景", "width": self.args.width, "height": self.args.height,
            "fps_set": self.args.fps, "buffersize": 0, "exposure": 0, "gain": 0,
        }

    # ------------------------------------------------------------ 相机
    def _open_camera(self) -> Any:
        """打开 UVC 相机（``--capture gst`` 走 GStreamer，默认走 OpenCV 的 V4L2 后端）。

        ``buffersize`` 关键：设 1 时驱动无法流水线、取流被砍到 ~15 FPS（老工程就是 1），
        设 4（或不设）同款相机 1280×720 MJPG 能跑满 30 FPS。
        """
        if getattr(self.args, "capture", "v4l2") == "gst":
            source = GstSource(
                self.args.device, self.args.width, self.args.height, self.args.fps
            )
            if source.opened:
                return source
            # 起不来就退回 OpenCV 的 V4L2 后端，别让整个 demo 挂掉
            print("[警告] GStreamer 取流失败（%s），改用 v4l2 后端" % source.error, flush=True)
        cap = cv2.VideoCapture(self.args.device, cv2.CAP_V4L2)
        if not cap.isOpened():
            self.error = "打不开相机 %s（插好了吗？被别的程序占用了？）" % self.args.device
            return None
        cap.set(cv2.CAP_PROP_FOURCC, fourcc_value(self.args.fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.args.width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.args.height))
        cap.set(cv2.CAP_PROP_FPS, float(self.args.fps))
        if self.args.buffersize > 0:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, float(self.args.buffersize))
        return cap

    def _camera_info(self, cap: cv2.VideoCapture) -> dict[str, Any]:
        """相机实际生效的参数（分辨率/格式/曝光等）。"""
        return {
            "device": self.args.device,
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps_set": round(float(cap.get(cv2.CAP_PROP_FPS)), 1),
            "buffersize": round(float(cap.get(cv2.CAP_PROP_BUFFERSIZE)), 1),
            "exposure": round(float(cap.get(cv2.CAP_PROP_EXPOSURE)), 1),
            "gain": round(float(cap.get(cv2.CAP_PROP_GAIN)), 1),
        }

    # ------------------------------------------------------------ 主循环
    def _loop(self) -> None:
        """取流 →（门控）推理 → 叠加 → 编码；任何异常都不让线程死掉。"""
        cap = self._open_camera() if self.source_kind == "camera" else None
        if self.source_kind == "camera" and cap is None:
            return
        self.camera_info = self._source_info(cap)
        self.detector.use(self.detector.current)  # 预热（首次加载模型约 0.2s）
        tick = 0
        while self.running:
            try:
                start = time.perf_counter()
                frame = self._read_frame(cap)
                if frame is None:
                    time.sleep(0.02)
                    continue
                after_capture = time.perf_counter()

                if tick % self.interval == 0:
                    boxes, class_ids, scores, timing = self.detector.infer(frame)
                    self.breakdown.update(timing)
                    labels = self.detector.labels()
                    detections = filter_detections(
                        boxes, class_ids, scores, labels, self.threshold,
                        self.detector.current == "self",
                    )
                    with self.lock:
                        self.detections = detections
                    self._record(detections)
                    # ⚠️ timing 里的值已经是**毫秒**（pre/bpu/post 都是 ms），别再乘 1000
                    self.infer_ms.append(sum(timing.values()))
                    self.infer_ts.append(time.perf_counter())
                    self.infer_fps = rate_of(self.infer_ts)
                tick += 1

                with self.lock:
                    detections = list(self.detections)
                # ⚠️ 原地画，不 copy：annotate 改的就是 cap.read() 刚返回的新帧，
                #    多一次 2.7MB 拷贝在这儿要花 2~3ms（实测叠加+编码才是最贵的一段）
                canvas = annotate(
                    frame,
                    detections,
                    self.detector.labels(),
                    self.show_boxes,
                    self.show_lines,
                    self.show_text,
                    self.f_px,
                )
                encode_start = time.perf_counter()
                # 推流用**缩放后**的画面编码：720p JPEG 在 X5 单核上要 ~20ms（实测），
                # 这是端到端帧率的真正瓶颈；截图仍保存全分辨率原帧。
                stream = canvas
                if self.stream_width and canvas.shape[1] > self.stream_width:
                    scale = self.stream_width / float(canvas.shape[1])
                    stream = cv2.resize(
                        canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR
                    )
                resize_done = time.perf_counter()
                self.breakdown["resize"] = (resize_done - encode_start) * 1000.0
                if self.local:
                    # 本地窗口：JPEG 编码（10ms+）完全是浪费，直接交裸帧
                    with self.lock:
                        self.latest_stream = stream
                        self.latest_frame = canvas
                    self.breakdown["encode"] = 0.0
                else:
                    ok_jpg, buffer = cv2.imencode(
                        ".jpg", stream, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
                    )
                    encode_done = time.perf_counter()
                    self.breakdown["encode"] = (encode_done - resize_done) * 1000.0
                    if ok_jpg:
                        with self.lock:
                            self.latest_jpeg = buffer.tobytes()
                            self.latest_frame = canvas
                end = time.perf_counter()
                self.capture_ms.append((after_capture - start) * 1000.0)
                self.total_ms.append((end - start) * 1000.0)
                self.capture_ts.append(after_capture)
                self.total_ts.append(end)
                self.frame_index += 1
                self.capture_fps = rate_of(self.capture_ts)
                self.e2e_fps = rate_of(self.total_ts)
            except Exception as error:  # 单帧出问题不该终止整个 demo
                self.error = "取流/推理异常：%s" % error
                time.sleep(0.05)
        if cap is not None:
            cap.release()

    def _record(self, detections: list[dict[str, Any]]) -> None:
        """最近检测记录（每帧最多记一条置信度最高的）。"""
        if not detections:
            return
        best = max(detections, key=lambda item: item["score"])
        x1, y1, x2, y2 = best["box"]
        box_h = max(y2 - y1, 1.0)
        cx = (x1 + x2) / 2.0
        width = float(self.camera_info.get("width") or self.args.width)
        lateral = "左" if cx < width / 3 else ("右" if cx > width * 2 / 3 else "中")
        with self.lock:
            self.records.appendleft(
                {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "score": round(float(best["score"]), 3),
                    "h_px": int(box_h),
                    "distance_cm": round(self.f_px * CYL_HEIGHT_MM / box_h / 10.0, 1),
                    "lateral": lateral,
                }
            )

    # ------------------------------------------------------------ 统计
    def mean(self, values: deque[float]) -> float:
        """滑动平均。"""
        return round(float(np.mean(values)), 2) if values else 0.0

    def state(self) -> dict[str, Any]:
        """给界面轮询的状态快照。"""
        with self.lock:
            jpeg_ready = self.latest_jpeg is not None
            detections = [
                {
                    "score": round(item["score"], 3),
                    "h_px": int(item["box"][3] - item["box"][1]),
                    "distance_cm": round(
                        self.f_px * CYL_HEIGHT_MM / max(item["box"][3] - item["box"][1], 1.0) / 10.0, 1
                    ),
                }
                for item in self.detections
            ]
            records = list(self.records)
        return {
            "ok": not self.error,
            "error": self.error,
            "jpeg_ready": jpeg_ready,
            "source": self.source_kind,
            "model": self.detector.current,
            "model_label": "自训黑柱模型" if self.detector.current == "self" else "官方 yolo11n（80类）",
            "capture_fps": round(self.capture_fps, 1),
            "infer_fps": round(self.infer_fps, 1),
            "e2e_fps": round(self.e2e_fps, 1),
            "capture_ms": self.mean(self.capture_ms),
            "infer_ms": self.mean(self.infer_ms),
            "total_ms": self.mean(self.total_ms),
            "breakdown": {key: round(value, 2) for key, value in self.breakdown.items()},
            "threshold": self.threshold,
            "interval": self.interval,
            "f_px": self.f_px,
            "show_boxes": self.show_boxes,
            "show_lines": self.show_lines,
            "show_text": self.show_text,
            "camera": self.camera_info,
            "detections": detections,
            "records": records,
            "frames": self.frame_index,
            "uptime_s": int(time.time() - self.started),
            "shot_dir": str(SHOT_DIR),
        }

    # ------------------------------------------------------------ 动作
    def save_frame(self, path: str) -> str:
        """把当前带叠加的画面存到指定路径（--snapshot 与截屏都用它）。"""
        with self.lock:
            frame = None if self.latest_frame is None else self.latest_frame
        if frame is None:
            return ""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(target), frame)
        return str(target)

    def screenshot(self) -> str:
        """一键截屏（存到 captures/，文件名带时间戳）。"""
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        return self.save_frame(
            str(SHOT_DIR / ("cyl_%s.jpg" % datetime.now().strftime("%Y%m%d_%H%M%S")))
        )

    def export_csv(self) -> str:
        """把最近记录导出成 CSV，返回路径。"""
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOT_DIR / ("cyl_records_%s.csv" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        with self.lock:
            records = list(self.records)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["time", "score", "h_px", "distance_cm", "lateral"])
            writer.writeheader()
            writer.writerows(records)
        return str(path)

    def lock_exposure(self, exposure: int, gain: int) -> str:
        """一键锁曝光（走 v4l2-ctl，与既有工程一致）。"""
        output = []
        for args in (
            ["v4l2-ctl", "-d", self.args.device, "-c", "auto_exposure=1"],
            ["v4l2-ctl", "-d", self.args.device, "-c", "exposure_dynamic_framerate=0"],
            ["v4l2-ctl", "-d", self.args.device, "-c", "exposure_time_absolute=%d" % exposure],
            ["v4l2-ctl", "-d", self.args.device, "-c", "white_balance_automatic=0"],
            ["v4l2-ctl", "-d", self.args.device, "-c", "gain=%d" % gain],
        ):
            output.append(run_ctl(args))
        return "已锁定：曝光 %d / 增益 %d" % (exposure, gain)


PIPELINE: Pipeline | None = None


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>黑柱识别 · 板上实时 demo</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--fg:#c9d1d9;--muted:#8b949e;--ok:#7ee787;--warn:#ff7b72;--acc:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.5 -apple-system,"Microsoft YaHei",sans-serif}
header{display:flex;gap:14px;align-items:center;padding:8px 14px;background:var(--panel);border-bottom:1px solid var(--line)}
header h1{font-size:15px;margin:0}
header .kpi{color:var(--muted)}
header .kpi b{color:var(--fg);font-weight:600}
header .grow{flex:1}
main{display:flex;gap:10px;padding:10px;align-items:flex-start}
#video{flex:1;background:#000;border:1px solid var(--line);border-radius:6px;min-height:420px;display:flex;align-items:center;justify-content:center}
#video img{width:100%;height:auto;display:block;border-radius:6px}
aside{width:390px;flex:none}
.card{background:var(--panel);border:1px solid var(--line);border-radius:6px;margin-bottom:8px}
.card h2{font-size:13px;margin:0;padding:7px 10px;border-bottom:1px solid var(--line);color:var(--muted);font-weight:600}
.card .body{padding:8px 10px}
.row{display:flex;align-items:center;gap:8px;margin:4px 0}
.row label{flex:none;width:96px;color:var(--muted)}
.row input[type=range]{flex:1}
.row .val{width:52px;text-align:right;font-variant-numeric:tabular-nums}
button{background:#21262d;color:var(--fg);border:1px solid var(--line);border-radius:5px;padding:5px 9px;cursor:pointer;font-size:12px}
button:hover{background:#30363d}
button.on{background:#238636;border-color:#2ea043;color:#fff}
button.acc{background:#1f6feb;border-color:#388bfd;color:#fff}
table{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}
th,td{padding:3px 4px;border-bottom:1px solid #21262d;text-align:right}
th:first-child,td:first-child{text-align:left}
.muted{color:var(--muted)}
.bar{height:8px;background:#21262d;border-radius:4px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--acc)}
body.demo .debug{display:none}
body.demo aside{width:300px}
body:not(.demo) .onlydemo{display:none}
#err{display:none;background:#3d1418;border-color:#f85149;color:#ffdcd7}
</style></head><body class="demo">
<header>
  <h1>黑柱识别 · 实时 demo</h1>
  <span class="kpi">模型 <b id="model">-</b></span>
  <span class="kpi">取流 <b id="cfps">-</b> fps</span>
  <span class="kpi">推理 <b id="ifps">-</b> fps</span>
  <span class="kpi">端到端 <b id="tms">-</b> ms</span>
  <span class="kpi">目标 <b id="ndet">0</b></span>
  <span class="grow"></span>
  <button id="mode" class="acc" onclick="toggleMode()">切换到调试模式</button>
</header>
<main>
  <div id="video"><img id="stream" src="/stream.mjpg" alt="stream"></div>
  <aside>
    <div class="card" id="err"><div class="body" id="errtext"></div></div>

    <div class="card"><h2>① 检测控制</h2><div class="body">
      <div class="row"><label>置信度阈值</label><input id="th" type="range" min="5" max="95" value="25" oninput="setv('threshold',this.value/100)"><span class="val" id="thv">0.25</span></div>
      <div class="row"><label>检测间隔</label>
        <button onclick="setv('interval',1)">每帧</button>
        <button onclick="setv('interval',2)">1/2</button>
        <button onclick="setv('interval',3)">1/3</button>
        <button onclick="setv('interval',5)">1/5</button>
        <span class="val" id="iv">1</span>
      </div>
      <div class="row"><label>模型</label>
        <button id="m_self" class="on" onclick="setv('model','self')">自训黑柱</button>
        <button id="m_stock" onclick="setv('model','stock')">官方 yolo11n</button>
      </div>
      <div class="row"><label>叠加层</label>
        <button id="b_boxes" class="on" onclick="tog('show_boxes',this)">框</button>
        <button id="b_lines" class="on" onclick="tog('show_lines',this)">辅助线</button>
        <button id="b_text" class="on" onclick="tog('show_text',this)">文字</button>
      </div>
    </div></div>

    <div class="card"><h2>② 测距</h2><div class="body">
      <div class="row"><label>焦距 f_px</label><input id="fp" type="number" value="914" step="1" style="width:90px" onchange="setv('f_px',parseFloat(this.value))">
        <span class="muted">Z = f_px × 100mm ÷ 框高px</span></div>
      <div class="row"><label>当前目标</label><span id="dist" class="val">-</span></div>
    </div></div>

    <div class="card"><h2>③ 计时</h2><div class="body debug">
      <div class="row"><label>取流</label><span id="cms">-</span> ms
        <label style="width:60px">预处理</label><span id="pre">-</span> ms</div>
      <div class="row"><label>BPU 推理</label><span id="bpu">-</span> ms
        <label style="width:60px">后处理</label><span id="post">-</span> ms</div>
      <div class="row"><label>叠加+编码</label><span id="enc">-</span> ms
        <label style="width:60px">端到端</label><span id="tot">-</span> ms</div>
      <div class="bar"><i id="barbpu" style="width:0%"></i></div>
      <div class="muted">蓝条 = BPU 占端到端的比例（实测瓶颈常在前后处理）</div>
    </div>
    <div class="body onlydemo muted" id="timing_demo">-</div></div>

    <div class="card"><h2>④ 检测记录（最近 20 条）</h2><div class="body">
      <table><thead><tr><th>时间</th><th>置信度</th><th>框高px</th><th>距离cm</th><th>位置</th></tr></thead>
      <tbody id="recs"><tr><td colspan="5" class="muted">还没有检测</td></tr></tbody></table>
      <div class="row" style="margin-top:6px">
        <button onclick="act('csv')">导出 CSV</button>
        <button onclick="act('reset')">清空</button>
        <span id="csvpath" class="muted"></span>
      </div>
    </div></div>

    <div class="card"><h2>⑤ 相机</h2><div class="body">
      <div class="row"><label>设备</label><span id="cam">-</span></div>
      <div class="row"><label>锁曝光</label>
        <input id="exp" type="number" value="100" step="10" style="width:80px">
        <input id="gain" type="number" value="0" step="1" style="width:60px">
        <button onclick="lockExp()">一键锁曝光</button>
      </div>
      <div class="row"><label>截屏</label><button onclick="act('screenshot')">保存当前画面</button><span id="shotpath" class="muted"></span></div>
    </div></div>
  </aside>
</main>
<script>
function toggleMode(){
  const d=document.body.classList.toggle('demo');
  document.getElementById('mode').textContent = d ? '切换到调试模式' : '切换到演示模式';
}
async function setv(k,v){
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[k]:v})});
}
function tog(k,btn){ btn.classList.toggle('on'); setv(k, btn.classList.contains('on')); }
async function act(what){
  const r = await fetch('/api/'+what,{method:'POST'}); const j = await r.json();
  if(what==='csv'){ document.getElementById('csvpath').textContent = j.path ? ('已导出：'+j.path) : '导出失败'; }
  if(what==='screenshot'){ document.getElementById('shotpath').textContent = j.path ? ('已保存：'+j.path) : '保存失败'; }
}
async function lockExp(){
  const e=parseInt(document.getElementById('exp').value||'100');
  const g=parseInt(document.getElementById('gain').value||'0');
  const r=await fetch('/api/lock_exposure',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({exposure:e,gain:g})});
  const j=await r.json(); alert(j.message||'已执行');
}
function fmt(v){ return (v===undefined||v===null)?'-':v; }
async function poll(){
  try{
    const s = await (await fetch('/api/state')).json();
    document.getElementById('model').textContent = s.model_label;
    document.getElementById('cfps').textContent = fmt(s.capture_fps);
    document.getElementById('ifps').textContent = fmt(s.infer_fps);
    document.getElementById('tms').textContent = fmt(s.total_ms);
    document.getElementById('ndet').textContent = s.detections.length;
    document.getElementById('th').value = Math.round(s.threshold*100);
    document.getElementById('thv').textContent = s.threshold.toFixed(2);
    document.getElementById('iv').textContent = s.interval;
    document.getElementById('fp').value = s.f_px;
    document.getElementById('m_self').className = s.model==='self'?'on':'';
    document.getElementById('m_stock').className = s.model==='stock'?'on':'';
    document.getElementById('cms').textContent = fmt(s.capture_ms);
    document.getElementById('pre').textContent = fmt(s.breakdown.pre);
    document.getElementById('bpu').textContent = fmt(s.breakdown.bpu);
    document.getElementById('post').textContent = fmt(s.breakdown.post);
    document.getElementById('enc').textContent = fmt(s.breakdown.encode);
    document.getElementById('tot').textContent = fmt(s.total_ms);
    const pct = s.total_ms>0 ? Math.min(100, s.breakdown.bpu/s.total_ms*100) : 0;
    document.getElementById('barbpu').style.width = pct.toFixed(0)+'%';
    document.getElementById('timing_demo').textContent =
      '端到端 '+fmt(s.total_ms)+' ms（取流 '+fmt(s.capture_ms)+' / 推理 '+fmt(s.infer_ms)+'）　目标 '+s.detections.length+' 个';
    const c = s.camera||{};
    document.getElementById('cam').textContent =
      c.device ? (c.device+' '+c.width+'x'+c.height+' 曝光'+c.exposure+' 增益'+c.gain) : '-';
    if(s.detections.length){
      const d=s.detections[0];
      document.getElementById('dist').textContent = d.distance_cm+' cm（框高 '+d.h_px+'px, conf '+d.score+'）';
    } else { document.getElementById('dist').textContent='-'; }
    const tb=document.getElementById('recs');
    tb.innerHTML = s.records.length ? s.records.map(r=>
      '<tr><td>'+r.time+'</td><td>'+r.score.toFixed(2)+'</td><td>'+r.h_px+'</td><td>'+r.distance_cm+'</td><td>'+r.lateral+'</td></tr>').join('')
      : '<tr><td colspan="5" class="muted">还没有检测</td></tr>';
    const err=document.getElementById('err');
    if(s.error){ err.style.display='block'; document.getElementById('errtext').textContent='⚠ '+s.error; }
    else { err.style.display='none'; }
  }catch(e){}
}
setInterval(poll, 400); poll();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    """HTTP：页面 / MJPEG 流 / 控制 API。"""

    def log_message(self, fmt: str, *args: Any) -> None:
        """静音默认日志（否则每帧一次请求会刷爆日志）。"""
        return

    def _json(self, payload: dict[str, Any], code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _mjpeg(self) -> None:
        """MJPEG 推流（multipart/x-mixed-replace）。"""
        assert PIPELINE is not None
        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        last = 0
        try:
            while PIPELINE.running:
                with PIPELINE.lock:
                    jpeg = PIPELINE.latest_jpeg
                if jpeg is None or len(jpeg) == last:
                    time.sleep(0.01)
                    continue
                last = len(jpeg)
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ")
                self.wfile.write(str(len(jpeg)).encode())
                self.wfile.write(b"\r\n\r\n")
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            return  # 浏览器关掉页面就正常结束

    def do_GET(self) -> None:
        """页面与状态查询。"""
        assert PIPELINE is not None
        if self.path in ("/", "/index.html"):
            self._html(PAGE)
        elif self.path.startswith("/stream.mjpg"):
            self._mjpeg()
        elif self.path.startswith("/api/state"):
            self._json(PIPELINE.state())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        """控制接口。"""
        assert PIPELINE is not None
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        path = self.path.split("?")[0]
        if path == "/api/config":
            if "threshold" in body:
                PIPELINE.threshold = max(0.01, min(0.99, float(body["threshold"])))
            if "interval" in body:
                PIPELINE.interval = max(1, int(body["interval"]))
            if "model" in body:
                PIPELINE.detector.use(str(body["model"]))
            if "f_px" in body:
                PIPELINE.f_px = max(50.0, float(body["f_px"]))
            for key in ("show_boxes", "show_lines", "show_text"):
                if key in body:
                    setattr(PIPELINE, key, bool(body[key]))
            self._json({"ok": True, **PIPELINE.state()})
        elif path == "/api/screenshot":
            self._json({"ok": True, "path": PIPELINE.screenshot()})
        elif path == "/api/csv":
            self._json({"ok": True, "path": PIPELINE.export_csv()})
        elif path == "/api/reset":
            with PIPELINE.lock:
                PIPELINE.records.clear()
            self._json({"ok": True})
        elif path == "/api/lock_exposure":
            message = PIPELINE.lock_exposure(int(body.get("exposure", 100)), int(body.get("gain", 0)))
            self._json({"ok": True, "message": message})
        else:
            self._json({"error": "not found"}, 404)


PIPELINE_PORT = 8093


def mode_selftest(args: argparse.Namespace) -> int:
    """全链路自测：不需要相机、不开 UI。"""
    print("=" * 70)
    print("自测：模型加载 + 前后处理 + 输出解码")
    print("=" * 70)
    print("[1/4] 加载模型 %s" % SELF_MODEL)
    detector = Detector()
    try:
        start = time.perf_counter()
        detector.use("self")
        detector.infer(synthetic_frame(640, 640, 0))
        print("      √ 加载 + 首次推理 %.2f s" % (time.perf_counter() - start))
    except Exception as error:
        print("      × 失败：%s" % error)
        return 1
    print("[2/4] 合成场景（浅色地面 + 深色圆柱，共 3 帧）")
    for seed in range(3):
        boxes, _, _, timing = detector.infer(synthetic_frame(args.width, args.height, seed))
        print("      帧%d：检出 %d 个，BPU %.1f ms（合成图与实拍差异大，检出 0 属正常）"
              % (seed, len(boxes), timing["bpu"]))
    print("[3/4] 类别名：%s" % detector.labels()[:3])
    print("[4/4] 官方模型对照")
    if Path(STOCK_MODEL).exists():
        try:
            detector.use("stock")
            detector.infer(synthetic_frame(640, 640, 0))
            print("      √ 官方 yolo11n 也能加载（%d 类）" % len(detector.labels()))
        except Exception as error:
            print("      ! 官方模型加载失败：%s" % error)
    else:
        print("      ! 板上没有官方预置模型，跳过")
    print("-" * 70)
    print("自测结果：通过")
    return 0


def mode_bench(args: argparse.Namespace) -> int:
    """纯性能：跑 N 次推理（不含取流/编码）。"""
    detector = Detector()
    detector.use("self")
    frame = synthetic_frame(1280, 720, 1)
    detector.infer(frame)  # 预热
    pre: list[float] = []
    bpu: list[float] = []
    post: list[float] = []
    for _ in range(max(1, args.bench)):
        _, _, _, timing = detector.infer(frame)
        pre.append(timing["pre"])
        bpu.append(timing["bpu"])
        post.append(timing["post"])
    total = [a + b + c for a, b, c in zip(pre, bpu, post, strict=True)]
    mean = sum(total) / len(total)
    print("=" * 70)
    print("性能测试：%d 次推理（%s）" % (len(total), Path(SELF_MODEL).name))
    print("  预处理 平均 %.2f ms" % (sum(pre) / len(pre)))
    print("  BPU    平均 %.2f ms" % (sum(bpu) / len(bpu)))
    print("  后处理 平均 %.2f ms" % (sum(post) / len(post)))
    print("  合计   平均 %.2f ms → %.1f FPS（不含取流与编码）" % (mean, 1000.0 / mean))
    return 0


def mode_image(args: argparse.Namespace) -> int:
    """单图/批量图：命令行输出检测结果。"""
    detector = Detector()
    detector.use("self")
    labels = detector.labels()
    out_dir = Path(args.save_dir) if args.save_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    print("=" * 70)
    print("图片推理：%d 张（阈值 %.2f，f_px %.0f）" % (len(args.image), args.threshold, args.f_px))
    print("=" * 70)
    total = 0
    for path in args.image:
        frame = cv2.imread(path)
        if frame is None:
            print("\n%s\n  读不出图片" % path)
            continue
        boxes, class_ids, scores, timing = detector.infer(frame)
        detections = filter_detections(boxes, class_ids, scores, labels, args.threshold, True)
        total += len(detections)
        print("\n%s  (%dx%d)  推理 %.1f ms"
              % (Path(path).name, frame.shape[1], frame.shape[0], sum(timing.values())))
        if detections:
            for line in describe_detections(detections, labels, frame.shape[1], args.f_px):
                print(line)
        else:
            print("  未检出目标")
        if out_dir:
            canvas = annotate(frame.copy(), detections, labels, f_px=args.f_px)
            target = out_dir / ("pred_" + Path(path).name)
            cv2.imwrite(str(target), canvas)
            print("  标注图：%s" % target)
    print("\n合计检出 %d 个目标" % total)
    return 0


def _cli_frames(args: argparse.Namespace) -> Iterator[tuple[str, np.ndarray]]:
    """命令行逐帧来源：图片目录（按序一遍）或合成场景（N 帧）。"""
    if args.frames_dir:
        files: list[str] = []
        for pattern in ("*.jpg", "*.jpeg", "*.png"):
            files += sorted(glob.glob(str(Path(args.frames_dir) / pattern)))
        for path in files:
            frame = cv2.imread(path)
            if frame is not None:
                yield Path(path).name, frame
        return
    for index in range(max(1, args.synthetic)):
        yield "synthetic#%d" % index, synthetic_frame(args.width, args.height, index)


def mode_frames(args: argparse.Namespace) -> int:
    """命令行逐帧跑（合成场景 / 图片目录），每帧打印检出，不开浏览器。"""
    detector = Detector()
    detector.use("self")
    labels = detector.labels()
    out_dir = Path(args.save_dir) if args.save_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    print("=" * 70)
    print("逐帧推理（阈值 %.2f，f_px %.0f）" % (args.threshold, args.f_px))
    print("=" * 70)
    deadline = time.time() + args.seconds if args.seconds else None
    count = 0
    total = 0
    for name, frame in _cli_frames(args):
        if deadline is not None and time.time() > deadline:
            print("\n到时间了（--seconds %.0f），提前结束" % args.seconds)
            break
        boxes, class_ids, scores, timing = detector.infer(frame)
        detections = filter_detections(boxes, class_ids, scores, labels, args.threshold, True)
        count += 1
        total += len(detections)
        print("\n[%d] %s  (%dx%d)  推理 %.1f ms"
              % (count, name, frame.shape[1], frame.shape[0], sum(timing.values())))
        if detections:
            for line in describe_detections(detections, labels, frame.shape[1], args.f_px):
                print(line)
        else:
            print("  未检出目标")
        if out_dir:
            canvas = annotate(frame.copy(), detections, labels, f_px=args.f_px)
            target = out_dir / (Path(name).stem + "_pred.jpg")
            cv2.imwrite(str(target), canvas)
            print("  标注图：%s" % target)
    print("\n共 %d 帧，合计检出 %d 个目标" % (count, total))
    return 0


def read_pid() -> int | None:
    """读后台进程 pid。"""
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    """进程还在吗。"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    """端口是否在监听。"""
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def board_ip() -> str:
    """板子 IP（拿不到就返回占位符）。"""
    found = run_ctl(["hostname", "-I"]).split()
    return found[0] if found else "<板子IP>"


def mode_stop() -> int:
    """停掉后台 demo。"""
    pid = read_pid()
    if pid is None or not pid_alive(pid):
        print("后台没在跑（pid 文件：%s）" % PID_FILE)
        PID_FILE.unlink(missing_ok=True)
        return 1
    os.kill(pid, signal.SIGTERM)
    print("已发送停止信号给 PID %d ..." % pid)
    for _ in range(50):
        if not pid_alive(pid):
            print("已停止。")
            PID_FILE.unlink(missing_ok=True)
            return 0
        time.sleep(0.1)
    print("没退出，强杀。")
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    PID_FILE.unlink(missing_ok=True)
    return 0


def mode_status(args: argparse.Namespace) -> int:
    """看状态：进程 / 端口 / 相机 / 截图目录。"""
    pid = read_pid()
    alive = pid is not None and pid_alive(pid)
    print("=" * 70)
    print("黑柱识别 demo 状态")
    print("  进程   : %s" % ("运行中 PID %d" % pid if alive and pid else "未运行"))
    print("  端口   : %d %s" % (args.port, "在监听" if port_open(args.port) else "没在监听"))
    print("  相机   : %s" % ("存在" if Path(args.device or "/dev/video0").exists()
                            else "没找到 %s" % (args.device or "/dev/video0")))
    print("  浏览器 : http://%s:%d/" % (board_ip(), args.port))
    print("  日志   : %s" % LOG_FILE)
    print("  截图   : %s" % SHOT_DIR)
    if not alive:
        print("\n  启动：python3 %s --cam 0 --daemon" % Path(__file__).name)
    return 0 if alive else 1


def mode_log(args: Any) -> int:
    """打印日志尾部。"""
    if not LOG_FILE.exists():
        print("还没有日志：%s" % LOG_FILE)
        return 1
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-max(1, args.log):]:
        print(line)
    return 0


def start_daemon(extra_argv: list[str]) -> int:
    """后台启动（把同一条命令重跑成子进程，写 pid 文件）。"""
    pid = read_pid()
    if pid is not None and pid_alive(pid):
        print("已经在后台跑了（PID %d）。要重启先：--stop" % pid)
        return 1
    argv = [sys.executable, str(Path(__file__).resolve())] + [
        item for item in extra_argv if item not in ("--daemon", "--no-show")
    ]
    with LOG_FILE.open("ab") as handle:
        proc = subprocess.Popen(argv, stdout=handle, stderr=handle, start_new_session=True)
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    time.sleep(3.0)
    if port_open(PIPELINE_PORT):
        print("已后台启动（PID %d）" % proc.pid)
        print("  浏览器：http://%s:%d/" % (board_ip(), PIPELINE_PORT))
        print("  看状态：python3 %s --status" % Path(__file__).name)
        print("  看日志：python3 %s --log 30" % Path(__file__).name)
        print("  停止  ：python3 %s --stop" % Path(__file__).name)
        return 0
    print("启动失败，日志尾部：")
    return mode_log(SimpleNamespace(log=20))


def print_run_summary(pipe: Pipeline, seconds: float) -> None:
    """跑 N 秒后的小结（--no-show 无人值守验证用）。"""
    state = pipe.state()
    camera = state["camera"] or {}
    print()
    print("=" * 70)
    print("运行 %.0f 秒小结（来源 %s）" % (seconds, camera.get("device", state["source"])))
    print("  取流   : %.1f 帧/秒（解码 %.1f ms/帧）" % (state["capture_fps"], state["capture_ms"]))
    print("  推理   : %.1f 次/秒（单次 %.1f ms，间隔 1/%d）"
          % (state["infer_fps"], state["infer_ms"], state["interval"]))
    print("  端到端 : %.1f 帧/秒（%.1f ms/帧）" % (state["e2e_fps"], state["total_ms"]))
    print("  耗时   : 缩放 %.1f ms ＋ 编码 %.1f ms"
          % (state["breakdown"]["resize"], state["breakdown"]["encode"]))
    print("  当前帧 : %d 个目标" % len(state["detections"]))
    for item in state["detections"]:
        print("           conf=%.3f  h=%dpx  ~%.0fcm" % (item["score"], item["h_px"], item["distance_cm"]))
    if state["records"]:
        print("  最近检出：")
        for item in state["records"][:3]:
            print("           %s  conf=%.3f  h=%dpx  ~%.0fcm  %s"
                  % (item["time"], item["score"], item["h_px"], item["distance_cm"], item["lateral"]))
    else:
        print("  最近检出：无")
    print("=" * 70, flush=True)


def parse_screen(text: str) -> tuple[int, int]:
    """``'1024x600'`` → ``(1024, 600)``。"""
    width, _, height = text.lower().partition("x")
    return int(width), int(height)


def detect_screen() -> tuple[int, int]:
    """屏幕当前分辨率（解析 xrandr；拿不到就按 1024x600 猜）。"""
    for line in run_ctl(["xrandr"]).splitlines():
        if "*" in line:
            token = line.split()[0] if line.split() else ""
            width, _, height = token.partition("x")
            if width.isdigit() and height.isdigit():
                return int(width), int(height)
    return 1024, 600


def paint_local(
    canvas: np.ndarray,
    stream: np.ndarray,
    detections: list[dict[str, Any]],
    pipe: Pipeline,
    frozen: bool = False,
) -> None:
    """把一帧按比例贴进画布，并在底部压暗一条状态栏（原地改 canvas）。

    不放大（``scale`` 上限 1.0）：放大只会更糊还多花几 ms。
    """
    screen_h, screen_w = canvas.shape[:2]
    frame_h, frame_w = stream.shape[:2]
    scale = min(screen_w / float(frame_w), screen_h / float(frame_h), 1.0)
    if scale < 0.999:
        shown = cv2.resize(
            stream,
            (max(1, int(frame_w * scale)), max(1, int(frame_h * scale))),
            interpolation=cv2.INTER_LINEAR,
        )
    else:
        shown = stream
    canvas[:] = 0
    y0 = (screen_h - shown.shape[0]) // 2
    x0 = (screen_w - shown.shape[1]) // 2
    canvas[y0:y0 + shown.shape[0], x0:x0 + shown.shape[1]] = shown

    top = screen_h - BAR_HEIGHT
    canvas[top:screen_h, :] = (canvas[top:screen_h, :] * 0.3).astype(np.uint8)
    line = "%.1f fps | e2e %.1f | thr %.2f | 1/%d | %s" % (
        pipe.capture_fps,
        pipe.e2e_fps,
        pipe.threshold,
        pipe.interval,
        ("%d target" % len(detections)) if detections else "no target",
    )
    if detections:
        best = max(detections, key=lambda item: item["score"])
        box_h = max(best["box"][3] - best["box"][1], 1.0)
        line += " | best conf %.2f  h=%.0fpx  ~%.0fcm" % (
            best["score"],
            box_h,
            pipe.f_px * CYL_HEIGHT_MM / box_h / 10.0,
        )
    cv2.putText(
        canvas, line, (10, screen_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
        (120, 255, 160), 1, cv2.LINE_AA,
    )
    if frozen:
        cv2.putText(
            canvas, "FROZEN", (screen_w // 2 - 70, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
            (0, 200, 255), 2, cv2.LINE_AA,
        )


def mode_local(args: argparse.Namespace) -> int:
    """HDMI 屏本地窗口显示：不编码 JPEG、不走 HTTP，延迟和帧率都最好。"""
    global PIPELINE
    if not os.environ.get("DISPLAY"):
        print("⚠ 没有 DISPLAY —— 在 ssh 里跑本地窗口前请先执行：")
        print("     export DISPLAY=:0 XAUTHORITY=/var/run/lightdm/root/:0")
        print("  （或者在 HDMI 屏上的终端里直接跑，那里本来就有 DISPLAY）")
        return 1
    screen_w, screen_h = args.screen or detect_screen()
    if args.stream_width == DEFAULT_STREAM_WIDTH and screen_w > args.stream_width:
        args.stream_width = screen_w  # 本地窗口没必要先缩到 960 再放大
    PIPELINE = Pipeline(args)
    for _ in range(150):
        if PIPELINE.latest_stream is not None or PIPELINE.error:
            break
        time.sleep(0.1)
    if PIPELINE.error:
        print("⚠ %s" % PIPELINE.error)
        return 1

    print("=" * 70)
    print("黑柱识别 demo（本地窗口，屏幕 %dx%d）" % (screen_w, screen_h))
    print("  模型 : %s" % SELF_MODEL)
    print("  来源 : %s" % PIPELINE.camera_info)
    print("  参数 : 阈值 %.2f｜间隔 1/%d｜送显 %dpx｜f_px %.0f"
          % (args.threshold, args.interval, args.stream_width, args.f_px))
    print("  按键 : q/Esc 退出　空格 冻结　s 截图　f 全屏切换　+/- 阈值　[/] 间隔　b/l/t 显隐")
    if args.seconds:
        print("  %.0f 秒后自动退出" % args.seconds)
    print("=" * 70, flush=True)

    canvas = np.zeros((screen_h, screen_w, 3), np.uint8)
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    fullscreen = not args.windowed  # 默认全屏：1024x600 的屏，窗口化会超出屏幕
    if fullscreen:
        cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    deadline = time.time() + args.seconds if args.seconds else None
    frozen = False
    try:
        while True:
            if deadline is not None and time.time() > deadline:
                print_run_summary(PIPELINE, args.seconds)
                break
            with PIPELINE.lock:
                stream = PIPELINE.latest_stream
                detections = list(PIPELINE.detections)
            if stream is not None and not frozen:
                paint_local(canvas, stream, detections, PIPELINE)
                cv2.imshow(WINDOW_NAME, canvas)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                print("\n收到退出键（q/Esc）")
                break
            if key == ord(" "):
                frozen = not frozen
                print("冻结：%s" % ("开" if frozen else "关"))
            elif key == ord("s"):
                print("已截图：%s" % PIPELINE.screenshot())
            elif key == ord("f"):
                fullscreen = not fullscreen
                cv2.setWindowProperty(
                    WINDOW_NAME,
                    cv2.WND_PROP_FULLSCREEN,
                    cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL,
                )
                print("全屏：%s" % ("开" if fullscreen else "关"))
            elif key in (ord("+"), ord("=")):
                PIPELINE.threshold = min(0.95, round(PIPELINE.threshold + 0.05, 2))
                print("置信度阈值 → %.2f" % PIPELINE.threshold)
            elif key in (ord("-"), ord("_")):
                PIPELINE.threshold = max(0.05, round(PIPELINE.threshold - 0.05, 2))
                print("置信度阈值 → %.2f" % PIPELINE.threshold)
            elif key == ord("["):
                PIPELINE.interval = max(1, PIPELINE.interval - 1)
                print("检测间隔 → 1/%d" % PIPELINE.interval)
            elif key == ord("]"):
                PIPELINE.interval = min(10, PIPELINE.interval + 1)
                print("检测间隔 → 1/%d" % PIPELINE.interval)
            elif key == ord("b"):
                PIPELINE.show_boxes = not PIPELINE.show_boxes
                print("框：%s" % ("开" if PIPELINE.show_boxes else "关"))
            elif key == ord("l"):
                PIPELINE.show_lines = not PIPELINE.show_lines
                print("辅助线：%s" % ("开" if PIPELINE.show_lines else "关"))
            elif key == ord("t"):
                PIPELINE.show_text = not PIPELINE.show_text
                print("画面文字：%s" % ("开" if PIPELINE.show_text else "关"))
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C，退出")
    finally:
        cv2.destroyAllWindows()
        cv2.waitKey(1)
        PIPELINE.running = False
    return 0


def mode_serve(args: argparse.Namespace, extra_argv: list[str]) -> int:
    """相机 / 图片目录 / 合成 + 浏览器 UI（主用法）。"""
    global PIPELINE
    if args.daemon:
        return start_daemon(extra_argv)
    PIPELINE = Pipeline(args)
    for _ in range(100):
        if PIPELINE.latest_jpeg is not None or PIPELINE.error:
            break
        time.sleep(0.1)
    print("=" * 70)
    print("黑柱识别 demo（来源：%s）" % args.source_label)
    print("  页面 : http://%s:%d/" % (board_ip(), args.port))
    print("  模型 : %s" % SELF_MODEL)
    print("  参数 : 阈值 %.2f｜间隔 1/%d｜推流 %dpx q%d｜f_px %.0f"
          % (args.threshold, args.interval, args.stream_width, args.quality, args.f_px))
    if PIPELINE.error:
        print("  ⚠ %s" % PIPELINE.error)
    else:
        print("  来源 : %s" % PIPELINE.camera_info)
    print("  Ctrl-C 退出" + ("（%.0f 秒后自动退出）" % args.seconds if args.seconds else ""))
    print("=" * 70, flush=True)

    if args.seconds:
        def stopper() -> None:
            """定时退出（无人值守验证用）。"""
            time.sleep(args.seconds)
            if PIPELINE is not None:
                if args.snapshot:
                    print("\n已保存画面：%s" % PIPELINE.save_frame(args.snapshot))
                print_run_summary(PIPELINE, args.seconds)
                PIPELINE.running = False
            time.sleep(0.3)
            os._exit(0)

        threading.Thread(target=stopper, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C，退出")
    finally:
        PIPELINE.running = False
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    """命令行开关（与 color_blob_demo.py 一个风格）。"""
    parser = argparse.ArgumentParser(
        description="黑柱识别 + 测距 demo（相机推流 / 单图 / 合成 / 自测 / 性能 / 后台管理）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
        "  python3 cyl_stream_ui.py --cam 0\n"
        "  python3 cyl_stream_ui.py --cam 0 --local\n"
        "  python3 cyl_stream_ui.py --cam 0 --daemon\n"
        "  python3 cyl_stream_ui.py --status / --log 30 / --stop\n"
        "  python3 cyl_stream_ui.py --image a.jpg b.jpg --save-dir /tmp/out\n"
        "  python3 cyl_stream_ui.py --frames-dir /tmp/cyl_test --no-show\n"
        "  python3 cyl_stream_ui.py --synthetic 3 --no-show\n"
        "  python3 cyl_stream_ui.py --selftest\n"
        "  python3 cyl_stream_ui.py --bench 200\n",
    )
    parser.add_argument("--cam", type=int, default=None, help="相机序号（0 → /dev/video0），实时 + UI")
    parser.add_argument("--device", default="", help="直接给设备节点（优先于 --cam）")
    parser.add_argument("--image", nargs="+", default=None, help="单图或一批图，命令行输出结果")
    parser.add_argument("--frames-dir", default="", help="图片目录当假相机（+--no-show 逐张打印，否则推流）")
    parser.add_argument("--synthetic", type=int, default=0, help="合成场景：跑 N 帧（+--no-show 命令行打印）")
    parser.add_argument("--selftest", action="store_true", help="全链路自测（无相机、无 UI）")
    parser.add_argument("--bench", type=int, default=0, help="跑 N 次推理测性能")
    parser.add_argument("--no-show", action="store_true", help="不开浏览器：相机跑几秒看统计；合成/目录逐帧打印")
    parser.add_argument("--local", action="store_true", help="HDMI 屏本地窗口显示（不经浏览器，帧率/延迟最好）")
    parser.add_argument("--windowed", action="store_true", help="本地窗口化显示（默认全屏，配 --local）")
    parser.add_argument("--screen", type=parse_screen, default=None, help="屏幕分辨率如 1024x600（默认 xrandr 自动检测）")
    parser.add_argument("--daemon", action="store_true", help="后台运行（配 --cam 用）")
    parser.add_argument("--stop", action="store_true", help="停掉后台 demo")
    parser.add_argument("--status", action="store_true", help="看状态")
    parser.add_argument("--log", type=int, default=0, help="打印日志尾部 N 行")
    parser.add_argument("--port", type=int, default=8093)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fourcc", default="MJPG")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--buffersize", type=int, default=4, help="设 1 会把取流砍到 ~15FPS（v4l2 后端）")
    parser.add_argument(
        "--capture", choices=("v4l2", "gst"), default="v4l2",
        help="取流后端：v4l2=OpenCV 软解；gst=GStreamer jpegdec（解码在独立线程，快得多）",
    )
    parser.add_argument("--threshold", type=float, default=0.45, help="置信度阈值")
    parser.add_argument("--interval", type=int, default=3, help="检测间隔（1=每帧推理）")
    parser.add_argument("--quality", type=int, default=65, help="推流 JPEG 质量")
    parser.add_argument("--stream-width", type=int, default=DEFAULT_STREAM_WIDTH, help="送显/推流宽度（0=原分辨率）")
    parser.add_argument("--f-px", type=float, default=DEFAULT_F_PX, help="测距焦距（现场标定）")
    parser.add_argument("--seconds", type=float, default=0.0, help="跑 N 秒后自动退出")
    parser.add_argument("--snapshot", default="", help="退出前把带框画面存到该路径")
    parser.add_argument("--save-dir", default="", help="--image 模式：保存带框图到该目录")
    return parser


def main() -> int:
    """入口：按开关分派到各模式。"""
    global PIPELINE_PORT
    parser = build_parser()
    args = parser.parse_args()
    PIPELINE_PORT = args.port

    # ① 后台管理类（不建管线）
    if args.stop:
        return mode_stop()
    if args.status:
        return mode_status(args)
    if args.log:
        return mode_log(args)
    # ② 离线模式
    if args.selftest:
        return mode_selftest(args)
    if args.bench:
        return mode_bench(args)
    if args.image:
        return mode_image(args)
    # ②b 命令行逐帧：合成场景 / 图片目录 + --no-show（不占相机、不开浏览器）
    if args.no_show and args.cam is None and not args.device and (args.frames_dir or args.synthetic):
        return mode_frames(args)

    # ③ 推流模式：确定帧来源
    if args.device:
        pass
    elif args.cam is not None:
        args.device = "/dev/video%d" % args.cam
    elif args.frames_dir or args.synthetic:
        args.device = ""
    else:
        parser.print_help()
        print("\n提示：最常用的是   python3 %s --cam 0" % Path(__file__).name)
        return 2

    if args.frames_dir:
        args.source_label = "图片目录 %s" % args.frames_dir
    elif args.synthetic:
        args.source_label = "合成场景 %d 帧" % args.synthetic
    else:
        args.source_label = "相机 %s" % args.device

    if args.no_show and not args.seconds and not args.daemon and not args.local:
        args.seconds = 5.0  # 命令行模式：跑几秒打印小结就退出
    if args.local:
        return mode_local(args)
    return mode_serve(args, [item for item in sys.argv[1:] if item != "--daemon"])


if __name__ == "__main__":
    raise SystemExit(main())
