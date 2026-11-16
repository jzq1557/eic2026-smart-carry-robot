#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tpl_time.py —— 量数字模板构建耗时(决定 UI 启动策略)"""
import time
import ring_center_detect_v2 as r
import digit_templates_ext as d

for step in (5, 15):
    r._DIGIT_TM = None
    t = time.time()
    labels, T = r._build_rot_templates(step)
    print("step=%2d  build=%6.1fs  templates=%d  T=%s"
          % (step, time.time() - t, len(labels), T.shape))

r._DIGIT_TM = None
t = time.time()
d.install(verbose=False)
print("install(custom empty) = %.1fs" % (time.time() - t))
