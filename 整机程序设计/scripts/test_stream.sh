#!/bin/bash
# 端到端流测试: 启动 -> 端口/首页/视频流检查 -> 清理(与主程序无关的临时验证)
cd ~/vision_robot
pkill -f 'ring_center_detect_v2[.]py' 2>/dev/null
sleep 1
python3 -u ring_center_detect_v2.py --stream 8081 </dev/null >/tmp/t.log 2>&1 &
PID=$!
sleep 10
echo "--- port ---"
ss -tln | grep 8081 || echo PORT_NOT_LISTENING
echo "--- page ---"
curl -s -o /dev/null -w 'page: http=%{http_code} bytes=%{size_download}\n' --max-time 3 http://127.0.0.1:8081/
echo "--- stream 2s ---"
curl -s -o /dev/null -w 'stream: http=%{http_code} bytes=%{size_download}\n' --max-time 2 http://127.0.0.1:8081/stream
echo "--- perf tail ---"
tail -3 /tmp/t.log
kill $PID 2>/dev/null
pkill -f 'ring_center_detect_v2[.]py' 2>/dev/null
echo TEST_DONE
