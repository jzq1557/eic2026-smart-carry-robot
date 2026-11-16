#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_school_local.py —— 校赛主程序纯逻辑回归（本机可跑，不需要相机/串口/cv2）
================================================================================
覆盖：
  1. 新协议帧打包（0x20/0x21/0x22/0x23/0x24 + TTL/移动/夹爪）
  2. 任务码解析 / 放置映射 / 颜色映射
  3. 车载账本（含重复上车、重复放置等异常）
  4. 瞄准收敛（仿真视觉里 PID 真的收敛，且脉冲方向正确）
  5. **整场 dry-run**：6 次瞄准（G1~G3 + P1~P3）都跑到、账本正确、动作组被调用

跑法： D:\\programs\\anaconda\\python.exe test_school_local.py
       (RDK 上: python3 test_school_local.py —— 有 cv2 也照样能跑)
"""
import io
import os
import sys
import types
import contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import school_config as SC                      # noqa: E402
import lower_api as LA                          # noqa: E402
from ledger import Ledger                       # noqa: E402
from aim import Aim                             # noqa: E402
from vision import MockVision                   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


def silent(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


# ----------------------------------------------------------------------
def t1_frames():
    print("[1] 协议帧打包")
    f = LA.pack_set_param(0x0001, 8)
    check("0x20 帧长 10 + 头尾", len(f) == 10 and f[0] == 0xFF and f[-1] == 0xFE, f.hex())
    check("0x20 cmd/id/value 大端", f[1] == 0x20 and f[2:4] == b"\x00\x01" and f[4:8] == b"\x00\x00\x00\x08")
    f = LA.pack_get_param(0x0010)
    check("0x22 读参数", f[1] == 0x22 and f[2:4] == b"\x00\x10")
    check("0x23 存参数", LA.pack_save_params()[1] == 0x23)
    f = LA.pack_step_head(2, 3, LA.STEP_KIND["axis"], 4, 0, 40)
    check("0x21 建步头", f[1] == 0x21 and f[2] == 1 and f[3] == 2 and f[4] == 3 and f[5] == 1 and f[6] == 4)
    f = LA.pack_step_pulses(2, 3, 6400)
    check("0x21 补脉冲(大端 6400)", f[5:9] == b"\x00\x00\x19\x00", f.hex())
    check("0x21 提交", LA.pack_step_commit(2, 4)[2] == 3)
    f = LA.pack_motion_run(5, 2)
    check("0x24 执行组+slot", f[1] == 0x24 and f[2] == 5 and f[3] == 2)
    f = LA.pack_ttl(4, 0, 40, 6400)                       # dir=0 正转
    check("0x12 base 轴帧(dir/vel/脉冲 大端)",
          f[1] == 0x12 and f[2] == 0 and f[3:5] == b"\x00\x28" and f[5:9] == b"\x00\x00\x19\x00",
          f.hex())
    check("0x10 伸缩轴", LA.pack_ttl(1, 0, 40, 3000)[1] == 0x10)
    check("0x11 龙门轴", LA.pack_ttl(2, 0, 40, 3000)[1] == 0x11)
    try:
        LA.pack_ttl(9, 0, 1, 1)
        check("轴号非法要报错", False)
    except LA.PackError:
        check("轴号非法要报错", True)
    try:
        LA.pack_move_x_cm(200)
        check("int8 越界要报错", False)
    except LA.PackError:
        check("int8 越界要报错", True)
    check("前进方向符号", LA.pack_move_x_cm(20)[2] == 20 and LA.pack_move_x_cm(-20)[2] == 0xEC)


def t2_code():
    print("[2] 任务码与映射")
    check("解析 123+321", SC.parse_code("123+321") == ("123", "321"))
    check("非法码返回空", SC.parse_code("12+321") == (None, None))
    check("颜色映射 1/2/3", (SC.color_name("1"), SC.color_name("2"), SC.color_name("3")) == ("red", "green", "blue"))
    check("放置映射 3→1,2→2,1→3", SC.PLACEMENT_MAP == {"3": 1, "2": 2, "1": 3})
    plan = SC.place_plan("321")
    check("放置计划(321)", plan == [("3", 1), ("2", 2), ("1", 3)], plan)
    plan = SC.place_plan("123")
    check("放置计划(123) 会走 3→2→1 位置", plan == [("1", 3), ("2", 2), ("3", 1)], plan)


def t3_ledger():
    print("[3] 车载账本")
    lg = Ledger()
    check("空车", lg.summary() == "(空车)")
    check("第一个取 3 号 → slot1", lg.record("3") == 1)
    check("第二个取 1 号 → slot2", lg.record("1") == 2)
    check("第三个取 2 号 → slot3", lg.record("2") == 3)
    check("反查 1 号在 slot2", lg.slot_of("1") == 2)
    check("在车清单", sorted(lg.on_board()) == ["1", "2", "3"])
    lg.consume("3")
    check("放完 3 号", lg.done == ["3"] and lg.on_board() == ["1", "2"])
    try:
        lg.consume("3")
        check("重复放置要报错", False)
    except RuntimeError:
        check("重复放置要报错", True)
    try:
        lg.record("3")
        check("重复上车要报错", False)
    except RuntimeError:
        check("重复上车要报错", True)


def t4_aim_converge():
    print("[4] 瞄准收敛（仿真闭环）")
    v = MockVision(cx=320.0, cy=210.0, w=512, h=384)     # 中心 =(256,192) → dx=+64, dy=+18
    l = LA.MockLower(verbose=False)
    a = Aim(v, l, log=lambda *x: None, dry_run=True)
    ok, off = a.converge(tag="T")
    check("收敛成功", ok, "off=%s" % off)
    check("x 方向走的是 base 反转（dx>0）", off["base"] < 0, off)
    check("y 方向走的是 arm 反转（dy>0）", off["arm"] < 0, off)
    check("收敛后目标贴近中心", abs(v.cx - 256) <= SC.AIM_TOL_PX + 2 and abs(v.cy - 192) <= SC.AIM_TOL_PX + 2,
          "c=(%.1f,%.1f)" % (v.cx, v.cy))
    # 回放：把偏移反向加回，位置应该大致回到起点
    cx0, cy0 = v.cx, v.cy
    a.replay(off, sign=+1)
    check("回放后位置发生变化", abs(v.cx - cx0) > 1 or abs(v.cy - cy0) > 1)


def t5_full_flow():
    print("[5] 整场 dry-run（6 次瞄准）")
    import school_flow as SF
    args = types.SimpleNamespace(dry_run=True, port="x", baud=115200, code="123+321",
                                start="M1", perf="rdk", force=False)
    ctx = SF.Ctx(args)
    ctx.lower = LA.MockLower(verbose=False)
    ctx.vision = MockVision(cx=330.0, cy=200.0, w=512, h=384)
    ctx.aim = Aim(ctx.vision, ctx.lower, log=lambda *x: None, dry_run=True)
    ok, out = silent(SF.run_match, ctx, "M1")
    check("流程跑完无异常", ok is ctx)
    check("任务码解析", (ctx.pick_order, ctx.place_order) == ("123", "321"))
    check("三个物料全部上车 + slot 正确",
          ctx.ledger.map == {"1": 1, "2": 2, "3": 3}, ctx.ledger.map)
    check("三个物料全部放置", sorted(ctx.ledger.done) == ["1", "2", "3"], ctx.ledger.done)
    check("放置顺序按后段(321)", ctx.ledger.done == ["3", "2", "1"], ctx.ledger.done)

    calls = ctx.lower.calls
    n_grab = sum(1 for c in calls if c[0] == "run_group" and c[1]["name"] == "BUILTIN_GRAB_VEH")
    n_place = sum(1 for c in calls if c[0] == "run_group" and c[1]["name"] == "BUILTIN_PLACE")
    slots = [c[1]["slot"] for c in calls
             if c[0] == "run_group" and c[1]["name"] == "BUILTIN_GRAB_VEH"]
    nttl = sum(1 for c in calls if c[0] == "ttl")
    check("调用了 3 次去物架夹料(0x16)", n_grab == 3, n_grab)
    check("调用了 3 次放料(0x13)", n_place == 3, n_place)
    # 码 123+321：取料顺序 123 → 物料 1/2/3 分别进 slot 1/2/3；
    # 放置顺序 321 → 先放 3 号料(在 slot3)、再 2 号(slot2)、最后 1 号(slot1)
    check("账本 -> 物架 slot 顺序正确(3,2,1)", slots == [3, 2, 1], slots)
    check("瞄准过程里下发了 TTL 脉冲", nttl > 12, "ttl=%d" % nttl)
    check("右移出启动区 + 结束右移平移都有",
          sum(1 for c in calls if c[0] == "move" and c[1]["dir"] == "right") >= 2)
    check("有一次原地自旋 90°",
          any(c[0] == "spin" and c[1]["deg"] == 90 for c in calls))
    check("base 左转 90° 组被调用 2 次(取料区+加工区)",
          sum(1 for c in calls if c[0] == "run_group" and c[1]["name"] == "BASE_TO_LEFT") >= 2)
    check("流程日志里有 6 次瞄准的痕迹",
          out.count("G1") and out.count("G2") and out.count("G3")
          and out.count("P1") and out.count("P2") and out.count("P3"))

    # 换一个任务码再跑一次，验证"放置顺序 ≠ 位置顺序"也能跑
    args2 = types.SimpleNamespace(dry_run=True, port="x", baud=115200, code="123+123",
                                 start="M1", perf="rdk", force=False)
    ctx2 = SF.Ctx(args2)
    ctx2.lower = LA.MockLower(verbose=False)
    ctx2.vision = MockVision(cx=330.0, cy=200.0, w=512, h=384)
    ctx2.aim = Aim(ctx2.vision, ctx2.lower, log=lambda *x: None, dry_run=True)
    silent(SF.run_match, ctx2, "M1")
    check("码 123+123 时放置顺序为 1→2→3", ctx2.ledger.done == ["1", "2", "3"], ctx2.ledger.done)


def t6_sync():
    print("[6] 参数/动作组下发链路")
    l = LA.MockLower(verbose=False)
    prof = SC.load_profile()
    ok, fail = l.sync_profile(prof)
    check("sync 成功", ok, fail)
    check("参数已进下位机参数表", l.params.get(LA.P["chassis_vel"]) == prof["params"]["chassis_vel"])
    check("动作组全部下发", len(l.groups) == len(prof["groups"]), len(l.groups))
    gid = l.gid["HOME"]
    check("HOME 组步骤数一致", len(l.groups[gid]) == len(prof["groups"]["HOME"]))
    check("步骤里带脉冲与速度",
          all("pulses" in s and "vel" in s for s in l.groups[gid]), l.groups[gid])


if __name__ == "__main__":
    print("=" * 66)
    print("校赛纯逻辑回归")
    print("=" * 66)
    t1_frames()
    t2_code()
    t3_ledger()
    t4_aim_converge()
    t5_full_flow()
    t6_sync()
    print("=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败清单：")
        for f in FAIL:
            print("  - %s" % f)
    print("=" * 66)
    sys.exit(1 if FAIL else 0)
