#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_task_code_v2.py —— 官方任务码解析回归(纯 python, 无第三方依赖)
用法: python test_task_code_v2.py        → 期望 all OK
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from task_code_v2 import TaskCodeError, parse_task_code, describe, COLOR_NAMES  # noqa: E402


class TestParse(unittest.TestCase):
    def test_official_sample(self):
        p = parse_task_code("156+123+516+231")
        self.assertEqual(p["groups"], ["156", "123", "516", "231"])
        b1, b2 = p["batches"]
        # 第一批: 红→黑→浅蓝, 放 1/2/3 号环
        self.assertEqual(b1["colors"], ["红", "黑", "浅蓝"])
        self.assertEqual(b1["places"], [1, 2, 3])
        # 第二批: 黑→红→浅蓝, 放 2/3/1 号环
        self.assertEqual(b2["colors"], ["黑", "红", "浅蓝"])
        self.assertEqual(b2["places"], [2, 3, 1])

    def test_color_legend_complete(self):
        # 官方 6 色编号必须完整且与《研读笔记》一致: 红1黄2蓝3绿4黑5浅蓝6
        self.assertEqual(COLOR_NAMES,
                         {"1": "红", "2": "黄", "3": "蓝",
                          "4": "绿", "5": "黑", "6": "浅蓝"})

    def test_whitespace_tolerance(self):
        p = parse_task_code(" 156+123+516+231 ")
        self.assertEqual(p["raw"], "156+123+516+231")

    # ---------- 非法输入 ----------
    def test_reject_old_two_group_format(self):
        # 旧上位机只认 XXX+YYY —— 新版必须明确拒绝并给出原因
        with self.assertRaises(TaskCodeError) as cm:
            parse_task_code("156+231")
        self.assertIn("4 组", str(cm.exception))

    def test_reject_non_digit_group(self):
        for bad in ("1a6+123+516+231", "156+12+516+231", "156+123+516+23",
                    "156+123+516+231x", ""):
            with self.assertRaises(TaskCodeError):
                parse_task_code(bad)

    def test_reject_color_out_of_range(self):
        # 颜色只能是 1~6(旧代码只到 3, 4/5/6 会 IndexError → 必须能解析)
        with self.assertRaises(TaskCodeError) as cm:
            parse_task_code("167+123+516+231")
        self.assertIn("非法编号", str(cm.exception))

    def test_reject_dup_color_in_group(self):
        # 同批三种颜色互不相同(旧代码不查, 会重复搬运)
        with self.assertRaises(TaskCodeError) as cm:
            parse_task_code("115+123+516+231")
        self.assertIn("重复", str(cm.exception))

    def test_place_range_ok(self):
        # 位置环号 1..6 都允许, 例如暂存区用满 6 环的边界输入
        p = parse_task_code("123+654+456+231")
        self.assertEqual(p["batches"][0]["places"], [6, 5, 4])

    def test_describe_readable(self):
        s = describe("156+123+516+231")
        self.assertIn("红", s)
        self.assertIn("1号环", s)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestParse)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    ok = result.wasSuccessful()
    print("ALL TESTS PASSED" if ok else "TESTS FAILED")
    sys.exit(0 if ok else 1)
