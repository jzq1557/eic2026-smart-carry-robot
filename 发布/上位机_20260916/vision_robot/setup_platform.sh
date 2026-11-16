#!/bin/bash
# ============================================================
# setup_platform.sh —— 上位机运行依赖准备（Jetson / RDK X5 / 树莓派 通用）
#
# 用法:  bash setup_platform.sh
#
# 说明:
#   · 纯视觉工具(ring_stream_ui.py / color_blob_stream.py / aim_and_place.py --demo)
#     只需要 python3 + opencv + numpy。各平台出厂情况:
#       Jetson Orin Nano : 自带 OpenCV 4.10(带 GStreamer/CUDA), numpy 1.23
#       RDK X5           : 自带 apt 版 OpenCV 4.5.4(**不带 GStreamer**), 需确认 numpy
#       树莓派 4B        : 一般已装 python3-opencv
#   · 只有"要通过串口控制下位机"才需要 pyserial(出厂常常没有, 本脚本帮你装)。
# ============================================================
set -u
echo "== python: $(python3 --version 2>&1)"

echo "== 平台 =="
if [ -r /proc/device-tree/model ]; then
  tr -d '\0' < /proc/device-tree/model; echo
fi
if [ -r /etc/nv_tegra_release ]; then head -1 /etc/nv_tegra_release; fi
if [ -d /usr/hobot ]; then echo "  (RDK X5: /usr/hobot 存在, BPU/TROS 工具链)"; fi

echo "== 检查 numpy / opencv =="
python3 - <<'PY' 2>&1
try:
    import numpy, cv2
    print("  numpy  ", numpy.__version__)
    print("  opencv ", cv2.__version__)
    info = cv2.getBuildInformation()
    for k in ("GStreamer", "v4l/v4l2", "CUDA"):
        for ln in info.splitlines():
            if ln.strip().startswith(k):
                print("  %s" % ln.strip())
except Exception as e:
    print("  !! 缺依赖: %r" % e)
    print("  Jetson 自带 opencv-python; 树莓派/RDK: sudo apt-get install -y python3-opencv python3-numpy")
PY

echo "== 检查 pyserial(只有要控制下位机才必须) =="
if python3 -c "import serial" 2>/dev/null; then
    python3 -c "import serial; print('  pyserial', serial.__version__, 'OK')"
    exit 0
fi
echo "  未安装, 尝试 pip3 install --user pyserial ..."
if pip3 install --user pyserial 2>&1 | tail -3; then
    python3 -c "import serial; print('  pyserial', serial.__version__, 'OK')" 2>&1
else
    echo "  !! 装不上(多半没网)。纯视觉用法不受影响; 需要串口时先解决网络,"
    echo "     或用 aim_and_place.py --demo/--stream 只跑视觉。"
    echo "     RDK X5 若 pip 受限可试: sudo apt-get install -y python3-serial"
fi
