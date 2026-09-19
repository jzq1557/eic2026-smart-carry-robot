#!/bin/bash
# rdiag.sh —— ring_stream_ui 启动诊断(量模板构建耗时/看报错)
cd "$HOME/vision_robot" || exit 1
echo "--- ls ---"
ls -l ring_stream_ui.py digit_templates_ext.py
echo "--- 模板构建耗时 ---"
/usr/bin/time -f "install_wall=%e s" timeout 180 python3 -u -c 'import digit_templates_ext as d; print("install:", d.install(verbose=True))' 2>&1 | tail -5
echo "--- --help ---"
timeout 30 python3 -u ring_stream_ui.py --help 2>&1 | head -12
echo "--- 前台跑 25s(看是否起服务) ---"
timeout 25 python3 -u ring_stream_ui.py --image "工创赛标靶A4(12).png" --no-cam --port 8093 2>&1 | head -20
echo "rc=$?"
