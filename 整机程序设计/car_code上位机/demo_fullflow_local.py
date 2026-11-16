#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo_fullflow_local.py —— 全流程纯逻辑端到端演示(无硬件, 本机可跑)
====================================================================
串起已交付地基: 任务码解析 → 任务编排 → 编排引擎(假串口/假视觉) → 统计 → 显示行。
真机场景下把 FakeExecutor 换成"真串口 SerialLink+cmd_pack+视觉握手"即可。

用法: python demo_fullflow_local.py [任务码]
      默认任务码 156+123+516+231; 可传 2 组旧格式演示报错。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from task_code_v2 import parse_task_code, describe, TaskCodeError  # noqa: E402
from task_stats import RunStats                                   # noqa: E402
from flow_runner import FlowRunner, Clock, build_mission          # noqa: E402


class FakeExecutor:
    """演示用假执行器: 打日志模拟每步动作; fail_ids 可注入失败。"""

    def __init__(self, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.trace = []

    def perform(self, task):
        if task.id in self.fail_ids:
            self.trace.append("[FAIL] %s" % task.desc)
            return False
        self.trace.append("[ ok ] %s" % task.desc)
        return True


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else "156+123+516+231"
    try:
        plan = parse_task_code(raw)
    except TaskCodeError as e:
        print("任务码解析失败(演示):", e)
        return 2

    print(describe(raw))
    print("-" * 62)

    stats = RunStats(plan["raw"])
    tasks = build_mission(plan)                       # 编排: 19 步
    ex = FakeExecutor(fail_ids=set(sys.argv[2:]) if len(sys.argv) > 2 else set())
    cfg = {"total_budget_s": 180, "idle_max_s": 15, "task_timeout_s": 10.0,
           "task_retries": 1}
    rep = FlowRunner(ex, stats, cfg, Clock()).run(tasks)

    print("-" * 62)
    print("执行轨迹(%d 步):" % len(ex.trace))
    for t in ex.trace:
        print("  " + t)
    print("-" * 62)
    su = stats.summary()
    print("结果: outcome=%s  executed=%d ok=%d failed=%s" %
          (rep["outcome"], rep["executed"], rep["ok"], rep["failed"]))
    print("统计: 抓取 %d/%d 放置 %d/%d(分场合 %s) 环分=%d 完成=%s" %
          (su["pick_ok"], su["pick_total"], su["place_ok"], su["place_total"],
           su["place_by_site"], su["ring_score"], su["finished"]))
    print("显示装置行:")
    for ln in stats.display_lines():
        print("   " + ln)
    return 0 if rep["outcome"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
