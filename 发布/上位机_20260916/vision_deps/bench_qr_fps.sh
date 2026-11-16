#!/bin/bash
# 测 demo 实时 fps: 启动->8秒->停止->打日志 (避免 pkill 自匹配)
cd /home/justin/vision_robot || exit 1
pkill -f 'qr_stream_demo[.]py' 2>/dev/null
sleep 1
python3 -u qr_stream_demo.py --stream 8082 </dev/null >/tmp/qrfps.log 2>&1 &
PID=$!
sleep 8
kill $PID 2>/dev/null
sleep 1
echo '--- fps ---'
grep -a perf /tmp/qrfps.log | tail -6
echo '--- err ---'
grep -a -iE 'error|cannot' /tmp/qrfps.log | head -3
echo DONE
