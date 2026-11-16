#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_platform.py —— 上位机运行平台探测：Jetson / RDK X5 / 树莓派 通用
=====================================================================
只读探测, 不开窗口, 输出全 ASCII(远端终端 CJK 常乱码):
  1) 平台: /proc/device-tree/model, 型号识别(Jetson / RDK X5 / Pi4), JetPack/RDK OS, 核数, 内存
  2) OpenCV: 版本 + 是否带 GStreamer + 是否带 V4L2 + CUDA/线程数
  3) 相机: /dev/video* 列表; CSI(NVIDIA nvarguscamerasrc) 与 UVC(V4L2) 各试一次
  4) 结论: 该不该用 --gst + 推荐 --perf 档位 + 实测相机 read 上限

用法(在任一台上位机上):
  python3 -u probe_platform.py                # 自动: 先试 CSI 再试 UVC
  python3 -u probe_platform.py --no-cam       # 只探测平台/OpenCV
  python3 -u probe_platform.py --seconds 3    # 每路相机多测几秒

(旧名 probe_jetson.py 仍然可用, 它只是转调本脚本。)
"""
import argparse
import glob
import os
import re
import sys
import time

HAS_CV2 = True
try:
    import cv2
    import numpy as np
except Exception as e:                                        # pragma: no cover
    HAS_CV2 = False
    print("[env] OpenCV/numpy import FAIL: %r" % e)


def line(t=""):
    print(t, flush=True)


def read_text(p):
    try:
        with open(p) as f:
            return f.read().strip()
    except Exception:
        return ""


def identify(model):
    """按 device-tree model / /proc/version 判断平台族。"""
    m = (model or "").lower()
    low = (read_text("/proc/version") + " " + read_text("/etc/nv_tegra_release")).lower()
    if any(k in m for k in ("orin", "xavier", "nano", "tegra", "jetson")) or "tegra" in low:
        return "jetson"
    if any(k in m for k in ("rdk", "d-robotics", "drobotics", "x5", "sunrise")):
        return "rdk"
    if "raspberry" in m or "bcm27" in m:
        return "pi"
    return "generic"


def plat_info():
    line("=" * 62)
    line("[platform]")
    model = read_text("/proc/device-tree/model").replace("\x00", "")
    fam = identify(model)
    line("  model      : %s" % (model or "(unknown, no device-tree)"))
    line("  family     : %s" % fam.upper())
    if fam == "jetson":
        teg = read_text("/etc/nv_tegra_release")
        if teg:
            line("  jetpack    : %s" % teg.splitlines()[0])
    if fam == "rdk":
        for p in ("/etc/version", "/etc/rdk-release", "/etc/hobot/version"):
            t = read_text(p)
            if t:
                line("  rdk os     : %s" % t.splitlines()[0])
        if os.path.exists("/usr/hobot"):
            line("  hobot      : /usr/hobot present (BPU/TROS 工具链)")
        if os.path.exists("/sys/class/vps"):
            line("  vps        : /sys/class/vps present (MIPI CSI 走 hobot VPS)")
    if fam == "pi":
        line("  pi model   : %s" % (read_text("/proc/device-tree/model").strip("\x00") or "?"))
    line("  kernel     : %s" % read_text("/proc/version").split(" ")[2] if read_text("/proc/version") else "")
    line("  os         : %s" % (read_text("/etc/os-release").splitlines()[0]
                                if read_text("/etc/os-release") else "?"))
    line("  cpu cores  : %s" % (os.cpu_count() or "?"))
    try:
        with open("/proc/meminfo") as f:
            m = re.search(r"MemTotal:\s+(\d+) kB", f.read())
        if m:
            line("  mem        : %.1f GB" % (int(m.group(1)) / 1048576.0))
    except Exception:
        pass
    line("  python     : %s" % sys.version.split()[0])
    line("  cwd        : %s" % os.getcwd())
    return fam


def cv_info():
    line("=" * 62)
    line("[opencv]")
    if not HAS_CV2:
        line("  (skipped, cv2 unavailable)")
        return
    line("  version    : %s" % cv2.__version__)
    info = ""
    try:
        info = cv2.getBuildInformation()
    except Exception as e:
        line("  buildinfo  : FAIL %r" % e)
    for key in ("GStreamer", "v4l/v4l2", "FFMPEG", "CUDA", "cuDNN", "NVIDIA CUDA",
                "Parallel framework", "OpenCL", "IPP"):
        m = re.search(r"^\s*%s\s*:\s*(.+)$" % re.escape(key), info, re.M)
        if m:
            line("  %-19s : %s" % (key, m.group(1).strip()))
    try:
        line("  threads    : %s" % cv2.getNumThreads())
    except Exception:
        pass
    line("  numpy      : %s" % (np.__version__ if HAS_CV2 else "?"))


def video_devices():
    line("=" * 62)
    line("[devices]")
    devs = sorted(glob.glob("/dev/video*"))
    if not devs:
        line("  no /dev/video* found")
    for d in devs:
        n = read_text("/sys/class/video4linux/%s/name" % os.path.basename(d))
        line("  %s%s" % (d, ("   name: " + n) if n else ""))
    try:
        tty = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
        line("  serial     : %s" % (", ".join(tty) if tty else "(none - 下位机 USB-TTL 没插)"))
    except Exception:
        pass


def try_capture(kind, gst=None, idx=0, seconds=2.0, size=(1280, 720)):
    """返回 (ok, 描述字符串, fps)"""
    if not HAS_CV2:
        return False, "no cv2", 0.0
    if kind == "csi":
        cap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
    else:
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(size[0]))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(size[1]))
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        return False, "open failed", 0.0
    t0 = time.time()
    ok, fr = cap.read()
    t_read = time.time() - t0
    if not ok or fr is None:
        cap.release()
        return False, "opened but read failed", 0.0
    h, w = fr.shape[:2]
    n, t0 = 0, time.time()
    while time.time() - t0 < seconds:
        ok, _ = cap.read()
        if not ok:
            break
        n += 1
    cap.release()
    fps = n / max(1e-6, (time.time() - t0))
    return True, "%dx%d first_read=%.0fms" % (w, h, t_read * 1000.0), fps


def has_gstreamer():
    if not HAS_CV2:
        return False
    try:
        return "GStreamer:                   YES" in cv2.getBuildInformation()
    except Exception:
        return False


def cam_probe(fam, seconds):
    line("=" * 62)
    line("[camera]  (make sure nothing else uses the camera!)")
    best = None
    # CSI: 只有 NVIDIA Jetson 有 nvarguscamerasrc
    if fam == "jetson" and HAS_CV2:
        gst = ("nvarguscamerasrc sensor-id=0 ! "
               "video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1 ! "
               "nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
               "video/x-raw,format=BGR ! appsink drop=1 max-buffers=1")
        for sid in (0, 1):
            g = gst.replace("sensor-id=0", "sensor-id=%d" % sid)
            ok, desc, fps = try_capture("csi", gst=g, seconds=seconds)
            line("  CSI sensor-id=%d : %s%s" % (sid, "OK  " if ok else "FAIL", desc))
            if ok:
                line("      -> %.1f fps (read+decode ceiling)" % fps)
                if best is None:
                    best = ("gst", sid, fps)
                break
    elif fam == "rdk":
        line("  (RDK X5 的 MIPI CSI 不走 nvarguscamerasrc: 由 hobot VPS/mipi_cam 驱动,")
        line("   正常出图后会在 /dev/video* 出现节点 -> 直接当 UVC 用 --cam N)")
    # UVC / V4L2: 所有平台通用
    for i in (0, 1, 2, 3):
        if not os.path.exists("/dev/video%d" % i):
            continue
        ok, desc, fps = try_capture("uvc", idx=i, seconds=seconds)
        line("  UVC /dev/video%d  : %s%s" % (i, "OK  " if ok else "FAIL", desc))
        if ok:
            line("      -> %.1f fps (read+decode ceiling)" % fps)
            if best is None:
                best = ("uvc", i, fps)
    return best


def verdict(fam, best):
    line("=" * 62)
    line("[verdict]")
    if best is None:
        line("  no working camera found -> run with --no-cam / --image for offline checks")
        if fam == "rdk":
            line("  (RDK X5: 没插 USB 摄像头、或 MIPI 摄像头驱动没加载 -> 检查 dmesg | grep -i -E 'mipi|gc|imx|ov'")
    else:
        kind, idx, fps = best
        if kind == "gst":
            line("  camera     : CSI sensor-id=%d" % idx)
            line("  run with   : --gst --sensor-id %d" % idx)
        else:
            line("  camera     : UVC /dev/video%d" % idx)
            line("  run with   : --cam %d   (不要加 --gst)" % idx)
        line("  read ceiling: %.1f fps" % fps)
    if not has_gstreamer():
        line("  note       : 本机 OpenCV 不带 GStreamer -> --gst 无用, 一律走 V4L2")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from color_blob_demo import detect_perf, PERF_PRESETS
        name = detect_perf()
        line("  perf preset: %s  %s" % (name, PERF_PRESETS[name]))
        line("  run with   : --perf %s   (或 --perf auto 自动判断)" % name)
    except Exception as e:
        line("  perf preset: (无法导入 color_blob_demo: %r)" % e)
    line("=" * 62)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cam", action="store_true")
    ap.add_argument("--seconds", type=float, default=2.0)
    a = ap.parse_args()
    fam = plat_info()
    cv_info()
    video_devices()
    best = None
    if not a.no_cam:
        best = cam_probe(fam, a.seconds)
    verdict(fam, best)


if __name__ == "__main__":
    main()
