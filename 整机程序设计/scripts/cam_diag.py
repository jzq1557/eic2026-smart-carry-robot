#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
相机采集链诊断工具 —— 定位"画面卡/帧率极低"的瓶颈
==================================================
只测"纯采集"性能(不跑任何图像算法), 把帧率问题拆成三段:
  1. 后端/格式: MSMF(Windows默认) vs DSHOW, MJPG vs YUYV —— 0.4fps 的常见元凶
  2. 相机是否真正接受了分辨率/格式设置(很多相机静默忽略 -> 按1080p/YUYV工作)
  3. 曝光: 暗环境自动曝光会把每帧拖到几百ms -> 帧率崩盘

用法:
  python cam_diag.py                        # 默认: 0号相机, 640x480, MJPG
  python cam_diag.py --backend dshow        # Windows 换 DSHOW 后端
  python cam_diag.py --backend msmf --fourcc YUYV
  python cam_diag.py --index 1 --width 320 --height 240
  python cam_diag.py --no-exposure-lock     # 不锁曝光, 观察自动曝光的影响

解读输出:
  actual WxH    != 请求值      -> 相机忽略了设置, 在按原生分辨率(可能1080p)工作
  fourcc        = YUYV         -> 未进入MJPG模式(USB带宽受限)
  read fps      < 5            -> 采集链问题: 换后端/格式/锁曝光(下方 verdict 给建议)
  read fps      > 20           -> 采集没问题, 卡顿在算法或显示, 看 v2 的阶段耗时
"""

import argparse
import subprocess
import sys
import time

import cv2


def decode_fourcc(v):
    try:
        v = int(v)
        return ''.join(chr((v >> (8 * i)) & 0xFF) for i in range(4))
    except Exception:
        return '?'


def open_cam(index, w, h, fourcc, backend):
    backends = {
        'v4l2': cv2.CAP_V4L2, 'dshow': cv2.CAP_DSHOW,
        'msmf': cv2.CAP_MSMF, 'any': cv2.CAP_ANY,
    }
    try:
        cap = cv2.VideoCapture(index, backends[backend])
    except Exception as e:
        print(f'[open] backend {backend} 不可用: {e}')
        return None
    if not cap.isOpened():
        print(f'[open] 打不开相机 index={index} backend={backend}')
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)      # 只留最新帧, 消除排队延迟
    return cap


def lock_exposure(cap, manual_exposure):
    """锁定曝光/白平衡。注意单位: Linux V4L2 曝光单位=100us, Windows 单位=log2秒(负)。"""
    if manual_exposure is None:
        return
    r1 = cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)   # 0.25=手动
    r2 = cap.set(cv2.CAP_PROP_EXPOSURE, float(manual_exposure))
    r3 = cap.set(cv2.CAP_PROP_AUTO_WB, 0.0)
    g1 = cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
    g2 = cap.get(cv2.CAP_PROP_EXPOSURE)
    print(f'[exposure] set AUTO_EXP=0.25 -> {r1} (get={g1}), '
          f'set EXP={manual_exposure} -> {r2} (get={g2}), set AUTO_WB=0 -> {r3}')


def measure_read_fps(cap, seconds=3.0):
    t0 = time.time()
    n = 0
    last_frame = None
    while time.time() - t0 < seconds:
        ok, frame = cap.read()
        if ok:
            n += 1
            last_frame = frame
    dt = time.time() - t0
    return n / dt, last_frame


def linux_thermal():
    """树莓派: 显示降频/温度(降频会拖慢所有处理)。"""
    try:
        freq = open('/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq').read().strip()
        gov = open('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor').read().strip()
        temp = open('/sys/class/thermal/thermal_zone0/temp').read().strip()
        return (f'cpu={int(freq)/1000:.0f}MHz governor={gov} '
                f'temp={int(temp)/1000:.1f}C')
    except OSError:
        return None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', type=int, default=0)
    ap.add_argument('--width', type=int, default=640)
    ap.add_argument('--height', type=int, default=480)
    ap.add_argument('--fourcc', default='MJPG')
    ap.add_argument('--backend', default='any',
                    help='any / v4l2(树莓派) / dshow(Windows推荐) / msmf(Windows默认)')
    ap.add_argument('--exposure', type=float, default=None,
                    help='手动曝光值(缺省不锁曝光, 观察自动曝光影响)')
    ap.add_argument('--seconds', type=float, default=3.0)
    args = ap.parse_args()

    print(f'OpenCV {cv2.__version__}  python {sys.version.split()[0]}')
    info = cv2.getBuildInformation()
    for key in ('GStreamer', 'V4L/V4L2', 'DSHOW', 'MSMF', 'FFMPEG'):
        for line in info.splitlines():
            if line.strip().startswith(key + ':'):
                print(f'  build: {line.strip()}')
                break

    t = linux_thermal()
    if t:
        print(f'[thermal] {t}')
        try:
            out = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True,
                                 text=True, timeout=3).stdout.strip()
            if out:
                print(f'[throttled] {out}  (非 0x0 表示发生过降频)')
        except Exception:
            pass

    cap = open_cam(args.index, args.width, args.height, args.fourcc, args.backend)
    if cap is None:
        return

    # ---- 相机实际接受的工作模式 ----
    w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    fcc = decode_fourcc(cap.get(cv2.CAP_PROP_FOURCC))
    fps_req = cap.get(cv2.CAP_PROP_FPS)
    print(f'[mode]  请求 {args.width}x{args.height} {args.fourcc}  '
          f'-> 实际 {w:.0f}x{h:.0f} {fcc} (fps属性={fps_req})')
    if abs(w - args.width) > 2 or abs(h - args.height) > 2:
        print('  !! 相机没有接受分辨率设置(在按原生分辨率工作, 可能1080p)')
    if fcc.strip() != args.fourcc:
        print(f'  !! 相机没有接受 {args.fourcc} 格式(实际 {fcc}, USB带宽可能受限)')

    lock_exposure(cap, args.exposure)

    # ---- 第一次读帧延迟(冷启动) ----
    t0 = time.time()
    ok, _ = cap.read()
    print(f'[first-frame] {1000*(time.time()-t0):.0f} ms')

    # ---- 纯采集帧率 ----
    fps, last = measure_read_fps(cap, args.seconds)
    sz = f'{last.shape[1]}x{last.shape[0]}' if last is not None else '?'
    print(f'[read] 纯采集 {fps:.1f} fps  (帧尺寸 {sz})')

    # ---- 判定建议 ----
    print('\n[verdict]')
    if fps < 5:
        print('  * 采集链就是瓶颈, 与算法无关。依次尝试:')
        if args.backend == 'any' and sys.platform == 'win32':
            print('    a) 换后端:  python cam_diag.py --backend dshow   (HP等UVC相机MSMF下0.4fps是已知问题)')
            print('    b) 再不行: python cam_diag.py --backend dshow --fourcc YUYV')
        elif args.backend == 'msmf':
            print('    a) 换后端:  python cam_diag.py --backend dshow')
        else:
            print('    a) 树莓派:  sudo v4l2-ctl -d /dev/video0 --set-fmt-video=width=640,height=480,pixelformat=MJPG --set-parm=30')
            print('    b) 锁曝光(暗光下自动曝光会拖长每帧): --exposure 156  (Linux单位100us, 156≈1/64s)')
            print('       Windows DSHOW 单位是log2秒, 用负值: --exposure -5  (1/32s)')
            print('    c) 检查供电/线材: 树莓派 config.txt 加 max_usb_current_enable=1(旧型号)')
    elif fps < 15:
        print('  * 采集偏慢但仍可用; 若画面卡顿, 再测 dshow/v4l2 与 YUYV 组合')
    else:
        print(f'  * 采集正常({fps:.0f}fps)。若程序仍卡: 瓶颈在算法(看v2的阶段耗时)或显示(SSH/VNC下imshow极慢, 设 show=False)')

    cap.release()


if __name__ == '__main__':
    main()
