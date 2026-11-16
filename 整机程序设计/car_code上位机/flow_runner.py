#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flow_runner.py —— 全流程搬运上位机 v2: 编排骨架(纯 Python, 无第三方依赖)
====================================================================
设计见 《整机程序设计/全流程上位机v2设计.md》。本模块只做"流程编排/看门狗/
重试/日志/统计联动", 不包含任何真机运动——运动通过注入的 Executor 完成
(树上联调时用真实串口/视觉适配; 本机回归用 FakeExecutor)。

核心概念:
  - Task: 命名高层动作 {id, desc, kind: qr|fetch|place|transfer|stack|nav,
          batch, slot, ring, timeout_s, retries, fail: abort|skip, requires}
  - Executor.perform(task) -> bool   (真机适配器; 失败返回 False)
  - 看门狗(规则): 总时长预算 total_budget_s(默认 180s=3 分钟);
                 静止判负 idle_max_s(默认 15s; 等转盘场景调用 mark_busy 豁免)
  - Clock: 注入时钟(time.monotonic 语义), 测试可手拨
用法:
  runner = FlowRunner(executor, stats, cfg, clock)
  report = runner.run(build_mission(plan))
本地回归: python test_flow_runner.py
"""
from __future__ import annotations

import time as _time
from typing import Callable, Dict, List, Optional

__all__ = [
    "FlowRunner", "build_mission", "Task", "Clock",
    "FlowAborted", "BUDGET_DEFAULT", "IDLE_MAX_DEFAULT",
]

# 规则默认(现场按实际节奏覆盖)
BUDGET_DEFAULT = 180.0     # 总时长预算 3 分钟
IDLE_MAX_DEFAULT = 15.0    # 静止 >15s 判负(等转盘可 +8s)


class FlowAborted(Exception):
    """流程因看门狗/失败策略中止。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Clock:
    """可注入时钟: 生产用 time.monotonic, 测试用手拨。"""

    def __init__(self, now_fn: Optional[Callable[[], float]] = None,
                 sleep_fn: Optional[Callable[[float], None]] = None):
        self._now = now_fn or _time.monotonic
        self._sleep = sleep_fn or _time.sleep

    def now(self) -> float:
        return self._now()

    def sleep(self, s: float) -> None:
        if s > 0:
            self._sleep(s)


class Task:
    def __init__(self, tid: str, kind: str, desc: str = "",
                 batch: Optional[int] = None, slot: Optional[int] = None,
                 ring: Optional[int] = None,
                 timeout_s: float = 15.0, retries: int = 1,
                 fail: str = "abort", requires: Optional[List[str]] = None):
        self.id = tid
        self.kind = kind            # qr|fetch|place|transfer|stack|nav|other
        self.desc = desc or tid
        self.batch = batch
        self.slot = slot
        self.ring = ring            # 放置目标环号(place/transfer/stack)
        self.timeout_s = timeout_s
        self.retries = retries      # 除首次外的额外重试次数
        self.fail = fail            # abort | skip
        self.requires = list(requires or [])

    def __repr__(self) -> str:      # noqa: D105
        return "<Task %s %s>" % (self.id, self.kind)


def build_mission(plan: dict, cfg: Optional[dict] = None) -> List[Task]:
    """由 parse_task_code 的 plan 生成标准任务序列(读码→两批)。

    第一批: fetch×3 → place(粗加工, 环=组2) ×3 → transfer(暂存, 环=组2) ×3
    第二批: fetch×3 → place(粗加工, 环=组4) ×3 → stack(暂存, 环=组4, 码垛) ×3
    cfg 可覆盖: {"task_timeout_s":.., "task_retries":.., "read_qr": False}
    """
    cfg = cfg or {}
    batches = plan["batches"]
    to = float(cfg.get("task_timeout_s", 15.0))
    rt = int(cfg.get("task_retries", 1))
    tasks: List[Task] = []
    if cfg.get("read_qr", True):
        tasks.append(Task("qr_read", "qr", "读任务码二维码", timeout_s=to, retries=rt))
    for bi, b in enumerate(batches):
        pre = tasks[-1].id if tasks else ""
        for slot, col in enumerate(b["colors"]):
            tasks.append(Task(
                "b%d_fetch_%d" % (bi, slot), "fetch",
                "第%d批 抓取 %s(%s)" % (bi + 1, col, b["seq"][slot]),
                batch=bi, slot=slot, timeout_s=to, retries=rt,
                requires=[pre] if pre else []))
            pre = tasks[-1].id
        # 放置: 第1批→粗加工区; 第2批→粗加工区; 环号 = 该批位置码第 slot 位
        for slot, ring in enumerate(b["places"]):
            tasks.append(Task(
                "b%d_place_%d" % (bi, slot), "place",
                "第%d批 放粗加工 %d号环" % (bi + 1, ring),
                batch=bi, slot=slot, ring=ring, timeout_s=to, retries=rt,
                requires=[pre] if pre else []))
            pre = tasks[-1].id
        # 转场/码垛
        kind2 = "transfer" if bi == 0 else "stack"
        for slot, ring in enumerate(b["places"]):
            tasks.append(Task(
                "b%d_%s_%d" % (bi, kind2, slot), kind2,
                ("第%d批 暂存平面放 %d号环" if bi == 0
                 else "第%d批 暂存码垛 %d号环") % (bi + 1, ring),
                batch=bi, slot=slot, ring=ring, timeout_s=to, retries=rt,
                requires=[pre] if pre else []))
            pre = tasks[-1].id
    return tasks


class FlowRunner:
    def __init__(self, executor, stats, cfg: Optional[dict] = None,
                 clock: Optional[Clock] = None):
        """
        executor: 需实现 perform(task)->bool
        stats   : task_stats.RunStats 或同接口对象(可为 None 跳过统计)
        cfg     : {total_budget_s, idle_max_s, task_timeout_s, task_retries}
        """
        cfg = cfg or {}
        self.exec = executor
        self.stats = stats
        self.total_budget = float(cfg.get("total_budget_s", BUDGET_DEFAULT))
        self.idle_max = float(cfg.get("idle_max_s", IDLE_MAX_DEFAULT))
        self.clock = clock or Clock()
        self.log: List[Dict] = []

    # ---------- 日志/时钟 ----------
    def _log(self, level: str, msg: str) -> None:
        self.log.append({"t": round(self.clock.now(), 2), "level": level, "msg": msg})

    def _check_budget(self) -> None:
        if self.clock.now() - self.t0 > self.total_budget:
            self._log("FATAL", "总时长预算超限(%.0fs)" % self.total_budget)
            raise FlowAborted("total_budget_exceeded")

    # ---------- 主流程 ----------
    def run(self, tasks: List[Task]) -> dict:
        self.t0 = self.clock.now()
        self.last_progress = self.t0
        done: Dict[str, bool] = {}
        executed, ok_count, failed = 0, 0, []
        outcome = "completed"

        for task in tasks:
            self._check_budget()
            # 前置依赖检查
            unmet = [r for r in task.requires
                     if r not in done or done[r] is not True]
            if unmet:
                self._log("skip", "%s 前置未满足 %s" % (task.id, unmet))
                failed.append(task.id)
                done[task.id] = False
                continue

            ok = False
            tries = 0
            while tries <= task.retries:
                self._check_budget()
                self._log("run", "%s (%s) try%d" % (task.id, task.kind, tries))
                try:
                    ok = bool(self.exec.perform(task))
                except FlowAborted:
                    raise
                except Exception as e:              # Executor 内部异常按失败处理
                    self._log("warn", "%s 异常: %r" % (task.id, e))
                    ok = False
                executed += 1
                if ok:
                    self.last_progress = self.clock.now()
                    break
                # 静止看门狗: 重试间也无进展超时
                if self.clock.now() - self.last_progress > self.idle_max:
                    self._log("FATAL", "%s 静止超时(>%.0fs)判负" % (task.id, self.idle_max))
                    raise FlowAborted("idle_timeout@%s" % task.id)
                tries += 1
                if tries <= task.retries:
                    self.clock.sleep(0.05)

            done[task.id] = ok
            self._record(task, ok)
            if not ok:
                failed.append(task.id)
                self._log("warn", "%s 重试%d次后仍失败" % (task.id, task.retries))
                if task.fail == "abort":
                    outcome = "aborted"
                    self._log("FATAL", "%s 失败策略=中止" % task.id)
                    break
            else:
                ok_count += 1

        return {
            "outcome": outcome,
            "executed": executed,
            "ok": ok_count,
            "failed": failed,
            "stats": (self.stats.summary() if self.stats is not None else None),
            "log": self.log,
        }

    # ---------- 统计联动 ----------
    SITE_OF = {"place": "coarse", "transfer": "buffer", "stack": "stack"}

    def _record(self, task: Task, ok: bool) -> None:
        if self.stats is None:
            return
        if task.batch is None or task.slot is None:
            return
        try:
            if task.kind == "fetch":
                self.stats.record_pick(task.batch, task.slot, ok)
            elif task.kind in self.SITE_OF:
                site = self.SITE_OF[task.kind]
                self.stats.record_place(task.batch, task.slot,
                                        ring=task.ring if ok else None,
                                        ok=ok, site=site)
        except Exception as e:
            self._log("warn", "统计入账失败: %r" % e)
