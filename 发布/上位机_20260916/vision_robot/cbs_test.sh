#!/bin/bash
# cbs_test.sh —— color_blob_stream 冒烟测试(在树莓派上跑, 纯 HTTP, 无 GUI)
cd "$HOME/vision_robot" || exit 1
rm -f /tmp/cbs.log
(timeout 30 python3 -u color_blob_stream.py --cam 0 --port 8090 >/tmp/cbs.log 2>&1 &)
sleep 6
echo "--- GET / ---"
curl -s -m 3 -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8090/
echo "--- GET /state ---"
curl -s -m 3 http://127.0.0.1:8090/state | head -c 320; echo
echo "--- GET /set?circ_min=0.5 ---"
curl -s -m 3 'http://127.0.0.1:8090/set?circ_min=0.5'; echo
echo "--- GET /color?name=blue ---"
curl -s -m 3 'http://127.0.0.1:8090/color?name=blue'; echo
echo "--- GET /roi?x=640&y=360 ---"
curl -s -m 3 'http://127.0.0.1:8090/roi?x=640&y=360'; echo
echo "--- GET /calib ---"
curl -s -m 3 'http://127.0.0.1:8090/calib'; echo
echo "--- GET /snapshot ---"
curl -s -m 3 'http://127.0.0.1:8090/snapshot'; echo
echo "--- stream 首 2KB 头 ---"
curl -s -m 3 http://127.0.0.1:8090/stream | head -c 120 | tr -d '\0' | head -3; echo
sleep 16
echo "--- log tail ---"
tail -6 /tmp/cbs.log
echo "--- leftover ---"
pgrep -af color_blob_stream.py || echo none
