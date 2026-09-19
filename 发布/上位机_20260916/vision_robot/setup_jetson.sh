#!/bin/bash
# ============================================================
# setup_jetson.sh —— Jetson(或树莓派)上准备上位机运行依赖
#
# 用法:  bash setup_jetson.sh
#
# 说明:
#   · 纯视觉工具(ring_stream_ui.py / color_blob_stream.py / aim_and_place.py --demo)
#     只需要 python3 + opencv + numpy; Jetson 出厂镜像里就带了(实测 OpenCV 4.10 带 CUDA)。
#   · 只有"要通过串口控制下位机"才需要 pyserial(Jetson 出厂没有, 本脚本帮你装)。
#   · 树莓派上如果需要, 同一个脚本也能用。
# ============================================================
set -u
echo "== python: $(python3 --version 2>&1)"

echo "== 检查 numpy / opencv =="
python3 - <<'PY' 2>&1
try:
    import numpy, cv2
    print("  numpy  ", numpy.__version__)
    print("  opencv ", cv2.__version__)
    info = cv2.getBuildInformation()
    for k in ("GStreamer", "v4l/v4l2"):
        for ln in info.splitlines():
            if ln.strip().startswith(k):
                print("  %s" % ln.strip())
except Exception as e:
    print("  !! 缺依赖: %r" % e)
    print("  Jetson 自带 opencv-python; 树莓派可 apt-get install -y python3-opencv")
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
fi
