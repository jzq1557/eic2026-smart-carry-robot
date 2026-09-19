#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_task_stats.py —— 统计模块回归(纯 python)
用法: python test_task_stats.py   → 期望 all OK
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from task_stats import RunStats, RING_SCORE  # noqa: E402


class TestStats(unittest.TestCase):
    def test_full_run(self):
        s = RunStats("156+123+516+231")
        # 第 1 批: 全抓成功, 放 1/2/3 号环
        for slot, ring in enumerate((1, 2, 3), 0):
            self.assertTrue(s.record_pick(0, slot, True, ring=ring))
            self.assertTrue(s.record_place(0, slot, ring=ring))
        # 第 2 批: 第 2 个抓取失败
        self.assertTrue(s.record_pick(1, 0, True, ring=2))
        self.assertTrue(s.record_pick(1, 1, False, ring=3))   # 失败也入账(计入 total)
        self.assertTrue(s.record_pick(1, 2, True, ring=1))
        self.assertTrue(s.record_place(1, 0, ring=2))
        self.assertTrue(s.record_place(1, 1, ring=3))   # 抓到 False -> 自动判定放置失败
        self.assertTrue(s.record_place(1, 2, ring=1))

        su = s.summary()
        self.assertEqual(su["pick_ok"], 5)
        self.assertEqual(su["pick_total"], 6)
        self.assertEqual(su["place_ok"], 5)
        self.assertEqual(su["place_total"], 6)
        # 环分 = (15+10+7) + (10+15) = 57 (第2批第2个放置失败 → 不计分)
        self.assertEqual(s.place_scores(), [15, 10, 7, 10, 15])
        self.assertEqual(su["ring_score"], 57)
        # 有失败 → 不算"完成"(须全部抓到且放置达标)
        self.assertFalse(su["finished"])
        self.assertIn("Done N", s.display_lines()[-1])

    def test_dup_event_ignored(self):
        s = RunStats()
        self.assertTrue(s.record_pick(0, 0, True))
        self.assertFalse(s.record_pick(0, 0, True))     # 重复记账被拒
        self.assertTrue(s.record_place(0, 0, ring=1))
        self.assertFalse(s.record_place(0, 0, ring=2))  # 已放不可改
        self.assertEqual(s.pick_total, 1)
        self.assertEqual(s.place_total, 1)

    def test_ring_scores_table(self):
        self.assertEqual(RING_SCORE, {1: 15, 2: 10, 3: 7, 4: 5, 5: 3, 6: 1})

    def test_auto_ok_requires_pick(self):
        s = RunStats()
        # 没抓到就放(自动判定) → 失败入账
        self.assertTrue(s.record_place(0, 0, ring=1))
        self.assertEqual(s.place_ok, 0)
        self.assertIn("Grab", s.display_lines()[1])
        self.assertTrue(s.display_lines()[0].startswith("TK "))

    def test_multi_site_per_material(self):
        # 同一物料可在 coarse→buffer 各放一次, 且各自计分入账
        s = RunStats("156+123+516+231")
        self.assertTrue(s.record_pick(0, 0, True, ring=1))
        self.assertTrue(s.record_place(0, 0, ring=1, site="coarse"))
        self.assertTrue(s.record_place(0, 0, ring=1, site="buffer"))  # 允许第二次
        self.assertFalse(s.record_place(0, 0, ring=1, site="buffer"))  # 防重
        self.assertEqual(s.place_total, 2)
        self.assertEqual(s.place_ok, 2)
        self.assertEqual(s.score(), 30)      # 15 + 15
        self.assertEqual(s.summary()["place_by_site"]["buffer"], 1)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestStats)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    ok = result.wasSuccessful()
    print("ALL TESTS PASSED" if ok else "TESTS FAILED")
    sys.exit(0 if ok else 1)
