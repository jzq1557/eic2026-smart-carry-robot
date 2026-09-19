#!/bin/bash
# 720p 帧率验证: 启动 -> 9秒 -> 停止 -> 打日志 (脚本自身不会被 pkill 匹配)
cd /home/justin/vision_robot || exit 1
pkill -f 'ring_center_detect_v2[.]py' 2>/dev/null
sleep 1
python3 -u ring_center_detect_v2.py --stream 8101 </dev/null >/tmp/live9.log 2>&1 &
PID=$!
sleep 9
kill $PID 2>/dev/null
sleep 1
echo '--- fps ---'
grep -a perf /tmp/live9.log | tail -5
echo '--- errors ---'
grep -a -iE 'error|cannot' /tmp/live9.log | head -3
echo 'DONE'
