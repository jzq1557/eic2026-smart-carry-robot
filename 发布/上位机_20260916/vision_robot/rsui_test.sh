#!/bin/bash
# rsui_test.sh —— ring_stream_ui 无相机冒烟(用 A4 靶标图当输入源)
cd "$HOME/vision_robot" || exit 1
rm -f /tmp/rsui.log
(timeout 150 python3 -u ring_stream_ui.py --image "工创赛标靶A4(12).png" --no-cam --port 8091 >/tmp/rsui.log 2>&1 &)
sleep 20
echo "--- GET / ---"
curl -s -m 3 -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8091/
echo "--- GET /state ---"
curl -s -m 3 http://127.0.0.1:8091/state | head -c 460; echo
echo "--- GET /autotune ---"
curl -s -m 5 http://127.0.0.1:8091/autotune | head -c 420; echo
echo "--- GET /template?label=2 (打印体一键加模板) ---"
curl -s -m 5 'http://127.0.0.1:8091/template?label=2' | head -c 360; echo
echo "--- GET /state (加模板后再看) ---"
curl -s -m 3 http://127.0.0.1:8091/state | head -c 300; echo
echo "--- GET /snapshot ---"
curl -s -m 3 http://127.0.0.1:8091/snapshot; echo
echo "--- digit_custom 目录 ---"
find digit_custom -type f 2>/dev/null | head -5; find digit_custom -type f 2>/dev/null | wc -l
echo "--- log tail ---"
tail -8 /tmp/rsui.log
