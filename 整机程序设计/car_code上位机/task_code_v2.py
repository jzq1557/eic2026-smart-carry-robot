#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task_code_v2.py —— 官方任务码(二维码)解析 v2
================================================
背景: 旧 my_move.py 的 get_taskinfo 只认 "XXX+YYY" 两组三位数(颜色仅红绿蓝、
编号与官方不一致),遇官方 15 字符四组任务码(如 156+123+516+231)会 int('+')
崩溃或全部放错。本模块按《附件2-1 命题与运行》重写语义层,纯 Python 无第三方依赖,
供"新版全流程搬运上位机"直接复用(替换 get_taskinfo)。

官方语义(附件2-1):
  任务码 = 四组三位数, 以 "+" 连接, 例 "156+123+516+231"
    第1组: 第一批 3 个物料的 颜色+搬运顺序 (第 k 位 = 第 k 个抓的颜色)
    第2组: 第一批在 粗加工区/暂存区 的放置位置 (第 k 位 = 第 k 个放几号环)
    第3组: 第二批 3 个物料颜色+顺序 (语义同第1组)
    第4组: 第二批在 粗加工区 的放置位置 (语义同第2组)
  颜色编号: 1红 2黄 3蓝 4绿 5黑 6浅蓝
  位置编号: 1..6 号圆环(样本用 1/2/3); 组内数字不重复(一料一环、同批异色)

用法:
  from task_code_v2 import parse_task_code, COLOR_NAMES, color_of, describe
  plan = parse_task_code("156+123+516+231")   # 失败抛 TaskCodeError(带原因)
  plan["batches"][0]  -> {"colors": ["红","黑","浅蓝"], "places": [1, 2, 3]}
  运行: python test_task_code_v2.py   # 期望 Ran N tests, all OK
"""
from __future__ import annotations

__all__ = [
    "TaskCodeError", "COLOR_NAMES", "color_of", "parse_task_code", "describe",
]

# 官方颜色编号表(唯一标准; 旧代码 [0,'red','green','blue'] 已被取代)
COLOR_NAMES = {
    "1": "红",
    "2": "黄",
    "3": "蓝",
    "4": "绿",
    "5": "黑",
    "6": "浅蓝",
}

PLACE_RANGE = (1, 6)          # 位置编号允许范围(圆环号 1~6)


class TaskCodeError(ValueError):
    """任务码非法时的异常, message 面向现场调试。"""


def color_of(digit: str) -> str:
    if digit not in COLOR_NAMES:
        raise TaskCodeError("颜色编号必须是 1~6(红1黄2蓝3绿4黑5浅蓝6), 收到 %r" % digit)
    return COLOR_NAMES[digit]


def _clean_groups(text: str) -> list:
    """去掉空白/首尾 + , 切出四组; 兼容多组余量只取前四? 不: 严格校验。"""
    if not text:
        raise TaskCodeError("任务码为空")
    s = str(text).strip()
    parts = s.split("+")
    if len(parts) != 4:
        raise TaskCodeError(
            "任务码应为 4 组三位数(如 156+123+516+231), 实际 %d 组: %r"
            "  (旧版只支持 2 组 'XXX+YYY', 会在此失败——请升级上位机解析)"
            % (len(parts), s))
    return parts


def _check_group(part: str, kind: str, digit_set, index: int) -> list:
    if len(part) != 3 or not part.isdigit():
        raise TaskCodeError("第 %d 组(%s)必须是 3 位数字, 收到 %r" % (index + 1, kind, part))
    digits = list(part)
    for d in digits:
        if d not in digit_set:
            raise TaskCodeError(
                "第 %d 组(%s)出现非法编号 %r: 允许 %s"
                % (index + 1, kind, d, "".join(sorted(digit_set))))
    if len(set(digits)) != 3:
        raise TaskCodeError(
            "第 %d 组(%s)%r 内数字重复——同批三种颜色/三个环位必须互不相同"
            % (index + 1, kind, part))
    return digits


def parse_task_code(text: str) -> dict:
    """解析官方任务码。

    返回:
      {
        "raw": "156+123+516+231",
        "groups": ["156", "123", "516", "231"],
        "batches": [
            {"colors": ["红","黑","浅蓝"], "places": [1, 2, 3], "seq": "156", "pos": "123"},
            {"colors": ["黑","红","浅蓝"], "places": [2, 3, 1], "seq": "516", "pos": "231"},
        ],
      }
    任何不符合规则处抛 TaskCodeError。
    """
    parts = _clean_groups(text)
    color_digits = {str(i) for i in range(1, 7)}
    place_digits = {str(i) for i in range(1, PLACE_RANGE[1] + 1)}   # 1..6

    g_color1 = _check_group(parts[0], "第一批颜色+顺序", color_digits, 0)
    g_place1 = _check_group(parts[1], "第一批放置位置", place_digits, 1)
    g_color2 = _check_group(parts[2], "第二批颜色+顺序", color_digits, 2)
    g_place2 = _check_group(parts[3], "第二批放置位置", place_digits, 3)

    return {
        "raw": str(text).strip(),
        "groups": parts,
        "batches": [
            {"seq": parts[0], "pos": parts[1],
             "colors": [color_of(d) for d in g_color1],
             "places": [int(d) for d in g_place1]},
            {"seq": parts[2], "pos": parts[3],
             "colors": [color_of(d) for d in g_color2],
             "places": [int(d) for d in g_place2]},
        ],
    }


def describe(text: str) -> str:
    """把任务码翻译成人话(现场确认/日志用)。"""
    p = parse_task_code(text)
    out = ["任务码 %s:" % p["raw"]]
    for i, b in enumerate(p["batches"], 1):
        order = "→".join("%s(%s)" % (b["colors"][k], b["seq"][k])
                         for k in range(3))
        places = "、".join("%d号环" % x for x in b["places"])
        out.append("  第%d批: 依次抓 %s; 依次放 %s" % (i, order, places))
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        try:
            print(describe(arg))
        except TaskCodeError as e:
            print("任务码解析失败:", e)
            sys.exit(1)
