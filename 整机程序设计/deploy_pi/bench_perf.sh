#!/bin/bash
# ============================================================
# bench_perf.sh —— 本机视觉工具「性能档」对比(树莓派4B / Jetson Orin 通用)
#
# 用法(在 ~/vision_robot 或 ~/VisualBot 里):
#   bash bench_perf.sh            # 每档 12 秒
#   bash bench_perf.sh 20 8099    # 每档 20 秒, 用 8099 端口
#
# 说明: 用 --no-cam(合成靶标/合成色块)跑, 不占相机, 反映的是"纯算力 + 编码"上限;
#       真实帧率还会被相机 read 速度卡住(树莓派 UVC 实测 ~17fps, Jetson 内置摄像头 ~15fps)。
#       关注输出里的 fps= 和 loop_ms=: fps 是处理+编码的整环速度, loop_ms 是单帧耗时。
# ============================================================
set -u
SECS=${1:-12}
PORT=${2:-8093}
cd "$(dirname "$0")" || exit 1

echo "== host: $(hostname)   cores: $(nproc) =="
python3 -c "import cv2;print('== opencv', cv2.__version__, ' threads', cv2.getNumThreads())" 2>/dev/null
python3 -c "import sys;sys.path.insert(0,'.');from color_blob_demo import detect_perf,PERF_PRESETS;n=detect_perf();print('== 本机推荐档位: %s %s' % (n, PERF_PRESETS[n]))" 2>/dev/null

for p in low mid high rdk; do
  echo "=== ring_stream_ui  perf=$p  (${SECS}s) ==="
  timeout "$SECS" python3 -u ring_stream_ui.py --no-cam --perf "$p" --port "$PORT" 2>&1 \
    | grep -E "\[perf\]|\[loop\]" | tail -3
done

for p in low mid high rdk; do
  echo "=== color_blob_stream perf=$p  (${SECS}s) ==="
  timeout "$SECS" python3 -u color_blob_stream.py --no-cam --perf "$p" --port "$PORT" 2>&1 \
    | grep -E "\[perf\]|\[loop\]" | tail -3
done
echo "== done =="
