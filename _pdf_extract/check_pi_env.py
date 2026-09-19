#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_pi_env.py —— 树莓派部署前环境自检(经 scp 上传后运行)"""
import sys

def probe(what, fn):
    try:
        fn()
        print("[ok]  %s" % what)
    except Exception as e:
        print("[NO]  %s -> %s" % (what, e))

def main():
    print("python:", sys.version.split()[0])
    import os
    print("cwd:", os.getcwd())
    probe("cv2", lambda: __import__("cv2").__version__ and None or None)
    probe("numpy", lambda: __import__("numpy"))
    probe("pyserial", lambda: __import__("serial"))
    probe("vision files",
          lambda: [__import__(m) for m in
                   ("ring_center_detect_v2", "aim_and_place",
                    "calibrate_aim", "smoke_calib")])
    import glob
    print("video:", glob.glob("/dev/video*")[:5])
    print("ttyUSB:", glob.glob("/dev/ttyUSB*"), "ttyACM:", glob.glob("/dev/ttyACM*"))
    print("CHECK_DONE")

if __name__ == "__main__":
    main()
