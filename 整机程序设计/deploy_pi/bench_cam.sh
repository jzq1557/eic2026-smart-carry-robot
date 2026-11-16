#!/bin/bash
# ============================================================
# bench_cam.sh —— 真实相机下的端到端性能测量(树莓派 / Jetson 通用)
#
# 用法(在 ~/vision_robot 或 ~/VisualBot 里, 先确保没有别的程序占用相机):
#   bash bench_cam.sh             # 每项 16 秒
#   bash bench_cam.sh 20 0        # 每项 20 秒, 相机 /dev/video0
#
# 与 bench_perf.sh 的区别: bench_perf.sh 用合成画面测"纯算力";
#   本脚本开真相机, 测的是"端到端"(含相机 read 阻塞)的真实帧率。
#   注意 fps= 是处理循环的速度: 后台采集线程会不断复用最新帧,
#   所以处理 fps 可能高于相机实际出帧率 —— 看 probe_jetson.py 的 read 上限。
# ============================================================
set -u
SECS=${1:-16}
CAM=${2:-0}
PORT=${3:-8098}
cd "$(dirname "$0")" || exit 1

echo "== host: $(hostname)  cores: $(nproc)  camera=/dev/video${CAM} =="

echo "=== ring_stream_ui  (相机 ${CAM}, perf=high) ==="
timeout "$SECS" python3 -u ring_stream_ui.py --cam "$CAM" --perf high --port "$PORT" 2>&1 \
  | grep -E "\[perf\]|\[cam\]|\[loop\]|\[warn\]" | tail -4

echo "=== color_blob_stream  (相机 ${CAM}, perf=high) ==="
timeout "$SECS" python3 -u color_blob_stream.py --cam "$CAM" --perf high --port "$PORT" 2>&1 \
  | grep -E "\[perf\]|\[cam\]|\[loop\]|\[warn\]" | tail -4

echo "=== aim_and_place --demo (相机 ${CAM}, 不连车) ==="
timeout "$SECS" python3 -u aim_and_place.py --demo --stream "$PORT" 2>&1 \
  | grep -E "\[cam\]|fps|frame" | tail -6

echo "== done =="
