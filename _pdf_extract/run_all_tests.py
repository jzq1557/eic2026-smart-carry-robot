#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键回归: 语法检查 + 三个测试套件 + 两个真实标靶图离线验证。"""
import ast
import os
import subprocess
import sys

os.chdir("/home/justin/vision_robot")

for f in ("ring_center_detect_v2.py", "qr_stream_demo.py"):
    ast.parse(open(f, encoding="utf-8").read())
    print(f"[syntax] {f} OK")

for t in ("test_rotation.py", "test_dashed.py", "test_stability.py"):
    r = subprocess.run([sys.executable, "-u", t], capture_output=True, text=True)
    tail = "\n".join(r.stdout.strip().splitlines()[-3:])
    print(f"[test] {t} rc={r.returncode}\n{tail}")

# 真实标靶图离线验证 (图片模式现在应打印 [result] 后自行退出)
for img in ("工创赛标靶A4(12).png", "工创赛标靶A4(13).png"):
    r = subprocess.run([sys.executable, "-u", "ring_center_detect_v2.py", img],
                       capture_output=True, text=True, timeout=60)
    out = [l for l in r.stdout.splitlines() if "[result]" in l or "not found" in l.lower()]
    print(f"[img] {img} rc={r.returncode}\n" + "\n".join(out[-4:]))

# 二维码真实图: test_qr.png
r = subprocess.run([sys.executable, "-u", "qr_stream_demo.py", "test_qr.png"],
                   capture_output=True, text=True, timeout=60)
out = [l for l in r.stdout.splitlines() if "[result]" in l or "未识别" in l]
print(f"[img] test_qr.png rc={r.returncode}\n" + "\n".join(out[-4:]))

print("ALL DONE")
