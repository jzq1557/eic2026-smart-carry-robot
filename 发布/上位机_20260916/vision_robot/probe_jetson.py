#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_jetson.py —— 【旧名, 保留兼容】实际转调 probe_platform.py
================================================================
探测逻辑已通用化(Jetson Orin / RDK X5 / 树莓派 4B 都能用), 新代码请直接:
    python3 -u probe_platform.py --seconds 2
本文件只是为了让历史文档/脚本里写的 probe_jetson.py 仍然能跑。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
print("[note] probe_jetson.py 已改名为 probe_platform.py(通用), 这里自动转调", flush=True)

from probe_platform import main      # noqa: E402

if __name__ == "__main__":
    main()
