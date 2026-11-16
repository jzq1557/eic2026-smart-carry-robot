# -*- coding: utf-8 -*-
"""
ledger.py —— 车载物料账本（物料编号 ↔ 物架 slot）
====================================================
校赛最容易出错的地方：**取料顺序 ≠ 放置顺序**。
取料时把第 i 个取的物料放进物架 slot i；放置时按"要放的物料编号"反查它在哪个 slot。

    ledger.record("3", 1)     # 第一个取的是 3 号料 → 放 slot 1
    ledger.slot_of("3")       # 放置阶段的 3 号料 → 1
    ledger.consume("3")       # 放完标记，防止重复放
"""


class Ledger:
    def __init__(self):
        self.map = {}          # 物料编号 -> slot
        self.done = []         # 已放置的物料编号
        self.history = []      # 事件流水（打印/复盘用）

    # ---- 取料阶段 ----
    def next_free_slot(self):
        used = set(self.map.values())
        for s in (1, 2, 3):
            if s not in used:
                return s
        return None

    def record(self, mid, slot=None):
        mid = str(mid)
        slot = int(slot) if slot else self.next_free_slot()
        if slot is None:
            raise RuntimeError("物架已满，无法再记录物料 %s" % mid)
        if mid in self.map:
            raise RuntimeError("物料 %s 重复上车（账本=%s）" % (mid, self.map))
        self.map[mid] = slot
        self.history.append(("pick", mid, slot))
        return slot

    def slot_of(self, mid):
        s = self.map.get(str(mid))
        if s is None:
            raise RuntimeError("账本里没有物料 %s（已上车：%s）" % (mid, self.map))
        return s

    def on_board(self):
        return [m for m in self.map if m not in self.done]

    # ---- 放置阶段 ----
    def consume(self, mid):
        mid = str(mid)
        if mid in self.done:
            raise RuntimeError("物料 %s 已经放过了" % mid)
        self.slot_of(mid)
        self.done.append(mid)
        self.history.append(("place", mid, self.map[mid]))

    def summary(self):
        rows = []
        for mid, slot in sorted(self.map.items()):
            rows.append("%s号(slot%d)%s" % (mid, slot, "已放" if mid in self.done else "在车"))
        return " / ".join(rows) if rows else "(空车)"
