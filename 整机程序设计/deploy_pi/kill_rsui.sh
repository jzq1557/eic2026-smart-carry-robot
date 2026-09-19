#!/bin/bash
# kill_rsui.sh —— 安全清理 ring_stream_ui 残留实例(避开自身包装进程)
pids=$(ps -eo pid,args | awk '/ring_stream_ui\.py/ && !/awk/ && !/kill_rsui/ && !/bash -c/ {print $1}')
if [ -n "$pids" ]; then
  echo "killing: $pids"
  for p in $pids; do kill "$p" 2>/dev/null; done
  sleep 2
else
  echo "no running instance"
fi
echo "--- after ---"
ps -eo pid,args | awk '/ring_stream_ui\.py/ && !/awk/ && !/kill_rsui/ && !/bash -c/ {print}' || true
echo "done"
