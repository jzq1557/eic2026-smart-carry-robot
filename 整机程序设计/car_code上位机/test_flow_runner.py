#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_flow_runner.py —— FlowRunner 编排骨架回归(纯 python, FakeExecutor+假时钟)
用法: python test_flow_runner.py   → 期望 all OK
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from task_code_v2 import parse_task_code        # noqa: E402
from task_stats import RunStats                  # noqa: E402
from flow_runner import (FlowRunner, Clock, Task, FlowAborted,  # noqa: E402
                         build_mission)


class FakeExecutor:
    """按 id 表返回结果; 可记录调用序列。"""

    def __init__(self, ok_map=None, fail_ids=()):
        self.ok_map = ok_map or {}
        self.fail_ids = set(fail_ids)
        self.calls = []

    def perform(self, task: Task) -> bool:
        self.calls.append(task.id)
        if task.id in self.fail_ids:
            return False
        return self.ok_map.get(task.id, True)


class FakeClock(Clock):
    def __init__(self):
        super().__init__()
        self.t = 100.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


PLAN = parse_task_code("156+123+516+231")


class TestMission(unittest.TestCase):
    def test_mission_shape(self):
        tasks = build_mission(PLAN)
        # qr 1 + 每批(抓3+粗放3+转场/码垛3) = 1 + 6 + 6 = 19
        self.assertEqual(len(tasks), 19)
        kinds = [t.kind for t in tasks]
        self.assertEqual(kinds[0], "qr")
        self.assertEqual(kinds.count("fetch"), 6)
        self.assertEqual(kinds.count("place"), 6)
        self.assertEqual(kinds.count("transfer"), 3)
        self.assertEqual(kinds.count("stack"), 3)
        # 第1批放 1/2/3 号环; 第2批放 2/3/1 号环(环号取自位置组)
        place_rings = [t.ring for t in tasks if t.kind == "place"]
        self.assertEqual(place_rings, [1, 2, 3, 2, 3, 1])


class TestRun(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.stats = RunStats(PLAN["raw"])

    def test_happy_path(self):
        ex = FakeExecutor()
        cfg = {"total_budget_s": 600, "idle_max_s": 100}
        rep = FlowRunner(ex, self.stats, cfg, self.clock).run(build_mission(PLAN))
        self.assertEqual(rep["outcome"], "completed")
        self.assertEqual(rep["ok"], 19)
        self.assertEqual(self.stats.pick_ok, 6)
        # 放置事件 = 粗放6 + 转场3 + 码垛3 = 12, 全部成功
        self.assertEqual(self.stats.place_ok, 12)
        self.assertEqual(self.stats.place_total, 12)
        self.assertTrue(self.stats.finished())
        self.assertEqual(len(ex.calls), 19)           # 无跳过/重试

    def test_retry_then_success(self):
        # 第1个抓取前两次失败, 第3次成功(retries=2)
        ex = FakeExecutor(fail_ids={"b0_fetch_0"})
        ex.n = 0

        def flaky(task):
            if task.id == "b0_fetch_0":
                ex.n += 1
                return ex.n >= 3
            return True

        ex.perform = flaky
        cfg = {"total_budget_s": 600, "idle_max_s": 100, "task_retries": 2}
        rep = FlowRunner(ex, self.stats, cfg, self.clock).run(build_mission(PLAN, cfg))
        self.assertEqual(rep["outcome"], "completed")
        self.assertEqual(self.stats.pick_ok, 6)
        self.assertEqual(self.stats.place_ok, 12)

    def test_abort_after_retries(self):
        ex = FakeExecutor(fail_ids={"b0_fetch_1"})
        cfg = {"total_budget_s": 600, "idle_max_s": 100, "task_retries": 1}
        rep = FlowRunner(ex, self.stats, cfg, self.clock).run(build_mission(PLAN, cfg))
        self.assertEqual(rep["outcome"], "aborted")
        self.assertIn("b0_fetch_1", rep["failed"])
        # fetch 失败记录进统计(place_ok 保持 0)
        self.assertEqual(self.stats.pick_total, 2)    # 0/1 两槽各一次(0成功1失败)
        self.assertEqual(self.stats.place_total, 0)

    def test_total_budget_watchdog(self):
        ex = FakeExecutor()
        cfg = {"total_budget_s": 10, "idle_max_s": 100}
        # 每次 perform 拨快 5s → 第3个任务前超限
        def slow(task):
            self.clock.t += 5
            return True

        ex.perform = slow
        with self.assertRaises(FlowAborted) as cm:
            FlowRunner(ex, None, cfg, self.clock).run(build_mission(PLAN, cfg))
        self.assertIn("budget", str(cm.exception))

    def test_idle_watchdog(self):
        ex = FakeExecutor(fail_ids={"qr_read"})       # 读码一直失败
        cfg = {"total_budget_s": 600, "idle_max_s": 15, "task_retries": 9}

        class Ticker(FakeClock):
            def sleep(self, s):
                self.t += 5                            # 每次重试间隔5s, 无进展

        self.clock = Ticker()
        with self.assertRaises(FlowAborted) as cm:
            FlowRunner(ex, None, cfg, self.clock).run(build_mission(PLAN, cfg))
        self.assertIn("idle", str(cm.exception))

    def test_prereq_skip(self):
        # 人为要求一个不存在的 id → 该任务跳过(不计执行), 流程其余继续
        tasks = build_mission(PLAN, {"read_qr": False})
        tasks.insert(1, Task("never_done", "other", requires=["no_such_task"]))
        ex = FakeExecutor()
        cfg = {"total_budget_s": 600, "idle_max_s": 100}
        rep = FlowRunner(ex, self.stats, cfg, self.clock).run(tasks)
        self.assertEqual(rep["outcome"], "completed")
        self.assertIn("never_done", rep["failed"])
        self.assertEqual(rep["executed"], 18)         # 19 个任务, 跳过 1 个


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestMission)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestRun))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    ok = result.wasSuccessful()
    print("ALL TESTS PASSED" if ok else "TESTS FAILED")
    sys.exit(0 if ok else 1)
