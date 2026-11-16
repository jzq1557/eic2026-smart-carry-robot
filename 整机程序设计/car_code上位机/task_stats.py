#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task_stats.py —— 全场运行统计(规则要求: 显示任务码+抓取/放置正确次数)
====================================================================
纯 Python、无第三方依赖, 供"新版全流程上位机"与任务码显示装置共用。

规则对应(附件2-2): 显示装置需显示 所有任务码 + 抓取正确次数 + 放置正确次数;
"每正确抓取 1 个物料(抓到并放到机器人上) 2 分; 放置按环 1~6 计分"。

同一物料会多次放置(先粗加工区, 再暂存区平面/码垛), 故放置事件按
(batch, slot, site) 记账; site: coarse(粗加工) | buffer(暂存平面) | stack(码垛)。

用法:
  s = RunStats("156+123+516+231")
  s.record_pick(batch=0, slot=0, ok=True)             # 抓取结果
  s.record_place(batch=0, slot=0, ring=1, site="coarse")
  s.record_place(batch=0, slot=0, ring=1, site="buffer")
  s.summary() / s.display_lines()
运行回归: python test_task_stats.py
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

__all__ = ["RunStats", "RING_SCORE", "SITES"]


# 官方环分: 1环15 / 2环10 / 3环7 / 4环5 / 5环3 / 6环1 (附件2-2)
RING_SCORE = {1: 15, 2: 10, 3: 7, 4: 5, 5: 3, 6: 1}

# 放置场合(计分细节现场按规则复核; 结构上可扩展)
SITES = ("coarse", "buffer", "stack")


class RunStats:
    """单场运行的统计账本。batch: 0=第一批 1=第二批; slot: 0..2。"""

    def __init__(self, task_raw: Optional[str] = None, batch_sizes=(3, 3)):
        self.task_raw = task_raw or ""
        self.batch_sizes = list(batch_sizes)          # 每批物料数(规则=3)
        self.pick_ok = 0                              # 抓取正确次数(全局)
        self.place_ok = 0                             # 放置正确次数(全局)
        self.pick_total = 0
        self.place_total = 0
        # 抓取明细: batches[b][slot] = {"picked": None|bool, "ring": 计划环}
        self.batches: List[List[Dict]] = []
        # 放置明细: (batch, slot, site) -> {"ok": bool, "ring": int|None}
        self.place_events: Dict[Tuple[int, int, str], Dict] = {}
        for n in self.batch_sizes:
            self.batches.append(
                [{"picked": None, "ring": None} for _ in range(n)])

    # ---------- 事件 ----------
    def record_pick(self, batch: int, slot: int, ok: bool,
                    ring: Optional[int] = None) -> bool:
        """抓取事件: ok=是否抓到并放到机器人上(规则口径)。返回 True=成功入账。"""
        b = self._batch(batch)
        if not (0 <= slot < len(b)):
            return False
        if b[slot]["picked"] is not None:               # 防重复记账
            return False
        b[slot]["picked"] = bool(ok)
        self.pick_total += 1
        if ok:
            self.pick_ok += 1
        if ring is not None:
            b[slot]["ring"] = int(ring)
        return True

    def record_place(self, batch: int, slot: int,
                     ring: Optional[int] = None, ok: Optional[bool] = None,
                     site: str = "coarse") -> bool:
        """放置事件(同一物料可在多个 site 各放一次)。
        ok 为空时自动判定: 环号 1..6 且该物料已抓到。返回 True=成功入账。"""
        b = self._batch(batch)
        if not (0 <= slot < len(b)):
            return False
        if site not in SITES:
            return False
        key = (batch, slot, site)
        if key in self.place_events:                    # 防重复记账
            return False
        if ok is None:
            ok = (ring is not None and 1 <= int(ring) <= 6
                  and b[slot]["picked"] is True)
        self.place_events[key] = {"ok": bool(ok),
                                  "ring": int(ring) if ring is not None else None}
        self.place_total += 1
        if ok:
            self.place_ok += 1
        if ring is not None:
            b[slot]["ring"] = int(ring)
        return True

    # ---------- 查询 ----------
    def _batch(self, batch: int) -> List[Dict]:
        if not (0 <= batch < len(self.batches)):
            raise IndexError("batch 越界: %r (共 %d 批)" % (batch, len(self.batches)))
        return self.batches[batch]

    def place_scores(self) -> List[int]:
        """已确认(成功)放置事件按环查分; 无效环记 0。"""
        out = []
        for ev in self.place_events.values():
            if ev["ok"] and ev["ring"]:
                out.append(RING_SCORE.get(int(ev["ring"]), 0))
        return out

    def score(self) -> int:
        """成功放置事件的环分合计(计分细节以现场规则复核为准)。"""
        return sum(self.place_scores())

    def finished(self) -> bool:
        """严格完成: 本批物料全部抓到 且 放置达标(≥每批件数)才算完成;
        中途有失败(如某料没抓到/没放成)应显示未完成。"""
        need = sum(self.batch_sizes)
        return self.pick_ok >= need and self.place_ok >= need

    def summary(self) -> dict:
        return {
            "task_raw": self.task_raw,
            "pick_ok": self.pick_ok, "pick_total": self.pick_total,
            "place_ok": self.place_ok, "place_total": self.place_total,
            "place_by_site": {s: sum(1 for k, v in self.place_events.items()
                                     if k[2] == s and v["ok"]) for s in SITES},
            "ring_score": self.score(),
            "finished": self.finished(),
        }

    def display_lines(self) -> List[str]:
        """给显示装置的行(每行短)。外部可再按装置拼接/分页。"""
        lines = [
            "TK " + (self.task_raw[:20] if self.task_raw else "-"),
            "Grab %d/%d  Place %d/%d" % (
                self.pick_ok, self.pick_total, self.place_ok, self.place_total),
            "RingPts %d  Done %s" % (self.score(), "Y" if self.finished() else "N"),
        ]
        return lines
