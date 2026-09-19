#!/bin/bash
# 抓帧验证: 启动推流 -> 保存一帧 -> 打印检测统计
cd ~/vision_robot
pkill -f 'ring_center_detect_v2[.]py' 2>/dev/null
sleep 1
python3 -u ring_center_detect_v2.py --stream 8081 </dev/null >/tmp/stream.log 2>&1 &
SPID=$!
sleep 8
curl -s --max-time 3 http://127.0.0.1:8081/stream -o /tmp/frame.mjpeg
echo "mjpeg_bytes=$(wc -c < /tmp/frame.mjpeg)"
echo "--- stream log tail ---"
tail -2 /tmp/stream.log
kill $SPID 2>/dev/null
sleep 1
echo "--- detection stats ---"
python3 -u - <<'EOF'
import sys
sys.path.insert(0, '/home/justin/vision_robot')
import ring_center_detect_v2 as m
cap = m.cv.VideoCapture(0, m.cv.CAP_V4L2)
cap.set(m.cv.CAP_PROP_FOURCC, m.cv.VideoWriter_fourcc(*'MJPG'))
cap.set(m.cv.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(m.cv.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(m.cv.CAP_PROP_BUFFERSIZE, 1)
det = m.RingDetector(m.CALIB)
for i in range(6):
    ok, f = cap.read()
    if not ok:
        print('read fail')
        break
    r = det.process(f)
    if r and r.get('found'):
        print(f'frame{i}: FOUND cx={r["cx_px"]:.0f} cy={r["cy_px"]:.0f} rings={r["n_rings"]}/6 conf={r["conf"]:.2f} r_outer={r["r_outer_px"]:.0f}px')
    else:
        print(f'frame{i}: not found (n_rings={r.get("n_rings",0) if r else 0})')
cap.release()
EOF
echo CAPTURE_DONE
