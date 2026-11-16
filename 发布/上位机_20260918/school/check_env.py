#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_env.py —— 「我到底跑的是哪份代码 / 哪个目录」自检
================================================================================
什么时候跑它：
  · 改了参数/档案却"没生效"          · 不确定当前在哪个目录、用的是哪份库
  · 报"相机打不开 / 串口找不到"        · 怀疑有旧副本、或者程序开错了目录
  · 换了终端/重连 ssh 之后想确认环境

用法：
    cd /root/VisualBot/校赛 && python3 -u check_env.py
    （在哪个目录跑都行，它会把自己看到的都打出来）
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, "..")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

BAR = "=" * 70

print(BAR)
print("运行环境自检（程序开错了 / 参数没生效 / 相机串口找不到，先跑这个）")
print(BAR)

print("① 目录")
print("   当前工作目录 cwd : %s" % os.getcwd())
print("   本脚本所在目录   : %s" % HERE)
print("   Python           : %s  (%s)" % (sys.version.split()[0], sys.executable))

print("\n② 关键模块**实际加载自哪个文件** ← 「我跑的是哪份代码」就靠这个")
for m in ("school_main", "car", "vision", "aim", "lower_api", "school_config",
          "cam_rotate", "ring_center_detect_v2", "color_blob_demo", "monitor_ui"):
    try:
        mod = __import__(m)
        print("   %-24s %s" % (m, getattr(mod, "__file__", "?")))
    except Exception as exc:                                     # noqa: BLE001
        print("   %-24s !! 载入失败：%r" % (m, exc))

print("\n③ 现场标定档案（主程序会按顺序找下列位置，**第一个命中的就是它在用的**）")
for name in ("color_profile.json", "ring_calib.json", "aim_offset.txt"):
    cands = [os.path.join(os.getcwd(), name), name,
             os.path.join(HERE, name), os.path.join(HERE, "..", name)]
    seen, hits = [], []
    for p in cands:
        ap = os.path.abspath(p)
        if os.path.isfile(p) and ap not in seen:
            seen.append(ap)
            hits.append("%s   (%s)" % (ap, __import__("time").strftime(
                "%m-%d %H:%M", __import__("time").localtime(os.path.getmtime(p)))))
    print("   %-20s %s" % (name, ("\n" + " " * 23).join(hits) if hits else "（一个都没找到）"))

print("\n④ 相机设备（video1/video3 通常是 metadata 节点，打不开是正常的）")
devs = sorted(glob.glob("/dev/video*"))
if not devs:
    print("   （没有 /dev/video* —— 相机没插或驱动没起）")
for d in devs:
    try:
        with open("/sys/class/video4linux/%s/name" % os.path.basename(d)) as f:
            nm = f.read().strip()
    except Exception:                                            # noqa: BLE001
        nm = "?"
    print("   %-14s %s" % (d, nm))

print("\n⑤ 串口设备（下位机）")
ser = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
print("   %s" % (ser if ser else "（没有！下位机没插 / USB 掉了 → 跑 dmesg | tail -20 看 USB 断连）"))
# ★顺带**真开一下**（走 lower_api 的自动探测）：能不能用一眼就知道，不必去跑主程序冒险
try:
    import lower_api as LA
    _sl = LA.SerialLower("/dev/ttyUSB0", verbose=False)
    print("   打开测试：✓ 成功，实际用的是 %s（请求 /dev/ttyUSB0）" % _sl.port)
    _sl.close()
except Exception as exc:                                         # noqa: BLE001
    print("   打开测试：✗ 失败 → %s" % exc)

print("\n⑥ 正在跑的 python 进程（谁占着相机/串口）")
try:
    out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
    rows = [ln.strip() for ln in out.splitlines()
            if "python" in ln and "grep" not in ln and "ps -eo" not in ln]
    for ln in rows:
        print("   %s" % ln)
    if not rows:
        print("   （没有别的 python 在跑）")
except Exception as exc:                                         # noqa: BLE001
    print("   （查不到：%r）" % exc)

print("\n⑦ 查「某个进程到底在哪个目录跑」（最直接的排错手段）")
print("   ls -ld /proc/<PID>/cwd      # 例：ls -ld /proc/$(pgrep -f school_main)/cwd")

print("\n" + BAR)
print("怎么读：")
print("  · ② 里模块路径若不是 /root/VisualBot/... → 你在别的目录跑，或板子上还有旧副本")
print("  · ③ 里档案若出现在两个位置 → 调参台在两处各存了一份（不一致时以主程序读到的那份为准）")
print("  · ④⑤ 缺设备先查线；⑥ 有别的进程 → pkill -f <名字>，相机/串口同一时刻只能一个程序用")
print(BAR)
