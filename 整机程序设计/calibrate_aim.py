#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibrate_aim.py —— 半自动标定 K_PX2STEP(像素/脉冲),用于 aim_and_place.py 闭环

原理:视觉锁定圆心 → 发电机转 N 脉冲 → 量圆心移动了多少像素
      K = 像素位移 / 脉冲数  (每 1 脉冲圆心移动的像素)
分两轮: ① base轴(画面x方向)  ② 伸缩臂(画面y方向)
同时自动判定方向极性,直接打印出可粘贴进 aim_and_place.py 的配置。

用法:
  python3 calibrate_aim.py --port /dev/ttyUSB0
  交互提示:每次电机停稳后按回车,脚本自动测当前圆心位置;
            转完一轮自动算 K。Ctrl-C 中断。

前置:
  - 下位机已烧录(含 0x10~0x14 协议),USB-TTL 接 PA9/PA10
  - 靶标固定可见(用与 aim_and_place 相同的原版 RingDetector 检测)
"""
import sys, time, argparse
import cv2
import aim_and_place as A        # 复用检测器 / Lower / 摄像头逻辑

def grab_center(cap, det, n=5):
    """连续读 n 帧, 用原版 RingDetector 取圆心; 返回 (cx, cy, r) 或 None。
    ★坐标空间与 aim_and_place.aim_loop 保持一致: 统一按 CALIB 先旋转再检测,
     否则 rotate_ccw=True 时标定得到的 K/方向会和瞄准闭环使用的 x/y 对不上。"""
    xs, ys, rs = [], [], []
    for _ in range(n):
        ok, frame = cap.read()
        if not ok:
            continue
        frame = A.rotate_ccw(frame, A.CALIB)   # 与 aim_loop 同一旋转策略
        res = det.process(frame)     # ★原版检测(内部已做 proc_scale/EMA)
        if res["found"]:
            xs.append(res["cx_px"]); ys.append(res["cy_px"])
            rs.append(res.get("r_outer_px", 0))
        time.sleep(0.1)
    if not xs:
        return None
    return (sum(xs)/len(xs), sum(ys)/len(ys), sum(rs)/len(rs))

def measure_shift(cap, det, lower, motor_fn, dir_, pulses, vel, settle=1.2):
    """发一段运动,量圆心位移;返回 (位移像素, 方向sign)"""
    p0 = grab_center(cap, det)
    if p0 is None:
        print("  [err] 看不到圆心,无法标定"); return None
    print(f"  起点 center=({p0[0]:.1f},{p0[1]:.1f})")
    motor_fn(dir_, vel, pulses)          # 非阻塞发送
    time.sleep(settle)                    # 等运动完成
    p1 = grab_center(cap, det)
    if p1 is None:
        print("  [err] 运动后看不到圆心"); return None
    print(f"  终点 center=({p1[0]:.1f},{p1[1]:.1f})")
    return (p1[0] - p0[0], p1[1] - p0[1])   # (dpx, dpy)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--pulses", type=int, default=200, help="每轮测试脉冲数(默认200)")
    ap.add_argument("--vel", type=int, default=60, help="测试速度 RPM")
    ap.add_argument("--cam", type=int, default=None,
                    help="摄像头索引(默认自动尝试; 设备号漂移时可指定)")
    a = ap.parse_args()

    # ★与 aim_and_place 相同的摄像头/检测器(原版 RingDetector, 1280x720)
    cap = A.open_camera(a.cam if a.cam is not None else None)
    if cap is None:
        print("[err] camera open failed"); return 1
    det = A.make_detector()
    lower = A.Lower(a.port, a.baud)
    print(f"串口 OK,摄像头 OK(实际 {int(cap.get(3))}x{int(cap.get(4))})。靶标请保持可见。")
    print("=" * 60)
    print("第 1 轮:标定 base 轴(dir0=正转=夹爪左移=画面右移)")
    print("=" * 60)
    input("  按回车:发 base dir0 转 %d 脉冲..." % a.pulses)
    d0 = measure_shift(cap, det, lower, lower.motor4, 0, a.pulses, a.vel)
    input("  按回车:发 base dir1 转 %d 脉冲..." % a.pulses)
    d1 = measure_shift(cap, det, lower, lower.motor4, 1, a.pulses, a.vel)
    if d0 and d1:
        print(f"  dir0 位移 dx={d0[0]:+.1f}px | dir1 位移 dx={d1[0]:+.1f}px")
        # 选位移量大且方向正确的一方: 要 dir0 使画面右移(dx>0) 才符合"正转=画面右移"
        k0 = abs(d0[0]) / a.pulses
        k1 = abs(d1[0]) / a.pulses
        print(f"  K_base(dir0)={k0:.4f} px/脉冲   K_base(dir1)={k1:.4f} px/脉冲")
        if d0[0] > 0:
            print("  → dir0 使画面右移 ✓ 与机械事实一致")
            print(f"  建议 K_PX2STEP_BASE = {k0:.4f}")
        elif d1[0] < 0:
            print("  → dir1 使画面左移,方向与假设相反!建议把闭环 dir_ 两分支对调")
            print(f"  建议 K_PX2STEP_BASE = {k1:.4f}")
        else:
            print("  ⚠️ 两方向位移都异常,检查接线/靶标")
    print()
    print("=" * 60)
    print("第 2 轮:标定伸缩臂(dir0=正转=夹爪前移=画面下移)")
    print("=" * 60)
    input("  按回车:发 arm dir0 转 %d 脉冲..." % a.pulses)
    d0 = measure_shift(cap, det, lower, lower.motor1, 0, a.pulses, a.vel)
    input("  按回车:发 arm dir1 转 %d 脉冲..." % a.pulses)
    d1 = measure_shift(cap, det, lower, lower.motor1, 1, a.pulses, a.vel)
    if d0 and d1:
        print(f"  dir0 位移 dy={d0[1]:+.1f}px | dir1 位移 dy={d1[1]:+.1f}px")
        k0 = abs(d0[1]) / a.pulses
        k1 = abs(d1[1]) / a.pulses
        print(f"  K_arm(dir0)={k0:.4f} px/脉冲   K_arm(dir1)={k1:.4f} px/脉冲")
        if d0[1] > 0:
            print("  → dir0 使画面下移 ✓ 与机械事实一致")
            print(f"  建议 K_PX2STEP_ARM = {k0:.4f}")
        elif d1[1] < 0:
            print("  → dir1 使画面上移,方向与假设相反!建议把闭环 dir_ 两分支对调")
            print(f"  建议 K_PX2STEP_ARM = {k1:.4f}")
        else:
            print("  ⚠️ 两方向位移都异常,检查接线/靶标")

    print()
    print("==== 标定完成,把下面的值填进 aim_and_place.py ====")
    print("K_PX2STEP_BASE = <上面建议值>")
    print("K_PX2STEP_ARM  = <上面建议值>")
    print("(若提示方向相反,还要交换闭环里 dir_ 的两个分支)")
    cap.release()
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(130)
