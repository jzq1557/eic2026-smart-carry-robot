#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
school_flow.py —— 校赛整场主程序（上位机大脑）
================================================
状态机（详见 校赛开发.md §1.4）：

    M0 IDLE → M1 出启动区 → M2 扫码 → M3 进原料区(+base左转90°)
    → M4 取料循环(3 次瞄准 G1/G2/G3) → M5 前进+自旋+进加工区
    → M6 放置循环(3 次瞄准 P1/P2/P3) → M7 前进+右移 → M8 DONE

六次瞄准：
    · G1/G2/G3：物料位 → 瞄准 → **直接夹**（顺带颜色确认）
    · P1/P2/P3：靶位 → 瞄准记偏移 → 归位 → 去物架夹料 → 回放偏移 → 放料

用法（在 RDK 的 ~/VisualBot/校赛 或本机）：
    python3 -u school_flow.py --dry-run                 # 空跑：不连车/不用相机，验证逻辑
    python3 -u school_flow.py --dry-run --code 321+123  # 指定任务码空跑
    python3 -u school_flow.py --port /dev/ttyUSB0       # 真跑
    python3 -u school_flow.py --port /dev/ttyUSB0 --from M4   # 从中断处继续
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
for _p in (os.path.join(HERE, "..", "deploy_pi"), "/root/VisualBot", "/home/justin/vision_robot"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import school_config as SC                                     # noqa: E402
import lower_api as LA                                         # noqa: E402
from ledger import Ledger                                      # noqa: E402


# ----------------------------------------------------------------------
class Ctx:
    """一场比赛的全部上下文（换成 dataclass 也行，这里保持简单）。"""

    def __init__(self, args):
        self.args = args
        self.profile = SC.load_profile()
        self.route = self.profile["route"]
        _pos = {k: dict(v) for k, v in (self.profile.get("positions") or {}).items()
                if isinstance(v, dict)}                  # 跳过 JSON 里的 _comment
        if "material" not in _pos or "target" not in _pos:
            _pos.setdefault("material", {str(k): float(v) for k, v in SC.MATERIAL_POS_CM.items()})
            _pos.setdefault("target", {str(k): float(v) for k, v in SC.TARGET_POS_CM.items()})
        self.pos = _pos
        self.lower = None
        self.vision = None
        self.color = None
        self.aim = None
        self.ledger = Ledger()
        self.code = args.code
        self.pick_order = getattr(args, "pick_order", None)
        self.place_order = getattr(args, "place_order", None)
        self.cur_cm = 0.0                 # 当前纵向坐标(cm)
        self.t0 = time.time()
        self.fail = []

    def log(self, *a):
        print("[%6.1fs] %s" % (time.time() - self.t0, " ".join(str(x) for x in a)), flush=True)


def build_lower(args):
    if args.dry_run:
        return LA.MockLower(verbose=True)
    return LA.SerialLower(port=args.port, baud=args.baud, verbose=True)


def build_vision(args):
    if args.dry_run:
        from vision import MockVision
        # 仿真：目标初始偏离画面中心，让 PID 真的收敛（能看到 6 次瞄准的全过程）
        return MockVision(cx=320.0, cy=210.0, w=512, h=384), None
    from vision import RingVision, ColorVision
    rv = RingVision(perf=args.perf, cam_index=SC.CAM_FRONT)
    if not rv.open():
        raise RuntimeError("前方作业相机打不开（--cam %d）" % SC.CAM_FRONT)
    return rv, ColorVision()


def sync_or_die(ctx, args):
    """★上电同步：把 motion_profile.json 的参数/动作组全部下发 + 读回校验。"""
    ctx.log("M0 初始化：下发参数与动作组 ...")
    ok, fail = ctx.lower.sync_profile(ctx.profile)
    if not ok and not args.dry_run:
        ctx.log("!! 同步失败：%s —— 拒绝发车（先修好再跑）" % fail)
        if not args.force:
            return False
    ctx.log("参数/动作组同步完成（组数 %d）" % len(ctx.lower.gid))
    return True


# ----------------------------------------------------------------------
# 移动辅助：统一用"纵向坐标"表达，自动决定前进还是后退
# ----------------------------------------------------------------------
def goto_cm(ctx, target_cm, tag=""):
    delta = float(target_cm) - ctx.cur_cm
    if abs(delta) < 1.0:
        ctx.log("  %s已在位（%.0fcm）" % (tag, ctx.cur_cm))
        return
    d = "fwd" if delta > 0 else "back"
    ctx.log("  %s纵向 %s %.0fcm（%.0f → %.0f）" % (tag, "前进" if delta > 0 else "后退",
                                                abs(delta), ctx.cur_cm, target_cm))
    ctx.lower.move(d, abs(delta))
    ctx.cur_cm = float(target_cm)
    # ★dry-run 专用：车一动，画面里的目标就偏了（不模拟这一步的话，仿真里从第 2 次瞄准起
    #   "一次都不动就判收敛"，测不出 6 次瞄准的真实迭代）。真实视觉下这天然发生。
    if hasattr(ctx.vision, "nudge"):
        ctx.vision.nudge(-delta * 1.5, 0.0)          # 1cm ≈ 画面偏 1.5px（示意值）


# ----------------------------------------------------------------------
# 状态机
# ----------------------------------------------------------------------
def m1_leave_start(ctx):
    ctx.log("M1 右移出启动区 + 前进到扫码位")
    ctx.lower.move("right", ctx.route["leave_start_right_cm"])
    ctx.lower.move("fwd", ctx.route["to_scan_fwd_cm"])


def m2_scan(ctx):
    ctx.log("M2 侧装摄像头扫码")
    if ctx.args.code:
        ctx.code = ctx.args.code
        ctx.log("  使用命令行指定码：%s" % ctx.code)
    else:
        import qr_scan
        code, ok = qr_scan.listen(ctx.vision, timeout=SC.T_SCAN, log=ctx.log)
        ctx.code = code
        if not ok:
            ctx.fail.append("scan_timeout")
    a, b = SC.parse_code(ctx.code)
    if not a:
        ctx.log("!! 任务码不合法：%r → 用兜底码 %s" % (ctx.code, SC.CODE_DEFAULT))
        a, b = SC.parse_code(SC.CODE_DEFAULT)
        ctx.fail.append("code_invalid")
    ctx.pick_order, ctx.place_order = a, b
    ctx.log("  任务码 %s → 取料顺序 %s / 放置顺序 %s" % (ctx.code, a, b))
    ctx.lower.show_qr(ctx.code)          # 上屏（规则要求显示任务码）


def m3_to_material(ctx):
    ctx.log("M3 进原料区 + base 左转 90° 进入作业姿态")
    ctx.lower.move("fwd", ctx.route["to_material_fwd_cm"])
    ctx.cur_cm = 0.0
    ctx.lower.run_group("BASE_TO_LEFT")


def m4_pick(ctx):
    """★G1/G2/G3：按取料顺序，逐个移动到位 → 瞄准 → 直接夹。"""
    ctx.log("M4 取料循环（3 次瞄准 G1/G2/G3）")
    for i, mid in enumerate(ctx.pick_order, start=1):
        pos = float(ctx.pos["material"][mid])
        ctx.log("G%d：去 %s 号位（%s，%.0fcm）" % (i, mid, SC.color_name(mid), pos))
        goto_cm(ctx, pos, tag="  ")
        ok, off = ctx.aim.grab(expect_color=SC.color_name(mid), tag="G%d" % i)
        if not ok:
            ctx.log("  !! G%d 抓取失败，跳过该物料" % i)
            ctx.fail.append("pick_%s" % mid)
            continue
        slot = ctx.ledger.record(mid, i)
        ctx.log("  ✓ 已上车：%s 号 → slot %d（账本 %s）" % (mid, slot, ctx.ledger.summary()))
    ctx.log("取料结束：%s" % ctx.ledger.summary())
    ctx.lower.run_group("BASE_TO_HOME")


def m5_to_process(ctx):
    ctx.log("M5 出原料区 → 前进 → 原地自旋 90° → 进加工区")
    ctx.lower.move("fwd", ctx.route["to_process_fwd_cm"])
    ctx.lower.spin("cw", ctx.route["spin_deg"])
    ctx.lower.move("fwd", ctx.route["enter_process_fwd_cm"])
    ctx.cur_cm = 0.0
    ctx.lower.run_group("BASE_TO_LEFT")


def m6_place(ctx):
    """★P1/P2/P3：按放置顺序，逐个移动到位 → 瞄准→取料→放料。"""
    ctx.log("M6 放置循环（3 次瞄准 P1/P2/P3）")
    plan = SC.place_plan(ctx.place_order)
    for i, (mid, tgt) in enumerate(plan, start=1):
        ctx.log("P%d：%s 号料（%s）→ 加工区 %d 号靶位" % (i, mid, SC.color_name(mid), tgt))
        try:
            slot = ctx.ledger.slot_of(mid)
        except RuntimeError as e:
            ctx.log("  !! %s，跳过" % e)
            ctx.fail.append("place_%s" % mid)
            continue
        goto_cm(ctx, float(ctx.pos["target"][str(tgt)]), tag="  ")
        if ctx.aim.place_mission(slot=slot, tag="P%d" % i):
            ctx.ledger.consume(mid)
            ctx.log("  ✓ 已放置：%s 号 → 靶位 %d（账本 %s）" % (mid, tgt, ctx.ledger.summary()))
        else:
            ctx.log("  !! P%d 放置失败" % i)
            ctx.fail.append("place_%s" % mid)
    ctx.lower.run_group("BASE_TO_HOME")


def m7_finish(ctx):
    ctx.log("M7 前进 + 向右长距平移 → 结束位")
    ctx.lower.move("fwd", ctx.route["to_finish_fwd_cm"])
    ctx.lower.move("right", ctx.route["finish_right_cm"])


def m8_done(ctx):
    ctx.lower.stop()
    placed = len(ctx.ledger.done)
    ctx.log("M8 DONE：放置 %d/3，用时 %.1fs，账本 %s"
            % (placed, time.time() - ctx.t0, ctx.ledger.summary()))
    if ctx.fail:
        ctx.log("异常记录：%s" % ", ".join(sorted(set(ctx.fail))))
    ctx.log("=== 完赛 ===")


STATES = [("M1", m1_leave_start), ("M2", m2_scan), ("M3", m3_to_material),
          ("M4", m4_pick), ("M5", m5_to_process), ("M6", m6_place),
          ("M7", m7_finish), ("M8", m8_done)]


def run_match(ctx, start="M1"):
    names = [s for s, _ in STATES]
    if start not in names:
        raise SystemExit("--from 只能是 %s" % names)
    idx = names.index(start)
    if idx > 0:
        ctx.log("从中断点 %s 继续（跳过前面的状态）" % start)
    for name, fn in STATES[idx:]:
        ctx.log("=== %s ===" % name)
        fn(ctx)
    return ctx


# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dry-run", action="store_true", help="不连车/不用相机，验证整场逻辑")
    ap.add_argument("--code", default=None, help="直接指定任务码(跳过扫码)，如 123+321")
    ap.add_argument("--from", dest="start", default="M1", help="从中断点继续，如 M4")
    ap.add_argument("--perf", default=SC.PERF, help="视觉性能档(auto/low/mid/high/rdk)")
    ap.add_argument("--force", action="store_true", help="同步校验失败也强行发车")
    a = ap.parse_args()

    ctx = Ctx(a)
    print("=" * 66)
    print("校赛主程序 | dry_run=%s | port=%s | perf=%s | 码=%s"
          % (a.dry_run, a.port, a.perf, a.code or "(扫码)"))
    print("路线: %s" % ctx.route)
    print("=" * 66)

    ctx.lower = build_lower(a)
    if not sync_or_die(ctx, a):
        return 2
    ctx.vision, ctx.color = build_vision(a)
    from aim import Aim
    ctx.aim = Aim(ctx.vision, ctx.lower, log=ctx.log, dry_run=a.dry_run)

    try:
        run_match(ctx, a.start)
    except KeyboardInterrupt:
        ctx.log("!! 人工中断 → 停车")
        ctx.lower.stop()
        return 3
    finally:
        try:
            ctx.vision.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
